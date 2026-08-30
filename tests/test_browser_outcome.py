"""What the browser established, as opposed to what it did.

Reported live, immediately after the 4E.4 dispatch fix landed: the browser
really ran -- five rounds, a search, a navigation, a click attempt, a page
read, ``status=done`` -- and the spoken result was "Opened." That sentence
is true about the run and answers nothing about

    "Does the Lotte Hotel have a room available September 18?"

``status=done`` means the planner stopped cleanly. It never meant the goal
was verified, and reading it as if it did is how an action report ends up
being spoken as an answer.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from brain import browser_outcome as bo
from brain import capability_selection as caps
from brain.deliberation import goal_intent, interaction
from brain.browser_action_planner import ActionPlanResult
from brain.intent_router import IntentDecision
from tests.turn_harness import build_engine

LIVE_CASE = "does the lotte hotel have a room available september 18"
# Also needs the live page, and also has a half a search can honestly
# answer -- so this one is allowed to fall back and LIVE_CASE is not.
RESEARCHABLE_CASE = (
    "what is the rate for a room at the lotte hotel on september 18"
)


def _read(summary, *, succeeded=True, needs_verification=True, goal=LIVE_CASE):
    return bo.read(
        summary, succeeded=succeeded,
        needs_verification=needs_verification, goal=goal,
    )


class ActionReportsAreNotAnswersTests(unittest.TestCase):
    """The exact live failure, and its neighbours."""

    def test_the_reported_summary_is_not_treated_as_an_answer(self):
        outcome = _read("Opened.")

        self.assertEqual(outcome.state, bo.NOT_VERIFIED)
        self.assertFalse(outcome.verified)

    def test_nothing_of_the_action_report_survives_into_the_reply(self):
        # The point is not merely a different state -- it is that "Opened."
        # must not reach the user as the answer to a question.
        outcome = _read("Opened.")

        self.assertEqual(outcome.answer, "")

    def test_a_longer_action_report_is_still_only_an_action_report(self):
        for said in (
            "Opened the Lotte Hotel booking page.",
            "Opened the availability page.",
            "Searched for the Lotte Hotel and opened the first result.",
            "Clicked the date picker.",
            "That's done.",
        ):
            with self.subTest(said=said):
                self.assertEqual(_read(said).state, bo.NOT_VERIFIED)

    def test_the_word_availability_alone_does_not_confirm_availability(self):
        # "Opened the availability page" carries the word and none of the
        # meaning. A verdict needs a verb.
        self.assertEqual(
            _read("Opened the availability page.").state, bo.NOT_VERIFIED,
        )

    def test_the_run_still_finished(self):
        self.assertTrue(_read("Opened.").ran)


class VerifiedResultsTests(unittest.TestCase):
    """A real finding is kept, and kept verbatim."""

    def test_a_positive_verdict_reads_as_verified(self):
        for said in (
            "Rooms are available on September 18 from 250,000 KRW.",
            "The Deluxe King is available for September 18.",
            "There are rooms available that night.",
            "You can book a room for the 18th.",
        ):
            with self.subTest(said=said):
                self.assertEqual(_read(said).state, bo.VERIFIED_TRUE)

    def test_a_price_read_off_the_page_is_itself_a_finding(self):
        outcome = _read("The room shows $310 per night for that date.")

        self.assertEqual(outcome.state, bo.VERIFIED_TRUE)

    def test_a_negative_verdict_reads_as_verified_false(self):
        for said in (
            "The hotel is fully booked on September 18.",
            "No rooms available for that date.",
            "Those dates are sold out.",
        ):
            with self.subTest(said=said):
                self.assertEqual(_read(said).state, bo.VERIFIED_FALSE)

    def test_a_verified_answer_is_passed_through_untouched(self):
        said = "Rooms are available on September 18 from 250,000 KRW."

        self.assertEqual(_read(said).answer, said)

    def test_verified_covers_both_directions(self):
        self.assertTrue(_read("No rooms available that night.").verified)
        self.assertTrue(_read("Rooms are available that night.").verified)


class HedgesOutrankVerdictsTests(unittest.TestCase):
    """A page that did not say is not a page that said no."""

    def test_not_shown_is_not_the_same_as_none_available(self):
        for said in (
            "The page doesn't show availability for September 18.",
            "I couldn't confirm whether a room is available.",
            "Availability is not shown; you'll need to check the site "
            "directly.",
            "I was unable to load the booking calendar.",
            "I'd recommend contacting the hotel to confirm availability.",
        ):
            with self.subTest(said=said):
                self.assertEqual(_read(said).state, bo.NOT_VERIFIED)

    def test_a_hedge_beats_the_positive_words_inside_it(self):
        # Contains "are available" and means the opposite of a confirmation.
        outcome = _read(
            "I couldn't tell from the page whether rooms are available.",
        )

        self.assertEqual(outcome.state, bo.NOT_VERIFIED)


class ActionGoalsKeepTheirSummaryTests(unittest.TestCase):
    """Nothing to verify means the action itself is the result."""

    def test_a_click_goal_reports_the_click(self):
        outcome = _read(
            "Clicked Images.", needs_verification=False, goal="click images",
        )

        self.assertEqual(outcome.state, bo.VERIFIED_TRUE)
        self.assertEqual(outcome.answer, "Clicked Images.")

    def test_the_strictness_is_only_applied_where_it_belongs(self):
        # The same sentence, read against a question, answers nothing.
        self.assertEqual(_read("Clicked Images.").state, bo.NOT_VERIFIED)


class FailureIsItsOwnStateTests(unittest.TestCase):

    def test_a_failed_run_is_not_an_unverified_one(self):
        outcome = _read("I couldn't reach the browser.", succeeded=False)

        self.assertEqual(outcome.state, bo.FAILED)
        self.assertFalse(outcome.ran)

    def test_a_failed_run_keeps_its_own_explanation(self):
        outcome = _read("I couldn't reach the browser.", succeeded=False)

        self.assertEqual(outcome.answer, "I couldn't reach the browser.")

    def test_an_empty_summary_is_unverified_not_failed(self):
        self.assertEqual(_read("   ").state, bo.NOT_VERIFIED)


class FallbackIsGatedOnHonestyTests(unittest.TestCase):
    """A snippet cannot know whether one room is free on one night."""

    def test_a_pure_live_question_has_no_useful_fallback(self):
        for goal in (
            "does the lotte hotel have a room available september 18",
            "is the shilla free tonight",
            "is it sold out",
        ):
            with self.subTest(goal=goal):
                self.assertFalse(bo.fallback_can_help(goal))

    def test_a_question_with_a_researchable_half_does(self):
        for goal in (
            "what is the rate for a room at the lotte hotel on september 18",
            "which hotels have rooms on september 18",
            "what is the lotte hotel phone number",
            "compare rates for the 18th",
        ):
            with self.subTest(goal=goal):
                self.assertTrue(bo.fallback_can_help(goal))


class HonestReportTests(unittest.TestCase):

    def test_the_line_admits_it_did_not_get_the_answer(self):
        line = bo.unverified_line(LIVE_CASE).lower()

        self.assertTrue(
            "couldn't" in line or "never" in line,
            line,
        )

    def test_the_same_question_gives_a_stable_line(self):
        # str.__hash__ is salted per process; this must not be.
        self.assertEqual(
            bo.unverified_line(LIVE_CASE), bo.unverified_line(LIVE_CASE),
        )

    def test_the_fallback_notice_forbids_claiming_a_live_check(self):
        notice = bo.fallback_notice().lower()

        self.assertIn("could not confirm", notice)
        self.assertIn("do not state or imply", notice)


class DispatchUsesTheOutcomeTests(unittest.TestCase):
    """The reply the user actually hears, end to end."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self._original = self.engine._handle_browser_action
        self.engine._capability_failures.clear()
        self.engine._live_check_note = ""
        self.engine.computer_control_mode.set_enabled(True)

    def tearDown(self):
        self.engine._handle_browser_action = self._original

    def _routing(self, request):
        route = IntentDecision(
            intent="web_search", confidence=0.95,
            normalized_request=request, topic="the Lotte Hotel",
            verification_required=True, requires_external_evidence=True,
        )
        goal = goal_intent.read(route)
        decision = interaction.decide(route, goal=goal)
        choice = caps.select(goal, decision, route=route)
        return route, SimpleNamespace(
            route=route, decision=decision, goal_intent=goal,
            capability=choice,
        )

    def _run(self, request, summary, *, status="ui_action_done"):
        route, routing = self._routing(request)
        self.engine._handle_browser_action = lambda *a, **k: (
            summary, SimpleNamespace(status=status),
        )
        return routing, self.engine._run_browser_capability(
            route, routing, request,
        )

    def test_opened_never_reaches_the_user_as_an_answer(self):
        _routing, (message, _result) = self._run(LIVE_CASE, "Opened.")

        self.assertNotEqual(message.strip(), "Opened.")
        self.assertNotIn("opened.", message.lower())

    def test_it_says_plainly_that_it_could_not_confirm(self):
        _routing, (message, _result) = self._run(LIVE_CASE, "Opened.")

        self.assertTrue(message.strip(), "the turn went silent")
        self.assertEqual(message, bo.unverified_line(LIVE_CASE))

    def test_a_real_finding_is_spoken_as_the_answer(self):
        said = "Rooms are available on September 18 from 250,000 KRW."
        _routing, (message, _result) = self._run(LIVE_CASE, said)

        self.assertEqual(message, said)

    def test_a_verified_run_is_not_counted_against_the_browser(self):
        self._run(LIVE_CASE, "Rooms are available that night.")

        self.assertNotIn("browser_control", self.engine._capability_failures)

    def test_an_unverified_run_counts_against_the_browser(self):
        self._run(LIVE_CASE, "Opened.")

        self.assertEqual(
            self.engine._capability_failures.get("browser_control"), 1,
        )

    def test_a_pure_live_question_does_not_fall_back_to_a_snippet(self):
        routing, (_message, _result) = self._run(LIVE_CASE, "Opened.")

        self.assertEqual(routing.capability.capability, caps.BROWSER_CONTROL)
        self.assertEqual(self.engine._live_check_note, "")

    def test_a_researchable_question_does_fall_back(self):
        routing, (message, _result) = self._run(
            RESEARCHABLE_CASE, "Opened.",
        )

        self.assertEqual(routing.capability.capability, caps.WEB_SEARCH)
        self.assertEqual(message, "", "the fallback never got to answer")

    def test_the_fallback_is_warned_not_to_claim_a_live_check(self):
        self._run(
            RESEARCHABLE_CASE, "Opened.",
        )

        self.assertIn("LIVE CHECK FAILED", self.engine._live_check_note)

    def test_the_warning_is_consumed_once(self):
        self._run(
            RESEARCHABLE_CASE, "Opened.",
        )

        first = self.engine._take_live_check_note()
        second = self.engine._take_live_check_note()

        self.assertIn("LIVE CHECK FAILED", first)
        self.assertEqual(second, "", "a later search inherited the warning")

    def test_a_failed_run_keeps_the_browsers_own_explanation(self):
        _routing, (message, _result) = self._run(
            LIVE_CASE, "I couldn't reach the browser.",
            status="ui_action_failed",
        )

        self.assertEqual(message, "I couldn't reach the browser.")



