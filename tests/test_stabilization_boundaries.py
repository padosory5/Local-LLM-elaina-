"""The release invariants at interpretation, execution and emission boundaries."""
import unittest
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agents.research_agent import ResearchAgent, ResearchResult
from brain import browser_navigation as nav, browser_progress, candidate_fit as cf
from brain import recommendation_state as rs
from brain.intent_router import IntentDecision, SemanticIntentRouter
from brain.resolved_turn import command_was_fused
from brain.task_session import TaskSessionStore
from tests.turn_harness import build_engine
from tools.browser_control.browser_control import BrowserActionResult, BrowserControl
from tools.computer_control.computer_control import ComputerActionResult, ComputerControl, PreparedComputerAction
from tools.browser_control.safe_browser import SafeBrowserControl
from security.policy import PolicyEngine


def page(url="https://example.com", **kwargs):
    values = dict(url=url, title="Example destination", text="Visible destination content",
                  identity="dispatched:17", correlated=True)
    values.update(kwargs)
    return nav.PageEvidence(**values)


class NavigationBoundaryTests(unittest.TestCase):
    def test_dispatch_preserves_failure_and_does_not_promote_legacy_boolean(self):
        for dispatched, expected in ((BrowserActionResult("failed", "navigate", "Navigation failed."), "failed"),
                                     (True, "url_dispatched")):
            with self.subTest(dispatched=dispatched):
                control = ComputerControl(PolicyEngine(), browser=SafeBrowserControl(opener=lambda url: dispatched))
                result = control.execute(PreparedComputerAction("open_url", "example.com", "example.com", url="https://example.com"))
                self.assertEqual(result.status, expected)
                self.assertFalse(result.succeeded)

    def test_only_the_correlated_page_counts_even_if_an_old_tab_matches(self):
        old = page(correlated=False, identity="old:1")
        error = page(error_code="ERR_NAME_NOT_RESOLVED")
        result = nav.verify(nav.start("example.com", "https://example.com"), (old, error))
        self.assertEqual(result.classification, "dns_error")
        self.assertFalse(result.arrived)

    def test_a_background_matching_tab_alone_is_not_evidence(self):
        result = nav.verify(nav.start("example.com", "https://example.com"),
                            (page(correlated=False),))
        self.assertEqual(result.status, nav.UNVERIFIED)

    def test_valid_page_and_internal_redirect_are_verified(self):
        for url in ("https://example.com/path", "https://m.example.com/path"):
            with self.subTest(url=url):
                self.assertTrue(nav.verify(nav.start("example.com", "https://example.com"),
                                           (page(url),)).arrived)

    def test_parent_institution_is_not_the_specifically_requested_service(self):
        result = nav.verify(nav.start("iss.washington.edu", "https://iss.washington.edu"),
                            (page("https://washington.edu", title="University of Washington"),))
        self.assertFalse(result.arrived)
        self.assertEqual(result.classification, "wrong_destination")

    def test_error_interstitial_and_wrong_destination_never_verify(self):
        for observed, classification in (
            (page(error_code="ERR_CONNECTION_REFUSED"), "connection_error"),
            (page(http_status=503), "connection_error"),
            (page(title="Just a moment", text="Verify you are human"), "interstitial"),
            (page(title="Your connection is not private"), "interstitial"),
            (page("https://other.example"), "wrong_destination"),
            (page("about:blank"), "blank"),
            (page("https://www.google.com/search?q=example.com"), "search_results"),
            (page(title="elsewhere.com | Home"), "wrong_destination"),
            (page(document_url="https://other.example"), "wrong_destination"),
        ):
            with self.subTest(classification=classification, observed=observed):
                result = nav.verify(nav.start("example.com", "https://example.com"), (observed,))
                self.assertFalse(result.arrived)
                self.assertEqual(result.classification, classification)

    def test_title_alone_or_unreadable_content_hedges(self):
        for observed in (page(text=""), page(readable=False), page(identity="")):
            with self.subTest(observed=observed):
                self.assertEqual(nav.verify(nav.start("example.com", "https://example.com"),
                                             (observed,)).status, nav.UNVERIFIED)

    def test_conflicting_receipts_are_ambiguous(self):
        result = nav.verify(nav.start("example.com", "https://example.com"),
                            (page(), page(identity="another:18")))
        self.assertEqual(result.classification, "ambiguous")

    def test_cdp_reads_the_dispatched_page_even_if_active_tab_changes(self):
        from tests.test_browser_control import _FakePage, _FakeObserver
        dispatched = _FakePage()
        observer = _FakeObserver(dispatched)
        observer._resolve_page = Mock(side_effect=AssertionError("must not choose a tab again"))
        result = BrowserControl(observer=observer).navigate(0, "https://example.com")
        self.assertTrue(result.navigation.arrived)
        self.assertEqual(result.navigation.observation_id, f"cdp:{id(dispatched)}")
        observer._resolve_page.assert_not_called()

    def test_dns_failure_keeps_requested_url_but_never_verifies(self):
        from tests.test_browser_control import _FakePage, _FakeObserver
        dispatched = _FakePage()
        dispatched.goto = Mock(side_effect=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
        result = BrowserControl(observer=_FakeObserver(dispatched)).navigate(0, dispatched.url)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.navigation.classification, "dns_error")

    def test_screen_wrong_window_cannot_verify(self):
        from tests.test_screen_browser_control import _observation, _element, _control, _FakeObserver
        observer = _FakeObserver([
            _observation(_element(), url="https://before.example", handle=1),
            _observation(_element(), handle=2),
        ])
        result = _control(observer).navigate("https://example.com")
        self.assertFalse(result.navigation.arrived)

    def test_screen_followup_binds_the_dispatched_window_after_cold_launch(self):
        from tests.test_screen_browser_service import _FakeFinder, _FakeScreenObserver, _screen_observation, _window
        from tools.screen_browser.screen_browser_service import ScreenBrowserObserverAdapter, ScreenBrowserControlAdapter
        finder = _FakeFinder([])
        screen = _FakeScreenObserver({1: _screen_observation(1), 2: _screen_observation(2)}, active_handle=2)
        observer = ScreenBrowserObserverAdapter(screen, finder)
        receipt = nav.verify(nav.start("example.com", "https://example.com"), (page(identity="hwnd:1:scan1"),))
        def navigate(url, **kwargs):
            finder._windows = [_window(1), _window(2, active=True)]
            return BrowserActionResult("navigated", "Opened", navigation=receipt)
        control = ScreenBrowserControlAdapter(SimpleNamespace(navigate=navigate), observer)
        control.navigate(None, "https://example.com")
        observer.describe_page()
        self.assertEqual(screen.observed, [1])

    def test_engine_uses_receipt_not_whichever_tab_is_visible(self):
        engine = build_engine()
        self.addCleanup(engine.close)
        error = nav.verify(nav.start("example.com", "https://example.com"),
                           (page(error_code="ERR_NAME_NOT_RESOLVED"),))
        engine.browser_observer.showing("https://example.com", "Working old tab")
        result, line = engine._verify_navigation(ComputerActionResult(
            "url_dispatched", "example.com", "example.com", "Dispatched",
            url="https://example.com", navigation=error,
        ), IntentDecision("computer_action", 1.0, "open example.com", action_target="example.com"))
        self.assertFalse(result.succeeded)
        self.assertNotIn("is open", line)
        self.assertEqual(engine.browser_observer.calls, [])

    def test_unreadable_attempt_still_keeps_the_user_supplied_spelling(self):
        engine = build_engine()
        self.addCleanup(engine.close)
        receipt = nav.verify(nav.start("isss.washington.edu", "https://isss.washington.edu"), ())
        engine._verify_navigation(ComputerActionResult("url_dispatched", "isss.washington.edu", "ISS", "Sent",
            url=receipt.url, navigation=receipt), IntentDecision("computer_action", 1.0, "open ISS", action_target="isss.washington.edu"))
        self.assertEqual(engine._navigation_history, ("https://isss.washington.edu",))

    def test_failed_preparation_cannot_reuse_existing_recovery_page(self):
        engine = build_engine()
        self.addCleanup(engine.close)
        engine.computer_control.prepare = Mock(return_value=SimpleNamespace(prepared=None))
        engine._look_at = Mock(side_effect=AssertionError("no dispatch, no verification"))
        request = replace(nav.start("openExample.com", "https://openexample.com",
                                    command_fused=True), status=nav.ERROR_PAGE)
        result, line = engine._recover_navigation(request)
        self.assertFalse(result.arrived)
        self.assertIn("couldn't try", line)


