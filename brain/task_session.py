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
from brain import result_state
from brain import surface_log
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
        # Tasks the conversation has moved on from, most recent first. They
        # are superseded rather than destroyed: "back to those hotels" is a
        # real thing to say, and answering it needs the hotels to still
        # exist somewhere. Nothing here is ever consulted unless the person
        # explicitly asks to go back -- historical state must never
        # silently take part in the current turn.
        self._history: list[RecommendationProblem] = []
        # The one answer to "what are we talking about", so the layers
        # downstream read it instead of each deriving their own.
        self._focus: conversation_focus.Focus | None = None
        # What the person has already answered, by dimension, for as long
        # as this session lasts. A restarted problem drops its constraints;
        # the person's memory of having said it does not.
        self._answered: dict[tuple[str, str], str] = {}

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

    def note_turn(self, text: str, *, subject: str = "", pointer: bool = False):
        """Fold this turn into the focus, and hand back what it now is.

        ``pointer`` says the caller already knows this turn is about
        something the conversation is holding -- a correction to the
        action just taken, say. Such a turn introduces no subject of its
        own, and reading one out of it is how "I meant only one S" became
        a topic called "only one S".
        """
        focus = self.focus()
        if focus is None:
            focus = conversation_focus.start(now=time.monotonic())
        self._focus = conversation_focus.update(
            focus, text, subject=subject, now=time.monotonic(),
            pointer=pointer,
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
        follow_up: bool = False,
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
        # Asking to go back to something is answered before anything else:
        # it is neither a continuation of what is open nor a new problem,
        # and reading it as either loses the task being returned to.
        restored = self.reactivate(text)
        if restored is not None:
            return restored

        problem = self.active_recommendation()
        same_problem = bool(
            problem is not None
            and recommendation_state.about_the_same_thing(
                problem, text, subject=subject, topic_shift=topic_shift,
                follow_up=follow_up,
            )
        )
        if problem is not None:
            surface_log.note("[Task Continuity]")
            surface_log.note(f"  previous_task: {problem.id[:8]}")
            surface_log.note(f"  previous_thing: {problem._thing() or '(none)'}")
            surface_log.note(
                f"  current_thing: "
                f"{recommendation_state._head_noun(subject or text) or '(none)'}"
            )
            surface_log.note(f"  follow_up: {bool(follow_up)}")
            surface_log.note(
                f"  decision: {'CONTINUE' if same_problem else 'NEW_TASK'}"
            )
            surface_log.note(
                "  reason: "
                + ("the turn refines or refers to what is open"
                   if same_problem
                   else "current thing differs from the active thing")
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
            # The task being left keeps everything it had -- candidates,
            # constraints, evidence, verification -- but keeps it *out of
            # the way*. A new problem starts empty by construction, which
            # is what makes "old state must not survive a switch" true of
            # the structure rather than of a filter downstream.
            if problem is not None:
                self._supersede(problem)
                surface_log.note("[Task Lifecycle]")
                surface_log.note(f"  superseded: {problem.id[:8]}")
            if not recommendation_state.references_conversation_anchor(text):
                location = ""
                anchor = ""
            # No domain here. ``update`` reads it from the request once the
            # subject has been reduced to a phrase, which is the only point
            # at which a conditional clause ("what if you have a car") can
            # be told apart from the thing being asked about.
            problem = recommendation_state.start(
                subject or text, now=time.monotonic(),
            )
        was_new = problem.id != getattr(
            self._problem, "id", None,
        ) if problem is not None else True
        self._problem = recommendation_state.update(
            problem,
            text,
            subject=subject,
            location=location,
            anchor=anchor,
            said_before=said_before,
            now=time.monotonic(),
        )
        if was_new:
            surface_log.note(f"  activated: {self._problem.id[:8]}")
        for slot in self._problem.constraints:
            self._answered[(self._problem._thing(), slot.name)] = slot.value
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
            remembered = (
                next(iter(problem.values(dimension)), "")
                or self._answered.get((problem._thing(), dimension), "")
            )
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
                    self._answered[(problem._thing(), dimension)] = slot.value
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

        Accepts either bare names or whole
        :class:`~brain.result_state.Candidate` records, and stores records
        either way. Names were all this used to keep, which meant the URL,
        the ranking reason and the verdict were computed by the fit layer
        and thrown away in the same breath -- so "open the second one" had
        a position to count to and nothing to open.
        """
        problem = self.active_recommendation()
        if problem is None:
            return
        kept: list[result_state.Candidate] = []
        for rank, item in enumerate(items or ()):
            if isinstance(item, result_state.Candidate):
                kept.append(replace(item, rank=rank))
                continue
            name = " ".join(str(item).split()).strip()
            if name:
                kept.append(result_state.Candidate(name=name, rank=rank))
            if len(kept) >= 8:
                break
        found = tuple(kept[:8])
        if not found and not evidence:
            return
        self._problem = replace(
            problem,
            candidates=found or problem.candidates,
            evidence=(
                tuple(str(value) for value in evidence)[-4:]
                or problem.evidence
            ),
        )

    def results(self) -> "result_state.ResultSet":
        """The candidates in hand, as a result set rather than a list.

        A view rather than a second store: the problem still owns them, and
        this is what gives a caller positions, identities and the ability to
        ask whether any of them can actually be opened.
        """
        problem = self.active_recommendation()
        held = tuple(getattr(problem, "candidates", ()) or ())
        items = tuple(
            item if isinstance(item, result_state.Candidate)
            else result_state.Candidate(name=str(item), rank=rank)
            for rank, item in enumerate(held)
        )
        return result_state.ResultSet(
            items=items,
            query=str(getattr(problem, "subject", "") or ""),
            source="web_search",
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

    HISTORY_LIMIT = 4

    def _supersede(self, problem) -> None:
        """Move a task out of the way of the one replacing it."""
        if problem is None:
            return
        self._history = [
            held for held in self._history if held.id != problem.id
        ][:self.HISTORY_LIMIT - 1]
        self._history.insert(0, problem)

    def _historical_match(self, named: str):
        """The superseded task the person means, or nothing.

        Matched on the thing itself, not on any word in the sentence. A
        turn has to name what it is going back to.
        """
        wanted = recommendation_state._head_noun(named)
        if not wanted:
            return None
        for held in self._history:
            thing = recommendation_state._head_noun(held._thing() or "")
            subject = str(getattr(held, "subject", "") or "").casefold()
            if thing and (thing == wanted or wanted in subject):
                return held
        return None

    def reactivate(self, text: str):
        """Bring back a task the person explicitly asked to return to.

        The one path by which historical state may become current again,
        and it needs the person to have said so. The task being left is
        superseded in its turn, so a return is a switch like any other and
        the same one rule holds throughout: exactly one task is active.
        """
        named = recommendation_state.returns_to_earlier(text)
        if not named:
            return None
        restored = self._historical_match(named)
        if restored is None:
            return None
        surface_log.note("[Task Continuity]")
        surface_log.note("  decision: REACTIVATE")
        surface_log.note(f"  task: {restored.id[:8]} ({restored._thing()})")
        surface_log.note(f"  reason: explicit reference to {named!r}")
        leaving = self._problem
        if leaving is not None and leaving.id != restored.id:
            self._supersede(leaving)
        self._history = [
            held for held in self._history if held.id != restored.id
        ]
        # A task coming back gets a fresh lease; it expired only because
        # the conversation was elsewhere.
        self._problem = replace(
            restored,
            expires_at=time.monotonic() + float(self.ttl_seconds),
        )
        return self._problem

    def clear_recommendation(self) -> None:
        self._problem = None

    def clear(self) -> None:
        self._context = None
        self._problem = None
        self._history = []
        self._focus = None
        self._answered.clear()

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
