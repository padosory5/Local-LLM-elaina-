"""The time somewhere else is arithmetic, not a guess.

B-22 and B-24, from the first dogfooding session:

    User:   Tell me the time in Seattle right now.
    Elaina: The time in Seattle right now is 07:57 PM on Wednesday,
            September 02, 2026.
    User:   That's not the time in Seattle right now.
    Elaina: It's 3:45 PM in Seattle right now.

07:57 PM was Korea, where the user was. 3:45 PM was invented -- and the
correction after it was invented too. The prompt held one unlabelled
clock:

    The current local time is 07:57 PM.

No zone, no offset, nothing to convert from. An 8B model asked to do
timezone arithmetic against that is being asked to guess.

The date answer that followed happened to be right -- Seattle was still
on September 2 at 03:57 -- but by luck: the same reasoning that produced
3:45 PM produced it.
"""

import re
import unittest
from datetime import datetime, timezone

from brain import world_clock


class ReadingThePlaceTests(unittest.TestCase):

    def test_the_place_a_time_question_names(self):
        for said, place in (
            ("Tell me the time in Seattle right now.", "seattle"),
            ("Can you tell me the date in Seattle right now?", "seattle"),
            ("what time is it in New York", "new york"),
            ("What is the time in Tokyo?", "tokyo"),
            ("what time is it in London right now", "london"),
            ("the time in South Korea", "south korea"),
            ("what time is it in Los Angeles", "los angeles"),
        ):
            with self.subTest(said=said):
                self.assertEqual(world_clock.read_place(said), place)

    def test_a_question_naming_nowhere_names_nowhere(self):
        for said in (
            "Tell me the time",
            "what time is it",
            "what is the date today",
            "Nice weather today",
            "how long until the meeting",
        ):
            with self.subTest(said=said):
                self.assertEqual(world_clock.read_place(said), "")

    def test_a_place_needs_a_preposition_to_be_read_as_one(self):
        # The whole sentence is not scanned for city names: too many of
        # them are ordinary words and surnames.
        self.assertEqual(world_clock.read_place("Nice is lovely"), "")

    def test_the_live_search_phrasing_resolves(self):
        # B-23's turn. "of" carries the place here and nothing else does.
        self.assertEqual(
            world_clock.read_place("Search me the current time of Seattle."),
            "seattle",
        )

    def test_a_school_named_after_a_place_is_not_that_place(self):
        # "University of Washington" is in Seattle. Reading its "of" as a
        # locative would answer with Washington DC, three zones away --
        # and this user is moving to that exact school.
        self.assertEqual(
            world_clock.read_place(
                "what time is it at the University of Washington",
            ),
            "",
        )


class ComputingTheTimeTests(unittest.TestCase):

    def test_seattle_is_resolved_and_is_not_the_local_clock(self):
        found = world_clock.clock_in("seattle")

        self.assertIsNotNone(found)
        zone, moment = found
        self.assertEqual(zone, "America/Los_Angeles")
        self.assertIsNotNone(moment.tzinfo)

    def test_the_offset_is_the_real_one(self):
        # Anchored against UTC rather than against a hard-coded hour, so
        # this keeps working across daylight saving.
        _zone, seattle = world_clock.clock_in("seattle")
        _zone, seoul = world_clock.clock_in("seoul")
        now = datetime.now(timezone.utc)

        self.assertLess(abs((seattle - now).total_seconds()), 5)
        self.assertLess(abs((seoul - now).total_seconds()), 5)
        # Seoul is always ahead of Seattle, by 16 or 17 hours.
        gap = (
            seoul.utcoffset().total_seconds()
            - seattle.utcoffset().total_seconds()
        ) / 3600
        self.assertIn(gap, (16.0, 17.0))

    def test_an_unknown_place_resolves_to_nothing(self):
        self.assertIsNone(world_clock.clock_in("atlantis"))
        self.assertEqual(world_clock.describe("atlantis"), "")

    def test_the_description_states_a_time_and_a_date(self):
        line = world_clock.describe("seattle")

        self.assertIn("Seattle", line)
        self.assertIn("America/Los_Angeles", line)
        self.assertRegex(line, r"\d{2}:\d{2} (?:AM|PM)")
        self.assertRegex(line, r"\w+ \d{2}, \d{4}")


