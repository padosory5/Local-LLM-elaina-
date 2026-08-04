from __future__ import annotations

import time
import json
from dataclasses import dataclass, replace
from typing import Any


# These intents can invoke a specialist agent or an external/read-only tool.
# Normal conversation and stable knowledge stay with Elaina herself.
AGENT_EXECUTION_INTENTS = frozenset({
    "web_search",
    "project_question",
    "project_edit",
    "git_commit",
    "git_publish",
    "screen_analysis",
    "agent_create",
    "calendar_action",
    "entity_correction",
    "fact_check",
})

# Agent Builder is intentionally absent: it may be used only after the user
# directly requests a new agent. It must never be suggested as a generic way
# to create an avatar, UI asset, document, or other unsupported capability.
AGENT_OFFERABLE_INTENTS = frozenset({
    "web_search",
    "project_question",
    "project_edit",
    "git_commit",
    "git_publish",
    "screen_analysis",
    "calendar_action",
    "entity_correction",
    "fact_check",
})

CONSENT_DECISIONS = frozenset({
    "accept",
    "reject",
    "modify",
    "unclear",
})


@dataclass(frozen=True)
class PendingAgentOffer:
    """A task Elaina offered but has not been authorized to start."""

    intent: str
    request: str
    reason: str
    created_at: float
    expires_at: float

    def public_context(self) -> dict[str, str | int]:
        return {
            "intent": self.intent,
            "request": self.request,
            "reason": self.reason,
            "expires_in_seconds": max(
                0,
                int(self.expires_at - time.monotonic()),
            ),
        }


@dataclass(frozen=True)
class ConsentResult:
    status: str
    offer: PendingAgentOffer | None = None
    request: str = ""


@dataclass(frozen=True)
class SemanticConsentDecision:
    decision: str
    confidence: float
    reason: str = ""
    modified_request: str = ""


class SemanticConsentClassifier:
    """Judge one reply against one pending action before normal routing."""

    def __init__(
        self,
        client: Any,
        model: str,
        keep_alive: int | str = -1,
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive

    def classify(
        self,
        user_input: str,
        offer: Any,
        recent_turns: list[dict[str, str]] | None = None,
    ) -> SemanticConsentDecision:
        prompt = (
            "Decide how the user's latest reply relates to Elaina's one "
            "pending optional action. Judge conversational meaning, not "
            "keywords. Return JSON only.\n\n"
            "decision must be exactly one of:\n"
            "accept: the user authorizes the offered task\n"
            "reject: the user declines or postpones it\n"
            "modify: the user authorizes a changed version of it\n"
            "unrelated: the user changed topics or made a separate request\n"
            "unclear: the reply cannot safely be understood\n\n"
            "A short reply can accept or reject only because of its context. "
            "Do not treat every affirmative-sounding phrase as permission. "
            "For modify, return the complete revised task in "
            "modified_request. Otherwise modified_request is empty.\n\n"
            f"Pending intent: {offer.intent}\n"
            f"Pending task: {offer.request}\n"
            "Recent turns:\n"
            f"{json.dumps((recent_turns or [])[-4:], ensure_ascii=False)}\n"
            f"Latest reply: {user_input}"
        )
        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 80},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            payload = json.loads(
                str(self._value(message, "content", ""))
            )
            decision = str(payload.get("decision", "")).strip().lower()
            if decision not in {
                "accept",
                "reject",
                "modify",
                "unrelated",
                "unclear",
            }:
                raise ValueError("Unknown consent decision.")
            confidence = max(
                0.0,
                min(float(payload.get("confidence", 0)), 1.0),
            )
            return SemanticConsentDecision(
                decision=decision,
                confidence=confidence,
                reason=str(payload.get("reason", "")).strip(),
                modified_request=str(
                    payload.get("modified_request", "")
                ).strip(),
            )
        except Exception as error:
            print(
                "[Consent] Semantic decision failed safely: "
                f"{type(error).__name__}: {error}"
            )
            return SemanticConsentDecision(
                decision="unclear",
                confidence=0.0,
                reason="The semantic consent decision could not be verified.",
            )

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)


class AgentConsentGate:
    """
    Hold optional agent work until the semantic router confirms consent.

    This class deliberately contains no yes/no phrase matching. The language
    model decides whether a reply accepts, rejects, modifies, or fails to
    answer the offer; this class only enforces the resulting state transition.
    """

    def __init__(self, expiry_seconds: int = 300) -> None:
        self.expiry_seconds = max(30, int(expiry_seconds))
        self._pending: PendingAgentOffer | None = None

    def offer(
        self,
        *,
        intent: str,
        request: str,
        reason: str = "",
    ) -> PendingAgentOffer:
        if intent not in AGENT_OFFERABLE_INTENTS:
            raise ValueError(f"Intent cannot be delegated: {intent}")
        request = str(request).strip()
        if not request:
            raise ValueError("An agent offer needs a concrete request.")

        now = time.monotonic()
        self._pending = PendingAgentOffer(
            intent=intent,
            request=request,
            reason=str(reason).strip(),
            created_at=now,
            expires_at=now + self.expiry_seconds,
        )
        return self._pending

    def peek(self) -> PendingAgentOffer | None:
        if self._pending is None:
            return None
        if time.monotonic() >= self._pending.expires_at:
            self._pending = None
            return None
        return self._pending

    def clear(self) -> None:
        self._pending = None

    def resolve(
        self,
        decision: str,
        *,
        modified_request: str = "",
    ) -> ConsentResult:
        offer = self.peek()
        if offer is None:
            return ConsentResult(status="no_offer")

        decision = str(decision).strip().lower()
        if decision not in CONSENT_DECISIONS:
            return ConsentResult(status="unclear", offer=offer)

        if decision == "unclear":
            return ConsentResult(status="unclear", offer=offer)

        self._pending = None
        if decision == "reject":
            return ConsentResult(status="rejected", offer=offer)

        request = offer.request
        status = "accepted"
        if decision == "modify":
            revised = str(modified_request).strip()
            if not revised:
                self._pending = offer
                return ConsentResult(status="unclear", offer=offer)
            request = revised
            status = "accepted_modified"

        return ConsentResult(
            status=status,
            offer=offer,
            request=request,
        )


