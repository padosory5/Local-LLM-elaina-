import unittest

from brain.answer_condenser import AnswerCondenser


class FakeClient:
    def __init__(self, content, *, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"message": {"content": self.content}}


LONG_RESULT = (
    "I looked at three places in Seoul. Hotel Cappuccino in Gangnam is "
    "around 120,000 won a night and has a rooftop bar. L7 Hongdae is about "
    "95,000 won a night and sits two minutes from the subway. Nine Tree "
    "Premier in Myeongdong is roughly 140,000 won a night and is the "
    "closest of the three to the palaces, though it books out on weekends "
    "and the rooms are the smallest of the three by a fair margin."
)


class CondensingThresholdTests(unittest.TestCase):
    def test_a_short_result_is_never_sent_to_the_model(self):
        client = FakeClient("shorter")
        condenser = AnswerCondenser(client, "qwen3:8b")

        result = condenser.condense(
            "Rooms start at 95,000 won.", max_words=45, max_sentences=2,
        )

        self.assertEqual(result, "Rooms start at 95,000 won.")
        self.assertEqual(client.calls, [])

    def test_a_slightly_long_result_is_not_worth_a_model_call(self):
        condenser = AnswerCondenser(FakeClient("x"), "qwen3:8b")

        self.assertFalse(condenser.should_condense("word " * 50, max_words=45))
        self.assertTrue(condenser.should_condense("word " * 120, max_words=45))

    def test_no_word_limit_disables_condensing_entirely(self):
        condenser = AnswerCondenser(FakeClient("x"), "qwen3:8b")

        self.assertFalse(condenser.should_condense("word " * 500, max_words=0))


class FaithfulnessTests(unittest.TestCase):
    """The contract is checked in code, not trusted to the prompt."""

    def _condense(self, candidate):
        client = FakeClient(candidate)
        condenser = AnswerCondenser(client, "qwen3:8b")
        return condenser.condense(
            LONG_RESULT, max_words=45, max_sentences=2, goal="hotels in Seoul",
        ), client

    def test_a_faithful_shortening_is_accepted(self):
        short = (
            "L7 Hongdae is the cheapest at about 95,000 won, Hotel "
            "Cappuccino is 120,000, and Nine Tree Premier is 140,000."
        )

        result, client = self._condense(short)

        self.assertEqual(result, short)
        self.assertEqual(len(client.calls), 1)

    def test_a_shortening_that_invents_a_number_is_rejected(self):
        # The one failure mode that would turn shortening into fabrication.
        result, _ = self._condense("The cheapest room is 45,000 won a night.")

        self.assertEqual(result, LONG_RESULT)

    def test_a_shortening_that_is_not_actually_shorter_is_rejected(self):
        result, _ = self._condense(LONG_RESULT + " All three are central.")

        self.assertEqual(result, LONG_RESULT)

    def test_a_truncated_shortening_is_rejected(self):
        result, _ = self._condense("L7 Hongdae is about 95,000 won and")

        self.assertEqual(result, LONG_RESULT)

    def test_an_empty_shortening_is_rejected(self):
        result, _ = self._condense("   ")

        self.assertEqual(result, LONG_RESULT)


class FailureSafetyTests(unittest.TestCase):
    def test_a_model_failure_leaves_the_verified_result_untouched(self):
        client = FakeClient("", error=RuntimeError("offline"))
        condenser = AnswerCondenser(client, "qwen3:8b")

        result = condenser.condense(LONG_RESULT, max_words=45, max_sentences=2)

        self.assertEqual(result, LONG_RESULT)


if __name__ == "__main__":
    unittest.main()
