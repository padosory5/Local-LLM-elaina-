"""Which tool, and why that one rather than the cheaper or dearer neighbour.

Phase 4E.2 stopped the router's label picking the tool. It chose on ``need``
alone, which was enough to separate "answer" from "look it up" and not enough
to separate two abilities once both could serve: "does Hotel X have a room on
the 18th" and "what are some good hotels in Seoul" have the same need and
want different tools. The first needs the real page; the second does not, and
opening one would be slower and more disruptive for no gain.

So selection now weighs what the request requires against what each ability
costs, and the ladder runs cheapest-first:

    what she knows -> what this session found -> a lookup -> a live page
    -> operating the machine

Thirty-two scenarios below, grouped by the judgement each one tests.
"""

from __future__ import annotations

import unittest

from brain import capability_selection as caps
from brain.deliberation import goal_intent, interaction
from types import SimpleNamespace

from brain.action_commitment import ActionCommitmentGuard
from brain.intent_router import IntentDecision
from tests.turn_harness import build_engine


def _chain(label: str, *, has_context: bool = False, failures=None, **fields):
    fields.setdefault("confidence", 0.95)
    fields.setdefault("normalized_request", "the request")
    route = IntentDecision(intent=label, **fields)
    goal = goal_intent.read(route)
    decision = interaction.decide(
        route, goal=goal, has_usable_context=has_context,
    )
    choice = caps.select(goal, decision, route=route, failures=failures)
    return goal, decision, choice


def _capability(label: str, **kwargs) -> str:
    return _chain(label, **kwargs)[2].capability


class BriefExampleTests(unittest.TestCase):
    """The six worked examples, verbatim."""

    def test_1_good_hotels_uses_search_not_the_browser(self):
        # "Browser control is probably unnecessary initially."
        self.assertEqual(
            _capability(
                "web_search",
                normalized_request="what are some good hotels in seoul",
                topic="hotels in Seoul", requires_external_evidence=True,
            ),
            caps.WEB_SEARCH,
        )

    def test_2_live_availability_uses_the_browser(self):
        # A snippet cannot honestly answer whether one room is free on one
        # night. This is the case 4E.2 got wrong.
        self.assertEqual(
            _capability(
                "web_search",
                normalized_request=(
                    "does hotel X have a room available september 18"
                ),
                topic="hotel X", verification_required=True,
                requires_external_evidence=True,
            ),
            caps.BROWSER_CONTROL,
        )

    def test_3_opening_a_result_is_browser_control(self):
        self.assertEqual(
            _capability(
                "computer_action",
                normalized_request="open the second hotel",
                action_requested=True,
                computer_operation="browser_action",
            ),
            caps.BROWSER_CONTROL,
        )

    def test_4_a_follow_up_reuses_what_was_found(self):
        self.assertEqual(
            _capability(
                "web_search", has_context=True,
                normalized_request="which one would you choose",
                topic="hotels in Seoul", is_follow_up=True,
                requires_external_evidence=True,
            ),
            caps.DIRECT_ANSWER,
        )

    def test_5_a_definition_needs_no_tool(self):
        self.assertEqual(
            _capability(
                "knowledge_question",
                normalized_request="what does recursion mean",
                topic="recursion",
            ),
            caps.DIRECT_ANSWER,
        )

    def test_6_freshness_uses_a_current_lookup(self):
        self.assertEqual(
            _capability(
                "web_search",
                normalized_request="what is the latest version of this library",
                topic="the library", information_freshness="live",
                requires_external_evidence=True,
            ),
            caps.WEB_SEARCH,
        )


