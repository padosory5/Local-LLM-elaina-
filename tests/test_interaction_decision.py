"""What should happen about a request, decided once.

The layer exists because ``route.intent`` was consulted in thirty-four
separate places and each one re-derived whether the turn was an answer, a
search, or an action. These tests are the contract that replaces that: given
what routing worked out, exactly one conclusion, and a safe one when the
request is unrecognised.
"""

from __future__ import annotations

import unittest

from brain.deliberation.interaction import (
    ANSWER,
    ASK_PERMISSION,
    CLARIFY,
    CONSEQUENTIAL,
    EXECUTE,
    INFORMATIONAL,
    MODES,
    NEED_FRESH,
    NEED_MACHINE,
    NEED_NONE,
    NEED_RECALLED,
    NEED_VERIFIED,
    NEEDS,
    RECOMMEND,
    VISIBLE,
    InteractionDecision,
    decide,
    permission_level_for,
)
from brain.deliberation import goal_intent
from brain.intent_router import IntentDecision
from tests.turn_harness import build_engine


def _route(intent: str, **fields) -> IntentDecision:
    fields.setdefault("confidence", 0.9)
    fields.setdefault("normalized_request", "the request")
    return IntentDecision(intent=intent, **fields)


class DirectQuestionTests(unittest.TestCase):
    """Criterion 10: direct questions. No tool, no offer, no permission."""

    def test_a_stable_knowledge_question_is_answered(self):
        decision = decide(_route("knowledge_question"))

        self.assertEqual(decision.mode, ANSWER)
        self.assertEqual(decision.need, NEED_NONE)
        self.assertTrue(decision.can_answer_directly)
        self.assertFalse(decision.action_would_help)

    def test_the_everyday_direct_intents_all_answer(self):
        for intent in ("conversation", "time_question", "calculation"):
            with self.subTest(intent=intent):
                self.assertEqual(decide(_route(intent)).mode, ANSWER)


class CurrentInformationTests(unittest.TestCase):
    """Criterion 10: current information, and no pointless permission prompt."""

    def test_freshness_alone_is_enough_to_look_it_up(self):
        decision = decide(_route("web_search", information_freshness="live"))

        self.assertEqual(decision.need, NEED_FRESH)
        self.assertEqual(decision.mode, EXECUTE)

    def test_a_verified_source_is_distinguished_from_a_fresh_one(self):
        decision = decide(_route("web_search", verification_required=True))

        self.assertEqual(decision.need, NEED_VERIFIED)
        self.assertTrue(decision.needs_external_information)

    def test_looking_something_up_never_asks_first(self):
        # "What's the weather tomorrow?" must not become "would you like me
        # to search the weather?" -- the brief calls this out by name.
        for fields in (
            {"information_freshness": "live"},
            {"requires_external_evidence": True},
            {"verification_required": True},
        ):
            with self.subTest(**fields):
                decision = decide(_route("web_search", **fields))
                self.assertEqual(decision.mode, EXECUTE)
                self.assertEqual(decision.permission_level, INFORMATIONAL)


class FollowUpTests(unittest.TestCase):
    """Criterion 7: existing results prevent a redundant call."""

    def test_a_follow_up_the_session_can_answer_does_not_search_again(self):
        decision = decide(
            _route("web_search", is_follow_up=True,
                   requires_external_evidence=True),
            has_usable_context=True,
        )

        self.assertEqual(decision.need, NEED_RECALLED)
        self.assertEqual(decision.mode, ANSWER)
        self.assertTrue(decision.reuses_existing_results)
        self.assertFalse(decision.acts)

    def test_context_alone_does_not_suppress_a_fresh_request(self):
        # Holding hotel results must not stop the next, unrelated search.
        decision = decide(
            _route("web_search", is_follow_up=False,
                   requires_external_evidence=True),
            has_usable_context=True,
        )

        self.assertEqual(decision.mode, EXECUTE)

    def test_a_follow_up_with_nothing_stored_still_searches(self):
        decision = decide(
            _route("web_search", is_follow_up=True,
                   requires_external_evidence=True),
            has_usable_context=False,
        )

        self.assertEqual(decision.mode, EXECUTE)


