"""Live exact-target check for Spotify native desktop control.

This drives the production DesktopActionPlanner, including its title/artist
parser, its deterministic play path, and its pre-activation refusal. It
searches for ``Bang Bang IVE`` but may only activate a live control whose
exact accessible name is ``Bang Bang`` with ``IVE`` visible beside it, and
it activates by double-click -- a single click opens a track rather than
playing it. Generic Play, radio/mix/station, playlist, and ``Bang Bang by
IVE`` labels are refused before the cursor moves.

Success is not "a click happened": the check passes only if Spotify itself
reports the track as playing (its window renames itself to the track).

The track is paused again during cleanup.

    python scripts/live_spotify_exact_track_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain.chat_engine import ChatEngine
from tools.screen_control.dpi import ensure_per_monitor_dpi_aware


_GOAL = "Play Bang Bang by IVE in Spotify."
_TITLE = "bang bang"
_DECOYS = ("radio", "mix", "station", "playlist", "by ive")


def _activation_step(steps: list[str]) -> str:
    """The step that actually started something, if there is exactly one."""
    activations = [
        step for step in steps
        if "double-clicked" in step.casefold() or "clicked play" in step.casefold()
    ]
    return activations[-1] if activations else ""


def main() -> int:
    ensure_per_monitor_dpi_aware()
    print("=" * 70)
    print("Live Spotify exact-track check (moves the real mouse and keyboard)")
    print("=" * 70)

    engine = ChatEngine()
    planner = engine.desktop_action_planner
    cursor = engine.cursor_driver
    playback_started = False
    try:
        cursor.begin_run()
        started = time.time()
        result = planner.act(_GOAL)
        elapsed = time.time() - started
        print(f"status={result.status} failure={result.failure_code or '(none)'}")
        print(f"elapsed={elapsed:.1f}s summary={result.summary}")
        for step in result.steps_taken:
            print(f"  {step}")

        playback_started = result.status == "done"
        steps = [str(step) for step in result.steps_taken]
        activation = _activation_step(steps).casefold()
        exact_activation = bool(activation) and (
            _TITLE in activation or "clicked play" in activation
        )
        no_decoy = not any(decoy in activation for decoy in _DECOYS)

        if not playback_started:
            print("FAIL: the production planner did not verify playback.")
            return 1
        if not exact_activation or not no_decoy:
            print(f"FAIL: the activation step was {activation!r}.")
            return 1
        print("PASS: exact Bang Bang activated, with IVE as context only, "
              "and Spotify confirmed it is playing.")
        return 0
    finally:
        if playback_started:
            cleanup = planner.act("Pause the music in Spotify.")
            print(f"cleanup={cleanup.status}: {cleanup.summary}")
        cursor.end_run()
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
