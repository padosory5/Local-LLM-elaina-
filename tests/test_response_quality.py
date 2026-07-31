import unittest

from brain.response_quality import ResponseQualityGuard


class ResponseQualityGuardTests(unittest.TestCase):
    def test_retries_old_greeting_for_new_current_message(self):
        history = [
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "Hey there. What's up?"},
        ]

        self.assertTrue(ResponseQualityGuard.should_retry(
            "Hey there. What's up?",
            "The buttons on my project look boring.",
            history,
        ))

    def test_allows_same_answer_when_user_repeats_same_message(self):
        history = [
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "Hey there. What's up?"},
        ]

        self.assertFalse(ResponseQualityGuard.should_retry(
            "Hey there. What's up?",
            "Hello.",
            history,
        ))

    def test_allows_repeating_a_fact_when_user_asks_for_it_again(self):
        history = [
            {"role": "user", "content": "When was OpenAI founded?"},
            {
                "role": "assistant",
                "content": "OpenAI was founded in December 2015.",
            },
        ]

        self.assertFalse(ResponseQualityGuard.should_retry(
            "OpenAI was founded in December 2015.",
            "Yeah, tell me when.",
            history,
        ))


if __name__ == "__main__":
    unittest.main()
