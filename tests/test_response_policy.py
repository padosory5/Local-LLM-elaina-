import unittest

from brain.response_policy import (
    AdviceResponseGuard,
    AnswerCompletionGuard,
    ClosingOfferGuard,
    ResponseLimits,
)


class ResponsePolicyTests(unittest.TestCase):
    def test_limits_are_written_as_generation_instructions(self):
        instruction = ResponseLimits(
            max_words=45,
            max_sentences=2,
        ).instruction(calculation=True)

        self.assertIn("at most 45 spoken words", instruction)
        self.assertIn("at most 2 complete sentences", instruction)
        self.assertIn("final numerical result first", instruction)
        self.assertIn("never stop mid-sentence", instruction)

    def test_zero_disables_length_target_without_disabling_completeness(self):
        instruction = ResponseLimits().instruction(calculation=False)

        self.assertIn("only as much detail", instruction)
        self.assertIn("Answer the current request", instruction)

    def test_voice_advice_requires_a_short_actionable_recommendation(self):
        instruction = ResponseLimits(
            max_words=45,
            max_sentences=2,
        ).instruction(recommendation=True)

        self.assertIn("direct, friendly recommendation", instruction)
        self.assertIn("action the user can take now", instruction)
        self.assertIn("one essential caution", instruction)
        self.assertIn("Never make a referral the whole answer", instruction)
        self.assertIn("Do not append a doctor", instruction)
        self.assertIn("at most 2 complete sentences", instruction)

    def test_routine_referral_requires_advice_rewrite(self):
        self.assertTrue(AdviceResponseGuard.needs_rewrite(
            "Try melatonin, then check with a doctor before using it.",
            recommendation=True,
            urgent_safety=False,
        ))

    def test_actionable_routine_advice_does_not_require_rewrite(self):
        self.assertFalse(AdviceResponseGuard.needs_rewrite(
            "Try melatonin and follow the label. Tell me which medicines you take first.",
            recommendation=True,
            urgent_safety=False,
        ))

    def test_urgent_safety_advice_keeps_emergency_direction(self):
        self.assertFalse(AdviceResponseGuard.needs_rewrite(
            "Call emergency services now and follow their instructions.",
            recommendation=True,
            urgent_safety=True,
        ))

    def test_numeric_health_dose_requires_rewrite(self):
        self.assertTrue(AdviceResponseGuard.needs_rewrite(
            "Try 0.5 mg of melatonin before bed.",
            recommendation=True,
            urgent_safety=False,
            advice_domain="health",
        ))

    def test_label_based_health_guidance_is_allowed(self):
        self.assertFalse(AdviceResponseGuard.needs_rewrite(
            "Try melatonin and start with the lowest amount on the label.",
            recommendation=True,
            urgent_safety=False,
            advice_domain="health",
        ))

    def test_generation_budget_is_not_the_old_100_token_cutoff(self):
        self.assertGreaterEqual(
            ResponseLimits(max_words=45, max_sentences=2)
            .generation_budget(),
            256,
        )

    def test_overlong_answer_is_marked_for_model_rewrite(self):
        limits = ResponseLimits(max_words=8, max_sentences=1)

        self.assertTrue(limits.exceeds(
            "You receive 260 dollars. Your friend also receives 260 dollars."
        ))
        self.assertFalse(limits.exceeds("You receive 260 dollars."))

    def test_short_extra_advice_sentence_is_merged_without_data_loss(self):
        limits = ResponseLimits(max_words=45, max_sentences=2)
        draft = (
            "Choose Live2D because it is easier. Start with a simple model. "
            "Move to 3D later if you need more realism."
        )

        compact = limits.merge_extra_sentences(draft)

        self.assertFalse(limits.exceeds(compact))
        self.assertIn("Choose Live2D", compact)
        self.assertIn("Start with a simple model", compact)
        self.assertIn("Move to 3D later", compact)

    def test_calculation_deferral_requires_retry(self):
        self.assertTrue(AnswerCompletionGuard.needs_retry(
            "Let me calculate that for you. Want me to do the math?",
            calculation=True,
        ))

    def test_complete_calculation_does_not_require_retry(self):
        self.assertFalse(AnswerCompletionGuard.needs_retry(
            (
                "You and the first friend receive 260 dollars each, and the "
                "second friend receives 130 dollars."
            ),
            calculation=True,
        ))

    def test_mid_sentence_calculation_requires_retry(self):
        self.assertTrue(AnswerCompletionGuard.needs_retry(
            "You receive 260 dollars and your friend receives",
            calculation=True,
        ))


