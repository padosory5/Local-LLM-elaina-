"""Prices Elaina never looked up must not be stated as fact.

Found live, in a conversation-routed turn with no tool call in it:

    User:   for real? that seems cheap
    Elaina: "Trip.com shows prices starting at around 120,000 KRW for
             Harbour Plaza Hotels."

Nothing was read. Being handed an invented figure is the worst possible
answer to someone doubting a number.
"""

import threading
import unittest

from brain.chat_engine import ChatEngine
from brain.grounded_values import GroundedValueGuard
from security.capability_offer import CapabilityOfferGate


EVIDENCE = "Harbour Plaza and The Peninsula are both near the harbour."


class UnsupportedAmountTests(unittest.TestCase):
    def test_a_figure_absent_from_the_evidence_is_flagged(self):
        self.assertTrue(GroundedValueGuard.needs_correction(
            "Trip.com shows prices starting at around 120,000 KRW.",
            evidence=EVIDENCE,
            action_performed=False,
        ))

    def test_a_figure_present_in_the_evidence_is_fine(self):
        self.assertFalse(GroundedValueGuard.needs_correction(
            "It's about 120,000 KRW a night.",
            evidence="Harbour Plaza is listed at ₩120,000 per night.",
            action_performed=False,
        ))

    def test_currency_notation_differences_do_not_count_as_new(self):
        self.assertFalse(GroundedValueGuard.needs_correction(
            "Rooms are ₩120,000.",
            evidence="Rooms are 120000 won.",
            action_performed=False,
        ))

    def test_a_figure_the_user_supplied_is_fine(self):
        self.assertFalse(GroundedValueGuard.needs_correction(
            "Under 300,000 won should be easy.",
            evidence="budget around 300000 won",
            action_performed=False,
        ))

    def test_a_turn_that_actually_ran_a_capability_is_never_second_guessed(self):
        self.assertFalse(GroundedValueGuard.needs_correction(
            "Rooms start at 120,000 KRW.",
            evidence=EVIDENCE,
            action_performed=True,
        ))

    def test_ordinary_conversation_with_no_grounded_subject_is_left_alone(self):
        # Most numbers in conversation are perfectly fine to state from
        # general knowledge; only a follow-up about something Elaina really
        # looked up is checked.
        self.assertFalse(GroundedValueGuard.needs_correction(
            "A coffee in Seoul is about 5,000 won.",
            evidence="",
            action_performed=False,
        ))

    def test_a_plain_count_or_year_is_not_a_money_claim(self):
        self.assertFalse(GroundedValueGuard.needs_correction(
            "There are 3 good options, all built after 2015.",
            evidence=EVIDENCE,
            action_performed=False,
        ))


class CorrectionTests(unittest.TestCase):
    def test_only_the_sentence_carrying_the_invented_figure_is_dropped(self):
        result = GroundedValueGuard.correct(
            "Harbour Plaza is the closest to the harbour. "
            "Trip.com shows prices starting at 120,000 KRW.",
            evidence=EVIDENCE,
            offer="Want me to check?",
        )

        self.assertIn("closest to the harbour", result)
        self.assertNotIn("120,000", result)
        self.assertIn("Want me to check?", result)

    def test_a_reply_that_is_only_an_invented_figure_becomes_the_offer(self):
        result = GroundedValueGuard.correct(
            "It's 120,000 KRW.", evidence=EVIDENCE, offer="Want me to check?",
        )

        self.assertEqual(result, "Want me to check?")

    def test_nothing_unsupported_means_nothing_changes(self):
        reply = "Harbour Plaza is closest to the harbour."

        self.assertEqual(
            GroundedValueGuard.correct(reply, evidence=EVIDENCE, offer="x?"),
            reply,
        )


class _Mode:
    def __init__(self, enabled):
        self.enabled = enabled


class _Screen:
    enabled = True


class EngineIntegrationTests(unittest.TestCase):
    def _engine(self, *, control_mode=True, statement=EVIDENCE):
        engine = ChatEngine.__new__(ChatEngine)
        engine.computer_control_mode = _Mode(control_mode)
        engine.browser_page_control_enabled = True
        engine._web_search_enabled = True
        engine.screen_monitor = _Screen()
        engine.project_mcp = None
        engine.capability_offer = CapabilityOfferGate()
        engine._desktop_surface_lock = threading.Lock()
        engine._grounded_context = {"subject": "Hong Kong hotels", "statement": statement}
        return engine

    def test_an_invented_price_becomes_an_answerable_offer(self):
        engine = self._engine()

        reply = engine._enforce_grounded_values(
            "Trip.com shows prices starting at around 120,000 KRW for Harbour Plaza.",
            user_input="for real? that seems cheap",
            action_performed=False,
        )

        self.assertNotIn("120,000", reply)
        self.assertIn("haven't actually checked", reply)
        self.assertEqual(
            engine.capability_offer.peek().capability_id, "browser_control",
        )

    def test_with_browser_control_off_it_declines_to_guess_instead(self):
        engine = self._engine(control_mode=False)

        reply = engine._enforce_grounded_values(
            "Rooms are 120,000 KRW.",
            user_input="for real?",
            action_performed=False,
        )

        self.assertIn("rather not guess", reply)
        self.assertIsNone(engine.capability_offer.peek())

    def test_a_turn_that_did_the_work_keeps_its_numbers(self):
        engine = self._engine()
        answer = "Rooms start at 120,000 KRW."

        self.assertEqual(
            engine._enforce_grounded_values(
                answer, user_input="check the price", action_performed=True,
            ),
            answer,
        )


if __name__ == "__main__":
    unittest.main()
