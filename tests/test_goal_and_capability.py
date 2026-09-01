"""Goal, then need, then capability -- in that order, and only that order.

The pipeline used to decide the tool in its first step: ``web_search`` was
the intent *and* the tool, so nothing downstream could conclude she already
knew the answer or that a page needed opening instead. These tests hold the
new order in place: what the person wants, what that requires, and only then
which ability meets it.
"""

from __future__ import annotations

import unittest

from brain import capability_selection as caps
from brain.capabilities import CAPABILITIES
from brain.deliberation import goal_intent, interaction
from brain.intent_router import IntentDecision
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


class GoalTests(unittest.TestCase):
    """Intent names what the person wants, never what Elaina would run."""

    def test_no_goal_intent_is_a_tool_name(self):
        tool_names = {capability.id for capability in CAPABILITIES}
        tool_names |= {"web_search", "browser_control", "ui_control"}

        for intent in goal_intent.INTENTS:
            with self.subTest(intent=intent):
                self.assertNotIn(intent, tool_names)

    def test_a_search_request_is_a_goal_not_a_search(self):
        goal = goal_intent.read(_route("web_search", topic="Nvidia"))

        self.assertEqual(goal.intent, goal_intent.RETRIEVE)
        self.assertEqual(goal.subject, "Nvidia")

    def test_wanting_options_reads_as_a_recommendation(self):
        goal = goal_intent.read(_route(
            "web_search", topic="hotels in Seoul", recommendation_needed=True,
        ))

        self.assertEqual(goal.intent, goal_intent.RECOMMEND)
        self.assertTrue(goal.recommendation)

    def test_wanting_them_weighed_reads_as_a_comparison(self):
        goal = goal_intent.read(_route(
            "web_search",
            normalized_request="compare some hotels in Seoul",
            topic="hotels in Seoul", recommendation_needed=True,
        ))

        self.assertEqual(goal.intent, goal_intent.COMPARE)

    def test_comparison_is_read_from_the_request_not_the_label(self):
        # The same label carries both; only the words distinguish them.
        for request, expected in (
            ("which one is better", goal_intent.COMPARE),
            ("what is the difference between them", goal_intent.COMPARE),
            ("tell me about Iceland", goal_intent.RETRIEVE),
        ):
            with self.subTest(request=request):
                goal = goal_intent.read(
                    _route("web_search", normalized_request=request)
                )
                self.assertEqual(goal.intent, expected)

    def test_making_something_is_not_operating_something(self):
        goal = goal_intent.read(_route(
            "computer_action", computer_operation="create_folder",
        ))

        self.assertEqual(goal.intent, goal_intent.CREATE)

    def test_the_everyday_labels_all_land_somewhere_sensible(self):
        for label, expected in (
            ("conversation", goal_intent.CHAT),
            ("knowledge_question", goal_intent.EXPLAIN),
            ("fact_check", goal_intent.VERIFY),
            ("computer_action", goal_intent.ACT),
            ("project_edit", goal_intent.MODIFY),
            ("screen_analysis", goal_intent.INSPECT),
            ("clarification", goal_intent.CLARIFY),
        ):
            with self.subTest(label=label):
                self.assertEqual(goal_intent.read(_route(label)).intent, expected)

    def test_an_unknown_label_becomes_conversation_not_an_action(self):
        self.assertEqual(
            goal_intent.read(_route("teleportation")).intent, goal_intent.CHAT,
        )

    def test_the_log_block_matches_the_shape_asked_for(self):
        block = goal_intent.read(_route(
            "web_search", normalized_request="compare some hotels in Seoul",
            topic="hotels in Seoul", recommendation_needed=True,
        )).log_block()

        self.assertIn("[Goal]", block)
        self.assertIn("Intent: compare", block)
        self.assertIn("Subject: hotels in Seoul", block)
        self.assertIn("Recommendation: true", block)


