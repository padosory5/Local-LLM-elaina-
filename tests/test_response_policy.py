import unittest

from brain.response_policy import AnswerCompletionGuard, ResponseLimits


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


if __name__ == "__main__":
    unittest.main()
