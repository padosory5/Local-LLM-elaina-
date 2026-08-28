"""Screen-native browser control (Phase 4E).

Operates the browser window the user already has open, by reading its live
UI Automation tree and driving the real mouse and keyboard -- rather than
launching a second, isolated, logged-out browser and speaking CDP to it.

The pointer/keyboard driver and DPI handling live in
``tools.screen_control``: they are shared with the whole-desktop driver and
were never browser-specific.
"""

from tools.screen_control.dpi import ensure_per_monitor_dpi_aware

ensure_per_monitor_dpi_aware()
