"""A stuck browser must become an error, never an endless turn.

BrowserService hands every Playwright operation to one owner thread and
waits for the answer. That wait used to be unbounded, so a browser that
accepted a connection and then stopped answering blocked the user's turn
permanently -- observed live as a task-planner browser step that logged its
sub-goal and then produced nothing at all for over five minutes.

The bound does not change what succeeds. It only puts a floor under
failure, so the planner can report honestly instead of going quiet.
"""

import io
import re
import threading
import time
import unittest

from tools.browser_control import browser_service
from tools.browser_control.browser_service import (
    BrowserService,
    BrowserServiceTimeoutError,
)


class _StuckObserver:
    def __init__(self, release: threading.Event):
        self._release = release

    def describe_page(self, tab_index=None, **kwargs):
        # Stands in for a Playwright call against an unresponsive browser.
        self._release.wait(timeout=30)
        return "eventually"

    def close(self):
        pass


class CallTimeoutTests(unittest.TestCase):
    def setUp(self):
        self._original = browser_service._CALL_TIMEOUT_SECONDS
        browser_service._CALL_TIMEOUT_SECONDS = 0.3
        self.release = threading.Event()

    def tearDown(self):
        browser_service._CALL_TIMEOUT_SECONDS = self._original
        self.release.set()

    def _service(self):
        service = BrowserService.__new__(BrowserService)
        service._requests = __import__("queue").Queue()
        service._lifecycle_lock = threading.Lock()
        service._closed = False
        service._owner_thread_id = None
        service._thread = object()  # already "started"; no real worker
        service._worker_observer = None
        service._worker_control = None
        return service

    def test_a_worker_that_never_answers_raises_instead_of_hanging(self):
        service = self._service()
        service._start_if_needed = lambda: None

        started = time.perf_counter()
        with self.assertRaises(BrowserServiceTimeoutError):
            service._call(lambda observer, control: "never reached")
        elapsed = time.perf_counter() - started

        # The point is that it returns at all, promptly.
        self.assertLess(elapsed, 5.0)

    def test_the_timeout_message_is_something_a_user_can_hear(self):
        service = self._service()
        service._start_if_needed = lambda: None

        with self.assertRaises(BrowserServiceTimeoutError) as caught:
            service._call(lambda observer, control: None)

        message = str(caught.exception)
        self.assertIn("stopped responding", message)
        self.assertNotIn("Traceback", message)

    def test_a_prompt_answer_still_returns_normally(self):
        service = self._service()
        service._start_if_needed = lambda: None

        def worker():
            request = service._requests.get()
            request.result = "done"
            request.completed.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            self.assertEqual(
                service._call(lambda observer, control: None), "done",
            )
        finally:
            thread.join(timeout=2)

    def test_a_worker_error_is_raised_rather_than_swallowed(self):
        service = self._service()
        service._start_if_needed = lambda: None

        def worker():
            request = service._requests.get()
            request.error = RuntimeError("the page exploded")
            request.completed.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "the page exploded"):
                service._call(lambda observer, control: None)
        finally:
            thread.join(timeout=2)


class TimeoutBudgetTests(unittest.TestCase):
    def test_the_budget_covers_a_cold_launch_but_is_not_unbounded(self):
        # A cold browser launch plus a heavy navigation is the slowest
        # legitimate operation; anything beyond that is a stuck browser.
        self.assertGreaterEqual(browser_service._CALL_TIMEOUT_SECONDS, 30)
        self.assertLessEqual(browser_service._CALL_TIMEOUT_SECONDS, 180)


class FacadeCoverageTests(unittest.TestCase):
    """Every browser call in the app goes through these facades.

    A method missing from a facade is not a type error anywhere -- it is a
    feature that silently never runs, while every direct-observer test
    still passes. That is exactly how automatic cookie-consent handling
    shipped broken: BrowserControl had dismiss_privacy_overlay, the planner
    called it, and the facade in between simply did not expose it.
    """

    def _public(self, obj):
        return {name for name in dir(obj) if not name.startswith("_")}

    def test_the_control_facade_covers_every_call_the_planner_makes(self):
        from tools.browser_control.browser_service import _BrowserControlFacade

        source = io.open(
            "brain/browser_action_planner.py", encoding="utf-8",
        ).read()
        used = set(re.findall(r"self\.control\.(\w+)", source))
        # Resolved through getattr rather than attribute access, so it is
        # named here explicitly instead of being scraped.
        used.add("dismiss_privacy_overlay")

        self.assertEqual(used - self._public(_BrowserControlFacade), set())

    def test_the_observer_facade_covers_every_call_the_planner_makes(self):
        from tools.browser_control.browser_service import _BrowserObserverFacade

        source = io.open(
            "brain/browser_action_planner.py", encoding="utf-8",
        ).read()
        used = set(re.findall(r"self\.observer\.(\w+)", source))

        self.assertEqual(used - self._public(_BrowserObserverFacade), set())

    def test_the_facades_cover_every_call_the_chat_engine_makes(self):
        from tools.browser_control.browser_service import (
            _BrowserControlFacade,
            _BrowserObserverFacade,
        )

        source = io.open("brain/chat_engine.py", encoding="utf-8").read()
        control_used = set(re.findall(r"self\.browser_control\.(\w+)", source))
        observer_used = set(re.findall(r"self\.browser_observer\.(\w+)", source))

        self.assertEqual(
            control_used - self._public(_BrowserControlFacade), set(),
        )
        self.assertEqual(
            observer_used - self._public(_BrowserObserverFacade), set(),
        )


if __name__ == "__main__":
    unittest.main()
