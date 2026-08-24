"""Unified element-level surface abstraction over desktop windows and browser
tabs (Phase 4D groundwork).

Adapts the existing, unmodified Phase 4B (windows_ui_observer.py /
windows_ui_control.py) and Phase 4C (browser_observer.py / browser_control.py)
stacks behind one small, honest vocabulary: describe a surface, then
click/fill/select/scroll one of its just-observed elements, or read its text.
This module performs no lookup, verification, or safety logic itself -- every
real behavior still happens inside the wrapped observer/control instance;
this is pure translation, so it changes nothing about how either stack
already behaves.

Two real asymmetries are kept honest rather than papered over:
- Element identity: a desktop element has no persistent id and is re-resolved
  by fuzzy name every call; a browser element has a scan-scoped
  data-elaina-id that is stale the moment the page changes underneath it.
  See SurfaceElement.identity_kind.
- Scrolling: a desktop control scrolls one line in a caller-given direction;
  a browser element has no direction concept -- it is scrolled into view.
  BrowserSurfaceAdapter.scroll() therefore takes no direction parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.browser_control.browser_control import BrowserControl
from tools.browser_control.browser_observer import BrowserObserver, PageElement, PageObservation
from tools.computer_control.windows_ui_control import WindowsUIControl
from tools.computer_control.windows_ui_observer import ControlInfo, WindowInfo, WindowsUIObserver


@dataclass(frozen=True)
class SurfaceElement:
    role: str
    label: str
    value: str = ""
    is_enabled: bool | None = None
    is_visible: bool | None = None
    is_actionable: bool = False
    href: str = ""
    # What a later click/fill/select/scroll call re-targets.
    element_ref: str = ""
    # "fuzzy_name" (desktop -- re-resolved by name every call, no persistent
    # id) or "scan_id" (browser -- valid only against the scan it came from).
    identity_kind: str = "fuzzy_name"


@dataclass(frozen=True)
class SurfaceObservation:
    status: str
    title: str = ""
    url: str = ""
    elements: tuple[SurfaceElement, ...] = ()
    truncated: bool = False
    message: str = ""
    scan_id: str = ""
    # Opaque, adapter-owned identity of the observed surface -- produced by
    # one adapter's describe() and consumed only by that same adapter's own
    # act methods. Never inspected or constructed by a caller.
    surface_token: Any = None


@dataclass(frozen=True)
class SurfaceActionResult:
    status: str
    message: str
    element_ref: str = ""
    verified: bool | None = None
    evidence: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {
            "clicked", "typed", "filled", "selected", "scrolled",
        }


@dataclass(frozen=True)
class SurfaceTextResult:
    status: str
    text: str = ""
    truncated: bool = False
    message: str = ""
    title: str = ""
    url: str = ""


class DesktopSurfaceAdapter:
    """Adapts WindowsUIObserver/WindowsUIControl to the shared surface shape."""

    def __init__(
        self,
        *,
        observer: WindowsUIObserver | None = None,
        control: WindowsUIControl | None = None,
    ) -> None:
        self.observer = observer or WindowsUIObserver()
        self.control = control or WindowsUIControl(observer=self.observer)

    def describe(self, window: str | WindowInfo) -> SurfaceObservation:
        observation = self.observer.describe_window(window)
        if observation.status != "observed":
            return SurfaceObservation(
                observation.status,
                title=observation.title,
                message=observation.message,
                surface_token=window,
            )
        return SurfaceObservation(
            "observed",
            title=observation.title,
            elements=tuple(
                self._to_surface_element(control)
                for control in observation.controls
            ),
            truncated=observation.truncated,
            surface_token=window,
        )

    def click(
        self,
        observation: SurfaceObservation,
        element: SurfaceElement,
        *,
        confirmed: bool = False,
    ) -> SurfaceActionResult:
        result = self.control.click_control(
            observation.surface_token, element.element_ref, confirmed=confirmed,
        )
        return self._to_surface_result(result)

    def fill(
        self,
        observation: SurfaceObservation,
        element: SurfaceElement,
        text: str,
    ) -> SurfaceActionResult:
        result = self.control.type_text(
            observation.surface_token, element.element_ref, text,
        )
        return self._to_surface_result(result)

    def select(
        self,
        observation: SurfaceObservation,
        element: SurfaceElement,
        option: str,
    ) -> SurfaceActionResult:
        result = self.control.select_option(
            observation.surface_token, element.element_ref, option,
        )
        return self._to_surface_result(result)

    def scroll(
        self,
        observation: SurfaceObservation,
        element: SurfaceElement,
        direction: str,
    ) -> SurfaceActionResult:
        result = self.control.scroll_control(
            observation.surface_token, element.element_ref, direction,
        )
        return self._to_surface_result(result)

    def read_text(self, observation: SurfaceObservation) -> SurfaceTextResult:
        if observation.status != "observed":
            return SurfaceTextResult(observation.status, message=observation.message)
        return SurfaceTextResult(
            "unsupported",
            title=observation.title,
            message=(
                "Desktop windows don't expose a whole-surface text read; "
                "describe() already lists their controls and values."
            ),
        )

    @staticmethod
    def _to_surface_element(control: ControlInfo) -> SurfaceElement:
        return SurfaceElement(
            role=control.role,
            label=control.name,
            value=control.value,
            is_enabled=control.is_enabled,
            is_visible=control.is_visible,
            is_actionable=control.is_actionable,
            element_ref=control.name,
            identity_kind="fuzzy_name",
        )

    @staticmethod
    def _to_surface_result(result: Any) -> SurfaceActionResult:
        return SurfaceActionResult(
            result.status,
            result.message,
            element_ref=result.control_name,
            verified=result.verified,
            evidence=result.evidence,
        )


class BrowserSurfaceAdapter:
    """Adapts BrowserObserver/BrowserControl to the shared surface shape."""

    def __init__(
        self,
        *,
        observer: BrowserObserver | None = None,
        control: BrowserControl | None = None,
    ) -> None:
        self.observer = observer or BrowserObserver()
        self.control = control or BrowserControl(observer=self.observer)

    def describe(
        self,
        tab_index: int | None = None,
        *,
        query: str = "",
    ) -> SurfaceObservation:
        observation = self.observer.describe_page(tab_index, query=query)
        if observation.status != "observed":
            return SurfaceObservation(
                observation.status,
                title=observation.title,
                url=observation.url,
                message=observation.message,
                surface_token=observation.tab_index,
            )
        return SurfaceObservation(
            "observed",
            title=observation.title,
            url=observation.url,
            elements=tuple(
                self._to_surface_element(element)
                for element in observation.elements
            ),
            truncated=observation.truncated,
            scan_id=observation.scan_id,
            surface_token=observation.tab_index,
        )

    def click(
        self,
        observation: SurfaceObservation,
        element: SurfaceElement,
        *,
        confirmed: bool = False,
    ) -> SurfaceActionResult:
        result = self.control.click(
            observation.surface_token,
            element.element_ref,
            confirmed=confirmed,
            **self._expected_metadata(observation, element),
        )
        return self._to_surface_result(result)

    def fill(
        self,
        observation: SurfaceObservation,
        element: SurfaceElement,
        text: str,
        *,
        confirmed: bool = False,
    ) -> SurfaceActionResult:
        result = self.control.fill(
            observation.surface_token,
            element.element_ref,
            text,
            confirmed=confirmed,
            **self._expected_metadata(observation, element),
        )
        return self._to_surface_result(result)

    def select(
        self,
        observation: SurfaceObservation,
        element: SurfaceElement,
        option: str,
    ) -> SurfaceActionResult:
        result = self.control.select_option(
            observation.surface_token,
            element.element_ref,
            option,
            **self._expected_metadata(observation, element),
        )
        return self._to_surface_result(result)

    def scroll(
        self,
        observation: SurfaceObservation,
        element: SurfaceElement,
    ) -> SurfaceActionResult:
        result = self.control.scroll_to(
            observation.surface_token,
            element.element_ref,
            **self._expected_metadata(observation, element),
        )
        return self._to_surface_result(result)

    def read_text(self, observation: SurfaceObservation) -> SurfaceTextResult:
        if observation.status != "observed":
            return SurfaceTextResult(observation.status, message=observation.message)
        result = self.observer.read_text(observation.surface_token)
        return SurfaceTextResult(
            result.status,
            text=result.text,
            truncated=result.truncated,
            message=result.message,
            title=result.title,
            url=result.url,
        )

    @staticmethod
    def _to_surface_element(element: PageElement) -> SurfaceElement:
        return SurfaceElement(
            role=element.role or element.tag,
            label=element.label,
            is_enabled=not element.disabled,
            is_visible=True,
            is_actionable=True,
            href=element.href,
            element_ref=element.id,
            identity_kind="scan_id",
        )

    @staticmethod
    def _expected_metadata(
        observation: SurfaceObservation,
        element: SurfaceElement,
    ) -> dict[str, str]:
        metadata = {
            "expected_label": element.label,
            "expected_url": observation.url,
            "expected_scan_id": observation.scan_id,
            "expected_href": element.href,
        }
        return {key: value for key, value in metadata.items() if value}

    @staticmethod
    def _to_surface_result(result: Any) -> SurfaceActionResult:
        return SurfaceActionResult(
            result.status,
            result.message,
            element_ref=result.element_id,
            verified=result.verified,
            evidence=result.evidence,
        )
