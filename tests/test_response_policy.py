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

    def test_a_question_that_is_not_an_offer_to_act_survives(self):
        # Content, not filler: these name something in the conversation and
        # do not offer to go and do anything.
        for kept in (
            "There are three I'd look at. Let me know which one you'd prefer.",
            "I've paused it. Let me know when you're ready to start again.",
            "That's the cheaper one. Tell me if you want the other.",
            "Since you mentioned eye strain, a 27-inch 1440p IPS monitor "
            "is probably a good fit.",
        ):
            with self.subTest(kept=kept):
                self.assertEqual(ClosingOfferGuard.strip(kept), kept)

    def test_the_model_s_own_offers_to_act_are_removed(self):
        # Phase 4E.3 makes RecommendationPolicy the one layer deciding
        # whether a proactive offer appears. Measured over twenty live
        # turns, four of eleven recommend-turns were suppressed *because
        # the model had already offered* -- uncooled, unparked, and outside
        # the policy entirely. Its offers go; the policy re-adds one when
        # its cooldown allows, and that is the only one the user sees.
        for reply, expected in (
            ("I hope you enjoyed it. If you want, I can suggest some movies.",
             "I hope you enjoyed it."),
            ("That restaurant looks good. Would you like me to search for them?",
             "That restaurant looks good."),
            ("It is pricey. Want me to look some up?", "It is pricey."),
            ("I found the page. Want me to pull it up?", "I found the page."),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(ClosingOfferGuard.strip(reply), expected)

    def test_an_offer_without_the_words_me_to_is_still_an_offer(self):
        # Live: "Would you like help finding specific models or deals?"
        # survived, the policy appended its own offer after it, and the reply
        # carried two.
        self.assertEqual(
            ClosingOfferGuard.strip(
                "Check Coupang for prices. "
                "Would you like help finding specific models or deals?"
            ),
            "Check Coupang for prices.",
        )

    def test_curly_apostrophe_offer_from_live_search_failure_is_removed(self):
        reply = (
            "I couldn't show real listings right now. "
            "Let me know if you’d like me to search for options or suggest "
            "areas to look."
        )

        self.assertEqual(
            ClosingOfferGuard.strip(reply),
            "I couldn't show real listings right now.",
        )

    def test_advice_is_never_mistaken_for_an_offer(self):
        # "I'd suggest checking their site" recommends that *you* do
        # something; "I can suggest some options" offers that *she* does.
        for kept in (
            "I'd suggest checking out LG or Samsung's websites.",
            "You could try the Pomodoro Technique to stay focused.",
            "Start by browsing listings and reaching out to sellers.",
            "Just make sure to check the inspection reports first.",
        ):
            with self.subTest(kept=kept):
                self.assertFalse(ClosingOfferGuard.offers_to_act(kept))
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

    def test_an_offer_to_act_is_now_recognised_too(self):
        # It was deliberately not, until RecommendationPolicy became the one
        # layer deciding whether the user sees an offer.
        self.assertTrue(
            ClosingOfferGuard.is_closing_offer("Want me to open the second one?")
        )

    def test_a_waiting_offer_is_protected_from_the_strip(self):
        # Her own "want me to?" is parked, and the next "ok" resolves it.
        # Removing the sentence would leave the gate holding an offer the
        # user never saw -- which broke two whole-turn tests when it did.
        reply = "Yes, I can control the browser. Want me to open it?"

        self.assertEqual(
            ClosingOfferGuard.strip(reply, keep_offers=True), reply,
        )
        self.assertNotEqual(ClosingOfferGuard.strip(reply), reply)

    def test_generic_filler_goes_even_when_an_offer_is_waiting(self):
        reply = "Discord is open. Let me know if you need anything else."

        self.assertEqual(
            ClosingOfferGuard.strip(reply, keep_offers=True),
            "Discord is open.",
        )


if __name__ == "__main__":
    unittest.main()
