"""Phase 5: what she has learned about the person, and how sure she is.

The case that makes a profile worth having is the one the architecture
started from: "Bang Bang" is IVE's song or Jessie J's, and getting it wrong
plays a stranger. Learning which one is meant turns a coin flip into a
stated assumption the person can correct in four words.
"""

import tempfile
import unittest
from pathlib import Path

from brain.deliberation import ACT, ACT_AND_SAY, ASK, decide, interpret
from brain.deliberation.goal import SOURCE_PROFILE
from brain.deliberation.profile import (
    ARTIST_FOR_TITLE,
    FAVOURITE_TRACK,
    OBSERVED,
    STATED,
    UserProfile,
)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "profile.json"

    def profile(self):
        return UserProfile(path=self.path)

    def test_one_observation_is_not_yet_a_taste(self):
        profile = self.profile()

        profile.observe(FAVOURITE_TRACK, "Bang Bang", source=OBSERVED)

        self.assertIsNone(profile.preferred(FAVOURITE_TRACK))

    def test_a_repeated_observation_becomes_something_to_act_on(self):
        profile = self.profile()

        profile.observe(FAVOURITE_TRACK, "Bang Bang", source=OBSERVED)
        profile.observe(FAVOURITE_TRACK, "Bang Bang", source=OBSERVED)

        self.assertEqual(profile.preferred(FAVOURITE_TRACK).value, "Bang Bang")

    def test_something_said_outright_counts_immediately(self):
        profile = self.profile()

        profile.observe(
            ARTIST_FOR_TITLE, "IVE", key="Bang Bang", source=STATED,
        )

        known = profile.preferred(ARTIST_FOR_TITLE, key="Bang Bang")
        self.assertEqual(known.value, "IVE")
        self.assertEqual(known.because(), "you told me that one")

    def test_a_correction_changes_behaviour_rather_than_averaging_into_it(self):
        profile = self.profile()
        for _ in range(3):
            profile.observe(
                ARTIST_FOR_TITLE, "IVE", key="Bang Bang", source=OBSERVED,
            )

        profile.observe(
            ARTIST_FOR_TITLE, "Jessie J", key="Bang Bang", source=STATED,
        )

        self.assertEqual(
            profile.preferred(ARTIST_FOR_TITLE, key="Bang Bang").value,
            "Jessie J",
        )

    def test_what_is_learned_survives_the_app_closing(self):
        first = self.profile()
        first.observe(FAVOURITE_TRACK, "Bang Bang", source=OBSERVED)
        first.observe(FAVOURITE_TRACK, "Bang Bang", source=OBSERVED)

        self.assertEqual(
            self.profile().preferred(FAVOURITE_TRACK).value, "Bang Bang",
        )

    def test_a_damaged_profile_means_she_knows_nothing_not_that_she_breaks(self):
        self.path.write_text("{ this is not json", encoding="utf-8")

        self.assertEqual(self.profile().known(), ())

    def test_an_unknown_kind_is_refused_rather_than_stored(self):
        profile = self.profile()

        self.assertIsNone(profile.observe("shoe_size", "42"))
        self.assertEqual(profile.known(), ())

    def test_something_plainly_wrong_can_be_dropped(self):
        profile = self.profile()
        profile.observe(
            ARTIST_FOR_TITLE, "IVE", key="Bang Bang", source=STATED,
        )

        profile.forget(ARTIST_FOR_TITLE, key="Bang Bang")

        self.assertIsNone(profile.preferred(ARTIST_FOR_TITLE, key="Bang Bang"))


class ProfileInTheGateTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.profile = UserProfile(
            path=Path(self._directory.name) / "profile.json",
        )

    def test_a_known_artist_settles_a_shared_title_and_is_said_out_loud(self):
        self.profile.observe(
            ARTIST_FOR_TITLE, "IVE", key="Bang Bang", source=STATED,
        )

        decision = decide(
            interpret("Play Bang Bang in Spotify"), profile=self.profile,
        )

        self.assertEqual(decision.action, ACT_AND_SAY)
        self.assertEqual(decision.goal.value("artist"), "IVE")
        self.assertEqual(decision.goal.slots["artist"].source, SOURCE_PROFILE)
        self.assertIn("by IVE", decision.assumption)
        self.assertIn("say the word", decision.assumption)

    def test_an_artist_the_person_named_is_never_overridden(self):
        self.profile.observe(
            ARTIST_FOR_TITLE, "Jessie J", key="Bang Bang", source=STATED,
        )

        decision = decide(
            interpret("Play Bang Bang by IVE in Spotify"), profile=self.profile,
        )

        self.assertEqual(decision.action, ACT)
        self.assertEqual(decision.goal.value("artist"), "IVE")

    def test_a_vague_request_falls_back_to_what_they_play_most(self):
        for _ in range(2):
            self.profile.observe(FAVOURITE_TRACK, "Bang Bang", source=OBSERVED)

        decision = decide(
            interpret("Play some music in Spotify"), profile=self.profile,
        )

        self.assertEqual(decision.action, ACT_AND_SAY)
        self.assertEqual(decision.goal.kind, "play_track")
        self.assertIn("Bang Bang", decision.assumption)

    def test_this_session_outranks_the_profile(self):
        # What she just played is better evidence about now than a habit.
        for _ in range(5):
            self.profile.observe(FAVOURITE_TRACK, "Bang Bang", source=OBSERVED)

        decision = decide(
            interpret("Play some music in Spotify"),
            recent_subject="After LIKE",
            profile=self.profile,
        )

        self.assertIn("After LIKE", decision.assumption)

    def test_knowing_nothing_still_asks(self):
        decision = decide(
            interpret("Play some music in Spotify"), profile=self.profile,
        )

        self.assertEqual(decision.action, ASK)


if __name__ == "__main__":
    unittest.main()
