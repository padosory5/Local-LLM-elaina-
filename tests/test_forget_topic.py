"""Dropping a topic is not forgetting a preference.

B-27, reported in both dogfooding sessions -- session 1 as B-04, and again
in session 2 after that fix, because the fix was in a different layer:

    User:   Okay, forget about my rent. I recently submitted my I-20...
    [Preference Resolution]
      Choice: about my rent. I recently submitted my I-20 ...
      Applied: no
    Elaina: I wasn't using about my rent. I recently submitted my I-20 ...
            by default anyway.

    User:   forget about it since we're talking about cars do you think I
            can rent a car...
    Elaina: I wasn't using about it since we're talking about cars...

The preference reader's forget branch was ``forget\s+(?:my\s+)?(.+)$`` --
greedy to the end of the utterance, so a topic change plus everything the
person said after it became the name of a preference to drop.

Two things separate the readings. "Forget X" names a thing she has been
defaulting to; "forget about X" is the idiom for dropping a subject. And a
preference name is a phrase, so it ends where its sentence does.
"""

import unittest

from brain import preferences


class ForgetAboutIsATopicChangeTests(unittest.TestCase):

    def test_the_live_turns_state_no_preference(self):
        for said in (
            "Okay, forget about my rent. I recently submitted my I-20 to the "
            "University of Washington, but do you think I can get it before "
            "September 13th?",
            "forget about it since we're talking about cars do you think I "
            "can rent a car in Seattle",
            "forget about that",
            "let's forget about the apartment for now",
        ):
            with self.subTest(said=said):
                self.assertIsNone(preferences.read(said), said)

    def test_forgetting_a_named_default_still_works(self):
        for said, value in (
            ("forget Google Maps", "Google Maps"),
            ("forget my Spotify preference", "Spotify preference"),
            ("stop using Naver", "Naver"),
        ):
            with self.subTest(said=said):
                statement = preferences.read(said)
                self.assertIsNotNone(statement, said)
                self.assertEqual(statement.action, "forget")
                self.assertEqual(statement.value, value)

    def test_a_preference_name_ends_with_its_sentence(self):
        statement = preferences.read(
            "forget Google Maps. What's the weather like tomorrow?"
        )

        self.assertIsNotNone(statement)
        self.assertEqual(statement.value, "Google Maps")

    def test_a_bare_pronoun_names_no_preference(self):
        # "forget it" is calling something off, and the cancellation path
        # owns it. It must not arrive here as a preference named "it".
        for said in ("forget it", "forget that", "forget it.", "just forget it"):
            with self.subTest(said=said):
                self.assertIsNone(preferences.read(said), said)


if __name__ == "__main__":
    unittest.main()
