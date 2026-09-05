"""The requested page action, kept whole from the request to the report.

The browser acceptance run left direct navigation healthy and page
interaction in pieces. Four failures, one shape each, all from the same
session:

    You said: Can you click calendar on this webpage?
    direct target: 'calendar on this webpage'   -> not found

    click calendar -> direct_target_ambiguous
    Elaina: I couldn't get actual listing names out of that search --
            want me to open it in the browser and read them off?

    click about on this page ... You said: Yes. ... try again
    planner target: 'Yes.'

    retry -> two clicks, several describe_page rounds, status=done,
             a summary of the ISS page, and no evidence About was clicked

An element is not the words locating the page. A machine result is not
rewritten by a layer that thinks it was a search. An acknowledgement is
not a target. And a run that finished is not the request being met.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from brain.browser_interaction import (
    BrowserInteraction,
    strip_surface_context,
)
from brain.browser_action_planner import BrowserActionPlanner


def _conversation(text: str):
    from brain.intent_router import IntentDecision

    return IntentDecision(
        intent="conversation", confidence=0.9,
        normalized_request=text, reason="test",
    )


class TheElementIsNotThePageItIsOnTests(unittest.TestCase):
    """ACTION / ELEMENT / CONTEXT, read off as three things."""

    def test_the_locative_is_not_part_of_the_label(self):
        for said, element in (
            ("click calendar on this webpage", "calendar"),
            ("Can you click calendar on this webpage?", "calendar"),
            ("Can you click about on this page?", "about"),
            ("click Images in here", "Images"),
            ("click the About link on the current screen", "About"),
            ("press Submit in this window", "Submit"),
            ("tap Sign in on that site", "Sign in"),
            ("click Downloads in the browser", "Downloads"),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    BrowserActionPlanner._direct_click_target(said), element,
                )

    def test_a_label_with_no_context_survives_whole(self):
        # The over-correction to watch: "on Google" locates the label, it
        # does not locate the page, and stripping it searches for text
        # that is not on the page.
        for said, element in (
            ("click calendar", "calendar"),
            ("click Sign in on Google", "Sign in on Google"),
            ("click Add to cart", "Add to cart"),
            ("click here", "here"),
            ("click page", "page"),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    BrowserActionPlanner._direct_click_target(said), element,
                )

    def test_the_context_is_kept_not_discarded(self):
        self.assertEqual(
            strip_surface_context("calendar on this webpage"),
            ("calendar", "on this webpage"),
        )

    def test_the_ordinal_path_reads_the_same_locative(self):
        # Two copies of one grammar drift apart. There is one.
        for said, parsed in (
            ("click the first result on this page", (0, "")),
            ("click the second listing on this webpage", (1, "")),
            ("open the first hotel result", (0, "hotel")),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    BrowserActionPlanner._ordinal_result_request(said), parsed,
                )


class TheResultSaysWhatHappenedTests(unittest.TestCase):
    """A browser failure is answered from the browser result."""

    @staticmethod
    def _ambiguous(interaction):
        from brain.chat_engine import ChatEngine

        return ChatEngine._spoken_browser_failure(
            "direct_target_ambiguous", "some planner sentence",
            interaction=interaction,
        )

    def test_nothing_matched(self):
        from brain.chat_engine import ChatEngine

        said = ChatEngine._spoken_browser_failure(
            "direct_target_not_found", "raw",
            interaction=BrowserInteraction(
                operation="click_element", target="Calendar",
            ).finished("not_found"),
        )
        self.assertEqual(
            said, "I couldn't find a Calendar element on this page.",
        )

    def test_several_matched_and_it_says_which(self):
        said = self._ambiguous(
            BrowserInteraction(
                operation="click_element", target="Calendar",
            ).finished(
                "ambiguous", candidates=("Academic Calendar", "Event Calendar"),
            ),
        )
        self.assertIn("more than one Calendar", said)
        self.assertIn("Academic Calendar", said)
        self.assertIn("Which one", said)

    def test_it_never_becomes_search_language(self):
        said = self._ambiguous(
            BrowserInteraction(
                operation="click_element", target="Calendar",
            ).finished("ambiguous"),
        )
        self.assertNotIn("listing names", said)
        self.assertNotIn("search", said.casefold())


class AnAcknowledgementIsNotATargetTests(unittest.TestCase):
    """A bare yes cannot become the thing to do again."""

    @dataclass
    class _Plan:
        status: str = "done"
        interaction: BrowserInteraction | None = None
        steps_taken: tuple = ()
        summary: str = ""
        failure_code: str = ""
        model_rounds: int = 0

    def _engine(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._last_computer_action = "browser_action"
        engine._last_computer_goal = "about"
        engine._browser_interaction = BrowserInteraction(
            operation="click_element", target="about",
            source="click about on this page", tab_identity="hwnd:1",
        ).finished("user_took_over")
        return engine

    def test_it_does_not_overwrite_the_standing_action(self):
        engine = self._engine()
        try:
            engine._remember_browser_interaction(
                "Yes.", plan_result=self._Plan(), requested_goal="Yes.",
            )
            kept = engine._browser_interaction
            goal = engine._last_computer_goal
        finally:
            engine.close()

        self.assertEqual(goal, "about")
        self.assertEqual(kept.target, "about")

    def test_a_retry_repeats_the_action_not_the_utterance(self):
        engine = self._engine()
        try:
            route, _ = engine._rescue_capability_route(
                _conversation("Can you try again?"), "Can you try again?",
            )
        finally:
            engine.close()

        self.assertEqual(route.computer_operation, "browser_action")
        self.assertEqual(route.action_target, "about")

    def test_a_correction_changes_only_the_element(self):
        standing = BrowserInteraction(
            operation="click_element", target="about",
            tab_identity="hwnd:1", page_url="https://iss.washington.edu",
        )
        changed = standing.retargeted("calendar")

        self.assertEqual(changed.target, "calendar")
        self.assertEqual(changed.tab_identity, "hwnd:1")
        self.assertEqual(changed.page_url, "https://iss.washington.edu")
        self.assertEqual(changed.operation, "click_element")


class FinishingIsNotSucceedingTests(unittest.TestCase):
    """A run that stopped cleanly is not the request being met."""

    @dataclass
    class _Plan:
        status: str
        summary: str = ""
        steps_taken: tuple = ()
        failure_code: str = ""
        interaction: BrowserInteraction | None = None
        model_rounds: int = 0
        verified: bool | None = None
        pending: object = None
        clarification: object = None

    def test_a_click_with_no_evidence_is_not_done(self):
        from brain.chat_engine import ChatEngine

        bound = ChatEngine._bind_result_to_request(
            self._Plan(
                "done", summary="Read the page.",
                steps_taken=("Described the page.", "Clicked 'Housing'."),
            ),
            requested_goal="click About on this page",
        )

        self.assertEqual(bound.status, "failed")
        self.assertEqual(bound.failure_code, "request_unsatisfied")

    def test_a_click_with_evidence_stands(self):
        from brain.chat_engine import ChatEngine

        bound = ChatEngine._bind_result_to_request(
            self._Plan(
                "done", summary="Clicked About.",
                steps_taken=("Clicked 'About'.",),
            ),
            requested_goal="click About on this page",
        )

        self.assertEqual(bound.status, "done")

    def test_the_structured_record_is_evidence_too(self):
        from brain.chat_engine import ChatEngine

        bound = ChatEngine._bind_result_to_request(
            self._Plan(
                "done", summary="Clicked.",
                interaction=BrowserInteraction(
                    operation="click_element", target="About",
                ).finished("clicked", resolved="About"),
            ),
            requested_goal="click About on this page",
        )

        self.assertEqual(bound.status, "done")

    def test_a_goal_that_named_no_element_is_left_alone(self):
        # The over-correction: an open-ended browser goal has no single
        # element to check, and must not be failed for lacking one.
        from brain.chat_engine import ChatEngine

        bound = ChatEngine._bind_result_to_request(
            self._Plan("done", summary="Found three rooms."),
            requested_goal="check the rooms on trip.com and tell me the prices",
        )

        self.assertEqual(bound.status, "done")


class ALaunchReportsOnlyThatItOpenedTests(unittest.TestCase):
    """Measured: open_app status=opened, spoken as "Spotify's now playing
    your favorite tunes." Nothing played, and nothing looked."""

    def _engine(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._last_computer_action = "open_app"
        engine._last_computer_goal = "Spotify"
        return engine

    def test_an_activity_claim_is_replaced_with_what_is_known(self):
        engine = self._engine()
        try:
            said = engine._refuse_unobserved_app_activity(
                "Spotify's now playing your favorite tunes.",
            )
        finally:
            engine.close()

        self.assertEqual(said, "Spotify is open.")

    def test_saying_it_is_open_is_left_alone(self):
        engine = self._engine()
        try:
            for said in ("Spotify is open.", "Got it, Spotify is up.",
                         "Spotify is running now."):
                with self.subTest(said=said):
                    self.assertEqual(
                        engine._refuse_unobserved_app_activity(said), said,
                    )
        finally:
            engine.close()

    def test_it_only_applies_to_a_launch(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._last_computer_action = "browser_action"
        try:
            said = engine._refuse_unobserved_app_activity(
                "The page is showing three results.",
            )
        finally:
            engine.close()

        self.assertEqual(said, "The page is showing three results.")


if __name__ == "__main__":
    unittest.main()
