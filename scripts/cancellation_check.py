"""Print the cancellation check as a record: where the stop arrived, what
had run by then, and what the person was told.

The scenarios live in ``tests/test_cancellation.py`` and are asserted on
every run of the suite; this renders them for reading, the same way
``execution_report.py`` renders the execution matrix.

What it is for, in one line: a cancellation can reach the planner at four
different moments and only one of them had coverage. The three inside
``_advance`` -- before planning, before dispatching, before retrying -- were
untested, and the last of those is the one where a stop has to interrupt a
*recovery* rather than progress.

Each scenario is scored on three questions:

    stopped   the outcome is CANCELLED
    bounded   no tool call was dispatched after the stop was seen
    honest    the report says what had already been done, or says plainly
              that nothing had

The third is what A5 added. Before it, every one of these said "You took
control, so I stopped." and nothing more -- true, and it leaves the person
not knowing whether anything happened to their machine.

    .venv/Scripts/python.exe scripts/cancellation_check.py
    .venv/Scripts/python.exe scripts/cancellation_check.py --language ko
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8")

from tests.test_cancellation import SCENARIOS, examine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", default="en", choices=("en", "ko"))
    args = parser.parse_args()

    rows = []
    for name, plan, steps, cancel_on, tool_at in SCENARIOS:
        # The planner narrates its own loop; the report is what is on test.
        with contextlib.redirect_stdout(io.StringIO()):
            rows.append(
                examine(name, plan, steps, cancel_on, tool_at, args.language)
            )

    print("=" * 74)
    print(f"A5 -- CANCELLATION  (language: {args.language})")
    print("=" * 74)

    counted = [row for row in rows if not row["skipped"]]
    passed = 0
    for row in rows:
        if row["skipped"]:
            print(f"\n[ --  ] {row['name']}  (the plan had already finished; "
                  "nothing to cancel)")
            continue
        ok = row["stopped"] and row["bounded"] and row["honest"]
        passed += ok
        print(f"\n[{'ok  ' if ok else 'FAIL'}] {row['name']}"
              f"  ({row['outcome']}, {row['dispatched']} dispatched)")
        print(f"       heard: {row['heard']!r}")
        if row["did"]:
            print(f"       did:   {row['did']}")
        for label in ("stopped", "bounded", "honest"):
            if not row[label]:
                print(f"       >> not {label}")

    print("\n" + "=" * 74)
    print(f"{passed}/{len(counted)} cancellations stopped the plan and "
          "reported honestly")
    if len(rows) != len(counted):
        print(f"  ({len(rows) - len(counted)} arrived after the plan had "
              "finished and are not cancellations)")
    print("=" * 74)
    return 0 if passed == len(counted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
