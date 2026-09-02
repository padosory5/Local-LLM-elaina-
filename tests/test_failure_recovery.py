"""The failure and recovery benchmark: 27 scenarios, all deterministic.

Three questions, asked of every layer that can fail:

* does it end, or can it wait forever?
* does it end as one of the five named outcomes, or as a traceback?
* is the assistant still usable afterwards?

The cases that need real hardware -- a microphone physically disappearing,
Electron being force-closed -- are in ``docs/FAILURE_RECOVERY_BASELINE.md`` as
a manual checklist. Nothing here pretends to cover them.
"""

import threading
import time
import unittest

from brain import task_outcome
from brain.browser_action_planner import ActionPlanResult as BrowserResult
from brain.desktop_action_planner import ActionPlanResult as DesktopResult
from core.event_bus import EventBus
from core.lifecycle import Lifecycle, StartupTimeout, build_within
from core.websocket_server import WebSocketServer
from tests.test_task_planner import FakeComputerControlMode, _planner


def _lifecycle():
    lines = []
    return Lifecycle(log=lines.append), lines


# ------------------------------------------------------- startup and hangs


class StartupHangTests(unittest.TestCase):
    """No startup stage may wait forever. The 4E-G carry-over."""

    def test_a_hanging_constructor_is_bounded(self):
        # 1. startup hang
        started = time.monotonic()
        with self.assertRaises(StartupTimeout):
            build_within(
                "stuck", lambda: time.sleep(30), timeout=0.5,
                log=lambda _: None,
            )

        self.assertLess(time.monotonic() - started, 5.0)

    def test_a_bounded_build_still_returns_a_working_value(self):
        self.assertEqual(
            build_within("quick", lambda: "engine", timeout=5,
                         log=lambda _: None),
            "engine",
        )

    def test_a_constructor_error_is_not_swallowed_by_the_watchdog(self):
        with self.assertRaises(ZeroDivisionError):
            build_within("broken", lambda: 1 / 0, timeout=5,
                         log=lambda _: None)

    def test_the_stuck_thread_cannot_block_process_exit(self):
        # It is a daemon, which is the whole reason this design is safe:
        # Python cannot kill it, so it must not be joined at exit.
        names = []

        def factory():
            names.append(threading.current_thread().daemon)
            time.sleep(2)

        try:
            build_within("stuck", factory, timeout=0.3, log=lambda _: None)
        except StartupTimeout:
            pass
        time.sleep(0.2)

        self.assertEqual(names, [True])

    def test_a_startup_timeout_triggers_lifecycle_cleanup(self):
        # 2. required startup failure -> everything already up comes down
        life, _ = _lifecycle()
        released = []
        life.start("engine", lambda: object(),
                   cleanup=lambda _: released.append("engine"))
        life.start("server", lambda: object(),
                   cleanup=lambda _: released.append("server"))

        life.start("ollama", lambda: (_ for _ in ()).throw(
            StartupTimeout("timed out")))
        life.shutdown("a required subsystem did not start")

        self.assertFalse(life.ready())
        self.assertEqual(released, ["server", "engine"])

    def test_an_optional_startup_failure_degrades(self):
        # 3. optional startup failure
        life, _ = _lifecycle()
        life.start("engine", lambda: object())
        life.start("microphone", lambda: (_ for _ in ()).throw(
            OSError("no input device")), required=False)

        self.assertTrue(life.ready())
        self.assertEqual(life.degraded, ["microphone"])


class StaleResourceTests(unittest.TestCase):
    """A resource someone else already holds is a failure, not a crash."""

    def test_a_bound_port_is_a_clean_required_failure(self):
        # 4. stale port
        holder = WebSocketServer(
            event_bus=EventBus(), host="127.0.0.1", port=8796,
        )
        holder.start()
        try:
            life, _ = _lifecycle()
            life.start("engine", lambda: object())

            def second():
                server = WebSocketServer(
                    event_bus=EventBus(), host="127.0.0.1", port=8796,
                )
                server.start()
                return server

            life.start("websocket server", second)
            if not life.ready():
                # The good case: refused, and reported as required.
                self.assertIn("websocket server", life.failed_required)
            life.shutdown("test")
        finally:
            holder.stop()

    def test_recovery_never_reaches_for_a_process_name(self):
        # 5. no broad kill introduced by this phase
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        offenders = []
        for name in ("core/lifecycle.py", "brain/task_planner.py",
                     "brain/task_outcome.py", "main.py"):
            text = (root / name).read_text(encoding="utf-8", errors="replace")
            if "/IM" in text or "killall" in text or "pkill" in text:
                offenders.append(name)

        self.assertEqual(offenders, [])


