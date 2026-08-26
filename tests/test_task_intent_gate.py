import json
import unittest

from brain.task_intent_gate import TaskIntentGate


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def chat(self, **_kwargs):
        self.calls += 1
        return {"message": {"content": json.dumps(self.payload)}}


class MustNotRunClient:
    def chat(self, **_kwargs):
        raise AssertionError(
            "The escalation LLM call should not run when the regex "
            "heuristic doesn't suspect a compound goal."
        )


class TaskIntentGateTests(unittest.TestCase):
    def test_two_different_capabilities_escalates_to_the_llm_check(self):
        client = FakeClient({
            "is_multistep_task": True,
            "confidence": 0.9,
            "reason": "Opens a native app, then separately searches the web.",
        })
        gate = TaskIntentGate(client=client, model="qwen3:8b")

        decision = gate.check("open Whale and search for UW tuition")

        self.assertEqual(client.calls, 1)
        self.assertTrue(decision.is_multistep)

    def test_same_capability_on_both_sides_never_escalates(self):
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("open Spotify and play Dynamite")

        self.assertFalse(decision.is_multistep)

    def test_no_conjunction_never_escalates(self):
        # A subject no discovery category claims, so this exercises the
        # cross-capability heuristic itself rather than the deterministic
        # recommendation short-circuit ahead of it.
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("search for the height of Mount Hallasan")

        self.assertFalse(decision.is_multistep)

    def test_a_plural_category_request_still_reaches_the_discovery_offer(self):
        # "hotels in Guam" is how people actually type it; matching only
        # the singular sent it to a one-shot search with no chance of the
        # local-market offer.
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("search for hotels in Guam")

        self.assertTrue(decision.is_multistep)
        self.assertEqual(decision.confidence, 1.0)

    def test_heuristic_hit_but_llm_disagrees_stays_single_step(self):
        client = FakeClient({
            "is_multistep_task": False,
            "confidence": 0.85,
            "reason": "This is really one browser action.",
        })
        gate = TaskIntentGate(client=client, model="qwen3:8b")

        # Again deliberately not a discovery category: this test is about
        # the model being able to veto a regex hit, which only happens for
        # requests the deterministic policy does not already decide.
        decision = gate.check("open Notepad and search my meeting notes")

        self.assertEqual(client.calls, 1)
        self.assertFalse(decision.is_multistep)

    def test_llm_failure_fails_closed_to_single_step(self):
        class BrokenClient:
            def chat(self, **_kwargs):
                raise RuntimeError("offline")

        gate = TaskIntentGate(client=BrokenClient(), model="qwen3:8b")

        decision = gate.check("open Whale and search for UW tuition")

        self.assertFalse(decision.is_multistep)

    def test_single_capability_research_and_shortlist_escalates(self):
        # 4D-4's own flagship example: this never leaves browser_control,
        # so the cross-capability check alone would never flag it -- found
        # by testing the real routing path, where it fell through to a
        # one-shot web_search that never touched the task planner at all.
        client = FakeClient({
            "is_multistep_task": True,
            "confidence": 0.9,
            "reason": "Research-then-decide across multiple hotels.",
        })
        gate = TaskIntentGate(client=client, model="qwen3:8b")

        decision = gate.check("Find a hotel in Guam and make me a shortlist.")

        # TaskDiscoveryPolicy recognises "hotel" + selection language and
        # decides this deterministically, so the model is never consulted.
        self.assertEqual(client.calls, 0)
        self.assertTrue(decision.is_multistep)

    def test_research_synthesis_needs_no_conjunction_at_all(self):
        client = FakeClient({
            "is_multistep_task": True, "confidence": 0.9, "reason": "Compare and pick.",
        })
        gate = TaskIntentGate(client=client, model="qwen3:8b")

        decision = gate.check("Compare flight prices to Tokyo and tell me the cheapest.")

        # "flight" is a recognised discovery category, so this too is
        # settled before any model call.
        self.assertEqual(client.calls, 0)
        self.assertTrue(decision.is_multistep)

    def test_synthesis_signal_without_a_browser_verb_never_escalates(self):
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("What's the best way to tie a tie?")

        self.assertFalse(decision.is_multistep)


