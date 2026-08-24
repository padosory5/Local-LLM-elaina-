import unittest

from brain.chat_engine import ChatEngine
from brain.desktop_action_planner import (
    ActionPlanResult,
    DesktopActionPlanner,
    PendingConfirmation,
)
from brain.intent_router import IntentDecision
from security.computer_consent import ComputerConsentGate
from security.computer_control_mode import ComputerControlMode
from tools.computer_control.computer_control import (
    ComputerActionResult,
    PreparedComputerAction,
)
from tools.computer_control.windows_ui_observer import WindowInfo


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


class FakeEvents:
    def __init__(self):
        self.emitted = []

    def emit(self, name, **payload):
        self.emitted.append((name, payload))


class FakeComputerControl:
    def __init__(self, prepared_result, executed_result=None):
        self.prepared_result = prepared_result
        self.executed_result = executed_result or prepared_result
        self.enabled = True
        self.prepare_calls = []
        self.execute_calls = []

    def prepare(self, request):
        self.prepare_calls.append(request)
        return self.prepared_result

    def execute(self, prepared, *, confirmed=False):
        self.execute_calls.append((prepared, confirmed))
        return self.executed_result

    @staticmethod
    def requires_extra_confirmation(operation):
        return operation in {
            "force_quit_app",
            "delete_file",
            "delete_folder",
        }


class FakeDesktopActionPlanner:
    def __init__(self, act_result=None, resume_result=None):
        self.act_result = act_result
        self.resume_result = resume_result
        self.act_calls = []
        self.resume_calls = []
        self.resume_snapshots = []

    def act(self, goal, *, surface_context=None):
        self.act_calls.append(goal)
        return self.act_result

    def resume_confirmed_click(
        self, *, window_title, control_name, window_snapshot=None,
    ):
        self.resume_calls.append((window_title, control_name))
        self.resume_snapshots.append(window_snapshot)
        return self.resume_result


class OpenSettingsPlannerClient:
    """Model double that tries the unsafe Windows Settings fallback."""

    def __init__(self):
        self.messages = []

    def chat(self, **kwargs):
        self.messages = list(kwargs["messages"])
        return {
            "message": {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "open_app",
                        "arguments": {"app": "Settings"},
                    }
                }],
            }
        }


class NeverOpenSettings:
    def __init__(self):
        self.open_app_calls = []

    def open_app(self, target):
        self.open_app_calls.append(target)
        raise AssertionError(
            "A request scoped to this page must not open Windows Settings."
        )


