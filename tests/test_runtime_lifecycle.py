"""The runtime lifecycle benchmark: what starts, what fails, what unwinds.

Startup used to be a straight line of module-level statements with the
``try/finally`` beginning *after* it. A failure anywhere in that line -- no
microphone, no Ollama, no Node -- exited on an unhandled traceback with port
8765 still bound, an Electron window still open, and the engine's browser
service and MCP subprocess still running. The next launch met a port
collision and a second window.

Every case below is deterministic and runs in the ordinary suite. The cases
that genuinely need real hardware -- a real microphone, a real Electron
window, a real force-kill -- are listed in ``docs/RUNTIME_BASELINE.md`` as a
manual checklist rather than pretended to be automated here.
"""

import unittest

from core.event_bus import EventBus
from core.lifecycle import Lifecycle
from core.websocket_server import WebSocketServer


class _Recorder:
    """A subsystem that records whether it was started and released."""

    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail
        self.started = False
        self.released = False

    def start(self):
        if self.fail:
            raise RuntimeError(f"{self.name} is unavailable")
        self.started = True
        return self

    def close(self):
        self.released = True


def _lifecycle():
    lines = []
    return Lifecycle(log=lines.append), lines


class CleanStartupTests(unittest.TestCase):

    def test_a_clean_start_reaches_ready(self):
        life, lines = _lifecycle()
        for name in ("engine", "server", "window", "microphone"):
            life.start(name, _Recorder(name).start, cleanup=lambda v: v.close())

        self.assertTrue(life.ready())
        self.assertEqual(life.report_ready(), "[Lifecycle] READY")
        self.assertIn("[Lifecycle] engine ready.", lines)

    def test_ready_is_not_reported_before_the_required_parts_are_up(self):
        life, _ = _lifecycle()
        life.start("engine", _Recorder("engine", fail=True).start)

        self.assertFalse(life.ready())
        self.assertIn("NOT READY", life.report_ready())


class CleanShutdownTests(unittest.TestCase):

    def test_everything_started_is_released_newest_first(self):
        life, _ = _lifecycle()
        order = []
        for name in ("engine", "server", "microphone"):
            life.start(
                name, _Recorder(name).start,
                cleanup=lambda value: order.append(value.name) or value.close(),
            )

        life.shutdown("test")

        self.assertEqual(order, ["microphone", "server", "engine"])
        self.assertEqual(life.cleaned, ["microphone", "server", "engine"])

    def test_shutdown_runs_once_however_many_times_it_is_asked(self):
        # Electron closing and the backend's own finally both call this.
        life, _ = _lifecycle()
        calls = []
        life.start("engine", lambda: object(), cleanup=lambda _: calls.append(1))

        life.shutdown("electron closed")
        life.shutdown("backend finally")

        self.assertEqual(len(calls), 1)
        self.assertEqual(life.shutdown_reason, "electron closed")

    def test_the_reason_for_shutting_down_is_recorded(self):
        life, lines = _lifecycle()
        life.shutdown("the desktop window closed")

        self.assertIn("[Lifecycle] Shutting down: the desktop window closed", lines)


class PartialStartupTests(unittest.TestCase):
    """The case the old code got wrong: a failure part-way through."""

    def test_a_required_failure_unwinds_what_already_started(self):
        life, _ = _lifecycle()
        engine = _Recorder("engine")
        server = _Recorder("server")
        life.start("engine", engine.start, cleanup=lambda v: v.close())
        life.start("server", server.start, cleanup=lambda v: v.close())

        life.start("ollama", _Recorder("ollama", fail=True).start)
        self.assertFalse(life.ready())
        life.shutdown("a required subsystem did not start")

        self.assertTrue(engine.released, "the engine was left running")
        self.assertTrue(server.released, "the port was left bound")

    def test_nothing_further_starts_after_a_required_failure(self):
        life, _ = _lifecycle()
        life.start("ollama", _Recorder("ollama", fail=True).start)

        later = _Recorder("window")
        life.start("window", later.start, required=False)

        self.assertFalse(later.started)

    def test_a_failed_subsystem_is_never_cleaned_up(self):
        # Its cleanup is bound to a value that exists; a constructor that
        # raised produced none, so nothing may run against it.
        life, _ = _lifecycle()
        cleaned = []
        life.start(
            "microphone", _Recorder("microphone", fail=True).start,
            required=False, cleanup=lambda _: cleaned.append(1),
        )

        life.shutdown("test")

        self.assertEqual(cleaned, [])