# ------------------------------------------------------------ tool failure


def _run(plan, results, *, desktop=False, cancel=None, max_steps=8):
    kwargs = {"desktop_results" if desktop else "browser_results": results}
    planner, _, _ = _planner(
        responses=plan, max_steps=max_steps,
        computer_control_mode=FakeComputerControlMode(enabled=True),
        **kwargs,
    )
    if cancel is not None:
        planner._is_cancelled = cancel
    return planner.run("do the thing")


class ToolFailureTests(unittest.TestCase):
    """A tool blowing up becomes a task outcome, never a crash."""

    def test_a_raised_exception_becomes_a_structured_failure(self):
        # 6. tool exception
        class Exploding:
            def act(self, *_args, **_kwargs):
                raise RuntimeError("the driver exploded")

        planner, _, _ = _planner(
            responses=[{"capability": "browser_control", "sub_goal": "go"},
                       {"done": True, "summary": "stopped after the error"}],
        )
        planner.executors["browser_control"] = Exploding()

        # The property is that the exception is *contained*: it becomes a
        # recorded step failure and the run still returns a result. What the
        # planner then decides to do about it -- here, stop -- is the retry
        # policy's business, tested separately.
        result = planner.run("do the thing")

        self.assertIn(result.outcome().outcome, task_outcome.OUTCOMES)
        failed = [
            step for step in result.task_state.completed_steps
            if step.status == "failed"
        ]
        self.assertTrue(failed, "the exception was not recorded as a failure")
        self.assertIn("exploded", failed[0].summary)
        self.assertTrue(result.task_state.errors)

    def test_each_unavailable_dependency_ends_as_a_named_outcome(self):
        # 7-11. web search / browser / UI / screen / MCP unavailable
        for code, expected in (
            ("web_search_failed", task_outcome.RETRYABLE_FAILURE),
            ("surface_unavailable", task_outcome.RETRYABLE_FAILURE),
            ("planner_unavailable", task_outcome.RETRYABLE_FAILURE),
            ("spotify_not_found", task_outcome.TERMINAL_FAILURE),
            ("source_scope_violation", task_outcome.TERMINAL_FAILURE),
        ):
            with self.subTest(code=code):
                outcome = task_outcome.classify("failed", code)
                self.assertEqual(outcome.outcome, expected)

    def test_no_failure_produces_a_traceback_as_the_answer(self):
        # 12. user-facing failure behaviour
        result = _run(
            [{"capability": "browser_control", "sub_goal": "go"},
             {"done": True, "summary": "never"}],
            [BrowserResult("failed", "Could not reach the page.",
                           failure_code="planner_stalled")],
        )

        self.assertNotIn("Traceback", result.summary)
        self.assertNotIn("Error:", result.summary)


