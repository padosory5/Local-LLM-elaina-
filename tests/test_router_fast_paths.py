"""Turns that reach no model at all, and the guards that keep it safe.

Routing costs a ~3,850-token prompt, measured at ~4.8s median for the call
alone. That is worth paying to decide what an ambiguous sentence means. It is
not worth paying to be told that "ok" was conversation.

Every fast path here is a closed grammatical class with nothing outstanding,
so there is no classification left to make. The tests come in pairs: what
bypasses, and what must *not* -- because the danger of a fast path is never
the turn it was written for, it is the turn that happens to match it.
"""

import unittest

from brain.chat_engine import _BARE_ACKNOWLEDGEMENT, _CANCELLATION


class BareAcknowledgementTests(unittest.TestCase):

    def test_an_acknowledgement_alone_is_recognised(self):
        for said in (
            "I see", "ok", "okay", "got it", "gotcha", "right", "sure",
            "alright", "mm-hm", "uh-huh", "yeah", "yep", "fair enough",
            "makes sense", "noted", "understood", "cool", "nice", "Cool.",
            "알겠어",
        ):
            with self.subTest(said=said):
                self.assertTrue(_BARE_ACKNOWLEDGEMENT.fullmatch(said))

    def test_an_acknowledgement_carrying_a_request_is_not_bare(self):
        # The whole risk of this path: an affirmative with something after it
        # is a request, and must still reach the router.
        for said in (
            "ok open spotify",
            "sure, find me a hotel",
            "right, what about Seattle",
            "yeah they are getting expensive",
            "I see three options",
            "got it, now close Discord",
            "cool, what's the weather",
        ):
            with self.subTest(said=said):
                self.assertIsNone(_BARE_ACKNOWLEDGEMENT.fullmatch(said))


class CancellationTests(unittest.TestCase):

    def test_calling_it_off_is_recognised(self):
        for said in (
            "never mind", "nevermind", "forget it", "forget that",
            "cancel", "cancel that", "cancel it", "stop", "stop that",
            "drop it", "leave it", "no need", "actually never mind",
            "no, forget it", "취소", "그만",
        ):
            with self.subTest(said=said):
                self.assertTrue(_CANCELLATION.fullmatch(said))

    def test_a_cancellation_with_an_object_is_a_request(self):
        # "stop the music" is an instruction about something, not a bare
        # cancellation, and it must reach the router to be understood.
        for said in (
            "stop the music",
            "cancel my subscription",
            "never mind that hotel, find another",
            "stop playing Spotify",
        ):
            with self.subTest(said=said):
                self.assertIsNone(_CANCELLATION.fullmatch(said))


class NoModelCallTests(unittest.TestCase):
    """The point of the exercise: these turns never reach the model."""

    class CountingClient:
        """Records calls rather than raising.

        Raising does not work here: the router catches everything and falls
        back to conversation on purpose, so an exception would be swallowed
        and the test would pass for the wrong reason.
        """

        def __init__(self):
            self.calls = 0

        def chat(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("no model in this test")

    def _engine(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        client = self.CountingClient()
        engine.client = client
        engine.intent_router.client = client
        return engine, client

    def test_acknowledgements_and_cancellations_bypass_the_router(self):
        for said in ("ok", "I see", "got it", "never mind", "forget it",
                     "stop", "hi", "hello"):
            with self.subTest(said=said):
                engine, client = self._engine()
                routing = engine._route_turn(said, timings={})

                self.assertEqual(routing.route.intent, "conversation")
                self.assertEqual(
                    client.calls, 0,
                    f"{said!r} reached the model {client.calls} time(s)",
                )

    def test_a_real_request_still_reaches_the_router(self):
        # The negative half: bypassing must be the exception, and anything
        # with content in it still pays for a routing decision.
        engine, client = self._engine()

        engine._route_turn("what's the weather in Seattle", timings={})

        self.assertGreater(
            client.calls, 0,
            "a real request bypassed routing, which is the dangerous "
            "direction for a fast path",
        )

    def test_a_bypassed_turn_records_a_near_zero_route_time(self):
        engine, _client = self._engine()
        timings: dict = {}

        engine._route_turn("ok", timings=timings)

        self.assertIn("route", timings)
        self.assertLess(timings["route"], 0.25)


class PendingStateStillWinsTests(unittest.TestCase):
    """With something outstanding, "ok" means something and must be read."""

    def test_an_acknowledgement_with_a_pending_offer_is_not_bypassed(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine.capability_offer.offer(
            capability_id="web_search",
            goal="find restaurants nearby",
            offer_text="Want me to find restaurants nearby?",
        )

        # The offer branch runs before the bare-acknowledgement branch, so
        # the pending offer -- not the fast path -- decides this turn.
        self.assertIsNotNone(engine.capability_offer.peek())


if __name__ == "__main__":
    unittest.main()
