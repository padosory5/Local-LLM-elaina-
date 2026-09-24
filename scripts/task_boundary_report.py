"""When does one task end and the next one begin?

Rule 1: the instrument first, and this one is late. ``about_the_same_thing``
is asked once per turn and everything downstream follows what it says --
candidates, constraints, evidence, and (until the fix beside this) the
search query. It has had tests since the first dogfood session and never
had a *number*, so a regression in either direction looked the same from
outside: green.

Both directions are scored, and the continue half is the one to watch.
Splitting too eagerly throws away three turns of established context, which
is the failure the follow-up branch exists to prevent -- so a checker that
only counted the splits would give a perfect score to a function that
always said "different".

    python scripts/task_boundary_report.py

Drives the real :class:`~brain.task_session.TaskSessionStore`, one held
problem and one turn per case, and compares the task id before and after.
No model, no network; the whole decision is deterministic.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MATRIX = ROOT / "tests" / "boundary_matrix.json"


def decide(case: dict) -> str:
    """What the boundary says about this case: "continue" or "split"."""
    from brain.task_session import TaskSessionStore

    store = TaskSessionStore()
    opened = case["open"]
    turn = case["turn"]
    # The store logs its own reasoning on every call. Useful live, noise
    # here -- the report prints one line per case instead.
    with redirect_stdout(io.StringIO()):
        before = store.note_recommendation_turn(
            opened["said"], subject=opened.get("subject", ""),
        )
        after = store.note_recommendation_turn(
            turn["said"],
            subject=turn.get("subject", ""),
            follow_up=bool(turn.get("follow_up", False)),
            topic_shift=bool(turn.get("topic_shift", False)),
        )
    return "continue" if after.id == before.id else "split"


def report(cases: list[dict]) -> int:
    """Print one line per case; return the number that were wrong."""
    wrong = 0
    by_expectation: dict[str, list[bool]] = {"continue": [], "split": []}
    print(f"{'case':<36} {'lang':<5} {'want':<9} {'got':<9} ")
    print("-" * 74)
    for case in cases:
        want = case["expect"]
        got = decide(case)
        ok = want == got
        by_expectation[want].append(ok)
        if not ok:
            wrong += 1
        print(
            f"{case['id']:<36} {case.get('language', '?'):<5} "
            f"{want:<9} {got:<9} {'' if ok else '<-- wrong'}"
        )
    print()
    for want, results in by_expectation.items():
        if not results:
            continue
        right = sum(1 for value in results if value)
        print(
            f"must {want:<9} {right}/{len(results)} "
            f"({right / len(results):.0%})"
        )
    total = len(cases)
    print(f"{'overall':<14} {total - wrong}/{total} "
          f"({(total - wrong) / total:.0%})")
    return wrong


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--matrix", type=Path, default=MATRIX,
        help="the case list to score (default: tests/boundary_matrix.json)",
    )
    args = parser.parse_args(argv)
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    return 1 if report(matrix["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
