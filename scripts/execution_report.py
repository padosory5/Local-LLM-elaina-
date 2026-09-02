"""Print the execution benchmark as a record: goal, steps, states, outcome.

The scenarios themselves live in ``tests/execution_matrix.json`` and are
asserted by ``tests/test_execution_matrix.py`` on every run of the suite.
This renders them for reading -- what each task planned, which tool each step
chose, what state that step had to reach, what actually came back, and which
of the five terminal outcomes the planner reported.

It re-runs every scenario through the real planner rather than reprinting the
expected values, so the "observed" and "outcome" columns are what the code
did just now, not what the file claims it should do.

    .venv/Scripts/python.exe scripts/execution_report.py
    .venv/Scripts/python.exe scripts/execution_report.py --kind cancelled
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.console_style import status_label  # noqa: E402
from tests.test_execution_matrix import MATRIX_PATH, _run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", action="append", default=[])
    args = parser.parse_args()

    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    cases = matrix["cases"]
    if args.kind:
        cases = [case for case in cases if case["kind"] in set(args.kind)]

    print(f"{len(cases)} multi-step scenarios, through the real planner.\n")

    outcomes: Counter[str] = Counter()
    unverified: list[str] = []
    failures = 0

    for case in cases:
        result = _run(case)
        outcome = result.outcome()
        ok = outcome.outcome == case["expected_outcome"]
        failures += 0 if ok else 1
        outcomes[outcome.outcome] += 1
        if outcome.succeeded and not outcome.verified:
            unverified.append(case["id"])

        print("=" * 72)
        print(f"[{status_label(ok)}] {case['id']} ({case['kind']})")
        print(f"  goal      : {case['goal']}")
        for index, step in enumerate(case["steps"], start=1):
            ran = (
                result.task_state.completed_steps[index - 1]
                if index <= len(result.task_state.completed_steps) else None
            )
            print(f"  step {index}    : [{step.get('tool', 'browser_control')}] "
                  f"{step.get('summary', '')}")
            if step.get("expected_state"):
                print(f"    expects : {step['expected_state']}")
            if ran is None:
                print("    observed: (never ran)")
            else:
                mark = ran.failure_code or ran.status
                print(f"    observed: {mark}")
        print(f"  outcome   : {outcome.log_line()}")
        if not ok:
            print(f"  EXPECTED  : {case['expected_outcome']}")
        if case.get("note"):
            print(f"  note      : {case['note']}")

    print("\n" + "=" * 72)
    print(f"{len(cases) - failures}/{len(cases)} scenarios reached the "
          "expected outcome.")
    for name, count in sorted(outcomes.items()):
        print(f"  {name:20} {count}")
    if unverified:
        print(f"\nSucceeded without an observation confirming it "
              f"({len(unverified)}):")
        print("  " + ", ".join(unverified))
        print("  These are reported as EXECUTED_BUT_UNVERIFIED, not as "
              "verified success.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
