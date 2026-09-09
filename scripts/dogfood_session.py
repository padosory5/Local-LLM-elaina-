"""Run a dogfood arc several times and report what it is really worth.

Every Korean number in this project was quoted from a single run until
this existed, and single runs of this arc do not mean what they look like.
The same arc, the same code, eight times:

    58%  58%  33%  50%  58%  42%  42%  58%

A twenty-five point spread. Against that, "the 27B scored 83%" and
"EXAONE scored 50%" -- both single runs -- were not comparisons at all,
and a change that moved the number by ten points would have been
indistinguishable from doing nothing.

So this refuses to produce the shape of number that caused the problem. It
runs an arc **n times**, each against a fresh backend, and reports the
median with its range and sample count attached. A configuration is only
better than another when their ranges do not overlap.

Each run restarts the backend, because conversational state lives in the
running ChatEngine and a language pin or an open task from run one would
otherwise be measured as run two's behaviour. That is not hypothetical --
running five arcs against one backend once made every arc after the
code-switching one answer English input in Korean.

    .venv/Scripts/python.exe scripts/dogfood_session.py korean_day
    .venv/Scripts/python.exe scripts/dogfood_session.py korean_day --runs 5
    .venv/Scripts/python.exe scripts/dogfood_session.py evening social --runs 3
"""

from __future__ import annotations

import argparse
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8")

from scripts.conversation_quality_report import score  # noqa: E402

PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
PORT = 8765
BACKEND_TIMEOUT_SECONDS = 400

# Below this, two configurations are not distinguishable on this metric and
# should not be reported as if they were. Set from the measured spread.
INDISTINGUISHABLE_POINTS = 25


def _port_open() -> bool:
    probe = socket.socket()
    probe.settimeout(1)
    try:
        return probe.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        probe.close()


def _start_backend(log_path: Path):
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [PYTHON, "main.py"],
        cwd=str(PROJECT_ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        env={**os.environ, "ELAINA_OPEN_DESKTOP": "0"},
    )
    deadline = time.time() + BACKEND_TIMEOUT_SECONDS
    while not _port_open() and time.time() < deadline:
        if process.poll() is not None:
            handle.close()
            return None, handle
        time.sleep(3)
    return (process, handle) if _port_open() else (None, handle)


def _stop_backend(process) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
    # The port lingers for a moment after the process goes.
    deadline = time.time() + 30
    while _port_open() and time.time() < deadline:
        time.sleep(1)


def run_once(arc: str, out_path: Path, log_path: Path):
    """One arc against one fresh backend. Returns (clean, total) or None."""
    process, handle = _start_backend(log_path.with_suffix(".backend.log"))
    if process is None:
        print(f"    backend did not start -- see {log_path.with_suffix('.backend.log')}")
        handle.close()
        return None
    try:
        result = subprocess.run(
            [PYTHON, "scripts/live_dogfood_conversation.py",
             "--arc", arc, "--out", str(out_path)],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    finally:
        _stop_backend(process)
        handle.close()

    if not out_path.exists():
        return None
    scored = score(out_path)
    return (scored["clean_turns"], scored["total_turns"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arcs", nargs="+")
    parser.add_argument(
        "--runs", type=int, default=3,
        help="repeats per arc (default 3; one run is not a measurement)",
    )
    parser.add_argument("--tag", default="session")
    args = parser.parse_args()

    if args.runs < 2:
        print("A single run is what this script exists to prevent. Use --runs 2+.")
        return 2

    runtime = PROJECT_ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    summary: dict[str, list[float]] = {}

    for arc in args.arcs:
        print(f"\n### {arc}  ({args.runs} runs)")
        rates: list[tuple[int, int]] = []
        for index in range(1, args.runs + 1):
            stem = f"{args.tag}_{arc}_{index}"
            counted = run_once(
                arc, runtime / f"{stem}.json", runtime / f"{stem}.log",
            )
            if counted is None or not counted[1]:
                print(f"  run {index}: failed")
                continue
            rates.append(counted)
            clean, total = counted
            print(f"  run {index}: {clean/total:.0%}  ({clean}/{total})")
        summary[arc] = rates

    print("\n" + "=" * 66)
    print("RESULT")
    print("=" * 66)
    pooled_clean = pooled_total = 0
    for arc, rates in summary.items():
        if not rates:
            print(f"  {arc:<14} no successful runs")
            continue
        per_run = [clean / total for clean, total in rates]
        low, high = min(per_run), max(per_run)
        arc_clean = sum(clean for clean, _ in rates)
        arc_total = sum(total for _, total in rates)
        pooled_clean += arc_clean
        pooled_total += arc_total
        print(
            f"  {arc:<14} per-run median {statistics.median(per_run):.0%}   "
            f"range {low:.0%}-{high:.0%}   n={len(per_run)}"
        )

    if pooled_total:
        # The number to quote. One arc is twelve turns, so a single turn
        # flipping moves a per-run score by eight points -- the granularity
        # *is* most of the spread. Pooling every turn from every run into
        # one rate removes that: sixty turns instead of twelve.
        margin = 100 * (0.25 / pooled_total) ** 0.5 * 1.96
        print()
        print(
            f"  POOLED  {pooled_clean / pooled_total:.0%}  "
            f"({pooled_clean}/{pooled_total} turns, ±{margin:.0f} points)"
        )
        print()
        print("  Quote the pooled figure. The per-run range above is what a")
        print("  single run could have told you, which is why single runs of")
        print("  this arc were never a measurement.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
