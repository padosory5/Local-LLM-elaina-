"""Verified screen-native browser actions (Phase 4E).

Every action here follows the same shape the CDP path established in
``tools/browser_control/browser_control.py`` and the desktop path in
``windows_ui_control.py``: resolve the target in a *live* tree, refuse or
escalate anything dangerous, act, then re-observe and say what actually
changed.  It reuses those modules' safety predicates rather than restating
them, so a control named "Pay now" is treated identically whichever driver
is in charge.

Driving the physical pointer adds two failure modes CDP does not have, and
both are checked before any click:

* **The window has to be in front.**  A screen coordinate belongs to
  whatever is topmost there, so the browser is focused first and the page is
  re-observed *afterwards* -- focusing can move or resize the window, which
  would invalidate every rectangle read before it.
* **The point has to belong to the browser.**  ``WindowFromPoint`` is asked
  who actually owns the pixel we are about to click. An always-on-top
  overlay, a notification toast, or a dropdown from another app sitting over
  the target turns the click into a refusal instead of a click on something
  the user never asked about.

There is deliberately no UI-Automation ``Invoke`` fallback: this driver
clicks for real or reports that it could not.
"""

from __future__ import annotations

import ctypes
import time
from brain import browser_navigation as navigation
from typing import Any
from urllib.parse import urlsplit

from tools.browser_control.browser_control import (
    BrowserActionResult,
    is_committing_element,
    is_credential_field,
    is_download_link,
    is_payment_element,
)
from tools.browser_control.safe_browser import SafeBrowserControl
from tools.screen_browser.browser_window import BrowserWindow
from tools.screen_control.cursor_driver import CursorDriver
from tools.screen_browser.page_observer import (
    ScreenElement,
    ScreenPageObservation,
    ScreenPageObserver,
)

try:
    import win32con as _win32con
    import win32gui as _win32gui
except Exception:  # pragma: no cover - exercised only when pywin32 is absent
    _win32con = None
    _win32gui = None

# How long to let a page react before deciding an action did nothing. A
# click that navigates commits well inside this; one that does nothing
# still costs only this much before it is honestly reported as unverified.
_SETTLE_SECONDS = 0.45
# How long a page gets to visibly react before an action is called
# unverified, and how often to look while waiting. An observation costs
# about 0.1s, so polling is cheap enough to be the default.
_CHANGE_TIMEOUT_SECONDS = 4.0
_NAVIGATION_TIMEOUT_SECONDS = 9.0
_LANDING_READY_TIMEOUT_SECONDS = 4.0
_WINDOW_LAUNCH_TIMEOUT_SECONDS = 8.0
_POLL_INTERVAL_SECONDS = 0.25
_FOCUS_SETTLE_SECONDS = 0.25
_MAX_TYPE_LENGTH = 500
# Wheel notches for one "scroll down" request -- roughly a screenful.
_SCROLL_NOTCHES = 4

_TEXT_ENTRY_ROLES = frozenset({"textbox", "searchbox", "combobox"})
_SELECT_ROLES = frozenset({"combobox", "listbox"})


