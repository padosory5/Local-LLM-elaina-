"""What she may claim about a page, and what a complaint about one means.

The browser group from the first dogfooding session -- B-03, B-08, B-17.

B-08. Every step worked, and the answer said the opposite:

    [Browser Planner] round=1 tool=search        status=navigated
    [Browser Planner] round=2 tool=click_element status=clicked
    [Browser Planner] round=3 tool=describe_page status=observed
    [Browser Planner] round=4 tool=read_page_text status=observed
    [Computer Control] status=done rounds=5 failure=(none)

    Elaina: The page is empty except for the Google search bar and
            navigation links. No image results are visible. Please try
            refreshing the page or checking your internet connection.

    User:   No, I can see the images. Thank you.

She had asked for image results, reached them, and then read the page's
*text*. Google Images is nearly textless, so the text read came back
almost empty -- and an empty text read was turned into a claim that the
page was empty. Images are not text; their absence from a text read is
not evidence of anything.

B-17. One turn later, the complaint about a blank tab:

    User:   You're showing me nothing.
    [Rescue] computer_action/unsupported -> computer_action/unsupported
    Elaina: I can't do that one. Right now I can use browser control, web
            search, desktop control, screen vision...

She had just run a browser action. A complaint about the last action's
result is not a new request that happens to be unsupported.

B-03. A misheard name, corrected:

    Elaina: Got it, it up on Zelo is open.
    User:   no Zillow.
    Constraints: ... exclusion=Zillow [utterance]

"No X" is genuinely ambiguous -- it excludes X, or it corrects a name to
X. What separates them is whether she just said something that sounds
like it.
"""

import unittest

from brain import browser_outcome
from brain import recommendation_state as state


class ATextReadSaysNothingAboutImagesTests(unittest.TestCase):

    def test_the_live_request_reads_as_visual(self):
        for goal in (
            "Use my browser control, search up packing peanut, click images and show me.",
            "Can you use my browser control and then show me an image of a packing peanut?",
            "show me a picture of it",
            "pull up the photos",
            "show me the chart",
        ):
            with self.subTest(goal=goal):
                self.assertTrue(browser_outcome.asks_to_see(goal))

    def test_an_ordinary_request_is_not_visual(self):
        for goal in (
            "what does the page say",
            "read me the article",
            "click the sign in button",
            "find the phone number on this page",
            "book a room for September 18",
        ):
            with self.subTest(goal=goal):
                self.assertFalse(browser_outcome.asks_to_see(goal))

    def test_the_live_false_claim_is_recognised(self):
        summary = (
            "The page is empty except for the Google search bar and "
            "navigation links. No image results are visible. Please try "
            "refreshing the page or checking your internet connection."
        )

        self.assertTrue(browser_outcome.denies_visual_content(summary))

    def test_describing_what_is_there_is_not_a_denial(self):
        for summary in (
            "The image results are showing now.",
            "I opened the images tab.",
            "There are twelve results on the page.",
            "The page shows several packing peanut photos.",
        ):
            with self.subTest(summary=summary):
                self.assertFalse(browser_outcome.denies_visual_content(summary))

    def test_a_visual_run_that_worked_reports_what_it_did(self):
        summary = (
            "The page is empty except for the Google search bar. No image "
            "results are visible. Please try refreshing the page."
        )

        corrected = browser_outcome.correct_visual_claim(
            summary,
            goal="search up packing peanut, click images and show me",
            steps_succeeded=True,
        )

        self.assertNotIn("empty", corrected.casefold())
        self.assertNotIn("refresh", corrected.casefold())
        self.assertNotIn("no image", corrected.casefold())

    def test_a_run_that_failed_is_left_to_say_so(self):
        # She must still be able to report a real failure. Only a run whose
        # steps actually worked has its content claim replaced.
        summary = "I couldn't reach the page."

        self.assertEqual(
            browser_outcome.correct_visual_claim(
                summary, goal="show me the images", steps_succeeded=False,
            ),
            summary,
        )

    def test_a_non_visual_run_is_untouched(self):
        summary = "The page is empty except for a search bar."

        self.assertEqual(
            browser_outcome.correct_visual_claim(
                summary, goal="what does the page say", steps_succeeded=True,
            ),
            summary,
        )


