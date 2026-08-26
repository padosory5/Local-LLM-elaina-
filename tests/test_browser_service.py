import threading
import unittest

from tools.browser_control.browser_connection import BrowserConnectionError
from tools.browser_control.browser_control import BrowserActionResult
from tools.browser_control.browser_service import (
    BrowserService,
    BrowserServiceClosedError,
)


class _Connection:
    def __init__(self):
        self.last_opened_url = ""
        self.connect_calls = []

    def connect(self, *, allow_isolated_launch=False):
        self.connect_calls.append((threading.get_ident(), allow_isolated_launch))
        return None


class _WorkerObserver:
    instances = []

    def __init__(self, *, connection, ui_observer=None):
        self.connection = connection
        self.ui_observer = ui_observer
        self.thread_id = threading.get_ident()
        self.calls = []
        self.preferred = []
        self.closed_on = None
        type(self).instances.append(self)

    def list_tabs(self):
        self.calls.append(("list_tabs", threading.get_ident()))
        return ("tabs", threading.get_ident())

    def describe_page(self, tab_index=None, *, query=""):
        self.calls.append(("describe_page", threading.get_ident(), tab_index, query))
        return ("page", tab_index, query, threading.get_ident())

    def read_text(self, tab_index=None):
        self.calls.append(("read_text", threading.get_ident(), tab_index))
        return ("text", tab_index, threading.get_ident())

    def prefer_page(self, url):
        self.calls.append(("prefer_page", threading.get_ident(), url))
        self.preferred.append(url)

    def close(self):
        self.closed_on = threading.get_ident()


class _NoLaunchObserver(_WorkerObserver):
    def describe_page(self, tab_index=None, *, query=""):
        self.connection.connect(allow_isolated_launch=False)
        return super().describe_page(tab_index, query=query)


class _WorkerControl:
    instances = []

    def __init__(self, *, observer):
        self.observer = observer
        self.thread_id = threading.get_ident()
        self.calls = []
        type(self).instances.append(self)

    @property
    def available(self):
        self.calls.append(("available", threading.get_ident()))
        return True

    def click(self, *args, **kwargs):
        self.calls.append(("click", threading.get_ident(), args, kwargs))
        return "clicked"

    def fill(self, *args, **kwargs):
        self.calls.append(("fill", threading.get_ident(), args, kwargs))
        return "filled"

    def select_option(self, *args, **kwargs):
        self.calls.append(("select_option", threading.get_ident(), args, kwargs))
        return "selected"

    def scroll_to(self, *args, **kwargs):
        self.calls.append(("scroll_to", threading.get_ident(), args, kwargs))
        return "scrolled"

    def navigate(self, *args, **kwargs):
        self.calls.append(("navigate", threading.get_ident(), args, kwargs))
        url = str(args[1]) if len(args) > 1 else ""
        return BrowserActionResult("navigated", "Opened.", url=url, verified=True)

    def search(self, *args, **kwargs):
        self.calls.append(("search", threading.get_ident(), args, kwargs))
        return "searched"


class _FailingOpenControl(_WorkerControl):
    def navigate(self, *args, **kwargs):
        self.calls.append(("navigate", threading.get_ident(), args, kwargs))
        return BrowserActionResult("failed", "The test page did not load.")


class BrowserServiceTests(unittest.TestCase):
    def setUp(self):
        _WorkerObserver.instances = []
        _NoLaunchObserver.instances = []
        _WorkerControl.instances = []
        _FailingOpenControl.instances = []
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.close()

    def _service(self, **kwargs):
        service = BrowserService(**kwargs)
        self.services.append(service)
        return service

    def test_all_browser_calls_share_one_owner_thread_across_response_threads(self):
        connection = _Connection()
        service = self._service(
            connection=connection,
            observer_factory=_WorkerObserver,
            control_factory=_WorkerControl,
        )
        caller_thread = threading.get_ident()

        first = service.observer.describe_page(2, query="hotel")
        results = []

        def next_response_turn():
            results.append(service.control.fill(0, "field", "Seoul"))
            results.append(service.observer.read_text(0))

        turn = threading.Thread(target=next_response_turn)
        turn.start()
        turn.join()

        self.assertEqual(first[:3], ("page", 2, "hotel"))
        self.assertEqual(results[0], "filled")
        self.assertEqual(results[1][:2], ("text", 0))
        self.assertEqual(len(_WorkerObserver.instances), 1)
        self.assertEqual(len(_WorkerControl.instances), 1)
        observer = _WorkerObserver.instances[0]
        control = _WorkerControl.instances[0]
        self.assertNotEqual(observer.thread_id, caller_thread)
        self.assertEqual(observer.thread_id, control.thread_id)
        self.assertEqual({call[1] for call in observer.calls}, {observer.thread_id})
        self.assertEqual({call[1] for call in control.calls}, {observer.thread_id})

    def test_observation_remains_read_only_and_does_not_opt_into_launch(self):
        connection = _Connection()
        service = self._service(
            connection=connection,
            observer_factory=_NoLaunchObserver,
            control_factory=_WorkerControl,
        )

        service.observer.describe_page()

        self.assertEqual(len(connection.connect_calls), 1)
        self.assertFalse(connection.connect_calls[0][1])

    def test_open_url_uses_the_same_actor_and_remembers_verified_final_url(self):
        connection = _Connection()
        service = self._service(
            connection=connection,
            observer_factory=_WorkerObserver,
            control_factory=_WorkerControl,
        )

        self.assertTrue(service.open_url("https://example.com/hotels"))

        observer = _WorkerObserver.instances[0]
        control = _WorkerControl.instances[0]
        navigation = control.calls[0]
        self.assertEqual(navigation[0], "navigate")
        self.assertEqual(navigation[2], (None, "https://example.com/hotels"))
        self.assertEqual(navigation[3]["allow_isolated_launch"], True)
        self.assertEqual(connection.last_opened_url, "https://example.com/hotels")
        self.assertEqual(observer.preferred, ["https://example.com/hotels"])
        self.assertEqual(navigation[1], observer.thread_id)

    def test_open_url_surfaces_a_real_navigation_failure(self):
        service = self._service(
            connection=_Connection(),
            observer_factory=_WorkerObserver,
            control_factory=_FailingOpenControl,
        )

        with self.assertRaisesRegex(BrowserConnectionError, "did not load"):
            service.open_url("https://example.com/")

    def test_close_disconnects_on_owner_thread_and_blocks_later_calls(self):
        service = self._service(
            connection=_Connection(),
            observer_factory=_WorkerObserver,
            control_factory=_WorkerControl,
        )
        service.observer.list_tabs()
        observer = _WorkerObserver.instances[0]

        service.close()

        self.assertEqual(observer.closed_on, observer.thread_id)
        self.assertFalse(service.is_running)
        with self.assertRaises(BrowserServiceClosedError):
            service.observer.list_tabs()


if __name__ == "__main__":
    unittest.main()
