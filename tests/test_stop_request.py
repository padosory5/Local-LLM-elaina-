"""Leaving once, rather than 1,926 times.

From the first dogfooding session. Saying "quit" produced this, repeated
until the process finally died:

    [Lifecycle] Stop signal 2 received.
    [Event Bus Error] lip_sync: Event loop is closed

1,926 of each, paired one to one. A SIGINT handler was installed, and the
way it asked the main loop to leave was ``_thread.interrupt_main()``,
which delivers SIGINT rather than raising ``KeyboardInterrupt`` when a
handler exists. The handler asked again, and every pass pushed another
event into an event loop shutdown had already closed.

The pairing is the tell: the second line is not a separate bug, it is the
first line's cost per iteration.
"""

import threading
import unittest

from core.lifecycle import StopRequest


class Recorder:
    def __init__(self):
        self.cleanups = 0
        self.interrupts = 0
        self.logs: list[str] = []

    def cleanup(self):
        self.cleanups += 1

    def interrupt(self):
        self.interrupts += 1


class AskedOnceTests(unittest.TestCase):

    def test_the_first_request_cleans_up_and_interrupts(self):
        recorder = Recorder()
        stop = StopRequest(recorder.cleanup, interrupt=recorder.interrupt)

        self.assertTrue(stop.notify())

        self.assertEqual(recorder.cleanups, 1)
        self.assertEqual(recorder.interrupts, 1)
        self.assertTrue(stop.requested)

    def test_asking_again_does_nothing_at_all(self):
        recorder = Recorder()
        stop = StopRequest(recorder.cleanup, interrupt=recorder.interrupt)

        stop.notify()
        for _ in range(50):
            self.assertFalse(stop.notify())

        self.assertEqual(recorder.cleanups, 1)
        self.assertEqual(recorder.interrupts, 1)

    def test_a_signal_never_asks_for_another_signal(self):
        # The loop itself. An interrupt delivered from inside a signal
        # handler re-enters the handler; a handler that is running is
        # already proof the main thread is reachable.
        recorder = Recorder()
        stop = StopRequest(recorder.cleanup, interrupt=recorder.interrupt)

        stop.notify(from_signal=True)

        self.assertEqual(recorder.cleanups, 1)
        self.assertEqual(recorder.interrupts, 0)

    def test_the_reentrant_case_terminates(self):
        # What actually happened, wired as it was: the interrupt calls the
        # handler, which asks to stop again. It must not recurse.
        recorder = Recorder()
        stop: StopRequest

        def interrupt():
            recorder.interrupt()
            stop.notify(from_signal=True)

        stop = StopRequest(recorder.cleanup, interrupt=interrupt)
        stop.notify()

        self.assertEqual(recorder.cleanups, 1)
        self.assertEqual(recorder.interrupts, 1)

    def test_only_one_of_many_threads_wins(self):
        recorder = Recorder()
        stop = StopRequest(recorder.cleanup, interrupt=recorder.interrupt)
        won: list[bool] = []
        barrier = threading.Barrier(8)

        def ask():
            barrier.wait()
            won.append(stop.notify())

        threads = [threading.Thread(target=ask) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(won.count(True), 1)
        self.assertEqual(recorder.cleanups, 1)

    def test_a_raising_cleanup_still_leaves(self):
        # Shutdown is the one path that may not depend on everything
        # working: a handler that throws must not keep the process up.
        recorder = Recorder()

        def cleanup():
            recorder.cleanup()
            raise RuntimeError("the microphone is gone")

        stop = StopRequest(
            cleanup, interrupt=recorder.interrupt, log=recorder.logs.append,
        )

        self.assertTrue(stop.notify())

        self.assertEqual(recorder.interrupts, 1)
        self.assertTrue(any("microphone" in line for line in recorder.logs))


class EventsAfterTheLoopClosesTests(unittest.TestCase):
    """The second line of the pair, and it is a bug on its own.

    Cancelling the turn in flight emits ``lip_sync``. The WebSocket server
    is subscribed to the bus for the life of the process, so during
    shutdown it kept scheduling coroutines onto a loop that was closing.
    """

    def test_a_stopped_server_broadcasts_nothing(self):
        import asyncio
        import contextlib
        import io

        from core.event_bus import EventBus
        from core.websocket_server import WebSocketServer

        bus = EventBus()
        server = WebSocketServer(bus, host="127.0.0.1", port=8799)

        # A real closed loop, which is what shutdown leaves behind.
        loop = asyncio.new_event_loop()
        loop.close()
        server._loop = loop

        # Before the fix this is the live failure: the bus catches the
        # RuntimeError and prints it, once per event, forever.
        noisy = io.StringIO()
        with contextlib.redirect_stdout(noisy):
            bus.emit("lip_sync", value=0.5)
        self.assertIn("Event loop is closed", noisy.getvalue())

        server.stop()
        self.assertIsNone(server._loop)

        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            for _ in range(20):
                bus.emit("lip_sync", value=0.5)

        self.assertEqual(quiet.getvalue(), "")


class WiredIntoMainTests(unittest.TestCase):
    """main.py is a script, so this reads it rather than importing it."""

    def _main(self) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        return (root / "main.py").read_text(encoding="utf-8")

    def test_the_signal_handler_does_not_ask_for_another_signal(self):
        source = self._main()

        self.assertIn("StopRequest", source)
        self.assertIn("from_signal=True", source)

    def test_interrupt_main_is_reached_only_through_the_stop_request(self):
        # The drift guard. A second interrupt_main() call anywhere in the
        # stop path rebuilds the loop this replaced. Counted as call sites
        # rather than as text: the comment explaining the bug names it too.
        import ast

        tree = ast.parse(self._main())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "interrupt_main"
        ]

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
