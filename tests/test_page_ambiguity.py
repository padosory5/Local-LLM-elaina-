"""Choosing between what she found is not a new command.

The session-13 run, and this is the whole of it:

    You said: click about on this page
    Elaina:   I found more than one about item -- ABOUT, ABOUT.
              Which one do you mean?
    You said: the first one
    [Reference] 'one of those' -> 'ABOUT'
    [Computer Control] open_search target=ABOUT
    -> https://www.google.com/search?q=ABOUT

Twice more, with Email and with Calendar. A selection between page
elements was being read as a fresh request, its label sent to Google.

Two things were wrong. The ambiguity kept labels rather than identities,
so "the first one" had nothing to resolve *to* -- and on that page both
labels were the word ABOUT, which no amount of label matching can tell
apart. And the answer was handed to the generic router, which does what
it does with a bare noun.

So the ambiguity is a record: the operation, the element as it was asked
for, the candidates with their ids and where each one sits, and the page
they belong to. A choosing turn fills its empty slot, and the same action
runs on the element that was picked.
"""

from __future__ import annotations

import unittest

from brain.browser_interaction import AmbiguousPageAction, Candidate


def _about_page(count: int = 2) -> AmbiguousPageAction:
    """The ISS case: identical labels, told apart only by where they sit."""
    return AmbiguousPageAction(
        operation="click_element",
        requested_label="about",
        candidates=tuple(
            Candidate(
                element_id=f"e{index}", label="ABOUT", order=index,
                where="the page navigation" if index == 1 else "the main content",
            )
            for index in range(1, count + 1)
        ),
        tab_index=0, tab_identity="hwnd:852794", scan_id="scan-1",
        page_url="https://iss.washington.edu", goal="click about on this page",
    )


class ACandidateHasAnIdentityTests(unittest.TestCase):

    def test_a_position_selects_the_element_not_the_label(self):
        standing = _about_page()
        for said, element in (
            ("the first one", "e1"),
            ("the second one", "e2"),
            ("first", "e1"),
            ("the last one", "e2"),
            ("number two", "e2"),
        ):
            with self.subTest(said=said):
                chosen = standing.choose(said)
                self.assertIsNotNone(chosen, said)
                self.assertEqual(chosen.element_id, element, said)

    def test_the_middle_of_three(self):
        standing = _about_page(3)
        self.assertEqual(standing.choose("the middle one").element_id, "e2")
        self.assertEqual(
            standing.choose("the one on the middle").element_id, "e2",
        )

    def test_leaving_the_choice_to_her_is_still_a_choice(self):
        # Measured live: "click any of them" became open_search Email.
        standing = _about_page()
        for said in (
            "click any of them", "any one", "either", "whichever",
            "one of them", "you choose", "it doesn't matter",
        ):
            with self.subTest(said=said):
                chosen = standing.choose(said)
                self.assertIsNotNone(chosen, said)
                self.assertEqual(chosen.element_id, "e1", said)

    def test_naming_a_distinct_label_picks_it(self):
        standing = AmbiguousPageAction(
            operation="click_element", requested_label="calendar",
            candidates=(
                Candidate("c1", "Academic Calendar", 1),
                Candidate("c2", "Events Calendar", 2),
            ),
        )
        self.assertEqual(
            standing.choose("the Events Calendar one").element_id, "c2",
        )

    def test_identical_labels_cannot_be_chosen_by_name(self):
        # The ISS case. Saying "ABOUT" back does not disambiguate ABOUT
        # from ABOUT, and pretending it does picks one at random.
        self.assertIsNone(_about_page().choose("ABOUT"))

    def test_a_turn_that_chooses_nothing_chooses_nothing(self):
        standing = _about_page()
        for said in (
            "no, open naver.com", "what is this page about?",
            "never mind", "the fifth one",
        ):
            with self.subTest(said=said):
                self.assertIsNone(standing.choose(said), said)


class TheQuestionCanBeAnsweredTests(unittest.TestCase):
    """"I found more than one about item -- ABOUT, ABOUT" gives a person
    nothing to choose with."""

    def test_identical_labels_are_told_apart_by_place(self):
        asked = _about_page().question()

        self.assertIn("two about links", asked)
        self.assertIn("the page navigation", asked)
        self.assertIn("the main content", asked)
        self.assertIn("Which one", asked)

    def test_distinct_labels_are_simply_listed(self):
        asked = AmbiguousPageAction(
            operation="click_element", requested_label="calendar",
            candidates=(
                Candidate("c1", "Academic Calendar", 1),
                Candidate("c2", "Events Calendar", 2),
            ),
        ).question()

        self.assertIn("Academic Calendar", asked)
        self.assertIn("Events Calendar", asked)

    def test_three_identical_labels_still_read_as_a_sentence(self):
        asked = _about_page(3).question()

        self.assertIn("three about links", asked)
        self.assertIn(", and ", asked)


class TheChoiceResumesTheSameActionTests(unittest.TestCase):

    def _engine(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._page_choice = _about_page()
        engine._last_computer_action = "browser_action"
        engine._last_computer_goal = "about"
        return engine

    def test_it_never_becomes_a_search(self):
        engine = self._engine()
        try:
            routing = engine._route_turn("the first one", timings={})
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "browser_action")
        self.assertNotEqual(routing.route.computer_operation, "open_search")

    def test_it_carries_the_chosen_element_to_the_page(self):
        engine = self._engine()
        try:
            routing = engine._route_turn("the second one", timings={})
            prepared = routing.approved_computer_action
        finally:
            engine.close()

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.target, "e2")
        self.assertEqual(prepared.browser_action, "click")
        self.assertEqual(prepared.tab_index, 0)
        self.assertEqual(prepared.browser_scan_id, "scan-1")

    def test_the_question_is_answered_once(self):
        engine = self._engine()
        try:
            engine._route_turn("the first one", timings={})
            still_asking = engine._page_choice
        finally:
            engine.close()

        self.assertIsNone(still_asking)

    def test_the_standing_action_keeps_what_was_asked_for(self):
        engine = self._engine()
        try:
            engine._route_turn("the first one", timings={})
            standing = engine._browser_interaction
        finally:
            engine.close()

        self.assertEqual(standing.target, "about")
        self.assertEqual(standing.operation, "click_element")
        self.assertEqual(standing.page_url, "https://iss.washington.edu")

    def test_a_new_errand_retires_the_question(self):
        engine = self._engine()
        try:
            engine._route_turn("open naver.com", timings={})
            still_asking = engine._page_choice
        finally:
            engine.close()

        self.assertIsNone(still_asking)

    def test_a_pending_choice_is_never_a_web_search(self):
        # The direct guard on the layer that did it. While elements are
        # waiting to be chosen between, no turn becomes open_search.
        from brain.intent_router import IntentDecision

        engine = self._engine()
        try:
            route = engine._resolve_named_choice(
                IntentDecision(
                    intent="computer_action", confidence=1.0,
                    normalized_request="the first one",
                    computer_operation="browser_action",
                    action_target="the first one", reason="t",
                ),
                "the first one",
            )
        finally:
            engine.close()

        self.assertNotEqual(route.computer_operation, "open_search")


if __name__ == "__main__":
    unittest.main()
