"""What to do about a request, decided once instead of thirty-four times.

Every signal this needs already existed. What did not exist was anywhere to
put the conclusion: ``route.intent`` was consulted in thirty-four separate
places across a five-thousand-line ``chat_engine``, and each one re-derived
whether the turn was an answer, a search, or an action. A decision spread
that thinly cannot be tested, cannot be logged, and can disagree with itself
between two branches of the same turn.

So this is a consolidation, not a new layer on top of the old one. It reads
what :class:`~brain.intent_router.IntentDecision` and the deliberation layer
already produced and states the conclusion once, in a shape the rest of the
turn can consult:

    need    what the request actually requires before it can be answered
    mode    what Elaina should therefore do about it

Two properties matter as much as the answer itself.

**It costs nothing.** ``decide`` is a pure function over values that already
exist by the time it runs -- the router call has already happened, the front
door and interpreter are deterministic, and session evidence is already
tracked. It makes no model call, so consulting it is free and it can be
called as often as a turn likes.

**It fails safe.** An intent this module has never heard of does not get
invented into an action: it falls through to ``answer``, which touches
nothing. Anything genuinely unresolved becomes ``clarify``, which asks.

Later phases read fields this one only computes: 4E.3 owns ``recommend`` and
``permission_level``, 4E.4 owns tool choice, 4E.5 owns ``result_surface``.
They are populated here so those phases consolidate rather than re-derive --
which is the mistake this module exists to undo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from brain.deliberation import goal_intent


# ------------------------------------------------------------------- needs
#
# What the request requires before it can be answered honestly. This is the
# question tool choice should turn on -- not the intent label, which says
# what kind of request it was rather than what satisfying it would take.

NEED_NONE = "none"                        # she already knows enough
NEED_RECALLED = "recalled_context"        # this session already found it
NEED_FRESH = "fresh_information"          # needs a current external lookup
NEED_VERIFIED = "live_verification"       # needs a checked, live source
NEED_MACHINE = "machine_action"           # needs something done on the machine

NEEDS = (NEED_NONE, NEED_RECALLED, NEED_FRESH, NEED_VERIFIED, NEED_MACHINE)


# ------------------------------------------------------------------- modes

ANSWER = "answer"                  # reply now, touch nothing
EXECUTE = "execute"                # do it, no further permission needed
RECOMMEND = "recommend"            # offer it; the user has not asked yet
ASK_PERMISSION = "ask_permission"  # needs a yes before it happens
CLARIFY = "clarify"                # needs a question answered first

MODES = (ANSWER, EXECUTE, RECOMMEND, ASK_PERMISSION, CLARIFY)


# ------------------------------------------------------- permission levels
#
# The three-level friction policy. 4E.2 only computes these; 4E.3 acts on
# them. Level 3 deliberately does not re-implement the existing approval
# gates -- security/ still owns the actual wall, and this only says which
# requests are expected to hit one.

INFORMATIONAL = 1   # looking something up; no visible side effect
VISIBLE = 2         # the user will see it happen (a tab, a window)
CONSEQUENTIAL = 3   # changes something: files, commits, messages, accounts

_LEVEL_BY_INTENT = {
    # 1 -- informational
    "conversation": INFORMATIONAL,
    "knowledge_question": INFORMATIONAL,
    "time_question": INFORMATIONAL,
    "calculation": INFORMATIONAL,
    "web_search": INFORMATIONAL,
    "fact_check": INFORMATIONAL,
    "entity_correction": INFORMATIONAL,
    "project_question": INFORMATIONAL,
    "memory_context": INFORMATIONAL,
    "agent_offer": INFORMATIONAL,
    "clarification": INFORMATIONAL,
    # 2 -- the user watches it happen
    "screen_analysis": VISIBLE,
    "browser_action": VISIBLE,
    "browser_tab": VISIBLE,
    "browser_search": VISIBLE,
    "computer_action": VISIBLE,
    "task_action": VISIBLE,
    "media_action": VISIBLE,
    # 3 -- it changes something
    "project_edit": CONSEQUENTIAL,
    "git_commit": CONSEQUENTIAL,
    "git_publish": CONSEQUENTIAL,
    "calendar_action": CONSEQUENTIAL,
    "agent_create": CONSEQUENTIAL,
    "pending_approval": CONSEQUENTIAL,
}

# Operations that change or destroy something, whatever intent carried them.
# The intent alone is too coarse here: "computer_action" covers both opening
# Spotify and deleting a folder.
_CONSEQUENTIAL_OPERATIONS = frozenset({
    "delete_file",
    "delete_folder",
    "force_quit_app",
    "create_file",
    "create_folder",
})

# Goals that cannot be met without doing something outside the conversation,
# and goals that cannot be met from memory. Both are stated in terms of what
# the person wants; the router-label sets below them are the fallback for a
# caller that passes a bare route with no goal.
_MACHINE_GOALS = frozenset({
    goal_intent.ACT,
    goal_intent.CREATE,
    goal_intent.MODIFY,
    goal_intent.INSPECT,
})
#
# RECOMMEND and COMPARE are deliberately absent. Wanting options, or wanting
# them weighed, says nothing about whether the answer has to be looked up:
# "Should I use Live2D or a 3D model?" is a recommendation the router marks
# as needing no external evidence at all. Including them here overrode that
# judgement and sent every advice turn to a search -- measured live, it
# answered "which one would you choose?" about hotels with a car.
_FRESH_GOALS = frozenset({
    goal_intent.RETRIEVE,
})

# Intents whose whole purpose is to reach outside for current information.
_RESEARCH_INTENTS = frozenset({"web_search", "fact_check", "entity_correction"})

# Intents that cannot be satisfied from conversation alone -- something has
# to run. Deliberately a separate axis from permission level: reading project
# files needs a tool but almost no friction, while deleting a folder needs
# little machinery and a great deal of friction. Deriving one from the other
# got both wrong (it would have stopped dispatching the Coding Agent for a
# project question, and made a requested file edit ask twice).
# Router labels that always mean something outside the conversation runs.
# "task_action" is deliberately absent: it means the planner needs several
# steps, which says nothing about whether a machine is involved. Including it
# made "find me some good hotels in Seoul" a machine action, which then
# demanded check-in dates for a question about famous hotels.
_MACHINE_INTENTS = frozenset({
    "computer_action",
    "browser_action",
    "browser_tab",
    "browser_search",
    "media_action",
    "screen_analysis",
    "project_question",
    "project_edit",
    "git_commit",
    "git_publish",
    "agent_create",
    "calendar_action",
    "pending_approval",
})

# Intents that answer from what she already knows.
_DIRECT_INTENTS = frozenset({
    "conversation",
    "knowledge_question",
    "time_question",
    "calculation",
    "memory_context",
    "agent_offer",
})

# Freshness values that mean "training knowledge is not good enough".
_STALE_FRESHNESS = frozenset({"live", "current", "recent"})


@dataclass(frozen=True)
class InteractionDecision:
    """What this turn is, and what should happen because of it."""

    mode: str = ANSWER
    need: str = NEED_NONE
    intent: str = ""
    topic: str = ""
    can_answer_directly: bool = True
    action_would_help: bool = False
    has_usable_context: bool = False
    permission_level: int = INFORMATIONAL
    confidence: float = 1.0
    result_surface: str = "none"
    reason: str = ""

    # -- what callers actually ask ------------------------------------

    @property
    def acts(self) -> bool:
        """Whether anything outside the conversation is about to happen."""
        return self.mode == EXECUTE

    @property
    def asks(self) -> bool:
        return self.mode in {ASK_PERMISSION, CLARIFY}

    @property
    def needs_external_information(self) -> bool:
        return self.need in {NEED_FRESH, NEED_VERIFIED}

    @property
    def reuses_existing_results(self) -> bool:
        """The answer is already in this session; running a tool would repeat it."""
        return self.need == NEED_RECALLED

    def log_block(self) -> str:
        """The debugging view. Console only -- never the conversation UI."""
        return (
            "[Interaction]\n"
            f"  Need: {self.need}\n"
            f"  Decision: {self.mode}\n"
            f"  Permission: level {self.permission_level}\n"
            f"  Confidence: {self.confidence:.2f}\n"
            f"  Why: {self.reason or '(none)'}"
        )


# ------------------------------------------------------------------ decide

def _value(route: Any, name: str, default: Any) -> Any:
    """Read a routing field without depending on which class produced it.

    Keeps this module usable from a test with a stub, and unaffected by a
    field being added to IntentDecision later.
    """
    value = getattr(route, name, default)
    return default if value is None else value


def permission_level_for(intent: str, operation: str = "") -> int:
    """How much friction this request should meet before it happens."""
    if str(operation or "").strip() in _CONSEQUENTIAL_OPERATIONS:
        return CONSEQUENTIAL
    return _LEVEL_BY_INTENT.get(str(intent or "").strip(), VISIBLE)


def _need_for(
    route: Any, *, has_usable_context: bool, goal: Any = None,
) -> str:
    """What this request requires, read from the goal rather than the label.

    This used to switch on the router's tool-shaped labels, which meant the
    need was a restatement of a tool choice already made. It reads the
    semantic goal now: wanting something *done* needs a machine whatever
    label carried it, and wanting a current fact needs a lookup whether the
    classifier called it web_search or something else entirely.
    """
    intent = str(_value(route, "intent", ""))
    wants = str(getattr(goal, "intent", "")) if goal is not None else ""

    if wants == goal_intent.CLARIFY or intent == "clarification":
        return NEED_NONE

    # Two sufficient causes, not alternatives. Wanting something *done* needs
    # a machine; so does a request the planner has to carry out in steps,
    # whatever the person's goal was. "Compare some hotels" is a compare goal
    # served by the task planner, and it needs both to be true at once.
    if wants in _MACHINE_GOALS or intent in _MACHINE_INTENTS:
        return NEED_MACHINE

    # Reuse beats retrieval. "Which one would you choose?" after a search is
    # answerable from what that search already returned, and searching again
    # is both slower and liable to return a different set to choose from.
    if has_usable_context and _value(route, "is_follow_up", False):
        return NEED_RECALLED

    if wants == goal_intent.VERIFY or _value(route, "verification_required", False):
        return NEED_VERIFIED
    if _value(route, "requires_external_evidence", False):
        return NEED_FRESH
    if str(_value(route, "information_freshness", "")) in _STALE_FRESHNESS:
        return NEED_FRESH
    if wants:
        if wants in _FRESH_GOALS:
            return NEED_FRESH
    elif intent in _RESEARCH_INTENTS:
        return NEED_FRESH

    return NEED_NONE


def _worth_offering(route: Any) -> bool:
    """Whether raising a capability unprompted would help rather than nag."""
    try:
        from brain.recommendation import worth_offering

        return worth_offering(
            str(_value(route, "normalized_request", ""))
        )
    except Exception:
        return False


def decide(
    route: Any,
    *,
    goal: Any = None,
    has_usable_context: bool = False,
    explicitly_requested: bool | None = None,
) -> InteractionDecision:
    """Read the routing signals once and say what should happen.

    ``has_usable_context`` is whether this session already holds results that
    answer the request -- from ``TaskSessionStore.context_for_followup``.
    ``explicitly_requested`` overrides the router's own reading of whether
    the user asked for the action outright; left unset, ``action_requested``
    decides.
    """
    intent = str(_value(route, "intent", ""))
    confidence = float(_value(route, "confidence", 1.0) or 1.0)
    requested = (
        bool(_value(route, "action_requested", False))
        if explicitly_requested is None
        else bool(explicitly_requested)
    )
    operation = str(_value(route, "computer_operation", ""))
    level = permission_level_for(intent, operation)
    need = _need_for(
        route, has_usable_context=has_usable_context, goal=goal,
    )

    def built(mode: str, reason: str, **extra: Any) -> InteractionDecision:
        return InteractionDecision(
            mode=mode,
            need=need,
            intent=(
                str(getattr(goal, "intent", "")) if goal is not None
                else intent
            ),
            topic=str(
                getattr(goal, "subject", "")
                or _value(route, "topic", "")
                or _value(route, "normalized_request", "")
            ),
            can_answer_directly=need in {NEED_NONE, NEED_RECALLED},
            action_would_help=need != NEED_NONE,
            has_usable_context=has_usable_context,
            permission_level=level,
            confidence=confidence,
            reason=reason,
            **extra,
        )

    # A question that has already been asked outranks everything: acting on a
    # request she has admitted she cannot read yet is how a wrong action
    # happens.
    if intent == "clarification" or (
        goal is not None and getattr(goal, "intent", "") == goal_intent.CLARIFY
    ):
        return built(CLARIFY, "the request cannot proceed until this is answered")

    if need == NEED_RECALLED:
        return built(
            ANSWER,
            "this session already found it; searching again would repeat work",
        )

    if need == NEED_MACHINE:
        if not requested:
            # She noticed something she could do that nobody asked for. 4E.3
            # decides how to phrase the offer; the point here is that it is
            # not an instruction.
            return built(
                RECOMMEND,
                "an action would help, but the user has not asked for one",
            )
        # Only a directly destructive operation stops here. An agent intent
        # such as project_edit is level 3 too, but its own approval wall is
        # what asks -- dispatching it *is* how the user gets asked, so
        # refusing to dispatch would ask before asking, and a request the
        # user already made outright would be questioned twice.
        if operation in _CONSEQUENTIAL_OPERATIONS:
            return built(
                ASK_PERMISSION,
                f"{operation} changes something, so it needs a separate yes",
            )
        return built(EXECUTE, "the user asked for this outright")

    if need in {NEED_FRESH, NEED_VERIFIED}:
        # Level 1 by definition: looking something up has no visible side
        # effect, so asking "shall I search?" is friction with no purpose.
        # This is the "what's the weather tomorrow" case from the brief.
        subject = str(getattr(goal, "subject", "")) if goal is not None else ""
        about = f" about {subject}" if subject else ""
        return built(
            EXECUTE,
            f"current information{about} is needed, and looking it up "
            "costs nothing",
        )

    if intent in _DIRECT_INTENTS or intent == "":
        # She can answer -- but sometimes a real source would add something
        # the answer cannot, and the person has only mused rather than asked.
        # That is an offer, not a search: "I'm thinking about getting a new
        # monitor" deserves "I can pull up a few current options", not a
        # silent lookup and not silence.
        if _worth_offering(route):
            return built(
                RECOMMEND,
                "she can answer, and a real source would add something the "
                "answer cannot",
            )
        return built(ANSWER, "she can answer this from what she already knows")

    # Fails safe. An intent this module has never seen does not become an
    # action: it answers, which touches nothing, and the legacy consumer for
    # that intent still runs as it did before.
    return built(
        ANSWER,
        f"no interaction rule for {intent!r}; answering rather than acting",
    )
