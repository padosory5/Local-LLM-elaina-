import unittest

from brain.response_policy import AnswerCompletionGuard, ResponseLimits
from brain.text_filter import TextFilter


class RepresentativeVoiceResponseTests(unittest.TestCase):
    CASES = (
        (
            "distribution",
            (
                "You and the friend who put in 100 dollars each receive 260 "
                "dollars, while the friend who put in 50 receives 130. Your "
                "individual profit is 160 dollars."
            ),
            ("260", "130", "160"),
        ),
        (
            "bill split",
            "The 20 percent tip makes the total 96 dollars, so each person pays 32 dollars.",
            ("96", "32"),
        ),
        (
            "discount",
            "A 25 percent discount takes 20 dollars off, so the final price is 60 dollars.",
            ("25", "20", "60"),
        ),
        (
            "travel time",
            "At 50 kilometers per hour, a 150-kilometer trip takes 3 hours.",
            ("50", "150", "3"),
        ),
    )

    def test_required_results_survive_voice_sanitizing(self):
        limits = ResponseLimits(max_words=45, max_sentences=2)

        for name, draft, expected_numbers in self.CASES:
            with self.subTest(case=name):
                reply = TextFilter.for_voice_response(
                    draft,
                    max_words=45,
                    max_sentences=2,
                )

                for number in expected_numbers:
                    self.assertIn(number, reply)
                self.assertFalse(AnswerCompletionGuard.needs_retry(
                    reply,
                    calculation=True,
                ))
                self.assertFalse(limits.exceeds(reply))


if __name__ == "__main__":
    unittest.main()
