"""Live acceptance for Phase 4: one run per skill, not per bug.

The request that started this work is the first case. "Play any songs from
my liked list" named a place rather than an item; for three phases the
honest answer was a question, because she had no procedure for a place.
Now she has one.

    python scripts/live_skill_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain.chat_engine import ChatEngine
from brain.deliberation import interpret
from brain.skills import skill_for
from tools.screen_control.dpi import ensure_per_monitor_dpi_aware


_COLLECTION = "Play any songs from my liked list in Spotify."
_TRACK = "Play Bang Bang by IVE in Spotify."
_UNKNOWN_PLACE = "Play something from my playlist in Spotify."


def main() -> int:
    ensure_per_monitor_dpi_aware()
    print("=" * 70)
    print("Live skill check (moves the real mouse for steps 1 and 2)")
    print("=" * 70)

    engine = ChatEngine()
    planner = engine.desktop_action_planner
    cursor = engine.cursor_driver
    failures = []
    playing = False
    try:
        cursor.begin_run()

        # Which procedure serves each request, before anything runs.
        for request in (_COLLECTION, _TRACK, _UNKNOWN_PLACE):
            goal = interpret(request)
            skill = skill_for(goal)
            print(
                f"  {request:52s} -> {goal.kind:16s} "
                f"skill={getattr(skill, 'name', '(none)')}"
            )

        started = time.time()
        collection = planner.act(_COLLECTION)
        print(f"\n[1] {_COLLECTION}")
        print(f"    status={collection.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {collection.summary}")
        for step in collection.steps_taken:
            print(f"      {step}")
        playing = collection.status == "done"
        if not playing:
            failures.append(
                f"[1] expected the collection to play, got {collection.status}"
            )

        started = time.time()
        track = planner.act(_TRACK)
        print(f"\n[2] {_TRACK}")
        print(f"    status={track.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {track.summary}")
        for step in track.steps_taken:
            print(f"      {step}")
        playing = playing or track.status == "done"
        if track.status != "done":
            failures.append(f"[2] expected the track to play, got {track.status}")

        started = time.time()
        unknown = planner.act(_UNKNOWN_PLACE)
        print(f"\n[3] {_UNKNOWN_PLACE}")
        print(f"    status={unknown.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {unknown.summary}")
        if unknown.status != "needs_clarification":
            failures.append(
                f"[3] a place she has no procedure for should ask, got "
                f"{unknown.status}"
            )
        if unknown.steps_taken:
            failures.append(f"[3] it acted: {unknown.steps_taken}")

        print()
        if failures:
            for line in failures:
                print(f"FAIL: {line}")
            return 1
        print("PASS: the collection plays, the track still plays, and a place "
              "she has no procedure for still asks.")
        return 0
    finally:
        if playing:
            cleanup = planner.act("Pause the music in Spotify.")
            print(f"cleanup={cleanup.status}: {cleanup.summary}")
        cursor.end_run()
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
