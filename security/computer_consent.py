"""Short-lived, one-shot consent state for local computer actions."""

from __future__ import annotations

import time
from dataclasses import dataclass

from tools.computer_control import PreparedComputerAction


@dataclass(frozen=True)
class PendingComputerAction:
    intent: str
    request: str
    target_name: str
    entry_id: str
    operation: str
    prepared: PreparedComputerAction
    reason: str
    created_at: float
    expires_at: float

    def public_context(self) -> dict[str, str | int]:
        return {
            "intent": self.intent,
            "request": self.request,
            "target_name": self.target_name,
            "operation": self.operation,
            "reason": self.reason,
            "expires_in_seconds": max(
                0,
                int(self.expires_at - time.monotonic()),
            ),
        }


class ComputerConsentGate:
    """Hold one exact prepared computer action until the user answers."""

    def __init__(self, expiry_seconds: int = 90) -> None:
        self.expiry_seconds = max(15, int(expiry_seconds))
        self._pending: PendingComputerAction | None = None

    def offer(
        self,
        *,
        prepared: PreparedComputerAction | None = None,
        target_name: str = "",
        entry_id: str = "",
        reason: str = "",
    ) -> PendingComputerAction:
        if prepared is None:
            target_name = str(target_name).strip()
            entry_id = str(entry_id).strip()
            if not target_name or not entry_id:
                raise ValueError("A computer takeover offer needs a resolved action.")
            prepared = PreparedComputerAction(
                operation="open_app",
                target=target_name,
                display_name=target_name,
                entry_id=entry_id,
            )
        target_name = prepared.display_name.strip()
        if not target_name:
            raise ValueError("A computer takeover offer needs a display name.")
        now = time.monotonic()
        self._pending = PendingComputerAction(
            intent="computer_action",
            request=prepared.request,
            target_name=target_name,
            entry_id=prepared.entry_id,
            operation=prepared.operation,
            prepared=prepared,
            reason=str(reason).strip(),
            created_at=now,
            expires_at=now + self.expiry_seconds,
        )
        return self._pending

    def peek(self) -> PendingComputerAction | None:
        if self._pending is None:
            return None
        if time.monotonic() >= self._pending.expires_at:
            self._pending = None
            return None
        return self._pending

    def clear(self) -> None:
        self._pending = None
