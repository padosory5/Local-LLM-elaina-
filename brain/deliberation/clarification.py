"""The gate: does she know enough to act, and what to do when she doesn't.

Phase 3. Every request passes through one place that answers a single
question, and it has three answers rather than two:

* **act** -- every required value is known.
* **act and say the assumption** -- a value was filled from what she
  already knows rather than from what was said, and the action is cheap
  and reversible. She does it, and says out loud what she assumed, so
  being wrong costs one sentence instead of a wasted turn.
* **ask** -- something required is missing and nothing may stand in for
  it. One question, naming what she needs.

The middle exit is what keeps this from becoming a nag. An assistant that
asks about everything is as unusable as one that never asks; the rule is
about consequence, not certainty alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from brain.deliberation.goal import SOURCE_PROFILE, SOURCE_WORLD, Goal, Slot
from brain.deliberation.profile import (
    ARTIST_FOR_TITLE,
    FAVOURITE_TRACK,
    UserProfile,
)
from brain.media_target import collection_phrase
from brain.task_discovery_policy import TaskDiscoveryPolicy

ACT = "act"
ACT_AND_SAY = "act_and_say"
ASK = "ask"

# What each kind of request cannot proceed without.
_REQUIRED_SLOTS = {
    "play_track": ("title",),
    "play_collection": ("collection",),
    "text_input": ("text",),
    "search": ("query",),
    "open_app": ("app",),
}

_QUESTIONS = {
    "title": "Which song would you like me to play?",
    "text": "What would you like me to type?",
    "query": "What should I search for?",
    "app": "Which app should I open?",
}

# How an answer completes the request. The answer is put back into a whole
# sentence and read by the same interpreter, so a completed request runs
# through exactly the path it would have taken if it had been said that way
# in the first place -- every guard included. Kinds absent from this table
# still get their question; their answer is simply routed as a fresh
# request rather than bound automatically.
_ANSWER_TEMPLATES = {
    ("play_unnamed", "title"): "Play {answer} in Spotify.",
    ("play_track", "title"): "Play {answer} in Spotify.",
}


@dataclass(frozen=True)
class Decision:
    """What the gate decided, and everything needed to carry it out."""

    action: str
    goal: Goal
    question: str = ""
    missing: str = ""
    assumption: str = ""
    template: str = ""

    @property
    def asks(self) -> bool:
        return self.action == ASK


def decide(
    goal: Goal,
    *,
    recent_subject: str = "",
    profile: UserProfile | None = None,
) -> Decision:
    """Decide whether to act, act and admit an assumption, or ask.

    Two things may stand in for a value nobody said, and both are said out
    loud when they do. ``recent_subject`` is what she last verifiably
    played this session. ``profile`` is what she has learned across
    sessions -- which of two identically titled songs this person means,
    and what they put on most -- and it only offers something once it is
    established enough to be worth acting on.
    """
    if goal.kind == "unknown":
        return Decision(
            ASK,
            goal,
            question="I'm not sure what you'd like me to do -- say it another way?",
        )

    if goal.kind == "play_track" and not goal.has("artist") and profile:
        # Two songs share this title and she knows which one they mean.
        # This is the case that made a profile worth having: "Bang Bang"
        # is IVE's or Jessie J's, and getting it wrong plays a stranger.
        known = profile.preferred(ARTIST_FOR_TITLE, key=goal.value("title"))
        if known is not None:
            filled = _with_slot(goal, "artist", known.value, SOURCE_PROFILE)
            return Decision(
                ACT_AND_SAY,
                filled,
                assumption=(
                    f"Playing {goal.value('title')} by {known.value} -- "
                    f"{known.because()}; say the word if you meant another."
                ),
            )

    if goal.kind == "play_unnamed":
        collection = goal.value("collection")
        if collection:
            # A whole collection is not something she can start yet, so the
            # honest question asks for what she *can* do rather than
            # promising a shuffle that would not happen.
            return _ask_for(
                goal,
                "title",
                (
                    f"I can only start one specific song for now, not "
                    f"{collection_phrase(collection)}. Which song do you want?"
                ),
            )
        if recent_subject:
            # Once the title is known the request *is* a track request, and
            # the skill that serves those is the one that should run.
            filled = _with_slot(
                goal, "title", recent_subject, SOURCE_WORLD,
                kind="play_track",
            )
            return Decision(
                ACT_AND_SAY,
                filled,
                assumption=(
                    f"Putting {recent_subject} back on -- say the word if "
                    "you meant something else."
                ),
            )
        favourite = (
            profile.preferred(FAVOURITE_TRACK) if profile else None
        )
        if favourite is not None:
            # Nothing has been played this session, but she has been asked
            # for this often enough to have a good guess.
            filled = _with_slot(
                goal, "title", favourite.value, SOURCE_PROFILE,
                kind="play_track",
            )
            return Decision(
                ACT_AND_SAY,
                filled,
                assumption=(
                    f"Putting on {favourite.value} -- {favourite.because()}; "
                    "say the word if you meant something else."
                ),
            )
        return _ask_for(goal, "title", _QUESTIONS["title"])

    if goal.kind in {"research", "booking"}:
        return _research_decision(goal)

    for name in _REQUIRED_SLOTS.get(goal.kind, ()):
        if not goal.has(name):
            return _ask_for(goal, name, _QUESTIONS.get(name, ""))

    return Decision(ACT, goal)


def _research_decision(goal: Goal) -> Decision:
    """Committing to something needs its inputs; looking around does not.

    A hotel *booking* without dates is not a booking at all, so nothing is
    browsed before that is settled. A hotel *search* without dates is
    merely less precise, and the task planner already offers that
    conversation through the same discovery policy -- asking again here
    would be a second question for one request, and would interrupt a task
    that had already answered it.
    """
    if goal.kind != "booking":
        return Decision(ACT, goal)
    preferences = {name: slot.value for name, slot in goal.slots.items()}
    category = goal.value("category")
    missing = TaskDiscoveryPolicy.missing_required_preferences(
        category, preferences,
    )
    if not missing:
        return Decision(ACT, goal)
    slot = missing[0]
    return Decision(
        ASK,
        goal,
        question=TaskDiscoveryPolicy.required_preference_prompt(
            category, preferences,
        ),
        missing=slot,
        # The answer completes this request rather than replacing it: the
        # original wording is kept and the missing detail added to it.
        template=f"{goal.utterance} on {{answer}}",
    )


def _ask_for(goal: Goal, slot: str, question: str) -> Decision:
    return Decision(
        ASK,
        goal,
        question=question or f"What {slot} did you mean?",
        missing=slot,
        template=_ANSWER_TEMPLATES.get((goal.kind, slot), ""),
    )


def _with_slot(
    goal: Goal, name: str, value: str, source: str, *, kind: str = "",
) -> Goal:
    slots = dict(goal.slots)
    slots[name] = Slot(name, value, source, 0.7)
    return Goal(
        kind=kind or goal.kind, utterance=goal.utterance, slots=slots,
    )
