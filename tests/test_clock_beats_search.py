"""A clock she can read is not fresh information to go and fetch.

B-57, from session 3. The session started 15:55 KST on September 3, so
Seattle was 11:58 PM on the 2nd, PDT, UTC-7:

    [Router] time_question (1.00): The user is asking for the current time
             in Seattle
    [Interaction] Need: fresh_information
    [Capability] Selected: web_search
    [Tool] Searching web for: What time is it in Seattle now?
    Elaina: It's 2:52 AM in Seattle right now. The time is in Pacific
            Daylight Time, which is one hour behind UTC.

Both wrong, and the second is wrong with confidence.

This is an incomplete fix rather than a new bug. B-22 stopped the *router*
sending a resolvable time question to the web. The interaction layer
decides freshness on its own, using different inputs, and sent it anyway --
so ``world_clock`` was never consulted at all.

One door was guarded and the other was not. The rule belongs where the
decision is made: a time question whose place this machine can resolve is
arithmetic, and arithmetic is never fresh information.
"""

import unittest

from brain.deliberation import interaction


class FakeRoute:
    def __init__(self, **fields):
        self.intent = "time_question"
        self.normalized_request = ""
        self.information_freshness = "live"
        self.requires_external_evidence = False
        self.verification_required = False
        self.is_follow_up = False
        self.action_requested = False
        self.speech_act = "information_request"
        for name, value in fields.items():
            setattr(self, name, value)


class AResolvableClockNeedsNothingFreshTests(unittest.TestCase):

    def test_the_live_turn_does_not_need_a_search(self):
        need = interaction._need_for(
            FakeRoute(normalized_request="What time is it in Seattle now?"), has_usable_context=False,
        )

        self.assertNotEqual(need, interaction.NEED_FRESH)

    def test_every_place_the_clock_knows(self):
        for said in (
            "what time is it in Seattle now?",
            "Can you tell me the date in Tokyo right now?",
            "what time is it in London",
            "the current time of Seattle",
        ):
            with self.subTest(said=said):
                self.assertNotEqual(
                    interaction._need_for(
                        FakeRoute(normalized_request=said),
                        has_usable_context=False,
                    ),
                    interaction.NEED_FRESH,
                    said,
                )

    def test_a_place_it_cannot_resolve_still_searches(self):
        # The half that keeps her honest: if the clock cannot answer it,
        # going and looking is right.
        need = interaction._need_for(
            FakeRoute(normalized_request="what time is it in Atlantis"), has_usable_context=False,
        )

        self.assertEqual(need, interaction.NEED_FRESH)

    def test_a_time_question_that_is_really_about_events_still_searches(self):
        need = interaction._need_for(
            FakeRoute(
                normalized_request="what time does the game start tonight",
            ), has_usable_context=False,
        )

        self.assertEqual(need, interaction.NEED_FRESH)

    def test_other_intents_are_untouched(self):
        need = interaction._need_for(
            FakeRoute(
                intent="web_search",
                normalized_request="what time is it in Seattle now?",
            ), has_usable_context=False,
        )

        self.assertEqual(need, interaction.NEED_FRESH)


if __name__ == "__main__":
    unittest.main()
