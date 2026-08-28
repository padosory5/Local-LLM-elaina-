"""Process-wide DPI awareness for screen-coordinate control (Phase 4E).

This is a correctness prerequisite for every other module in this package,
not a rendering nicety.  Measured on the development machine: with the
process left at its default ``DPI_AWARENESS_UNAWARE``, Windows reports a
2048x1152 desktop while UI Automation reports element rectangles in the
real 2560x1440 physical space.  A button UI Automation places at x=2080 is
then *off the right edge* of the screen the process believes exists, so a
cursor move to that point is silently clamped and the click lands about a
quarter of the way back across the window -- on whatever happens to be
there.

Declaring PER_MONITOR_AWARE_V2 puts ``GetWindowRect``, ``GetCursorPos``,
``SendInput`` and UI Automation into one shared physical coordinate space.
After the change the same window measured (315,123)-(2195,1308) with its
document at (324,265)-(2134,1299): nested, consistent, and directly usable
as cursor targets.

Windows only allows the awareness of a process to be set before its first
UI call, and only once.  Both facts are why this is a module-level,
idempotent, failure-tolerant call rather than something a caller opts into
per action.
"""

from __future__ import annotations

import ctypes
import sys

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2. Passed as a context *handle*,
# which is a negative sentinel value rather than a pointer.
_PER_MONITOR_AWARE_V2 = -4
# Values returned by GetAwarenessFromDpiAwarenessContext.
_AWARENESS_UNAWARE = 0
_AWARENESS_PER_MONITOR = 2

_state: str = "not_attempted"


def _apply() -> str:
    """Declare per-monitor DPI awareness. Returns a short status string."""
    if not sys.platform.startswith("win"):
        return "unsupported_platform"
    try:
        user32 = ctypes.windll.user32
    except Exception:  # pragma: no cover - non-Windows or restricted host
        return "unavailable"

    # Already per-monitor aware (a host app, or a previous call) is a success,
    # not something to fight: SetProcessDpiAwarenessContext would fail with
    # ACCESS_DENIED and there would be nothing to fix.
    if current_awareness() == _AWARENESS_PER_MONITOR:
        return "already_aware"

    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_PER_MONITOR_AWARE_V2)):
            return "applied"
    except AttributeError:
        # Windows 8.1 .. Windows 10 1607 expose only the older per-process
        # API. PROCESS_PER_MONITOR_DPI_AWARE (2) is the closest equivalent.
        try:
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                return "applied_legacy"
        except Exception:
            return "unavailable"
    except Exception:
        return "unavailable"
    return "refused"


def current_awareness() -> int | None:
    """The process's DPI awareness value, or None when it can't be read."""
    if not sys.platform.startswith("win"):
        return None
    try:
        user32 = ctypes.windll.user32
        return int(
            user32.GetAwarenessFromDpiAwarenessContext(
                user32.GetThreadDpiAwarenessContext()
            )
        )
    except Exception:
        return None


def ensure_per_monitor_dpi_aware() -> str:
    """Make this process share one coordinate space with UI Automation.

    Idempotent and safe to call from any module's import. The first call
    does the work; later calls report what the first one achieved.
    """
    global _state
    if _state == "not_attempted":
        _state = _apply()
    return _state


def coordinates_are_trustworthy() -> bool:
    """True when cursor targets derived from UIA rectangles will land.

    Used by the control layer to refuse to click rather than click blindly
    on a machine where awareness could not be established -- an off-target
    physical click is worse than a reported failure.
    """
    if not sys.platform.startswith("win"):
        return False
    awareness = current_awareness()
    if awareness is None:
        return False
    return awareness != _AWARENESS_UNAWARE
