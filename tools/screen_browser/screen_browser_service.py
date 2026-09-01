"""Screen-native browser stack behind the Phase 4C interfaces (Phase 4E).

``BrowserActionPlanner`` already knows how to run a grounded observe-then-act
loop: it renders a page digest, makes the model choose a real element id, and
refuses anything it did not scan.  None of that reasoning is CDP-specific, so
this module presents the screen-native driver through the same
``BrowserObserver``/``BrowserControl`` shapes the planner already calls
instead of forking the planner.  Selecting a driver then becomes one config
value, and both drivers stay covered by the same tests and the same
acceptance criteria.

The adapters translate rather than pretend.

Link targets survive the translation, which was not obvious: Chromium
exposes a hyperlink's ``href`` through UIA ValuePattern, so download
detection, ad classification and the ordinal "open the first result" logic
all keep working on this driver. What genuinely does not survive is
**background tabs** -- UI Automation exposes only the *visible* page of a
window, so a "tab" here is a browser window. Listing a background tab as
though its content were readable would be a lie the planner would then act
on, so they are simply not listed.
"""

from __future__ import annotations

from tools.browser_control.browser_control import (
    BrowserActionResult,
    is_ad_link,
    is_safe_privacy_rejection,
)
from tools.browser_control.browser_observer import (
    PageElement,
    PageImage,
    PageObservation,
    PageTextResult,
    TabInfo,
)
from tools.browser_control.safe_browser import SafeBrowserControl
from tools.screen_browser.browser_window import BrowserWindowFinder
from tools.screen_control.cursor_driver import CursorDriver
from tools.screen_browser.page_observer import (
    ScreenPageObservation,
    ScreenPageObserver,
)
from tools.screen_browser.screen_browser_control import ScreenBrowserControl

# Roles the planner should treat as text entry when it renders the page.
_TEXT_ENTRY_ROLES = frozenset({"textbox", "searchbox", "combobox"})


def _element_index(element_id: str) -> int | None:
    """Parse the index out of a "<scan>-e<n>" id, or a bare number."""
    text = str(element_id or "").strip()
    if not text:
        return None
    tail = text.rsplit("-e", 1)[-1] if "-e" in text else text
    try:
        return int(tail)
    except ValueError:
        return None


class ScreenBrowserObserverAdapter:
    """Presents ScreenPageObserver as the planner's BrowserObserver."""

    def __init__(
        self,
        observer: ScreenPageObserver,
        finder: BrowserWindowFinder | None = None,
    ) -> None:
        self._observer = observer
        self._finder = finder or observer.finder
        # The window each tab index referred to when tabs were last listed,
        # so a later "tab 1" still means the window the model was shown.
        self._tab_handles: tuple[int, ...] = ()
        # Browser Use-style session binding: once Elaina starts operating a
        # browser window, later planner steps keep that stable HWND even if a
        # notification or another app steals foreground focus. It is released
        # automatically only when the window actually closes.
        self._bound_handle: int | None = None
        self._preferred_page_url = ""

    @property
    def connected(self) -> bool:
        return bool(self._finder.list_windows())

    def prefer_page(self, url: str) -> None:
        """Bind follow-ups to the browser window Elaina just opened.

        The CDP observer can remember a page by URL. Screen-native control
        cannot map a URL to a hidden window without inspecting it, but just
        after ``open_url`` or ``open_search`` the launched browser is the
        foreground window. Capturing that HWND supplies the same stable
        handoff without pretending UI Automation can read background tabs.
        """
        self._preferred_page_url = str(url or "").strip()
        active = self._finder.active_window()
        if active is not None:
            self._bound_handle = active.handle

    def list_tabs(self) -> tuple[TabInfo, ...]:
        windows = self._finder.list_windows()
        self._tab_handles = tuple(window.handle for window in windows)
        return tuple(
            TabInfo(
                index=position,
                title=window.page_title,
                url="",
                is_active=window.is_active,
            )
            for position, window in enumerate(windows)
        )

    def _handle_for(self, tab_index: int | None) -> int | None:
        if tab_index is None:
            if (
                self._bound_handle is not None
                and self._finder.window_for_handle(self._bound_handle) is not None
            ):
                return self._bound_handle
            active = self._finder.active_window()
            if active is not None:
                self._bound_handle = active.handle
                return self._bound_handle
            windows = self._finder.list_windows()
            if len(windows) == 1:
                self._bound_handle = windows[0].handle
                return self._bound_handle
            self._bound_handle = None
            return None
        if 0 <= tab_index < len(self._tab_handles):
            self._bound_handle = self._tab_handles[tab_index]
            return self._bound_handle
        windows = self._finder.list_windows()
        if 0 <= tab_index < len(windows):
            self._bound_handle = windows[tab_index].handle
            return self._bound_handle
        return None

    def _index_for_handle(self, handle: int | None) -> int | None:
        """Which tab index the observed window is, refreshing the mapping.

        BrowserObserver always stamps a real index on an observation, and
        BrowserActionPlanner relies on it: an observation without one is
        filed as an ambiguous fallback, so a model that then names "tab 0"
        finds nothing and wastes a whole round re-scanning. Resolving the
        window we actually looked at back to its index keeps the two
        drivers' contracts identical.
        """
        if handle is None:
            return None
        self._bound_handle = handle
        if handle not in self._tab_handles:
            self.list_tabs()
        try:
            return self._tab_handles.index(handle)
        except ValueError:
            return None

    def describe_page(
        self, tab_index: int | None = None, *, query: str = "",
    ) -> PageObservation:
        observation = self._observer.observe(self._handle_for(tab_index))
        # Always report the index of the window actually looked at, never
        # the one that was asked for. A model-supplied index that does not
        # exist falls back to the active window, and labelling that page
        # with the requested number would make the planner believe it had
        # scanned a tab that was never there.
        return to_page_observation(
            observation, self._index_for_handle(observation.handle),
        )

    def read_text(self, tab_index: int | None = None) -> PageTextResult:
        observation = self._observer.observe(self._handle_for(tab_index))
        tab_index = self._index_for_handle(observation.handle)
        if observation.status != "observed":
            return PageTextResult(
                observation.status,
                message=observation.message,
                tab_index=tab_index,
            )
        # The accessible text of the visible page, not the full document
        # source: headings first so a summary starts from real structure.
        body = " ".join(observation.headings + (observation.text_excerpt,)).strip()
        return PageTextResult(
            "observed",
            url=observation.url,
            title=observation.title,
            text=body,
            truncated=observation.text_truncated,
            tab_index=tab_index,
        )


