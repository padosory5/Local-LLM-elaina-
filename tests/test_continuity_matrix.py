"""Twenty-two multi-turn conversations, through the real continuity state.

What makes a follow-up work is not the transcript; it is a small amount of
structured state that outlives the turn. These conversations drive that state
directly -- the real :mod:`brain.conversation_focus`, the real
:class:`~brain.task_session.TaskSessionStore`, the real
:class:`~security.capability_offer.CapabilityOfferGate` -- and assert what
each turn leaves behind.

Offline, and that is the point being made as well as a convenience: if
continuity needed the whole transcript replayed through a model, none of this
could be asserted cheaply. It can, because the state is small and explicit.

Each turn supplies what the routing layer would have offered as a subject. A
blank one means the turn named nothing of its own -- "find me something
cheaper", "open the second one" -- which is exactly the case continuity has
to carry.
"""

import json
import unittest
from pathlib import Path

from brain import conversation_focus
from brain import references
from brain.task_session import TaskSessionStore

MATRIX_PATH = Path(__file__).with_name("continuity_matrix.json")


class _Session:
    """One conversation's continuity state, advanced a turn at a time."""

    def __init__(self):
        self.store = TaskSessionStore()
        self.focus = conversation_focus.start()
        self.candidates: tuple[str, ...] = ()

    def say(self, turn: dict) -> dict:
        said = turn["said"]
        self.focus = conversation_focus.update(
            self.focus, said, subject=turn.get("offered_subject", ""),
        )
        if "candidates" in turn:
            self.candidates = tuple(turn["candidates"])
        seen = {
            "subject": self.focus.subject,
            "superseded": self.focus.superseded,
        }
        reference = references.resolve(said, self.candidates)
        if not reference.resolved and not reference.ambiguous:
            reference = references.resolve_bare(said, self.candidates)
        seen["reference"] = reference
        return seen


def _run(conversation: dict) -> list[dict]:
    session = _Session()
    return [session.say(turn) for turn in conversation["turns"]]


class ContinuityMatrixTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        cls.conversations = cls.matrix["conversations"]

    def test_the_benchmark_covers_the_required_ground(self):
        self.assertGreaterEqual(len(self.conversations), 20)
        kinds = {c["kind"] for c in self.conversations}
        for required in (
            "short_term", "correction", "result_reference",
            "ambiguous_reference", "task_continuation", "topic_change",
            "stale_context", "analogical", "pending_offer", "long_term",
        ):
            self.assertIn(required, kinds)

    def test_every_turn_leaves_the_expected_state(self):
        for conversation in self.conversations:
            seen = _run(conversation)
            for turn, state in zip(conversation["turns"], seen):
                label = f"{conversation['id']}: {turn['said']!r}"
                with self.subTest(turn=label):
                    if "subject" in turn:
                        self.assertEqual(
                            state["subject"], turn["subject"], label,
                        )
                    if "not_subject" in turn:
                        self.assertNotEqual(
                            state["subject"], turn["not_subject"], label,
                        )
                    if "superseded" in turn:
                        self.assertIn(
                            turn["superseded"], state["superseded"], label,
                        )

    def test_result_references_resolve_to_the_right_candidate(self):
        checked = 0
        for conversation in self.conversations:
            seen = _run(conversation)
            for turn, state in zip(conversation["turns"], seen):
                if "resolves_to" not in turn:
                    continue
                checked += 1
                with self.subTest(turn=turn["said"]):
                    reference = state["reference"]
                    self.assertTrue(
                        reference.resolved,
                        f"{turn['said']!r}: {reference.log_line()}",
                    )
                    self.assertEqual(reference.value, turn["resolves_to"])
        self.assertGreaterEqual(checked, 5)

    def test_an_ambiguous_reference_never_resolves(self):
        """The one that must not be a near-miss: nothing gets picked."""
        checked = 0
        for conversation in self.conversations:
            seen = _run(conversation)
            for turn, state in zip(conversation["turns"], seen):
                if not turn.get("unresolved"):
                    continue
                checked += 1
                with self.subTest(turn=turn["said"]):
                    reference = state["reference"]
                    self.assertFalse(
                        reference.resolved,
                        f"{turn['said']!r} resolved to {reference.value!r} "
                        "when it should have asked",
                    )
                    self.assertTrue(
                        reference.reason,
                        "an unresolved reference must say why",
                    )
        self.assertGreaterEqual(checked, 3)

    def test_a_correction_supersedes_rather_than_accumulates(self):
        for conversation in self.conversations:
            if conversation["kind"] != "correction":
                continue
            seen = _run(conversation)
            with self.subTest(conversation=conversation["id"]):
                # Whatever was corrected away is retired, and the current
                # subject is never one of the retired values.
                final = seen[-1]
                self.assertNotIn(final["subject"], final["superseded"])