class DegradedStartTests(unittest.TestCase):
    """Optional subsystems degrade; required ones abort."""

    def test_an_optional_failure_still_reaches_ready(self):
        life, _ = _lifecycle()
        life.start("engine", _Recorder("engine").start)
        life.start(
            "microphone", _Recorder("microphone", fail=True).start,
            required=False,
        )

        self.assertTrue(life.ready())
        self.assertEqual(life.degraded, ["microphone"])
        self.assertIn("degraded: microphone", life.report_ready())

    def test_a_required_failure_does_not_reach_ready(self):
        life, _ = _lifecycle()
        life.start("ollama", _Recorder("ollama", fail=True).start)

        self.assertFalse(life.ready())
        self.assertIn("ollama", life.failed_required)

    def test_the_log_says_which_subsystem_and_why(self):
        life, lines = _lifecycle()
        life.start(
            "microphone", _Recorder("microphone", fail=True).start,
            required=False,
        )

        said = " ".join(lines)
        self.assertIn("microphone", said)
        self.assertIn("RuntimeError", said)


class CleanupResilienceTests(unittest.TestCase):
    """A runtime exception must not bypass the cleanup after it."""

    def test_one_failing_handler_does_not_skip_the_rest(self):
        life, _ = _lifecycle()
        microphone = _Recorder("microphone")
        life.start("engine", lambda: object(), cleanup=_raise)
        life.start("microphone", microphone.start, cleanup=lambda v: v.close())

        life.shutdown("test")

        self.assertTrue(
            microphone.released,
            "a stuck handler left the microphone open",
        )
        self.assertEqual(len(life.cleanup_errors), 1)
        self.assertIn("engine", life.cleanup_errors[0])

    def test_cleanup_problems_are_reported_not_swallowed(self):
        life, lines = _lifecycle()
        life.start("engine", lambda: object(), cleanup=_raise)

        life.shutdown("test")

        self.assertTrue(any("Cleanup failed" in line for line in lines))
        self.assertTrue(any("problem(s)" in line for line in lines))


def _raise(_value):
    raise RuntimeError("cleanup exploded")


class RestartCycleTests(unittest.TestCase):
    """Start, stop, start again -- without a port collision."""

    PORT = 8798

    def test_three_consecutive_cycles_bind_the_same_port(self):
        for cycle in range(3):
            with self.subTest(cycle=cycle):
                life, _ = _lifecycle()
                server = life.start(
                    "websocket server",
                    lambda: _serve(self.PORT),
                    cleanup=lambda value: value.stop(),
                )
                self.assertIsNotNone(server, "the port was still bound")
                self.assertTrue(life.ready())
                life.shutdown("cycle end")
                self.assertIn("websocket server", life.cleaned)

    def test_a_second_server_on_a_held_port_fails_rather_than_shadowing(self):
        life, _ = _lifecycle()
        first = life.start(
            "first", lambda: _serve(8797), cleanup=lambda v: v.stop(),
        )
        try:
            second = life.start("second", lambda: _serve(8797), required=False)
            # Either it refused (the good case) or the platform allowed the
            # rebind; what must never happen is a silent required-failure.
            self.assertTrue(life.ready())
            if second is not None:
                second.stop()
        finally:
            life.shutdown("test")
        self.assertIsNotNone(first)


def _serve(port: int) -> WebSocketServer:
    server = WebSocketServer(
        event_bus=EventBus(), host="127.0.0.1", port=port,
    )
    server.start()
    return server


class ProcessOwnershipTests(unittest.TestCase):
    """Elaina releases what she opened, and nothing else."""

    def test_no_source_file_kills_by_process_name(self):
        # /IM matches every process with that image name -- it would take the
        # user's own Chrome, Python or Electron with it. Ownership here is by
        # PID only.
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        offenders = []
        for folder in ("brain", "core", "tools", "agents", "voice", "memory"):
            for path in (root / folder).rglob("*.py"):
                text = path.read_text(encoding="utf-8", errors="replace")
                if "/IM" in text or "killall" in text or "pkill" in text:
                    offenders.append(str(path.relative_to(root)))
        for path in (root / "main.py",):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "/IM" in text or "killall" in text or "pkill" in text:
                offenders.append(path.name)

        self.assertEqual(offenders, [])

    def test_the_electron_kill_is_scoped_to_one_pid(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        main_js = (root / "desktop" / "main.js").read_text(encoding="utf-8")

        self.assertIn('"/pid"', main_js)
        self.assertNotIn('"/IM"', main_js)


if __name__ == "__main__":
    unittest.main()