class InformationAcquisitionScenarioTests(unittest.TestCase):
    """The 5 scenarios required for the Information Acquisition layer,
    plus paraphrases, hand-traced against the regex triggers before being
    written here. Scenario 5 ("book the best one") is deliberately not
    tested here -- it's a single committing action, not a multi-step
    research task, so it's meant to reach the existing
    ComputerConsentGate/is_committing_element checkpoint via the plain
    router (see tests/test_intent_router.py), not via TaskPlanner."""

    def _escalates(self, text: str) -> bool:
        """Assert the request reaches the task planner, by either route.

        Two routes now lead there, and which one fires is not the point of
        these scenarios: TaskDiscoveryPolicy recognises a recommendation
        category deterministically and never calls the model at all, and
        anything it doesn't cover falls through to the cheap LLM
        classification. The deterministic route is the stronger of the two
        (it survives the local model being busy or offline), so a scenario
        taking it must not read as a failure here.
        """
        client = FakeClient({
            "is_multistep_task": True, "confidence": 0.9, "reason": "test",
        })
        gate = TaskIntentGate(client=client, model="qwen3:8b")
        decision = gate.check(text)
        self.assertLessEqual(
            client.calls, 1,
            f"{text!r} should cost at most one classification call",
        )
        return decision.is_multistep

    def test_a_recognised_category_escalates_without_any_model_call(self):
        # The deterministic route, asserted explicitly so it cannot
        # silently regress into depending on the model being available.
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("Find a hotel in Guam and make me a shortlist.")

        self.assertTrue(decision.is_multistep)
        self.assertEqual(decision.confidence, 1.0)

    # Scenario 1: casual discovery -- must NOT escalate (stays on the
    # fast, cheap web_search path).
    def test_scenario_1_casual_discovery_never_escalates(self):
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("Give me some good hotels in Seoul.")

        self.assertFalse(decision.is_multistep)

    def test_scenario_1_paraphrase_reaches_the_offer_via_selection_language(self):
        # Scenario 1's base case ("Give me some good hotels in Seoul")
        # names no selection language and stays on the cheap path. This
        # paraphrase says "to stay at", which is selection language, so it
        # gets the local-market offer instead -- deterministically, with
        # no model call. That is the better answer for the user: a Seoul
        # hotel question should reach 야놀자/여기어때, not a generic
        # snippet.
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("What are some good hotels to stay at in Seoul?")

        self.assertTrue(decision.is_multistep)
        self.assertEqual(decision.confidence, 1.0)

    # Scenario 2: quantity + price constraint, no search verb at all.
    def test_scenario_2_quantity_and_price_escalates(self):
        self.assertTrue(self._escalates(
            "Give me five good hotels in Seoul under ₩200,000.",
        ))

    def test_scenario_2_paraphrase_escalates(self):
        self.assertTrue(self._escalates(
            "Can you find me five highly rated hotels in Seoul for less than ₩200,000?",
        ))

    # Scenario 3: quantity + price + verification, with a search verb.
    def test_scenario_3_booking_with_price_and_verification_escalates(self):
        self.assertTrue(self._escalates(
            "I'm booking a hotel in Seoul tonight, find five highly-rated "
            "hotels under ₩200,000 and check their actual current prices.",
        ))

    def test_scenario_3_paraphrase_escalates(self):
        self.assertTrue(self._escalates(
            "I need to book a hotel in Seoul tonight -- search for five "
            "highly rated ones under ₩200,000 and confirm their current "
            "prices.",
        ))

    # Scenario 4: bare follow-up verification question, no leading verb.
    def test_scenario_4_followup_verification_escalates(self):
        self.assertTrue(self._escalates(
            "Which of these hotels is actually available Friday night?",
        ))

    def test_scenario_4_paraphrase_escalates(self):
        self.assertTrue(self._escalates(
            "Are any of those hotels actually available this Friday?",
        ))

    def test_quantity_alone_without_price_or_synthesis_does_not_escalate(self):
        # "five hotels" alone, with no price/comparison signal, isn't
        # enough -- avoids over-triggering on plain counting.
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("I stayed at five hotels in Seoul last year.")

        self.assertFalse(decision.is_multistep)

    def test_deictic_backref_without_verification_signal_does_not_escalate(self):
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("Tell me more about these hotels.")

        self.assertFalse(decision.is_multistep)

    # Reason (4): evaluative recommendation / price-bounded shopping with
    # no _BROWSER_CONTROL_VERBS match at all -- needed for the strategy-
    # offer checkpoint (only reachable from inside the task planner) to
    # ever get a chance to fire on these.
    def test_evaluative_recommendation_without_a_verb_escalates(self):
        self.assertTrue(self._escalates("Best restaurants to go in Seoul."))

    def test_evaluative_recommendation_paraphrase_escalates(self):
        self.assertTrue(self._escalates(
            "What are the top-rated restaurants in Seoul?",
        ))

    def test_price_bounded_shopping_without_a_verb_escalates(self):
        self.assertTrue(self._escalates("Cars to buy under 10k."))

    def test_price_bounded_shopping_paraphrase_escalates(self):
        self.assertTrue(self._escalates(
            "I want to purchase a car for less than $10,000.",
        ))

    def test_bare_compliment_never_escalates(self):
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("You're the best!")

        self.assertFalse(decision.is_multistep)

    def test_purchase_verb_without_a_price_never_escalates(self):
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("I need to buy groceries.")

        self.assertFalse(decision.is_multistep)

    def test_best_way_phrasing_never_escalates(self):
        # A generic instructional "best way to X" is not a recommendation-
        # among-options request -- "way" is deliberately excluded from the
        # noun-hint list so this stays off the escalation path entirely.
        gate = TaskIntentGate(client=MustNotRunClient(), model="qwen3:8b")

        decision = gate.check("What's the best way to learn Python?")

        self.assertFalse(decision.is_multistep)


if __name__ == "__main__":
    unittest.main()
