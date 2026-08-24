import json
import unittest

from brain.browser_action_planner import (
    ActionPlanResult as BrowserActionPlanResult,
    PendingConfirmation as BrowserPendingConfirmation,
)
from brain.desktop_action_planner import (
    ActionPlanResult as DesktopActionPlanResult,
    PendingConfirmation as DesktopPendingConfirmation,
)
from brain.task_planner import (
    TaskPlanner,
    TaskState,
    TaskStep,
    _MAX_CONSECUTIVE_FAILURES,
)


class FakeClient:
    """Returns one queued JSON decision per .chat() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps(self._responses.pop(0))}}


class FakeDesktopExecutor:
    def __init__(self, *, act_results=None, resume_result=None):
        self._act_results = list(act_results or [])
        self._resume_result = resume_result
        self.act_calls = []
        self.resume_calls = []

    def act(self, goal):
        self.act_calls.append(goal)
        return self._act_results.pop(0)

    def resume_confirmed_click(self, *, window_title, control_name, window_snapshot=None):
        self.resume_calls.append((window_title, control_name, window_snapshot))
        return self._resume_result


class FakeBrowserExecutor:
    def __init__(self, *, act_results=None, resume_result=None):
        self._act_results = list(act_results or [])
        self._resume_result = resume_result
        self.act_calls = []
        self.resume_calls = []

    def act(self, goal):
        self.act_calls.append(goal)
        return self._act_results.pop(0)

    def resume_confirmed_action(self, **kwargs):
        self.resume_calls.append(kwargs)
        return self._resume_result


class FakeComputerControlMode:
    def __init__(self, enabled):
        self.enabled = enabled


def _planner(
    responses,
    *,
    desktop_results=None,
    browser_results=None,
    desktop_resume=None,
    browser_resume=None,
    computer_control_mode=None,
    browser_control_enabled=True,
    max_steps=8,
):
    desktop = FakeDesktopExecutor(act_results=desktop_results, resume_result=desktop_resume)
    browser = FakeBrowserExecutor(act_results=browser_results, resume_result=browser_resume)
    planner = TaskPlanner(
        client=FakeClient(responses),
        model="qwen3:8b",
        keep_alive=-1,
        agent_registry=None,
        desktop_action_planner=desktop,
        browser_action_planner=browser,
        computer_control_mode=computer_control_mode,
        browser_control_enabled=browser_control_enabled,
        max_steps=max_steps,
    )
    return planner, desktop, browser


class TaskPlannerTests(unittest.TestCase):
    def test_executes_a_single_step_task_to_completion(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capability": "browser_control",
                    "sub_goal": "Search Google for hotels in Guam.",
                    "rationale": "Start the research.",
                },
                {"done": True, "summary": "Found several hotels in Guam."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Opened search results."),
            ],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Found several hotels in Guam.")
        self.assertEqual(browser.act_calls, ["Search Google for hotels in Guam."])
        self.assertEqual(result.task_state.step_count, 1)

    def test_executes_multiple_steps_and_folds_collected_information(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels in Guam."},
                {"capability": "browser_control", "sub_goal": "Open the first result."},
                {"done": True, "summary": "Shortlist ready."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Search results shown."),
                BrowserActionPlanResult("done", "Opened Ocean View Hotel."),
            ],
        )

        result = planner.run("Find a hotel in Guam and make a shortlist.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(result.task_state.completed_steps), 2)
        self.assertEqual(
            result.task_state.collected_information,
            ["Search results shown.", "Opened Ocean View Hotel."],
        )

    def test_max_steps_bound_stops_the_loop(self):
        step_decision = {"capability": "browser_control", "sub_goal": "Keep looking."}
        planner, _, browser = _planner(
            responses=[step_decision] * 3,
            browser_results=[
                BrowserActionPlanResult("done", "Still looking.") for _ in range(3)
            ],
            max_steps=3,
        )

        result = planner.run("An open-ended goal.")

        self.assertEqual(result.status, "stopped")
        self.assertEqual(result.task_state.step_count, 3)

    def test_capability_unavailable_short_circuits_before_executing(self):
        planner, desktop, _ = _planner(
            responses=[
                {"capability": "ui_control", "sub_goal": "Open Notepad."},
            ],
            desktop_results=[DesktopActionPlanResult("done", "Opened Notepad.")],
            computer_control_mode=FakeComputerControlMode(enabled=False),
        )

        result = planner.run("Write something in Notepad.")

        self.assertEqual(result.status, "capability_unavailable")
        self.assertEqual(desktop.act_calls, [])

    def test_needs_confirmation_returns_pending_details_and_pauses(self):
        pending = BrowserPendingConfirmation(
            tab_index=1, element_id="scan1-e2", element_label="Buy now",
            url="https://example.com", action="click", scan_id="scan1",
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Click Buy now."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "needs_confirmation", "Clicking 'Buy now' needs confirmation.",
                    pending=pending,
                ),
            ],
        )

        result = planner.run("Buy the item.")

        self.assertEqual(result.status, "needs_confirmation")
        self.assertEqual(result.task_state.status, "needs_confirmation")
        self.assertEqual(result.pending_capability, "browser_control")
        self.assertIsNotNone(result.pending_step)
        self.assertEqual(result.pending_prepared.operation, "browser_action")
        self.assertEqual(result.pending_prepared.target, "scan1-e2")
        self.assertEqual(result.pending_prepared.tab_index, 1)

    def test_resume_continues_after_confirmation_and_completes(self):
        pending = BrowserPendingConfirmation(
            tab_index=1, element_id="scan1-e2", element_label="Buy now",
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Click Buy now."},
                {"done": True, "summary": "Purchased."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "needs_confirmation", "needs confirmation", pending=pending,
                ),
            ],
            browser_resume=BrowserActionPlanResult("done", "Clicked Buy now."),
        )
        first = planner.run("Buy the item.")
        self.assertEqual(first.status, "needs_confirmation")

        result = planner.resume(
            first.task_state,
            approved_action=first.pending_prepared,
            step=first.pending_step,
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(browser.resume_calls[0]["element_id"], "scan1-e2")

    def test_single_failed_step_replans_and_can_still_finish(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"capability": "browser_control", "sub_goal": "Try a different site."},
                {"done": True, "summary": "Found a hotel after retrying."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "failed", "Could not load the page.",
                    failure_code="direct_target_not_found",
                ),
                BrowserActionPlanResult("done", "Opened search results."),
            ],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Found a hotel after retrying.")
        self.assertEqual(browser.act_calls, [
            "Search for hotels.", "Try a different site.",
        ])
        self.assertIn("browser_control", result.task_state.errors[0])
        self.assertEqual(result.task_state.consecutive_failures, 0)

    def test_consecutive_failures_past_the_budget_stop_the_task(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"capability": "browser_control", "sub_goal": "Try again."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "failed", "Could not load the page.", failure_code="x",
                ),
                BrowserActionPlanResult(
                    "failed", "Still could not load the page.", failure_code="x",
                ),
            ],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.summary, "Still could not load the page.")
        self.assertEqual(len(browser.act_calls), _MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(
            result.task_state.consecutive_failures, _MAX_CONSECUTIVE_FAILURES,
        )

    def test_a_success_between_failures_resets_the_budget(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "a"},
                {"capability": "browser_control", "sub_goal": "b"},
                {"capability": "browser_control", "sub_goal": "c"},
                {"done": True, "summary": "Done anyway."},
            ],
            browser_results=[
                BrowserActionPlanResult("failed", "f1", failure_code="x"),
                BrowserActionPlanResult("done", "s1"),
                BrowserActionPlanResult("failed", "f2", failure_code="x"),
            ],
        )

        result = planner.run("An open-ended goal.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(browser.act_calls), 3)

    def test_resume_failure_also_replans_and_can_still_finish(self):
        pending = BrowserPendingConfirmation(
            tab_index=1, element_id="scan1-e2", element_label="Buy now",
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Click Buy now."},
                {"capability": "browser_control", "sub_goal": "Try clicking again."},
                {"done": True, "summary": "Purchased on retry."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "needs_confirmation", "needs confirmation", pending=pending,
                ),
                BrowserActionPlanResult("done", "Clicked Buy now on retry."),
            ],
            browser_resume=BrowserActionPlanResult(
                "failed", "Click did not register.", failure_code="unverified_outcome",
            ),
        )
        first = planner.run("Buy the item.")
        self.assertEqual(first.status, "needs_confirmation")

        result = planner.resume(
            first.task_state,
            approved_action=first.pending_prepared,
            step=first.pending_step,
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Purchased on retry.")
        self.assertEqual(browser.resume_calls[0]["element_id"], "scan1-e2")
        self.assertEqual(
            browser.act_calls, ["Click Buy now.", "Try clicking again."],
        )

    def test_invalid_capability_in_decision_fails_safely(self):
        planner, desktop, browser = _planner(
            responses=[
                {"capability": "vision", "sub_goal": "Look at the screen."},
            ],
        )

        result = planner.run("Look at my screen and tell me what's there.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(desktop.act_calls, [])
        self.assertEqual(browser.act_calls, [])

    def test_planner_call_failure_fails_safely(self):
        planner, _, _ = _planner(responses=[])
        # No queued responses at all -- FakeClient.chat() will raise
        # IndexError, exercising the planner's own exception handling.

        result = planner.run("Do something.")

        self.assertEqual(result.status, "failed")


if __name__ == "__main__":
    unittest.main()
