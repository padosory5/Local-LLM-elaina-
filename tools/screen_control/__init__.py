"""Shared screen-native control primitives (Phase 4E/4F).

Reading a window's live UI Automation tree and driving the real mouse and
keyboard is not browser-specific, so these live here rather than inside
``tools/screen_browser/``: the browser driver and the whole-desktop driver
are two users of the same pointer, the same keyboard, and the same
coordinate space.

DPI awareness is established at import time because every coordinate this
package produces is meaningless without it. See dpi.py for the measurements.
"""

from tools.screen_control.dpi import ensure_per_monitor_dpi_aware

ensure_per_monitor_dpi_aware()
