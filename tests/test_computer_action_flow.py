import unittest

from brain.chat_engine import ChatEngine
from brain.intent_router import IntentDecision
from security.computer_consent import ComputerConsentGate
from tools.computer_control import (
    ComputerActionResult,
    PreparedComputerAction,
)


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


class FakeComputerControl:
    def __init__(self, prepared_result, executed_result=None):
        self.prepared_result = prepared_result
        self.executed_result = executed_result or prepared_result
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


class ComputerActionFlowTests(unittest.TestCase):
    def engine_with(self, prepared_result, executed_result=None):
        engine = ChatEngine.__new__(ChatEngine)
        engine.brief_responses = FakeBriefResponses()
        engine.computer_control = FakeComputerControl(
            prepared_result,
            executed_result,
        )
        engine.computer_consent = ComputerConsentGate()
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

    def test_missing_takeover_prepares_but_never_executes(self):
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
        engine = self.engine_with(result)
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

        self.assertEqual(response, "locked:action_offer")
        self.assertIs(returned, result)
        self.assertEqual(engine.computer_control.execute_calls, [])
        self.assertEqual(engine.computer_consent.peek().prepared, prepared)

    def test_explicit_takeover_executes_prepared_action(self):
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

    def test_contextual_acceptance_executes_only_stored_action(self):
        approved = self.prepared("open_app", "Discord", "discord-entry")
        irrelevant = ComputerActionResult("blocked", "", "", "")
        opened = ComputerActionResult(
            "opened", "Discord", "Discord", "Opened Discord.", operation="open_app",
        )
        engine = self.engine_with(irrelevant, opened)
        route = IntentDecision(
            intent="computer_action",
            confidence=1,
            normalized_request="Open Discord",
            action_requested=True,
            action_target="Discord",
            computer_operation="open_app",
        )

        response, _returned = engine._handle_computer_action(
            route,
            approved_action=approved,
        )

        self.assertEqual(response, "locked:opened")
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


if __name__ == "__main__":
    unittest.main()
