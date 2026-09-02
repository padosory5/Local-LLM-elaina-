"""Twenty-two multi-step tasks, run through the real planner loop.

The core rule this phase exists to enforce: **a successful tool call is not a
successful goal**. Every scenario below drives the actual
:class:`~brain.task_planner.TaskPlanner` -- its real loop, its real budgets,
its real result adapters -- with scripted model decisions and scripted tool
results, and asserts which of the five terminal outcomes came out.

Deterministic on purpose, and offline. Half of these cases *cannot* be
produced reliably against a live machine: "the click succeeds and playback
never starts", "the model stalls three times", "the user takes the mouse back
between step two and step three". Scripting the tool result is the only way
to test the pipeline's response to them at all, and the pipeline's response
is what is under test here -- not the browser's.

Each scenario records, in the matrix beside this file: the goal, the planned
steps, the tool chosen, the expected state for each critical step, the
observed result, and the final outcome.
"""

import json
import unittest
from pathlib import Path

from brain import task_outcome
from brain.browser_action_planner import ActionPlanResult as BrowserResult
from brain.desktop_action_planner import (
    ActionPlanResult as DesktopResult,
    DesktopSurfaceContext,
)
from tests.test_task_planner import FakeComputerControlMode, _planner

MATRIX_PATH = Path(__file__).with_name("execution_matrix.json")


def _result(spec: dict):
    """Build the scripted tool result a scenario step describes."""
    kind = spec.get("tool", "browser_control")
    status = spec["status"]
    fields = {
        "summary": spec.get("summary", "step ran"),
        "failure_code": spec.get("failure_code", ""),
    }
    if kind == "ui_control":
        # A real desktop step reports the foreground application it actually
        # landed on, read back off the UI tree. That surface context *is*
        # the observation, so a scenario without one is honestly reported as
        # executed-but-unverified rather than verified.
        app = spec.get("observed_app", "")
        return DesktopResult(
            status,
            surface_context=DesktopSurfaceContext(app_name=app, is_active=bool(app)),
            **fields,
        )
    result = BrowserResult(status, **fields)
    if "verified" in spec:
        result = BrowserResult(status, verified=spec["verified"], **fields)
    return result


def _run(scenario: dict):
    desktop, browser = [], []
    for step in scenario["steps"]:
        (desktop if step.get("tool") == "ui_control" else browser).append(
            _result(step)
        )
    planner, _, _ = _planner(
        responses=scenario["plan"],
        desktop_results=desktop or None,
        browser_results=browser or None,
        max_steps=scenario.get("max_steps", 8),
        # ui_control has a real precondition: Desktop Control Mode must be
        # on, or the step never dispatches at all. Leaving it off made every
        # local-application scenario return capability_unavailable before
        # running a single step -- the harness testing the guard rather than
        # the behaviour behind it.
        computer_control_mode=FakeComputerControlMode(enabled=True),
    )
    return planner.run(scenario["goal"])


class ExecutionMatrixTests(unittest.TestCase):
    """Every scenario in execution_matrix.json, end to end."""

    @classmethod
    def setUpClass(cls):
        cls.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        cls.cases = cls.matrix["cases"]

    def test_the_matrix_covers_the_required_ground(self):
        self.assertGreaterEqual(len(self.cases), 20)
        kinds = {case["kind"] for case in self.cases}
        for required in (
            "success_browser", "success_local", "multi_step",
            "verification_failed", "recoverable", "non_recoverable",
            "needs_user_input", "cancelled", "retry_succeeds",
            "retry_exhausted", "compound",
        ):
            self.assertIn(required, kinds)

    def test_every_scenario_reaches_its_expected_outcome(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                result = _run(case)
                outcome = result.outcome()

                self.assertEqual(
                    outcome.outcome, case["expected_outcome"],
                    f"{case['id']}: {outcome.log_line()}",
                )
                if "expected_verification" in case:
                    self.assertEqual(
                        outcome.verification, case["expected_verification"],
                        f"{case['id']}: {outcome.log_line()}",
                    )

    def test_a_tool_success_with_failed_verification_is_never_success(self):
        """Rule 5, stated as its own test rather than left to the table."""
        failing = [
            case for case in self.cases
            if case["kind"] == "verification_failed"
        ]

        self.assertGreaterEqual(len(failing), 3)
        for case in failing:
            with self.subTest(case=case["id"]):
                outcome = _run(case).outcome()
                self.assertNotEqual(outcome.outcome, task_outcome.SUCCESS)
                self.assertTrue(outcome.is_verification_failure)

    def test_no_scenario_runs_more_steps_than_it_should(self):
        """Bounded retries: nothing loops, and nothing exceeds max_steps."""
        for case in self.cases:
            with self.subTest(case=case["id"]):
                result = _run(case)
                self.assertLessEqual(
                    result.task_state.step_count,
                    case.get("max_steps", 8),
                )
                if "max_tool_calls" in case:
                    self.assertLessEqual(
                        len(result.task_state.completed_steps),
                        case["max_tool_calls"],
                    )

    def test_cancellation_stops_before_the_remaining_steps(self):
        for case in self.cases:
            if case["kind"] != "cancelled":
                continue
            with self.subTest(case=case["id"]):
                result = _run(case)

                self.assertEqual(
                    result.outcome().outcome, task_outcome.CANCELLED,
                )
                # The scripted plan offers more steps than were consumed;
                # nothing queued after the cancellation may have run.
                self.assertLess(
                    len(result.task_state.completed_steps),
                    len(case["plan"]),
                )

    def test_missing_information_asks_rather_than_guessing(self):
        for case in self.cases:
            if case["kind"] != "needs_user_input":
                continue
            with self.subTest(case=case["id"]):
                outcome = _run(case).outcome()

                self.assertEqual(outcome.outcome, task_outcome.NEEDS_USER_INPUT)

    def test_a_terminal_failure_does_not_spend_the_retry_budget(self):
        for case in self.cases:
            if case["kind"] != "non_recoverable":
                continue
            with self.subTest(case=case["id"]):
                result = _run(case)

                self.assertEqual(
                    result.outcome().outcome, task_outcome.TERMINAL_FAILURE,
                )
                # It stopped on the failing step rather than retrying it.
                self.assertEqual(len(result.task_state.completed_steps), 1)


if __name__ == "__main__":
    unittest.main()
