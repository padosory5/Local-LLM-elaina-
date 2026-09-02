"""A question she asks has to be answerable.

The offer/consent cluster from session 2 -- B-45 and B-33.

B-45. The entity guard removed five unverified landmarks and asked:

    Elaina: I don't want to send you somewhere I haven't checked --
            want me to look up real ones?
    User:   Yeah.
    [Router] conversation (1.00): The user acknowledged the delivered
             task results.
    Elaina: Got it.

    [Timing] route=0.00s

route=0.00s is the bare-acknowledgement fast path, which only fires when
*nothing is outstanding*. Nothing was: the guard wrote the question into
the reply and parked nothing to answer it. Its sibling, the value guard,
has always parked a real offer -- this one never did, and the session-1
work made it fire far more often, which is how it surfaced.

B-33. The opposite failure -- something outstanding that should not have
won:

    User:   So use my browser control, go to Zelo.com, search up
            apartments near University of Washington.
    [Consent Resume] capability: web_search, reused payload: yes
    [Router] web_search (1.00): The user accepted the offered ability.

A turn that names a capability outright is not an acceptance of an older
offer for a different one. What she was asked for this turn outranks what
she offered several turns ago.
"""

import unittest

from tests.turn_harness import build_engine


class AnAskedQuestionIsAnswerableTests(unittest.TestCase):

    def test_the_entity_guard_parks_what_answers_it(self):
        engine = build_engine()
        engine.capability_offer.clear()

        reply = engine._enforce_grounded_entities(
            "You could check out local music stores like Melody House or "
            "Guitar Center Korea.",
            user_input="where can I buy a guitar",
            action_performed=False,
            evidence="nothing relevant",
        )

        self.assertIn("look up real ones", reply)
        self.assertIsNotNone(
            engine.capability_offer.peek(),
            "she asked a question and left nothing to answer it with",
        )

    def test_a_reply_it_leaves_alone_parks_nothing(self):
        engine = build_engine()
        engine.capability_offer.clear()

        engine._enforce_grounded_entities(
            "Try bibimbap tonight.",
            user_input="what should I eat",
            action_performed=False,
            evidence="",
        )

        self.assertIsNone(engine.capability_offer.peek())

    def test_yeah_after_the_offer_is_not_a_bare_acknowledgement(self):
        # The exact failure: with the offer parked, "Yeah." must not take
        # the fast path that answers "Got it." and does nothing.
        engine = build_engine()
        engine.capability_offer.clear()
        engine._enforce_grounded_entities(
            "You could check out local music stores like Melody House.",
            user_input="where can I buy a guitar",
            action_performed=False,
            evidence="nothing relevant",
        )

        routing = engine._route_turn("Yeah.", timings={})

        self.assertNotEqual(
            getattr(routing.route, "reason", ""),
            "A bare acknowledgement with nothing outstanding.",
        )


class ThisTurnOutranksAnOlderOfferTests(unittest.TestCase):
    """B-33. Naming a capability is not accepting a different one."""

    def _with_offer(self):
        engine = build_engine()
        engine.capability_offer.offer(
            capability_id="web_search",
            goal="find studio apartments near UW",
            offer_text="Want me to look that up?",
        )
        return engine

    def test_an_explicit_request_is_not_an_acceptance(self):
        from brain.recommendation import reads_as_clear_acceptance

        for said in (
            "So use my browser control, go to Zelo.com, search up apartments "
            "near University of Washington.",
            "use browser control and open zillow",
            "open Spotify for me",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    reads_as_clear_acceptance(said),
                    "a fresh instruction was read as accepting an old offer",
                )

    def test_a_plain_yes_still_accepts(self):
        from brain.recommendation import reads_as_clear_acceptance

        for said in ("yeah", "yes please", "go ahead", "sure, do that", "ok do it"):
            with self.subTest(said=said):
                self.assertTrue(reads_as_clear_acceptance(said))


if __name__ == "__main__":
    unittest.main()


class PromisedAndNotDoneTests(unittest.TestCase):
    """B-35, which turned out to be B-33 wearing different words.

        User:   Use browser control and then show me a sturdy box.
        [Router] web_search (1.00): The user accepted the offered ability.
        [Tool] Searching web for: Moving and Shipping look at Zillow for
               rental options near University of Washington Seattle
        Elaina: I'll use browser control to show you a sturdy box...

    She said she would use browser control because that is what was asked
    for, and then a pending web-search offer had already replaced the
    request with its own stored goal. No browser action was ever
    dispatched, and the search ran on somebody else's query.
    """

    def test_the_request_is_not_swallowed_by_the_offer(self):
        from brain.recommendation import reads_as_clear_acceptance

        self.assertFalse(
            reads_as_clear_acceptance(
                "Use browser control and then show me a sturdy box. "
                "I don't know what that is"
            )
        )

    def test_the_follow_up_yes_is_an_acceptance(self):
        # "Yeah, do that." was read as acknowledging results she had never
        # produced, and answered "Mm-hm."
        from brain.recommendation import reads_as_clear_acceptance

        self.assertTrue(reads_as_clear_acceptance("Yeah, do that."))

    def test_asking_whether_she_is_doing_it_is_not_consent(self):
        from brain.recommendation import reads_as_clear_acceptance

        self.assertFalse(reads_as_clear_acceptance("Are you doing it?"))
