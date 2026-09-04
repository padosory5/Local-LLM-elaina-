"""Dispatch is not arrival.

Session 7. ``open_url`` returned ``url_opened`` and every layer above it
read that as "the page the person asked for is on their screen". It never
meant that; it meant Windows accepted the navigation command.

    You said: openZillow.com
    [Computer Control] open_url openZillow.com status=url_opened
    Elaina: All set, openZillow.com is open.
    You said: didn't open it.
    Elaina: Zillow.com is open.
    You said: the website is not opened on my browser.
    Elaina: I can't do that one.

Three claims about one navigation, none of them checked, the second made
after being told the first was wrong, and the third contradicting both.
``openZillow.com`` is not a host anybody owns, so nothing had loaded at
any point.

These are the states a navigation can be in, the line between the ones
that may be spoken as success and the ones that may not, and the two
places an honest recovery candidate can come from.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from brain import browser_navigation as nav
from brain.intent_router import IntentDecision


def _tab(url: str, title: str = "", active: bool = True):
    return SimpleNamespace(index=0, url=url, title=title, is_active=active)


def _route(target: str, url: str = ""):
    return IntentDecision(
        intent="computer_action", confidence=0.99,
        normalized_request=f"open {target}", reason="session 7",
        computer_operation="open_url", action_target=target,
        computer_url=url or f"https://{target}", action_requested=True,
    )


class WhereTheNavigationGotToTests(unittest.TestCase):

    def test_the_requested_site_is_the_only_success(self):
        started = nav.start("zillow.com", "https://zillow.com")

        looked = nav.verify(started, (_tab("https://www.zillow.com/homes/", "Zillow"),))

        self.assertEqual(looked.status, nav.VERIFIED)
        self.assertTrue(looked.arrived)

    def test_a_browser_error_page_is_not_success(self):
        started = nav.start("nosuch.example", "https://nosuch.example")

        looked = nav.verify(started, (
            _tab("chrome-error://chromewebdata/", "This site can't be reached"),
        ))

        self.assertEqual(looked.status, nav.ERROR_PAGE)
        self.assertFalse(looked.arrived)

    def test_searching_for_the_address_is_not_going_to_it(self):
        started = nav.start("openzillow.com", "https://openzillow.com")

        looked = nav.verify(started, (
            _tab("https://www.google.com/search?q=openzillow.com",
                 "openzillow.com - Google Search"),
        ))

        self.assertEqual(looked.status, nav.ERROR_PAGE)

    def test_staying_on_the_previous_page_is_not_success(self):
        started = nav.start("naver.com", "https://naver.com")

        looked = nav.verify(started, (_tab("https://zillow.com/", "Zillow"),))

        self.assertEqual(looked.status, nav.WRONG_DESTINATION)

    def test_a_blank_tab_is_not_success(self):
        started = nav.start("naver.com", "https://naver.com")

        self.assertEqual(
            nav.verify(started, (_tab("about:blank", ""),)).status, nav.FAILED,
        )

    def test_a_browser_that_cannot_be_read_is_unverified_not_failed(self):
        # The distinction that makes the honest sentence sayable: "I sent
        # the browser there, but I haven't checked that it loaded."
        started = nav.start("naver.com", "https://naver.com")

        looked = nav.verify(started, ())

        self.assertEqual(looked.status, nav.UNVERIFIED)
        self.assertFalse(looked.arrived)
        self.assertFalse(looked.checked)

    def test_a_redirect_within_the_site_is_still_arriving(self):
        # The over-correction to watch: real sites redirect constantly.
        for actual in (
            "https://www.zillow.com/homes/for_rent/",
            "https://zillow.com/?utm_source=x",
            "https://m.zillow.com/",
        ):
            with self.subTest(actual=actual):
                looked = nav.verify(
                    nav.start("zillow.com", "https://zillow.com"),
                    (_tab(actual, "Zillow"),),
                )
                self.assertTrue(looked.arrived, actual)


class WhereARecoveryCandidateComesFromTests(unittest.TestCase):
    """Two sources, both things the conversation said. No invented domains."""

    def test_a_verb_the_transcriber_ran_into_the_host(self):
        self.assertEqual(nav.unfused("https://openzillow.com"), "zillow.com")
        self.assertEqual(nav.unfused("https://opennaver.com"), "naver.com")

    def test_the_split_is_a_proposal_and_never_a_rewrite(self):
        # openai.com and opentable.com are real hosts, and nothing here can
        # tell them apart from a fused verb by looking. That is why the
        # split is only ever offered *after* the requested address failed
        # to load -- a site that works is never second-guessed. The engine
        # test below is the one that holds that line.
        self.assertEqual(nav.unfused("https://opentable.com"), "table.com")
        self.assertEqual(nav.unfused("https://zillow.com"), "")

    def test_the_spelling_between_the_two_that_were_tried(self):
        # Session 7's own example. "isss" was asked for, "only one S"
        # produced "is", and that host does not exist -- so the spelling
        # neither has tried is the one the person meant.
        self.assertEqual(
            nav.spellings_between("isss.washington.edu", "is.washington.edu"),
            ("iss.washington.edu",),
        )

    def test_nothing_is_invented_when_there_is_nothing_to_derive(self):
        started = nav.start("nosuchhost.example", "https://nosuchhost.example")

        self.assertEqual(nav.recovery_candidates(started), ())

    def test_a_candidate_already_tried_is_not_offered_again(self):
        started = nav.start(
            "is.washington.edu", "https://is.washington.edu",
            history=("https://isss.washington.edu", "https://iss.washington.edu"),
        )

        self.assertNotIn(
            "iss.washington.edu", nav.recovery_candidates(started),
        )


class WhatSheSaysAfterOpeningSomethingTests(unittest.TestCase):
    """The same lifecycle, through the engine, in the four shapes."""

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine()
        self.engine.NAVIGATION_SETTLE_SECONDS = 0

    def tearDown(self):
        self.engine.close()

    def test_a_page_that_loaded_is_reported_normally(self):
        self.engine.browser_observer.showing(
            "https://www.zillow.com/homes/", "Zillow",
        )

        line, result = self.engine._handle_computer_action(_route("zillow.com"))

        self.assertEqual(result.status, "url_opened")
        self.assertNotIn("couldn't", line)

    def test_a_page_that_did_not_load_is_never_reported_as_open(self):
        self.engine.browser_observer.showing(
            "chrome-error://chromewebdata/", "This site can't be reached",
        )

        line, result = self.engine._handle_computer_action(
            _route("nosuchhost.example"),
        )

        self.assertEqual(result.status, "navigation_failed")
        self.assertIn("didn't load", line)
        self.assertNotIn("is open", line)

    def test_a_browser_she_cannot_read_produces_a_hedge_not_a_claim(self):
        self.engine.browser_observer.tabs = ()
        self.engine.browser_observer.page = None

        line, result = self.engine._handle_computer_action(_route("naver.com"))

        self.assertEqual(result.status, "url_dispatched")
        self.assertIn("couldn't check", line)

    def test_a_site_that_works_is_never_second_guessed(self):
        # openai.com begins with "open" and is a real place. Nothing may
        # propose "ai.com" for it, because it loaded.
        self.engine.browser_observer.showing("https://openai.com/", "OpenAI")

        line, result = self.engine._handle_computer_action(_route("openai.com"))

        self.assertEqual(result.status, "url_opened")
        self.assertNotIn("instead", line)
        self.assertNotIn("ai.com", line)

    def test_a_fused_verb_is_recovered_and_verified(self):
        self.engine.browser_observer.showing(
            "chrome-error://chromewebdata/", "This site can't be reached",
        )
        executed = self.engine.computer_control.execute

        def execute(prepared, **kwargs):
            outcome = executed(prepared, **kwargs)
            if "zillow.com" in str(getattr(prepared, "url", "")):
                if not str(getattr(prepared, "url", "")).count("openzillow"):
                    self.engine.browser_observer.showing(
                        "https://www.zillow.com/", "Zillow",
                    )
            return outcome

        self.engine.computer_control.execute = execute

        line, result = self.engine._handle_computer_action(
            _route("openzillow.com"),
        )

        self.assertIn("zillow.com", line)
        self.assertIn("instead", line)
        self.assertEqual(result.status, "url_opened")

    def test_the_correction_history_supplies_the_recovery(self):
        # The whole of session 7's S7-03, in order.
        self.engine.browser_observer.showing(
            "chrome-error://chromewebdata/", "This site can't be reached",
        )
        self.engine._handle_computer_action(_route("isss.washington.edu"))

        corrected, _ = self.engine._rescue_capability_route(
            IntentDecision(
                intent="conversation", confidence=0.9,
                normalized_request="I meant only one S", reason="t",
            ),
            "I meant only one S.",
        )
        self.assertEqual(corrected.action_target, "is.washington.edu")

        executed = self.engine.computer_control.execute

        def execute(prepared, **kwargs):
            outcome = executed(prepared, **kwargs)
            if "iss.washington.edu" in str(getattr(prepared, "url", "")):
                self.engine.browser_observer.showing(
                    "https://iss.washington.edu/", "ISS",
                )
            return outcome

        self.engine.computer_control.execute = execute

        line, _ = self.engine._handle_computer_action(
            _route("is.washington.edu"),
        )

        self.assertIn("iss.washington.edu", line)


if __name__ == "__main__":
    unittest.main()