class UIActionFlowTests(unittest.TestCase):
    def engine_with(self, planner, *, mode_enabled=True):
        engine = ChatEngine.__new__(ChatEngine)
        engine.brief_responses = FakeBriefResponses()
        engine.desktop_action_planner = planner
        engine.computer_consent = ComputerConsentGate()
        engine.computer_control_mode = ComputerControlMode(enabled=mode_enabled)
        engine.agent_consent = FakeAgentConsent()
        return engine

    @staticmethod
    def route(target="click the Next button in Setup Wizard"):
        return IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request=target,
            speech_act="action_request",
            action_requested=True,
            action_target=target,
            computer_operation="ui_action",
        )

    def test_control_mode_off_never_calls_the_planner(self):
        planner = FakeDesktopActionPlanner()
        engine = self.engine_with(planner, mode_enabled=False)

        response, returned = engine._handle_computer_action(self.route())

        self.assertEqual(response, "locked:control_mode_off")
        self.assertIsNone(returned)
        self.assertEqual(planner.act_calls, [])

    def test_ordinary_click_speaks_the_planners_own_summary(self):
        planner = FakeDesktopActionPlanner(
            act_result=ActionPlanResult("done", "Clicked Next in Setup Wizard.")
        )
        engine = self.engine_with(planner)
        route = self.route()

        response, returned = engine._handle_computer_action(route)

        self.assertEqual(response, "Clicked Next in Setup Wizard.")
        self.assertEqual(planner.act_calls, [route.normalized_request])
        # Not run back through the LLM-based brief_responses generator --
        # this is the planner's own tool-grounded result, not a status kind.
        self.assertEqual(engine.brief_responses.calls, [])
        self.assertEqual(returned.status, "ui_action_done")
        self.assertTrue(returned.succeeded)

    def test_failed_step_speaks_the_planners_own_summary(self):
        planner = FakeDesktopActionPlanner(
            act_result=ActionPlanResult("failed", "I couldn't find that control.")
        )
        engine = self.engine_with(planner)

        response, returned = engine._handle_computer_action(self.route())

        self.assertEqual(response, "I couldn't find that control.")
        self.assertEqual(returned.status, "ui_action_failed")
        self.assertFalse(returned.succeeded)

    def test_router_normalization_cannot_drop_current_page_scope(self):
        client = OpenSettingsPlannerClient()
        computer_control = NeverOpenSettings()
        planner = DesktopActionPlanner(
            client=client,
            model="qwen3:8b",
            keep_alive=-1,
            observer=object(),
            control=object(),
            computer_control=computer_control,
        )
        engine = self.engine_with(planner)
        engine._desktop_surface_for_turn = lambda: {
            "title": "sample/repository - Google Chrome",
            "application": "Chrome_WidgetWin_1",
            "kind": "browser",
            "identity": "hwnd:44",
            "handle": 44,
            "process_id": 55,
        }
        route = self.route("Click Settings")

        response, returned = engine._handle_computer_action(
            route,
            original_request="Click Settings on this page",
        )

        self.assertEqual(
            client.messages[1]["content"],
            "Click Settings\n"
            "Original user request: Click Settings on this page",
        )
        self.assertEqual(computer_control.open_app_calls, [])
        self.assertIn("current page", response)
        self.assertEqual(returned.status, "ui_action_failed")
        self.assertFalse(returned.succeeded)

    def test_committing_control_offers_a_confirmation_instead_of_clicking(self):
        snapshot = WindowInfo(
            "Checkout", is_active=True, handle=123, process_id=456,
        )
        pending = PendingConfirmation(
            window_title="Checkout",
            control_name="Submit Order",
            window_snapshot=snapshot,
        )
        planner = FakeDesktopActionPlanner(
            act_result=ActionPlanResult(
                "needs_confirmation",
                "Submit Order needs confirmation first.",
                pending=pending,
            )
        )
        engine = self.engine_with(planner)

        response, returned = engine._handle_computer_action(
            self.route("submit my order in Checkout")
        )

        self.assertEqual(response, "locked:ui_action_offer")
        kind, kwargs = engine.brief_responses.calls[0]
        self.assertEqual(kind, "ui_action_offer")
        self.assertEqual(kwargs["subject"], "Submit Order")
        self.assertEqual(returned.status, "prepared")
        self.assertFalse(returned.succeeded)

        offered = engine.computer_consent.peek()
        self.assertIsNotNone(offered)
        self.assertEqual(offered.prepared.operation, "ui_action")
        self.assertEqual(offered.prepared.window_title, "Checkout")
        self.assertEqual(offered.prepared.display_name, "Submit Order")
        self.assertIs(offered.prepared.window_snapshot, snapshot)

    def test_confirmed_click_resumes_the_exact_stored_control_not_a_new_goal(self):
        snapshot = WindowInfo(
            "Checkout", is_active=True, handle=123, process_id=456,
        )
        approved = PreparedComputerAction(
            operation="ui_action",
            target="Submit Order",
            display_name="Submit Order",
            window_title="Checkout",
            window_snapshot=snapshot,
        )
        planner = FakeDesktopActionPlanner(
            resume_result=ActionPlanResult("done", "Clicked Submit Order.")
        )
        engine = self.engine_with(planner)
        route = self.route("submit my order in Checkout")

        response, returned = engine._handle_computer_action(
            route, approved_action=approved,
        )

        self.assertEqual(response, "Clicked Submit Order.")
        self.assertEqual(planner.resume_calls, [("Checkout", "Submit Order")])
        self.assertEqual(planner.resume_snapshots, [snapshot])
        self.assertEqual(planner.act_calls, [])
        self.assertEqual(returned.status, "ui_action_done")