class ScreenBrowserControl:
    """Click, type, scroll and navigate in the browser already on screen."""

    def __init__(
        self,
        *,
        observer: ScreenPageObserver | None = None,
        cursor: CursorDriver | None = None,
        safe_browser: SafeBrowserControl | None = None,
        sleeper=None,
        window_at_point=None,
        focuser=None,
        window_launcher=None,
    ) -> None:
        self.observer = observer or ScreenPageObserver()
        self.cursor = cursor or CursorDriver()
        # Reused so URL policy (scheme, localhost/private-IP, invented
        # domains) is decided in exactly one place for both drivers.
        self.safe_browser = safe_browser or SafeBrowserControl(opener=lambda url: None)
        self._sleep = sleeper or time.sleep
        self._window_at_point = window_at_point or self._default_window_at_point
        self._focuser = focuser or self._default_focuser
        self._window_launcher = window_launcher

    @property
    def available(self) -> bool:
        return self.observer.available and self.cursor.available

    # ------------------------------------------------------------------
    # window focus and pixel ownership

    @staticmethod
    def _default_window_at_point(point: tuple[int, int]) -> int:
        if _win32gui is None:  # pragma: no cover - absent pywin32
            return 0
        try:
            return int(_win32gui.WindowFromPoint(point))
        except Exception:
            return 0

    def _default_focuser(self, handle: int) -> bool:
        """Bring a window to the front, verifying after each attempt.

        Windows refuses SetForegroundWindow from a process that does not
        already own the foreground or the most recent input event, and it
        fails *silently as far as timing goes*: the call returns before the
        foreground has actually changed. An earlier version checked
        immediately and concluded it had failed while the window was in fact
        coming forward, so every attempt here is followed by a settle and a
        real check.

        The tiers escalate only as needed. The ALT keystroke exists because
        having just delivered input is one of the conditions that earns a
        process the right to change the foreground.
        """
        if _win32gui is None:  # pragma: no cover - absent pywin32
            return False

        def _is_front() -> bool:
            try:
                return int(_win32gui.GetForegroundWindow()) == int(handle)
            except Exception:
                return False

        if _is_front():
            return True
        try:
            if _win32gui.IsIconic(handle):
                _win32gui.ShowWindow(handle, _win32con.SW_RESTORE)
                self._sleep(_FOCUS_SETTLE_SECONDS)
        except Exception:
            pass

        def _set_foreground() -> None:
            _win32gui.SetForegroundWindow(handle)

        def _with_alt() -> None:
            self.cursor.press("alt")
            _win32gui.SetForegroundWindow(handle)

        def _switch_to() -> None:
            ctypes.windll.user32.SwitchToThisWindow(handle, True)

        for attempt in (_set_foreground, _with_alt, _switch_to):
            try:
                attempt()
            except Exception:
                pass
            self._sleep(_FOCUS_SETTLE_SECONDS)
            if _is_front():
                return True
        return False

    def _owns_point(self, handle: int, point: tuple[int, int]) -> bool:
        """True when the pixel about to be clicked belongs to this window."""
        owner = self._window_at_point(point)
        if not owner:
            return False
        if owner == handle:
            return True
        if _win32gui is None:  # pragma: no cover - absent pywin32
            return False
        try:
            # Page content lives in child windows of the browser frame.
            root = _win32gui.GetAncestor(owner, 2)  # GA_ROOT
            return int(root) == int(handle)
        except Exception:
            return False

    def focus_and_observe(
        self, window: BrowserWindow | int | None = None,
    ) -> ScreenPageObservation:
        """Bring the browser forward, then scan it.

        Order matters: focusing can restore, move or resize the window, so
        any rectangle read before it would be stale by the time we clicked.
        """
        observation = self.observer.observe(window)
        if observation.status != "observed" or observation.handle is None:
            return observation
        if not self._focuser(observation.handle):
            return ScreenPageObservation(
                "not_focused",
                handle=observation.handle,
                title=observation.title,
                url=observation.url,
                message=(
                    "I could not bring the browser window to the front, so I "
                    "will not click at screen coordinates it may not own."
                ),
            )
        self._sleep(_FOCUS_SETTLE_SECONDS)
        return self.observer.observe(observation.handle)

    # ------------------------------------------------------------------
    # actions

    def click(
        self,
        index: int,
        *,
        expected_label: str = "",
        expected_scan_id: str = "",
        window: BrowserWindow | int | None = None,
        confirmed: bool = False,
    ) -> BrowserActionResult:
        """Click an observed element by its index."""
        observation = self.focus_and_observe(window)
        if observation.status != "observed":
            return BrowserActionResult(
                "unavailable", observation.message, element_id=str(index),
            )
        lookup = self.observer.resolve(
            observation, index,
            expected_label=expected_label, expected_scan_id=expected_scan_id,
        )
        if lookup.status != "resolved" or lookup.element is None:
            return BrowserActionResult(
                "not_found", lookup.message, element_id=str(index),
                url=observation.url,
            )
        element = lookup.element
        label = element.label

        refusal = self._safety_refusal(element, confirmed=confirmed)
        if refusal is not None:
            return refusal

        if element.disabled:
            return BrowserActionResult(
                "not_actionable",
                f"{label!r} is disabled on this page.",
                element_id=str(index), element_label=label, url=observation.url,
            )
        if not self._owns_point(observation.handle, element.click_point):
            return BrowserActionResult(
                "blocked",
                f"Something else is covering {label!r} on screen, so I did "
                "not click there.",
                element_id=str(index), element_label=label, url=observation.url,
            )

        before = self._page_signature(observation)
        outcome = self.cursor.click(element.click_point)
        if not outcome.succeeded:
            return BrowserActionResult(
                outcome.status, outcome.message,
                element_id=str(index), element_label=label, url=observation.url,
            )
        after = self._await_change(observation.handle, before)
        changed, evidence = self._changed(before, after)
        if not changed:
            return BrowserActionResult(
                "click_unverified",
                f"I clicked {label!r} but nothing observable changed on the "
                "page, so I cannot say it worked.",
                element_id=str(index), element_label=label,
                url=after.url or observation.url, verified=False,
            )
        return BrowserActionResult(
            "clicked",
            f"Clicked {label!r}.",
            element_id=str(index), element_label=label,
            url=after.url or observation.url, verified=True, evidence=evidence,
        )

    def fill(
        self,
        index: int,
        text: str,
        *,
        expected_label: str = "",
        expected_scan_id: str = "",
        window: BrowserWindow | int | None = None,
    ) -> BrowserActionResult:
        """Type into an observed text field, then read the value back."""
        value = str(text)
        if len(value) > _MAX_TYPE_LENGTH:
            return BrowserActionResult(
                "refused",
                f"That text is longer than the {_MAX_TYPE_LENGTH}-character "
                "limit for one field.",
                element_id=str(index),
            )
        observation = self.focus_and_observe(window)
        if observation.status != "observed":
            return BrowserActionResult(
                "unavailable", observation.message, element_id=str(index),
            )
        lookup = self.observer.resolve(
            observation, index,
            expected_label=expected_label, expected_scan_id=expected_scan_id,
        )
        if lookup.status != "resolved" or lookup.element is None:
            return BrowserActionResult(
                "not_found", lookup.message, element_id=str(index),
                url=observation.url,
            )
        element = lookup.element
        label = element.label
        if is_credential_field(label, element.role):
            return BrowserActionResult(
                "refused",
                f"{label!r} looks like a credential field -- please type that "
                "one yourself.",
                element_id=str(index), element_label=label, url=observation.url,
            )
        if element.role not in _TEXT_ENTRY_ROLES:
            return BrowserActionResult(
                "not_actionable",
                f"{label!r} is a {element.role}, not a text field.",
                element_id=str(index), element_label=label, url=observation.url,
            )
        if element.disabled:
            return BrowserActionResult(
                "not_actionable", f"{label!r} is disabled on this page.",
                element_id=str(index), element_label=label, url=observation.url,
            )
        if not self._owns_point(observation.handle, element.click_point):
            return BrowserActionResult(
                "blocked",
                f"Something else is covering {label!r} on screen, so I did "
                "not type into it.",
                element_id=str(index), element_label=label, url=observation.url,
            )

        # Typing lands wherever focus is, so the field must be clicked and
        # cleared first -- and the click itself is what puts focus there.
        clicked = self.cursor.click(element.click_point)
        if not clicked.succeeded:
            return BrowserActionResult(
                clicked.status, clicked.message,
                element_id=str(index), element_label=label, url=observation.url,
            )
        self._sleep(_SETTLE_SECONDS)
        cleared = self.cursor.clear_field()
        if not cleared.succeeded:
            return BrowserActionResult(
                cleared.status, cleared.message,
                element_id=str(index), element_label=label, url=observation.url,
            )
        typed = self.cursor.type_text(value)
        if not typed.succeeded:
            return BrowserActionResult(
                typed.status, typed.message,
                element_id=str(index), element_label=label, url=observation.url,
            )
        self._sleep(_SETTLE_SECONDS)
        actual = self._read_back(observation.handle, index, label)
        if actual is None:
            return BrowserActionResult(
                "fill_unverified",
                f"I typed into {label!r} but could not read the field back to "
                "confirm it.",
                element_id=str(index), element_label=label,
                url=observation.url, verified=None,
            )
        if value.strip() and value.strip() not in actual:
            return BrowserActionResult(
                "fill_unverified",
                f"After typing, {label!r} reads {actual!r} rather than "
                f"{value!r}, so I did not treat it as filled.",
                element_id=str(index), element_label=label,
                url=observation.url, verified=False,
            )
        return BrowserActionResult(
            "filled",
            f"Typed {value!r} into {label!r}.",
            element_id=str(index), element_label=label, url=observation.url,
            verified=True, evidence=f"field now reads {actual!r}",
        )

    def select_option(
        self,
        index: int,
        option: str,
        *,
        expected_label: str = "",
        expected_scan_id: str = "",
        window: BrowserWindow | int | None = None,
    ) -> BrowserActionResult:
        """Select a combobox option with click, type-to-select, and readback."""
        value = " ".join(str(option or "").split()).strip()
        if not value:
            return BrowserActionResult("refused", "Tell me which option to select.")
        observation = self.focus_and_observe(window)
        if observation.status != "observed":
            return BrowserActionResult("unavailable", observation.message)
        lookup = self.observer.resolve(
            observation,
            index,
            expected_label=expected_label,
            expected_scan_id=expected_scan_id,
        )
        if lookup.status != "resolved" or lookup.element is None:
            return BrowserActionResult(
                "not_found", lookup.message, element_id=str(index),
                url=observation.url,
            )
        element = lookup.element
        if element.role not in _SELECT_ROLES:
            return BrowserActionResult(
                "not_actionable",
                f"{element.label!r} is a {element.role}, not a selectable list.",
                element_id=str(index), element_label=element.label,
                url=observation.url,
            )
        if element.disabled:
            return BrowserActionResult(
                "not_actionable", f"{element.label!r} is disabled.",
                element_id=str(index), element_label=element.label,
                url=observation.url,
            )
        if not self._owns_point(observation.handle, element.click_point):
            return BrowserActionResult(
                "blocked", f"Something else is covering {element.label!r}.",
                element_id=str(index), element_label=element.label,
                url=observation.url,
            )

        for operation in (
            lambda: self.cursor.click(element.click_point),
            lambda: self.cursor.type_text(value),
            lambda: self.cursor.press("enter"),
        ):
            outcome = operation()
            if not outcome.succeeded:
                return BrowserActionResult(outcome.status, outcome.message)
            self._sleep(_SETTLE_SECONDS)

        after = self.observer.observe(observation.handle)
        selected_value = ""
        if after.status == "observed":
            for candidate in after.elements:
                if (
                    candidate.role in _SELECT_ROLES
                    and candidate.label == element.label
                ):
                    selected_value = candidate.value
                    break
        verified = _normalized_text(value) in _normalized_text(selected_value)
        if not verified:
            return BrowserActionResult(
                "select_unverified",
                f"I chose {value!r} in {element.label!r}, but could not read "
                "that selection back.",
                element_id=str(index), element_label=element.label,
                url=after.url or observation.url, verified=False,
            )
        return BrowserActionResult(
            "selected",
            f"Selected {selected_value!r} in {element.label!r}.",
            element_id=str(index), element_label=element.label,
            url=after.url or observation.url, verified=True,
            evidence=f"accessible value reads {selected_value!r}",
        )

    def submit(
        self, *, window: BrowserWindow | int | None = None,
    ) -> BrowserActionResult:
        """Press Enter in the focused field and report what changed."""
        observation = self.focus_and_observe(window)
        if observation.status != "observed":
            return BrowserActionResult("unavailable", observation.message)
        before = self._page_signature(observation)
        pressed = self.cursor.press("enter")
        if not pressed.succeeded:
            return BrowserActionResult(pressed.status, pressed.message)
        after = self._await_change(observation.handle, before)
        changed, evidence = self._changed(before, after)
        if not changed:
            return BrowserActionResult(
                "click_unverified",
                "I pressed Enter but the page did not visibly change.",
                url=after.url or observation.url, verified=False,
            )
        return BrowserActionResult(
            "clicked", "Submitted.", url=after.url or observation.url,
            verified=True, evidence=evidence,
        )

    def scroll(
        self,
        direction: str = "down",
        *,
        window: BrowserWindow | int | None = None,
        notches: int = _SCROLL_NOTCHES,
    ) -> BrowserActionResult:
        """Wheel-scroll the page body."""
        observation = self.focus_and_observe(window)
        if observation.status != "observed":
            return BrowserActionResult("unavailable", observation.message)
        heading = str(direction).strip().lower()
        if heading not in {"up", "down"}:
            return BrowserActionResult(
                "refused", "I can scroll up or down.",
            )
        centre = self._page_centre(observation)
        if centre is None:
            return BrowserActionResult(
                "unavailable", "I could not work out where the page body is.",
            )
        before = self._page_signature(observation)
        outcome = self.cursor.scroll(
            centre, notches if heading == "up" else -notches,
        )
        if not outcome.succeeded:
            return BrowserActionResult(outcome.status, outcome.message)
        after = self._await_change(
            observation.handle, before, timeout=_SETTLE_SECONDS * 2,
        )
        changed, evidence = self._changed(before, after)
        return BrowserActionResult(
            "scrolled" if changed else "click_unverified",
            f"Scrolled {heading}." if changed
            else f"I scrolled {heading} but the page did not move.",
            url=after.url or observation.url,
            verified=changed, evidence=evidence,
        )

    def navigate(
        self, url: str, *, window: BrowserWindow | int | None = None,
    ) -> BrowserActionResult:
        """Type a validated URL into the address bar and go there."""
        resolution = self.safe_browser.resolve(url)
        if resolution.status != "resolved":
            return BrowserActionResult("refused", resolution.message)
        return self._go_to(resolution.url, window=window)

    def search(
        self, query: str, *, window: BrowserWindow | int | None = None,
    ) -> BrowserActionResult:
        """Search using the configured engine -- never a model-chosen domain."""
        resolution = self.safe_browser.resolve_search(query)
        if resolution.status != "resolved":
            return BrowserActionResult("refused", resolution.message)
        return self._go_to(resolution.url, window=window)

    def _go_to(
        self, url: str, *, window: BrowserWindow | int | None = None,
    ) -> BrowserActionResult:
        # Navigation needs a real browser *window*, not an already-readable
        # document. Fresh tabs and about:blank commonly expose no Document
        # node at all; requiring one before Ctrl+L created the exact deadlock
        # where Elaina could not leave the blank page.
        initial = self.observer.observe(window)
        if initial.handle is None and self._window_launcher is not None:
            try:
                self._window_launcher()
            except Exception as error:
                return BrowserActionResult(
                    "unavailable",
                    f"I could not launch the browser ({type(error).__name__}).",
                )
            attempts = max(
                1, int(_WINDOW_LAUNCH_TIMEOUT_SECONDS / _POLL_INTERVAL_SECONDS),
            )
            for _ in range(attempts):
                self._sleep(_POLL_INTERVAL_SECONDS)
                initial = self.observer.observe(None)
                if initial.handle is not None:
                    break
        if initial.handle is None:
            return BrowserActionResult("unavailable", initial.message)
        handle = initial.handle
        if not self._focuser(handle):
            return BrowserActionResult(
                "unavailable",
                "I could not bring that browser window to the front.",
            )
        self._sleep(_FOCUS_SETTLE_SECONDS)
        before_observation = self.observer.observe(handle)
        before = (
            self._page_signature(before_observation)
            if before_observation.status == "observed"
            else ("", before_observation.title, 0, ())
        )

        last = before_observation
        for attempt in range(2):
            if attempt:
                # One bounded recovery for a tab that stayed blank or whose
                # renderer never woke. Escape cancels a stuck omnibox/page
                # load before selecting the address bar again.
                cancelled = self.cursor.press("escape")
                if not cancelled.succeeded:
                    return BrowserActionResult(cancelled.status, cancelled.message)
            focused = self.cursor.press("ctrl", "l")
            if not focused.succeeded:
                return BrowserActionResult(focused.status, focused.message)
            self._sleep(_SETTLE_SECONDS)
            typed = self.cursor.type_text(url)
            if not typed.succeeded:
                return BrowserActionResult(typed.status, typed.message)
            entered = self.cursor.press("enter")
            if not entered.succeeded:
                return BrowserActionResult(entered.status, entered.message)
            last = self._await_change(
                handle, before, timeout=_NAVIGATION_TIMEOUT_SECONDS,
            )
            if last.status != "observed":
                continue
            # The omnibox changes before a renderer has necessarily exposed
            # its first usable accessibility tree. Returning at that instant
            # made the next command resolve a transient index (live on
            # Google: an unlabeled link became "Skip to main content"). Wait
            # for two matching, meaningful scans just like Browser Use waits
            # for a usable page state rather than treating the URL event as
            # the end of navigation.
            last = self._await_landing_ready(handle, last)
            landed = last.url or ""
            readable = self._landing_is_readable(last)
            if readable and self._same_page(landed, url):
                return self._navigation_result(url, handle, last, before)
            if (
                readable
                and self._usable_web_url(landed)
                and landed != before[0]
            ):
                # Redirects are normal. The reported URL is always what the
                # live address bar shows, never the requested value.
                return self._navigation_result(url, handle, last, before)
            # Only a still-blank/unreadable landing receives the one retry.

        return self._navigation_result(url, handle, last, before)

    def _navigation_result(self, url, handle, page, before):
        """Keep the observation made against the HWND that received Enter."""
        receipt = navigation.verify(navigation.start(url, url), (
            navigation.PageEvidence(
                url=page.url, title=page.title,
                text=" ".join((*page.headings, page.text_excerpt)),
                identity=f"hwnd:{handle}:{page.scan_id}",
                correlated=page.handle == handle,
                readable=page.status == "observed",
            ),
        ), before=((before[0], before[1]),))
        if self._page_signature(page) == before and receipt.arrived:
            from dataclasses import replace
            receipt = replace(receipt, status=navigation.UNVERIFIED,
                              classification="stale_tab", detail="the page did not change")
        return BrowserActionResult(
            "navigated" if receipt.arrived else "navigate_unverified",
            f"Opened {receipt.actual_url}." if receipt.arrived
            else f"I could not verify {url}: {receipt.detail}.",
            url=receipt.actual_url, verified=True if receipt.arrived else (False if receipt.checked else None),
            evidence=receipt.detail, navigation=receipt,
        )

    # ------------------------------------------------------------------
    # safety

    def _safety_refusal(
        self, element: ScreenElement, *, confirmed: bool,
    ) -> BrowserActionResult | None:
        """Refusals and confirmation gates, shared with the CDP driver."""
        label = element.label
        if is_payment_element(label):
            return BrowserActionResult(
                "refused",
                f"{label!r} looks like it completes a payment -- please do "
                "that yourself.",
                element_id=str(element.index), element_label=label,
            )
        if is_credential_field(label, element.role):
            return BrowserActionResult(
                "refused",
                f"{label!r} looks like a credential field -- please handle "
                "that one yourself.",
                element_id=str(element.index), element_label=label,
            )
        # Chromium exposes a link's target through UIA, so the href-based
        # checks the CDP driver runs are available here too rather than
        # being quietly skipped.
        target = element.href
        if target and not self._target_is_permitted(target):
            return BrowserActionResult(
                "refused",
                f"{label!r} points at {target} -- I only follow ordinary "
                "web addresses, not local or private-network ones.",
                element_id=str(element.index), element_label=label,
            )
        if not confirmed and is_download_link(label, target, False):
            return BrowserActionResult(
                "confirmation_required",
                f"Downloading {label!r} needs confirmation first.",
                element_id=str(element.index), element_label=label,
            )
        if not confirmed and is_committing_element(label):
            return BrowserActionResult(
                "confirmation_required",
                f"Clicking {label!r} needs confirmation first.",
                element_id=str(element.index), element_label=label,
            )
        return None

    def _target_is_permitted(self, href: str) -> bool:
        """Whether a scanned link target passes the shared URL policy.

        Decided by SafeBrowserControl so `file:`, localhost and private-IP
        rules are defined in exactly one place for both drivers.
        """
        try:
            return self.safe_browser.resolve(href).status == "resolved"
        except Exception:
            return False

    # ------------------------------------------------------------------
    # verification helpers

    @staticmethod
    def _page_signature(
        observation: ScreenPageObservation,
    ) -> tuple[str, str, int, tuple[str, ...]]:
        return (
            observation.url,
            observation.title,
            len(observation.elements),
            tuple(element.label for element in observation.elements[:12]),
        )

    @staticmethod
    def _changed(
        before: tuple[str, str, int, tuple[str, ...]],
        after: ScreenPageObservation,
    ) -> tuple[bool, str]:
        """Did anything observable change, and what."""
        if after.status != "observed":
            return False, ""
        if after.url != before[0]:
            return True, f"URL changed to {after.url}"
        if after.title != before[1]:
            return True, f"title changed to {after.title!r}"
        current = tuple(element.label for element in after.elements[:12])
        if current != before[3]:
            return True, "the page's controls changed"
        if len(after.elements) != before[2]:
            return True, "the number of controls on the page changed"
        return False, ""

    def _await_change(
        self,
        handle: int | None,
        before: tuple[str, str, int, tuple[str, ...]],
        *,
        timeout: float = _CHANGE_TIMEOUT_SECONDS,
    ) -> ScreenPageObservation:
        """Re-observe until the page differs, or the timeout expires.

        A click that navigates does not finish inside a fixed short sleep --
        measured against a real page, an ordinary link took well over half a
        second to commit, and checking too early reported a click that had
        plainly worked as unverified. Polling an observation that costs
        ~0.1s is both quicker than a generous fixed sleep on a fast page and
        honest on a slow one: it returns the moment something really changed
        and never claims a still-identical page has moved on.
        """
        observation = self.observer.observe(handle)
        attempts = max(1, int(timeout / _POLL_INTERVAL_SECONDS))
        for _ in range(attempts):
            changed, _ = self._changed(before, observation)
            if changed:
                return observation
            self._sleep(_POLL_INTERVAL_SECONDS)
            observation = self.observer.observe(handle)
        return observation

    def _await_landing_ready(
        self,
        handle: int | None,
        observation: ScreenPageObservation,
    ) -> ScreenPageObservation:
        """Wait for a meaningful accessibility tree to settle twice.

        A URL and even a Document node can arrive before the real controls.
        Requiring two matching usable scans avoids handing a transient index
        to the next action while keeping the wait bounded.
        """
        current = observation
        prior_signature = None
        stable_count = 0
        attempts = max(
            1,
            int(_LANDING_READY_TIMEOUT_SECONDS / _POLL_INTERVAL_SECONDS),
        )
        for _ in range(attempts):
            if self._landing_is_readable(current):
                signature = self._page_signature(current)
                if signature == prior_signature:
                    stable_count += 1
                else:
                    prior_signature = signature
                    stable_count = 1
                if stable_count >= 2:
                    return current
            else:
                prior_signature = None
                stable_count = 0
            self._sleep(_POLL_INTERVAL_SECONDS)
            current = self.observer.observe(handle)
        return current

    @classmethod
    def _landing_is_readable(cls, observation: ScreenPageObservation) -> bool:
        if (
            observation.status != "observed"
            or not cls._usable_web_url(observation.url)
        ):
            return False
        meaningful = tuple(
            element for element in observation.elements
            if _normalized_text(element.label)
            not in {"", "unlabeled", "skip to main content"}
        )
        if cls._is_search_results_url(observation.url):
            # Search chrome can appear well before the results. A heading or
            # a labeled main-content link is the first trustworthy signal
            # that an ordinal like "first result" has an order to resolve.
            return bool(observation.headings) or any(
                element.role == "link" and element.href and element.in_main
                for element in meaningful
            )
        return bool(
            observation.headings
            or observation.text_excerpt.strip()
            or meaningful
        )

    @staticmethod
    def _is_search_results_url(url: str) -> bool:
        try:
            parsed = urlsplit(str(url))
        except ValueError:
            return False
        host = (parsed.hostname or "").casefold()
        path = (parsed.path or "/").casefold()
        return bool(
            ("google." in host and path == "/search")
            or (host.endswith("bing.com") and path == "/search")
            or (host.endswith("search.yahoo.com") and path == "/search")
            or (host.endswith("duckduckgo.com") and path == "/")
        )

    def _read_back(
        self, handle: int | None, index: int, label: str,
    ) -> str | None:
        """The field's value after typing, matched by label not position."""
        observation = self.observer.observe(handle)
        if observation.status != "observed":
            return None
        for element in observation.elements:
            if element.label == label and element.role in _TEXT_ENTRY_ROLES:
                return element.value
        for element in observation.elements:
            if element.index == index and element.role in _TEXT_ENTRY_ROLES:
                return element.value
        return None

    @staticmethod
    def _page_centre(
        observation: ScreenPageObservation,
    ) -> tuple[int, int] | None:
        points = [
            element.click_point for element in observation.elements
            if element.click_point != (0, 0)
        ]
        if not points:
            return None
        xs = sorted(point[0] for point in points)
        ys = sorted(point[1] for point in points)
        return (xs[len(xs) // 2], ys[len(ys) // 2])

    @staticmethod
    def _same_page(landed: str, requested: str) -> bool:
        """Whether the address bar shows the page that was asked for.

        Chromium's omnibox drops the scheme and often the trailing slash, so
        host plus path is the comparable part.
        """
        def _key(value: str) -> tuple[str, str]:
            parsed = urlsplit(value if "//" in value else f"https://{value}")
            host = parsed.netloc.lower().removeprefix("www.")
            return host, parsed.path.rstrip("/")

        try:
            return _key(landed) == _key(requested)
        except Exception:
            return False

    @staticmethod
    def _usable_web_url(value: str) -> bool:
        text = str(value or "").strip().casefold()
        return bool(text) and not text.startswith(("about:blank", "about:newtab"))


def _normalized_text(value: str) -> str:
    return " ".join(str(value or "").casefold().split()).strip()