class FactorTests(unittest.TestCase):
    """Requirements are read from earlier layers, not from keywords here."""

    def _factors(self, **kwargs) -> caps.Factors:
        return _chain(**kwargs)[2].factors

    def test_freshness_is_read_from_the_router(self):
        factors = self._factors(
            label="web_search", normalized_request="nvidia price now",
            information_freshness="live", requires_external_evidence=True,
        )

        self.assertTrue(factors.freshness_required)
        self.assertFalse(factors.live_state_required)

    def test_live_state_is_read_from_the_same_test_the_preflight_uses(self):
        factors = self._factors(
            label="web_search",
            normalized_request="is a room available on september 18",
            verification_required=True,
        )

        self.assertTrue(factors.live_state_required)

    def test_verification_is_distinct_from_freshness(self):
        factors = self._factors(
            label="fact_check", normalized_request="is that still true",
            verification_required=True,
        )

        self.assertTrue(factors.verification_required)
        self.assertTrue(factors.freshness_required)

    def test_interaction_is_read_from_the_named_surface(self):
        factors = self._factors(
            label="web_search",
            normalized_request="open booking.com and check the price",
            action_requested=True,
        )

        self.assertTrue(factors.interaction_required)

    def test_existing_context_is_recorded(self):
        factors = self._factors(
            label="web_search", has_context=True, is_follow_up=True,
            normalized_request="which of those is cheapest",
            requires_external_evidence=True,
        )

        self.assertTrue(factors.existing_context_available)

    def test_permission_level_travels_with_the_choice(self):
        factors = self._factors(
            label="knowledge_question", normalized_request="what is ram",
        )

        self.assertEqual(factors.permission_level, 1)

    def test_a_plain_question_requires_nothing(self):
        factors = self._factors(
            label="knowledge_question",
            normalized_request="what does recursion mean",
        )

        self.assertFalse(factors.freshness_required)
        self.assertFalse(factors.live_state_required)
        self.assertFalse(factors.interaction_required)


class EscalationTests(unittest.TestCase):
    """Cheapest first, and only as far up as the requirement forces."""

    def test_nothing_outruns_an_answer_already_in_hand(self):
        _g, _d, choice = _chain(
            "web_search", has_context=True, is_follow_up=True,
            normalized_request="which was the cheapest",
            requires_external_evidence=True,
        )

        self.assertEqual(choice.capability, caps.DIRECT_ANSWER)
        self.assertEqual(choice.candidates[0].capability, caps.DIRECT_ANSWER)

    def test_a_lookup_beats_a_page_when_a_snippet_will_do(self):
        _g, _d, choice = _chain(
            "web_search", normalized_request="who is nvidia's ceo",
            topic="Nvidia", requires_external_evidence=True,
        )
        scores = {c.capability: c.score for c in choice.candidates}

        self.assertGreater(scores[caps.WEB_SEARCH], scores[caps.BROWSER_CONTROL])

    def test_a_page_beats_a_lookup_when_the_state_is_live(self):
        _g, _d, choice = _chain(
            "web_search",
            normalized_request="are there rooms available tonight",
            verification_required=True,
        )
        scores = {c.capability: c.score for c in choice.candidates}

        self.assertGreater(scores[caps.BROWSER_CONTROL], scores[caps.WEB_SEARCH])

    def test_knowing_it_beats_everything_when_nothing_is_required(self):
        _g, _d, choice = _chain(
            "knowledge_question", normalized_request="what is a gpu",
        )
        scores = {c.capability: c.score for c in choice.candidates}

        self.assertGreater(scores[caps.DIRECT_ANSWER], scores[caps.WEB_SEARCH])

    def test_the_expensive_tool_is_not_chosen_for_a_cheap_question(self):
        for request in (
            "what does recursion mean", "explain what ram does",
            "what is a compiler",
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    _capability(
                        "knowledge_question", normalized_request=request,
                    ),
                    caps.DIRECT_ANSWER,
                )

    def test_an_unreliable_snippet_is_not_used_for_live_state(self):
        # The other half of the brief's rule.
        for request in (
            "is it available on the 18th",
            "what is the price tonight",
            "can i book it for two nights",
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    _capability(
                        "web_search", normalized_request=request,
                        verification_required=True,
                    ),
                    caps.BROWSER_CONTROL,
                )


class VerificationTests(unittest.TestCase):
    """Checking a source is not the same as needing a live page.

    Found live: "what are some good hotels in Seoul" reached browser control
    because the router marks it verification_required and the browser scored
    higher on verification. That is the exact case the brief says browser
    control is unnecessary for -- a search that cites a source verifies it.
    """

    def test_verification_alone_stays_with_search(self):
        for request in (
            "what are some good hotels in seoul",
            "is that still true",
            "who is nvidia's ceo now",
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    _capability(
                        "web_search", normalized_request=request,
                        verification_required=True,
                        requires_external_evidence=True,
                    ),
                    caps.WEB_SEARCH,
                )

    def test_verification_plus_live_state_reaches_the_page(self):
        self.assertEqual(
            _capability(
                "web_search",
                normalized_request="is a room available on september 18",
                verification_required=True,
                requires_external_evidence=True,
            ),
            caps.BROWSER_CONTROL,
        )

    def test_the_reason_names_the_factor_that_decided_it(self):
        _g, _d, live = _chain(
            "web_search",
            normalized_request="is a room available on september 18",
            topic="the hotel", verification_required=True,
        )
        _g, _d, named = _chain(
            "web_search",
            normalized_request="open booking.com and check the price",
            action_requested=True, verification_required=True,
        )

        self.assertIn("live state", live.reason)
        self.assertIn("names a page", named.reason)


