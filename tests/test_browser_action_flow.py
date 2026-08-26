import unittest

from brain.browser_action_planner import ActionPlanResult, PendingConfirmation
from brain.chat_engine import ChatEngine
from brain.intent_router import IntentDecision
from security.computer_consent import ComputerConsentGate
from security.computer_control_mode import ComputerControlMode
from tools.computer_control.computer_control import ComputerActionResult, PreparedComputerAction


class FakeBriefResponses:
    def __init__(self):
        self.calls = []

    def generate(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        return f"locked:{kind}"


class FakeAgentConsent:
    def __init__(self):
        self.cleared = False

    def clear(self):
        self.cleared = True


class FakeBrowserActionPlanner:
    def __init__(self, act_result=None, resume_result=None):
        self.act_result = act_result
        self.resume_result = resume_result
        self.act_calls = []
        self.act_contexts = []
        self.resume_calls = []

    def act(self, goal, *, context="", **_kwargs):
        self.act_calls.append(goal)
        self.act_contexts.append(context)
        return self.act_result

    def resume_confirmed_click(self, *, tab_index, element_id, element_label=""):
        self.resume_calls.append((tab_index, element_id, element_label))
        return self.resume_result


class BrowserActionFlowTests(unittest.TestCase):
    def engine_with(self, planner, *, mode_enabled=True):
        engine = ChatEngine.__new__(ChatEngine)
        engine.brief_responses = FakeBriefResponses()
        engine.browser_action_planner = planner
        engine.computer_consent = ComputerConsentGate()
        engine.computer_control_mode = ComputerControlMode(enabled=mode_enabled)
        engine.agent_consent = FakeAgentConsent()
        return engine

    @staticmethod
    def route(target="click Settings on this GitHub page"):
        return IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request=target,
            speech_act="action_request",
            action_requested=True,
            action_target=target,
            computer_operation="browser_action",
        )

    def test_control_mode_off_never_calls_the_planner(self):
        planner = FakeBrowserActionPlanner()
        engine = self.engine_with(planner, mode_enabled=False)

        response, returned = engine._handle_computer_action(self.route())

        self.assertEqual(response, "locked:control_mode_off")
        self.assertIsNone(returned)
        self.assertEqual(planner.act_calls, [])

    def test_ordinary_click_speaks_the_planners_own_summary(self):
        planner = FakeBrowserActionPlanner(
            act_result=ActionPlanResult("done", "Clicked Settings on the GitHub page.")
        )
        engine = self.engine_with(planner)
        route = self.route()

        response, returned = engine._handle_computer_action(route)

        self.assertEqual(response, "Clicked Settings on the GitHub page.")
        self.assertEqual(planner.act_calls, [route.normalized_request])
        # Not run back through the LLM-based brief_responses generator --
        # this is the planner's own tool-grounded result, not a status kind.
        self.assertEqual(engine.brief_responses.calls, [])
        self.assertEqual(returned.status, "ui_action_done")
        self.assertEqual(returned.operation, "browser_action")
        self.assertTrue(returned.succeeded)

    def test_browser_planner_receives_the_original_short_utterance(self):
        # A router may remove question words or punctuation.  BrowserAction-
        # Planner's deterministic follow-up parsers need the original spoken
        # request rather than a two-line "Original user request" annotation.
        planner = FakeBrowserActionPlanner(
            act_result=ActionPlanResult("done", "Clicked Images.")
        )
        engine = self.engine_with(planner)
        route = self.route("click images in here")

        engine._handle_computer_action(
            route,
            original_request="Can you click images in here?",
        )

        self.assertEqual(planner.act_calls, ["Can you click images in here?"])

    def test_failed_step_speaks_the_planners_own_summary(self):
        planner = FakeBrowserActionPlanner(
            act_result=ActionPlanResult("failed", "I couldn't find that element.")
        )
        engine = self.engine_with(planner)

        response, returned = engine._handle_computer_action(self.route())

        self.assertEqual(response, "I couldn't find that element.")
        self.assertEqual(returned.status, "ui_action_failed")
        self.assertFalse(returned.succeeded)

    def test_an_internal_planner_instruction_is_never_spoken_aloud(self):
        # Found live: a failed browser step read its own note-to-self out
        # loud -- "That element was not in the latest live page scan. Call
        # describe page before acting." That sentence is addressed to the
        # model, and means nothing to the user.
        planner = FakeBrowserActionPlanner(
            act_result=ActionPlanResult(
                "failed",
                "That element was not in the latest live page scan. "
                "Call describe_page before acting.",
                failure_code="unobserved",
            )
        )
        engine = self.engine_with(planner)

        response, returned = engine._handle_computer_action(self.route())

        self.assertNotIn("describe_page", response)
        self.assertIn("lost track of that element", response)
        self.assertEqual(returned.status, "ui_action_failed")

    def test_an_honest_failure_sentence_is_kept_word_for_word(self):
        # The other half: "there's no book button on this page" is a real
        # answer and must not be replaced with a generic apology.
        planner = FakeBrowserActionPlanner(
            act_result=ActionPlanResult(
                "failed",
                "There's no book button on this listing page.",
                failure_code="no_commit_control",
            )
        )
        engine = self.engine_with(planner)

        response, _returned = engine._handle_computer_action(self.route())

        self.assertEqual(response, "There's no book button on this listing page.")

    def test_committing_element_offers_a_confirmation_instead_of_clicking(self):
        pending = PendingConfirmation(
            tab_index=0, element_id="e3", element_label="Submit Order",
            url="https://shop.example/checkout",
            action="click",
            scan_id="scan-123",
            href="/checkout/submit",
        )
        planner = FakeBrowserActionPlanner(
            act_result=ActionPlanResult(
                "needs_confirmation",
                "Clicking 'Submit Order' needs confirmation first.",
                pending=pending,
            )
        )
        engine = self.engine_with(planner)

        response, returned = engine._handle_computer_action(
            self.route("submit my order on this page")
        )

        self.assertEqual(response, "locked:ui_action_offer")
        kind, kwargs = engine.brief_responses.calls[0]
        self.assertEqual(kind, "ui_action_offer")
        self.assertEqual(kwargs["subject"], "Submit Order")
        self.assertEqual(kwargs["operation"], "browser_action")
        self.assertEqual(returned.status, "prepared")
        self.assertFalse(returned.succeeded)

        offered = engine.computer_consent.peek()
        self.assertIsNotNone(offered)
        self.assertEqual(offered.prepared.operation, "browser_action")
        self.assertEqual(offered.prepared.tab_index, 0)
        self.assertEqual(offered.prepared.target, "e3")
        self.assertEqual(offered.prepared.display_name, "Submit Order")
        self.assertEqual(offered.prepared.url, "https://shop.example/checkout")
        self.assertEqual(offered.prepared.browser_scan_id, "scan-123")
        self.assertEqual(offered.prepared.browser_href, "/checkout/submit")

    def test_confirmed_click_resumes_the_exact_stored_element_not_a_new_goal(self):
        approved = PreparedComputerAction(
            operation="browser_action",
            target="e3",
            display_name="Submit Order",
            tab_index=0,
        )
        planner = FakeBrowserActionPlanner(
            resume_result=ActionPlanResult("done", "Clicked Submit Order.")
        )
        engine = self.engine_with(planner)
        route = self.route("submit my order on this page")

        response, returned = engine._handle_computer_action(
            route, approved_action=approved,
        )

        self.assertEqual(response, "Clicked Submit Order.")
        self.assertEqual(planner.resume_calls, [(0, "e3", "Submit Order")])
        self.assertEqual(planner.act_calls, [])
        self.assertEqual(returned.status, "ui_action_done")
        self.assertEqual(returned.operation, "browser_action")


if __name__ == "__main__":
    unittest.main()
