"""FAST/DEEP routing, in shadow: which model *would* answer this turn.

Nothing here changes an answer. The engine asks ``decide`` once per answered
turn, after the router and the response budget have spoken, and records the
result in the turn's trace (context ``shadow_route``); the turn is still
answered the way it always is. The same function scores the frozen
FAST/DEEP evidence offline (evals/fastdeep_shadow.py), so what is evaluated
is exactly what records.

It reads only what the turn already has -- the 8B router's structured
reading, the response budget chosen from it, whether a deterministic domain
claimed the turn, and the previous turn's budget. No model call, no phrase
list, nothing from an evaluation.

**The rule.** DEEP is for an explanation that builds on the answer just
given. In the frozen baseline (docs/FAST_DEEP_BASELINE.md) the 27B was
stably better on 8 of 9 confusion follow-ups and on no factual,
calculation or time turn; the Stage B diagnostic (docs/FAST_DEEP_STAGEB.md)
found that most of that follow-up gain survives with the 8B doing the
reading, so the 8B's reading is what this rule is allowed to use. First-time
explanations stay FAST: no existing signal told the ones the 27B improved
from the rest (docs/FAST_DEEP_ROUTING.md).

A turn "builds on the answer just given" when any of three independent
signals says so:

* ``router_follow_up`` -- the router marks the turn a follow-up;
* ``after_explanation`` -- the previous turn's budget was an explanation;
* ``re_explanation`` -- the router reads it as a clarification: the person
  asking her to explain her last answer again (R9).

Two or more agreeing makes the decision high-confidence; one, low.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

FAST, DEEP = "FAST", "DEEP"
EXPLANATION_BUDGETS = ("explain", "elaborate")


@dataclass(frozen=True)
class ShadowSignals:
    """Everything the decision reads, all of it already in the turn."""

    intent: str = ""
    speech_act: str = ""
    answer_shape: str = ""
    is_follow_up: bool = False
    topic_shift: bool = False
    detailed: bool = False
    budget: str = ""             # this turn's response budget
    shape: str = ""
    previous_budget: str = ""    # the previous turn's, "" when it had none
    domain_claimed: bool = False
    language: str = ""
    router_confidence: float | None = None


@dataclass(frozen=True)
class ShadowDecision:
    decision: str                # FAST or DEEP
    reason: str
    confidence: str              # "high", "low", or "" for FAST
    continuation: tuple          # the continuation signals that held
    signals: ShadowSignals

    def as_record(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "confidence": self.confidence,
            "continuation": list(self.continuation),
            "signals": asdict(self.signals),
        }


def continuation(signals: ShadowSignals) -> tuple[str, ...]:
    """The signals saying this turn builds on the answer just given."""
    held = []
    if signals.is_follow_up:
        held.append("router_follow_up")
    if signals.previous_budget in EXPLANATION_BUDGETS:
        held.append("after_explanation")
    if signals.intent == "clarification":
        held.append("re_explanation")
    return tuple(held)


def decide(signals: ShadowSignals) -> ShadowDecision:
    """FAST unless the turn is an explanation that builds on the last one."""
    held = continuation(signals)

    def fast(reason: str) -> ShadowDecision:
        return ShadowDecision(FAST, reason, "", held, signals)

    if signals.domain_claimed:
        return fast("a deterministic domain answers it")
    if signals.budget not in EXPLANATION_BUDGETS:
        return fast("not an explanation")
    if signals.topic_shift:
        return fast("a new topic")
    if not held:
        return fast("a first explanation; no signal yet tells which ones gain")
    return ShadowDecision(
        DEEP,
        "an explanation building on the answer just given",
        "high" if len(held) >= 2 else "low",
        held,
        signals,
    )


def signals_for(route, budget, *, previous_budget: str, domain_claimed: bool,
                language: str, detailed: bool) -> ShadowSignals:
    """The signals, from the engine's own objects for the turn."""
    def read(name, default=""):
        return getattr(route, name, default)

    confidence = read("confidence", None)
    return ShadowSignals(
        intent=str(read("intent") or ""),
        speech_act=str(read("speech_act") or ""),
        answer_shape=str(read("answer_shape") or ""),
        is_follow_up=bool(read("is_follow_up", False)),
        topic_shift=bool(read("topic_shift", False)),
        detailed=bool(detailed),
        budget=str(getattr(budget, "kind", "") or ""),
        shape=str(getattr(budget, "shape", "") or ""),
        previous_budget=str(previous_budget or ""),
        domain_claimed=bool(domain_claimed),
        language=str(language or ""),
        router_confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
    )
