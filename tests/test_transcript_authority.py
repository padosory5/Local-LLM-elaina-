"""What was just said beats what was heard before.

B-59, from session 3. "University" was misheard as "universe" once; the
user asked again, correctly transcribed, and the router put the error back:

    You said: Do you remember what kind of university I'm going to?
    [Router] Interpreted transcript as: Do you remember what kind of
             universe I'm going to?
    [Recall] Set aside 5 memory item(s) unrelated to 'Universe'.

The model reads recent turns, so it reproduced its own earlier mishearing
over the correction. Memory was then searched for the wrong thing, and she
said she did not remember.

This is the project's recurring rule in a new layer -- what the person just
said outranks anything held from before, including the model's memory of
mishearing it. The paraphrase may still reword freely; it may not swap a
word for a near-miss of one the transcript actually contains.
"""

import unittest

from brain.intent_router import _restore_misheard_words


class TheTranscriptWinsTests(unittest.TestCase):

    def test_the_live_case(self):
        self.assertEqual(
            _restore_misheard_words(
                "Do you remember what kind of universe I'm going to?",
                "Do you remember what kind of university I am going to?",
            ),
            "Do you remember what kind of university I'm going to?",
        )

    def test_capitalisation_is_kept(self):
        self.assertEqual(
            _restore_misheard_words("Universe", "university"), "University",
        )

    def test_ordinary_paraphrase_is_untouched(self):
        # A paraphrase is allowed to reword. Only a near-miss of a word the
        # transcript actually holds is put back.
        for paraphrase, transcript in (
            ("Find a hotel in Guam", "book me a place in Guam"),
            ("open Spotify", "can you open Spotify for me"),
            ("Explain the voice input flow",
             "Inspect the codebase and explain how voice input reaches chat"),
            ("current time in Seattle", "what time is it over there in Seattle"),
            ("Marathon official release date", "When was Marathon released?"),
            ("guitar prices", "What is the price of that guitar?"),
        ):
            with self.subTest(paraphrase=paraphrase):
                self.assertEqual(
                    _restore_misheard_words(paraphrase, transcript),
                    paraphrase,
                )

    def test_an_empty_transcript_changes_nothing(self):
        self.assertEqual(
            _restore_misheard_words("anything at all", ""), "anything at all",
        )

    def test_short_words_are_left_alone(self):
        # Three characters is the floor: "an"/"in"/"on" are near-misses of
        # each other and rewriting them would be noise.
        self.assertEqual(
            _restore_misheard_words("put it on the list", "put it in the list"),
            "put it on the list",
        )


if __name__ == "__main__":
    unittest.main()
