"""Structured precondition checks for abilities and their tools.

A precondition returns (ok, message) instead of letting a caller proceed
silently on missing state or fail with a generic error -- "screen vision is
turned off in configuration" instead of a blank/confusing result. Registered
by name so an ability's declared `preconditions:` (see agents/base.py) can
eventually be checked generically; for now callers invoke one by name
directly at the one point in the codebase that needs it.
"""

from __future__ import annotations

from typing import Any, Callable

PreconditionCheck = Callable[..., tuple[bool, str]]

_CHECKS: dict[str, PreconditionCheck] = {}


def register_precondition(name: str, check: PreconditionCheck) -> None:
    _CHECKS[name] = check


def check_precondition(name: str, **context: Any) -> tuple[bool, str]:
    """Run a registered precondition by name.

    An unnamed/unregistered precondition passes silently -- there is
    nothing yet to enforce for it, matching this phase's additive scope.
    """
    check = _CHECKS.get(name)
    if check is None:
        return True, ""
    return check(**context)


def _screen_capture_enabled(
    *, screen_monitor: Any = None, **_: Any
) -> tuple[bool, str]:
    if screen_monitor is None or not getattr(screen_monitor, "enabled", False):
        return False, "Screen vision is turned off in configuration."
    return True, ""


def _computer_control_mode_enabled(
    *, computer_control_mode: Any = None, **_: Any
) -> tuple[bool, str]:
    if computer_control_mode is None or not getattr(
        computer_control_mode, "enabled", False,
    ):
        return False, (
            "Desktop Control Mode is off, so native app/window control "
            "isn't available right now."
        )
    return True, ""


def _browser_page_control_enabled(
    *,
    browser_control_enabled: bool = True,
    computer_control_mode: Any = None,
    **_: Any,
) -> tuple[bool, str]:
    if not browser_control_enabled:
        return False, "Browser-page control is disabled in configuration."
    # Production passes the session-owned toggle.  ``None`` is retained for
    # legacy standalone planners/tests that intentionally have no desktop
    # mode concept; when the toggle exists, browser automation must obey the
    # same visible Control On/Off boundary as native UI automation.
    if computer_control_mode is not None and not getattr(
        computer_control_mode, "enabled", False,
    ):
        return False, (
            "Desktop Control Mode is off, so browser-page control isn't "
            "available right now."
        )
    return True, ""


def _web_search_enabled(
    *, web_search_enabled: bool = True, **_: Any
) -> tuple[bool, str]:
    if not web_search_enabled:
        return False, "Web search is disabled in configuration."
    return True, ""


register_precondition("screen_capture_enabled", _screen_capture_enabled)
register_precondition(
    "computer_control_mode_enabled", _computer_control_mode_enabled,
)
register_precondition(
    "browser_page_control_enabled", _browser_page_control_enabled,
)
register_precondition("web_search_enabled", _web_search_enabled)