class ActionRequestTests(unittest.TestCase):
    """Criterion 10: action requests, and criterion 3's four outcomes."""

    def test_an_explicit_action_executes(self):
        decision = decide(_route(
            "computer_action", action_requested=True,
            computer_operation="open_app",
        ))

        self.assertEqual(decision.need, NEED_MACHINE)
        self.assertEqual(decision.mode, EXECUTE)

    def test_a_destructive_operation_asks_first(self):
        for operation in ("delete_file", "delete_folder", "force_quit_app"):
            with self.subTest(operation=operation):
                decision = decide(_route(
                    "computer_action", action_requested=True,
                    computer_operation=operation,
                ))
                self.assertEqual(decision.mode, ASK_PERMISSION)
                self.assertEqual(decision.permission_level, CONSEQUENTIAL)
                self.assertTrue(decision.asks)

    def test_an_agent_intent_still_dispatches_despite_being_level_three(self):
        # project_edit and git_publish are consequential, but their approval
        # wall lives downstream: dispatching them is *how* the user gets
        # asked. Refusing to dispatch would ask before asking, and question a
        # request the user already made outright.
        for intent in ("project_edit", "git_commit", "git_publish",
                       "calendar_action", "agent_create"):
            with self.subTest(intent=intent):
                decision = decide(_route(intent, action_requested=True))
                self.assertEqual(decision.mode, EXECUTE)
                self.assertEqual(decision.permission_level, CONSEQUENTIAL)

    def test_reading_the_project_needs_a_tool_but_little_friction(self):
        # The axis that got conflated once already: needing machinery and
        # needing permission are different questions.
        decision = decide(_route("project_question", action_requested=True))

        self.assertEqual(decision.need, NEED_MACHINE)
        self.assertEqual(decision.mode, EXECUTE)
        self.assertEqual(decision.permission_level, INFORMATIONAL)


class RecommendationTests(unittest.TestCase):
    """Criterion 3: recommend, rather than act on something nobody asked for."""

    def test_an_unrequested_action_is_offered_not_taken(self):
        decision = decide(_route("computer_action", action_requested=False))

        self.assertEqual(decision.mode, RECOMMEND)
        self.assertTrue(decision.action_would_help)
        self.assertFalse(decision.acts)

    def test_an_explicit_request_is_never_downgraded_to_an_offer(self):
        decision = decide(_route("computer_action", action_requested=True,
                                 computer_operation="open_app"))

        self.assertEqual(decision.mode, EXECUTE)


class AmbiguityTests(unittest.TestCase):
    """Criteria 8 and 9: confidence carried, unknown requests fail safe."""

    def test_an_open_question_clarifies_before_anything_else(self):
        decision = decide(_route("clarification", action_requested=True,
                                 computer_operation="type_text"))

        self.assertEqual(decision.mode, CLARIFY)
        self.assertTrue(decision.asks)
        self.assertFalse(decision.acts)

    def test_an_unknown_intent_answers_rather_than_inventing_an_action(self):
        for intent in ("teleportation", "", "  ", "wat"):
            with self.subTest(intent=intent):
                decision = decide(_route(intent))
                self.assertEqual(decision.mode, ANSWER)
                self.assertFalse(decision.acts)

    def test_an_unknown_intent_is_not_treated_as_friction_free(self):
        self.assertEqual(permission_level_for("teleportation"), VISIBLE)

    def test_confidence_is_carried_through(self):
        self.assertAlmostEqual(
            decide(_route("conversation", confidence=0.31)).confidence, 0.31,
        )

    def test_a_missing_field_does_not_raise(self):
        class Bare:
            intent = "conversation"

        decision = decide(Bare())

        self.assertEqual(decision.mode, ANSWER)
        self.assertEqual(decision.confidence, 1.0)


class ShapeTests(unittest.TestCase):
    """Criterion 4: the decision is structured, not prose."""

    def test_every_mode_and_need_is_a_declared_value(self):
        for intent in (
            "conversation", "knowledge_question", "web_search",
            "computer_action", "project_edit", "clarification", "nonsense",
        ):
            for requested in (True, False):
                for context in (True, False):
                    decision = decide(
                        _route(intent, action_requested=requested,
                               is_follow_up=True),
                        has_usable_context=context,
                    )
                    with self.subTest(intent=intent, requested=requested):
                        self.assertIn(decision.mode, MODES)
                        self.assertIn(decision.need, NEEDS)
                        self.assertIn(
                            decision.permission_level,
                            (INFORMATIONAL, VISIBLE, CONSEQUENTIAL),
                        )

    def test_acts_and_asks_are_never_both_true(self):
        for intent in ("computer_action", "web_search", "clarification",
                       "project_edit", "conversation"):
            for requested in (True, False):
                decision = decide(
                    _route(intent, action_requested=requested,
                           computer_operation="delete_file" if requested else ""),
                )
                with self.subTest(intent=intent, requested=requested):
                    self.assertFalse(decision.acts and decision.asks)

    def test_the_default_decision_is_inert(self):
        decision = InteractionDecision()

        self.assertEqual(decision.mode, ANSWER)
        self.assertFalse(decision.acts)
        self.assertFalse(decision.asks)

    def test_the_surface_is_declared_but_not_yet_chosen(self):
        # 4E.5 owns this field. Populated here so that phase consolidates
        # rather than re-deriving, which is the mistake this module undoes.
        self.assertEqual(decide(_route("web_search")).result_surface, "none")

    def test_every_decision_explains_itself(self):
        for intent in ("conversation", "web_search", "computer_action",
                       "clarification", "nonsense"):
            with self.subTest(intent=intent):
                self.assertTrue(decide(_route(intent)).reason)