class ComplainingAboutTheLastActionTests(unittest.TestCase):
    """B-17. Six real phrasings; the predicate matched one."""

    def test_the_live_complaint_is_recognised(self):
        for said in (
            "You're showing me nothing.",
            "you showed me nothing",
            "nothing is showing",
            "I don't see anything",
            "that showed nothing",
            "why are you not showing me anything",
            "there's nothing there",
            "it didn't show anything",
            "아무것도 안 보여",
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    state.complains_about_missing_results(said), said,
                )

    def test_ordinary_turns_are_not_complaints(self):
        for said in (
            "show me the images",
            "nothing else for now",
            "I don't see the point",
            "that's nothing to worry about",
            "thanks, that's everything",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    state.complains_about_missing_results(said), said,
                )

    def test_a_complaint_is_not_a_fresh_unsupported_request(self):
        from tests.turn_harness import build_engine
        from brain.intent_router import IntentDecision

        engine = build_engine()
        engine._router_history.extend([
            {"role": "user", "content": "show it on my browser"},
            {"role": "assistant", "content": "Sure, new tab opened."},
        ])
        engine._last_computer_action = "browser_action"

        route, note = engine._rescue_capability_route(
            IntentDecision(
                intent="computer_action",
                computer_operation="unsupported",
                confidence=0.95,
                normalized_request="You're showing me nothing.",
                reason="not a grounded Phase 4A action",
            ),
            "You're showing me nothing.",
        )

        self.assertNotIn(
            "can't do that one", str(note).casefold(),
            "a complaint about the last action was refused as a new one",
        )


class NoXAfterASimilarNameTests(unittest.TestCase):
    """B-03. "no Zillow" right after "Zelo is open" corrects the name."""

    def test_a_near_miss_is_read_as_a_correction(self):
        self.assertEqual(
            state.corrects_a_named_surface("no Zillow.", said_before="Got it, it up on Zelo is open."),
            "Zillow",
        )

    def test_an_unrelated_name_is_still_an_exclusion(self):
        self.assertEqual(
            state.corrects_a_named_surface(
                "no spicy food", said_before="Here are some places near you.",
            ),
            "",
        )
        self.assertEqual(
            state.corrects_a_named_surface(
                "no Zillow", said_before="Here are some listings I found.",
            ),
            "",
        )

    def test_nothing_said_before_means_no_correction(self):
        self.assertEqual(
            state.corrects_a_named_surface("no Zillow", said_before=""), "",
        )

    def test_a_correction_does_not_become_an_exclusion(self):
        constraints = state.read_constraints(
            "no Zillow.", said_before="Got it, it up on Zelo is open.",
        )

        self.assertNotIn(
            state.EXCLUSION, {slot.name for slot in constraints},
        )

    def test_the_corrected_request_goes_back_to_the_same_surface(self):
        # The half the bug report called "and failed browser action": the
        # correction is the previous request with the name put right, so it
        # returns to the surface that ran it.
        from tests.turn_harness import build_engine
        from brain.intent_router import IntentDecision

        engine = build_engine()
        engine._router_history.extend([
            {"role": "user", "content": "search it up on Zelo"},
            {"role": "assistant", "content": "Got it, it up on Zelo is open."},
        ])
        engine._last_computer_action = "browser_action"
        engine._last_computer_goal = "rental listings on Zelo"

        route, note = engine._rescue_capability_route(
            IntentDecision(
                intent="computer_action",
                computer_operation="unsupported",
                confidence=0.95,
                normalized_request="no Zillow.",
                reason="not a grounded Phase 4A action",
            ),
            "no Zillow.",
        )

        self.assertEqual(route.computer_operation, "browser_action")
        self.assertIn("Zillow", route.action_target)
        self.assertNotIn("Zelo", route.action_target)
        self.assertEqual(note, "")

    def test_a_real_exclusion_is_unchanged(self):
        constraints = state.read_constraints(
            "nothing spicy", said_before="Here are some places near you.",
        )

        self.assertIn(state.EXCLUSION, {slot.name for slot in constraints})


if __name__ == "__main__":
    unittest.main()
