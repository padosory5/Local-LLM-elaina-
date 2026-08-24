"""Short-lived, one-shot consent state for a paused multi-step task.

Mirrors security/computer_consent.py's ComputerConsentGate exactly, but
parks a whole TaskState (not just one prepared action) so a confirmation
mid-task doesn't lose everything already gathered before it. intent/request
are plain fields, not derived, because agents.consent.SemanticConsentClassifier
reads offer.intent/offer.request directly -- confirmed against
PendingComputerAction's own shape, the existing precedent for this contract.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PendingTaskAction:
    intent: str
    request: str
    task_state: Any  # brain.task_planner.TaskState
    step: Any  # brain.task_planner.TaskStep
    capability: str
    prepared: Any  # tools.computer_control.computer_control.PreparedComputerAction
    reason: str
    created_at: float
    expires_at: float

    def public_context(self) -> dict[str, str | int]:
        return {
            "intent": self.intent,
            "request": self.request,
            "capability": self.capability,
            "reason": self.reason,
            "expires_in_seconds": max(
                0,
                int(self.expires_at - time.monotonic()),
            ),
        }


class TaskConsentGate:
    """Hold one paused task until the user answers its pending step."""

    def __init__(self, expiry_seconds: int = 90) -> None:
        self.expiry_seconds = max(15, int(expiry_seconds))
        self._pending: PendingTaskAction | None = None

    def offer(
        self,
        *,
        task_state: Any,
        step: Any,
        capability: str,
        prepared: Any,
        reason: str = "",
    ) -> PendingTaskAction:
        now = time.monotonic()
        self._pending = PendingTaskAction(
            intent="task_action",
            request=step.sub_goal,
            task_state=task_state,
            step=step,
            capability=capability,
            prepared=prepared,
            reason=str(reason).strip(),
            created_at=now,
            expires_at=now + self.expiry_seconds,
        )
        return self._pending

    def peek(self) -> PendingTaskAction | None:
        if self._pending is None:
            return None
        if time.monotonic() >= self._pending.expires_at:
            self._pending = None
            return None
        return self._pending

    def clear(self) -> None:
        self._pending = None
