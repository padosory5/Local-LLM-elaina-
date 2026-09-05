"""What the turn is about, decided before any pending gate reads it.

The browser acceptance run. Two things a person said were taken as
accepting an offer, and neither was one:

    Elaina: I couldn't confirm that zillow.com loaded.
    You said: It's opened. Thanks.
    [Router] computer_action: The user accepted the offered ability.
    [Computer Control] open_url zillow.com          <- again

    You said: I meant only two S's.
    [Router] computer_action (0.00): The user accepted the offered ability.
    [Browser Planner] open_url / list_tabs / describe_page

The first is a person answering the doubt she raised. The second is a
correction to an address. There was no offer worth accepting in either.

The order the user set out, and the one these hold to:

    explicit correction
      > explicit new instruction
      > a report about the last action
      > retry
      > pending-offer acceptance
      > generic acknowledgement

A pending offer is the last reading, not the first.
"""

from __future__ import annotations

import unittest

from brain import browser_navigation, browser_progress
from brain.intent_router import IntentDecision


class ReadingWhatAPersonSaidTests(unittest.TestCase):

    def test_a_report_that_it_worked(self):
        for said in (
            "It's opened. Thanks.", "it's open", "that worked",
            "yep, it's there", "it opened", "the page is up",
            "ok it loaded fine", "thanks, got it", "got it",
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    browser_progress.confirms_last_action(said), said,
                )

    def test_anything_carrying_a_request_is_not_a_report(self):
        # The over-correction that matters: this must never swallow a turn
        # that asks for something.
        for said in (
            "it didn't open", "open zillow.com",
            "It's opened but the wrong page",
            "got it, now open naver.com",
            "that worked well for the guitar search",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    browser_progress.confirms_last_action(said), said,
                )

    def test_the_natural_forms_of_a_character_count_correction(self):
        # Parsed as a structured edit, not collected as phrases: what
        # varies is the verb in front of a number and a letter.
        for said, expected in (
            ("I meant only two S's.", "iss.washington.edu"),
            ("Can you try with two S's?", "iss.washington.edu"),
            ("make it two S's", "iss.washington.edu"),
            ("use two S's", "iss.washington.edu"),
            ("there should be two S's", "iss.washington.edu"),
            ("try with one S", "is.washington.edu"),
            ("one less S", "iss.washington.edu"),
            ("remove one S", "iss.washington.edu"),
            ("add another S", "issss.washington.edu"),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    browser_progress.respelled_address(
                        "isss.washington.edu", said,
                    ),
                    expected, said,
                )

    def test_an_ordinary_sentence_is_not_a_spelling_edit(self):
        for said in (
            "I want a studio", "open zillow.com", "that's not it",
            "Only one.", "I only have one S in my name",
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    browser_progress.respelled_address(
                        "isss.washington.edu", said,
                    ),
                    "", said,
                )

    def test_an_opening_verb_and_an_address_is_an_instruction(self):
        for said, expected in (
            ("No, open naver.com instead.", "naver.com"),
            ("Open no such host.example.", "nosuchhost.example"),
            ("Use my browser control and open isss.washington.edu.",
             "isss.washington.edu"),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    browser_navigation.asks_to_open_an_address(said),
                    expected, said,
                )

    def test_merely_naming_an_address_is_not_asking_to_open_it(self):
        for said in ("what is naver.com about?", "I read that on naver.com"):
            with self.subTest(said=said):
                self.assertEqual(
                    browser_navigation.asks_to_open_an_address(said), "", said,
                )


