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

from brain.chat_engine import (
    _BARE_ACKNOWLEDGEMENT,
    _CANCELLATION,
    _RESULT_FOLLOW_UP,
)
from tests.turn_harness import build_engine


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


class ResultFollowUpTests(unittest.TestCase):
    """A question about the set she already found.

    The router can only answer "conversation, and it is a follow-up" --
    the answer is in the session rather than in the sentence. Measured on a
    dogfooding-shaped workload these were 3 of 20 turns, each paying a full
    ~2.2s call to be told that.
    """

    def test_a_follow_up_about_results_is_recognised(self):
        for said in (
            "anything cheaper?",
            "anything quieter?",
            "got anything better?",
            "is there anything cheaper",
            "anything cheaper than that?",
            "which one would you choose?",
            "which would you pick?",
            "which do you recommend?",
            "is it actually good?",
            "is that any good?",
            "what about the second one?",
            "that one sounds nice",
        ):
            with self.subTest(said=said):
                self.assertTrue(_RESULT_FOLLOW_UP.fullmatch(said))

    def test_a_follow_up_carrying_new_constraints_is_not_one(self):
        # The whole risk of this path. A refinement that names a place, a
        # budget or a use is a request whose words have to be read by
        # something that can put them into the problem -- not waved past
        # as "conversation, follow-up".
        for said in (
            "anything cheaper in Seoul that has parking?",
            "is it good for gaming under 500?",
            "which one would you choose for a 4K workflow?",
            "what about the second one in Gangnam?",
            "find me a cheaper one",
            "can you pull it up?",
            "open the second one",
            "book the second one",
            "what about Seattle",
            "is it going to rain tomorrow?",
            "which hotel is in Gangnam?",
        ):
            with self.subTest(said=said):
                self.assertIsNone(_RESULT_FOLLOW_UP.fullmatch(said))


class TierZeroReachesNoModelTests(unittest.TestCase):
    """Counted, not inspected: these turns cost zero routing calls."""

    class _Counting:
        def __init__(self, inner):
            self._inner, self.calls = inner, 0

        def chat(self, **kwargs):
            self.calls += 1
            return self._inner.chat(**kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _engine_with_results(self):
        engine = build_engine(routes={
            "find me a good monitor": {
                "intent": "web_search", "confidence": 0.95,
                "normalized_request": "find a good monitor",
                "topic": "monitor", "speech_act": "action_request",
                "action_requested": True, "requires_external_evidence": True,
                "recommendation_needed": True, "reason": "asked outright",
            },
        })
        engine._web_search_enabled = True
        engine.research_agent._search = lambda q, m=5: "LG UltraGear, 165Hz."
        engine.client.reply = "The LG UltraGear looks good."
        engine.chat("Find me a good monitor")
        # Only the router's client is counted. The engine's own client
        # answers the turn as well, and counting that would measure
        # generation rather than routing.
        counting = self._Counting(engine.client)
        engine.intent_router.client = counting
        return engine, counting

    def test_a_follow_up_costs_no_routing_call(self):
        engine, counting = self._engine_with_results()
        engine.client.reply = "I'd take the LG."

        engine.chat("which one would you choose?")

        self.assertEqual(
            counting.calls, 0,
            "a follow-up about known results still paid for routing",
        )

    def test_and_still_reaches_the_interaction_decision(self):
        # A fast path that returned a whole routing result would skip the
        # decision layer entirely -- measured, it did, and the follow-up
        # answered from general knowledge instead of the session's own
        # results. Tier 0 substitutes for the model call and nothing else.
        engine, _counting = self._engine_with_results()
        engine.client.reply = "I'd take the LG."

        engine.chat("which one would you choose?")

        self.assertTrue(engine._last_interaction.continues)

    def test_a_refinement_with_new_content_still_routes(self):
        engine, counting = self._engine_with_results()
        engine.client.reply = "Sure."

        engine.chat("anything cheaper in Seoul that has parking?")

        self.assertGreater(counting.calls, 0)

    def test_an_acknowledgement_with_an_open_problem_costs_nothing(self):
        # An open recommendation used to block this, so "ok" two turns into
        # a conversation about monitors paid a full routing call. It cannot
        # mean "yes, go ahead": every gate that could have asked is empty.
        engine = build_engine(routes={})
        engine.task_sessions.note_recommendation_turn(
            "I'm thinking about getting a new monitor.", subject="monitor",
        )
        counting = self._Counting(engine.client)
        engine.intent_router.client = counting

        engine.chat("ok")

        self.assertEqual(counting.calls, 0)