class InThePromptTests(unittest.TestCase):
    """The context the model is actually handed.

    Two clocks are only two clocks when they are in different places.
    These were written on a machine set to Korean time, and read "here"
    off the host, so on a machine in Seattle they asked whether Seattle
    differs from Seattle and failed on an answer that was right. Which
    zone counts as here is stated below instead, through the
    ``time.timezone`` setting config.yaml has always offered.
    """

    # Asia/Seoul is a fixed +09:00 that never observes daylight saving,
    # so it is never within half an hour of America/Los_Angeles: the gap
    # is always 16 or 17 hours, and always in the same direction.
    HERE = "Asia/Seoul"

    def _engine(self, zone=HERE):
        """An engine whose idea of "here" does not come from the host.

        ``build_engine`` deep-copies the config, so a zone pinned here
        cannot reach another test. Passing ``zone=""`` leaves the
        machine's own clock in place, which is the shipped default.
        """
        from tests.turn_harness import build_engine

        engine = build_engine()
        if zone:
            engine.config.data.setdefault("time", {})["timezone"] = zone
        return engine

    def test_the_local_clock_now_says_which_zone_it_is(self):
        # Unpinned: the machine's own zone, whatever it is. That is what
        # ships, so it is what this one covers.
        context = self._engine(zone="").build_time_context()

        self.assertRegex(context, r"UTC[+-]\d{4}")

    def test_a_configured_zone_is_the_clock_the_prompt_states(self):
        # Asia/Seoul is +09:00 the whole year round.
        context = self._engine().build_time_context()

        self.assertIn("UTC+0900", context)

    def test_a_question_about_elsewhere_carries_that_clock(self):
        context = self._engine().build_time_context(
            "Tell me the time in Seattle right now.",
        )

        self.assertIn("Seattle", context)
        self.assertIn("America/Los_Angeles", context)

    def test_the_two_clocks_are_not_the_same_number(self):
        # The failure exactly: the local time was offered as Seattle's.
        context = self._engine().build_time_context(
            "Tell me the time in Seattle right now.",
        )
        times = re.findall(r"\d{2}:\d{2} (?:AM|PM)", context)

        self.assertEqual(len(times), 2)
        self.assertNotEqual(times[0], times[1])

    def test_one_clock_asked_about_itself_is_said_to_be_the_same_time(self):
        # Asked about Seattle from Seattle there is no difference to
        # state, and "0 hours behind" would be a strange way to say so.
        # Real behaviour on any machine already in the zone the question
        # names -- and until now it had no test of its own: it showed up
        # only as the three tests above failing on a correct answer.
        context = self._engine(
            zone="America/Los_Angeles",
        ).build_time_context("Tell me the time in Seattle right now.")
        times = re.findall(r"\d{2}:\d{2} (?:AM|PM)", context)

        self.assertIn("That is the same time as", context)
        self.assertNotRegex(context, r"\d+ hours? (?:behind|ahead of)")
        # The local label that finishes the sentence is the zone's own
        # name -- "PDT" here, "your local time" when the OS hands over
        # something that is not an abbreviation.
        self.assertEqual(times[0], times[1])

    def test_an_ordinary_time_question_is_unchanged_in_shape(self):
        context = self._engine().build_time_context("what time is it")

        self.assertIn("The current local time is", context)
        self.assertNotIn("it is now", context)


class NotSentToTheWebTests(unittest.TestCase):
    """B-23. A conversion is arithmetic; it does not need a search."""

    def test_a_named_place_keeps_a_time_question_local(self):
        from brain.intent_router import IntentDecision, SemanticIntentRouter

        decision = IntentDecision(
            intent="time_question",
            confidence=1.0,
            normalized_request="Search me the current time of Seattle in",
            reason="test",
            requires_external_evidence=True,
        )

        result = SemanticIntentRouter._apply_factual_source_policy(decision)

        self.assertEqual(result.intent, "time_question")

    def test_a_genuinely_external_time_value_still_searches(self):
        from brain.intent_router import IntentDecision, SemanticIntentRouter

        decision = IntentDecision(
            intent="time_question",
            confidence=1.0,
            normalized_request="what time does the game start tonight",
            reason="test",
            requires_external_evidence=True,
        )

        result = SemanticIntentRouter._apply_factual_source_policy(decision)

        self.assertEqual(result.intent, "web_search")


if __name__ == "__main__":
    unittest.main()