class ClosingOfferGuardTests(unittest.TestCase):
    """The canned "anything else?" footer her personality file already bans.

    Every case below with a source note was produced by the real model in a
    real session, which is the point: the instruction not to write them has
    been in the prompt the whole time.
    """

    def test_the_measured_failure_is_removed(self):
        # Live, answering "what is the current exchange rate between yen and won".
        reply = (
            "The current exchange rate between the Japanese Yen (JPY) and the "
            "South Korean Won (KRW) is approximately 1 KRW = 0.013 JPY, based "
            "on the latest available data as of 2026-08-29. Let me know if "
            "you need help with anything else!"
        )

        stripped = ClosingOfferGuard.strip(reply)

        self.assertNotIn("Let me know", stripped)
        self.assertIn("0.013 JPY", stripped)
        self.assertTrue(stripped.endswith("2026-08-29."))

    def test_a_canned_greeting_footer_is_removed(self):
        # Live, answering "hi elaina, how are you doing today".
        reply = (
            "Hey there! I'm doing great, thanks for asking. How about you? "
            "I'm here to help with anything you need."
        )

        stripped = ClosingOfferGuard.strip(reply)

        self.assertEqual(
            stripped,
            "Hey there! I'm doing great, thanks for asking. How about you?",
        )

    def test_the_usual_shapes_are_all_caught(self):
        for closer in (
            "Let me know if you have any other questions.",
            "Feel free to ask if you need more.",
            "Is there anything else I can help you with?",
            "Happy to help with anything else!",
            "Let me know if you need anything else.",
            "If you need anything, just ask.",
        ):
            with self.subTest(closer=closer):
                stripped = ClosingOfferGuard.strip(f"Discord is open. {closer}")
                self.assertEqual(stripped, "Discord is open.")

    def test_several_stacked_footers_all_go(self):
        reply = (
            "Spain won. Let me know if you have any questions. "
            "Happy to help with anything else!"
        )

        self.assertEqual(ClosingOfferGuard.strip(reply), "Spain won.")

    def test_an_offer_with_a_real_referent_survives(self):
        # This is content, not filler: it names something in the conversation
        # and the user can act on it. Phase 4E.3 depends on these surviving.
        for kept in (
            "There are three I'd look at. Let me know which one you'd prefer.",
            "I found the page. Want me to pull it up?",
            "I've paused it. Let me know when you're ready to start again.",
            "That's the cheaper one. Tell me if you want the other.",
        ):
            with self.subTest(kept=kept):
                self.assertEqual(ClosingOfferGuard.strip(kept), kept)

    def test_an_ordinary_answer_is_untouched(self):
        for reply in (
            "Recursion is a function calling itself.",
            "Spain won the 2026 World Cup, beating Argentina 1-0.",
            "",
            "   ",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(ClosingOfferGuard.strip(reply), reply)

    def test_a_reply_that_is_only_an_offer_is_never_emptied(self):
        # A weak reply is a different failure from no reply at all, and
        # silently returning "" would turn one into the other.
        for reply in (
            "Let me know if you need anything else.",
            "Is there anything else I can help with?",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(ClosingOfferGuard.strip(reply), reply)

    def test_the_question_mark_form_is_recognized(self):
        self.assertTrue(
            ClosingOfferGuard.is_closing_offer("Anything else I can help with?")
        )
        self.assertFalse(
            ClosingOfferGuard.is_closing_offer("Want me to open the second one?")
        )


if __name__ == "__main__":
    unittest.main()
