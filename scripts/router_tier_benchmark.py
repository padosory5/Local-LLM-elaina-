"""How much of a real conversation reaches the model router, and what it costs.

``router_latency_check.py`` times ``route()`` in isolation, which is the right
tool for one component and the wrong one for this question. What 4F.4 needs to
know is different: across a conversation people actually have, how many turns
pay for a routing call at all, and how long the ones that do take.

So this drives whole turns through ``ChatEngine._route_turn`` with a real model
and a counting client, and reports the split. A turn that never reaches the
model costs nothing, and the honest headline number is the mix -- not the cost
of the calls that remain.

The workload is taken from the dogfooding transcripts rather than invented:
musings, follow-ups, corrections, commands, acknowledgements and small talk in
roughly the proportions they actually occur.

    .venv/Scripts/python.exe scripts/router_tier_benchmark.py
    .venv/Scripts/python.exe scripts/router_tier_benchmark.py --runs 2
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ollama  # noqa: E402

from config.loader import Config  # noqa: E402


# One session, in the shape the dogfooding logs show. ``kind`` is what the
# turn is, not what the router says about it -- it is here so the report can
# say which kinds still pay for a call.
WORKLOAD = (
    ("hey", "social"),
    ("I'm thinking about getting a new monitor.", "musing"),
    ("I'm not really sure yet.", "chat"),
    ("like a gaming one, and I code sometimes", "chat"),
    ("Can you give me some recommendations?", "request"),
    ("which one would you choose?", "follow_up"),
    ("anything cheaper?", "follow_up"),
    ("is it actually good?", "follow_up"),
    ("ok", "acknowledgement"),
    ("got it", "acknowledgement"),
    ("thanks", "acknowledgement"),
    ("Actually let's look at keyboards instead.", "correction"),
    ("mechanical", "chat"),
    ("tactile", "chat"),
    ("what do you think?", "chat"),
    ("open spotify", "command"),
    ("close discord", "command"),
    ("never mind", "cancellation"),
    ("what's the capital of France?", "knowledge"),
    ("what time is it?", "knowledge"),
)


class CountingClient:
    """A real Ollama client that says how often it was asked anything."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0
        self.output_tokens = 0

    def chat(self, **kwargs):
        self.calls += 1
        result = self._inner.chat(**kwargs)
        with contextlib.suppress(Exception):
            self.output_tokens += int(result.get("eval_count", 0) or 0)
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--json", type=str, default="")
    args = parser.parse_args()

    from tests.turn_harness import build_engine

    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        engine = build_engine()
    config = Config()
    real = ollama.Client(host=config.get("llm", "ollama", "base_url"))
    client = CountingClient(real)
    engine.client = client
    for owner in (
        engine.intent_router, engine.desktop_action_planner,
        engine.browser_action_planner,
        getattr(engine, "task_planner", None),
        getattr(engine, "task_intent_gate", None),
        getattr(engine, "brief_responses", None),
        getattr(engine, "consent_classifier", None),
    ):
        if owner is not None and hasattr(owner, "client"):
            owner.client = client

    print(f"model: {config.get('llm', 'ollama', 'model')}")
    print(f"workload: {len(WORKLOAD)} turns x {args.runs}\n")

    rows: list[tuple[str, str, float, int]] = []
    for _run in range(args.runs):
        for said, kind in WORKLOAD:
            before = client.calls
            timings: dict[str, float] = {}
            started = time.perf_counter()
            with contextlib.redirect_stdout(quiet):
                try:
                    engine._route_turn(said, timings=timings)
                except Exception as error:  # a benchmark must not hide these
                    print(f"  !! {said!r}: {type(error).__name__}: {error}")
                    continue
            elapsed = time.perf_counter() - started
            rows.append((kind, said, elapsed, client.calls - before))

    routed = [row for row in rows if row[3] > 0]
    free = [row for row in rows if row[3] == 0]
    times = sorted(row[2] for row in rows)
    routed_times = sorted(row[2] for row in routed)

    def pct(values, fraction):
        if not values:
            return 0.0
        return values[max(0, round(fraction * len(values)) - 1)]

    print(f"{'kind':16s} {'turns':>6s} {'reached model':>14s} {'median':>9s}")
    kinds = sorted({row[0] for row in rows})
    for kind in kinds:
        group = [row for row in rows if row[0] == kind]
        called = sum(1 for row in group if row[3] > 0)
        median = statistics.median([row[2] for row in group])
        print(f"{kind:16s} {len(group):6d} {called:14d} {median:8.2f}s")

    print()
    print(f"turns                 : {len(rows)}")
    print(f"  never reached model : {len(free)} ({len(free) / len(rows):.0%})")
    print(f"  routed by the model : {len(routed)} ({len(routed) / len(rows):.0%})")
    print(f"model calls           : {client.calls}")
    print(f"output tokens         : {client.output_tokens}")
    print()
    print("route stage, all turns:")
    print(f"  p50 {statistics.median(times):.2f}s   p95 {pct(times, 0.95):.2f}s"
          f"   max {times[-1]:.2f}s")
    if routed_times:
        print("route stage, model-routed turns only:")
        print(f"  p50 {statistics.median(routed_times):.2f}s"
              f"   p95 {pct(routed_times, 0.95):.2f}s"
              f"   max {routed_times[-1]:.2f}s")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "turns": len(rows),
            "free": len(free),
            "routed": len(routed),
            "calls": client.calls,
            "output_tokens": client.output_tokens,
            "p50_all": statistics.median(times),
            "p95_all": pct(times, 0.95),
            "p50_routed": statistics.median(routed_times) if routed_times else 0,
            "p95_routed": pct(routed_times, 0.95),
            "rows": [
                {"kind": k, "said": s, "seconds": round(t, 3), "calls": c}
                for k, s, t, c in rows
            ],
        }, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