class SurfaceDifferentiationTests(unittest.TestCase):
    """Web, browser and native control are told apart."""

    def test_each_machine_surface_is_named(self):
        for label, expected in (
            ("computer_action", caps.UI_CONTROL),
            ("media_action", caps.UI_CONTROL),
            ("screen_analysis", caps.SCREEN_ANALYSIS),
            ("project_question", caps.PROJECT_QUESTION),
            ("project_edit", caps.PROJECT_EDIT),
            ("git_commit", caps.GIT),
            ("git_publish", caps.GIT),
            ("calendar_action", caps.CALENDAR_ACTION),
        ):
            with self.subTest(label=label):
                self.assertEqual(
                    _capability(label, action_requested=True), expected,
                )

    def test_the_operation_names_the_surface_not_the_intent(self):
        """Browser versus desktop is carried by computer_operation.

        This used to be asserted against intents "browser_action",
        "browser_tab" and "browser_search" -- none of which the router has
        ever emitted. Every machine request arrives as "computer_action",
        so those branches were unreachable and every page action was filed
        as Windows UI control: measured, eight of eight browser cases and
        all three surface follow-ups, including "Click the Sign in button
        on this page."
        """
        for operation, expected in (
            ("browser_action", caps.BROWSER_CONTROL),
            ("open_url", caps.BROWSER_CONTROL),
            ("open_search", caps.BROWSER_CONTROL),
            ("ui_action", caps.UI_CONTROL),
            ("open_app", caps.UI_CONTROL),
            ("close_app", caps.UI_CONTROL),
            ("force_quit_app", caps.UI_CONTROL),
            ("list_windows", caps.UI_CONTROL),
            ("delete_file", caps.UI_CONTROL),
        ):
            with self.subTest(operation=operation):
                self.assertEqual(
                    _capability(
                        "computer_action",
                        action_requested=True,
                        computer_operation=operation,
                    ),
                    expected,
                )

    def test_an_unsupported_operation_names_no_surface(self):
        # "Chrome keeps crashing on me lately." routes as a computer action
        # the machine cannot carry out. Naming ui_control implied a driver
        # was standing by for a complaint.
        self.assertEqual(
            _capability(
                "computer_action",
                action_requested=True,
                computer_operation="unsupported",
            ),
            caps.DIRECT_ANSWER,
        )

    def test_native_control_is_never_chosen_for_information(self):
        for request in (
            "what are good hotels in seoul", "what is the exchange rate",
            "what does recursion mean",
        ):
            with self.subTest(request=request):
                self.assertNotEqual(
                    _capability(
                        "web_search", normalized_request=request,
                        requires_external_evidence=True,
                    ),
                    caps.UI_CONTROL,
                )

    def test_an_open_question_runs_nothing_at_all(self):
        self.assertEqual(_capability("clarification"), caps.NOTHING)