class CapabilityTests(unittest.TestCase):
    """The ability is chosen after the need, from the registry's own ids."""

    def test_registry_ids_are_used_verbatim(self):
        known = {capability.id for capability in CAPABILITIES}

        for value in caps.ALL_CAPABILITIES:
            if value in {caps.DIRECT_ANSWER, caps.NOTHING}:
                continue
            with self.subTest(capability=value):
                self.assertTrue(
                    value in known or value in caps.UNREGISTERED,
                    f"{value!r} is neither a registry id nor declared unregistered",
                )

    def test_nothing_runs_for_a_question_that_is_still_open(self):
        _goal, _decision, choice = _chain(_route("clarification"))

        self.assertEqual(choice.capability, caps.NOTHING)
        self.assertFalse(choice.needs_a_tool)
        self.assertFalse(choice.needs_agent)

    def test_a_known_answer_needs_no_ability_at_all(self):
        _goal, _decision, choice = _chain(_route("knowledge_question"))

        self.assertEqual(choice.capability, caps.DIRECT_ANSWER)
        self.assertFalse(choice.needs_a_tool)

    def test_what_the_session_already_found_beats_looking_again(self):
        _goal, _decision, choice = _chain(
            _route("web_search", is_follow_up=True,
                   requires_external_evidence=True),
            has_usable_context=True,
        )

        self.assertEqual(choice.capability, caps.DIRECT_ANSWER)
        self.assertIn("already found", choice.reason)
        self.assertFalse(choice.needs_agent)

    def test_current_information_selects_the_search_ability(self):
        _goal, _decision, choice = _chain(
            _route("web_search", topic="Nvidia", information_freshness="live"),
        )

        self.assertEqual(choice.capability, caps.WEB_SEARCH)
        self.assertTrue(choice.needs_agent)
        self.assertIn("Nvidia", choice.reason)

    def test_each_machine_surface_is_named_honestly(self):
        for label, expected in (
            ("computer_action", caps.UI_CONTROL),
            ("screen_analysis", caps.SCREEN_ANALYSIS),
            ("project_question", caps.PROJECT_QUESTION),
            ("project_edit", caps.PROJECT_EDIT),
            ("git_publish", caps.GIT),
            ("calendar_action", caps.CALENDAR_ACTION),
        ):
            with self.subTest(label=label):
                _goal, _decision, choice = _chain(
                    _route(label, action_requested=True)
                )
                self.assertEqual(choice.capability, expected)

    def test_a_page_operation_names_the_browser_not_the_desktop(self):
        # Browser versus desktop rides on computer_operation; the router has
        # never emitted "browser_action" as an intent, so asserting it here
        # tested an unreachable branch while every real page action was
        # being filed as Windows UI control.
        _goal, _decision, choice = _chain(
            _route(
                "computer_action",
                action_requested=True,
                computer_operation="browser_action",
            )
        )

        self.assertEqual(choice.capability, caps.BROWSER_CONTROL)

    def test_driving_the_machine_herself_dispatches_no_agent(self):
        # ui_control and browser_control are hers; the agent coordinator was
        # never on those paths and must not appear on them now.
        for label in ("computer_action", "browser_action"):
            with self.subTest(label=label):
                _goal, _decision, choice = _chain(
                    _route(label, action_requested=True)
                )
                self.assertFalse(choice.needs_agent)

    def test_the_log_block_matches_the_shape_asked_for(self):
        _goal, _decision, choice = _chain(
            _route("web_search", topic="hotels in Seoul",
                   verification_required=True),
        )
        block = choice.log_block()

        self.assertIn("[Capability]", block)
        self.assertIn("Selected:", block)
        self.assertIn("Candidates:", block)
        self.assertIn("Why:", block)
        # 4E.4 shows the working: the factors read, and every ability
        # considered with its score.
        self.assertIn("live_state_required:", block)
        self.assertIn("web_search:", block)


class OrderTests(unittest.TestCase):
    """The point of the whole change: the tool is chosen last."""

    def test_the_same_label_yields_different_capabilities(self):
        # One router label, three outcomes, decided by need rather than by
        # the label. This is what could not happen before.
        _g, _d, fresh = _chain(
            _route("web_search", information_freshness="live",
                   requires_external_evidence=True),
        )
        _g, _d, recalled = _chain(
            _route("web_search", is_follow_up=True,
                   requires_external_evidence=True),
            has_usable_context=True,
        )
        # The router said "search"; it also said the knowledge is stable.
        # The need reconciles them, and the tool name no longer wins by
        # default.
        _g, _d, known = _chain(_route("web_search", information_freshness="stable"))

        self.assertEqual(fresh.capability, caps.WEB_SEARCH)
        self.assertEqual(recalled.capability, caps.DIRECT_ANSWER)
        self.assertEqual(known.capability, caps.DIRECT_ANSWER)

    def test_a_capability_is_never_chosen_before_a_need_exists(self):
        for label in ("web_search", "computer_action", "conversation"):
            with self.subTest(label=label):
                goal, decision, choice = _chain(_route(label))
                if choice.needs_a_tool:
                    self.assertNotEqual(decision.need, interaction.NEED_NONE)


