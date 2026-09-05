"""One recorded action, so "try again" has something to mean.

Session 9. A browser action failed and every way of asking for it again
went somewhere else:

    open_url isss.washington.edu status=failed
    You said: Can you try again?
    [Rescue] computer_action/unsupported -> unsupported
    Elaina: I can't do that one.

    Elaina: Zillow.com didn't open, try again?
    You said: Yeah.
    Elaina: I got it. Let me know what you need next.

The second is the worse of the two: she asked the question and left
nothing outstanding for the answer to accept. The person then had to say
"you didn't open it" before anything happened.

And a failure to navigate never reached the recovery lifecycle at all,
because that hung off the verification path and a dispatch that failed
outright never got there -- so is.washington.edu ended at "action failed"
with iss.washington.edu sitting unexamined in the correction history.
"""

from __future__ import annotations

import unittest

from brain import browser_progress
from brain.intent_router import IntentDecision


def _route(target: str):
    return IntentDecision(
        intent="computer_action", confidence=0.99,
        normalized_request=f"open {target}", reason="session 9",
        computer_operation="open_url", action_target=target,
        computer_url=f"https://{target}", action_requested=True,
    )


class TryAgainNamesNoTargetBecauseItNeedsNoneTests(unittest.TestCase):

    def test_the_ways_a_person_asks_for_the_same_thing_again(self):
        for said in (
            "Can you try again?", "try again", "Try again please.",
            "do it again", "again", "one more time", "So open it.",
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    browser_progress.continues_the_last_action(said), said,
                )

    def test_a_turn_that_names_its_own_target_is_not_a_retry(self):
        for said in (
            "open zillow.com", "try opening naver.com",
            "Can you try that on naver.com?", "I tried again yesterday",
            "say that again",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    browser_progress.continues_the_last_action(said), said,
                )

    def test_it_goes_back_to_the_action_that_failed(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._last_computer_action = "open_url"
        engine._last_computer_goal = "isss.washington.edu"
        engine._turn_points_at_the_last_action = False
        try:
            route, note = engine._rescue_capability_route(
                IntentDecision(
                    intent="computer_action", confidence=0.9,
                    normalized_request="try again", reason="t",
                    computer_operation="unsupported",
                ),
                "Can you try again?",
            )
        finally:
            engine.close()

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "isss.washington.edu")
        self.assertNotIn("can't do that one", note)


class TheRetryOfferIsSomethingAYesCanAcceptTests(unittest.TestCase):

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine()
        self.engine.NAVIGATION_SETTLE_SECONDS = 0
        executed = self.engine.computer_control.execute
        self.real = {"zillow.com": "Zillow: Real Estate"}

        def execute(prepared, **kwargs):
            outcome = executed(prepared, **kwargs)
            url = str(getattr(prepared, "url", "") or "")
            host = url.replace("https://", "").rstrip("/")
            if host in self.real:
                self.engine.browser_observer.showing(
                    url, self.real[host], "a real page",
                )
            else:
                self.engine.browser_observer.showing(url, host, "")
            return outcome

        self.engine.computer_control.execute = execute

    def tearDown(self):
        self.engine.close()

    def test_a_failed_navigation_parks_the_retry_it_offers(self):
        line, result = self.engine._handle_computer_action(
            _route("nosuchhost.example"),
        )

        self.assertNotIn("is open", line)
        pending = self.engine.capability_offer.peek()
        self.assertIsNotNone(pending)
        self.assertIn("nosuchhost.example", pending.goal)

    def test_a_navigation_that_worked_parks_nothing(self):
        # The over-correction: a successful open must leave no offer
        # behind for the next "yeah" to trip over.
        self.engine._handle_computer_action(_route("zillow.com"))

        self.assertIsNone(self.engine.capability_offer.peek())


class AFailedDispatchReachesTheRecoveryTests(unittest.TestCase):
    """S9-07. The lifecycle existed and the failure never got to it."""

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine()
        self.engine.NAVIGATION_SETTLE_SECONDS = 0
        self.real = {
            "iss.washington.edu": "International Student Services - ISS",
        }
        executed = self.engine.computer_control.execute

        def execute(prepared, **kwargs):
            outcome = executed(prepared, **kwargs)
            url = str(getattr(prepared, "url", "") or "")
            host = url.replace("https://", "").rstrip("/")
            if host in self.real:
                self.engine.browser_observer.showing(
                    url, self.real[host], "a real page",
                )
            else:
                self.engine.browser_observer.showing(url, host, "")
            return outcome

        self.engine.computer_control.execute = execute

    def tearDown(self):
        self.engine.close()

    def test_the_correction_history_still_supplies_the_candidate(self):
        self.engine._handle_computer_action(_route("isss.washington.edu"))

        corrected, _ = self.engine._rescue_capability_route(
            IntentDecision(
                intent="conversation", confidence=0.9,
                normalized_request="I meant only one S", reason="t",
            ),
            "I meant only one S.",
        )
        line, result = self.engine._handle_computer_action(
            _route(corrected.action_target.replace("https://", "")),
        )

        self.assertIn("iss.washington.edu", line)
        self.assertIn("instead", line)
        self.assertEqual(result.status, "url_opened")


if __name__ == "__main__":
    unittest.main()
