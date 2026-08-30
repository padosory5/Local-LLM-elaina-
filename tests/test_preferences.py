"""What this person usually wants used, and how sure she is allowed to be.

The whole risk here is asymmetric. Reading "use Naver Maps for this
search" as a standing order changes what she does permanently, invisibly,
and in a way nobody asked for; reading "use Naver Maps whenever I ask for
restaurants" as a one-off costs one repeated instruction. So the tests
below spend most of their attention on what must *not* be remembered.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain import preferences
from brain.deliberation import profile as profile_module
from brain.deliberation.profile import (
    FAVOURITE_FOR,
    OBSERVED,
    SOURCE_FOR,
    STATED,
    SUGGESTED,
    TOOL_FOR,
    UserProfile,
    context_key,
)


def _profile():
    directory = tempfile.mkdtemp()
    return UserProfile(path=Path(directory) / "profile.json")


class DurableVersusOneOffTests(unittest.TestCase):
    """The distinction the whole feature turns on."""

    def test_from_now_on_is_durable(self):
        statement = preferences.read(
            "From now on, use Naver Maps whenever I ask for restaurants.",
        )

        self.assertEqual(statement.action, "remember")
        self.assertEqual(statement.domain, "restaurant")
        self.assertEqual(statement.value, "Naver Maps")
        self.assertEqual(statement.source, STATED)

    def test_for_this_search_is_not(self):
        statement = preferences.read("Use Naver Maps for this search.")

        self.assertEqual(statement.action, "override")

    def test_for_this_one_is_not(self):
        statement = preferences.read(
            "Use Google Maps for this one and find sushi.",
        )

        self.assertEqual(statement.action, "override")
        self.assertEqual(statement.value, "Google Maps")

    def test_a_bare_instruction_is_not(self):
        # The commonest case, and the one that must never be learned from.
        statement = preferences.read("Use Naver Maps and find some sushi.")

        self.assertEqual(statement.action, "override")

    def test_usually_is_durable_but_softer(self):
        statement = preferences.read(
            "I usually prefer Naver Maps for restaurants.",
        )

        self.assertEqual(statement.action, "remember")
        self.assertEqual(statement.source, SUGGESTED)

    def test_ordinary_requests_say_nothing_about_preferences(self):
        for text in (
            "Find Korean BBQ near me.",
            "What should I eat for dinner?",
            "Play some music.",
            "I have a sore throat.",
            "Which one would you choose?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(preferences.read(text))


class WhatGetsSavedTests(unittest.TestCase):

    def test_a_durable_statement_is_actionable_at_once(self):
        profile = _profile()

        preferences.apply(profile, preferences.read(
            "From now on, use Naver Maps whenever I ask for restaurants.",
        ))

        found = profile.preferred_in(SOURCE_FOR, "restaurant")
        self.assertIsNotNone(found)
        self.assertEqual(found.value, "Naver Maps")

    def test_a_soft_statement_is_saved_but_not_yet_acted_on(self):
        # "Persistent, but lower confidence than always" -- kept at 1.5,
        # below the acting threshold, until it is said or seen again.
        profile = _profile()

        preferences.apply(profile, preferences.read(
            "I usually prefer Naver Maps for restaurants.",
        ))

        self.assertIsNone(profile.preferred_in(SOURCE_FOR, "restaurant"))
        self.assertTrue(profile.known())

    def test_saying_it_twice_makes_it_actionable(self):
        profile = _profile()
        statement = preferences.read(
            "I usually prefer Naver Maps for restaurants.",
        )

        preferences.apply(profile, statement)
        preferences.apply(profile, statement)

        self.assertIsNotNone(profile.preferred_in(SOURCE_FOR, "restaurant"))

    def test_an_override_writes_nothing(self):
        profile = _profile()

        preferences.apply(profile, preferences.read(
            "Use Google Maps for this one.",
        ))

        self.assertEqual(profile.known(), ())

    def test_one_observation_is_never_a_preference(self):
        # The profile's own rule, restated here because this feature leans
        # on it: a single noticed choice is evidence, not a default.
        profile = _profile()

        profile.observe(SOURCE_FOR, "Yelp", key=context_key("restaurant"))

        self.assertIsNone(profile.preferred_in(SOURCE_FOR, "restaurant"))


class ContextTests(unittest.TestCase):

    def test_a_situation_is_read_as_context(self):
        statement = preferences.read(
            "When my throat hurts, I usually get juk from Bonjuk.",
        )

        self.assertEqual(statement.kind, FAVOURITE_FOR)
        self.assertEqual(statement.context, "throat hurts")
        self.assertEqual(statement.value, "Bonjuk")

    def test_an_asking_clause_is_not_context(self):
        # "whenever I ask for restaurants" says what, not when.
        statement = preferences.read(
            "Use Naver Maps whenever I ask for restaurants.",
        )

        self.assertEqual(statement.context, "")
        self.assertEqual(statement.domain, "restaurant")

    def test_a_recurring_situation_is_durable(self):
        statement = preferences.read(
            "When I work out, use my workout playlist.",
        )

        self.assertEqual(statement.action, "remember")
        self.assertEqual(statement.context, "work out")
        self.assertEqual(statement.value, "workout playlist")

    def test_the_specific_context_beats_the_general_default(self):
        profile = _profile()
        profile.observe(
            FAVOURITE_FOR, "Anywhere",
            key=context_key("restaurant"), source=STATED,
        )
        profile.observe(
            FAVOURITE_FOR, "Bonjuk",
            key=context_key("restaurant", "sore throat"), source=STATED,
        )

        self.assertEqual(
            profile.preferred_in(
                FAVOURITE_FOR, "restaurant", "sore throat",
            ).value,
            "Bonjuk",
        )

    def test_an_unrelated_context_falls_back_to_the_default(self):
        profile = _profile()
        profile.observe(
            SOURCE_FOR, "Naver Maps",
            key=context_key("restaurant"), source=STATED,
        )

        self.assertEqual(
            profile.preferred_in(SOURCE_FOR, "restaurant", "raining").value,
            "Naver Maps",
        )

    def test_a_source_and_a_favourite_coexist(self):
        # How to look and what to look for are different claims.
        profile = _profile()
        profile.observe(
            SOURCE_FOR, "Naver Maps",
            key=context_key("restaurant"), source=STATED,
        )
        profile.observe(
            FAVOURITE_FOR, "Bonjuk",
            key=context_key("restaurant", "sore throat"), source=STATED,
        )

        self.assertEqual(
            profile.preferred_in(SOURCE_FOR, "restaurant").value, "Naver Maps",
        )
        self.assertEqual(
            profile.preferred_in(
                FAVOURITE_FOR, "restaurant", "sore throat",
            ).value,
            "Bonjuk",
        )


class PrecedenceTests(unittest.TestCase):

    def test_this_turn_outranks_what_is_saved(self):
        profile = _profile()
        profile.observe(
            SOURCE_FOR, "Naver Maps",
            key=context_key("restaurant"), source=STATED,
        )

        resolved = preferences.resolve(
            profile, SOURCE_FOR, "restaurant", override="Google Maps",
        )

        self.assertEqual(resolved.choice, "Google Maps")
        self.assertEqual(resolved.source, "current_turn_override")

    def test_an_override_does_not_erase_what_is_saved(self):
        profile = _profile()
        profile.observe(
            SOURCE_FOR, "Naver Maps",
            key=context_key("restaurant"), source=STATED,
        )

        preferences.resolve(
            profile, SOURCE_FOR, "restaurant", override="Google Maps",
        )

        self.assertEqual(
            preferences.resolve(profile, SOURCE_FOR, "restaurant").choice,
            "Naver Maps",
        )

    def test_a_saved_preference_outranks_the_market_default(self):
        profile = _profile()
        profile.observe(
            SOURCE_FOR, "Naver Maps",
            key=context_key("restaurant"), source=STATED,
        )

        resolved = preferences.resolve(
            profile, SOURCE_FOR, "restaurant", default="Diningcode",
        )

        self.assertEqual(resolved.choice, "Naver Maps")
        self.assertEqual(resolved.source, "explicit_user_default")

    def test_the_market_default_is_used_when_nothing_is_saved(self):
        resolved = preferences.resolve(
            _profile(), SOURCE_FOR, "restaurant", default="Diningcode",
        )

        self.assertEqual(resolved.choice, "Diningcode")
        self.assertEqual(resolved.confidence, "low")

    def test_nothing_saved_and_no_default_applies_nothing(self):
        resolved = preferences.resolve(_profile(), SOURCE_FOR, "restaurant")

        self.assertFalse(resolved.applied)


class ReversibilityTests(unittest.TestCase):

    def test_stopping_a_default_removes_it(self):
        profile = _profile()
        preferences.apply(profile, preferences.read(
            "From now on, use Naver Maps whenever I ask for restaurants.",
        ))

        spoken = preferences.apply(
            profile, preferences.read("Stop using Naver Maps by default."),
        )

        self.assertIsNone(profile.preferred_in(SOURCE_FOR, "restaurant"))
        self.assertIn("stop", spoken.casefold())

    def test_stopping_something_never_set_says_so(self):
        spoken = preferences.apply(
            _profile(), preferences.read("Stop using Naver Maps by default."),
        )

        self.assertIn("wasn't using", spoken.casefold())

    def test_switching_outweighs_the_old_one(self):
        # A correction changes behaviour rather than being averaged in.
        profile = _profile()
        profile.observe(
            TOOL_FOR, "YouTube Music",
            key=context_key("music"), source=STATED,
        )

        preferences.apply(profile, preferences.read(
            "Use Spotify instead of YouTube Music from now on.",
        ))

        self.assertEqual(
            profile.preferred_in(TOOL_FOR, "music").value, "Spotify",
        )


class LoggingTests(unittest.TestCase):

    def test_the_block_names_the_grounds(self):
        profile = _profile()
        profile.observe(
            SOURCE_FOR, "Naver Maps",
            key=context_key("restaurant"), source=STATED,
        )

        block = preferences.resolve(
            profile, SOURCE_FOR, "restaurant",
        ).log_block()

        for line in (
            "[Preference Resolution]", "Domain:", "Choice:", "Source:",
            "Confidence:", "Applied:",
        ):
            self.assertIn(line, block)

    def test_an_unapplied_resolution_says_why(self):
        block = preferences.resolve(
            _profile(), SOURCE_FOR, "restaurant",
        ).log_block()

        self.assertIn("Applied: no", block)
        self.assertIn("Why:", block)


if __name__ == "__main__":
    unittest.main()
