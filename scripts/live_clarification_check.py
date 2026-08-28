"""Live check for Phase 3: the gate's three exits, against real Spotify.

1. A request naming no song, with nothing played yet, comes back as a
   question -- and nothing is touched.
2. The answer to that question continues the *same* request: it is folded
   back into a whole sentence, read by the same interpreter, and played
   through every guard on that path.
3. Asked vaguely again, now that something has been played, she acts on
   what she last played and says so, instead of asking twice.

    python scripts/live_clarification_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain.chat_engine import ChatEngine
from brain.deliberation import ClarificationGate
from tools.screen_control.dpi import ensure_per_monitor_dpi_aware


_VAGUE = "Play some music in Spotify."
_ANSWER = "Bang Bang by IVE"


def main() -> int:
    ensure_per_monitor_dpi_aware()
    print("=" * 70)
    print("Live clarification check (moves the real mouse for steps 2 and 3)")
    print("=" * 70)

    engine = ChatEngine()
    planner = engine.desktop_action_planner
    cursor = engine.cursor_driver
    gate = ClarificationGate()
    failures = []
    playing = False
    try:
        cursor.begin_run()

        started = time.time()
        asked = planner.act(_VAGUE)
        print(f"\n[1] {_VAGUE}")
        print(f"    status={asked.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {asked.summary}")
        if asked.status != "needs_clarification":
            failures.append(f"[1] expected a question, got {asked.status}")
        if asked.steps_taken:
            failures.append(f"[1] it acted: {asked.steps_taken}")
        if asked.clarification is None:
            failures.append("[1] the question was not answerable")
            print("\nFAIL: no clarification to answer.")
            return 1

        pending = gate.offer(
            goal=asked.clarification.goal,
            slot=asked.clarification.missing,
            question=asked.clarification.question,
            template=asked.clarification.template,
        )
        completed = pending.completed(_ANSWER)
        print(f"\n[2] (answering) {_ANSWER}")
        print(f"    completed request: {completed.utterance!r}")
        print(f"    slots: {({k: v.value for k, v in completed.slots.items()})}")

        started = time.time()
        played = planner.act(completed)
        print(f"    status={played.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {played.summary}")
        for step in played.steps_taken:
            print(f"      {step}")
        playing = played.status == "done"
        if not playing:
            failures.append(f"[2] expected playback, got {played.status}")

        started = time.time()
        again = planner.act(_VAGUE)
        print(f"\n[3] {_VAGUE}  (asked vaguely a second time)")
        print(f"    status={again.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {again.summary}")
        playing = playing or again.status == "done"
        if again.status != "done":
            failures.append(
                f"[3] expected her to act on what she last played, got "
                f"{again.status}"
            )
        elif "say the word" not in again.summary:
            failures.append("[3] she acted on an assumption without saying so")

        print()
        if failures:
            for line in failures:
                print(f"FAIL: {line}")
            return 1
        print("PASS: asked once, played the answer, then acted on what she "
              "knew and said so.")
        return 0
    finally:
        if playing:
            cleanup = planner.act("Pause the music in Spotify.")
            print(f"cleanup={cleanup.status}: {cleanup.summary}")
        cursor.end_run()
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
