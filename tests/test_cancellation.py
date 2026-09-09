"""A5: "stop" stops it, wherever the stop arrives, and says what it did.

A cancellation can reach the planner at four different moments, and only
one of them had coverage. ``execution_matrix.json`` has two cancellation
scenarios and both cancel the same way -- the tool comes back with
``user_took_over``, by which point the planner is already looking at a
failed step. The other three are checks inside ``_advance``:

    before planning     a cancellation between two steps must not be
                        discovered after the next one has already run
    before dispatching  planning a step is a model call; the person can
                        cancel while it runs
    before retrying     a retry is a new action, so cancelling during a
                        failure has to stop the recovery too

That last one had no coverage at all: every existing case cancels a run in
which nothing has failed, so the recovery path is never what gets
interrupted.

Rather than script each checkpoint by name -- which would stop covering a
checkpoint added later -- the predicate says no *n* times and yes after,
which walks it through them in a real run. Two plans are used, one that
goes cleanly and one that fails a step and recovers.

The scenarios live here and ``scripts/cancellation_check.py`` renders them,
the same way ``execution_report.py`` renders the execution matrix.
"""

import unittest

from brain import task_outcome, task_progress
from brain.desktop_action_planner import (
    ActionPlanResult as DesktopResult,
    DesktopSurfaceContext,
)
from tests.test_task_planner import FakeComputerControlMode, _planner

GOAL = "Open Spotify, open Liked Songs, and shuffle."

CLEAN_PLAN = [
    {"capability": "ui_control", "sub_goal": "Open Spotify."},
    {"capability": "ui_control", "sub_goal": "Open Liked Songs."},
    {"capability": "ui_control", "sub_goal": "Shuffle."},
    {"done": True, "summary": "Done."},
]

# A plan whose second step fails in a way the planner may retry, so a
# cancellation can arrive while it is recovering rather than progressing.
RECOVERY_PLAN = [
    {"capability": "ui_control", "sub_goal": "Open Spotify."},
    {"capability": "ui_control", "sub_goal": "Open Liked Songs."},
    {"capability": "ui_control", "sub_goal": "Open Liked Songs another way."},
    {"capability": "ui_control", "sub_goal": "Shuffle."},
    {"done": True, "summary": "Done."},
]


def _done(summary: str) -> DesktopResult:
    return DesktopResult(
        "done",
        summary=summary,
        surface_context=DesktopSurfaceContext(app_name="Spotify", is_active=True),
    )


def _failed(summary: str, code: str) -> DesktopResult:
    return DesktopResult(
        "failed",
        summary=summary,
        failure_code=code,
        surface_context=DesktopSurfaceContext(app_name="Spotify", is_active=True),
    )


CLEAN_STEPS = [
    _done("Spotify is open."),
    _done("Liked Songs is open."),
    _done("Shuffle is on."),
]

RECOVERY_STEPS = [
    _done("Spotify is open."),
    _failed("That view would not open.", "model_reported_failure"),
    _done("Liked Songs is open."),
    _done("Shuffle is on."),
]

# name, plan, steps, cancel_on, tool_cancels_at
SCENARIOS = (
    [(f"clean plan, stop on ask #{n}", CLEAN_PLAN, CLEAN_STEPS, n, None)
     for n in range(1, 7)]
    + [(f"recovering plan, stop on ask #{n}", RECOVERY_PLAN, RECOVERY_STEPS,
        n, None) for n in (4, 6)]
    + [
        ("tool took over on step 1", CLEAN_PLAN, CLEAN_STEPS, None, 0),
        ("tool took over on step 3", CLEAN_PLAN, CLEAN_STEPS, None, 2),
    ]
)


def _run(plan, steps, *, cancel_on=None, tool_cancels_at=None):
    results = list(steps)
    if tool_cancels_at is not None:
        results = results[:tool_cancels_at] + [
            _failed("You took the mouse back.", "user_took_over"),
        ]

    seen = {"asked": 0, "dispatched_at_cancel": None}
    planner, desktop, _ = _planner(
        responses=list(plan),
        desktop_results=results,
        computer_control_mode=FakeComputerControlMode(enabled=True),
    )

    if cancel_on:
        def predicate() -> bool:
            seen["asked"] += 1
            fired = seen["asked"] >= cancel_on
            if fired and seen["dispatched_at_cancel"] is None:
                # How much had actually been dispatched when the person
                # said stop. Anything after this is the thing under test.
                seen["dispatched_at_cancel"] = len(desktop.act_calls)
            return fired

        planner._is_cancelled = predicate

    return planner.run(GOAL), desktop, seen


