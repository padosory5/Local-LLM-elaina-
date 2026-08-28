"""Find the browser windows the user already has open (Phase 4E).

Phase 4C's answer to "which page am I operating?" was to launch a private
browser and own it outright.  This package's answer is the opposite: operate
a window that already exists, with the user's real profile and real logins.
That makes *identifying* the window the first safety boundary, so the tests
here are deliberately narrow.

Window class is not a usable test.  Measured on the development machine, the
ChatGPT and Claude desktop apps both present ``Chrome_WidgetWin_1`` windows
-- the same class Chrome and Whale use -- because they embed WebView2.  They
expose about a dozen UI Automation nodes and no page at all.  The executable
behind the window is the signal that actually separates a browser from an
app that merely contains one, so that is what this module matches on.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from dataclasses import dataclass

from tools.screen_control.dpi import ensure_per_monitor_dpi_aware

ensure_per_monitor_dpi_aware()

try:
    import win32gui as _win32gui
    import win32process as _win32process
except Exception:  # pragma: no cover - exercised only when pywin32 is absent
    _win32gui = None
    _win32process = None

# Chromium-family browsers whose page tree this package can read, plus
# Firefox, which exposes an accessibility tree of its own.  A name not in
# this set is not treated as a browser: an unknown executable that happens
# to host a web view would otherwise be driven as if it were one.
_BROWSER_EXECUTABLES = frozenset({
    "whale.exe", "chrome.exe", "msedge.exe", "brave.exe",
    "vivaldi.exe", "opera.exe", "opera_gx.exe", "firefox.exe",
    "chromium.exe", "thorium.exe",
})

# WebView2/Electron hosts. Listed explicitly rather than left to fall
# through the allowlist so the reason is documented where someone would
# otherwise be tempted to "fix" a missing browser by loosening the test.
_EMBEDDED_WEBVIEW_EXECUTABLES = frozenset({
    "msedgewebview2.exe", "electron.exe",
})

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_DWMWA_CLOAKED = 14
_MAX_PATH_CHARACTERS = 32768
# Chromium appends " - <browser name>" to every window title. Anything
# longer than this is page text that merely contains a dash, not chrome.
_MAX_TITLE_SUFFIX_LENGTH = 30


@dataclass(frozen=True)
class BrowserWindow:
    """One live, visible browser window that could be operated."""

    handle: int
    title: str
    process_id: int
    process_name: str
    is_active: bool = False
    # Physical screen pixels, meaningful only under per-monitor DPI
    # awareness -- see dpi.py.
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def identity(self) -> str:
        return f"hwnd:{self.handle}"

    @property
    def page_title(self) -> str:
        """The window title with the browser's own suffix removed.

        Chromium titles a window "page - browser"; the suffix is chrome, not
        page identity, and repeating it back to the user as if it were part
        of the page name reads as noise.
        """
        title = str(self.title or "").strip()
        head, separator, tail = title.rpartition(" - ")
        if separator and head and len(tail) <= _MAX_TITLE_SUFFIX_LENGTH:
            return head.strip()
        return title


def executable_is_browser(process_name: str) -> bool:
    """True only for executables that are themselves a web browser."""
    name = str(process_name or "").strip().lower()
    if not name:
        return False
    name = name.replace("/", "\\").rsplit("\\", 1)[-1]
    if name in _EMBEDDED_WEBVIEW_EXECUTABLES:
        return False
    return name in _BROWSER_EXECUTABLES


def _process_image_name(process_id: int) -> str:
    """Full executable path for a pid, or "" when it cannot be read."""
    try:
        kernel32 = ctypes.windll.kernel32
    except Exception:  # pragma: no cover - non-Windows host
        return ""
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, int(process_id),
    )
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARACTERS)
        size = wintypes.DWORD(_MAX_PATH_CHARACTERS)
        if kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size),
        ):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _is_cloaked(handle: int) -> bool:
    """True for windows Windows keeps alive but hidden (virtual desktops).

    A cloaked window is invisible to the user even though IsWindowVisible
    reports it as visible, and clicking into one moves the pointer somewhere
    nothing is drawn.
    """
    try:
        cloaked = ctypes.c_int(0)
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(handle),
            ctypes.c_uint(_DWMWA_CLOAKED),
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return result == 0 and cloaked.value != 0
    except Exception:
        return False


class BrowserWindowFinder:
    """Enumerate visible browser windows, foreground first."""

    def __init__(
        self,
        *,
        enumerator=None,
        process_name_reader=None,
        foreground_reader=None,
    ) -> None:
        self._enumerator = enumerator or self._default_enumerator
        self._process_name_reader = process_name_reader or _process_image_name
        self._foreground_reader = foreground_reader or self._default_foreground

    @property
    def available(self) -> bool:
        return _win32gui is not None

    @staticmethod
    def _default_foreground() -> int:
        if _win32gui is None:  # pragma: no cover - absent pywin32
            return 0
        try:
            return int(_win32gui.GetForegroundWindow())
        except Exception:
            return 0

    @staticmethod
    def _default_enumerator() -> list[tuple[int, str, int, tuple[int, int, int, int]]]:
        """(handle, title, pid, rect) for every visible, uncloaked window."""
        if _win32gui is None or _win32process is None:  # pragma: no cover
            return []
        found: list[tuple[int, str, int, tuple[int, int, int, int]]] = []

        def _collect(handle: int, _accumulator) -> bool:
            try:
                if not _win32gui.IsWindowVisible(handle):
                    return True
                title = _win32gui.GetWindowText(handle) or ""
                if not title.strip():
                    return True
                if _is_cloaked(handle):
                    return True
                _, process_id = _win32process.GetWindowThreadProcessId(handle)
                rect = _win32gui.GetWindowRect(handle)
            except Exception:
                return True
            found.append((int(handle), title, int(process_id), tuple(rect)))
            return True

        try:
            _win32gui.EnumWindows(_collect, None)
        except Exception:
            return []
        return found

    def list_windows(self) -> tuple[BrowserWindow, ...]:
        """Every open browser window, the foreground one first."""
        try:
            foreground = int(self._foreground_reader() or 0)
        except Exception:
            foreground = 0
        windows: list[BrowserWindow] = []
        try:
            rows = list(self._enumerator())
        except Exception:
            # A window enumeration that fails mid-turn must not take the
            # whole reply down with it -- "no browser window" is a state
            # the caller already handles honestly.
            return ()
        for handle, title, process_id, rect in rows:
            try:
                image = self._process_name_reader(process_id)
            except Exception:
                image = ""
            if not executable_is_browser(image):
                continue
            windows.append(
                BrowserWindow(
                    handle=int(handle),
                    title=str(title),
                    process_id=int(process_id),
                    process_name=str(image).replace("/", "\\").rsplit("\\", 1)[-1],
                    is_active=int(handle) == foreground,
                    rect=tuple(int(value) for value in rect),
                )
            )
        windows.sort(key=lambda window: (not window.is_active, window.handle))
        return tuple(windows)

    def active_window(self) -> BrowserWindow | None:
        """The foreground browser window, or the only one, else None.

        Returning None when several browser windows are open and none has
        focus is deliberate: guessing which page a request refers to is the
        C-06 failure the acceptance tests already forbid, so the caller is
        made to ask instead.
        """
        windows = self.list_windows()
        if not windows:
            return None
        if windows[0].is_active:
            return windows[0]
        if len(windows) == 1:
            return windows[0]
        return None

    def window_for_handle(self, handle: int) -> BrowserWindow | None:
        for window in self.list_windows():
            if window.handle == int(handle):
                return window
        return None