class APendingOfferIsTheLastReadingTests(unittest.TestCase):

    def _engine(self, *, goal="zillow.com", offer=True):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine.NAVIGATION_SETTLE_SECONDS = 0
        engine._last_computer_action = "open_url"
        engine._last_computer_goal = goal
        engine._navigation = browser_navigation.start(goal, f"https://{goal}")
        engine._last_action_failed = True
        if offer:
            engine.capability_offer.offer(
                capability_id="browser_control", goal=goal,
                offer_text=f"Want me to try {goal} again?",
            )
        return engine

    def test_a_report_of_success_runs_nothing(self):
        engine = self._engine()
        try:
            routing = engine._route_turn("It's opened. Thanks.", timings={})
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "none")
        self.assertTrue(routing.locked_response)

    def test_and_resolves_the_doubt_she_raised(self):
        engine = self._engine()
        try:
            engine._route_turn("It's opened. Thanks.", timings={})
            navigation = engine._navigation
        finally:
            engine.close()

        self.assertTrue(navigation.arrived)
        self.assertEqual(navigation.classification, "user_confirmed")

    def test_a_report_of_failure_still_retries(self):
        engine = self._engine(offer=False)
        try:
            routing = engine._route_turn("It didn't open.", timings={})
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "open_url")
        self.assertEqual(routing.route.action_target, "zillow.com")

    def test_a_correction_outranks_the_offer(self):
        engine = self._engine(goal="isss.washington.edu")
        try:
            routing = engine._route_turn("I meant only two S's.", timings={})
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "open_url")
        self.assertEqual(routing.route.action_target, "iss.washington.edu")

    def test_a_natural_correction_outranks_it_too(self):
        engine = self._engine(goal="isss.washington.edu")
        try:
            routing = engine._route_turn(
                "Can you try with two S's?", timings={},
            )
        finally:
            engine.close()

        self.assertEqual(routing.route.action_target, "iss.washington.edu")

    def test_an_explicit_new_instruction_outranks_it(self):
        engine = self._engine()
        try:
            routing = engine._route_turn(
                "No, open naver.com instead.", timings={},
            )
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "open_url")
        self.assertEqual(routing.route.action_target, "naver.com")

    def test_a_genuine_acceptance_still_accepts(self):
        # The over-correction to watch. An offer she made must remain
        # answerable, or every question she asks is unanswerable.
        engine = self._engine()
        try:
            routing = engine._route_turn("Yeah.", timings={})
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "open_url")
        self.assertEqual(routing.route.action_target, "zillow.com")


class DisputingTheReasonIsNotAskingAgainTests(unittest.TestCase):
    """The acceptance run cancelled six browser actions and blamed the
    person for moving the mouse. They said so, and it ran again.

        You said: I'm not moving the mouse.
        [Router] computer_action (0.00): The user accepted the offered
                 ability.  -> open_url, and the same claim again
    """

    def test_the_shapes_a_person_denies_it_in(self):
        for said in (
            "I'm not moving the mouse.", "I'm not touching the mouse",
            "I didn't touch the mouse", "No, I'm not moving the mouse",
            "I am not moving anything",
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    browser_progress.disputes_the_reason(said), said,
                )

    def test_a_request_is_not_a_denial(self):
        for said in ("it didn't open", "open zillow.com", "I'm not sure"):
            with self.subTest(said=said):
                self.assertFalse(
                    browser_progress.disputes_the_reason(said), said,
                )

    def test_it_runs_nothing_and_clears_the_offer(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine.capability_offer.offer(
            capability_id="browser_control", goal="zillow.com",
            offer_text="Want me to try zillow.com again?",
        )
        try:
            routing = engine._route_turn(
                "I'm not moving the mouse.", timings={},
            )
            cleared = engine.capability_offer.peek() is None
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "none")
        self.assertTrue(cleared)
        self.assertIn("something else moved the pointer", routing.locked_response.casefold())


class ABareReferenceToASiteResolvesTests(unittest.TestCase):
    """"That website" means the one the conversation named.

    Measured live: after "I met Zillow.com", "can you open that website
    for me?" went to the planner, which searched and landed on the Google
    homepage.
    """

    def test_one_address_in_the_conversation_is_the_referent(self):
        for said in (
            "Yeah, can you open that website for me?", "open that site",
            "go there",
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    browser_progress.site_pointed_at(
                        said, said_recently="I met Zillow.com",
                    ),
                    "Zillow.com", said,
                )

    def test_two_addresses_are_a_question_not_a_referent(self):
        self.assertEqual(
            browser_progress.site_pointed_at(
                "open that website",
                said_recently="zillow.com and naver.com",
            ),
            "",
        )

    def test_a_turn_that_names_its_own_address_is_not_pointing(self):
        self.assertEqual(
            browser_progress.site_pointed_at(
                "open naver.com", said_recently="I met Zillow.com",
            ),
            "",
        )

    def test_it_goes_to_the_navigation_operation(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._router_history.extend([
            {"role": "user", "content": "I met Zillow.com"},
            {"role": "assistant", "content": "Zillow.com is a listings site."},
        ])
        try:
            route, _ = engine._rescue_capability_route(
                IntentDecision(
                    intent="conversation", confidence=0.9,
                    normalized_request="open that website", reason="t",
                ),
                "Yeah, can you open that website for me?",
            )
        finally:
            engine.close()

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "Zillow.com")


