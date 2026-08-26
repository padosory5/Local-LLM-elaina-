"""Single-owner service for Elaina's browser-control session.

Playwright's synchronous API binds a CDP connection to the thread which
created it.  The desktop app intentionally handles each user turn on a fresh
response thread, so handing a :class:`BrowserObserver` directly to those
threads forced it to disconnect and reconnect for every turn.  That created a
race between opening a page, finding the tab again, and acting on its DOM.

``BrowserService`` owns one observer/control pair on one small worker thread.
The public observer and control facades are synchronous, deliberately narrow
adapters: callers keep the same APIs, while every Playwright operation runs in
the actor that owns the connection.  The worker is created lazily, therefore
merely asking to *observe* still calls ``connect(allow_isolated_launch=False)``
and cannot launch an Elaina browser window.

The service is intentionally not a generic background browser automation
framework.  It serializes only the existing, verified Phase 4C operations and
does not weaken their DOM grounding, confirmation, or navigation policies.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable

from tools.browser_control.browser_connection import (
    BrowserConnection,
    BrowserConnectionError,
)
from tools.browser_control.browser_control import BrowserActionResult, BrowserControl
from tools.browser_control.browser_observer import BrowserObserver


class BrowserServiceClosedError(RuntimeError):
    """Raised when code tries to use a ChatEngine-owned service after close."""


class BrowserServiceTimeoutError(TimeoutError):
    """The browser owner thread did not answer a request in time."""


# Generous enough for the slowest legitimate operation (a cold browser
# launch plus a heavy navigation), but never unbounded: the owner thread
# can block on a browser that accepted a connection and then stopped
# answering, and an unbounded wait turns that into a turn that never ends.
_CALL_TIMEOUT_SECONDS = 60.0


@dataclass
class _BrowserCall:
    """One synchronous request submitted to the browser owner thread."""

    operation: Callable[[BrowserObserver, BrowserControl], Any]
    completed: threading.Event
    result: Any = None
    error: BaseException | None = None


class _BrowserObserverFacade:
    """Thread-safe public subset of :class:`BrowserObserver`.

    BrowserControl keeps a reference to the real observer on the service
    thread.  This facade is exclusively for planners and the chat engine,
    which must not be able to obtain or retain raw Playwright page handles.
    """

    def __init__(self, service: "BrowserService") -> None:
        self._service = service

    def list_tabs(self):
        return self._service._observer_call("list_tabs")

    def describe_page(self, tab_index: int | None = None, *, query: str = ""):
        return self._service._observer_call("describe_page", tab_index, query=query)

    def read_text(self, tab_index: int | None = None):
        return self._service._observer_call("read_text", tab_index)

    def prefer_page(self, url: str) -> None:
        self._service._observer_call("prefer_page", url)


class _BrowserControlFacade:
    """Thread-safe public subset of :class:`BrowserControl`."""

    def __init__(self, service: "BrowserService") -> None:
        self._service = service

    @property
    def available(self) -> bool:
        return bool(self._service._control_call("available"))

    def click(self, *args, **kwargs):
        return self._service._control_call("click", *args, **kwargs)

    def fill(self, *args, **kwargs):
        return self._service._control_call("fill", *args, **kwargs)

    def select_option(self, *args, **kwargs):
        return self._service._control_call("select_option", *args, **kwargs)

    def scroll_to(self, *args, **kwargs):
        return self._service._control_call("scroll_to", *args, **kwargs)

    def navigate(self, *args, **kwargs):
        return self._service._control_call("navigate", *args, **kwargs)

    def dismiss_privacy_overlay(self, *args, **kwargs):
        # Without this the planner's automatic consent handling silently
        # never runs in the real application: every browser call reaches
        # BrowserControl through this facade, and a method missing here is
        # simply not reachable.
        return self._service._control_call(
            "dismiss_privacy_overlay", *args, **kwargs,
        )

    def search(self, *args, **kwargs):
        return self._service._control_call("search", *args, **kwargs)


class BrowserService:
    """Keep exactly one browser observer/control pair on one owner thread.

    A service belongs to one ``ChatEngine`` lifetime.  It starts only on the
    first browser request and closes the Playwright driver (not the user's
    controlled browser window) when the engine shuts down.
    """

    _STOP = object()

    def __init__(
        self,
        *,
        connection: BrowserConnection | None = None,
        ui_observer: Any = None,
        observer_factory: Callable[..., BrowserObserver] = BrowserObserver,
        control_factory: Callable[..., BrowserControl] = BrowserControl,
        thread_name: str = "elaina-browser-control",
    ) -> None:
        self.connection = connection or BrowserConnection()
        self._ui_observer = ui_observer
        self._observer_factory = observer_factory
        self._control_factory = control_factory
        self._thread_name = thread_name
        self._requests: queue.Queue[_BrowserCall | object] = queue.Queue()
        self._lifecycle_lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner_thread_id: int | None = None
        self._worker_observer: BrowserObserver | None = None
        self._worker_control: BrowserControl | None = None
        self._startup_error: BaseException | None = None
        self._closed = False
        self.observer = _BrowserObserverFacade(self)
        self.control = _BrowserControlFacade(self)

    @property
    def is_running(self) -> bool:
        """Whether the actor exists and is still alive (for diagnostics)."""
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def open_url(self, url: str) -> bool:
        """Open a validated URL in the actor-owned controlled browser.

        ``SafeBrowserControl`` already validates the public computer action
        before it calls this bridge.  ``BrowserControl.navigate`` repeats its
        own HTTP(S)/private-network validation, so direct users retain the
        lower-layer safety boundary as well.
        """
        requested_url = str(url).strip()

        def operation(observer: BrowserObserver, control: BrowserControl) -> bool:
            result = control.navigate(
                None,
                requested_url,
                allow_isolated_launch=True,
            )
            if not isinstance(result, BrowserActionResult) or not result.succeeded:
                raise BrowserConnectionError(
                    getattr(result, "message", "")
                    or "I couldn't open that page in the controlled browser."
                )
            final_url = str(getattr(result, "url", "") or requested_url)
            # The observed final URL is the identity for the next short
            # follow-up.  It is set on the same actor before control returns
            # to the next response thread.
            self.connection.last_opened_url = final_url
            observer.prefer_page(final_url)
            return True

        return bool(self._call(operation))

    def close(self) -> None:
        """Stop the actor and disconnect its driver without closing tabs."""
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            if thread is None:
                return
            self._requests.put(self._STOP)

        # ``close`` normally runs on the main/UI thread.  Do not wait from
        # the owner itself: that would deadlock if future shutdown handling
        # is triggered while completing an actor call.
        if threading.get_ident() != self._owner_thread_id:
            thread.join(timeout=5.0)

    def _observer_call(self, method: str, *args, **kwargs):
        return self._call(
            lambda observer, _control: getattr(observer, method)(*args, **kwargs),
        )

    def _control_call(self, method: str, *args, **kwargs):
        return self._call(
            lambda _observer, control: getattr(control, method)(*args, **kwargs),
        )

    def _call(self, operation: Callable[[BrowserObserver, BrowserControl], Any]):
        self._start_if_needed()
        if threading.get_ident() == self._owner_thread_id:
            observer = self._worker_observer
            control = self._worker_control
            if observer is None or control is None:
                raise BrowserServiceClosedError("Browser service is not available.")
            return operation(observer, control)

        request = _BrowserCall(operation=operation, completed=threading.Event())
        self._requests.put(request)
        # Never an unbounded wait -- observed live as a browser step that
        # logged its sub-goal and then produced nothing at all for five
        # minutes, which is exactly the "loads forever" symptom.
        if not request.completed.wait(timeout=_CALL_TIMEOUT_SECONDS):
            raise BrowserServiceTimeoutError(
                "The browser stopped responding, so I stopped waiting on it."
            )
        if request.error is not None:
            raise request.error
        return request.result

    def _start_if_needed(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise BrowserServiceClosedError(
                    "Browser control is closed because Elaina is shutting down.",
                )
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name=self._thread_name,
                    daemon=True,
                )
                self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise self._startup_error

    def _run(self) -> None:
        self._owner_thread_id = threading.get_ident()
        try:
            observer = self._observer_factory(
                connection=self.connection,
                ui_observer=self._ui_observer,
            )
            control = self._control_factory(observer=observer)
            self._worker_observer = observer
            self._worker_control = control
        except BaseException as error:
            self._startup_error = error
            self._ready.set()
            return
        self._ready.set()

        while True:
            request = self._requests.get()
            if request is self._STOP:
                break
            assert isinstance(request, _BrowserCall)
            try:
                request.result = request.operation(observer, control)
            except BaseException as error:
                request.error = error
            finally:
                request.completed.set()

        try:
            observer.close()
        except Exception:
            # Teardown must never make the desktop process hang. The browser
            # itself remains open by BrowserObserver's existing contract.
            pass
        finally:
            self._worker_observer = None
            self._worker_control = None

