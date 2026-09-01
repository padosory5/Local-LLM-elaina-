"""Read the request before asking a model where to send it.

Move 1 of the rebuild. Until now the order was: a model router classified
every utterance, and only then did anything deterministic look at the
words. Measured on the live system, that put three of four headline
requests somewhere the deliberation layer does not exist --

    "book me a hotel in guam"   -> web_search      (3/3 runs)
    "play my liked songs"       -> browser_action  (3/3 runs)
    "open my documents folder"  -> create_folder   (3/3 runs)

-- so the gate, the skills and the guards built for exactly those requests
could never run. The tests that proved them called the planners directly.

This flips the order. The interpreter reads the utterance first; when it
types to a goal that a skill serves, the request goes straight there with
its slots intact. The model router still exists and still decides
everything this cannot read -- which is most of conversation, and all the
requests where a classifier genuinely is the right tool.

Two rules keep it safe. It only claims a request when a *skill* or a known
precondition applies, so an unrecognised sentence still reaches the router
untouched. And it never answers: it decides where the request goes, and
the gate inside that planner decides whether to act or ask.
"""

from __future__ import annotations

from dataclasses import dataclass

from brain.deliberation.clarification import Decision, decide
from brain.deliberation.goal import Goal
from brain.deliberation.interpreter import interpret
from brain.skills import skill_for

# Goal kinds this door is willing to route on its own, and where each goes.
# A kind is listed here only when the destination owns a gate for it: the
# desktop planner for anything she can do in an app, the browser planner
# for a commitment that has to be researched before it can be made.
_DESKTOP_KINDS = frozenset({
    "play_track", "play_collection", "play_unnamed", "shuffle_collection",
    "find_in_collection",
})

_BROWSER_KINDS = frozenset({"booking"})


@dataclass(frozen=True)
class DirectRoute:
    """A request understood without asking a model anything.

    ``operation`` names where it goes when this door knows; it is empty for
    a request that was read but belongs to the router. The decision travels
    with it either way, because whether she can proceed is a property of
    the request, not of the destination -- which is what lets one gate
    serve every path.
    """

    goal: Goal
    operation: str
    reason: str
    decision: Decision

    @property
    def target(self) -> str:
        return self.goal.utterance

    @property
    def asks(self) -> bool:
        return self.decision.asks

    @property
    def question(self) -> str:
        return self.decision.question


def read(
    utterance: str, *, recent_subject: str = "", profile=None,
    media_application: str = "",
) -> DirectRoute | None:
    """What this request is, and where it belongs when that is readable.

    Returns None only for what cannot be typed at all -- conversation,
    mostly -- which is the signal to let the model router decide. Anything
    else comes back with its decision attached, even when this door has no
    destination for it: a request that cannot proceed should be asked
    about wherever it was headed.
    """
    goal = interpret(utterance, media_application=media_application)
    if goal.kind in {"generic", "unknown"}:
        return None

    decision = decide(goal, recent_subject=recent_subject, profile=profile)
    # A decision may fill a slot from what she knows, which can change what
    # kind of request it is -- "play some music" becomes a track request
    # once the title is known.
    settled = decision.goal

    if settled.kind in _DESKTOP_KINDS or goal.kind in _DESKTOP_KINDS:
        served = skill_for(settled) is not None
        return DirectRoute(
            goal=settled,
            operation="ui_action",
            reason=(
                f"The request reads as {settled.kind}, which "
                + ("a skill serves." if served else "needs one more detail.")
            ),
            decision=decision,
        )

    if settled.kind in _BROWSER_KINDS:
        return DirectRoute(
            goal=settled,
            operation="browser_action",
            reason=(
                "The request commits to something, so its inputs are "
                "settled before anything is opened."
            ),
            decision=decision,
        )

    # Read, but not this door's to route: searches, ordinary research and
    # text entry keep going through the router, which weighs context this
    # deliberately does not look at. The decision still travels with it, so
    # the turn can ask before any of those paths begins.
    return DirectRoute(
        goal=settled,
        operation="",
        reason=f"The request reads as {settled.kind}.",
        decision=decision,
    )
