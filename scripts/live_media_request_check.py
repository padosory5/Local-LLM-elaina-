"""Live check for Phase 0: what a media request actually named.

Three things, against real Spotify and the production planner:

1. A request that names no track ("play any songs from my liked list")
   comes back as a question. Nothing is typed, nothing is clicked, and the
   cursor never moves -- this is the failure that put the whole sentence
   into Spotify's search box.
2. A named track still plays, unchanged.
3. A *second* named track plays straight after the first. This is the real
   proof that typing replaces rather than appends: if the previous query
   were still in the box, the second search would read
   "Bang Bang IVEAfter LIKE IVE" and its exact row would not exist.

Playback is paused again during cleanup.

    python scripts/live_media_request_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain.chat_engine import ChatEngine
from tools.screen_control.dpi import ensure_per_monitor_dpi_aware


_UNNAMED = "Play any songs from my liked list in Spotify."
_FIRST = "Play Bang Bang by IVE in Spotify."
_SECOND = "Play After LIKE by IVE in Spotify."


def main() -> int:
    ensure_per_monitor_dpi_aware()
    print("=" * 70)
    print("Live media-request check (moves the real mouse for steps 2 and 3)")
    print("=" * 70)

    engine = ChatEngine()
    planner = engine.desktop_action_planner
    cursor = engine.cursor_driver
    failures = []
    playing = False
    try:
        cursor.begin_run()

        # 1 -- names nothing: expect a question, and no action at all.
        started = time.time()
        asked = planner.act(_UNNAMED)
        print(f"\n[1] {_UNNAMED}")
        print(f"    status={asked.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {asked.summary}")
        if asked.status != "needs_clarification":
            failures.append(f"[1] expected a question, got {asked.status}")
        if asked.steps_taken:
            failures.append(f"[1] it acted: {asked.steps_taken}")

        # 2 -- names a track: expect playback, as before.
        started = time.time()
        first = planner.act(_FIRST)
        print(f"\n[2] {_FIRST}")
        print(f"    status={first.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {first.summary}")
        for step in first.steps_taken:
            print(f"      {step}")
        playing = first.status == "done"
        if not playing:
            failures.append(f"[2] expected playback, got {first.status}")

        # 3 -- a second query on top of the first.
        started = time.time()
        second = planner.act(_SECOND)
        print(f"\n[3] {_SECOND}")
        print(f"    status={second.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {second.summary}")
        for step in second.steps_taken:
            print(f"      {step}")
        playing = playing or second.status == "done"
        if second.status != "done":
            failures.append(
                f"[3] expected the second track to play, got {second.status} "
                "-- the search box may still hold the previous query"
            )

        print()
        if failures:
            for line in failures:
                print(f"FAIL: {line}")
            return 1
        print("PASS: an unnamed request asks; named tracks play; a second "
              "search replaces the first.")
        return 0
    finally:
        if playing:
            cleanup = planner.act("Pause the music in Spotify.")
            print(f"cleanup={cleanup.status}: {cleanup.summary}")
        cursor.end_run()
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