class LogTests(unittest.TestCase):
    """The debugging view from the brief -- console only."""

    def test_the_block_names_the_decision_and_its_reason(self):
        block = decide(
            _route("web_search", information_freshness="live")
        ).log_block()

        self.assertIn("[Interaction]", block)
        for label in ("Need:", "Decision:", "Permission:", "Confidence:", "Why:"):
            self.assertIn(label, block)
        self.assertIn("execute", block)

    def test_the_block_never_prints_a_tool_name_as_an_intent(self):
        # The [Goal] block above it names the intent. Repeating the router's
        # label here was the last place a tool name was presented as one.
        block = decide(
            _route("web_search", information_freshness="live"),
            goal=goal_intent.read(_route("web_search", information_freshness="live")),
        ).log_block()

        self.assertNotIn("Intent:", block)
        self.assertNotIn("web_search", block)


class EngineIntegrationTests(unittest.TestCase):
    """Criterion 11: existing tool calls still work through the new layer."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine(routes=_ROUTES)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_every_turn_produces_a_decision(self):
        routing = self.engine._route_turn("what is recursion", timings={})

        self.assertIsInstance(routing.decision, InteractionDecision)
        self.assertIn(routing.decision.mode, MODES)

    def test_a_knowledge_question_decides_to_answer(self):
        routing = self.engine._route_turn("what is recursion", timings={})

        self.assertEqual(routing.decision.mode, ANSWER)
        self.assertFalse(routing.decision.acts)

    def test_a_current_question_decides_to_execute(self):
        routing = self.engine._route_turn(
            "what is nvidia trading at right now", timings={},
        )

        self.assertTrue(routing.decision.acts)
        self.assertTrue(routing.decision.needs_external_information)

    def test_the_session_lookup_never_decides_the_turn_by_failing(self):
        class Broken:
            def context_for_followup(self, _request):
                raise RuntimeError("session store is down")

        original = self.engine.task_sessions
        self.engine.task_sessions = Broken()
        try:
            has_context, _evidence, _origin = self.engine._recall_context(
                _route("web_search", normalized_request="hotels"),
                goal_intent.read(_route("web_search", topic="hotels")),
            )
            self.assertFalse(has_context)
        finally:
            self.engine.task_sessions = original

    def test_a_shortlist_in_the_session_is_reused_instead_of_researched(self):
        # The brief's headline case: "find me hotels" then "which one would
        # you choose". The store is fed by the task planner, which is what
        # produces a shortlist there is anything to choose *between* -- so
        # this seeds it the way that path does rather than mocking the gate.
        class Item:
            def __init__(self, name):
                self.name = name

        class State:
            goal = "hotels in Seoul"
            collected_information = ("three options found",)
            collected_items = (Item("Hotel A"), Item("Hotel B"), Item("Hotel C"))

        self.engine.task_sessions.remember(State())
        try:
            has_context, evidence, origin = self.engine._recall_context(
                _route("web_search",
                       normalized_request="which one would you choose"),
                goal_intent.read(_route("web_search", topic="hotels in Seoul")),
            )
            self.assertTrue(has_context)
            self.assertIn("Hotel A", evidence)
            self.assertEqual(origin, "active task")
            decision = decide(
                _route("web_search", is_follow_up=True,
                       requires_external_evidence=True,
                       normalized_request="which one would you choose"),
                has_usable_context=True,
            )
            self.assertEqual(decision.mode, ANSWER)
            self.assertFalse(decision.acts)
        finally:
            self.engine.task_sessions.clear()

    def test_a_new_request_is_not_captured_by_a_stored_shortlist(self):
        # Only a genuine back-reference reuses. A fresh question after a
        # shortlist must still run, or the session would swallow the next
        # unrelated search.
        class Item:
            def __init__(self, name):
                self.name = name

        class State:
            goal = "hotels in Seoul"
            collected_information = ()
            collected_items = (Item("Hotel A"),)

        self.engine.task_sessions.remember(State())
        try:
            has_context, _evidence, _origin = self.engine._recall_context(
                _route("web_search",
                       normalized_request="what is nvidia trading at"),
                goal_intent.read(_route("web_search", topic="Nvidia")),
            )
            self.assertFalse(has_context)
        finally:
            self.engine.task_sessions.clear()

    def test_a_locked_response_is_never_treated_as_recalled_context(self):
        # A locked response is already the answer; consulting the session
        # for one would be answering a question nobody asked.
        has_context, _evidence, _origin = self.engine._recall_context(
            _route("web_search", normalized_request="hotels"),
            goal_intent.read(_route("web_search", topic="hotels")),
            locked_response="I can't do that yet.",
        )
        self.assertFalse(has_context)


_ROUTES = {
    "what is recursion": {
        "intent": "knowledge_question",
        "confidence": 0.95,
        "normalized_request": "what is recursion",
        "reason": "scripted",
        "information_freshness": "stable",
    },
    "nvidia trading": {
        "intent": "web_search",
        "confidence": 0.95,
        "normalized_request": "nvidia share price now",
        "reason": "scripted",
        "information_freshness": "live",
        "requires_external_evidence": True,
        "verification_required": True,
    },
}


if __name__ == "__main__":
    unittest.main()