class ComputerActionFlowTests(unittest.TestCase):
    def engine_with(
        self,
        prepared_result,
        executed_result=None,
        *,
        mode_enabled=True,
    ):
        engine = ChatEngine.__new__(ChatEngine)
        engine.brief_responses = FakeBriefResponses()
        engine.computer_control = FakeComputerControl(
            prepared_result,
            executed_result,
        )
        engine.computer_consent = ComputerConsentGate()
        engine.computer_control_mode = ComputerControlMode(
            enabled=mode_enabled
        )
        engine.agent_consent = FakeAgentConsent()
        return engine

    @staticmethod
    def prepared(operation="open_app", target="Discord", entry_id="discord-entry"):
        return PreparedComputerAction(
            operation=operation,
            target=target,
            display_name=target,
            entry_id=entry_id,
        )

    def test_control_mode_off_never_prepares_or_executes(self):
        prepared = self.prepared()
        result = ComputerActionResult(
            status="prepared",
            target="Discord",
            display_name="Discord",
            message="Ready.",
            operation="open_app",
            entry_id="discord-entry",
            prepared=prepared,
        )
        engine = self.engine_with(result, mode_enabled=False)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Open Discord",
            speech_act="action_request",
            action_requested=False,
            action_target="Discord",
            computer_operation="open_app",
        )

        response, returned = engine._handle_computer_action(route)

        self.assertEqual(response, "locked:control_mode_off")
        self.assertIsNone(returned)
        self.assertEqual(engine.computer_control.prepare_calls, [])
        self.assertEqual(engine.computer_control.execute_calls, [])
        self.assertIsNone(engine.computer_consent.peek())

    def test_control_mode_on_executes_a_low_risk_prepared_action(self):
        prepared = self.prepared("open_app", "Steam", "steam-entry")
        ready = ComputerActionResult(
            "prepared", "Steam", "Steam", "Ready.",
            operation="open_app", prepared=prepared,
        )
        opened = ComputerActionResult(
            "opened", "Steam", "Steam", "Opened Steam.", operation="open_app",
        )
        engine = self.engine_with(ready, opened)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Open Steam",
            speech_act="action_request",
            action_requested=True,
            action_target="Steam",
            computer_operation="open_app",
        )

        response, _returned = engine._handle_computer_action(route)

        self.assertEqual(response, "locked:opened")
        self.assertEqual(
            engine.computer_control.execute_calls,
            [(prepared, False)],
        )

    def test_high_risk_acceptance_executes_only_the_stored_action(self):
        approved = self.prepared(
            "force_quit_app", "Discord", "discord-entry"
        )
        irrelevant = ComputerActionResult("blocked", "", "", "")
        stopped = ComputerActionResult(
            "force_quit", "Discord", "Discord", "Stopped Discord.",
            operation="force_quit_app",
        )
        engine = self.engine_with(irrelevant, stopped)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Open Discord",
            action_requested=True,
            action_target="Discord",
            computer_operation="force_quit_app",
        )

        response, _returned = engine._handle_computer_action(
            route,
            approved_action=approved,
        )

        self.assertEqual(response, "locked:force_quit")
        self.assertEqual(
            engine.computer_control.execute_calls,
            [(approved, True)],
        )
        self.assertEqual(engine.computer_control.prepare_calls, [])

    def test_force_quit_always_requires_second_confirmation(self):
        prepared = self.prepared("force_quit_app", "VS Code", "code-entry")
        ready = ComputerActionResult(
            "prepared", "VS Code", "VS Code", "Ready.",
            operation="force_quit_app", prepared=prepared,
        )
        engine = self.engine_with(ready)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Force quit VS Code",
            speech_act="action_request",
            action_requested=True,
            action_target="VS Code",
            computer_operation="force_quit_app",
        )

        response, _returned = engine._handle_computer_action(route)

        self.assertEqual(response, "locked:force_quit_offer")
        self.assertEqual(engine.computer_control.execute_calls, [])
        self.assertEqual(engine.computer_consent.peek().prepared, prepared)

    def test_low_risk_action_cannot_enter_through_confirmation_path(self):
        approved = self.prepared("open_app", "Discord", "discord-entry")
        irrelevant = ComputerActionResult("blocked", "", "", "")
        engine = self.engine_with(irrelevant)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Open Discord",
            speech_act="action_request",
            action_requested=True,
            action_target="Discord",
            computer_operation="open_app",
        )

        response, returned = engine._handle_computer_action(
            route,
            approved_action=approved,
        )

        self.assertEqual(response, "locked:blocked")
        self.assertIsNone(returned)
        self.assertEqual(engine.computer_control.execute_calls, [])

    def test_delete_always_requires_recycle_bin_confirmation(self):
        prepared = PreparedComputerAction(
            operation="delete_folder",
            target="Notes",
            display_name="Notes",
            path="C:/Users/test/Documents/Notes",
        )
        ready = ComputerActionResult(
            "prepared", "Notes", "Notes", "Ready.",
            operation="delete_folder", prepared=prepared,
        )
        engine = self.engine_with(ready)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Delete Notes from Documents",
            speech_act="action_request",
            action_requested=True,
            action_target="Notes",
            computer_operation="delete_folder",
            computer_location="Documents",
        )

        response, _returned = engine._handle_computer_action(route)

        self.assertEqual(response, "locked:delete_offer")
        self.assertEqual(engine.computer_control.execute_calls, [])
        self.assertEqual(engine.computer_consent.peek().prepared, prepared)

    def test_not_found_cannot_use_success_response(self):
        result = ComputerActionResult(
            "not_found", "Missing App", "", "I couldn't find Missing App.",
            operation="open_app",
        )
        engine = self.engine_with(result)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Open Missing App",
            action_requested=True,
            action_target="Missing App",
            computer_operation="open_app",
        )

        response, _returned = engine._handle_computer_action(route)

        self.assertEqual(response, "locked:not_found")
        self.assertEqual(engine.brief_responses.calls[0][0], "not_found")

    def test_unsupported_operation_never_calls_computer_control(self):
        result = ComputerActionResult("opened", "", "", "")
        engine = self.engine_with(result)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Disable a setting",
            action_target="Smart App Control",
            computer_operation="unsupported",
        )

        response, returned = engine._handle_computer_action(route)

        self.assertEqual(response, "locked:blocked")
        self.assertIsNone(returned)
        self.assertEqual(engine.computer_control.prepare_calls, [])
        self.assertEqual(engine.computer_control.execute_calls, [])

    def test_turning_mode_off_clears_pending_destructive_confirmation(self):
        prepared = self.prepared("force_quit_app", "Discord", "discord-entry")
        ready = ComputerActionResult(
            "prepared", "Discord", "Discord", "Ready.",
            operation="force_quit_app", prepared=prepared,
        )
        engine = self.engine_with(ready)
        engine.events = FakeEvents()
        engine.computer_consent.offer(prepared=prepared, reason="Requested.")

        active = engine.set_computer_control_mode(False)

        self.assertFalse(active)
        self.assertIsNone(engine.computer_consent.peek())
        self.assertEqual(
            engine.events.emitted[-1],
            (
                "computer_control_mode_changed",
                {"enabled": False, "available": True},
            ),
        )


if __name__ == "__main__":
    unittest.main()
