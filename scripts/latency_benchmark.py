"""Measure where a turn's time actually goes, before optimising anything.

Drives real turns through a real ``ChatEngine`` -- the real router, the real
memory lookup, the real model -- and reports each stage's median and p90.

Cold and warm are reported separately and never averaged together: the first
turn pays for a model load that no later turn does, and a single number over
both describes neither.

What this covers: everything from the transcript onward -- routing, memory,
time to first token, generation, tools, total. The stages before it (the VAD's
trailing silence, transcription) and after it (first audible audio, interrupt)
are instrumented and appear on the live ``[Timing]`` line, but they need a
real microphone and speaker, so they are measured by the procedure in
``docs/LATENCY_BASELINE.md`` rather than here.

    .venv/Scripts/python.exe scripts/latency_benchmark.py
    .venv/Scripts/python.exe scripts/latency_benchmark.py --runs 5
    .venv/Scripts/python.exe scripts/latency_benchmark.py --scenario conversation
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import timing  # noqa: E402
from core.lifecycle import StartupTimeout, build_within  # noqa: E402

# The scenarios from the brief. Each is one utterance; the shape of the work
# behind it is what differs.
SCENARIOS = (
    ("conversation", "Hey, how are you?"),
    ("conversation", "That film last night was better than I expected."),
    ("no_tool_fact", "What's the capital of France?"),
    ("no_tool_fact", "Explain what idempotent means."),
    ("memory", "What have I told you about my project?"),
    ("web_search", "What's the weather in Seattle tomorrow?"),
    ("web_search", "Good restaurants in Seattle?"),
    ("multi_step", "Find me a hotel in Seoul and check it has rooms on the 18th."),
    ("short_input", "Thanks."),
    ("long_input",
     "I'm moving to Seattle in a couple of weeks and I still need to work out "
     "where I'm going to live, so I've been looking at neighbourhoods near the "
     "university and trying to work out what rent actually costs there."),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3,
                        help="warm iterations per scenario")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--out", type=Path, default=None,
                        help="write the raw records as JSON")
    parser.add_argument("--engine-timeout", type=float, default=240.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenarios = [
        (kind, text) for kind, text in SCENARIOS
        if not args.scenario or kind in set(args.scenario)
    ]
    if not scenarios:
        print("No scenarios matched.")
        return 2

    from brain.chat_engine import ChatEngine

    print("Building the engine (cold)...")
    cold_started = time.perf_counter()
    try:
        engine = build_within(
            "chat engine", ChatEngine, timeout=args.engine_timeout,
        )
    except StartupTimeout as error:
        print(f"Could not measure: {error}")
        return 1
    cold_build = time.perf_counter() - cold_started
    print(f"Engine ready in {cold_build:.1f}s.\n")

    timing.reset()
    records: list[dict] = []

    def run(kind: str, text: str, *, cold: bool) -> None:
        timing.begin(label=kind, cold=cold)
        started = time.perf_counter()
        try:
            engine.chat(text)
        except Exception as error:  # pragma: no cover - operator-facing
            print(f"  {kind}: turn failed: {type(error).__name__}: {error}")
            timing.finish()
            return
        elapsed = time.perf_counter() - started
        line = timing.finish()
        if line is not None:
            records.append({"kind": kind, "cold": cold, "text": text,
                            **line.as_dict()})
        print(f"  [{'cold' if cold else 'warm'}] {kind:14} {elapsed:6.2f}s  "
              f"{text[:44]!r}")

    # One cold turn first, kept apart from everything after it.
    print("Cold turn (first request after startup):")
    run(scenarios[0][0], scenarios[0][1], cold=True)

    print(f"\nWarm turns, {args.runs} run(s) per scenario:")
    for index in range(args.runs):
        print(f"-- run {index + 1} --")
        for kind, text in scenarios:
            run(kind, text, cold=False)

    try:
        engine.close()
    except Exception:
        pass

    warm = timing.history(cold=False)
    cold = timing.history(cold=True)

    print("\n" + "=" * 72)
    print(f"WARM ({len(warm)} turns) -- median / p90 / min / max, seconds\n")
    report = timing.summarize(warm)
    for name, stats in report.items():
        print(f"  {name:26} {stats['median']:6.2f} {stats['p90']:6.2f} "
              f"{stats['min']:6.2f} {stats['max']:6.2f}   n={int(stats['n'])}")

    if cold:
        print(f"\nCOLD ({len(cold)} turn) -- reported separately, never averaged in\n")
        for name, stats in timing.summarize(cold).items():
            print(f"  {name:26} {stats['median']:6.2f}")
    print(f"\n  engine construction (cold)  {cold_build:6.2f}")

    print("\nTOP BOTTLENECKS, by measured median:")
    for rank, (name, seconds) in enumerate(timing.bottlenecks(report), start=1):
        print(f"  {rank}. {name:26} {seconds:.2f}s")

    perceived = report.get("end_of_speech_to_response", {})
    if perceived:
        print(f"\n  Perceived (transcript -> first token): "
              f"median {perceived['median']:.2f}s, p90 {perceived['p90']:.2f}s")
        print("  Add the VAD trailing silence and transcription measured "
              "live for the full end-of-speech figure.")

    if args.out:
        args.out.write_text(
            json.dumps(records, indent=2), encoding="utf-8",
        )
        print(f"\nRaw records written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
