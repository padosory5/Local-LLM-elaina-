import unittest

from brain.chat_engine import _drop_unfinished_sentence


class UnfinishedSentenceTests(unittest.TestCase):
    """A reply cut off by the token budget, spoken aloud mid-sentence.

    Measured live, with Ollama reporting ``done_reason == "length"``:

        "Seattle's a cool place to live -- just don't forget the rain. Need"

    The stop reason was on the final streamed chunk and was being discarded,
    so nothing downstream could tell a finished answer from a severed one.
    """

    def test_the_severed_fragment_is_dropped(self):
        self.assertEqual(
            _drop_unfinished_sentence(
                "Seattle's a cool place to live. Need"
            ),
            "Seattle's a cool place to live.",
        )

    def test_a_finished_answer_is_untouched(self):
        for text in (
            "Done. All set.",
            "Is it far? Probably not.",
            "Sure!",
            'She said "go north."',
        ):
            with self.subTest(text=text):
                self.assertEqual(_drop_unfinished_sentence(text), text)

    def test_a_truncated_first_sentence_is_kept_rather_than_emptied(self):
        # Half an answer still beats silence.
        self.assertEqual(
            _drop_unfinished_sentence("It rains a lot there"),
            "It rains a lot there",
        )

    def test_only_the_last_finished_sentence_survives(self):
        self.assertEqual(
            _drop_unfinished_sentence(
                "Is it far? Maybe. You should probably chec"
            ),
            "Is it far? Maybe.",
        )

    def test_empty_and_blank_are_safe(self):
        self.assertEqual(_drop_unfinished_sentence(""), "")
        self.assertEqual(_drop_unfinished_sentence("   "), "")
        self.assertEqual(_drop_unfinished_sentence(None), "")


if __name__ == "__main__":
    unittest.main()
