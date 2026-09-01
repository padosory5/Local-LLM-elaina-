"""Which ability satisfies this need -- decided after the need is known.

The step that was missing. Tool choice used to happen in the router's first
token: the label ``web_search`` *was* the decision, and everything after it
was delivery. Nothing downstream could conclude that she already knew the
answer, or that a page needed opening instead, because there was no point at
which that question was open.

So the pipeline now reads:

    [Goal]        what the person wants        (deliberation/goal_intent.py)
    [Interaction] what that requires           (deliberation/interaction.py)
    [Capability]  which ability meets it       (this module)
    [Agent]       who owns that ability        (agents/coordinator.py)

Capability ids are the ones in :mod:`brain.capabilities` -- the registry that
already answers "what can I do right now". This module does not keep a second
list; it chooses from that one, plus two answers that need no ability at all:
``direct_answer``, whether she knows it or already found it.

Deterministic and free. It reads a goal and a decision that already exist and
makes no model call, so choosing a capability costs nothing and can be
logged, tested, and disagreed with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from brain.deliberation import goal_intent
from brain.deliberation.interaction import (
    NEED_FRESH,
    NEED_MACHINE,
    NEED_RECALLED,
    NEED_VERIFIED,
)

# Not abilities: answers that need nothing run. Named here so the log says
# what happened rather than going blank when no tool is chosen.
DIRECT_ANSWER = "direct_answer"
NOTHING = "none"

# Registry ids (brain/capabilities.py), used verbatim so the two cannot drift.
WEB_SEARCH = "web_search"
BROWSER_CONTROL = "browser_control"
UI_CONTROL = "ui_control"
SCREEN_ANALYSIS = "screen_analysis"
PROJECT_QUESTION = "project_question"
CALENDAR_ACTION = "calendar_action"
TASK_PLANNING = "task_planning"

# Surfaces the registry does not model yet. They are real and distinct -- a
# commit is not a project read, and installing an agent is not planning a
# task -- and naming them here keeps the [Capability] log honest instead of
# filing a git push under "project access". Adding them to
# brain/capabilities.py is the tidier end state; a test below asserts every
# other id really is a registry one, so this list cannot quietly grow.
PROJECT_EDIT = "project_edit"
GIT = "git"
AGENT_BUILDING = "agent_building"

UNREGISTERED = frozenset({PROJECT_EDIT, GIT, AGENT_BUILDING})

# Every capability this layer can choose. Named explicitly so the drift test
# checks these and not, say, the need constants imported above.
ALL_CAPABILITIES = (
    DIRECT_ANSWER, NOTHING,
    WEB_SEARCH, BROWSER_CONTROL, UI_CONTROL, SCREEN_ANALYSIS,
    PROJECT_QUESTION, CALENDAR_ACTION, TASK_PLANNING,
    PROJECT_EDIT, GIT, AGENT_BUILDING,
)

# Which ability serves a request that needs something *done*. Keyed on the
# router's label only here, at the very end -- by this point the goal and the
# need are already settled, so the label is being used for what it is good
# at: naming the concrete surface, not the intent.
_MACHINE_CAPABILITY = {
    "computer_action": UI_CONTROL,
    "task_action": TASK_PLANNING,
    "browser_action": BROWSER_CONTROL,
    "browser_tab": BROWSER_CONTROL,
    "browser_search": BROWSER_CONTROL,
    "media_action": UI_CONTROL,
    "screen_analysis": SCREEN_ANALYSIS,
    "project_question": PROJECT_QUESTION,
    "project_edit": PROJECT_EDIT,
    "git_commit": GIT,
    "git_publish": GIT,
    "calendar_action": CALENDAR_ACTION,
    "agent_create": AGENT_BUILDING,
    "pending_approval": TASK_PLANNING,
}

# Which capabilities a specialist agent carries out, as opposed to the ones
# Elaina drives herself through a planner or handler.
#
# *Which* agent stays where it already is: the ``intents:`` list in each
# agents/definitions/*.yaml, read through AgentRegistry.for_intent. Copying
# that mapping here would be a second answer to a question that already has
# one, and the two would drift. What moves into this layer is only the
# decision to dispatch at all -- which used to be a bare membership test on
# the router's label.
AGENT_DISPATCHED = frozenset({
    WEB_SEARCH,
    PROJECT_QUESTION,
    PROJECT_EDIT,
    GIT,
    SCREEN_ANALYSIS,
    CALENDAR_ACTION,
    AGENT_BUILDING,
})


# The label that names each agent-carried capability in the definition
# YAMLs, and every label that legitimately maps to it. Used only to look an
# agent up consistently -- the ownership itself still lives in the YAML.
_CANONICAL_LABEL = {
    WEB_SEARCH: "web_search",
    PROJECT_QUESTION: "project_question",
    PROJECT_EDIT: "project_edit",
    GIT: "git_commit",
    SCREEN_ANALYSIS: "screen_analysis",
    CALENDAR_ACTION: "calendar_action",
    AGENT_BUILDING: "agent_create",
}
_LABELS_FOR = {
    WEB_SEARCH: ("web_search", "fact_check", "entity_correction"),
    PROJECT_QUESTION: ("project_question",),
    PROJECT_EDIT: ("project_edit",),
    GIT: ("git_commit", "git_publish"),
    SCREEN_ANALYSIS: ("screen_analysis",),
    CALENDAR_ACTION: ("calendar_action",),
    AGENT_BUILDING: ("agent_create",),
}


# ---------------------------------------------------------------- factors
#
# What the request needs, and what each ability costs to meet it. Phase 4E.2
# chose on ``need`` alone, which was enough to stop the router's label
# picking the tool but not enough to tell two abilities apart once both
# could serve: "does Hotel X have a room on the 18th" and "what are some
# good hotels in Seoul" have the same need and want different tools.

@dataclass(frozen=True)
class Factors:
    """What this request requires, weighed against what each tool costs."""

    freshness_required: bool = False
    live_state_required: bool = False
    verification_required: bool = False
    interaction_required: bool = False
    existing_context_available: bool = False
    structured_data_available: bool = False
    permission_level: int = 1
    preferred_provider_or_source: str = ""
    preference_kind: str = ""

    def log_lines(self) -> tuple[str, ...]:
        return tuple(
            f"  {name}: {str(value).lower()}"
            for name, value in (
                ("freshness_required", self.freshness_required),
                ("live_state_required", self.live_state_required),
                ("verification_required", self.verification_required),
                ("interaction_required", self.interaction_required),
                ("existing_context", self.existing_context_available),
                ("permission_level", self.permission_level),
            )
        ) + tuple(
            line for line in (
                f"  preference_kind: {self.preference_kind}"
                if self.preference_kind else "",
                f"  preferred_provider_or_source: {self.preferred_provider_or_source}"
                if self.preferred_provider_or_source else "",
            ) if line
        )


@dataclass(frozen=True)
class Candidate:
    """One ability considered, and how well it fits."""

    capability: str
    score: float
    reason: str = ""


# What each ability costs to use, on the same scale so they can be compared.
# Latency is time-to-answer; disruption is what the user has to watch happen.
# These are the escalation ladder made explicit: the cheapest reliable tool
# wins unless something the request needs is only available further up.
_COST = {
    DIRECT_ANSWER: 0.00,
    WEB_SEARCH: 0.15,
    PROJECT_QUESTION: 0.20,
    SCREEN_ANALYSIS: 0.30,
    CALENDAR_ACTION: 0.30,
    TASK_PLANNING: 0.45,
    BROWSER_CONTROL: 0.55,
    PROJECT_EDIT: 0.55,
    GIT: 0.55,
    AGENT_BUILDING: 0.70,
    UI_CONTROL: 0.75,
}

# How much each ability can be trusted for a given requirement. A search
# snippet is good evidence for "what exists" and poor evidence for "is this
# specific room free tonight"; the browser is the reverse, at a cost.
_RELIABILITY = {
    (WEB_SEARCH, "fresh"): 0.90,
    (WEB_SEARCH, "live"): 0.35,
    # Verification means "check it against a real source", and a search that
    # cites one does that. Only *live state* justifies the browser -- scored
    # at 0.60 this sent "what are some good hotels in Seoul" to browser
    # control, the exact case the brief says browser control is unnecessary
    # for. Measured live before the weight was corrected.
    (WEB_SEARCH, "verify"): 0.85,
    (BROWSER_CONTROL, "fresh"): 0.75,
    (BROWSER_CONTROL, "live"): 0.95,
    (BROWSER_CONTROL, "verify"): 0.90,
    (DIRECT_ANSWER, "fresh"): 0.05,
    (DIRECT_ANSWER, "live"): 0.00,
    (DIRECT_ANSWER, "verify"): 0.05,
}

# Below this a candidate is not worth offering as a fallback either.
_VIABLE = 0.20

# Failing this often in a session means stop choosing it. The task planner
# bounds retries *inside* one task; this bounds them across turns, which is
# where a capability that is simply unavailable kept being chosen again.
_MAX_FAILURES = 2


# Live state: something whose answer can change between now and the next
# minute, for one specific thing. Deliberately *not*
# TaskDiscoveryPolicy.dates_would_change_the_answer -- that answers "should I
# ask for dates before researching", which is a different question and much
# broader. Reusing it read "nvidia price now" as live state and would have
# opened a browser for a stock quote a search answers perfectly well.
_AVAILABILITY = re.compile(
    r"\b(?:availab(?:le|ility)|vacan(?:t|cy|cies)|in stock|out of stock|"
    r"sold out|book(?:ing)?|reserve|reservation|check[- ]?in|check[- ]?out)"
    r"\b"
    r"|\brooms?\s+(?:for|on|available)"
    r"|예약|숙박|재고",
    re.IGNORECASE,
)

# A moment, not merely "current". "Now" is what a search is for; "on the
# 18th" or "tonight" is what a page is for.
_TIME_SPECIFIC = re.compile(
    r"\b(?:tonight|today|tomorrow|this (?:evening|weekend|afternoon))\b"
    r"|\bon \w+day\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}"
    r"|\b\d{1,2}\s*(?:st|nd|rd|th)\b"
    r"|\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}\s*(?:박|일)\b",
    re.IGNORECASE,
)

_PRICE = re.compile(
    r"\b(?:price|prices|rate|rates|cost|costs|fare|fares)\b|가격|요금",
    re.IGNORECASE,
)


def _is_live_state(request: str) -> bool:
    """Whether the answer turns on the state of one thing, right now."""
    text = str(request or "")
    if _AVAILABILITY.search(text):
        return True
    # A price on a named day is a live quote for that day; a price "now" is
    # a current fact, and search reports those well.
    return bool(_PRICE.search(text) and _TIME_SPECIFIC.search(text))


def read_factors(
    goal: goal_intent.SemanticGoal,
    decision: Any,
    *,
    route: Any = None,
    execution_preference: Any = None,
) -> Factors:
    """Read the requirement from what earlier layers already established.

    No model call and no keyword table of its own: freshness and
    verification come from the router, live state from the same
    date-sensitivity test the discovery preflight uses, and interaction from
    the same surface test the goal layer uses.
    """
    need = str(getattr(decision, "need", ""))
    request = str(
        getattr(route, "normalized_request", "") or goal.subject or ""
    )

    live = bool(
        need in {NEED_VERIFIED, NEED_FRESH} and _is_live_state(request)
    )

    return Factors(
        freshness_required=need in {NEED_FRESH, NEED_VERIFIED},
        live_state_required=live,
        verification_required=need == NEED_VERIFIED,
        interaction_required=goal_intent.names_a_surface(request),
        existing_context_available=need == NEED_RECALLED,
        structured_data_available=bool(
            getattr(decision, "has_usable_context", False)
        ),
        permission_level=int(getattr(decision, "permission_level", 1) or 1),
        preferred_provider_or_source=str(
            getattr(execution_preference, "choice", "") or ""
        ),
        preference_kind=str(
            getattr(execution_preference, "kind", "") or ""
        ),
    )


def _score(capability: str, factors: Factors, failures: int) -> float:
    """How well this ability meets the requirement, net of what it costs."""
    if factors.existing_context_available:
        # Nothing outruns an answer already in hand.
        return 1.0 if capability == DIRECT_ANSWER else 0.05

    fit = 0.0
    if factors.live_state_required:
        fit = _RELIABILITY.get((capability, "live"), 0.0)
    elif factors.verification_required:
        fit = _RELIABILITY.get((capability, "verify"), 0.0)
    elif factors.freshness_required:
        fit = _RELIABILITY.get((capability, "fresh"), 0.0)
    else:
        fit = 1.0 if capability == DIRECT_ANSWER else 0.10

    if factors.interaction_required and capability == BROWSER_CONTROL:
        # The request named a page to operate; nothing else can do that.
        fit = max(fit, 0.95)

    # Cost is what stops the most capable tool always winning.
    score = fit - _COST.get(capability, 0.5) * 0.35

    # Something that has already failed this session is a worse bet than its
    # profile suggests -- the point of not retrying it forever.
    score -= min(failures, _MAX_FAILURES) * 0.5
    return round(max(score, 0.0), 2)


@dataclass(frozen=True)
class CapabilityChoice:
    """The ability chosen to meet the need, and why that one."""

    capability: str = DIRECT_ANSWER
    reason: str = ""
    factors: Factors = field(default_factory=Factors)
    # Everything considered, best first -- so the log can show the working
    # and a failure can fall back without deciding again from scratch.
    candidates: tuple[Candidate, ...] = ()
    execution_preference: Any = None

    @property
    def fallbacks(self) -> tuple[str, ...]:
        """What to try next if this fails, best first, never itself."""
        return tuple(
            candidate.capability
            for candidate in self.candidates
            if candidate.capability != self.capability
            and candidate.score >= _VIABLE
        )

    @property
    def needs_a_tool(self) -> bool:
        return self.capability not in {
            DIRECT_ANSWER, NOTHING,
        }

    @property
    def needs_agent(self) -> bool:
        """Whether a specialist agent carries this out, rather than Elaina."""
        return self.capability in AGENT_DISPATCHED

    def dispatch_label(self, router_label: str = "") -> str:
        """The label to look the owning agent up by.

        Agent ownership stays declarative, in each definition's ``intents:``
        list -- but it has to be looked up by a label that agrees with the
        chosen capability. Measured live: a turn the router called
        "conversation" selected web_search, and dispatching on the router's
        own label handed it to the Conversation Agent, which then searched
        the raw utterance. When the two disagree, the capability wins,
        because it is the later and better-informed decision.
        """
        canonical = _CANONICAL_LABEL.get(self.capability, "")
        if not canonical:
            return router_label
        if _CANONICAL_LABEL.get(
            _MACHINE_CAPABILITY.get(router_label, ""), ""
        ) == canonical:
            # They already agree; keep the router's more specific label.
            return router_label
        if router_label in _LABELS_FOR.get(self.capability, ()):
            return router_label
        return canonical

    def log_block(self) -> str:
        """The debugging view. Console only -- never the conversation UI.

        Shows the working, not just the conclusion: which abilities were
        considered and how they scored is what makes a wrong choice
        diagnosable instead of merely wrong.
        """
        lines = ["[Capability]"]
        lines.extend(self.factors.log_lines())
        if self.candidates:
            lines.append("  Candidates:")
            lines.extend(
                f"    {candidate.capability}: {candidate.score:.2f}"
                for candidate in self.candidates
            )
        lines.append(f"  Selected: {self.capability}")
        if self.fallbacks:
            lines.append(f"  Fallback: {', '.join(self.fallbacks)}")
        lines.append(f"  Why: {self.reason or '(none)'}")
        return "\n".join(lines)


def select(
    goal: goal_intent.SemanticGoal,
    decision: Any,
    *,
    route: Any = None,
    failures: Any = None,
    execution_preference: Any = None,
) -> CapabilityChoice:
    """Choose the ability that meets this need, now that the need is known.

    Cheapest first, and only as far up the ladder as the requirement forces:

        what she already knows
            -> what this session already found
                -> a lookup
                    -> a live page
                        -> operating the machine

    ``failures`` is an optional mapping of capability -> how many times it
    has failed this session. A capability that keeps failing scores worse,
    so the next-best is chosen instead of the same one forever. Bounding
    retries *inside* one task is TaskPlanner's job and stays there; this is
    the across-turns case it cannot see.
    """
    need = str(getattr(decision, "need", ""))
    mode = str(getattr(decision, "mode", ""))
    label = str(getattr(route, "intent", "") or "")
    failed = dict(failures or {})

    if goal.intent == goal_intent.CLARIFY or mode == "clarify":
        return CapabilityChoice(
            NOTHING,
            "a question is outstanding; nothing runs until it is answered",
        )

    factors = read_factors(
        goal, decision, route=route,
        execution_preference=execution_preference,
    )

    # Something that has to be *done* is not a choice between information
    # sources. The surface is whatever the request names, and the ladder
    # below has nothing cheaper to offer.
    if need == NEED_MACHINE:
        capability = _MACHINE_CAPABILITY.get(label, TASK_PLANNING)
        return CapabilityChoice(
            capability,
            f"the request needs something done, and {capability} is the "
            "surface that does it",
            factors=factors,
            candidates=(Candidate(capability, 1.0, "the named surface"),),
            execution_preference=execution_preference,
        )

    # Everything else is an information question, and more than one ability
    # can answer it. Score them and take the best.
    considered = [DIRECT_ANSWER, WEB_SEARCH, BROWSER_CONTROL]
    candidates = tuple(sorted(
        (
            Candidate(
                capability,
                _score(capability, factors, failed.get(capability, 0)),
                _fit_reason(capability, factors),
            )
            for capability in considered
        ),
        key=lambda candidate: candidate.score,
        reverse=True,
    ))
    best = candidates[0]

    return CapabilityChoice(
        best.capability,
        _selection_reason(best.capability, factors, goal),
        factors=factors,
        candidates=candidates,
        execution_preference=execution_preference,
    )


def _fit_reason(capability: str, factors: Factors) -> str:
    if factors.existing_context_available:
        return "already found" if capability == DIRECT_ANSWER else "redundant"
    if factors.live_state_required:
        return {
            BROWSER_CONTROL: "reads the real page",
            WEB_SEARCH: "snippets go stale",
            DIRECT_ANSWER: "cannot know live state",
        }.get(capability, "")
    if factors.freshness_required:
        return {
            WEB_SEARCH: "current and cheap",
            BROWSER_CONTROL: "current but costly",
            DIRECT_ANSWER: "would be guessing",
        }.get(capability, "")
    return "knows this" if capability == DIRECT_ANSWER else "unnecessary"


def _selection_reason(
    capability: str, factors: Factors, goal: goal_intent.SemanticGoal,
) -> str:
    subject = goal.subject or "this"
    if factors.existing_context_available:
        return (
            "already found earlier in this conversation; searching again "
            "would repeat the work and could return a different set"
        )
    if capability == BROWSER_CONTROL:
        if factors.interaction_required:
            return "the request names a page to operate"
        if factors.live_state_required:
            return (
                f"{subject} turns on live state a search snippet cannot "
                "honestly report"
            )
        return f"reading the page directly is the reliable way to answer {subject}"
    if capability == WEB_SEARCH:
        if factors.verification_required:
            return "the answer has to hold up against a real source"
        return f"current public information about {subject} is required"
    return "she can answer this from what she already knows"


def note_failure(failures: dict, capability: str) -> dict:
    """Record that an ability did not work, so it is not the next choice too.

    Returns the mapping for convenience. Kept as a plain dict rather than a
    class because the only state involved is a count per capability, and the
    engine already owns a session to hang it on.
    """
    key = str(capability or "").strip()
    if key:
        failures[key] = failures.get(key, 0) + 1
    return failures


def note_success(failures: dict, capability: str) -> dict:
    """An ability that worked has earned its record back."""
    failures.pop(str(capability or "").strip(), None)
    return failures


def exhausted(failures: Any, capability: str) -> bool:
    """Whether this ability has failed too often to be worth choosing."""
    return dict(failures or {}).get(str(capability), 0) >= _MAX_FAILURES
