"""Cheap pre-router gate deciding a goal needs the task planner: 4D-4.

Mirrors this codebase's existing pattern of a small deterministic regex
layered in front of an LLM call (see intent_router.py's
_DEICTIC_SURFACE_REFERENCE / _IMPLICIT_BROWSER_ACTION) rather than folding
this judgment into SemanticIntentRouter's already-large single call. A 21st
value on that one call would risk the same prompt-crowding failure already
found and fixed at the computer_action/web_search boundary earlier this
project -- multi-step detection is a different kind of judgment than picking
one of ~20 single-shot labels.

A goal qualifies for four distinct reasons, not one:
(1) a compound, cross-capability sequence (open a native app, then
separately browse);
(2) a single-capability research-then-decide goal (search, then
extract/compare/filter/pick) -- 4D-3's whole reason to exist, triggered
either by a search-ish verb plus a comparison/verification signal
("find hotels and shortlist them"), or by a quantity/price constraint
alone with no verb at all ("give me five hotels under $200" -- "give me"
isn't a search word, but the constraint is just as strong a signal);
(3) a bare follow-up verification question with no leading verb at all,
referring back to prior results ("which of these is actually
available?");
(4) an evaluative recommendation or price-bounded shopping request with
no _BROWSER_CONTROL_VERBS match at all ("best restaurants to go in
Seoul", "cars to buy under 10k") -- the strategy-offer checkpoint (which
decides whether checking a specialized site would help) only ever gets a
chance to fire from inside the task planner, so these need to reach it
just as much as an explicit "find"/"shortlist" request does.
Case (2)'s verb-based half was a real, found-by-testing gap: "find a
hotel in Guam and make me a shortlist" never leaves browser_control, so
the cross-capability check alone never flagged it, and it fell through to
ordinary routing -- where a research-shaped request tends to win as a
one-shot "web_search" that never touches the controlled browser, the task
planner, or the extractor at all, silently routing around the entire 4D
architecture for its own flagship example. Cases (2)'s constraint-based
half and (3) were the same class of gap found by hand-tracing the
Information Acquisition layer's required test scenarios against these
regexes before they existed.

Every regex here only decides whether to *ask* a small, cheap LLM call;
none of them decide is_multistep on their own. A false positive here just
costs one small classification call that then correctly says "not
multistep" -- a false negative would silently drop a real compound,
research, or verification goal, which is worse -- so this stays
deliberately permissive rather than perfectly precise. A casual, truly
open-ended request ("give me some good hotels in Seoul" -- no verb,
no quantity, no price, no verification wording) must stay unmatched by
all three, keeping its fast, cheap path unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from brain.task_discovery_policy import TaskDiscoveryPolicy

_UI_CONTROL_VERBS = re.compile(
    r"\b(?:open|launch|start|run|play|pause|resume|skip|click|type|write|"
    r"select|scroll|focus|close)\b",
    flags=re.IGNORECASE,
)
_BROWSER_CONTROL_VERBS = re.compile(
    r"\b(?:search|google|look\s*up|find|browse|shortlist|compare)\b",
    flags=re.IGNORECASE,
)
_CONJUNCTION = re.compile(
    r"\band\s+then\b|,\s*then\b|\band\s+also\b|\band\b",
    flags=re.IGNORECASE,
)
# A goal needing research-then-synthesize work (4D-3's reason to exist:
# extract into a common structure, apply preferences, compare, explain a
# pick) is just as much a task-planner job as a cross-capability one --
# "find hotels in Guam and shortlist them" never leaves browser_control,
# but it's not one action either. Without this, such a goal has no
# conjunction splitting it into two *different* capability sides, so the
# cross-capability check below never fires, and it falls through to
# ordinary routing -- which, for a research-sounding request, tends to
# win as a one-shot "web_search" that never touches the controlled
# browser, task planner, or extractor at all.
_SYNTHESIS_SIGNAL = re.compile(
    r"\b(?:shortlist|compare|cheapest|best|top\s+\d+|rank|narrow\s+down|"
    r"which\s+(?:one|option)|recommend)\b",
    flags=re.IGNORECASE,
)
# "Give me five hotels under $200" is exactly as much a research-then-
# filter task as "find hotels and shortlist them" -- but has no verb from
# _BROWSER_CONTROL_VERBS at all ("give me" isn't a search word), so it
# needs its own, verb-independent trigger rather than a wider verb list.
_QUANTITY_CONSTRAINT = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"a\s+few|a\s+couple(?:\s+of)?|several)\b[^.?!]{0,25}?"
    r"\b(?:hotels?|options?|results?|restaurants?|places?|choices?|picks?|"
    r"candidates?|items?)\b",
    flags=re.IGNORECASE,
)
_PRICE_CONSTRAINT = re.compile(
    r"[$₩€£¥]\s?\d[\d,.]*"
    r"|\d[\d,.]*\s?(?:dollars?|won|euros?|pounds?)\b"
    r"|\b(?:under|over|below|above|less\s+than|more\s+than)\b[^.?!]{0,20}?\d",
    flags=re.IGNORECASE,
)
# "Check their actual current prices" / "which is actually available" --
# the goal explicitly wants direct/current confirmation, not a snippet.
# Distinct from _SYNTHESIS_SIGNAL (which is about comparing/picking among
# results, not about freshness/currency of one fact).
_VERIFICATION_SIGNAL = re.compile(
    r"\b(?:actual(?:ly)?|current(?:ly)?|availab(?:le|ility)|real-time|"
    r"right\s+now)\b",
    flags=re.IGNORECASE,
)
# "Which of these hotels..." / "are those still..." -- a follow-up
# verification question referring back to a prior turn's results has no
# leading action verb at all, so it needs an independent trigger rather
# than a wider verb list too.
_DEICTIC_BACKREF = re.compile(
    r"\b(?:these|those|them|the\s+ones?)\b|\bwhich\s+(?:of|one)\b",
    flags=re.IGNORECASE,
)
# "Best restaurants to go in Seoul" has no verb from _BROWSER_CONTROL_VERBS
# at all -- "good" is deliberately excluded here (kept as the module's own
# example of a casual request that must stay unescalated); only a genuine
# superlative/evaluative word counts.
_EVALUATIVE_SIGNAL = re.compile(
    r"\b(?:best|top|cheapest|nicest|highest[- ]rated|top[- ]rated|"
    r"most\s+affordable|budget[- ]friendly)\b",
    flags=re.IGNORECASE,
)
# Most category nouns this phrasing names are plural ("restaurants",
# "cars", "hotels", "gpus") -- a 4+ letter word ending in "s" catches that
# generically, with no hardcoded topic list (this must generalize to
# whatever topic comes up next). Requiring 3+ letters before the "s"
# excludes short function words ("is", "was", "his", "yes") that would
# otherwise make this check nearly always true near "best"/"top".
# "way" is deliberately excluded -- "the best way to tie a tie"/"the best
# way to learn Python" is a generic instructional question, not a
# recommendation-among-options request, and this codebase already has an
# explicit test guaranteeing the former never even reaches the cheap
# classification call.
_RECOMMENDATION_NOUN_HINT = re.compile(
    r"\b(?:[a-z]{3,}s|place|spot|site|option)\b", flags=re.IGNORECASE,
)
# "Cars to buy under 10k" names the item before the price limit -- the
# reverse of _QUANTITY_CONSTRAINT's assumed order -- so a purchase-intent
# verb paired with a price constraint is its own, order-independent
# trigger, naming no category itself.
_PURCHASE_INTENT_VERBS = re.compile(
    r"\b(?:buy|purchase|shop\s+for|order|pick\s+up)\b", flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskIntentDecision:
    is_multistep: bool
    confidence: float = 0.0
    reason: str = ""


class TaskIntentGate:
    """Cheaply decide whether a turn deserves the goal-level task planner."""

    def __init__(self, *, client: Any, model: str, keep_alive: Any = -1) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive

    def check(
        self,
        user_input: str,
        *,
        conversation_state: dict[str, Any] | None = None,
    ) -> TaskIntentDecision:
        # Recommendation/discovery requests are a product boundary, not an
        # uncertain semantic distinction.  Route recognised source-worthy
        # categories deterministically so a temporary local-model failure can
        # never send "shortlist hotels" back to the one-shot search path.
        if TaskDiscoveryPolicy.needs_discovery_conversation(user_input):
            return TaskIntentDecision(
                is_multistep=True,
                confidence=1.0,
                reason="This request needs a discovery conversation before research.",
            )
        state = conversation_state or {}
        if (
            state.get("task_candidates")
            and _DEICTIC_BACKREF.search(user_input)
            and (_VERIFICATION_SIGNAL.search(user_input) or _SYNTHESIS_SIGNAL.search(user_input))
        ):
            return TaskIntentDecision(
                is_multistep=True,
                confidence=1.0,
                reason="This follow-up refers to candidates from the current conversation.",
            )
        if not self._looks_multistep(user_input):
            return TaskIntentDecision(is_multistep=False)
        decision = self._classify(user_input)
        if decision.is_multistep:
            return decision
        if self._is_deterministic_research_task(user_input):
            # The model remains useful for ambiguous cross-application goals,
            # but it must not overrule an explicit gather/filter/verify
            # request just because it is unavailable or calls it "one step."
            return TaskIntentDecision(
                is_multistep=True,
                confidence=0.95,
                reason="The request explicitly needs research, filtering, or verification.",
            )
        return decision

    @staticmethod
    def _is_deterministic_research_task(text: str) -> bool:
        return any((
            TaskIntentGate._looks_like_research_synthesis(text),
            TaskIntentGate._looks_like_constrained_discovery(text),
            TaskIntentGate._looks_like_followup_verification(text),
            TaskIntentGate._looks_like_evaluative_recommendation(text),
            TaskIntentGate._looks_like_price_bounded_shopping(text),
        ))

    @staticmethod
    def _looks_multistep(text: str) -> bool:
        if TaskIntentGate._looks_like_research_synthesis(text):
            return True
        if TaskIntentGate._looks_like_constrained_discovery(text):
            return True
        if TaskIntentGate._looks_like_followup_verification(text):
            return True
        if TaskIntentGate._looks_like_evaluative_recommendation(text):
            return True
        if TaskIntentGate._looks_like_price_bounded_shopping(text):
            return True
        return TaskIntentGate._looks_cross_capability(text)

    @staticmethod
    def _looks_like_research_synthesis(text: str) -> bool:
        """A single-capability goal that still needs task-planner-level
        work: gather info, then extract/compare/filter/pick -- 4D-3's own
        reason to exist. No conjunction is required; "find hotels in
        Guam and shortlist them" is one continuous research ask, not two
        sides of an "and". A verification signal ("check their actual
        current prices") counts the same way a comparison signal does --
        both mean the request needs the observe-then-decide loop, not a
        one-shot lookup."""
        if not _BROWSER_CONTROL_VERBS.search(text):
            return False
        return bool(
            _SYNTHESIS_SIGNAL.search(text) or _VERIFICATION_SIGNAL.search(text)
        )

    @staticmethod
    def _looks_like_constrained_discovery(text: str) -> bool:
        """"Give me five hotels under $200" has no verb from
        _BROWSER_CONTROL_VERBS at all ("give me" isn't a search word) but
        is exactly as much a research-then-filter task as one that does --
        a quantity constraint paired with a price constraint or a
        synthesis signal is a strong enough standalone signal on its own.
        """
        if not _QUANTITY_CONSTRAINT.search(text):
            return False
        return bool(
            _PRICE_CONSTRAINT.search(text) or _SYNTHESIS_SIGNAL.search(text)
        )

    @staticmethod
    def _looks_like_followup_verification(text: str) -> bool:
        """"Which of these hotels is actually available Friday night?"
        has no leading action verb at all -- it's a bare follow-up
        question referring back to a prior turn's results, needing direct
        verification rather than a snippet-level answer."""
        return bool(
            _DEICTIC_BACKREF.search(text) and _VERIFICATION_SIGNAL.search(text)
        )

    @staticmethod
    def _looks_like_evaluative_recommendation(text: str) -> bool:
        """"Best restaurants to go in Seoul" has no action verb at all --
        an evaluative/superlative word paired with a following noun-phrase
        is itself the request shape, independent of _BROWSER_CONTROL_VERBS."""
        match = _EVALUATIVE_SIGNAL.search(text)
        if not match:
            return False
        window = text[match.end() : match.end() + 40]
        return bool(_RECOMMENDATION_NOUN_HINT.search(window))

    @staticmethod
    def _looks_like_price_bounded_shopping(text: str) -> bool:
        """"Cars to buy under 10k" states the item before the price
        limit -- a purchase-intent verb paired with a price constraint is
        its own trigger, since _QUANTITY_CONSTRAINT assumes the opposite
        word order and names no category itself."""
        return bool(
            _PURCHASE_INTENT_VERBS.search(text) and _PRICE_CONSTRAINT.search(text)
        )

    @staticmethod
    def _looks_cross_capability(text: str) -> bool:
        match = _CONJUNCTION.search(text)
        if not match:
            return False
        before, after = text[: match.start()], text[match.end() :]
        before_capabilities = {
            "ui_control" if _UI_CONTROL_VERBS.search(before) else None,
            "browser_control" if _BROWSER_CONTROL_VERBS.search(before) else None,
        } - {None}
        after_capabilities = {
            "ui_control" if _UI_CONTROL_VERBS.search(after) else None,
            "browser_control" if _BROWSER_CONTROL_VERBS.search(after) else None,
        } - {None}
        if not before_capabilities or not after_capabilities:
            return False
        return bool(before_capabilities - after_capabilities) or bool(
            after_capabilities - before_capabilities
        )

    def _classify(self, user_input: str) -> TaskIntentDecision:
        prompt = (
            "Decide whether this request is a real multi-step task for a "
            "goal-level planner, true for any of four reasons:\n"
            "(a) It genuinely needs more than one different capability in "
            "sequence -- for example opening a native app AND separately "
            "searching/reading a webpage, or controlling a browser AND "
            "separately controlling a different native app.\n"
            "(b) It needs research-then-decide work, even within one "
            "capability: search or gather information, then extract, "
            "compare, filter by a stated preference (a price limit, a "
            "quantity, a rating), or pick among results -- for example "
            "\"find hotels in Guam and shortlist them\", \"give me five "
            "hotels in Seoul under $200\", \"compare flight prices and "
            "tell me the cheapest\", or \"check their actual current "
            "prices\". These never leave a single capability (browsing), "
            "but still need the same observe-then-decide loop as a "
            "cross-capability task, not a one-shot lookup or a plain "
            "search-and-report.\n"
            "(c) It's a bare follow-up question asking to directly verify "
            "or confirm something about results already discussed -- for "
            "example \"which of these hotels is actually available Friday "
            "night?\" -- rather than trusting a remembered snippet.\n"
            "(d) It's an evaluative recommendation or shopping request "
            "naming a price limit, even with no search verb at all -- for "
            "example \"best restaurants to go in Seoul\" or \"cars to buy "
            "under 10k\" -- since deciding the right place to look (a "
            "generic search vs. a specific site) is itself planner-level "
            "work for these.\n"
            "None of these apply to one simple action described with an "
            "'and' in it that has nothing to compare, filter, or verify "
            "(e.g. 'open Spotify and play a song' is one capability, one "
            "action, not multistep), nor to a casual open-ended request "
            "with no stated quantity, price, or verification need (e.g. "
            "'give me some good hotels in Seoul' is a simple discovery "
            "request, not multistep). Return JSON only: is_multistep_task "
            "(bool), confidence (0-1), reason (short string).\n\n"
            f"Request: {user_input}"
        )
        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 100},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            payload = json.loads(str(self._value(message, "content", "")))
            is_multistep = bool(payload.get("is_multistep_task", False))
            confidence = max(0.0, min(float(payload.get("confidence", 0)), 1.0))
            reason = str(payload.get("reason", "")).strip()
            return TaskIntentDecision(
                is_multistep=is_multistep, confidence=confidence, reason=reason,
            )
        except Exception as error:
            print(
                "[Task Intent Gate] Classification failed safely: "
                f"{type(error).__name__}: {error}"
            )
            return TaskIntentDecision(is_multistep=False)

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
