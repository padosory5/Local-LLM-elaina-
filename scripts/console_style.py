"""Small, dependency-free helpers for readable terminal output."""

from __future__ import annotations

import os
import sys
from typing import TextIO


GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def _enable_windows_vt() -> bool:
    """Enable ANSI color handling in a Windows console when available."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        stdout_handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(stdout_handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError):
        return False


def colors_enabled(stream: TextIO | None = None) -> bool:
    """Return whether status labels should contain terminal color codes."""
    stream = sys.stdout if stream is None else stream
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR", "").casefold() not in {"", "0", "false"}:
        return True
    if not bool(getattr(stream, "isatty", lambda: False)()):
        return False
    return _enable_windows_vt()


def status_label(
    passed: bool,
    *,
    stream: TextIO | None = None,
    force: bool | None = None,
) -> str:
    """Return a green PASS or red FAIL label for terminal status output."""
    label = "PASS" if passed else "FAIL"
    use_color = colors_enabled(stream) if force is None else force
    if not use_color:
        return label
    color = GREEN if passed else RED
    return f"{color}{label}{RESET}"
