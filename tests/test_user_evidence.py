"""A grounded claim must not become immune to what the person knows.

B-52, from session 2. The whole flow, not just the first answer:

    Elaina: There are no legal gambling venues in Seattle, Washington.
    User:   Wait, I think I went to a casino in one of the brands of my land.
    Elaina: You mentioned a casino in your land -- if you're looking for
            gambling options in Seattle, there are no legal casinos or
            gambling venues there.
    ...
    User:   But I did go to a casino there with my friends. Can you find
            the place of that name?
    [Router] web_search (0.00): The semantic router confirmed the user's
             permission for the pending agent offer.
    [Router] Interpreted transcript as: Are there any casinos in
             Bainbridge Island near Seattle?
    Elaina: No, there are no casinos in Bainbridge Island. It's a
            residential area with no legal gambling venues.

Three separate things go wrong, and the last is the one that matters.

The turn carries first-hand evidence -- "I did go" -- which is the
strongest thing a person can offer against a claim about the world, and
nothing read it as evidence at all.

It also asks a *different* question: not "are there casinos" but "what was
the place called". A pending offer replaced it with the stored query, so
the same yes/no search ran again.

And so the same answer came back, stated with the same confidence. A
claim that has been searched once must not become unfalsifiable.
"""

import unittest

from brain import grounded_values as gv
from brain.recommendation import reads_as_clear_acceptance


FIRST_HAND = "But I did go to a casino there with my friends. Can you find the place of that name?"


class FirstHandEvidenceIsADisputeTests(unittest.TestCase):
    """Step 2 and 3 of the flow: she must treat this as strong evidence."""

    def test_having_been_there_disputes_a_claim_about_there(self):
        for said in (
            FIRST_HAND,
            "But I went to one there.",
            "I've been to one there",
            "I was there last year",
            "I saw one myself",
            "Wait, I think I went to a casino in one of the brands of my land.",
            "no I definitely went to one",
        ):
            with self.subTest(said=said):
                self.assertTrue(gv.reads_as_dispute(said), said)

    def test_an_ordinary_recollection_is_not_a_dispute(self):
        for said in (
            "I went to Seattle last year",
            "I've been meaning to go",
            "I saw that film",
            "I want to go there someday",
        ):
            with self.subTest(said=said):
                self.assertFalse(gv.reads_as_dispute(said), said)


class ANewQuestionIsNotConsentTests(unittest.TestCase):
    """The audit the brief asks for, in its worst form.

    "Can you find the place of that name?" read as bare consent, so the
    offer's stored query -- the yes/no question she had already answered --
    replaced it.
    """

    def test_the_live_turn_is_not_an_acceptance(self):
        self.assertFalse(reads_as_clear_acceptance(FIRST_HAND))

    def test_a_new_question_carrying_an_act_verb_is_not_consent(self):
        for said in (
            "Can you find the place of that name?",
            "can you look up when it opened",
            "search for the one on the island instead",
            "show me the casinos in Tacoma",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said), said)

    def test_bare_consent_still_consents(self):
        for said in (
            "yeah", "go ahead", "yes please", "sure, do that",
            "yeah look it up", "ok do it", "please do",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_clear_acceptance(said), said)


class TheSearchIsReframedNotRepeatedTests(unittest.TestCase):
    """Step 4: broaden or reframe, rather than repeating the conclusion."""

    def _engine(self):
        from tests.turn_harness import build_engine

        return build_engine()

    def _route(self):
        from brain.intent_router import IntentDecision

        return IntentDecision(
            intent="web_search",
            confidence=1.0,
            normalized_request="Are there any casinos in Bainbridge Island near Seattle?",
            search_query="Are there any casinos in Bainbridge Island near Seattle?",
            reason="test",
        )

    def _disputed(self):
        engine = self._engine()
        engine._router_history.extend([
            {"role": "user", "content": "any casinos in Bainbridge Island?"},
            {"role": "assistant", "content": (
                "No, there are no casinos in Bainbridge Island. It's a "
                "residential area with no legal gambling venues."
            )},
        ])
        return engine._escalate_disputed_claim(self._route(), FIRST_HAND)

    def test_the_stale_query_does_not_survive_the_dispute(self):
        escalated = self._disputed()

        self.assertNotEqual(
            escalated.search_query,
            "Are there any casinos in Bainbridge Island near Seattle?",
            "she re-ran the very question the user was contradicting",
        )

    def test_what_the_turn_actually_asked_is_what_gets_searched(self):
        escalated = self._disputed()

        self.assertIn("casino", escalated.search_query.casefold())

    def test_the_answer_must_be_checked_rather_than_restated(self):
        escalated = self._disputed()

        self.assertTrue(escalated.verification_required)
        self.assertTrue(escalated.requires_external_evidence)

    def test_an_undisputed_turn_keeps_its_query(self):
        engine = self._engine()
        engine._router_history.extend([
            {"role": "assistant", "content": "There are no casinos there."},
        ])
        route = self._route()

        self.assertIs(
            engine._escalate_disputed_claim(route, "thanks, that helps"),
            route,
        )


if __name__ == "__main__":
    unittest.main()
