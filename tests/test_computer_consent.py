import json
import unittest
from unittest.mock import patch

from agents.consent import SemanticConsentClassifier
from security.computer_consent import ComputerConsentGate
from tools.computer_control.computer_control import PreparedComputerAction


class ComputerConsentTests(unittest.TestCase):
    @staticmethod
    def force_quit(app="Discord", entry_id="discord-entry"):
        return PreparedComputerAction(
            operation="force_quit_app",
            target=app,
            display_name=app,
            entry_id=entry_id,
        )

    def test_offer_stores_one_exact_high_risk_action(self):
        gate = ComputerConsentGate(expiry_seconds=90)

        pending = gate.offer(prepared=self.force_quit())

        self.assertEqual(pending.request, "Force quit Discord")
        self.assertEqual(pending.entry_id, "discord-entry")
        self.assertIs(gate.peek(), pending)

    def test_offer_expires_before_a_later_reply_can_authorize_it(self):
        gate = ComputerConsentGate(expiry_seconds=90)
        with patch("security.computer_consent.time.monotonic", return_value=100):
            gate.offer(prepared=self.force_quit("Steam", "steam-entry"))

        with patch("security.computer_consent.time.monotonic", return_value=191):
            self.assertIsNone(gate.peek())

    def test_clear_removes_the_pending_action(self):
        gate = ComputerConsentGate()
        gate.offer(prepared=self.force_quit("Battle.net", "battle-entry"))

        gate.clear()

        self.assertIsNone(gate.peek())

    def test_offer_stores_exact_recycle_action_payload(self):
        prepared = PreparedComputerAction(
            operation="delete_file",
            target="notes.txt",
            display_name="notes.txt",
            path="C:/Users/test/Documents/notes.txt",
        )

        pending = ComputerConsentGate().offer(prepared=prepared)

        self.assertIs(pending.prepared, prepared)
        self.assertEqual(pending.operation, "delete_file")
        self.assertEqual(pending.request, "Delete file notes.txt")

    def test_low_risk_action_cannot_create_a_pending_confirmation(self):
        prepared = PreparedComputerAction(
            operation="open_app",
            target="Discord",
            display_name="Discord",
            entry_id="discord-entry",
        )

        with self.assertRaises(ValueError):
            ComputerConsentGate().offer(prepared=prepared)

    def test_semantic_positive_reply_accepts_the_exact_pending_action(self):
        class FakeClient:
            def chat(self, **_kwargs):
                return {"message": {"content": json.dumps({
                    "decision": "accept",
                    "confidence": 0.99,
                    "reason": "The reply authorizes the pending force quit.",
                    "modified_request": "",
                })}}

        pending = ComputerConsentGate().offer(prepared=self.force_quit())
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
                    "modified_request": "Force quit Steam instead.",
                })}}

        pending = ComputerConsentGate().offer(prepared=self.force_quit())
        result = SemanticConsentClassifier(FakeClient(), "test").classify(
            "Actually, force quit Steam instead.",
            pending,
        )

        self.assertEqual(result.decision, "modify")
        self.assertEqual(result.modified_request, "Force quit Steam instead.")


if __name__ == "__main__":
    unittest.main()