class ContextMinimisationTests(unittest.TestCase):
    """Continuity does not depend on replaying the transcript."""

    def test_the_history_window_is_bounded(self):
        from brain.conversation_manager import ConversationManager

        manager = ConversationManager()
        for index in range(200):
            manager.add("user", f"turn {index}")

        self.assertLessEqual(len(manager.get_history()), 20)

    def test_state_that_carries_a_follow_up_is_small(self):
        # The whole continuity payload for a follow-up: a subject, a little
        # background, and the candidates in hand. Asserted so that "just send
        # the transcript" cannot creep back in as a fix.
        session = _Session()
        session.say({"said": "Find me some monitors.", "offered_subject": "monitors",
                     "candidates": ["Dell U2723", "LG 27GP850"]})

        self.assertEqual(session.focus.subject, "monitors")
        self.assertLessEqual(len(session.candidates), 8)
        self.assertLessEqual(len(session.focus.query_context()), 3)


class ExpiryTests(unittest.TestCase):
    """Stale state stops applying rather than lingering."""

    def test_each_kind_of_state_expires(self):
        from security.capability_offer import CapabilityOfferGate

        # Three different lifetimes, shortest first: an unanswered offer is
        # stale soonest, an active task next, the topic last.
        self.assertLess(
            CapabilityOfferGate().expiry_seconds,
            TaskSessionStore().ttl_seconds,
        )
        self.assertLessEqual(
            TaskSessionStore().ttl_seconds,
            conversation_focus.DEFAULT_TTL_SECONDS,
        )

    def test_an_expired_focus_reports_itself_as_expired(self):
        focus = conversation_focus.start("monitors", now=0.0, ttl=60)

        self.assertFalse(focus.expired(now=30.0))
        self.assertTrue(focus.expired(now=61.0))


if __name__ == "__main__":
    unittest.main()


class MemoryRankingTests(unittest.TestCase):
    """How well a memory matched must actually count for something.

    ``MemoryRanker`` weights similarity at 0.50 -- its largest term -- and
    reads it with ``getattr(memory, "similarity", 1.0)``. ``MemoryManager``
    discarded the FAISS distances, so nothing ever set that attribute and
    every memory scored an identical 1.0 there: ranking was decided entirely
    by importance, recency and access count.

    No embedding model is loaded here. The ranker is a pure function of the
    attributes, which is all that needs pinning.
    """

    class _Memory:
        def __init__(self, content, similarity, importance=5, access_count=0):
            from datetime import datetime
            self.content = content
            self.similarity = similarity
            self.importance = importance
            self.access_count = access_count
            self.last_accessed = datetime.utcnow()

    def test_a_closer_match_outranks_a_more_familiar_one(self):
        from brain.memory_ranker import MemoryRanker

        relevant = self._Memory("My major is ECE.", similarity=0.90)
        familiar = self._Memory(
            "The user likes coffee.", similarity=0.10,
            importance=9, access_count=20,
        )

        ranked = MemoryRanker().rank([familiar, relevant])

        self.assertEqual(ranked[0].content, "My major is ECE.")

    def test_similarity_is_read_from_the_memory(self):
        from brain.memory_ranker import MemoryRanker

        near = self._Memory("near", similarity=0.95)
        far = self._Memory("far", similarity=0.05)

        ranked = MemoryRanker().rank([far, near])

        self.assertEqual([m.content for m in ranked], ["near", "far"])