def examine(name, plan, steps, cancel_on=None, tool_cancels_at=None,
            language="en"):
    """One scenario, scored on the three questions A5 asks of a stop."""
    result, desktop, seen = _run(
        plan, steps, cancel_on=cancel_on, tool_cancels_at=tool_cancels_at,
    )
    outcome = result.outcome()
    progress = task_progress.read(result.task_state)
    heard = task_progress.report(
        outcome, result.task_state,
        language=language, model_summary=result.summary,
    )

    at_cancel = seen["dispatched_at_cancel"]
    if progress.reached:
        honest = any(
            fragment.strip(" .").casefold() in heard.casefold()
            for fragment in progress.reached
        )
    else:
        honest = bool(heard.strip())

    return {
        "name": name,
        "outcome": outcome.outcome,
        "dispatched": len(desktop.act_calls),
        "at_cancel": at_cancel,
        "did": list(progress.reached),
        "heard": heard,
        "stopped": outcome.outcome == task_outcome.CANCELLED,
        # Nothing may be dispatched after the cancellation was seen. Measured
        # against the executor's own call count rather than by looking for a
        # step's words in the sentence -- the first version of this check did
        # the latter, which conflates "the step ran" with "the report
        # mentioned it" and cost a false failure.
        "bounded": at_cancel is None or len(desktop.act_calls) == at_cancel,
        "honest": honest,
        # A cancellation that arrives after the plan has finished is not a
        # cancellation: there is nothing left to stop and success is the
        # honest report.
        "skipped": (
            cancel_on is not None
            and outcome.outcome != task_outcome.CANCELLED
            and outcome.succeeded
        ),
    }


class StopStopsItWhereverItArrivesTests(unittest.TestCase):

    def test_every_cancellation_stops_the_plan(self):
        for name, plan, steps, cancel_on, tool_at in SCENARIOS:
            row = examine(name, plan, steps, cancel_on, tool_at)
            if row["skipped"]:
                continue
            with self.subTest(scenario=name):
                self.assertTrue(row["stopped"], f"{name}: {row['outcome']}")

    def test_nothing_is_dispatched_after_the_stop_is_seen(self):
        for name, plan, steps, cancel_on, tool_at in SCENARIOS:
            row = examine(name, plan, steps, cancel_on, tool_at)
            if row["skipped"]:
                continue
            with self.subTest(scenario=name):
                self.assertTrue(
                    row["bounded"],
                    f"{name}: {row['dispatched']} dispatched, "
                    f"{row['at_cancel']} at the stop",
                )

    def test_every_cancellation_says_what_it_had_already_done(self):
        """The half A5 added. "I stopped" alone leaves them checking."""
        for name, plan, steps, cancel_on, tool_at in SCENARIOS:
            row = examine(name, plan, steps, cancel_on, tool_at)
            if row["skipped"]:
                continue
            with self.subTest(scenario=name):
                self.assertTrue(row["honest"], f"{name}: {row['heard']!r}")

    def test_a_stop_during_a_recovery_stops_the_recovery(self):
        """The checkpoint nothing covered before this file.

        Every pre-existing cancellation case interrupts a run in which
        nothing has failed, so the retry path was never the thing being
        cancelled.
        """
        row = examine(
            "recovery", RECOVERY_PLAN, RECOVERY_STEPS, cancel_on=6,
        )
        self.assertTrue(row["stopped"])
        self.assertTrue(row["bounded"])
        self.assertIn("Spotify is open", row["heard"])

    def test_a_stop_before_anything_ran_says_nothing_changed(self):
        row = examine("first", CLEAN_PLAN, CLEAN_STEPS, cancel_on=1)
        self.assertEqual(row["dispatched"], 0)
        self.assertIn("Nothing changed", row["heard"])

    def test_the_korean_report_uses_korean_frames(self):
        row = examine(
            "korean", CLEAN_PLAN, CLEAN_STEPS, cancel_on=5, language="ko",
        )
        self.assertIn("직접 조작하셔서 멈췄습니다", row["heard"])
        self.assertIn("여기까지는 되어 있습니다", row["heard"])


if __name__ == "__main__":
    unittest.main()