class BothRoutesIntoTheHandlerAreCoveredTests(unittest.TestCase):
    """The router's own label reaches the same planner, and the same trap.

    Observed live on the follow-up turn: "Yeah, check it." was labelled
    computer_action by the router, so it went through _handle_computer_action
    rather than the capability dispatch -- and would have kept reporting
    whatever the planner said, unread. The interpretation belongs where the
    two routes meet, not in whichever branch called in.
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def _handle(self, request, summary, *, status="done"):
        route = IntentDecision(
            intent="computer_action", confidence=0.95,
            normalized_request=request, computer_operation="browser_action",
        )
        self.engine.browser_action_planner = SimpleNamespace(
            act=lambda *a, **k: ActionPlanResult(status, summary),
        )
        message, _result = self.engine._handle_browser_action(
            route, approved_action=None, original_request=request,
        )
        return message

    def test_the_router_labelled_route_also_refuses_an_action_report(self):
        message = self._handle(
            "check the lotte hotel room availability for september 18",
            "Opened.",
        )

        self.assertNotIn("opened", message.lower())

    def test_it_keeps_a_real_finding_on_that_route_too(self):
        said = "Rooms are available on September 18 from 250,000 KRW."
        message = self._handle(
            "check the lotte hotel room availability for september 18", said,
        )

        self.assertEqual(message, said)

    def test_a_plain_action_goal_still_reports_the_action(self):
        # Nothing was asked, so "Clicked Images." is the whole result and
        # must survive untouched.
        message = self._handle("click images", "Clicked Images.")

        self.assertEqual(message, "Clicked Images.")



if __name__ == "__main__":
    unittest.main()
