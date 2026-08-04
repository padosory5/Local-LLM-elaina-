import json
import unittest
from unittest.mock import patch

from agents.consent import SemanticConsentClassifier
from security.computer_consent import ComputerConsentGate
from tools.computer_control import PreparedComputerAction


class ComputerConsentTests(unittest.TestCase):
    def test_offer_stores_one_exact_resolved_app_without_launching(self):
        gate = ComputerConsentGate(expiry_seconds=90)

        pending = gate.offer(
            target_name="Discord",
            entry_id="discord-entry",
        )

        self.assertEqual(pending.request, "Open Discord")
        self.assertEqual(pending.entry_id, "discord-entry")
        self.assertIs(gate.peek(), pending)

    def test_offer_expires_before_a_later_reply_can_authorize_it(self):
        gate = ComputerConsentGate(expiry_seconds=90)
        with patch("security.computer_consent.time.monotonic", return_value=100):
            gate.offer(target_name="Steam", entry_id="steam-entry")

        with patch("security.computer_consent.time.monotonic", return_value=191):
            self.assertIsNone(gate.peek())

    def test_clear_removes_the_pending_action(self):
        gate = ComputerConsentGate()
        gate.offer(target_name="Battle.net Launcher", entry_id="battle-entry")

        gate.clear()

        self.assertIsNone(gate.peek())

    def test_offer_stores_exact_non_app_action_payload(self):
        prepared = PreparedComputerAction(
            operation="create_file",
            target="notes.txt",
            display_name="notes.txt",
            path="C:/Users/test/Documents/notes.txt",
        )

        pending = ComputerConsentGate().offer(prepared=prepared)

        self.assertIs(pending.prepared, prepared)
        self.assertEqual(pending.operation, "create_file")
        self.assertEqual(pending.request, "Create file notes.txt")

    def test_semantic_positive_reply_accepts_the_exact_pending_action(self):
        class FakeClient:
            def chat(self, **_kwargs):
                return {"message": {"content": json.dumps({
                    "decision": "accept",
                    "confidence": 0.99,
                    "reason": "The reply authorizes the pending app launch.",
                    "modified_request": "",
                })}}

        pending = ComputerConsentGate().offer(
            target_name="Discord",
            entry_id="discord-entry",
        )
        result = SemanticConsentClassifier(FakeClient(), "test").classify(
            "Yeah, go ahead.",
            pending,
        )

        self.assertEqual(result.decision, "accept")

    def test_semantic_modified_reply_returns_the_complete_new_request(self):
        class FakeClient:
            def chat(self, **_kwargs):
                return {"message": {"content": json.dumps({
                    "decision": "modify",
                    "confidence": 0.98,
                    "reason": "The user selected a different application.",
                    "modified_request": "Open Steam instead.",
                })}}

        pending = ComputerConsentGate().offer(
            target_name="Discord",
            entry_id="discord-entry",
        )
        result = SemanticConsentClassifier(FakeClient(), "test").classify(
            "Actually, open Steam instead.",
            pending,
        )

        self.assertEqual(result.decision, "modify")
        self.assertEqual(result.modified_request, "Open Steam instead.")


if __name__ == "__main__":
    unittest.main()
