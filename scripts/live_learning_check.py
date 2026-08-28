"""Live check for Phase 5: she learns which one you meant, and says so.

"Bang Bang" is IVE's song or Jessie J's. Naming the artist once settles it;
asking for the bare title afterwards plays the one you meant, and says out
loud why -- so being wrong costs four words rather than a wrong song.

Nothing here is inferred from a stranger's taste. It learns only from a
play that actually happened, and only from values you supplied yourself.

    python scripts/live_learning_check.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain.chat_engine import ChatEngine
from brain.deliberation.profile import ARTIST_FOR_TITLE, UserProfile
from tools.screen_control.dpi import ensure_per_monitor_dpi_aware


_NAMED = "Play Bang Bang by IVE in Spotify."
_BARE = "Play Bang Bang in Spotify."


def main() -> int:
    ensure_per_monitor_dpi_aware()
    print("=" * 70)
    print("Live learning check (moves the real mouse; profile is temporary)")
    print("=" * 70)

    engine = ChatEngine()
    planner = engine.desktop_action_planner
    cursor = engine.cursor_driver
    failures = []
    playing = False
    # A scratch profile: this check must never quietly edit the real one.
    directory = tempfile.TemporaryDirectory()
    planner.profile = UserProfile(path=Path(directory.name) / "profile.json")
    try:
        cursor.begin_run()

        print(f"\n[1] {_NAMED}")
        print(f"    knows beforehand: {planner.profile.known() or '(nothing)'}")
        started = time.time()
        first = planner.act(_NAMED)
        print(f"    status={first.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {first.summary}")
        playing = first.status == "done"
        if not playing:
            failures.append(f"[1] expected playback, got {first.status}")

        learned = planner.profile.preferred(ARTIST_FOR_TITLE, key="Bang Bang")
        print(f"    learned: {learned}")
        if learned is None or learned.value != "IVE":
            failures.append("[1] it did not learn which artist was meant")

        print(f"\n[2] {_BARE}  (the title alone, two artists share it)")
        started = time.time()
        second = planner.act(_BARE)
        print(f"    status={second.status} elapsed={time.time() - started:.1f}s")
        print(f"    said: {second.summary}")
        playing = playing or second.status == "done"
        if second.status != "done":
            failures.append(f"[2] expected playback, got {second.status}")
        elif "IVE" not in second.summary:
            failures.append("[2] it did not use, or did not say, what it knew")

        standing = planner.profile.preferred(ARTIST_FOR_TITLE, key="Bang Bang")
        print(f"    standing afterwards: {standing.standing if standing else '-'}")
        if learned is not None and standing is not None:
            if standing.standing != learned.standing:
                failures.append(
                    "[2] acting on its own guess counted as fresh evidence"
                )

        print()
        if failures:
            for line in failures:
                print(f"FAIL: {line}")
            return 1
        print("PASS: it learned which one you meant, used it, said so, and "
              "did not treat its own guess as proof.")
        return 0
    finally:
        if playing:
            cleanup = planner.act("Pause the music in Spotify.")
            print(f"cleanup={cleanup.status}: {cleanup.summary}")
        cursor.end_run()
        engine.close()
        directory.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