def to_page_observation(
    observation: ScreenPageObservation, tab_index: int | None = None,
) -> PageObservation:
    """Translate a screen-native scan into the planner's page model."""
    if observation.status != "observed":
        return PageObservation(
            observation.status,
            message=observation.message,
            tab_index=tab_index,
        )
    elements = []
    for element in observation.elements:
        privacy_reject = (
            element.in_dialog and is_safe_privacy_rejection(element.label)
        )
        elements.append(
            PageElement(
                # The planner's ordinal-result logic tests for an anchor
                # tag, so a link has to present as one here.
                id=f"{observation.scan_id}-e{element.index}",
                tag="a" if element.role == "link" else element.role,
                role=element.role,
                label=element.label or "(unlabeled)",
                element_type="text" if element.role in _TEXT_ENTRY_ROLES else "",
                disabled=element.disabled,
                href=element.href,
                is_ad=is_ad_link(element.href),
                in_dialog=element.in_dialog,
                in_privacy_dialog=privacy_reject,
                is_privacy_dismissal=privacy_reject,
                in_main=element.in_main,
            )
        )
    return PageObservation(
        "observed",
        url=observation.url,
        title=observation.title,
        elements=tuple(elements),
        truncated=observation.truncated,
        tab_index=tab_index,
        scan_id=observation.scan_id,
        blocking_dialog=observation.blocking_dialog,
        headings=observation.headings,
        text_excerpt=observation.text_excerpt,
        text_truncated=observation.text_truncated,
        images=tuple(PageImage(label) for label in observation.image_labels),
        image_count=observation.image_count,
    )


