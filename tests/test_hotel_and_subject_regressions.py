"""Two live bugs from 4E.2 testing, pinned so they cannot come back.

**"Find me" is not an instruction to operate a machine.** "Find me some good
hotels in Seoul" became ``Need: machine_action`` / ``Capability:
task_planning``, and the discovery policy then demanded check-in dates for a
question about which hotels are good. The router labels such requests
``task_action``, which means "this takes several steps" -- a statement about
delivery, not about whether a machine is involved.

**A resolved subject outranks embedding similarity.** "What should I eat for
dinner?" then "Which one would you choose?" answered about graphics cards,
because a GPU conversation earlier in the session was the nearest neighbour
of a sentence that means nothing on its own.
"""

from __future__ import annotations

import unittest

from brain import capability_selection as caps
from brain.deliberation import goal_intent, interaction
from brain.response_messages import build_personality_messages
from brain.intent_router import IntentDecision
from brain.task_discovery_policy import TaskDiscoveryPolicy
from tests.turn_harness import build_engine


def _route(label: str, **fields) -> IntentDecision:
    fields.setdefault("confidence", 0.95)
    fields.setdefault("normalized_request", "the request")
    return IntentDecision(intent=label, **fields)


def _chain(route, *, has_usable_context: bool = False):
    goal = goal_intent.read(route)
    decision = interaction.decide(
        route, goal=goal, has_usable_context=has_usable_context,
    )
    return goal, decision, caps.select(goal, decision, route=route)


def _hotels(request: str, **fields):
    fields.setdefault("recommendation_needed", True)
    return _route(
        "task_action",
        normalized_request=request,
        topic="hotels in Seoul",
        **fields,
    )


class HotelDiscoveryTests(unittest.TestCase):
    """A, B, C, D from the report."""

    def test_a_finding_hotels_is_information_not_a_machine_action(self):
        _goal, decision, choice = _chain(
            _hotels("find me some good hotels in seoul",
                    requires_external_evidence=True)
        )

        self.assertNotEqual(decision.need, interaction.NEED_MACHINE)
        self.assertNotEqual(choice.capability, caps.TASK_PLANNING)
        self.assertEqual(choice.capability, caps.WEB_SEARCH)

    def test_a_no_dates_are_demanded_for_finding_good_hotels(self):
        self.assertEqual(
            TaskDiscoveryPolicy.missing_required_preferences(
                "hotel", {}, "find me some good hotels in seoul",
            ),
            (),
        )

    def test_b_famous_hotels_need_neither_dates_nor_a_machine(self):
        for request in (
            "what are some famous hotels in seoul",
            "just tell me the hotels that are famous in seoul",
            "what are the best luxury hotels in seoul",
        ):
            with self.subTest(request=request):
                _goal, decision, choice = _chain(_hotels(request))

                self.assertNotEqual(decision.need, interaction.NEED_MACHINE)
                self.assertIn(
                    choice.capability, (caps.DIRECT_ANSWER, caps.WEB_SEARCH),
                )
                self.assertEqual(
                    TaskDiscoveryPolicy.missing_required_preferences(
                        "hotel", {}, request,
                    ),
                    (),
                )

    def test_b_asking_for_general_information_closes_the_source_offer(self):
        # "Just tell me the hotels that are famous" rejects the live-rate
        # path outright; offering it again reopens a loop just exited.
        self.assertIsNone(TaskDiscoveryPolicy.advise(
            "just tell me the hotels that are famous in seoul",
            browser_ready=True,
        ))

    def test_c_dates_are_required_when_they_change_the_answer(self):
        for request in (
            "find me hotels available September 15-18",
            "book me a hotel in seoul",
            "what are hotel prices in seoul tonight",
            "which hotels have rooms for two nights",
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    TaskDiscoveryPolicy.missing_required_preferences(
                        "hotel", {}, request,
                    ),
                    ("dates",),
                )

    def test_c_supplied_dates_are_never_asked_for_again(self):
        self.assertEqual(
            TaskDiscoveryPolicy.missing_required_preferences(
                "hotel", {"dates": "2026-09-15 to 2026-09-18"},
                "find me hotels available September 15-18",
            ),
            (),
        )

    def test_d_naming_a_site_to_operate_is_a_machine_action(self):
        for request in (
            "open booking.com and find me hotels in seoul",
            "use 여기어때 to find rooms",
            "go to a hotel website and compare these",
            "pull up the hotel page",
        ):
            with self.subTest(request=request):
                _goal, decision, choice = _chain(
                    _hotels(request, action_requested=True)
                )

                self.assertEqual(decision.need, interaction.NEED_MACHINE)
                self.assertTrue(choice.needs_a_tool)

    def test_the_surface_test_does_not_fire_on_plain_requests(self):
        for request in (
            "find me some good hotels in seoul",
            "what should I eat for dinner",
            "what are famous hotels in seoul",
        ):
            with self.subTest(request=request):
                self.assertFalse(goal_intent.names_a_surface(request))