class RecoveryProvenanceTests(unittest.TestCase):
    def test_outages_do_not_authorize_stripping_real_domain_names(self):
        for host in ("openai.com", "opentable.com", "openexample.com"):
            with self.subTest(host=host):
                self.assertEqual(nav.recovery_candidates(nav.start(host, host)), ())

    def test_parser_segmentation_is_required_for_fusion(self):
        for raw, normalized, expected in (
            ("openZillow.com", "openZillow.com", True),
            ("opennaver.com", "open naver.com", True),
            ("openai.com", "open openai.com", False),
            ("open openai.com", "open openai.com", False),
        ):
            with self.subTest(raw=raw):
                target = raw.removeprefix("open ")
                route = IntentDecision("computer_action", 1.0, normalized,
                                       computer_operation="open_url", action_target=target)
                self.assertEqual(command_was_fused(raw, route), expected)

    def test_relative_counts_correct_only_the_active_host_label(self):
        for said, expected in (
            ("remove one S", "iss.washington.edu"),
            ("one fewer S", "iss.washington.edu"),
            ("actually two S's", "iss.washington.edu"),
            ("add one S", "issss.washington.edu"),
        ):
            with self.subTest(said=said):
                self.assertEqual(browser_progress.respelled_address("isss.washington.edu", said), expected)
        self.assertEqual(browser_progress.respelled_address("assess.com", "remove one S"), "")
        self.assertEqual(browser_progress.respelled_address("isss.washington.edu",
                         "only one S. Open naver.com"), "")
        self.assertEqual(browser_progress.respelled_address("https://isss.washington.edu/students?q=new",
                         "remove one S"), "https://iss.washington.edu/students?q=new")

    def test_intermediate_spellings_cannot_also_change_other_letters(self):
        self.assertEqual(nav.spellings_between("isss.washington.edu", "is.washington.edu"),
                         ("iss.washington.edu",))
        self.assertEqual(nav.spellings_between("isssx.washington.edu", "is.washington.edu"), ())


class TurnAuthorityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.text = "Find me an electric guitar under 500,000 won."
        self.engine = build_engine({self.text: dict(intent="web_search", confidence=1.0,
            normalized_request=self.text, search_query=self.text, topic="electric guitar",
            recommendation_needed=True, action_requested=True, requires_external_evidence=True)})
        self.addCleanup(self.engine.close)

    def test_new_request_retires_all_offers_before_consent_classification(self):
        self.engine.agent_consent.offer(intent="web_search", request="old rental lookup")
        self.engine.capability_offer.offer(capability_id="browser_control", goal="old rental lookup", offer_text="Want me to look?")
        self.engine.consent_classifier.classify = Mock(side_effect=AssertionError("not consent"))
        turn = self.engine._route_turn(self.text, timings={})
        self.assertEqual(turn.capability.capability, "web_search")
        self.assertIsNone(self.engine.agent_consent.peek())
        self.assertIsNone(self.engine.capability_offer.peek())
        self.assertEqual(turn.resolved.entity_type, "product")
        self.assertEqual(turn.problem.missing_dimension(), "")
        self.assertIn("electric", turn.resolved.search_query)

    def test_resolved_payload_survives_later_stale_session_state(self):
        turn = self.engine._route_turn(self.text, timings={})
        expected_query = turn.resolved.search_query
        self.engine.task_sessions.clear()
        self.engine.task_sessions.note_recommendation_turn("Find a studio in Seattle under $1500")
        self.engine._research_for_recommendation = Mock(return_value=None)
        self.engine.research_agent.research = Mock(return_value=ResearchResult("No verified products yet.", (expected_query,)))
        with patch.object(self.engine, "_route_turn", return_value=turn):
            self.engine.chat(self.text)
        self.assertEqual(self.engine.research_agent.research.call_args.kwargs["search_query"], expected_query)
        self.assertTrue(self.engine.research_agent.research.call_args.kwargs["query_is_resolved"])
        self.assertIs(self.engine._research_for_recommendation.call_args.kwargs["resolved"], turn.resolved)
        with self.assertRaises(FrozenInstanceError):
            turn.resolved.subject = "rentals"

    def test_browser_fallback_query_is_resolved_before_execution(self):
        from brain import capability_selection as cs
        select = cs.select
        def use_browser(*args, **kwargs):
            return replace(select(*args, **kwargs), capability=cs.BROWSER_CONTROL)
        with patch.object(cs, "select", side_effect=use_browser):
            turn = self.engine._route_turn(self.text, timings={})
        self.assertIn(cs.WEB_SEARCH, turn.capability.fallbacks)
        self.engine.task_sessions.clear()
        self.engine.task_sessions.note_recommendation_turn("Find studios in Seattle under $1500")
        self.engine._fall_back_from(turn, cs.BROWSER_CONTROL)
        self.assertEqual(turn.capability.capability, cs.WEB_SEARCH)
        self.assertIn("electric", turn.resolved.search_query)
        self.assertIn("South Korea", turn.resolved.search_query)
        self.assertNotIn("Seattle", turn.resolved.search_query)

    def test_correction_marker_does_not_keep_an_unrelated_task(self):
        store = TaskSessionStore()
        old = store.note_recommendation_turn("Find a studio apartment in Seattle under $1500")
        revised = store.note_recommendation_turn("Actually, I want an electric guitar under 500,000 won.")
        self.assertNotEqual(old.id, revised.id)
        self.assertNotIn("1500", revised.search_query())
        self.assertNotIn("Seattle", revised.search_query())

    def test_relevant_omitted_constraints_still_inherit(self):
        store = TaskSessionStore()
        first = store.note_recommendation_turn(self.text)
        second = store.note_recommendation_turn("in Korea though")
        self.assertEqual(first.id, second.id)
        self.assertIn("electric", second.search_query())
        self.assertIn("500,000", second.search_query())
        self.assertIn("Korea", second.search_query())

    def test_cancel_retires_action_and_agent_offer(self):
        self.engine._last_computer_action = "open_url"
        self.engine._last_computer_goal = "https://old.example"
        self.engine.agent_consent.offer(intent="web_search", request="old lookup")
        self.engine._route_turn("forget about that", timings={})
        self.assertFalse(self.engine._last_computer_goal)
        self.assertIsNone(self.engine.agent_consent.peek())

    def test_explicit_new_task_retires_machine_target_without_old_focus(self):
        self.engine._last_computer_action = "open_url"
        self.engine._last_computer_goal = "https://old.example"
        self.engine._route_turn(self.text, timings={})
        self.assertFalse(self.engine._last_computer_goal)

    def test_each_router_field_respects_transcript_even_when_normalization_was_correct(self):
        from tests.test_intent_router import FakeClient
        router = SemanticIntentRouter(FakeClient(dict(intent="conversation", confidence=1.0,
            normalized_request="Tell me about university", topic="universe", entity="universe",
            search_query="universe", action_target="universe")), "test")
        route = router.route("Tell me about university")
        for name in ("normalized_request", "topic", "entity", "search_query", "action_target"):
            self.assertNotIn("universe", getattr(route, name))

    def test_user_dispute_invalidates_success_before_any_model_call(self):
        for said in ("it didn't open", "that's not it", "the website isn't open", "wrong page", "no, that failed"):
            with self.subTest(said=said):
                self.engine._last_computer_action = "open_url"
                self.engine._last_computer_goal = "https://example.com"
                self.engine._navigation = nav.verify(nav.start("example.com", "https://example.com"), (page(),))
                turn = self.engine._route_turn(said, timings={})
                self.assertEqual(self.engine._navigation.status, nav.DISPUTED)
                self.assertEqual(turn.route.computer_operation, "open_url")
                self.assertEqual(turn.route.action_target, "https://example.com")

    def test_new_explicit_target_is_not_a_dispute_or_url_edit(self):
        self.engine._last_computer_action = "open_url"
        self.engine._last_computer_goal = "https://isss.washington.edu"
        route = IntentDecision("computer_action", 1.0, "open naver.com",
                               action_target="naver.com", computer_operation="open_url")
        resolved, _ = self.engine._rescue_capability_route(route, "open naver.com")
        self.assertEqual(resolved.action_target, "naver.com")
        self.assertFalse(browser_progress.disputes_last_action("that failed experiment was interesting"))

    def test_remembered_clarification_is_scoped_to_object(self):
        store = TaskSessionStore()
        guitar = store.note_recommendation_turn(self.text)
        answered = store.answer_recommendation_dimension(guitar.id, rs.BUDGET, "same as I said")
        self.assertIsNotNone(answered)
        headphones = store.note_recommendation_turn("I want headphones")
        self.assertIsNone(store.answer_recommendation_dimension(headphones.id, rs.BUDGET, "same as I said"))