class ScreenBrowserControlAdapter:
    """Presents ScreenBrowserControl as the planner's BrowserControl."""

    def __init__(
        self,
        control: ScreenBrowserControl,
        observer_adapter: ScreenBrowserObserverAdapter,
    ) -> None:
        self._control = control
        self._observer_adapter = observer_adapter

    @property
    def observer(self):
        return self._observer_adapter

    @property
    def available(self) -> bool:
        return self._control.available

    def _handle(self, tab_index: int | None) -> int | None:
        return self._observer_adapter._handle_for(tab_index)

    @staticmethod
    def _bad_id(element_id: str) -> BrowserActionResult:
        return BrowserActionResult(
            "not_found",
            f"{element_id!r} is not an element id from a page I scanned.",
            element_id=str(element_id),
        )

    def click(
        self,
        tab_index: int | None,
        element_id: str,
        *,
        expected_label: str = "",
        expected_url: str = "",
        expected_scan_id: str = "",
        expected_href: str = "",
        confirmed: bool = False,
    ) -> BrowserActionResult:
        index = _element_index(element_id)
        if index is None:
            return self._bad_id(element_id)
        return self._control.click(
            index,
            expected_label=expected_label,
            expected_scan_id=expected_scan_id,
            window=self._handle(tab_index),
            confirmed=confirmed,
        )

    def fill(
        self,
        tab_index: int | None,
        element_id: str,
        text: str,
        *,
        expected_label: str = "",
        expected_url: str = "",
        expected_scan_id: str = "",
        expected_href: str = "",
        confirmed: bool = False,
    ) -> BrowserActionResult:
        index = _element_index(element_id)
        if index is None:
            return self._bad_id(element_id)
        return self._control.fill(
            index, text,
            expected_label=expected_label,
            expected_scan_id=expected_scan_id,
            window=self._handle(tab_index),
        )

    def submit(self, tab_index: int | None = None) -> BrowserActionResult:
        """Submit the currently focused field and verify the page changed."""
        return self._control.submit(window=self._handle(tab_index))

    def dismiss_privacy_overlay(
        self,
        tab_index: int | None,
        element_id: str,
        **metadata,
    ) -> BrowserActionResult:
        """Click one planner-verified reject/essential-only dialog control."""
        result = self.click(
            tab_index,
            element_id,
            expected_label=str(metadata.get("expected_label", "")),
            expected_scan_id=str(metadata.get("expected_scan_id", "")),
            expected_href=str(metadata.get("expected_href", "")),
        )
        if result.status == "clicked" and result.verified is True:
            return BrowserActionResult(
                "dismissed_privacy_overlay",
                "Rejected optional privacy choices.",
                element_id=result.element_id,
                element_label=result.element_label,
                url=result.url,
                verified=True,
                evidence=result.evidence,
            )
        return result

    def select_option(
        self,
        tab_index: int | None,
        element_id: str,
        option: str,
        **metadata,
    ) -> BrowserActionResult:
        index = _element_index(element_id)
        if index is None:
            return self._bad_id(element_id)
        return self._control.select_option(
            index,
            option,
            expected_label=str(metadata.get("expected_label", "")),
            expected_scan_id=str(metadata.get("expected_scan_id", "")),
            window=self._handle(tab_index),
        )

    def scroll_to(
        self, tab_index: int | None, element_id: str, **_metadata,
    ) -> BrowserActionResult:
        return self._control.scroll("down", window=self._handle(tab_index))

    def navigate(
        self,
        tab_index: int | None,
        url: str,
        *,
        allow_isolated_launch: bool = False,
    ) -> BrowserActionResult:
        return self._control.navigate(url, window=self._handle(tab_index))

    def search(
        self,
        tab_index: int | None,
        query: str,
        *,
        allow_isolated_launch: bool = False,
    ) -> BrowserActionResult:
        return self._control.search(query, window=self._handle(tab_index))


class ScreenBrowserService:
    """Owns the screen-native stack, mirroring BrowserService's surface.

    No worker thread is needed here. BrowserService exists because
    Playwright's synchronous API binds a CDP connection to the thread that
    created it, and each Elaina turn runs on a fresh thread; UI Automation
    has no such affinity, so the whole actor/queue layer disappears.
    """

    def __init__(
        self,
        *,
        finder: BrowserWindowFinder | None = None,
        page_observer: ScreenPageObserver | None = None,
        cursor: CursorDriver | None = None,
        safe_browser: SafeBrowserControl | None = None,
        window_launcher=None,
    ) -> None:
        self.finder = finder or BrowserWindowFinder()
        self.page_observer = page_observer or ScreenPageObserver(finder=self.finder)
        self.cursor = cursor or CursorDriver()
        self.screen_control = ScreenBrowserControl(
            observer=self.page_observer,
            cursor=self.cursor,
            safe_browser=safe_browser,
            window_launcher=window_launcher,
        )
        self.observer = ScreenBrowserObserverAdapter(self.page_observer, self.finder)
        self.control = ScreenBrowserControlAdapter(self.screen_control, self.observer)

    @property
    def available(self) -> bool:
        return self.screen_control.available

    def open_url(self, url: str) -> BrowserActionResult:
        return self.screen_control.navigate(
            url, window=self.observer._handle_for(None),
        )

    def close(self) -> None:
        """Nothing to tear down: no connection and no browser is owned.

        Present so the chat engine's shutdown path is identical for both
        drivers.
        """
        try:
            self.cursor.end_run(restore=False)
        except Exception:
            pass
