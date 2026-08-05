"""Thread-safe, session-only authorization for bounded desktop control."""

from __future__ import annotations

import threading


class ComputerControlMode:
    """Own the UI toggle state without persisting authorization across runs."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = bool(enabled)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> bool:
        with self._lock:
            self._enabled = bool(enabled)
            return self._enabled