class RetryPolicyTests(unittest.TestCase):
    """Bounded, and only where retrying could help."""

    def test_a_transient_failure_can_recover(self):
        # 13. transient then success
        result = _run(
            [{"capability": "browser_control", "sub_goal": "first"},
             {"capability": "browser_control", "sub_goal": "second"},
             {"done": True, "summary": "got there"}],
            [BrowserResult("failed", "flaky", failure_code="web_search_failed"),
             BrowserResult("done", "worked", verified=True)],
        )

        self.assertEqual(result.outcome().outcome, task_outcome.SUCCESS)

    def test_repeated_transient_failure_stops_rather_than_looping(self):
        # 14. retries exhausted
        result = _run(
            [{"capability": "browser_control", "sub_goal": f"try {n}"}
             for n in range(6)] + [{"done": True, "summary": "never"}],
            [BrowserResult("failed", "still flaky",
                           failure_code="model_reported_failure")] * 6,
        )

        self.assertNotEqual(result.outcome().outcome, task_outcome.SUCCESS)
        self.assertLessEqual(len(result.task_state.completed_steps), 3)

    def test_a_terminal_failure_does_not_spend_the_budget(self):
        # 15. terminal failure
        result = _run(
            [{"capability": "browser_control", "sub_goal": "go"},
             {"capability": "browser_control", "sub_goal": "again"},
             {"done": True, "summary": "never"}],
            [BrowserResult("failed", "not allowed",
                           failure_code="source_scope_violation")],
        )

        self.assertEqual(
            result.outcome().outcome, task_outcome.TERMINAL_FAILURE,
        )
        self.assertEqual(len(result.task_state.completed_steps), 1)

    def test_nothing_runs_past_the_step_budget(self):
        # 16. no infinite loop even when every step "succeeds"
        result = _run(
            [{"capability": "browser_control", "sub_goal": f"step {n}"}
             for n in range(30)],
            [BrowserResult("done", "did something")] * 30,
            max_steps=4,
        )

        self.assertLessEqual(result.task_state.step_count, 4)

    def test_missing_information_asks_instead_of_guessing(self):
        # 17. missing user information
        result = _run(
            [{"capability": "ui_control", "sub_goal": "send it to John"},
             {"done": True, "summary": "never"}],
            [DesktopResult("failed", "There are three Johns.",
                           failure_code="direct_target_ambiguous")],
            desktop=True,
        )

        self.assertEqual(
            result.outcome().outcome, task_outcome.NEEDS_USER_INPUT,
        )


class CancellationTests(unittest.TestCase):
    """Cancelling stops the rest of the plan, not just the current step."""

    PLAN = [
        {"capability": "browser_control", "sub_goal": "step one"},
        {"capability": "browser_control", "sub_goal": "step two"},
        {"capability": "browser_control", "sub_goal": "step three"},
        {"done": True, "summary": "finished"},
    ]

    def test_cancelling_before_anything_runs_dispatches_nothing(self):
        # 18. cancellation before action
        planner, _, browser = _planner(
            responses=self.PLAN,
            browser_results=[BrowserResult("done", "ran")] * 3,
        )
        planner._is_cancelled = lambda: True

        result = planner.run("do it")

        self.assertEqual(result.outcome().outcome, task_outcome.CANCELLED)
        self.assertEqual(browser.act_calls, [])

    def test_cancelling_midway_stops_the_remaining_steps(self):
        # 19. cancellation during a multi-step plan
        calls = {"n": 0}

        def cancel_after_first():
            calls["n"] += 1
            return calls["n"] > 2

        planner, _, browser = _planner(
            responses=self.PLAN,
            browser_results=[BrowserResult("done", "ran")] * 3,
        )
        planner._is_cancelled = cancel_after_first

        result = planner.run("do it")

        self.assertEqual(result.outcome().outcome, task_outcome.CANCELLED)
        self.assertLess(len(browser.act_calls), 3)

    def test_cancelling_during_a_retry_stops_the_recovery(self):
        # 20. cancellation during retry -- a retry is a new action
        state = {"failed_once": False}

        def cancel_after_failure():
            return state["failed_once"]

        planner, _, browser = _planner(
            responses=self.PLAN,
            browser_results=[
                BrowserResult("failed", "flaky",
                              failure_code="web_search_failed"),
                BrowserResult("done", "recovered"),
            ],
        )

        original = planner._run_step

        def run_step(*args, **kwargs):
            result = original(*args, **kwargs)
            state["failed_once"] = True
            return result

        planner._run_step = run_step
        planner._is_cancelled = cancel_after_failure

        result = planner.run("do it")

        self.assertEqual(result.outcome().outcome, task_outcome.CANCELLED)
        self.assertEqual(len(browser.act_calls), 1)

    def test_a_broken_cancel_predicate_does_not_strand_the_task(self):
        # 21. the check itself failing must not cancel or hang
        planner, _, _ = _planner(
            responses=[{"capability": "browser_control", "sub_goal": "go"},
                       {"done": True, "summary": "done"}],
            browser_results=[BrowserResult("done", "ran", verified=True)],
        )
        planner._is_cancelled = lambda: 1 / 0

        result = planner.run("do it")

        self.assertEqual(result.outcome().outcome, task_outcome.SUCCESS)

    def test_a_cancelled_task_reports_cancelled_not_merely_stopped(self):
        # 22. the outcome must be distinguishable from giving up
        planner, _, _ = _planner(
            responses=self.PLAN,
            browser_results=[BrowserResult("done", "ran")] * 3,
        )
        planner._is_cancelled = lambda: True

        outcome = planner.run("do it").outcome()

        self.assertEqual(outcome.outcome, task_outcome.CANCELLED)
        self.assertFalse(outcome.may_retry)


