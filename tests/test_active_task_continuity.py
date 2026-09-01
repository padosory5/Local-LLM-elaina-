"""Regression coverage for one task surviving every conversational handoff."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from brain import candidate_fit
from brain import recommendation_state as rs
from brain.chat_engine import ChatEngine
from brain.prompt_builder import PromptBuilder
from brain.response_quality import ResponseQualityGuard
from brain.task_session import TaskSessionStore
from tests.turn_harness import build_engine


def _route(intent: str, normalized: str, topic: str = "", **extra):
    decision = {
        "intent": intent,
        "confidence": 0.95,
        "normalized_request": normalized,
        "reason": "active-task continuity regression",
        "computer_operation": "none",
        "action_target": "",
        "speech_act": "information_request",
        "action_requested": False,
        "topic": topic,
    }
    decision.update(extra)
    return decision


ROUTES = {
    "going to uw": _route(
        "conversation",
        "I am going to University of Washington in Seattle",
        "University of Washington",
    ),
    "need rent near my school": _route(
        "web_search",
        "Find rentals near my school",
        "housing",
        recommendation_needed=True,
        requires_external_evidence=True,
        search_query="rent near my school",
    ),
    "why aren't": _route(
        "conversation",
        "Why are you not showing me anything",
        "housing complaint",
    ),
}


class ActiveTaskContinuityTests(unittest.TestCase):

    def setUp(self):
        self.engine = build_engine(ROUTES)

    def tearDown(self):
        self.engine.close()

    def route(self, said: str):
        return self.engine._route_turn(said, timings={})

    def test_exact_rental_sequence_keeps_one_problem_and_resumes_lookup(self):
        self.route("I am going to UW in Seattle.")
        first = self.route("I need rent near my school. Show me some places.")
        problem = self.engine.task_sessions.active_recommendation()
        task_id = problem.id

        self.assertEqual(first.locked_response, "What type of housing did you have in mind?")
        self.assertEqual(problem.anchor, "University of Washington")
        self.assertEqual(problem.location, "Seattle")
        self.assertTrue(problem.lookup_requested)

        typed = self.route("Studio.")
        self.assertIn("budget", typed.locked_response.casefold())
        final = self.route("About $1000 to $1300.")
        problem = self.engine.task_sessions.active_recommendation()

        self.assertEqual(problem.id, task_id)
        self.assertEqual(problem.values(rs.HOUSING_TYPE), ("studio",))
        self.assertEqual(problem.values(rs.BUDGET), ("$1000 to $1300",))
        self.assertIsNone(self.engine.clarification.peek())
        self.assertEqual(final.route.intent, "web_search")
        self.assertFalse(final.locked_response)
        self.assertEqual(
            problem.search_query(),
            "studio apartments near University of Washington in Seattle $1000 to $1300",
        )

        self.route("Yeah.")
        problem = self.engine.task_sessions.active_recommendation()
        self.assertEqual(problem.id, task_id)
        self.assertNotIn("yeah", " ".join(problem.values(rs.ATTRIBUTE)))

        complaint = self.route("Why aren't you showing me anything?")
        self.assertEqual(complaint.route.intent, "web_search")
        self.assertEqual(complaint.capability.capability, "web_search")
        self.assertEqual(complaint.route.computer_operation, "")
        self.assertEqual(complaint.route.search_query, problem.search_query())
        self.assertEqual(complaint.route.action_target, problem.search_query())
        self.assertEqual(self.engine.task_sessions.active_recommendation().id, task_id)
        self.assertNotIn("why", self.engine._resolved_search_query(
            complaint.route, complaint.goal_intent,
        ).casefold())

    def test_acknowledgement_cannot_answer_an_owned_dimension(self):
        self.route("I am going to UW in Seattle.")
        self.route("I need rent near my school. Show me some places.")
        pending = self.engine.clarification.peek()

        reply = self.route("Yeah, sure.")

        self.assertEqual(reply.locked_response, pending.question)
        self.assertEqual(self.engine.clarification.peek().task_id, pending.task_id)
        self.assertFalse(self.engine.task_sessions.active_recommendation().constraints)

    def test_capability_acceptance_reuses_the_active_task_payload(self):
        self.route("I am going to UW in Seattle.")
        self.route("I need rent near my school. Show me some places.")
        self.route("Studio.")
        result = self.route("About $1000 to $1300.")
        problem = self.engine.task_sessions.active_recommendation()
        self.engine.capability_offer.offer(
            capability_id="browser_control",
            goal="Check the current price for: University of Washington",
            offer_text="Want me to look it up?",
            task_id=problem.id,
            task_query=problem.search_query(),
        )

        resumed = self.route("Yeah, sure.")

        self.assertEqual(resumed.route.intent, "web_search")
        self.assertEqual(resumed.route.computer_operation, "")
        self.assertEqual(resumed.route.search_query, problem.search_query())
        self.assertEqual(self.engine.task_sessions.active_recommendation().id, problem.id)
        self.assertNotIn("current price", resumed.route.normalized_request.casefold())

    def test_stale_clarification_cannot_consume_a_new_problem_reply(self):
        self.route("I am going to UW in Seattle.")
        self.route("I need rent near my school. Show me some places.")
        old = self.engine.clarification.peek()
        self.engine.task_sessions.clear_recommendation()
        replacement = self.engine.task_sessions.note_recommendation_turn(
            "I want a guitar.", subject="guitar",
        )

        self.route("Electric.")

        self.assertNotEqual(old.task_id, replacement.id)
        self.assertNotIn(
            "electric",
            self.engine.task_sessions.active_recommendation().values(rs.HOUSING_TYPE),
        )
        self.assertEqual(self.engine.task_sessions.active_recommendation().anchor, "")
        self.assertEqual(self.engine.task_sessions.active_recommendation().location, "")


class ConstraintQualityTests(unittest.TestCase):

    def test_housing_type_and_full_budget_range_are_typed(self):
        found = rs.read_constraints("like a studio, around $1000 to $1300")
        self.assertIn((rs.HOUSING_TYPE, "studio"), {(s.name, s.value) for s in found})
        self.assertIn((rs.BUDGET, "$1000 to $1300"), {(s.name, s.value) for s in found})

    def test_affirmations_are_never_short_reply_constraints(self):
        for said in ("yeah", "ya", "sure", "yeah, sure", "go ahead", "do that"):
            with self.subTest(said=said):
                self.assertEqual(rs.read_short_reply(said), ())


class RentalAcquisitionContinuityTests(unittest.TestCase):

    def test_named_listing_gets_one_bounded_rent_verification_search(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "I need rent near my school. Show me some places.",
            subject="housing", location="Seattle",
            anchor="University of Washington",
        )
        store.note_recommendation_turn("Studio.", subject="housing")
        problem = store.note_recommendation_turn(
            "About $1000 to $1300.", subject="housing",
        )

        engine = ChatEngine.__new__(ChatEngine)
        engine.task_sessions = store
        engine.client = None
        engine.model = "test"
        engine._sources_for = lambda *args, **kwargs: ()
        engine._surface_hosts_for = lambda *args, **kwargs: ()
        calls = []

        lead = [{
            "title": "Sun Light - 4733 21st Ave NE Seattle WA",
            "summary": "Apartment community in Seattle; explore availability",
        }]
        verified = [{
            "title": "Sun Light - Seattle, WA",
            "summary": "Studio rental starting at $995/month in Seattle",
        }]

        def candidates(query, active, shape, *, preferred_source=""):
            calls.append(query)
            batch = verified if "Sun Light" in query else lead
            return candidate_fit.evaluate(batch, active, shape=shape)

        engine._candidates_for = candidates

        result = engine._research_for_recommendation(
            problem.search_query(), resolution=SimpleNamespace(choice=""),
        )

        self.assertIn("Sun Light", result.evidence)
        self.assertIn("[FITS]", result.evidence)
        self.assertTrue(any(
            "Sun Light" in q and "studio rent price" in q for q in calls
        ))

    def test_rental_search_query_is_listing_shaped_without_currency_syntax(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "I need rent near my school. Show me some places.",
            subject="housing", location="Seattle",
            anchor="University of Washington",
        )
        store.note_recommendation_turn("Studio.", subject="housing")
        problem = store.note_recommendation_turn(
            "About $1000 to $1300.", subject="housing",
        )
        engine = ChatEngine.__new__(ChatEngine)

        query = engine._shaped_query(problem.search_query(), problem)

        self.assertTrue(query.startswith("Seattle studio apartment 1000 to 1300"))
        self.assertIn("monthly rent address available", query)
        self.assertIn("near University of Washington", query)
        self.assertNotIn("$", query)


class ConversationProgressionTests(unittest.TestCase):

    HISTORY = [
        {"role": "user", "content": "I had pho."},
        {
            "role": "assistant",
            "content": "Pho sounds good. Did you have it with grilled meat?",
        },
    ]

    def test_uncertainty_answer_cannot_repeat_the_same_question(self):
        self.assertTrue(ResponseQualityGuard.should_retry(
            self.HISTORY[-1]["content"],
            "I don't know what meat was in it.",
            self.HISTORY,
        ))

    def test_plain_no_cannot_be_rewritten_as_no_thanks(self):
        self.assertTrue(ResponseQualityGuard.should_retry(
            "Got it -- no thanks. What's next?", "No.", self.HISTORY,
        ))

    def test_prompt_treats_short_reply_as_answer_without_priming_no_thanks(self):
        prompt = PromptBuilder().build("", "No.")
        self.assertIn("treat a short current reply as its answer", prompt)
        self.assertNotIn("no thanks", prompt.casefold())


if __name__ == "__main__":
    unittest.main()
