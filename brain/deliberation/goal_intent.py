"""What the person wants, said without naming a tool.

The router's labels are tool names wearing an intent's clothes: ``web_search``
is not something a person wants, it is something Elaina might *do*. While that
label was the intent, the architecture read:

    user -> intent=web_search -> Research Agent -> web_search

which decides the tool in the first step and then spends three more arriving
at it. Nothing in that chain can ever conclude "she already knows this" or
"the browser would be better", because the answer was fixed before the
question was asked.

This module puts a goal where the tool name was:

    user -> goal=compare hotels -> need=live_verification -> capability -> agent

The router still classifies -- it is good at that, and replacing its
vocabulary would mean retraining it, rewriting 113 matrix cases and 91KB of
router tests for no behavioural gain. What changes is that its label is
translated here and never consulted again downstream. From this point on,
nothing in the pipeline knows or cares that a classifier once said
``web_search``.

The translation is deterministic and free. It reads fields the router already
returns; there is no second model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# What a person can want. Every one of these is a thing the user is after,
# not a thing Elaina runs -- which is the whole distinction this module
# exists to draw.
CHAT = "chat"            # talking, not asking for anything
EXPLAIN = "explain"      # wants to understand something
RETRIEVE = "retrieve"    # wants a current fact
VERIFY = "verify"        # wants something confirmed against a real source
RECOMMEND = "recommend"  # wants options, or a choice made
COMPARE = "compare"      # wants options weighed against each other
ACT = "act"              # wants something done
CREATE = "create"        # wants something made
MODIFY = "modify"        # wants something changed
INSPECT = "inspect"      # wants something examined
CLARIFY = "clarify"      # cannot be served until a question is answered

INTENTS = (
    CHAT, EXPLAIN, RETRIEVE, VERIFY, RECOMMEND,
    COMPARE, ACT, CREATE, MODIFY, INSPECT, CLARIFY,
)


# Weighing named things against each other, rather than asking for a list.
_COMPARISON = re.compile(
    r"\bcompare\b|\bversus\b|\bvs\.?\b|\bdifference between\b|"
    r"\bwhich (?:one |of these |of those )?is (?:better|cheaper|best)\b|"
    r"\bbetter than\b|\bpros and cons\b|"
    r"비교|어느\s*(?:게|것이)\s*(?:더|나은)",
    re.IGNORECASE,
)

# Asking her to *operate* something: a site to open, an app to use, a page to
# go to. Deliberately narrow. "Find me some good hotels in Seoul" is an
# information request wearing an imperative, and treating "find me" as a
# machine instruction sent a plain question to the task planner and then
# demanded check-in dates for it.
# Asking for something to be *operated*, not merely mentioning where it
# lives. Every alternative here is a verb or a deictic that points at a
# surface to act on.
#
# A bare host name used to be one of these, and a mention is not an
# instruction: "what do reviews on booking.com say about the Peninsula?" is
# a research question, and naming the site it is answered from was enough to
# mark it as a page to drive. That is precisely the rule this must not
# break -- browser control is not warranted just because a request mentions
# a website. A domain accompanied by an actual verb ("open booking.com and
# check the price") still matches, on the verb.
_NAMES_A_SURFACE = re.compile(
    r"\b(?:open|launch|go\s+to|navigate\s+to|visit|browse|pull\s+up)\b"
    r"|\buse\s+\S+\s+to\b"
    r"|\bon\s+(?:the\s+)?(?:site|website|page|app)\b"
    r"|\b(?:click|scroll|fill\s+in|log\s+in|sign\s+in|book\s+it)\b"
    r"|열어|들어가|사이트에서|앱에서",
    re.IGNORECASE,
)


def names_a_surface(request: str) -> bool:
    """Whether the request asks for something to be operated, not just found."""
    return bool(_NAMES_A_SURFACE.search(str(request or "")))


# Router labels grouped by what the person actually wanted. The labels stay
# tool-shaped because the router still emits them; the grouping is where they
# stop being tools.
_BY_ROUTER_LABEL = {
    "conversation": CHAT,
    "agent_offer": CHAT,
    "memory_context": CHAT,
    "knowledge_question": EXPLAIN,
    "time_question": EXPLAIN,
    "calculation": EXPLAIN,
    "web_search": RETRIEVE,
    "entity_correction": RETRIEVE,
    "fact_check": VERIFY,
    "computer_action": ACT,
    "browser_action": ACT,
    "browser_tab": ACT,
    "browser_search": ACT,
    "media_action": ACT,
    "agent_create": CREATE,
    "calendar_action": CREATE,
    "project_edit": MODIFY,
    "git_commit": MODIFY,
    "git_publish": MODIFY,
    "screen_analysis": INSPECT,
    "project_question": INSPECT,
    "pending_approval": ACT,
    "clarification": CLARIFY,
}

# Operations that make something rather than operate something.
_CREATE_OPERATIONS = frozenset({"create_file", "create_folder"})


@dataclass(frozen=True)
class SemanticGoal:
    """What the person wants, and what it is about."""

    intent: str = CHAT
    subject: str = ""
    recommendation: bool = False

    @property
    def wants_information(self) -> bool:
        return self.intent in {EXPLAIN, RETRIEVE, VERIFY, RECOMMEND, COMPARE}

    @property
    def wants_something_done(self) -> bool:
        return self.intent in {ACT, CREATE, MODIFY, INSPECT}

    def log_block(self) -> str:
        """The debugging view. Console only -- never the conversation UI."""
        return (
            "[Goal]\n"
            f"  Intent: {self.intent}\n"
            f"  Subject: {self.subject or '(none)'}\n"
            f"  Recommendation: {str(self.recommendation).lower()}"
        )


def _value(route: Any, name: str, default: Any) -> Any:
    value = getattr(route, name, default)
    return default if value is None else value


def read(route: Any) -> SemanticGoal:
    """Translate a routing decision into what the person actually wanted."""
    label = str(_value(route, "intent", "")).strip()
    subject = str(
        _value(route, "topic", "") or _value(route, "normalized_request", "")
    ).strip()
    recommendation = bool(_value(route, "recommendation_needed", False))
    operation = str(_value(route, "computer_operation", "")).strip()
    request = str(
        _value(route, "normalized_request", "") or _value(route, "topic", "")
    )

    # "task_action" is not a goal. It means the planner will need several
    # steps, which is a statement about delivery -- measured live: "compare
    # some hotels in seoul" arrived under this label and came out as ACT,
    # because the label was read instead of the words. What the person wants
    # is whatever they said; how many steps it takes is the planner's
    # business, and reaches the need, not the goal.
    if label == "task_action":
        # Several steps, yes -- but steps towards what? Only a request that
        # names something to operate is an action; the rest are questions
        # that happen to need more than one lookup.
        if names_a_surface(request):
            intent = ACT
        else:
            intent = RECOMMEND if recommendation else RETRIEVE
    else:
        intent = _BY_ROUTER_LABEL.get(label, CHAT)

    # A question that has been asked outranks what it was asked about.
    if intent == CLARIFY:
        return SemanticGoal(CLARIFY, subject, recommendation)

    # Making a file is not operating an application, even though one router
    # label covers both.
    if operation in _CREATE_OPERATIONS:
        intent = CREATE

    # Wanting options, and wanting them weighed, are different goals -- and
    # they call for different answers even from identical evidence. Both can
    # arrive under any information-shaped label, so they are read from the
    # request rather than the label.
    # Only an information-shaped goal can be reclassified this way. The
    # trailing 'or recommendation' let a recommendation flag overwrite an
    # ACT goal, so "open Booking.com and find me hotels" came back as a
    # plain recommendation and never reached the browser.
    if intent in {RETRIEVE, VERIFY, EXPLAIN, CHAT}:
        if _COMPARISON.search(request):
            intent = COMPARE
        elif recommendation:
            intent = RECOMMEND

    # Verification is a property of what is being asked for, not of which
    # classifier label carried it.
    if intent == RETRIEVE and bool(_value(route, "verification_required", False)):
        intent = VERIFY

    # A label of "web_search" beside an explicit "this is stable knowledge"
    # is the router contradicting itself, and the tool name used to win by
    # default. It does not any more: both signals have to agree that a
    # lookup is needed before the goal says so.
    if (
        intent == RETRIEVE
        and str(_value(route, "information_freshness", "")) == "stable"
        and not _value(route, "requires_external_evidence", False)
        and not _value(route, "verification_required", False)
    ):
        intent = EXPLAIN

    return SemanticGoal(
        intent=intent,
        subject=subject,
        recommendation=recommendation,
    )
