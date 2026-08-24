import json
import unittest
from unittest.mock import patch

from agents.consent import SemanticConsentClassifier
from brain.task_planner import TaskState, TaskStep
from security.task_consent import TaskConsentGate
from tools.computer_control.computer_control import PreparedComputerAction


class TaskConsentTests(unittest.TestCase):
    @staticmethod
    def _offer(gate, *, sub_goal="Click Buy now."):
        task_state = TaskState(goal="Buy the item.")
        step = TaskStep(capability="browser_control", sub_goal=sub_goal)
        prepared = PreparedComputerAction(
            operation="browser_action", target="scan1-e2", display_name="Buy now",
        )
        return gate.offer(
            task_state=task_state, step=step, capability="browser_control",
            prepared=prepared, reason="Committing action.",
        )

    def test_offer_stores_the_paused_task_and_exposes_intent_and_request(self):
        gate = TaskConsentGate(expiry_seconds=90)

        pending = self._offer(gate)

        self.assertEqual(pending.intent, "task_action")
        self.assertEqual(pending.request, "Click Buy now.")
        self.assertEqual(pending.capability, "browser_control")
        self.assertIs(gate.peek(), pending)

    def test_offer_expires_before_a_later_reply_can_authorize_it(self):
        gate = TaskConsentGate(expiry_seconds=90)
        with patch("security.task_consent.time.monotonic", return_value=100):
            self._offer(gate)

        with patch("security.task_consent.time.monotonic", return_value=191):
            self.assertIsNone(gate.peek())

    def test_clear_removes_the_pending_task(self):
        gate = TaskConsentGate()
        self._offer(gate)

        gate.clear()

        self.assertIsNone(gate.peek())

    def test_public_context_never_leaks_the_full_task_state_object(self):
        gate = TaskConsentGate()
        pending = self._offer(gate)

        context = pending.public_context()

        self.assertEqual(context["intent"], "task_action")
        self.assertEqual(context["capability"], "browser_control")
        self.assertNotIn("task_state", context)

    def test_semantic_consent_classifier_accepts_the_real_pending_shape(self):
        # SemanticConsentClassifier.classify() reads offer.intent/offer.request
        # directly (not truly Any) -- this proves PendingTaskAction satisfies
        # that real contract, the same way PendingComputerAction already does.
        class FakeClient:
            def chat(self, **_kwargs):
                return {"message": {"content": json.dumps({
                    "decision": "accept",
                    "confidence": 0.97,
                    "reason": "The reply authorizes the pending step.",
                    "modified_request": "",
                })}}

        gate = TaskConsentGate()
        pending = self._offer(gate)

        result = SemanticConsentClassifier(FakeClient(), "test").classify(
            "Yes, go ahead.", pending,
        )

        self.assertEqual(result.decision, "accept")


if __name__ == "__main__":
    unittest.main()