class EngineChainTests(unittest.TestCase):
    """Every turn carries the whole chain, not just a label."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_a_turn_produces_goal_decision_and_capability(self):
        routing = self.engine._route_turn("what is recursion", timings={})

        self.assertIsInstance(routing.goal_intent, goal_intent.SemanticGoal)
        self.assertIsInstance(routing.capability, caps.CapabilityChoice)
        self.assertIn(routing.goal_intent.intent, goal_intent.INTENTS)

    def test_a_plain_question_reaches_no_agent(self):
        routing = self.engine._route_turn("what is recursion", timings={})

        self.assertFalse(routing.capability.needs_agent)
        self.assertEqual(routing.capability.capability, caps.DIRECT_ANSWER)

    def test_the_decision_carries_the_goal_not_the_router_label(self):
        routing = self.engine._route_turn("what is recursion", timings={})

        self.assertEqual(routing.decision.intent, routing.goal_intent.intent)
        self.assertIn(routing.decision.intent, goal_intent.INTENTS)


class HotelFollowUpRegressionTests(unittest.TestCase):
    """Reported live: "which one would you choose?" about Seoul hotels came
    back recommending an Audi Q7.

    Four separate faults lined up, three of them introduced by the capability
    rework itself. Each is pinned here by the exact route that produced it.
    """

    def _route_from_the_report(self):
        # Verbatim from the log: the router called it conversation, marked it
        # a recommendation, and the goal layer correctly resolved "hotels".
        return _route(
            "conversation",
            normalized_request="Which one would you choose?",
            topic="hotels",
            recommendation_needed=True,
            is_follow_up=True,
        )

    def test_the_follow_up_answers_instead_of_searching(self):
        goal, decision, choice = _chain(self._route_from_the_report())

        self.assertEqual(goal.intent, goal_intent.RECOMMEND)
        self.assertEqual(goal.subject, "hotels")
        self.assertEqual(decision.need, interaction.NEED_NONE)
        self.assertEqual(decision.mode, interaction.ANSWER)
        self.assertEqual(choice.capability, caps.DIRECT_ANSWER)
        self.assertFalse(choice.needs_agent)

    def test_wanting_advice_does_not_by_itself_mean_looking_it_up(self):
        # The regression's root. "Should I use Live2D or a 3D model?" is a
        # recommendation the feature matrix marks as needing no external
        # evidence; treating RECOMMEND as inherently fresh sent every advice
        # turn to a search.
        goal, decision, choice = _chain(_route(
            "conversation",
            normalized_request="should I use Live2D or a 3D model",
            recommendation_needed=True,
        ))

        self.assertEqual(decision.need, interaction.NEED_NONE)
        self.assertEqual(choice.capability, caps.DIRECT_ANSWER)

    def test_a_recommendation_that_does_need_evidence_still_searches(self):
        # The other half: the router's evidence flags still decide.
        _goal, decision, choice = _chain(_route(
            "conversation",
            normalized_request="good hotels in Seoul",
            topic="hotels in Seoul",
            recommendation_needed=True,
            requires_external_evidence=True,
        ))

        self.assertEqual(decision.mode, interaction.EXECUTE)
        self.assertEqual(choice.capability, caps.WEB_SEARCH)

    def test_the_agent_follows_the_capability_not_the_label(self):
        # A conversation-labelled turn whose capability is web_search was
        # handed to the Conversation Agent, which then searched the raw
        # utterance. When they disagree the capability is the later and
        # better-informed decision, so it wins.
        _goal, _decision, choice = _chain(_route(
            "conversation",
            normalized_request="good hotels in Seoul",
            recommendation_needed=True,
            requires_external_evidence=True,
        ))

        self.assertEqual(
            choice.dispatch_label("conversation"), "web_search",
        )

    def test_a_label_that_already_agrees_is_left_alone(self):
        _goal, _decision, choice = _chain(_route(
            "fact_check", verification_required=True,
        ))

        self.assertEqual(choice.dispatch_label("fact_check"), "fact_check")

    def test_a_machine_label_keeps_its_own_more_specific_agent(self):
        _goal, _decision, choice = _chain(
            _route("git_publish", action_requested=True)
        )

        self.assertEqual(choice.dispatch_label("git_publish"), "git_publish")


class SearchSubjectTests(unittest.TestCase):
    """What gets searched when the words themselves are not searchable."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_a_bare_follow_up_is_searched_by_its_subject(self):
        route = _route(
            "web_search",
            normalized_request="Which one would you choose?",
            topic="hotels in Seoul",
            is_follow_up=True,
        )
        goal = goal_intent.read(route)

        query = self.engine._search_subject(route, goal)

        self.assertIn("hotels in Seoul", query)
        self.assertIn("Which one", query)

    def test_a_self_contained_request_is_searched_as_asked(self):
        route = _route(
            "web_search",
            normalized_request="what is nvidia trading at",
            topic="Nvidia",
            is_follow_up=False,
        )
        goal = goal_intent.read(route)

        self.assertEqual(
            self.engine._search_subject(route, goal),
            "what is nvidia trading at",
        )

    def test_no_subject_falls_back_to_the_request(self):
        route = _route("web_search", normalized_request="tell me about it",
                       is_follow_up=True)
        goal = goal_intent.read(route)

        self.assertEqual(
            self.engine._search_subject(route, goal), "tell me about it",
        )


if __name__ == "__main__":
    unittest.main()
