import time
import unittest

from security.capability_offer import CapabilityOfferGate


class CapabilityOfferGateTests(unittest.TestCase):
    def test_an_offer_is_readable_until_it_is_cleared(self):
        gate = CapabilityOfferGate()

        gate.offer(
            capability_id="browser_control",
            goal="check the price on the browser",
            offer_text="I can use browser control for this -- want me to?",
        )
        pending = gate.peek()

        self.assertIsNotNone(pending)
        self.assertEqual(pending.capability_id, "browser_control")
        self.assertEqual(pending.intent, "computer_action")

        gate.clear()
        self.assertIsNone(gate.peek())

    def test_the_request_is_a_plain_action_never_the_spoken_question(self):
        # PendingStrategyOffer already learned this the hard way: feeding
        # SemanticConsentClassifier a two-option question as the "pending
        # task" made it read declines as acceptance.
        gate = CapabilityOfferGate()

        pending = gate.offer(
            capability_id="browser_control",
            goal="check the price on the browser",
            offer_text="Want me to check it, or is the estimate fine?",
        )

        self.assertNotIn("?", pending.request)
        self.assertIn("check the price on the browser", pending.request)
        self.assertEqual(
            pending.offer_text, "Want me to check it, or is the estimate fine?",
        )

    def test_an_expired_offer_can_never_authorise_a_late_yes(self):
        gate = CapabilityOfferGate(expiry_seconds=0)

        gate.offer(
            capability_id="browser_control", goal="check prices", offer_text="ok?",
        )
        # The gate floors the expiry at 15s, so move the clock instead of
        # sleeping for it.
        pending = gate.peek()
        self.assertIsNotNone(pending)
        gate._pending = pending.__class__(
            **{**pending.__dict__, "expires_at": time.monotonic() - 1}
        )

        self.assertIsNone(gate.peek())

    def test_a_new_offer_replaces_the_previous_one(self):
        gate = CapabilityOfferGate()

        gate.offer(capability_id="browser_control", goal="a", offer_text="a?")
        gate.offer(capability_id="ui_control", goal="b", offer_text="b?")

        self.assertEqual(gate.peek().capability_id, "ui_control")

    def test_public_context_exposes_no_internal_state(self):
        gate = CapabilityOfferGate()
        gate.offer(capability_id="browser_control", goal="check", offer_text="ok?")

        context = gate.peek().public_context()

        self.assertEqual(
            set(context), {"intent", "request", "capability", "expires_in_seconds"},
        )


if __name__ == "__main__":
    unittest.main()
