"""Short-lived, one-shot consent state for a pre-first-step strategy offer.

Mirrors security/task_consent.py's TaskConsentGate exactly, but parks a
whole TaskState *before any step has run* -- there is no prepared click
and no in-progress capability to remember, since the offer this gates is
a strategy choice ("check a specialized website directly, or a quick
overview?"), not a pause on a specific risky action. intent/request are
plain fields, not derived, because agents.consent.SemanticConsentClassifier
reads offer.intent/offer.request directly -- the same contract
PendingTaskAction and PendingComputerAction already satisfy.

Found live: `request` must be a plain action description, not the spoken
offer sentence itself. Every other pending-offer's `request` is a single,
unambiguous task ("Click Buy now.") -- but this offer's spoken text is a
two-option *question* ("...want that or a quicker overview instead?").
Feeding that whole question into the classifier's "Pending task: ..."
context confused it into reading a clear decline ("no, quick overview is
fine") as acceptance, since "quick overview" appears inside the pending
task's own text. `request` is kept separate from `offer_text` (the exact
spoken sentence, used only to re-ask on an unclear reply) for this reason.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PendingStrategyOffer:
    intent: str
    request: str
    task_state: Any  # brain.task_planner.TaskState
    offer_text: str
    created_at: float
    expires_at: float

    def public_context(self) -> dict[str, str | int]:
        return {
            "intent": self.intent,
            "request": self.request,
            "offer_text": self.offer_text,
            "expires_in_seconds": max(
                0,
                int(self.expires_at - time.monotonic()),
            ),
        }


class TaskStrategyConsentGate:
    """Hold one pre-first-step strategy offer until the user answers it."""

    def __init__(self, expiry_seconds: int = 90) -> None:
        self.expiry_seconds = max(15, int(expiry_seconds))
        self._pending: PendingStrategyOffer | None = None

    def offer(
        self,
        *,
        task_state: Any,
        offer_text: str,
    ) -> PendingStrategyOffer:
        now = time.monotonic()
        self._pending = PendingStrategyOffer(
            intent="task_action",
            request=(
                "Check a specialized website directly for: "
                f"{task_state.goal}"
            ),
            task_state=task_state,
            offer_text=offer_text,
            created_at=now,
            expires_at=now + self.expiry_seconds,
        )
        return self._pending

    def peek(self) -> PendingStrategyOffer | None:
        if self._pending is None:
            return None
        if time.monotonic() >= self._pending.expires_at:
            self._pending = None
            return None
        return self._pending

    def clear(self) -> None:
        self._pending = None
