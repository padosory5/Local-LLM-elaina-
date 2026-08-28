"""One unanswered question, held until the person answers it.

The point is that answering must *continue* the request rather than start
a new one. The answer is put back into a whole sentence and read by the
same interpreter, so "which song?" -> "Bang Bang by IVE" becomes exactly
the request that would have been made if it had been said that way at the
start -- every guard on that path still applies, none of them reimplemented
for the answered case.

Short-lived on purpose, and one at a time: a question that has been sitting
around for two minutes is no longer the thing the person is replying to.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from brain.deliberation.goal import SOURCE_ASKED, Goal, Slot
from brain.deliberation.interpreter import interpret

# Long enough to answer in conversation, short enough that a later,
# unrelated sentence is never mistaken for the answer.
_DEFAULT_EXPIRY_SECONDS = 120

# A reply that issues an instruction of its own is a new request, not an
# answer to the outstanding question -- "no, open Discord instead".
_INSTRUCTION_REPLY = re.compile(
    r"^(?:no\b|nah\b|never\s*mind|forget\s+it|stop\b|cancel\b)"
    r"|\b(?:open|launch|close|search|pause|stop|type|write|click)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PendingClarification:
    """A question that was asked, and what answering it would complete."""

    goal: Goal
    slot: str
    question: str
    template: str
    created_at: float
    expires_at: float

    @property
    def bindable(self) -> bool:
        """Whether an answer can complete this request automatically."""
        return bool(self.template and self.slot)

    def reads_as_answer(self, reply: str) -> bool:
        """Whether this reply is an answer rather than a new instruction."""
        text = " ".join(str(reply or "").split())
        if not text or not self.bindable:
            return False
        if _INSTRUCTION_REPLY.search(text):
            return False
        # An answer is short. A paragraph is a change of subject.
        return len(text.split()) <= 12

    def completed(self, reply: str) -> Goal | None:
        """The whole request, as if it had been said complete."""
        answer = " ".join(str(reply or "").split()).strip(" .!?,")
        if not answer or not self.bindable:
            return None
        completed = interpret(self.template.format(answer=answer))
        if not completed.has(self.slot):
            return None
        # Provenance stays honest: this value was asked for, not overheard.
        slots = dict(completed.slots)
        slots[self.slot] = Slot(
            self.slot, completed.value(self.slot), SOURCE_ASKED, 1.0,
        )
        return Goal(
            kind=completed.kind, utterance=completed.utterance, slots=slots,
        )

    def public_context(self) -> dict[str, str | int]:
        return {
            "question": self.question,
            "missing": self.slot,
            "expires_in_seconds": max(
                0, int(self.expires_at - time.monotonic()),
            ),
        }


class ClarificationGate:
    """Hold at most one outstanding question, until it is answered."""

    def __init__(self, expiry_seconds: int = _DEFAULT_EXPIRY_SECONDS) -> None:
        self.expiry_seconds = max(15, int(expiry_seconds))
        self._pending: PendingClarification | None = None

    def offer(
        self,
        *,
        goal: Goal,
        slot: str,
        question: str,
        template: str = "",
    ) -> PendingClarification:
        now = time.monotonic()
        self._pending = PendingClarification(
            goal=goal,
            slot=slot,
            question=question,
            template=template,
            created_at=now,
            expires_at=now + self.expiry_seconds,
        )
        return self._pending

    def peek(self) -> PendingClarification | None:
        if self._pending is None:
            return None
        if time.monotonic() >= self._pending.expires_at:
            self._pending = None
            return None
        return self._pending

    def clear(self) -> None:
        self._pending = None
