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

import tempfile
import unittest
from pathlib import Path

from brain import preferences
from brain.deliberation.profile import UserProfile


def _profile():
    return UserProfile(path=Path(tempfile.mkdtemp()) / "profile.json")


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


class ForgettingNothingIsNotAPreferenceCommandTests(unittest.TestCase):
    """The same idiom without "about", found in the A1 dogfood run.

        User:   actually forget the coffee, what's a good movie to watch
                tonight?
        Elaina: I wasn't using the coffee by default anyway.

    Two failures in one line. The bookkeeping was said out loud, and the
    question the person actually asked was never answered, because the
    preference layer had already taken the turn.

    "Stop using Naver Maps" says "using" and is about a default whether or
    not one is saved. A bare "forget X" is the same words a person uses to
    drop a subject, so when nothing was saved, nothing about preferences
    was said.
    """

    def test_forgetting_something_never_saved_leaves_the_turn_alone(self):
        spoken = preferences.apply(
            _profile(),
            preferences.read(
                "actually forget the coffee, what's a good movie to watch "
                "tonight?"
            ),
        )

        self.assertEqual(spoken, "")

    def test_stop_using_still_says_it_was_never_the_default(self):
        spoken = preferences.apply(
            _profile(),
            preferences.read("Stop using Naver Maps by default."),
        )

        self.assertIn("wasn't using", spoken.casefold())

    def test_the_two_readings_are_told_apart_by_the_word_using(self):
        self.assertTrue(
            preferences.read("stop using Naver").names_a_default
        )
        self.assertFalse(
            preferences.read("forget Google Maps").names_a_default
        )
