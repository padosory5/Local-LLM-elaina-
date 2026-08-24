"""Exercise the real task-planning model against simulated capabilities.

No real browser or application is opened -- this proves 4D-1 and 4D-2's
actual exit bars (one multi-step task, executed by the general planner, no
application-specific hardcoded workflow; a recoverable failure keeps the
task going, a dead end stops it cleanly) the same way
live_desktop_planner_check.py proves single-step goal completion: the real
Ollama model drives the loop, but the tier-2 "capabilities" it calls into
are simulated stand-ins, not the real DesktopActionPlanner/
BrowserActionPlanner.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import ollama


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.registry import AgentRegistry  # noqa: E402
from brain.browser_action_planner import ActionPlanResult  # noqa: E402
from brain.task_planner import (  # noqa: E402
    TaskPlanner,
    _MAX_CONSECUTIVE_FAILURES,
    _MAX_STEPS_DEFAULT,
)
from config.loader import Config  # noqa: E402
from scripts.console_style import status_label  # noqa: E402

_HOTELS = "Ocean View Resort ($180/night, 4.5 stars), Guam Beach Hotel ($120/night, 4.0 stars), and Paradise Inn ($95/night, 3.5 stars)"


class SimulatedBrowserCapability:
    """Returns increasingly-informative canned research results.

    Content-aware only in the loosest sense (call count), matching this
    project's existing SimulatedObserver/SimulatedState philosophy: the
    real model's own reasoning is what's under test, not a scripted DOM.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def act(self, goal: str) -> ActionPlanResult:
        self.calls.append(goal)
        if len(self.calls) == 1:
            return ActionPlanResult(
                "done",
                f"Opened a Google search for hotels in Guam. Results: {_HOTELS}.",
            )
        return ActionPlanResult(
            "done",
            f"Re-read the same search results page: {_HOTELS}. No new listings appeared.",
        )


class FlakyThenWorksCapability:
    """Fails once with a real failure summary, then succeeds.

    Proves 4D-2's actual exit bar: the real model reads a genuine failure
    and chooses to retry rather than the task ending there -- nothing here
    scripts *that* choice, only the environment's response to it.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def act(self, goal: str) -> ActionPlanResult:
        self.calls.append(goal)
        if len(self.calls) == 1:
            return ActionPlanResult(
                "failed",
                "The travel site's search results failed to load (timed out).",
                failure_code="direct_target_not_found",
            )
        return ActionPlanResult(
            "done",
            f"Opened a different travel site's search for hotels in Guam. Results: {_HOTELS}.",
        )


class AlwaysFailsCapability:
    """Never succeeds -- proves a genuine dead end stops the task cleanly
    (at the consecutive-failure budget) instead of spinning to the step
    limit."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def act(self, goal: str) -> ActionPlanResult:
        self.calls.append(goal)
        return ActionPlanResult(
            "failed",
            "The travel site is completely unreachable.",
            failure_code="direct_target_not_found",
        )


class NeverCalledCapability:
    """Fails the check immediately if the planner ever reaches for a
    capability this task-scenario has no legitimate reason to need."""

    def __init__(self, name: str) -> None:
        self.name = name

    def act(self, goal: str):
        raise AssertionError(
            f"The hotel-shortlist goal should never dispatch a step to "
            f"{self.name!r}: {goal!r}"
        )


class FakeControlMode:
    enabled = True


def _build_planner(browser: Any, model: str, keep_alive: Any, client: Any) -> TaskPlanner:
    return TaskPlanner(
        client=client,
        model=model,
        keep_alive=keep_alive,
        agent_registry=AgentRegistry(),
        desktop_action_planner=NeverCalledCapability("ui_control"),
        browser_action_planner=browser,
        computer_control_mode=FakeControlMode(),
        browser_control_enabled=True,
    )


def _print_run(goal: str, result) -> None:
    print(f"Goal: {goal}")
    print(f"Status: {result.status}  Steps: {result.task_state.step_count}")
    for step_result in result.task_state.completed_steps:
        print(
            f"  [{step_result.step.capability}] {step_result.step.sub_goal!r} "
            f"-> {step_result.status}"
        )
    print(f"Summary: {result.summary}\n")


def main() -> int:
    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    keep_alive = config.get(
        "llm", "ollama", "keep_alive", default=-1, required=False,
    )
    client = ollama.Client(host=config.get("llm", "ollama", "base_url"))

    all_checks: list[tuple[str, bool]] = []

    # Scenario 1 (4D-1): a clean multi-step task with no failures.
    browser = SimulatedBrowserCapability()
    planner = _build_planner(browser, model, keep_alive, client)
    goal = "Find a hotel in Guam and make me a shortlist."
    result = planner.run(goal)
    _print_run(goal, result)
    only_browser_control = all(
        s.step.capability == "browser_control" for s in result.task_state.completed_steps
    )
    mentions_a_hotel = any(
        name in result.summary
        for name in ("Ocean View", "Guam Beach", "Paradise Inn")
    )
    all_checks += [
        ("[4D-1] Reached done (not failed/stopped)", result.status == "done"),
        ("[4D-1] At least one step ran", result.task_state.step_count >= 1),
        ("[4D-1] Every step used browser_control only", only_browser_control),
        ("[4D-1] Final summary references real gathered hotels", mentions_a_hotel),
    ]

    # Scenario 2 (4D-2): one recoverable failure -- the task should still
    # finish, driven by the real model choosing to retry after reading it.
    browser = FlakyThenWorksCapability()
    planner = _build_planner(browser, model, keep_alive, client)
    goal = "Find a hotel in Guam and make me a shortlist."
    result = planner.run(goal)
    _print_run(goal, result)
    all_checks += [
        ("[4D-2] Recoverable failure still reaches done", result.status == "done"),
        ("[4D-2] The model actually retried (2+ calls)", len(browser.calls) >= 2),
        (
            "[4D-2] Final summary references real gathered hotels",
            any(name in result.summary for name in ("Ocean View", "Guam Beach", "Paradise Inn")),
        ),
    ]

    # Scenario 3 (4D-2): a genuine dead end should stop cleanly at the
    # consecutive-failure budget, not spin all the way to the step limit.
    browser = AlwaysFailsCapability()
    planner = _build_planner(browser, model, keep_alive, client)
    goal = "Find a hotel in Guam and make me a shortlist."
    result = planner.run(goal)
    _print_run(goal, result)
    all_checks += [
        ("[4D-2] Dead end stops (not done)", result.status in ("failed", "stopped")),
        (
            "[4D-2] Stops at the failure budget, not the step limit",
            result.task_state.step_count <= _MAX_CONSECUTIVE_FAILURES + 1,
        ),
        (
            "[4D-2] Never spins to the full step budget",
            result.task_state.step_count < _MAX_STEPS_DEFAULT,
        ),
    ]

    failures = 0
    for name, passed in all_checks:
        failures += 0 if passed else 1
        print(f"[{status_label(passed)}] {name}")
    print(f"{len(all_checks) - failures}/{len(all_checks)} live task-planner checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
