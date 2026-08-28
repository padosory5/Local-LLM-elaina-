"""Live check: a booking is asked about before anything is opened.

"Book me a hotel in Guam" cannot be researched, let alone booked, without
the dates it turns on -- a shortlist of prices for nobody's stay looks like
an answer, which is worse than no answer. So the question comes first, and
no page is opened until it is settled.

Nothing is browsed and nothing is booked by this check. It also confirms
the opposite case: looking around is not blocked on the same inputs, and a
booking that already carries dates proceeds.

    python scripts/live_booking_gate_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain.chat_engine import ChatEngine
from brain.deliberation import ClarificationGate, decide, interpret


_BOOKING = "Book me a hotel in Guam"
_DATES = "2026-09-01 to 2026-09-04"
_RESEARCH = "Find hotels in Guam"


def main() -> int:
    print("=" * 70)
    print("Live booking-gate check (opens nothing, books nothing)")
    print("=" * 70)

    engine = ChatEngine()
    planner = engine.browser_action_planner
    failures = []
    try:
        started = time.time()
        asked = planner.act(_BOOKING)
        print(f"\n[1] {_BOOKING}")
        print(f"    status={asked.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {asked.summary}")
        print(f"    steps: {list(asked.steps_taken) or '(nothing opened)'}")
        if asked.status != "needs_clarification":
            failures.append(f"[1] expected a question, got {asked.status}")
        if asked.steps_taken:
            failures.append(f"[1] it browsed before asking: {asked.steps_taken}")
        if asked.clarification is None:
            failures.append("[1] the question was not answerable")

        if asked.clarification is not None:
            gate = ClarificationGate()
            pending = gate.offer(
                goal=asked.clarification.goal,
                slot=asked.clarification.missing,
                question=asked.clarification.question,
                template=asked.clarification.template,
            )
            completed = pending.completed(_DATES)
            print(f"\n[2] (answering) {_DATES}")
            print(f"    completed request: {completed.utterance!r}")
            print(
                "    slots: "
                f"{({k: v.value for k, v in completed.slots.items()})}"
            )
            if completed.value("dates") != _DATES:
                failures.append("[2] the answer did not settle the dates")
            if decide(completed).action != "act":
                failures.append("[2] it would still not proceed")

        # Looking around is a different request with different needs.
        goal = interpret(_RESEARCH)
        decision = decide(goal)
        print(f"\n[3] {_RESEARCH}")
        print(f"    kind={goal.kind} decision={decision.action}")
        if decision.action != "act":
            failures.append(
                "[3] looking around should not be blocked on booking inputs"
            )

        print()
        if failures:
            for line in failures:
                print(f"FAIL: {line}")
            return 1
        print("PASS: the booking asks first and opens nothing; the answer "
              "settles it; looking around is unaffected.")
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