def apply_agent_permission(
    gate: AgentConsentGate,
    route: Any,
    *,
    user_input: str,
    has_explicit_attachment: bool,
    continuing_agent_flow: bool,
    available_intents: set[str] | None = None,
) -> tuple[Any, str]:
    """Convert the router's semantic consent judgment into a safe route."""
    pending = gate.peek()

    if route.intent == "agent_consent":
        result = gate.resolve(
            route.consent_decision,
            modified_request=(
                route.offered_request or route.normalized_request
            ),
        )
        if result.status in {"accepted", "accepted_modified"}:
            if result.offer is None:
                return replace(
                    route,
                    intent="conversation",
                    action_requested=False,
                ), (
                    "There is no active agent offer to accept. Respond "
                    "naturally and do not claim that an agent started."
                )

            # Import here to keep the state-only consent module lightweight.
            from brain.intent_router import IntentDecision

            accepted_request = result.request or result.offer.request
            return IntentDecision(
                intent=result.offer.intent,
                confidence=route.confidence,
                normalized_request=accepted_request,
                reason=(
                    "The semantic router confirmed the user's permission "
                    "for the pending agent offer."
                ),
                search_query=(
                    accepted_request
                    if result.offer.intent == "web_search"
                    else ""
                ),
                topic=route.topic,
                entity=route.entity,
                aliases=route.aliases,
                is_follow_up=True,
                speech_act="action_request",
                action_requested=True,
                action_target=(
                    route.action_target or result.offer.request
                ),
            ), ""

        if result.status == "rejected":
            return replace(
                route,
                intent="conversation",
                normalized_request=user_input,
                reason="The user declined the pending agent offer.",
                action_requested=False,
                action_target="",
            ), (
                "The user declined the optional agent task. Acknowledge that "
                "naturally in one short sentence. Do not invoke or claim to "
                "have invoked any agent."
            )

        return replace(
            route,
            intent="conversation",
            normalized_request=user_input,
            reason="The reply did not clearly resolve the agent offer.",
            action_requested=False,
            action_target="",
        ), (
            "The user's reply did not clearly accept or reject the pending "
            "offer. Briefly ask whether they want Elaina to proceed with this "
            f"task: {pending.request if pending else ''}"
        )

    # A normally routed new topic invalidates the old offer. This prevents a
    # later unrelated affirmation from accidentally authorizing stale work.
    if pending is not None:
        gate.clear()

    if route.intent == "agent_offer":
        offered_intent = route.offered_intent
        offered_request = route.offered_request or route.normalized_request
        if (
            offered_intent not in AGENT_OFFERABLE_INTENTS
            or not offered_request.strip()
            or (
                available_intents is not None
                and offered_intent not in available_intents
            )
        ):
            return replace(
                route,
                intent="conversation",
                normalized_request=user_input,
                action_requested=False,
            ), ""

        offer = gate.offer(
            intent=offered_intent,
            request=offered_request,
            reason=route.reason,
        )
        return route, (
            "No specialist agent has been invoked. Respond to what the user "
            "said first, then naturally offer the optional task below and ask "
            "whether they want you to proceed. Do not imply that work has "
            "started.\n"
            f"Optional task: {offer.request}\n"
            f"Available specialist intent: {offer.intent}"
        )

    needs_agent_permission = route.intent in AGENT_EXECUTION_INTENTS
    already_authorized = (
        route.action_requested
        or has_explicit_attachment
        or continuing_agent_flow
    )
    can_offer = (
        route.intent in AGENT_OFFERABLE_INTENTS
        and (
            available_intents is None
            or route.intent in available_intents
        )
    )
    if needs_agent_permission and not already_authorized and can_offer:
        offer = gate.offer(
            intent=route.intent,
            request=route.normalized_request,
            reason=route.reason,
        )
        return replace(
            route,
            intent="agent_offer",
            action_requested=False,
            action_target="",
            offered_intent=offer.intent,
            offered_request=offer.request,
        ), (
            "No specialist agent has been invoked. Respond briefly, then ask "
            "whether the user wants you to use the relevant agent for this "
            f"task: {offer.request}"
        )

    if needs_agent_permission and not already_authorized:
        return replace(
            route,
            intent="conversation",
            normalized_request=user_input,
            action_requested=False,
            action_target="",
        ), (
            "No compatible specialist agent was authorized for this request. "
            "Respond normally without offering or claiming an unavailable "
            "capability."
        )

    return route, ""