class DinnerFollowUpTests(unittest.TestCase):
    """E and F: a resolved subject beats an old conversation."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    class _Memory:
        def __init__(self, content):
            self.content = content

    def test_e_the_follow_up_stays_on_dinner(self):
        route = _route(
            "conversation",
            normalized_request="which one would you choose",
            topic="dinner",
            recommendation_needed=True,
        )
        goal, decision, choice = _chain(route)

        self.assertEqual(goal.subject, "dinner")
        self.assertEqual(decision.mode, interaction.ANSWER)
        self.assertEqual(choice.capability, caps.DIRECT_ANSWER)

    def test_e_memory_is_searched_by_the_resolved_subject(self):
        # The router often leaves is_follow_up unset on exactly these turns,
        # which is why the raw phrase used to reach FAISS.
        route = _route(
            "conversation",
            normalized_request="which one would you choose",
            topic="dinner",
        )
        goal = goal_intent.read(route)

        query = self.engine._search_subject(route, goal)

        self.assertIn("dinner", query)

    def test_f_an_unrelated_memory_cannot_override_the_current_subject(self):
        route = _route("conversation", normalized_request="which one would "
                       "you choose", topic="dinner")
        goal = goal_intent.read(route)
        memories = [
            self._Memory("The user was comparing the NVIDIA RTX 4090 and "
                         "the AMD Radeon graphics cards."),
            self._Memory("The user asked about dinner options in Seoul."),
        ]

        kept = self.engine._memories_about(memories, goal)

        self.assertEqual(len(kept), 1)
        self.assertIn("dinner", kept[0].content)

    def test_f_dropping_everything_is_allowed(self):
        # Nothing relevant means the turn is answered from the conversation,
        # which already holds the options being chosen between.
        route = _route("conversation", normalized_request="which one would "
                       "you choose", topic="dinner")
        goal = goal_intent.read(route)
        memories = [self._Memory("The user prefers the RTX 4090.")]

        self.assertEqual(self.engine._memories_about(memories, goal), [])

    def test_memories_survive_when_there_is_no_resolved_subject(self):
        route = _route("conversation", normalized_request="how has my week been")
        goal = goal_intent.SemanticGoal(intent=goal_intent.CHAT, subject="")
        memories = [self._Memory("anything at all")]

        self.assertEqual(self.engine._memories_about(memories, goal), memories)


class SubjectReachesThePromptTests(unittest.TestCase):
    """The fix that actually mattered, and the three that did not.

    The subject was resolved correctly all along and used for retrieval --
    but nothing *said* it in the prompt, so the model chose between the
    topics in history by itself and picked the one that came as a list.
    Putting it in one of the several context strings a turn assembles was
    not enough: ``_build_factual_messages`` replaces the message list
    wholesale on every evidence-backed turn, dropping it. It travels with
    the prompt builder now, which every path goes through.
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_the_subject_is_stated_in_the_prompt(self):
        messages = build_personality_messages(
            system_prompt="P",
            history=[],
            user_input="which one would you choose",
            followup_subject="dinner",
        )
        prompt = messages[-1]["content"]

        self.assertIn("WHAT THIS MESSAGE IS ABOUT", prompt)
        self.assertIn("dinner", prompt)

    def test_it_survives_an_evidence_backed_turn(self):
        # The path that dropped it before.
        messages = self.engine._build_factual_messages(
            "which one would you choose",
            "Some retrieved evidence.",
            followup_subject="dinner",
        )

        self.assertIn("WHAT THIS MESSAGE IS ABOUT", messages[-1]["content"])

    def test_a_self_contained_request_is_not_narrowed(self):
        route = _route(
            "conversation",
            normalized_request="what should I eat for dinner",
            topic="dinner",
        )
        goal = goal_intent.read(route)

        self.assertEqual(self.engine._followup_subject_for(route, goal), "")

    def test_a_bare_follow_up_is(self):
        route = _route(
            "conversation",
            normalized_request="which one would you choose",
            topic="dinner",
        )
        goal = goal_intent.read(route)

        self.assertEqual(
            self.engine._followup_subject_for(route, goal), "dinner",
        )

    def test_nothing_is_stated_when_no_subject_was_resolved(self):
        messages = build_personality_messages(
            system_prompt="P", history=[], user_input="hello",
        )

        self.assertNotIn("WHAT THIS MESSAGE IS ABOUT", messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
