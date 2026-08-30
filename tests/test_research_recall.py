"""What she found once, she does not go and find again.

Reported live: "Find me some good hotels in Seoul" then "Which one would you
choose?" answered with a car. The follow-up carried no subject of its own, so
nothing recognised that the previous turn had already found the answer, and
the search ran again on the literal words.

The recall ladder, cheapest first:

1. the active task's own evidence (TaskSessionStore)
2. recent research evidence in memory, by resolved subject
3. conversation history, already in every prompt

Research evidence is kept through the memory system that already exists --
one schema, one index, one retrieval path -- separated by ``category`` and
bounded by age, rather than in a second store built for the purpose.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from brain import capability_selection as caps
from brain.deliberation import goal_intent, interaction
from brain.intent_router import IntentDecision
from brain.task_session import DEICTIC_REFERENCE
from memory.memory_manager import RESEARCH_CATEGORY
from tests.turn_harness import build_engine


def _route(label: str, **fields) -> IntentDecision:
    fields.setdefault("confidence", 0.95)
    fields.setdefault("normalized_request", "the request")
    return IntentDecision(intent=label, **fields)


class _Memory:
    """One stored memory, as MemoryManager returns them."""

    def __init__(self, content, category=RESEARCH_CATEGORY, age_seconds=10):
        self.content = content
        self.category = category
        self.created_at = datetime.utcnow() - timedelta(seconds=age_seconds)
        self.is_active = True


class _RecordingMemory:
    """The memory system, recording what it was asked to keep and find."""

    def __init__(self, stored=()):
        self.stored: list[dict] = []
        self.queries: list[str] = []
        self._research = list(stored)

    def remember_research(self, **kwargs):
        self.stored.append(kwargs)
        return len(self.stored)

    def recall_research(self, subject, k=3, max_age_seconds=None):
        self.queries.append(subject)
        subject_key = str(subject).casefold()
        return [
            memory for memory in self._research
            if any(
                word in memory.content.casefold()
                for word in subject_key.split()
                if len(word) > 3
            )
        ][:k]

    def search(self, *_args, **_kwargs):
        return []


HOTELS = _Memory(
    "hotels in Seoul\n"
    "Asked: good hotels in Seoul\n"
    "Found: Hotel Entra Gangnam (4.5 stars, 180,000 KRW), Signiel Seoul "
    "(4.8 stars, 420,000 KRW), And Seoul Hotel (4.2 stars, 95,000 KRW).\n"
    "Options: Hotel Entra Gangnam; Signiel Seoul; And Seoul Hotel"
)


class FollowUpDetectionTests(unittest.TestCase):
    """The four shapes the report asked for, and the ones that must not match."""

    def test_the_reported_follow_ups_are_all_recognised(self):
        for request in (
            "which one would you choose",
            "which was the cheapest",
            "which had the best rating",
            "compare the first two",
        ):
            with self.subTest(request=request):
                self.assertTrue(DEICTIC_REFERENCE.search(request))

    def test_a_standalone_request_is_not_a_follow_up(self):
        for request in (
            "what is nvidia trading at",
            "open spotify",
            "tell me about iceland",
            "what is recursion",
        ):
            with self.subTest(request=request):
                self.assertFalse(DEICTIC_REFERENCE.search(request))


class RecallLadderTests(unittest.TestCase):
    """The engine's own ladder, with the memory system stood in for."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self.memory = _RecordingMemory(stored=[HOTELS])
        self.engine.memory_manager = self.memory
        self.engine.memory_enabled = True
        self.engine.task_sessions.clear()

    def _recall(self, request, subject):
        route = _route("web_search", normalized_request=request, topic=subject)
        goal = goal_intent.read(route)
        return self.engine._recall_context(route, goal)

    def test_the_reported_follow_up_recalls_the_hotels(self):
        has_context, evidence, origin = self._recall(
            "which one would you choose", "hotels in Seoul",
        )

        self.assertTrue(has_context)
        self.assertEqual(origin, "recent research")
        self.assertIn("Signiel Seoul", evidence)
        self.assertIn("hotels in Seoul", self.memory.queries)

    def test_memory_is_searched_by_subject_not_by_the_literal_words(self):
        self._recall("which one would you choose", "hotels in Seoul")

        self.assertNotIn("which one would you choose", self.memory.queries)

    def test_the_other_three_follow_ups_recall_too(self):
        for request in (
            "which was the cheapest",
            "which had the best rating",
            "compare the first two",
        ):
            with self.subTest(request=request):
                has_context, evidence, _origin = self._recall(
                    request, "hotels in Seoul",
                )
                self.assertTrue(has_context)
                self.assertIn("Hotel Entra Gangnam", evidence)

    def test_an_unrelated_follow_up_does_not_reuse_the_hotel_evidence(self):
        # "which one would you choose" about graphics cards must not be
        # answered from hotels merely because hotels are what was stored.
        has_context, _evidence, _origin = self._recall(
            "which one would you choose", "graphics cards",
        )

        self.assertFalse(has_context)

    def test_a_standalone_request_never_recalls(self):
        has_context, _evidence, _origin = self._recall(
            "what is nvidia trading at", "Nvidia",
        )

        self.assertFalse(has_context)

    def test_the_active_task_outranks_stored_research(self):
        class Item:
            def __init__(self, name):
                self.name = name

        class State:
            goal = "hotels in Seoul"
            collected_information = ("three options found",)
            collected_items = (Item("Lotte Hotel"),)

        self.engine.task_sessions.remember(State())

        _has, evidence, origin = self._recall(
            "which one would you choose", "hotels in Seoul",
        )

        self.assertEqual(origin, "active task")
        self.assertIn("Lotte Hotel", evidence)

    def test_recall_is_skipped_when_memory_is_switched_off(self):
        self.engine.memory_enabled = False

        has_context, _evidence, _origin = self._recall(
            "which one would you choose", "hotels in Seoul",
        )

        self.assertFalse(has_context)

    def test_a_failing_memory_system_never_decides_the_turn(self):
        class Broken:
            def recall_research(self, *_a, **_k):
                raise RuntimeError("index is corrupt")

        self.engine.memory_manager = Broken()

        has_context, _evidence, _origin = self._recall(
            "which one would you choose", "hotels in Seoul",
        )

        self.assertFalse(has_context)


