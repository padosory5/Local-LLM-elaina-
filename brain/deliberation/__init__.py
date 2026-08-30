"""What a request actually asked for, separated from the words that asked.

The deliberation layer. Phase 1 gave a request a type: a Goal whose slots
are values someone deliberately extracted, so the utterance itself can
never reach a keyboard. Phase 3 added the gate every request now passes
through -- act, act and say the assumption, or ask one question -- and the
pending question that lets an answer *continue* a request rather than
start a new one.
"""

from brain.deliberation.clarification import (
    ACT,
    ACT_AND_SAY,
    ASK,
    Decision,
    decide,
)
from brain.deliberation.goal import Goal, Slot
from brain.deliberation.interaction import (
    InteractionDecision,
    decide as decide_interaction,
)
from brain.deliberation.interpreter import interpret
from brain.deliberation.pending import ClarificationGate, PendingClarification

# Two different decisions, both named ``decide`` in their own module and
# deliberately not sharing a name here. ``decide`` answers "do I know enough
# to act on this goal"; ``decide_interaction`` answers "what should happen
# about this request at all". Collapsing them would hide that the second one
# runs first and can conclude that no goal is needed.
__all__ = [
    "ACT",
    "ACT_AND_SAY",
    "ASK",
    "ClarificationGate",
    "Decision",
    "Goal",
    "InteractionDecision",
    "PendingClarification",
    "Slot",
    "decide",
    "decide_interaction",
    "interpret",
]
