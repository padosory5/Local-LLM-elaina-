"""Live end-to-end check for screen-native browser control (Phase 4E).

Unlike the unit tests, this drives the *real* mouse and keyboard against the
browser window the user actually has open. It exists because the failure
modes that matter here -- a click landing on the wrong pixel, an
accessibility tree that never wakes, a window that will not come forward --
cannot be reproduced with fakes.

It is deliberately non-destructive: it works in a new tab, visits only
stable, side-effect-free pages, closes the tab afterwards, and puts the
pointer back where it found it.

    python scripts/live_screen_browser_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.screen_browser.browser_window import BrowserWindowFinder
from tools.computer_control.windows_app_catalog import WindowsAppCatalog
from tools.screen_control.cursor_driver import CursorDriver
from tools.screen_control.dpi import (
    coordinates_are_trustworthy,
    current_awareness,
    ensure_per_monitor_dpi_aware,
)
from tools.screen_browser.page_observer import ScreenPageObserver
from tools.screen_browser.screen_browser_control import ScreenBrowserControl

# Stable, tiny, and free of side effects. example.com has exactly one link,
# which makes "did the click actually navigate" unambiguous.
_TARGET_URL = "https://example.com"
_EXPECTED_LINK_HOST = "iana.org"

_passed: list[str] = []
_failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    marker = "PASS" if condition else "FAIL"
    (_passed if condition else _failed).append(name)
    print(f"  [{marker}] {name}" + (f" -- {detail}" if detail else ""))
    return condition


def main() -> int:
    print("=" * 68)
    print("Phase 4E live screen-native browser check")
    print("=" * 68)

    print("\n1. DPI awareness")
    state = ensure_per_monitor_dpi_aware()
    check(
        "process is per-monitor DPI aware",
        coordinates_are_trustworthy(),
        f"{state}, awareness={current_awareness()}",
    )

    finder = BrowserWindowFinder()
    observer = ScreenPageObserver(finder=finder)
    cursor = CursorDriver()
    catalog = WindowsAppCatalog()

    def launch_default_browser() -> None:
        resolution = catalog.resolve("Default Browser")
        if resolution.status != "resolved" or resolution.entry is None:
            raise OSError("No default browser is registered.")
        catalog.launch(resolution.entry)

    control = ScreenBrowserControl(
        observer=observer,
        cursor=cursor,
        window_launcher=launch_default_browser,
    )

    print("\n2. Browser window discovery and cold launch")
    windows = finder.list_windows()
    for window in windows:
        print(f"      {window.process_name} hwnd={window.handle} "
              f"active={window.is_active} {window.page_title[:50]!r}")
    cursor.begin_run()
    opened_tab = False
    try:
        print("\n3. Navigation in a fresh tab (moving your real mouse now)")
        if windows:
            target = finder.active_window() or windows[0]
            if not control._focuser(target.handle):
                check("browser can be brought to the front", False)
                return 1
            check("browser can be brought to the front", True)
            time.sleep(0.3)
            cursor.press("ctrl", "t")
            opened_tab = True
            time.sleep(0.8)
            navigation_window = target.handle
        else:
            # Exercises ScreenBrowserControl's real cold-start path: no
            # pre-opened window, no readable document, and no about:blank
            # prerequisite before Ctrl+L.
            navigation_window = None
            opened_tab = True

        started = time.time()
        result = control.navigate(_TARGET_URL, window=navigation_window)
        elapsed = time.time() - started
        check(
            f"navigate to {_TARGET_URL}",
            result.status == "navigated",
            f"{result.status}: {result.message} ({elapsed:.2f}s)",
        )
        if result.status != "navigated":
            return 1
        windows = finder.list_windows()
        if not check("browser exists after navigation", bool(windows)):
            return 1
        target = finder.active_window() or windows[0]

        print("\n4. Observing the navigated page")
        timings = []
        page = None
        for _ in range(3):
            started = time.time()
            page = control.focus_and_observe(target.handle)
            timings.append(time.time() - started)
        check(
            "navigated page is observable",
            page is not None and page.status == "observed",
            f"{page.status if page else 'none'}, url={page.url if page else ''!r}",
        )
        if page is None or page.status != "observed":
            return 1
        check(
            "observation is fast",
            min(timings) < 1.0,
            f"best {min(timings):.3f}s, worst {max(timings):.3f}s",
        )
        check(
            "elements have on-screen click points",
            all(element.click_point != (0, 0) for element in page.elements)
            and bool(page.elements),
            f"{len(page.elements)} elements",
        )
        links = [
            element for element in page.elements if element.role == "link"
        ]
        for element in page.elements:
            print(f"      {element.display}")
        if not check("the page exposes a link to click", bool(links)):
            return 1

        print("\n5. Real cursor click")
        link = links[0]
        print(f"      clicking {link.label!r} at {link.click_point}")
        started = time.time()
        clicked = control.click(
            link.index,
            expected_label=link.label,
            expected_scan_id=page.scan_id,
            window=target.handle,
        )
        elapsed = time.time() - started
        check(
            "click is verified by an observed change",
            clicked.status == "clicked" and clicked.verified is True,
            f"{clicked.status}: {clicked.message} ({elapsed:.2f}s)",
        )
        check(
            "click actually navigated where the link pointed",
            _EXPECTED_LINK_HOST in (clicked.url or ""),
            f"landed on {clicked.url!r}",
        )

        print("\n6. Stale-index refusal")
        stale = control.click(
            link.index,
            expected_label="a label this element never had",
            expected_scan_id=page.scan_id,
            window=target.handle,
        )
        check(
            "a changed/stale element is refused, not clicked",
            stale.status in {"not_found", "unavailable"},
            f"{stale.status}: {stale.message}",
        )
    finally:
        if opened_tab:
            print("\n7. Cleanup")
            cursor.press("ctrl", "w")
            time.sleep(0.4)
            print("      closed the test tab")
        cursor.end_run()
        print("      pointer returned to where you left it")

    print("\n" + "=" * 68)
    print(f"passed {len(_passed)}   failed {len(_failed)}")
    if _failed:
        for name in _failed:
            print(f"  FAILED: {name}")
    print("=" * 68)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
