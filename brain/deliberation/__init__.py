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
from brain.deliberation.interpreter import interpret
from brain.deliberation.pending import ClarificationGate, PendingClarification

__all__ = [
    "ACT",
    "ACT_AND_SAY",
    "ASK",
    "ClarificationGate",
    "Decision",
    "Goal",
    "PendingClarification",
    "Slot",
    "decide",
    "interpret",
]
