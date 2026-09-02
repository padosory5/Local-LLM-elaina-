"""Short-lived structured context for conversational task follow-ups.

This is intentionally separate from long-term user memory.  A hotel price or
GPU listing is volatile, so it lives only for the active chat session and is
used solely to resolve references such as "which of those" or "the second
one".  It never authorises a browser action by itself.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from typing import Any

from brain import conversation_focus
from brain import recommendation_state
from brain import references
from brain.recommendation_state import RecommendationProblem


# Widened for the recall ladder, which needs the same judgement. The four
# shapes below were all missed before: "which was the cheapest", "which had
# the best rating", "compare the first two", "any of them".
_SUPERLATIVE = (
    r"first|second|third|last|best|worst|cheapest|closest|nearest|"
    r"biggest|smallest|highest|lowest|top|nicest|quietest"
)
_DEICTIC_REFERENCE = re.compile(
    r"\b(?:these|those|them|they)\b"
    rf"|\bthe\s+(?:{_SUPERLATIVE})\b"
    r"|\bwhich\s+(?:one|of|was|is|were|are|had|has|would|do|did)\b"
    r"|\bcompare\s+(?:the|them|those|these|both)\b"
    r"|\b(?:any|either|both|each)\s+of\s+(?:them|those|these)\b"
    r"|\bof\s+(?:those|these|them)\b",
    re.I,
)

# Public: the recall ladder in brain/chat_engine.py asks the same question.
DEICTIC_REFERENCE = _DEICTIC_REFERENCE


@dataclass(frozen=True)
class TaskEvidenceContext:
    goal: str
    information: tuple[str, ...]
    items: tuple[Any, ...]
    expires_at: float


class TaskSessionStore:
    """Keep the most recent grounded shortlist for one conversational turn."""

    def __init__(self, *, ttl_seconds: int = 15 * 60) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._context: TaskEvidenceContext | None = None
        # The recommendation the conversation is currently working on. It
        # lives here rather than in a store of its own: this is already the
        # session-scoped, TTL'd holder of "what we were just talking
        # about", and a second one would only be a place for the two to
        # disagree. See brain/recommendation_state.py.
        self._problem: RecommendationProblem | None = None
        # The one answer to "what are we talking about", so the layers
        # downstream read it instead of each deriving their own.
        self._focus: conversation_focus.Focus | None = None
        # What the person has already answered, by dimension, for as long
        # as this session lasts. A restarted problem drops its constraints;
        # the person's memory of having said it does not.
        self._answered: dict[str, str] = {}

    def remember(self, task_state: Any) -> None:
        raw_items = tuple(getattr(task_state, "collected_items", ()) or ())
        if not raw_items:
            return
        # Preserve the first evidence record per name; it is the one the
        # planner actually presented most recently, and prevents a follow-up
        # prompt from becoming bloated with duplicate extraction passes.
        unique: list[Any] = []
        names: set[str] = set()
        for item in raw_items:
            name = str(getattr(item, "name", "")).strip()
            key = name.casefold()
            if not name or key in names:
                continue
            names.add(key)
            unique.append(item)
        if not unique:
            return
        self._context = TaskEvidenceContext(
            goal=str(getattr(task_state, "goal", "")).strip(),
            information=tuple(
                str(value)
                for value in (getattr(task_state, "collected_information", ()) or ())
            )[-4:],
            items=tuple(unique[:8]),
            expires_at=time.monotonic() + self.ttl_seconds,
        )

    # -------------------------------------------------- conversation focus

    def note_turn(self, text: str, *, subject: str = ""):
        """Fold this turn into the focus, and hand back what it now is."""
        focus = self.focus()
        if focus is None:
            focus = conversation_focus.start(now=time.monotonic())
        self._focus = conversation_focus.update(
            focus, text, subject=subject, now=time.monotonic(),
        )
        return self._focus

    def focus(self):
        held = self._focus
        if held is not None and held.expired(time.monotonic()):
            self._focus = None
            return None
        return held

    # ------------------------------------------------- active recommendation

    def note_recommendation_turn(
        self,
        text: str,
        *,
        subject: str = "",
        topic_shift: bool = False,
        location: str = "",
        anchor: str = "",
        said_before: str = "",
    ) -> "RecommendationProblem":
        """Fold this turn into the open recommendation, or open a new one.

        Called once per turn on the conversational path. Whether anything
        is *done* with the result is the caller's decision; keeping the
        problem current is not, because the turn that needs it ("pull up
        some spots") is never the turn that establishes it.
        """
        problem = self.active_recommendation()
        same_problem = bool(
            problem is not None
            and recommendation_state.about_the_same_thing(
                problem, text, subject=subject, topic_shift=topic_shift,
            )
        )
        if (
            same_problem
            and problem is not None
            and not recommendation_state.references_conversation_anchor(text)
        ):
            # Conversation background is attached when a task explicitly
            # points at it. It must not seep into an unrelated task on a
            # later one-word clarification.
            if not problem.location:
                location = ""
            if not problem.anchor:
                anchor = ""
        if problem is None or not same_problem:
            if not recommendation_state.references_conversation_anchor(text):
                location = ""
                anchor = ""
            problem = recommendation_state.start(
                subject or text,
                domain=recommendation_state.domain_for(text),
                now=time.monotonic(),
            )
        self._problem = recommendation_state.update(
            problem,
            text,
            subject=subject,
            location=location,
            anchor=anchor,
            said_before=said_before,
            now=time.monotonic(),
        )
        return self._problem

    def answer_recommendation_dimension(
        self, problem_id: str, dimension: str, reply: str,
    ) -> "RecommendationProblem | None":
        """Resolve a clarification only against the problem that owns it."""
        problem = self.active_recommendation()
        if problem is None or not problem_id or problem.id != problem_id:
            return None
        # "Same as I said." The answer is one they already gave, and a new
        # problem does not carry the old one's constraints -- so the
        # question came round again, word for word. Answered dimensions
        # are kept for the session rather than for the problem, because
        # that is the span over which a person remembers saying it.
        if recommendation_state.points_at_an_earlier_answer(reply):
            remembered = self._answered.get(dimension, "")
            if not remembered:
                return None
            reply = remembered
        resolved = recommendation_state.apply_dimension_answer(
            problem, dimension, reply, now=time.monotonic(),
        )
        if resolved is not None:
            self._problem = resolved
            for slot in resolved.constraints:
                if slot.name == dimension:
                    self._answered[dimension] = slot.value
        return resolved

    def active_recommendation(self) -> "RecommendationProblem | None":
        problem = self._problem
        if problem is not None and problem.expired(time.monotonic()):
            self._problem = None
            return None
        return problem

    def record_candidates(self, items, *, evidence=()) -> None:
        """Keep what a search actually found against the open problem.

        So a follow-up ranks the candidates already in hand rather than
        searching a second time for the same list.
        """
        problem = self.active_recommendation()
        if problem is None:
            return
        names = tuple(
            str(name).strip() for name in items if str(name).strip()
        )[:8]
        if not names and not evidence:
            return
        self._problem = replace(
            problem,
            candidates=names or problem.candidates,
            evidence=(
                tuple(str(value) for value in evidence)[-4:]
                or problem.evidence
            ),
        )

    def resolve_reference(self, text: str):
        """Which listed candidate the turn points at, or why none.

        The candidates were already being stored and only ever logged, so
        "open the second one" resolved against nothing. This reads them back.
        An empty list never resolves -- the right answer to a position named
        against no result set is a question, not a guess.
        """
        problem = self.active_recommendation()
        candidates = tuple(getattr(problem, "candidates", ()) or ())
        reference = references.resolve(text, candidates)
        if reference.ambiguous or reference.resolved:
            print(f"[Reference] {reference.log_line()}")
        return reference

    def note_source_override(self, value: str) -> bool:
        """Hold a named source for the length of the open task.

        Returns whether there was a task to hold it against -- an override
        with no active problem has nothing to scope it to and is the
        caller's business, not this store's.
        """
        problem = self.active_recommendation()
        if problem is None or not str(value or "").strip():
            return False
        self._problem = replace(problem, source_override=str(value).strip())
        return True

    def source_override(self) -> str:
        problem = self.active_recommendation()
        return getattr(problem, "source_override", "") if problem else ""

    def note_dimension_asked(self, dimension: str) -> None:
        """Record that this question has been put, so it is not re-asked."""
        problem = self.active_recommendation()
        if problem is None or not dimension:
            return
        if dimension in problem.asked:
            return
        self._problem = replace(
            problem, asked=problem.asked + (dimension,),
        )

    def clear_recommendation(self) -> None:
        self._problem = None

    def clear(self) -> None:
        self._context = None
        self._problem = None
        self._focus = None

    def current(self) -> TaskEvidenceContext | None:
        context = self._context
        if context is not None and time.monotonic() >= context.expires_at:
            self._context = None
            return None
        return context

    def context_for_followup(self, request: str) -> TaskEvidenceContext | None:
        if not _DEICTIC_REFERENCE.search(str(request)):
            return None
        return self.current()

    def public_conversation_state(self) -> dict[str, list[str]]:
        """Names only: enough for routing, never full source prose."""
        context = self.current()
        if context is None:
            return {}
        return {
            "task_candidates": [
                str(getattr(item, "name", ""))
                for item in context.items
                if str(getattr(item, "name", "")).strip()
            ],
        }