class AnAddressIsItsOwnErrandTests(unittest.TestCase):
    """A turn that is entirely a web address cannot be an answer.

    Measured in the acceptance run, with an offer pending:

        You said: openiss.washington.edu
        Current subject: Use browser_control to handle: openiss...
        [Router] computer_action (0.00): The user accepted the offered
                 ability.  -> Browser Planner

    "opennaver.com" one turn earlier went straight to deterministic
    navigation and recovered. The only difference was that an offer
    happened to be open, and a pending question is not allowed to decide
    what a sentence carrying its own destination means.
    """

    def _engine(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine.NAVIGATION_SETTLE_SECONDS = 0
        engine.capability_offer.offer(
            capability_id="browser_control", goal="the calendar",
            offer_text="Want me to use browser control for that?",
        )
        return engine

    def test_a_fused_address_goes_to_navigation_not_the_offer(self):
        engine = self._engine()
        try:
            routing = engine._route_turn("openiss.washington.edu", timings={})
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "open_url")
        self.assertNotIn("accepted the offered", routing.route.reason)

    def test_a_bare_address_does_too(self):
        engine = self._engine()
        try:
            routing = engine._route_turn("naver.com", timings={})
        finally:
            engine.close()

        self.assertEqual(routing.route.computer_operation, "open_url")

    def test_a_plain_yes_still_accepts_the_offer(self):
        # The half that has to keep working: an offer she made stays
        # answerable, or every question she asks is unanswerable.
        engine = self._engine()
        try:
            routing = engine._route_turn("Yes please.", timings={})
        finally:
            engine.close()

        self.assertIn("accepted the offered", routing.route.reason)


class AContradictoryFrameGetsASecondLookTests(unittest.TestCase):
    """A-06/A-07. The address bar commits before the title follows.

    Recovering from openisss.washington.edu to isss.washington.edu
    observed the new address with the old host still in the title, and
    called it a wrong destination on that first frame.
    """

    @staticmethod
    def _tab(url, title, text="a real page"):
        from types import SimpleNamespace

        return SimpleNamespace(
            index=0, url=url, title=title, text=text, is_active=True,
            identity="hwnd:1:scan", correlated=True, readable=True,
        )

    def test_a_title_this_navigation_already_tried_is_a_stale_frame(self):
        started = browser_navigation.start(
            "isss.washington.edu", "https://isss.washington.edu",
            history=("https://openisss.washington.edu",),
        )

        looked = browser_navigation.verify(started, (
            self._tab("https://isss.washington.edu", "openisss.washington.edu"),
        ))

        self.assertEqual(looked.classification, "stale_observation")
        self.assertFalse(looked.arrived)

    def test_once_it_settles_it_verifies(self):
        started = browser_navigation.start(
            "isss.washington.edu", "https://isss.washington.edu",
            history=("https://openisss.washington.edu",),
        )

        looked = browser_navigation.verify(started, (
            self._tab("https://isss.washington.edu", "ISS - Washington"),
        ))

        self.assertTrue(looked.arrived)

    def test_a_genuinely_different_site_is_still_the_wrong_destination(self):
        # The over-correction: a title naming somewhere this navigation has
        # never been is evidence, not a lagging frame.
        started = browser_navigation.start("zillow.com", "https://zillow.com")

        looked = browser_navigation.verify(started, (
            self._tab("https://zillow.com", "iss.washington.edu"),
        ))

        self.assertEqual(looked.classification, "wrong_destination")


if __name__ == "__main__":
    unittest.main()
