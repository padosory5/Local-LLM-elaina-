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


register_precondition("screen_capture_enabled", _screen_capture_enabled)
