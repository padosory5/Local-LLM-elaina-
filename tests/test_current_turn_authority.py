"""What the person just said outranks everything held from before.

Not a bug report -- a principle, written down because it has been broken
in four different layers across two dogfooding sessions, each time by a
different mechanism, and each time the symptom was that an explicit
request quietly became something else.

    B-33  a pending web-search offer replaced "use my browser control,
          go to Zelo.com" with its own stored goal
    B-35  the same, so a promised browser action was never dispatched
    B-52  the same, so "can you find the place of that name" re-ran the
          yes/no question the user was contradicting
    B-36  "one of those websites" was taken literally instead of being
          resolved, and B-03's "no Zillow" was read as an exclusion
    B-28  an anchor set by one correction rode into every later query
    B-42  the same anchor, in an unrelated internship search

Three things may be carried into a turn -- a generic phrase's referent,
task state, a standing offer -- and none of them may overrule what the
turn itself says. This asserts that at every layer that has broken it.
"""

import unittest

from brain import browser_progress
from brain import conversation_focus
from brain import recommendation_state as state
from brain.recommendation import reads_as_clear_acceptance


class AStandingOfferNeverReplacesARequestTests(unittest.TestCase):

    def test_an_instruction_naming_its_own_errand_is_not_consent(self):
        for said in (
            "So use my browser control, go to Zelo.com, search up apartments "
            "near University of Washington.",
            "Use browser control and then show me a sturdy box.",
            "But I did go to a casino there with my friends. Can you find "
            "the place of that name?",
            "open Spotify for me",
            "search for the one on the island instead",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said), said)

    def test_bare_assent_still_accepts(self):
        # The other direction matters just as much: an offer she made must
        # remain acceptable, or every question she asks is unanswerable.
        for said in (
            "yeah", "go ahead", "sure, do that", "yes please",
            "search for some", "I'm ready to start", "ok do it",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_clear_acceptance(said), said)


class AGenericPhraseNeverOverridesANameTests(unittest.TestCase):

    LISTED = "You could try Karrot, Bunjang, Joonggonara, or Hello Market."

    def test_a_turn_that_names_its_target_keeps_it(self):
        self.assertEqual(
            browser_progress.resolve_named_choice(
                "open Bunjang for me", said_before=self.LISTED,
            ),
            "",
        )

    def test_only_a_pointer_is_resolved(self):
        self.assertTrue(
            browser_progress.resolve_named_choice(
                "open one of those", said_before=self.LISTED,
            )
        )

    def test_a_named_app_is_not_re_resolved_by_role(self):
        from tools.computer_control.windows_app_catalog import WindowsAppCatalog

        self.assertEqual(
            WindowsAppCatalog().resolve_running(
                "Whale", running=("Whale", "Chrome"),
            ),
            "",
        )


class StaleStateNeverSteersANewSubjectTests(unittest.TestCase):

    def test_an_anchor_stops_when_the_subject_moves_on(self):
        import time

        focus = conversation_focus.start(now=time.monotonic())
        for said, subject in (
            ("I mean look at Zillow for rental options near University of "
             "Washington", "rental options"),
            ("Also, I'm trying to get some internships in 2027 summer",
             "Internship Preparation"),
        ):
            focus = conversation_focus.update(
                focus, said, subject=subject, now=time.monotonic(),
            )

        self.assertNotIn(
            "zillow", " ".join(focus.query_context()).casefold(),
        )

    def test_an_unrelated_sentence_does_not_join_an_open_task(self):
        problem = state.update(
            state.start("apartments", domain="apartments"),
            "just like a studio, $1000 to $1500",
        )

        for said in (
            "Okay, I searched it up and the phone number is 206-221-7857.",
            "I want to get an internship in summer 2027.",
            "Where can I get an international driving permit?",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    state.about_the_same_thing(problem, said), said,
                )

    def test_a_genuine_refinement_still_joins_it(self):
        problem = state.update(
            state.start("apartments", domain="apartments"),
            "I want to rent a place near UW",
        )

        for said in ("just like a studio", "from $1000 to $1500"):
            with self.subTest(said=said):
                self.assertTrue(
                    state.about_the_same_thing(problem, said), said,
                )


class ADisputeIsCheckedNotRepeatedTests(unittest.TestCase):

    def test_the_query_that_produced_the_claim_is_not_re_run(self):
        from brain.intent_router import IntentDecision
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._router_history.extend([
            {"role": "assistant", "content": (
                "No, there are no casinos in Bainbridge Island."
            )},
        ])
        stale = "Are there any casinos in Bainbridge Island near Seattle?"

        escalated = engine._escalate_disputed_claim(
            IntentDecision(
                intent="web_search", confidence=1.0,
                normalized_request=stale, search_query=stale, reason="t",
            ),
            "But I did go to a casino there with my friends.",
        )

        self.assertNotEqual(escalated.search_query, stale)


if __name__ == "__main__":
    unittest.main()