class DecisionTests(unittest.TestCase):
    """What recall makes the interaction layer decide."""

    def test_recalled_evidence_answers_with_no_search(self):
        route = _route(
            "web_search",
            normalized_request="which one would you choose",
            topic="hotels in Seoul",
            is_follow_up=True,
            requires_external_evidence=True,
        )
        goal = goal_intent.read(route)
        decision = interaction.decide(route, goal=goal, has_usable_context=True)
        choice = caps.select(goal, decision, route=route)

        self.assertEqual(decision.need, interaction.NEED_RECALLED)
        self.assertEqual(decision.mode, interaction.ANSWER)
        self.assertTrue(decision.has_usable_context)
        self.assertEqual(choice.capability, caps.DIRECT_ANSWER)
        self.assertFalse(choice.needs_agent)
        self.assertFalse(decision.acts)

    def test_incomplete_recall_escalates_back_to_a_search(self):
        route = _route(
            "web_search",
            normalized_request="which one would you choose",
            topic="hotels in Seoul",
            is_follow_up=True,
            requires_external_evidence=True,
        )
        goal = goal_intent.read(route)
        decision = interaction.decide(route, goal=goal, has_usable_context=False)
        choice = caps.select(goal, decision, route=route)

        self.assertEqual(decision.need, interaction.NEED_FRESH)
        self.assertEqual(decision.mode, interaction.EXECUTE)
        self.assertEqual(choice.capability, caps.WEB_SEARCH)


class StorageTests(unittest.TestCase):
    """What a search leaves behind, and where."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self.memory = _RecordingMemory()
        self.engine.memory_manager = self.memory
        self.engine.memory_enabled = True

    def test_a_search_result_is_kept_with_its_subject_and_query(self):
        class Result:
            evidence = "Signiel Seoul, Hotel Entra Gangnam, And Seoul Hotel."
            queries = ("good hotels in Seoul",)

        self.engine._remember_research(
            "hotels in Seoul", "good hotels in Seoul", Result(),
        )

        self.assertEqual(len(self.memory.stored), 1)
        kept = self.memory.stored[0]
        self.assertEqual(kept["subject"], "hotels in Seoul")
        self.assertEqual(kept["query"], "good hotels in Seoul")
        self.assertIn("Signiel", kept["evidence"])
        self.assertIn("good hotels in Seoul", kept["sources"])

    def test_nothing_is_kept_when_memory_is_switched_off(self):
        class Result:
            evidence = "something"
            queries = ()

        self.engine.memory_enabled = False
        self.engine._remember_research("hotels", "hotels", Result())

        self.assertEqual(self.memory.stored, [])

    def test_a_storage_failure_never_fails_a_good_answer(self):
        class Broken:
            def remember_research(self, **_kwargs):
                raise RuntimeError("disk is full")

        class Result:
            evidence = "something"
            queries = ()

        self.engine.memory_manager = Broken()

        self.engine._remember_research("hotels", "hotels", Result())


if __name__ == "__main__":
    unittest.main()
