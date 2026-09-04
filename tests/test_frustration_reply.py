"""What she says when the person is fed up with her.

B-18. Two insults in the same session, two different answers, both from
whatever the model happened to produce:

    "Fuck you."          -> "I'm sorry you're feeling that way."
    "Okay, you're stupid." -> "You're being rude. I'm here to help, but I
                              can't keep up with your attitude. Let's talk
                              about something real."

The second is a rewrite pass arguing back. Nothing in the system had a
position on this; the first answer was luck and the second was luck the
other way.

The brief for this project is that she should feel like a friend. A
friend who has just got something wrong does not open with the other
person's tone. She takes it, and asks what was actually wanted.

Two constraints keep the policy honest. It only fires when the turn is
*only* frustration -- "just answer my fucking question" carries a real
request and has to be answered, not soothed. And it never repeats itself,
because being told off twice and getting the same sentence back is worse
than the model's version.
"""

import unittest

from brain.social_lines import SocialLineSelector, reads_as_frustration


class ReadingFrustrationTests(unittest.TestCase):

    def test_hostility_aimed_at_her_is_recognised(self):
        for said in (
            "Okay, you're stupid.",
            "you're useless",
            "Fuck you.",
            "you are so dumb",
            "this is stupid, you're terrible at this",
            "shut up",
            "you're wrong",
            "짜증나",
            # Session 5. The expletive sat between the intensifier and the
            # adjective, so nothing matched, and the reply came from the
            # model instead: "I'm here to help, not to be insulted."
            "You're so fucking stupid.",
            "you are absolutely useless",
            "you're such a damn useless assistant",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_frustration(said))

    def test_a_turn_carrying_a_request_is_not_only_frustration(self):
        # The half that matters. These must still reach the router and be
        # answered; a soothing line here is the thing being complained
        # about, one more time.
        for said in (
            "just answer my fucking question",
            "you're stupid, what time is it in Seattle",
            "this is useless, can you search it again",
            "you're wrong, the number is 206-221-7857",
            "why are you repeating my sentence?",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_frustration(said))

    def test_ordinary_negatives_are_not_hostility(self):
        for said in (
            "that restaurant is terrible",
            "the weather is stupid today",
            "No, I can see the images. Thank you.",
            "I'm exhausted",
            "that didn't work",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_frustration(said))


class WhatSheSaysBackTests(unittest.TestCase):

    def _selector(self):
        return SocialLineSelector()

    def test_she_does_not_argue_back(self):
        selector = self._selector()
        for _ in range(40):
            line = selector.frustration().casefold()
            for word in (
                "rude", "attitude", "calm", "respect", "language",
                "unacceptable", "behave", "tone",
            ):
                self.assertNotIn(
                    word, line, f"she argued back: {line!r}",
                )

    def test_she_does_not_repeat_herself(self):
        selector = self._selector()

        lines = [selector.frustration() for _ in range(4)]

        self.assertEqual(len(set(lines)), len(lines))

    def test_every_line_is_short_enough_to_say(self):
        selector = self._selector()
        for _ in range(30):
            line = selector.frustration()
            self.assertTrue(line)
            self.assertLessEqual(len(line.split()), 14, line)

    def test_korean_gets_korean(self):
        selector = SocialLineSelector(language="ko")

        self.assertTrue(selector.frustration())


class InTheTurnTests(unittest.TestCase):
    """It answers without a model call, like the other social paths."""

    def _engine(self):
        from tests.turn_harness import build_engine

        class CountingClient:
            def __init__(self):
                self.calls = 0

            def chat(self, *args, **kwargs):
                self.calls += 1
                raise RuntimeError("no model in this test")

        engine = build_engine()
        client = CountingClient()
        engine.client = client
        engine.intent_router.client = client
        return engine, client

    def test_a_bare_insult_reaches_no_model(self):
        engine, client = self._engine()

        routing = engine._route_turn("Okay, you're stupid.", timings={})

        self.assertEqual(client.calls, 0)
        self.assertTrue(routing.locked_response)
        self.assertNotIn("attitude", routing.locked_response.casefold())

    def test_frustration_carrying_a_request_still_routes(self):
        engine, client = self._engine()

        engine._route_turn("just answer my fucking question", timings={})

        self.assertGreater(
            client.calls, 0,
            "a turn with a real request in it was answered with a platitude",
        )


if __name__ == "__main__":
    unittest.main()
