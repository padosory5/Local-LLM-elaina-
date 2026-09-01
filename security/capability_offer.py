"""Pending consent for an ability Elaina offered in ordinary conversation.

Every other pending-offer gate in this project parks an offer made by a
*planner* (a risky click, a strategy choice, an agent hand-off). None of
them covered the most ordinary case of all: Elaina, mid-conversation, says
"I can check that in the browser -- want me to?" and the user says "ok".

Without a parked offer, that "ok" routes as a fresh, contextless turn --
observed live producing the exact same sentence a second time, with nothing
ever opening. This gate closes that loop using the same shape as
security/task_strategy_consent.py, so agents.consent.SemanticConsentClassifier
reads it with no special-casing: plain ``intent``/``request`` fields, a
one-shot ``peek``/``clear``, and a short expiry so a much later "sure" can
never start a forgotten action.

``request`` is a plain, unambiguous action description ("Check prices in the
browser for: ...") and never the spoken offer sentence -- that mistake was
already made once with PendingStrategyOffer and confused the classifier into
reading declines as acceptance.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingCapabilityOffer:
    intent: str
    request: str
    capability_id: str
    goal: str
    offer_text: str
    created_at: float
    expires_at: float
    # Whether Elaina raised this herself rather than being asked. A
    # suggestion is not a question awaiting an answer, and must not be
    # allowed to interpret whatever the person says next as a reply to it.
    proactive: bool = False
    # When an offer pauses an active recommendation, this ID points back to
    # the full payload in TaskSessionStore and the canonical query is kept as
    # a defensive snapshot. Acceptance resumes it instead of routing the
    # generated offer sentence as a new request.
    task_id: str = ""
    task_query: str = ""

    def public_context(self) -> dict[str, str | int]:
        return {
            "intent": self.intent,
            "request": self.request,
            "capability": self.capability_id,
            "expires_in_seconds": max(0, int(self.expires_at - time.monotonic())),
        }


class CapabilityOfferGate:
    """Hold one conversational "want me to?" until the user answers it."""

    def __init__(self, expiry_seconds: int = 120) -> None:
        self.expiry_seconds = max(15, int(expiry_seconds))
        self._pending: PendingCapabilityOffer | None = None

    def offer(
        self,
        *,
        capability_id: str,
        goal: str,
        offer_text: str,
        intent: str = "computer_action",
        proactive: bool = False,
        task_id: str = "",
        task_query: str = "",
    ) -> PendingCapabilityOffer:
        now = time.monotonic()
        goal = " ".join(str(goal).split()).strip()
        self._pending = PendingCapabilityOffer(
            intent=intent,
            request=f"Use {capability_id} to handle: {goal}",
            capability_id=str(capability_id),
            goal=goal,
            offer_text=" ".join(str(offer_text).split()).strip(),
            created_at=now,
            expires_at=now + self.expiry_seconds,
            proactive=bool(proactive),
            task_id=str(task_id or "").strip(),
            task_query=" ".join(str(task_query or "").split()).strip(),
        )
        return self._pending

    def peek(self) -> PendingCapabilityOffer | None:
        if self._pending is None:
            return None
        if time.monotonic() >= self._pending.expires_at:
            self._pending = None
            return None
        return self._pending

    def clear(self) -> None:
        self._pending = None
