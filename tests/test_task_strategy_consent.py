import json
import unittest
from unittest.mock import patch

from agents.consent import SemanticConsentClassifier
from brain.task_planner import TaskState
from security.task_strategy_consent import TaskStrategyConsentGate


class TaskStrategyConsentTests(unittest.TestCase):
    @staticmethod
    def _offer(gate, *, offer_text="I could check a hotel booking site directly."):
        task_state = TaskState(goal="Give me a shortlist of hotels in Seoul.")
        return gate.offer(task_state=task_state, offer_text=offer_text)

    def test_offer_stores_the_paused_task_and_exposes_intent_and_request(self):
        gate = TaskStrategyConsentGate(expiry_seconds=90)

        pending = self._offer(gate)

        self.assertEqual(pending.intent, "task_action")
        # request is a plain action description for the consent classifier
        # to judge against -- distinct from offer_text (the exact spoken
        # question, which can embed a "...or a quick overview?" second
        # option that would otherwise confuse the classifier).
        self.assertIn(
            "Give me a shortlist of hotels in Seoul.", pending.request,
        )
        self.assertEqual(
            pending.offer_text, "I could check a hotel booking site directly.",
        )
        self.assertEqual(
            pending.task_state.goal, "Give me a shortlist of hotels in Seoul.",
        )
        self.assertIs(gate.peek(), pending)

    def test_offer_expires_before_a_later_reply_can_authorize_it(self):
        gate = TaskStrategyConsentGate(expiry_seconds=90)
        with patch("security.task_strategy_consent.time.monotonic", return_value=100):
            self._offer(gate)

        with patch("security.task_strategy_consent.time.monotonic", return_value=191):
            self.assertIsNone(gate.peek())

    def test_clear_removes_the_pending_offer(self):
        gate = TaskStrategyConsentGate()
        self._offer(gate)

        gate.clear()

        self.assertIsNone(gate.peek())

    def test_public_context_never_leaks_the_full_task_state_object(self):
        gate = TaskStrategyConsentGate()
        pending = self._offer(gate)

        context = pending.public_context()

        self.assertEqual(context["intent"], "task_action")
        self.assertEqual(
            context["offer_text"], "I could check a hotel booking site directly.",
        )
        self.assertNotIn("task_state", context)

    def test_semantic_consent_classifier_accepts_the_real_pending_shape(self):
        # SemanticConsentClassifier.classify() reads offer.intent/offer.request
        # directly (not truly Any) -- this proves PendingStrategyOffer
        # satisfies that real contract, the same way PendingTaskAction and
        # PendingComputerAction already do.
        class FakeClient:
            def chat(self, **_kwargs):
                return {"message": {"content": json.dumps({
                    "decision": "accept",
                    "confidence": 0.95,
                    "reason": "The reply authorizes checking the site.",
                    "modified_request": "",
                })}}

        gate = TaskStrategyConsentGate()
        pending = self._offer(gate)

        result = SemanticConsentClassifier(FakeClient(), "test").classify(
            "Yes, please check it.", pending,
        )

        self.assertEqual(result.decision, "accept")


if __name__ == "__main__":
    unittest.main()
