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


class PointingAtSomethingByWhatItIsNextToTests(unittest.TestCase):
    """People locate things on a page by their neighbours.

    Session 14, and the reason this exists:

        There are two about links on the page -- the first one in the page
        navigation and the second one in the page navigation.

    Which is one description, twice. Both links were in the navigation
    bar, so the thing that was supposed to tell them apart told the person
    nothing at all.
    """

    def test_a_row_of_links_is_described_by_its_neighbours(self):
        from brain.browser_interaction import landmarks_for

        placed = landmarks_for(
            [("e1", "ABOUT", (100, 10, 160, 30)),
             ("e2", "ABOUT", (600, 10, 660, 30))],
            [("Home", (20, 10, 90, 30)),
             ("Services", (680, 10, 780, 30))],
        )

        self.assertEqual(placed["e1"], ("next to", "Home"))
        self.assertEqual(placed["e2"], ("next to", "Services"))

    def test_a_column_uses_above_and_under(self):
        from brain.browser_interaction import landmarks_for

        placed = landmarks_for(
            [("e1", "Apply", (100, 100, 200, 130)),
             ("e2", "Apply", (100, 400, 200, 430))],
            [("Undergraduate", (100, 60, 200, 90)),
             ("Graduate", (100, 360, 200, 390))],
        )

        self.assertEqual(placed["e1"], ("under", "Undergraduate"))
        self.assertEqual(placed["e2"], ("under", "Graduate"))

    def test_a_landmark_every_candidate_shares_distinguishes_none(self):
        # The failure mode being fixed, in its general form: a label both
        # of them sit next to is the same sentence with more words in it.
        from brain.browser_interaction import landmarks_for

        placed = landmarks_for(
            [("e1", "Edit", (100, 10, 160, 30)),
             ("e2", "Edit", (100, 50, 160, 70))],
            [("Menu", (20, 10, 90, 30)), ("Menu", (20, 50, 90, 70))],
        )

        self.assertEqual(placed, {})

    def test_a_neighbour_too_long_to_say_is_not_a_landmark(self):
        from brain.browser_interaction import landmarks_for

        placed = landmarks_for(
            [("e1", "ABOUT", (100, 10, 160, 30))],
            [("Apply for your I-20 well before the deadline",
              (20, 10, 90, 30))],
        )

        self.assertEqual(placed, {})

    def test_something_far_away_is_not_next_to_anything(self):
        from brain.browser_interaction import landmarks_for

        placed = landmarks_for(
            [("e1", "ABOUT", (100, 10, 160, 30))],
            [("Search", (3000, 10, 3100, 30))],
        )

        self.assertEqual(placed, {})

    def test_the_question_points_at_the_neighbours(self):
        asked = AmbiguousPageAction(
            operation="click_element", requested_label="about",
            candidates=(
                Candidate("e1", "ABOUT", 1, relation="next to", near="Home"),
                Candidate("e2", "ABOUT", 2, relation="next to",
                          near="Student Life"),
            ),
        ).question()

        self.assertIn("the one next to Home", asked)
        self.assertIn("the one next to Student Life", asked)

    def test_and_the_neighbour_is_how_it_can_be_answered(self):
        standing = AmbiguousPageAction(
            operation="click_element", requested_label="about",
            candidates=(
                Candidate("e1", "ABOUT", 1, relation="next to", near="Home"),
                Candidate("e2", "ABOUT", 2, relation="next to",
                          near="Student Life"),
            ),
        )

        self.assertEqual(standing.choose("the one next to Home").element_id, "e1")
        self.assertEqual(
            standing.choose("the one next to student life").element_id, "e2",
        )
        # And a position still works, because people say both.
        self.assertEqual(standing.choose("the second one").element_id, "e2")

    def test_the_planner_places_the_candidates_it_found(self):
        from types import SimpleNamespace

        from brain.browser_action_planner import BrowserActionPlanner

        def element(identity, label, rect, main=True):
            return SimpleNamespace(
                id=identity, label=label, rect=rect, in_main=main,
                in_dialog=False, href="",
            )

        matches = [
            element("e1", "ABOUT", (100, 10, 160, 30)),
            element("e2", "ABOUT", (600, 10, 660, 30)),
        ]
        everything = matches + [
            element("n1", "Home", (20, 10, 90, 30)),
            element("n2", "Services", (680, 10, 780, 30)),
        ]

        placed = BrowserActionPlanner._candidates_from(matches, everything)

        self.assertEqual(placed[0].near, "Home")
        self.assertEqual(placed[1].near, "Services")
        self.assertEqual(placed[0].relation, "next to")

    def test_without_geometry_it_falls_back_to_position(self):
        # The CDP observer reports no rectangles. Saying "the first one"
        # is worse than saying "next to Home" and better than crashing.
        from types import SimpleNamespace

        from brain.browser_action_planner import BrowserActionPlanner

        matches = [
            SimpleNamespace(id="e1", label="ABOUT", in_main=False,
                            in_dialog=False, href=""),
            SimpleNamespace(id="e2", label="ABOUT", in_main=True,
                            in_dialog=False, href=""),
        ]
        placed = BrowserActionPlanner._candidates_from(matches, matches)

        self.assertEqual(placed[0].near, "")
        self.assertEqual(placed[0].named(), "the first one in the page navigation")


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

    def test_answering_does_not_use_up_the_answer(self):
        # This assertion used to be the other way round, and the
        # session-14 run showed it was wrong:
        #
        #     You said: the first one.
        #     [Page Action] chose the first one ... 'ABOUT' (78d2c290-e25)
        #     Elaina: Clicked 'ABOUT'.
        #     You said: Can you click the second one?
        #     [Reference] 'one of those' -> 'ABOUT'
        #     [Computer Control] open_search target=ABOUT
        #
        # Having answered "which one?" once, there was nothing left to
        # choose from, so a bare ordinal reached the router and went to
        # Google. The candidates outlive the choice.
        engine = self._engine()
        try:
            engine._route_turn("the first one", timings={})
            routing = engine._route_turn(
                "Can you click the second one?", timings={},
            )
            prepared = routing.approved_computer_action
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "browser_action")
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.target, "e2")

    def test_a_navigation_retires_the_candidates(self):
        # A different page has different elements.
        engine = self._engine()
        try:
            engine._route_turn("open naver.com", timings={})
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