class TypeAndQueryBoundaryTests(unittest.TestCase):
    def test_standalone_lookup_resolves_locale_before_the_research_boundary(self):
        from brain.deliberation.goal_intent import read
        engine = build_engine()
        self.addCleanup(engine.close)
        for query, local in (("packing peanuts retailers", True),
                             ("Korean restaurants near University of Washington", False)):
            route = IntentDecision("web_search", 1.0, query, search_query=query)
            resolved = engine._resolved_search_query(route, read(route))
            self.assertEqual("South Korea" in resolved, local)
    def test_approximate_budget_retains_tolerance_but_hard_ceiling_does_not(self):
        for relation, viable in (("around", True), ("under", False)):
            with self.subTest(relation=relation):
                problem = TaskSessionStore().note_recommendation_turn(f"Find an electric guitar {relation} 500,000 won")
                fit = cf.evaluate([dict(title="Cort Electric Guitar", summary="550,000 won")], problem)[0]
                self.assertEqual(fit.viable, viable)

    def test_resolved_queries_do_not_get_localized_again(self):
        locale = SimpleNamespace(localize_query=lambda q: q + " in South Korea")
        search = Mock(return_value="Verified source text")
        agent = ResearchAgent(search, locale=locale)
        query = "Korean restaurants near University of Washington"
        agent.research(request=query, search_query=query, query_is_resolved=True)
        self.assertEqual(search.call_args.args[0], query)
        agent.research(request="packing peanuts", search_query="packing peanuts")
        self.assertIn("South Korea", search.call_args.args[0])

    def test_product_constraints_and_type_survive_query_and_ranking(self):
        problem = TaskSessionStore().note_recommendation_turn("Find me an electric guitar under 500,000 won")
        candidates = [
            dict(title="85 Easy Electric Guitar Songs", summary="500,000 won", url="https://example.com/articles/songs"),
            dict(title="Electric guitar tutorial", summary="Learn here"),
            dict(title="Cort Electric Guitar", summary="480,000 won", url="https://shop.example/product/1"),
            dict(title="Other Electric Guitar", summary="600,000 won", url="https://shop.example/product/2"),
        ]
        fits = cf.evaluate(candidates, problem)
        self.assertEqual([fit.name for fit in fits if fit.viable], ["Cort Electric Guitar"])
        self.assertEqual(cf.off_target("85 Easy Electric Guitar Songs", "", "", cf.ANY), "")

    def test_bare_metadata_does_not_discard_a_valid_retrieved_product(self):
        problem = TaskSessionStore().note_recommendation_turn("Find me an electric guitar under 500,000 won")
        fit = cf.evaluate([dict(title="Cort Electric Guitar", summary="480,000 won")], problem)[0]
        self.assertTrue(fit.viable)


class EmissionBoundaryTests(unittest.TestCase):
    def test_final_repetition_retry_cannot_reintroduce_an_unperformed_promise(self):
        from brain.action_commitment import ActionCommitmentGuard
        engine = build_engine()
        self.addCleanup(engine.close)
        with patch.object(engine, "_final_response_check", return_value="I'll open the website now.") as final:
            reply = engine.chat("Tell me something interesting")
        final.assert_called_once()
        self.assertFalse(ActionCommitmentGuard.promises_action(reply))

    def test_final_repetition_retry_keeps_an_ordinary_answer(self):
        engine = build_engine()
        self.addCleanup(engine.close)
        with patch.object(engine, "_final_response_check", return_value="Octopuses have three hearts."):
            reply = engine.chat("Tell me something interesting")
        self.assertIn("three hearts", reply)


if __name__ == "__main__":
    unittest.main()