class FallbackTests(unittest.TestCase):
    """What to try next, and when to stop trying."""

    def test_a_choice_carries_an_ordered_fallback(self):
        _g, _d, choice = _chain(
            "web_search",
            normalized_request="is a room available on september 18",
            verification_required=True,
        )

        self.assertEqual(choice.capability, caps.BROWSER_CONTROL)
        self.assertIn(caps.WEB_SEARCH, choice.fallbacks)

    def test_a_fallback_never_includes_the_choice_itself(self):
        for label, fields in (
            ("web_search", {"requires_external_evidence": True}),
            ("knowledge_question", {}),
        ):
            with self.subTest(label=label):
                _g, _d, choice = _chain(label, **fields)
                self.assertNotIn(choice.capability, choice.fallbacks)

    def test_a_failing_tool_stops_being_the_first_choice(self):
        request = {
            "normalized_request": "is a room available on september 18",
            "verification_required": True,
        }
        first = _capability("web_search", **request)
        self.assertEqual(first, caps.BROWSER_CONTROL)

        failures: dict = {}
        caps.note_failure(failures, caps.BROWSER_CONTROL)
        caps.note_failure(failures, caps.BROWSER_CONTROL)

        self.assertEqual(
            _capability("web_search", failures=failures, **request),
            caps.WEB_SEARCH,
        )

    def test_the_same_failing_tool_is_not_chosen_forever(self):
        failures: dict = {}
        for _ in range(5):
            caps.note_failure(failures, caps.WEB_SEARCH)

        self.assertTrue(caps.exhausted(failures, caps.WEB_SEARCH))
        self.assertNotEqual(
            _capability(
                "web_search", failures=failures,
                normalized_request="nvidia price now",
                requires_external_evidence=True,
            ),
            caps.WEB_SEARCH,
        )

    def test_a_tool_that_works_gets_its_record_back(self):
        failures: dict = {}
        caps.note_failure(failures, caps.WEB_SEARCH)
        caps.note_failure(failures, caps.WEB_SEARCH)
        self.assertTrue(caps.exhausted(failures, caps.WEB_SEARCH))

        caps.note_success(failures, caps.WEB_SEARCH)

        self.assertFalse(caps.exhausted(failures, caps.WEB_SEARCH))

    def test_failures_are_counted_per_capability(self):
        failures: dict = {}
        caps.note_failure(failures, caps.BROWSER_CONTROL)
        caps.note_failure(failures, caps.BROWSER_CONTROL)

        self.assertTrue(caps.exhausted(failures, caps.BROWSER_CONTROL))
        self.assertFalse(caps.exhausted(failures, caps.WEB_SEARCH))

    def test_an_empty_failure_record_changes_nothing(self):
        request = {
            "normalized_request": "nvidia price now",
            "requires_external_evidence": True,
        }

        self.assertEqual(
            _capability("web_search", **request),
            _capability("web_search", failures={}, **request),
        )


class LoggingTests(unittest.TestCase):
    """Criterion 8: readable, and showing the working."""

    def test_the_block_shows_factors_candidates_and_the_choice(self):
        _g, _d, choice = _chain(
            "web_search",
            normalized_request="is a room available on september 18",
            topic="hotel X", verification_required=True,
        )
        block = choice.log_block()

        self.assertIn("[Capability]", block)
        self.assertIn("live_state_required: true", block)
        self.assertIn("Candidates:", block)
        self.assertIn("browser_control:", block)
        self.assertIn("web_search:", block)
        self.assertIn("Selected: browser_control", block)
        self.assertIn("Fallback:", block)
        self.assertIn("Why:", block)

    def test_every_candidate_carries_a_score(self):
        _g, _d, choice = _chain(
            "web_search", normalized_request="nvidia price",
            requires_external_evidence=True,
        )

        self.assertGreaterEqual(len(choice.candidates), 3)
        for candidate in choice.candidates:
            with self.subTest(candidate=candidate.capability):
                self.assertGreaterEqual(candidate.score, 0.0)
                self.assertLessEqual(candidate.score, 1.0)

    def test_candidates_are_ordered_best_first(self):
        _g, _d, choice = _chain(
            "web_search", normalized_request="nvidia price",
            requires_external_evidence=True,
        )
        scores = [candidate.score for candidate in choice.candidates]

        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(choice.candidates[0].capability, choice.capability)


class SafetyTests(unittest.TestCase):
    """It has to fail safe, like every other layer in this chain."""

    def test_an_unknown_label_never_reaches_for_a_machine(self):
        self.assertIn(
            _capability("teleportation"),
            (caps.DIRECT_ANSWER, caps.NOTHING),
        )

    def test_a_bare_route_still_produces_a_choice(self):
        class Bare:
            intent = "conversation"

        goal = goal_intent.read(Bare())
        decision = interaction.decide(Bare(), goal=goal)

        self.assertIn(
            caps.select(goal, decision, route=Bare()).capability,
            caps.ALL_CAPABILITIES,
        )

    def test_selection_makes_no_model_call(self):
        # Structural, like every other layer in the chain: there is nothing
        # to call. 4E.4 added weighing, not a second opinion.
        self.assertFalse(hasattr(caps, "client"))
        for _ in range(200):
            _capability(
                "web_search", normalized_request="hotels in seoul",
                requires_external_evidence=True,
            )


