"""One owner for opening an address.

The session-11 rerun regressed because four layers each thought they
owned it. A domain went to the desktop planner and was searched for among
260 installed applications; an explicit URL went to the browser planner
and was searched for on Google; a complaint about browser control became
a browser goal; and a turn where nothing ran at all reported that a page
had been opened.

    You said: Open no such host.example.
    [Rescue] computer_action/unsupported -> ui_action
    [Computer Control] Cataloged 260 apps.
    open_app target=no such host.example status=not_found

    You said: Use my browser control and open isss.washington.edu.
    ... address bar says google.com
        page says isss.washington.edu - Google Search

    You said: I don't think your browser control is working right now.
    [browser_action target=I don't think your browser control is
     working right now.] -> failed
    Elaina: I'm unable to access or control a browser.

So: an address is opened by the navigation operation. Not by the desktop
planner, not by the browser planner, not by a web search. The planner
still owns what happens *on* the page, which is what it is for.
"""

from __future__ import annotations

import unittest

from brain import browser_navigation
from brain.intent_router import IntentDecision


class ReadingAnAddressTests(unittest.TestCase):
    """A spoken address arrives with spaces in it."""

    def test_the_spaces_a_transcriber_adds_are_closed_up(self):
        for said, expected in (
            ("Open no such host.example.", "nosuchhost.example"),
            ("no such host.example", "nosuchhost.example"),
            ("open nosuchhost.example", "nosuchhost.example"),
            ("open zillow.com", "zillow.com"),
        ):
            with self.subTest(said=said):
                self.assertEqual(browser_navigation.address_in(said), expected)

    def test_an_address_inside_a_sentence_is_taken_alone(self):
        self.assertEqual(
            browser_navigation.address_in(
                "Can you use my browser control and open isss.washington.edu?",
            ),
            "isss.washington.edu",
        )
        self.assertEqual(
            browser_navigation.address_in("what is naver.com about?"),
            "naver.com",
        )

    def test_things_that_are_not_addresses(self):
        # A recognisable tail is required, or a version number and a
        # sentence about a letter both read as hosts.
        for said in (
            "I met only one S", "open Spotify", "version 2.0 is out",
            "make a folder on my Desktop", "close Discord",
        ):
            with self.subTest(said=said):
                self.assertEqual(browser_navigation.address_in(said), "", said)


class TheNavigationOperationOwnsAnAddressTests(unittest.TestCase):

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine()

    def tearDown(self):
        self.engine.close()

    def _rescued(self, said, *, intent="computer_action", operation="unsupported"):
        return self.engine._rescue_capability_route(
            IntentDecision(
                intent=intent, confidence=0.9, normalized_request=said,
                reason="session 11 rerun", computer_operation=operation,
            ),
            said,
        )

    def test_a_domain_never_becomes_an_application(self):
        route, _ = self._rescued("Open no such host.example.")

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "nosuchhost.example")

    def test_an_explicit_url_never_goes_to_the_planner(self):
        route, _ = self._rescued(
            "Use my browser control and open isss.washington.edu.",
        )

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "isss.washington.edu")

    def test_a_page_interaction_still_belongs_to_the_planner(self):
        # The over-correction to watch. Opening a page and doing something
        # on it are two requests, and the planner owns the second -- it can
        # navigate on its way.
        route, _ = self._rescued("open trip.com and check the rooms")

        self.assertEqual(route.computer_operation, "browser_action")

    def test_a_real_application_is_still_an_application(self):
        route, _ = self._rescued("open Spotify")

        self.assertEqual(route.computer_operation, "ui_action")

    def test_doubting_an_ability_is_a_question_not_a_goal(self):
        for said in (
            "I don't think your browser control is working right now.",
            "is your browser control working?",
        ):
            with self.subTest(said=said):
                route, note = self._rescued(
                    said, intent="conversation", operation="none",
                )
                self.assertIn("I do have browser control", note)
                self.assertNotEqual(route.computer_operation, "browser_action")


class ASuccessClaimNeedsAResultTests(unittest.TestCase):
    """S11R-04. She said a page was open on a turn where nothing ran.

        [browser planner] I could not verify ...
        You said: I meant only one this.
        Elaina: I opened isss.washington.edu in your browser.
    """

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine()
        self.engine._last_action_failed = True
        self.engine._last_computer_goal = "isss.washington.edu"

    def tearDown(self):
        self.engine.close()

    def test_a_claim_with_nothing_behind_it_is_removed(self):
        reply = self.engine._refuse_unearned_success(
            "I opened isss.washington.edu in your browser.",
            action_performed=False,
        )

        self.assertNotIn("I opened", reply)
        self.assertIn("didn't go through", reply)

    def test_an_action_that_did_run_this_turn_speaks_normally(self):
        reply = "I opened isss.washington.edu in your browser."

        self.assertEqual(
            self.engine._refuse_unearned_success(reply, action_performed=True),
            reply,
        )

    def test_nothing_is_touched_when_the_last_action_worked(self):
        self.engine._last_action_failed = False
        reply = "I opened isss.washington.edu in your browser."

        self.assertEqual(
            self.engine._refuse_unearned_success(reply, action_performed=False),
            reply,
        )


class ADispatchThatNeverRanIsNotADispatchTests(unittest.TestCase):
    """S11R-01/S11R-10. Four turns of "I sent the browser there".

        [Computer Control] open_url isss.washington.edu status=failed
        Elaina: I sent the browser to isss.washington.edu, but I couldn't
                check whether it loaded.

    The browser layer had already reported that it could not find a window
    to navigate. Not checking and not going are different failures, and
    the person can act on only one of them.
    """

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine()
        self.engine.NAVIGATION_SETTLE_SECONDS = 0
        self.engine.browser_observer.unreadable()

    def tearDown(self):
        self.engine.close()

    def _open(self, target, status):
        from tools.computer_control.computer_control import ComputerActionResult

        self.engine.computer_control.execute = (
            lambda prepared, **kwargs: ComputerActionResult(
                status, target, target,
                "No browser window is open right now.",
                operation="open_url", url=f"https://{target}",
            )
        )
        return self.engine._handle_computer_action(IntentDecision(
            intent="computer_action", confidence=0.99,
            normalized_request=f"open {target}", reason="t",
            computer_operation="open_url", action_target=target,
            computer_url=f"https://{target}", action_requested=True,
        ))

    def test_a_failed_dispatch_says_it_could_not_get_there(self):
        line, result = self._open("isss.washington.edu", "failed")

        self.assertEqual(result.status, "navigation_failed")
        self.assertNotIn("I sent the browser", line)
        self.assertIn("couldn't", line)

    def test_a_real_dispatch_that_could_not_be_read_still_hedges(self):
        # The over-correction to watch: the honest hedge is still the right
        # answer when the browser genuinely went and could not be read.
        line, result = self._open("naver.com", "url_dispatched")

        self.assertEqual(result.status, "url_dispatched")
        self.assertIn("sent the browser", line)


if __name__ == "__main__":
    unittest.main()
