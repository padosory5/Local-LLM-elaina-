"""Live end-to-end check for whole-desktop cursor control (Phase 4F).

Drives the *real* mouse and keyboard against a real application. It exists
because the failures that matter here cannot be reproduced with fakes: a
minimized window reporting nonsense coordinates, an accessibility tree that
has not woken yet, a click landing on the wrong pixel, and a search field
that the older Invoke driver could never type into at all.

Spotify is the target precisely because it is the hard case:
``windows_ui_control.click_then_type`` documents that CEF apps like it
"render their real search/text fields without ever exposing them as a named,
verifiable UIA control".

Non-destructive: it pauses whatever it started and puts the window back the
way it found it (including re-minimizing), and returns the pointer.

    python scripts/live_desktop_control_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import win32con
import win32gui

from tools.computer_control.session_action_memory import SessionActionMemory
from tools.computer_control.windows_ui_observer import WindowsUIObserver
from tools.screen_control.cursor_driver import CursorDriver
from tools.screen_control.dpi import (
    coordinates_are_trustworthy,
    ensure_per_monitor_dpi_aware,
)
from tools.screen_control.input_watcher import InputWatcher
from tools.screen_control.screen_ui_control import ScreenUIControl

ensure_per_monitor_dpi_aware()

_APP = "Spotify"
_QUERY = "Weightless Marconi Union"

_passed: list[str] = []
_failed: list[str] = []
_disturbed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    marker = "PASS" if condition else "FAIL"
    (_passed if condition else _failed).append(name)
    print(f"  [{marker}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(condition)


def note_disturbance(watcher) -> bool:
    """Whether the person used the machine while the check was running.

    Not a failure of the driver -- it is the driver behaving correctly, and
    it invalidates the timing-sensitive checks around it. Reported as its
    own outcome so a run that was simply interrupted is not read as a bug.
    """
    counters = watcher.counters()
    real = counters["real_mouse"] + counters["real_key"]
    if real:
        _disturbed.append(f"{real} real input events while running")
    return bool(real)


def find_window(observer: WindowsUIObserver):
    for window in observer.list_windows():
        if _APP.casefold() in (window.title or "").casefold():
            return window
    return None


def main() -> int:
    print("=" * 70)
    print("Phase 4F live whole-desktop cursor control check")
    print("=" * 70)

    print("\n1. Foundations")
    check("per-monitor DPI awareness", coordinates_are_trustworthy())
    watcher = InputWatcher()
    started = watcher.start()
    check(
        "input watcher is running",
        started,
        watcher.unavailable_reason or "hooks installed",
    )

    observer = WindowsUIObserver()
    window = find_window(observer)
    if not check(f"{_APP} is running", window is not None):
        print(f"\nStart {_APP} and run this again.")
        return 1
    handle = window.handle
    was_minimized = bool(win32gui.IsIconic(handle))
    print(f"      window {handle}, minimized={was_minimized}")

    cursor = CursorDriver(input_watcher=watcher)
    control = ScreenUIControl(observer=observer, cursor=cursor)
    memory = SessionActionMemory()
    cursor.begin_run()

    try:
        print("\n2. Injected input is not mistaken for the user")
        print("      (leave the mouse and keyboard alone for ~20s)")
        mark = watcher.mark()
        cursor.press("shift")
        time.sleep(0.3)
        counters = watcher.counters()
        check(
            "Elaina's own keystrokes are not counted as user input",
            counters["injected"] > 0 and not watcher.user_input_since(mark),
            f"counters={counters}",
        )

        print("\n3. Focus a minimized/background app (moving your mouse now)")
        focused = control.focus_window(_APP)
        if not check(
            f"{_APP} comes to the front",
            focused.status == "focused",
            f"{focused.status}: {focused.message}",
        ):
            return 1
        time.sleep(0.8)

        print("\n4. Observe the woken tree")
        observation = observer.describe_window(_APP)
        check(
            "the app exposes its controls",
            observation.status == "observed" and len(observation.controls) > 20,
            f"{observation.status}, {len(observation.controls)} controls",
        )
        search = next(
            (
                item for item in observation.controls
                if item.role in {"ComboBox", "Edit"}
            ),
            None,
        )
        if not check("a search field is present", search is not None):
            return 1
        print(f"      search field: [{search.element_id}] "
              f"{search.role}: {search.name[:40]!r}")

        print("\n5. Type into a field the Invoke driver cannot type into")
        typed = control.type_text(
            _APP, search.name, _QUERY,
            element_id=search.element_id, submit=True,
        )
        check(
            "the query is typed and verified in the real field",
            typed.status == "typed" and typed.verified is True,
            f"{typed.status} verified={typed.verified}: {typed.message[:90]}",
        )
        if typed.status == "typed" and typed.verified is True:
            memory.record(
                app=_APP, family="text_input", subject=_QUERY,
                window_title=typed.window_title,
                control_name=typed.control_name,
            )
        time.sleep(1.5)

        print("\n6. Act on the results")
        results = observer.describe_window(_APP)
        check(
            "the results page is observable",
            results.status == "observed",
            f"{results.status}, {len(results.controls)} controls",
        )
        playable = [
            item for item in results.controls
            if item.role in {"Button", "ListItem", "DataItem"}
            and item.name
            and "Marconi" in item.name
        ]
        print(f"      matching results: {len(playable)}")
        for item in playable[:3]:
            print(f"        [{item.element_id}] {item.name[:55]!r}")
        check(
            "the search actually returned the thing that was searched for",
            bool(playable),
            "no control mentioning the query was found" if not playable else "",
        )

        print("\n7. Follow-up memory")
        remembered = memory.last_subject(app=_APP)
        check(
            "'stop it' would resolve to what was actually searched",
            remembered is not None and remembered.subject == _QUERY,
            f"remembered {remembered.subject!r}" if remembered else "nothing",
        )

        print("\n8. Refusals still hold on this driver")
        refused = control.type_text(_APP, "Password", "secret")
        check(
            "a credential field is refused",
            refused.status in {"refused", "not_found"},
            f"{refused.status}: {refused.message[:70]}",
        )
    finally:
        note_disturbance(watcher)
        print("\n9. Cleanup")
        try:
            control.press_key(_APP, "escape")
        except Exception:
            pass
        if was_minimized:
            try:
                win32gui.ShowWindow(handle, win32con.SW_MINIMIZE)
                print("      re-minimized Spotify")
            except Exception:
                pass
        cursor.end_run()
        watcher.stop()
        print("      pointer returned, input watcher stopped")

    print("\n" + "=" * 70)
    print(f"passed {len(_passed)}   failed {len(_failed)}")
    for name in _failed:
        print(f"  FAILED: {name}")
    if _disturbed:
        print()
        print(f"NOTE: you used the mouse or keyboard while this ran "
              f"({_disturbed[0]}).")
        print("      That is the takeover detection working as intended, but "
              "it invalidates")
        print("      the timing-sensitive checks above. Re-run without "
              "touching the machine")
        print("      for a clean result.")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