class DispatchHandoffTests(unittest.TestCase):
    """Choosing an ability has to actually run it.

    Reported live: "Does the Lotte Hotel have a room available September
    18?" selected browser_control correctly and then nothing happened. The
    browser handler was reachable through exactly one door -- route.intent
    == "computer_action" *and* computer_operation == "browser_action", both
    the router's labels -- so a capability the router had labelled
    web_search had no execution path at all. Not an agent (browser control
    is not agent-dispatched), not a search (the capability was not
    web_search), not a handler (the label was not computer_action).
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self.engine._capability_failures.clear()
        self.engine.computer_control_mode.set_enabled(True)

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

    def test_the_live_case_selects_the_browser(self):
        _route, routing = self._routing(
            "does the lotte hotel have a room available september 18",
        )

        self.assertEqual(routing.capability.capability, caps.BROWSER_CONTROL)
        self.assertTrue(routing.decision.acts)

    def test_choosing_the_browser_actually_drives_it(self):
        route, routing = self._routing(
            "does the lotte hotel have a room available september 18",
        )

        message, _result = self.engine._run_browser_capability(
            route, routing, route.normalized_request,
        )

        # The harness records browser instructions rather than performing
        # them; what matters is that one was issued at all.
        self.assertTrue(
            self.engine.browser_control.actions or message,
            "the capability was chosen and nothing was dispatched",
        )

    def test_an_unavailable_browser_falls_back_rather_than_doing_nothing(self):
        route, routing = self._routing(
            "does the lotte hotel have a room available september 18",
        )
        self.engine.computer_control_mode.set_enabled(False)
        try:
            message, _result = self.engine._run_browser_capability(
                route, routing, route.normalized_request,
            )
        finally:
            self.engine.computer_control_mode.set_enabled(True)

        self.assertEqual(message, "")
        self.assertEqual(routing.capability.capability, caps.WEB_SEARCH)

    def test_a_raising_browser_falls_back_and_is_remembered(self):
        route, routing = self._routing(
            "does the lotte hotel have a room available september 18",
        )
        original = self.engine._handle_browser_action

        def explode(*_args, **_kwargs):
            raise RuntimeError("the page never loaded")

        self.engine._handle_browser_action = explode
        try:
            message, _result = self.engine._run_browser_capability(
                route, routing, route.normalized_request,
            )
        finally:
            self.engine._handle_browser_action = original

        self.assertEqual(message, "")
        self.assertEqual(routing.capability.capability, caps.WEB_SEARCH)
        self.assertEqual(
            self.engine._capability_failures.get("browser_control"), 1,
        )

    def test_the_fallback_is_the_one_selection_already_worked_out(self):
        _route, routing = self._routing(
            "does the lotte hotel have a room available september 18",
        )

        self.assertIn(caps.WEB_SEARCH, routing.capability.fallbacks)

    def test_falling_back_twice_stops_rather_than_looping(self):
        route, routing = self._routing(
            "does the lotte hotel have a room available september 18",
        )
        caps.note_failure(self.engine._capability_failures, caps.WEB_SEARCH)
        caps.note_failure(self.engine._capability_failures, caps.WEB_SEARCH)

        self.engine._fall_back_from(routing, caps.BROWSER_CONTROL)

        # web_search is exhausted, so nothing is swapped in and the turn
        # ends rather than cycling between two failing tools.
        self.assertEqual(routing.capability.capability, caps.BROWSER_CONTROL)


class StatusHonestyTests(unittest.TestCase):
    """She may not say she is checking when nothing was dispatched."""

    def test_a_bare_stalling_phrase_counts_as_a_promise(self):
        # Reported live: "One moment please." with no execution behind it.
        # The pattern required a trailing "while I".
        for said in ("One moment please.", "One moment.", "Bear with me."):
            with self.subTest(said=said):
                self.assertTrue(ActionCommitmentGuard.broken_promise(
                    said, action_performed=False,
                ))

    def test_the_same_phrase_is_fine_once_something_ran(self):
        self.assertFalse(ActionCommitmentGuard.broken_promise(
            "One moment please.", action_performed=True,
        ))

    def test_a_plain_answer_is_not_a_promise(self):
        for said in (
            "Recursion is a function calling itself.",
            "The exchange rate is 13.5 KRW.",
        ):
            with self.subTest(said=said):
                self.assertFalse(ActionCommitmentGuard.broken_promise(
                    said, action_performed=False,
                ))


if __name__ == "__main__":
    unittest.main()