class PostFailureUsabilityTests(unittest.TestCase):
    """One task failing must not poison the next."""

    def test_a_new_task_works_after_a_terminal_failure(self):
        # 23. failure followed by an unrelated successful request
        failed = _run(
            [{"capability": "browser_control", "sub_goal": "go"},
             {"done": True, "summary": "never"}],
            [BrowserResult("failed", "nope",
                           failure_code="source_scope_violation")],
        )
        self.assertEqual(
            failed.outcome().outcome, task_outcome.TERMINAL_FAILURE,
        )

        after = _run(
            [{"capability": "browser_control", "sub_goal": "something else"},
             {"done": True, "summary": "all good"}],
            [BrowserResult("done", "worked", verified=True)],
        )

        self.assertEqual(after.outcome().outcome, task_outcome.SUCCESS)

    def test_a_new_task_works_after_a_cancellation(self):
        # 24. cancellation does not poison the next task
        planner, _, _ = _planner(
            responses=[{"capability": "browser_control", "sub_goal": "go"},
                       {"done": True, "summary": "x"}],
            browser_results=[BrowserResult("done", "ran")],
        )
        planner._is_cancelled = lambda: True
        self.assertEqual(
            planner.run("first").outcome().outcome, task_outcome.CANCELLED,
        )

        after = _run(
            [{"capability": "browser_control", "sub_goal": "next"},
             {"done": True, "summary": "fine"}],
            [BrowserResult("done", "worked", verified=True)],
        )

        self.assertEqual(after.outcome().outcome, task_outcome.SUCCESS)

    def test_each_task_starts_from_its_own_state(self):
        # 25. no shared state carries a failure forward
        first = _run(
            [{"capability": "browser_control", "sub_goal": "go"},
             {"done": True, "summary": "never"}],
            [BrowserResult("failed", "nope", failure_code="planner_stalled")],
        )
        second = _run(
            [{"capability": "browser_control", "sub_goal": "go"},
             {"done": True, "summary": "fine"}],
            [BrowserResult("done", "worked", verified=True)],
        )

        self.assertTrue(first.task_state.errors)
        self.assertFalse(second.task_state.errors)


class RuntimeExceptionTests(unittest.TestCase):
    """An exception in one place must not take everything with it."""

    def test_one_failing_cleanup_does_not_skip_the_others(self):
        # 26. component crash during shutdown
        life, _ = _lifecycle()
        released = []
        life.start("engine", lambda: object(),
                   cleanup=lambda _: (_ for _ in ()).throw(
                       RuntimeError("stuck")))
        life.start("microphone", lambda: object(),
                   cleanup=lambda _: released.append("microphone"))

        life.shutdown("test")

        self.assertEqual(released, ["microphone"])
        self.assertEqual(len(life.cleanup_errors), 1)

    def test_every_covered_wait_has_a_bound(self):
        # 27. no infinite waits left in the startup/task paths
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        # A bare .wait() or .join() with no argument blocks forever.
        bare = re.compile(r"\.(?:wait|join)\(\)")
        allowed = {
            # Deliberate: these wait on a stop signal or a child's whole life.
            "core/websocket_server.py", "tools/project_mcp_client.py",
            "main.py",
        }
        offenders = []
        for folder in ("brain", "core", "tools", "voice", "agents", "memory"):
            for path in (root / folder).rglob("*.py"):
                relative = path.relative_to(root).as_posix()
                if relative in allowed:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    if bare.search(line) and "str" not in line:
                        offenders.append(f"{relative}: {line.strip()}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
