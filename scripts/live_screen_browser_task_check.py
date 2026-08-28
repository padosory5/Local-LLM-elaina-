"""Live planner-level check for screen-native browser control (Phase 4E).

``live_screen_browser_check.py`` proves the mechanism -- coordinates, focus,
clicking, verification. This one proves the *whole* stack: a real
BrowserActionPlanner, a real local model, and a real browser being driven by
the real mouse and keyboard through a multi-turn goal.

It is the check that answers "can she actually do what I asked", so it runs
a search, a click-through into a result, and a question answered from the
page that click landed on -- each one depending on the last.

Non-destructive: everything happens in a new tab, which is closed at the
end, and the pointer is returned to where it started.

    python scripts/live_screen_browser_task_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from brain.chat_engine import ChatEngine
from tools.screen_control.dpi import ensure_per_monitor_dpi_aware

ensure_per_monitor_dpi_aware()

# Each goal depends on the one before it, so a driver that only *looks*
# like it worked (a click that did not land, a page never really read)
# fails at the next step rather than passing quietly.
_GOALS = (
    ("Search Wikipedia for the Eiffel Tower.", ("google", "search")),
    ("Open the first search result.", ()),
    ("How tall is it according to this page?", ("330", "1,083", "metre", "ft")),
)


def main() -> int:
    print("=" * 68)
    print("Phase 4E live planner check (drives the real mouse and keyboard)")
    print("=" * 68)

    engine = ChatEngine()
    if engine.browser_driver != "screen":
        print(f"browser_control.driver is {engine.browser_driver!r}, not 'screen'.")
        return 1

    planner = engine.browser_action_planner
    cursor = engine.browser_service.cursor
    failures: list[str] = []

    cursor.begin_run()
    opened_tab = False
    try:
        first = engine.browser_service.screen_control.focus_and_observe()
        if first.status != "observed":
            print(f"No usable browser window: {first.status} -- {first.message}")
            return 1
        print(f"\nStarting from {first.url!r} ({len(first.elements)} elements, "
              f"{first.elapsed_seconds:.2f}s to observe)")
        cursor.press("ctrl", "t")
        opened_tab = True
        time.sleep(1.0)

        for goal, expected in _GOALS:
            print("\n" + "-" * 68)
            print(f"GOAL: {goal}")
            started = time.time()
            result = planner.act(goal=goal, context="")
            elapsed = time.time() - started
            print(f"  status={result.status} rounds={result.model_rounds} "
                  f"{elapsed:.1f}s")
            print(f"  {result.summary[:300]}")
            if result.status != "done":
                failures.append(f"{goal} -> {result.status} {result.failure_code}")
                continue
            summary = str(result.summary or "").casefold()
            if expected and not any(term.casefold() in summary for term in expected):
                failures.append(
                    f"{goal} -> finished but the answer mentioned none of "
                    f"{expected}"
                )
    finally:
        if opened_tab:
            cursor.press("ctrl", "w")
            time.sleep(0.4)
        cursor.end_run()
        print("\n(test tab closed, pointer returned)")

    print("\n" + "=" * 68)
    if failures:
        for failure in failures:
            print(f"  FAILED: {failure}")
        print("=" * 68)
        return 1
    print(f"all {len(_GOALS)} goals completed")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
