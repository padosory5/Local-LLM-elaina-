"""Read a live browser page through UI Automation (Phase 4E).

Strictly read-only, the same boundary ``windows_ui_observer.py`` holds for
desktop windows: nothing here clicks, types, focuses, or scrolls.  Acting on
what this returns is ``screen_browser_control.py``.

Why this exists at all: measured against a real, already-open Whale window on
YouTube, a complete observation -- page tree walk, ARIA roles, rectangles,
URL, headings and text -- costs about 0.04s.  The CDP path it replaces
budgets up to 15 seconds just to launch a second browser before it can look
at anything.

Two Chromium behaviours shape the whole module:

* **ARIA roles are exposed.**  ``IUIAutomationElement.CurrentAriaRole``
  returns the real web role -- ``link``, ``heading``, ``dialog``, ``main``,
  ``banner`` -- not just the coarse UIA control type.  Preferring it keeps
  this observer close to the DOM semantics the CDP observer had, including
  the landmark information the planner needs to tell page content from site
  chrome.
* **The renderer accessibility tree is lazily built.**  A browser window
  nobody has queried exposes about a dozen nodes and no Document at all.
  It wakes on first query and stays warm.  ``WM_GETOBJECT`` with
  ``OBJID_CLIENT`` was tested as a wake signal and does *not* work, so the
  honest handling is a bounded retry and then ``not_observable`` -- never a
  claim that a page is empty when it merely has not woken up.
"""

from __future__ import annotations

import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

from tools.screen_browser.browser_window import BrowserWindow, BrowserWindowFinder
from tools.screen_control.dpi import ensure_per_monitor_dpi_aware

ensure_per_monitor_dpi_aware()

try:
    from pywinauto import Desktop as _Desktop
except Exception:  # pragma: no cover - exercised only when pywinauto is absent
    _Desktop = None


# A page's own text can be a whole paragraph per node. Both caps keep one
# observation inside a model context window, matching the reasoning behind
# the equivalent caps in windows_ui_observer.py.
_MAX_LABEL_LENGTH = 90
_MAX_ELEMENTS = 60
_MAX_HEADINGS = 12
_MAX_IMAGE_LABELS = 24
_MAX_TEXT_EXCERPT = 1200
_MAX_WALK_NODES = 2500
_MAX_WALK_DEPTH = 40
_WALK_BUDGET_SECONDS = 6.0

# A cold window exposes no Document node at all -- measured at ~12 nodes for
# the whole window, against ~200 once Chromium has built the renderer tree.
# The absence of the Document is therefore the signal, and these bound how
# long it is given to appear before the page is reported as unreadable.
_WAKE_RETRY_DELAY_SECONDS = 0.6
_WAKE_RETRY_ATTEMPTS = 3

# UIA_ValueValuePropertyId -- a text field's current contents.
_UIA_VALUE_VALUE_PROPERTY = 30045

# ARIA roles a person can operate. Preferred over control type because
# Chromium reports the authored role here.
_INTERACTIVE_ARIA_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "menuitem", "menuitemcheckbox", "menuitemradio", "option",
    "tab", "slider", "spinbutton", "switch", "treeitem", "listbox",
})
# Fallback for nodes Chromium leaves without an ARIA role (plain HTML with
# no authored role still reports one, but non-Chromium engines may not).
_INTERACTIVE_CONTROL_TYPES = frozenset({
    "Button", "Hyperlink", "Edit", "ComboBox", "CheckBox", "RadioButton",
    "MenuItem", "TabItem", "Slider", "Spinner", "TreeItem", "ListItem",
})
_CONTROL_TYPE_TO_ROLE = {
    "Button": "button",
    "Hyperlink": "link",
    "Edit": "textbox",
    "ComboBox": "combobox",
    "CheckBox": "checkbox",
    "RadioButton": "radio",
    "MenuItem": "menuitem",
    "TabItem": "tab",
    "Slider": "slider",
    "Spinner": "spinbutton",
    "TreeItem": "treeitem",
    "ListItem": "option",
    "Text": "text",
    "Image": "image",
    "Document": "document",
}
_TEXT_ENTRY_ROLES = frozenset({"textbox", "searchbox", "combobox"})
_LINK_ROLES = frozenset({"link"})
_MAX_HREF_LENGTH = 400
_DIALOG_ROLES = frozenset({"dialog", "alertdialog"})
# Landmarks that are site chrome rather than the page's own answer.
_CHROME_LANDMARKS = frozenset({
    "banner", "navigation", "contentinfo", "complementary",
})
_MAIN_LANDMARKS = frozenset({"main"})


def _clean(value: Any, limit: int = _MAX_LABEL_LENGTH) -> str:
    """Collapse whitespace and bound length without mangling non-Latin text."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


@dataclass(frozen=True)
class ScreenElement:
    """One operable element, addressed by index the way Browser Use does."""

    index: int
    role: str
    label: str
    value: str = ""
    # A link's target. Chromium exposes it through UIA ValuePattern on
    # Hyperlink nodes, so the accessibility tree is not the href-free
    # surface it first appears to be -- which is what lets ad links and
    # download links be recognised on this driver too.
    href: str = ""
    disabled: bool = False
    # Physical screen pixels. click_point is the point the cursor driver
    # will actually move to.
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    click_point: tuple[int, int] = (0, 0)
    in_dialog: bool = False
    in_main: bool = True

    @property
    def display(self) -> str:
        label = self.label or "<unlabeled>"
        parts = [f"[{self.index}] <{self.role}> {label!r}"]
        if self.value:
            parts.append(f"value={self.value!r}")
        if self.disabled:
            parts.append("[disabled]")
        if self.in_dialog:
            parts.append("[in dialog]")
        return " ".join(parts)


@dataclass(frozen=True)
class ScreenPageObservation:
    """One scan of one browser window's live page."""

    status: str  # observed | cold_tree | no_page | no_browser | unavailable
    handle: int | None = None
    title: str = ""
    # Read from the browser's own address bar. Chromium hides the scheme
    # there, so this is normalized but still reported as what the omnibox
    # shows -- it is page identity, not a fetched URL.
    url: str = ""
    elements: tuple[ScreenElement, ...] = ()
    headings: tuple[str, ...] = ()
    text_excerpt: str = ""
    text_truncated: bool = False
    image_labels: tuple[str, ...] = ()
    image_count: int = 0
    truncated: bool = False
    blocking_dialog: bool = False
    scan_id: str = ""
    message: str = ""
    elapsed_seconds: float = 0.0

    def as_digest(self, limit: int = 0) -> str:
        """The page rendered for the model. Page text is data, never orders."""
        if self.status != "observed":
            return self.message or f"Page not observable ({self.status})."
        lines = [f"Page: {self.title!r}", f"URL: {self.url or '(unknown)'}"]
        if self.blocking_dialog:
            lines.append(
                "A dialog is open over this page. Its own controls are the "
                "only ones reachable until it is dealt with."
            )
        if self.headings:
            lines.append("Headings: " + " | ".join(self.headings))
        if self.text_excerpt:
            suffix = " [excerpt truncated]" if self.text_truncated else ""
            lines.append(f"Visible page text: {self.text_excerpt}{suffix}")
        shown = self.elements[:limit] if limit else self.elements
        for element in shown:
            lines.append("- " + element.display)
        if limit and len(self.elements) > limit:
            lines.append(f"... {len(self.elements) - limit} more element(s) not listed.")
        elif self.truncated:
            lines.append("... more elements exist beyond those listed.")
        return "\n".join(lines)


@dataclass
class _ScanRecord:
    """A scan kept only so an index can be resolved back to a live node."""

    scan_id: str
    handle: int
    elements: dict[int, tuple[Any, ScreenElement]] = field(default_factory=dict)


@dataclass(frozen=True)
class ElementLookup:
    status: str  # resolved | stale_scan | unknown_index | changed
    element: ScreenElement | None = None
    node: Any = None
    message: str = ""


class ScreenPageObserver:
    """Describe the live page in an already-open browser window."""

    def __init__(
        self,
        *,
        finder: BrowserWindowFinder | None = None,
        desktop: Any = None,
        sleeper=None,
    ) -> None:
        self.finder = finder or BrowserWindowFinder()
        self._desktop = desktop
        self._sleep = sleeper or time.sleep
        self._last_scan: _ScanRecord | None = None

    @property
    def available(self) -> bool:
        return _Desktop is not None or self._desktop is not None

    def _window_wrapper(self, handle: int) -> Any:
        desktop = self._desktop
        if desktop is None:
            if _Desktop is None:  # pragma: no cover - absent pywinauto
                raise RuntimeError("pywinauto is not available")
            desktop = _Desktop(backend="uia")
        return desktop.window(handle=int(handle))

    # ------------------------------------------------------------------
    # observation

    def observe(self, window: BrowserWindow | int | None = None) -> ScreenPageObservation:
        """Scan the given (or foreground) browser window's page."""
        if not self.available:
            return ScreenPageObservation(
                "unavailable",
                message="UI Automation is not available on this machine.",
            )
        target = self._resolve_window(window)
        if isinstance(target, ScreenPageObservation):
            return target

        started = time.time()
        for attempt in range(_WAKE_RETRY_ATTEMPTS):
            try:
                wrapper = self._window_wrapper(target.handle)
                document = self._find_document(wrapper)
            except Exception as error:
                return ScreenPageObservation(
                    "unavailable",
                    handle=target.handle,
                    message=(
                        "The browser window could not be read through UI "
                        f"Automation ({type(error).__name__})."
                    ),
                    elapsed_seconds=time.time() - started,
                )
            if document is not None:
                return self._observe_document(
                    target, wrapper, document, started,
                )
            # Cold renderer tree: give Chromium a beat to build it. This is
            # the documented lazy-activation case, not an empty page.
            if attempt < _WAKE_RETRY_ATTEMPTS - 1:
                self._sleep(_WAKE_RETRY_DELAY_SECONDS)

        return ScreenPageObservation(
            "cold_tree",
            handle=target.handle,
            title=target.page_title,
            message=(
                "This browser window is not exposing its page to UI "
                "Automation, so I cannot see what is on it. Its accessibility "
                "tree never became available."
            ),
            elapsed_seconds=time.time() - started,
        )

    def _resolve_window(
        self, window: BrowserWindow | int | None,
    ) -> BrowserWindow | ScreenPageObservation:
        if isinstance(window, BrowserWindow):
            return window
        if isinstance(window, int):
            found = self.finder.window_for_handle(window)
            if found is None:
                return ScreenPageObservation(
                    "no_browser",
                    message="That browser window is no longer open.",
                )
            return found
        active = self.finder.active_window()
        if active is not None:
            return active
        open_windows = self.finder.list_windows()
        if not open_windows:
            return ScreenPageObservation(
                "no_browser",
                message="No browser window is open right now.",
            )
        titles = ", ".join(repr(item.page_title) for item in open_windows[:4])
        return ScreenPageObservation(
            "no_browser",
            message=(
                f"{len(open_windows)} browser windows are open and none has "
                f"focus ({titles}). Tell me which one to use."
            ),
        )

    def _find_document(self, wrapper: Any) -> Any:
        """The page viewport node, or None while the tree is still cold."""
        try:
            documents = wrapper.descendants(control_type="Document")
        except Exception:
            return None
        for document in documents:
            try:
                rect = document.element_info.rectangle
                if rect.right > rect.left and rect.bottom > rect.top:
                    return document
            except Exception:
                continue
        return None

    def _observe_document(
        self,
        window: BrowserWindow,
        wrapper: Any,
        document: Any,
        started: float,
    ) -> ScreenPageObservation:
        info = document.element_info
        viewport = info.rectangle
        nodes = self._walk(info)

        elements: list[ScreenElement] = []
        headings: list[str] = []
        text_parts: list[str] = []
        image_labels: list[str] = []
        image_count = 0
        record: dict[int, tuple[Any, ScreenElement]] = {}
        seen: set[tuple[str, str, int, int]] = set()
        seen_text: set[str] = set()
        blocking_dialog = False
        truncated = False

        for node, ancestry in nodes:
            role, label, value, href, rect, enabled = self._read_node(node)
            if not role:
                continue
            in_dialog = bool(ancestry & _DIALOG_ROLES)
            if in_dialog:
                blocking_dialog = True
            if role == "heading":
                if label and len(headings) < _MAX_HEADINGS:
                    headings.append(label)
                continue
            if role == "image":
                image_count += 1
                if label and len(image_labels) < _MAX_IMAGE_LABELS:
                    image_labels.append(label)
                continue
            if role in {"text", "document"}:
                if self._is_readable_text(label) and not (ancestry & _CHROME_LANDMARKS):
                    if label not in seen_text:
                        seen_text.add(label)
                        text_parts.append(label)
                continue
            if role not in _INTERACTIVE_ARIA_ROLES:
                continue
            centre = self._click_point(rect)
            if centre is None or not self._within(centre, viewport):
                # Off-screen and scrolled-away controls carry real but
                # unusable rectangles -- a YouTube skip link measured at
                # y=-960. Clicking one moves the pointer off the page.
                continue
            key = (role, label, centre[0] // 4, centre[1] // 4)
            if key in seen:
                continue
            seen.add(key)
            if len(elements) >= _MAX_ELEMENTS:
                truncated = True
                continue
            element = ScreenElement(
                index=len(elements),
                role=role,
                label=label,
                value=value,
                href=href,
                disabled=enabled is False,
                rect=rect,
                click_point=centre,
                in_dialog=in_dialog,
                in_main=not (ancestry & _CHROME_LANDMARKS)
                or bool(ancestry & _MAIN_LANDMARKS),
            )
            elements.append(element)
            record[element.index] = (node, element)

        if blocking_dialog:
            # A modal's own controls are the only reachable ones, so they
            # are listed first rather than buried under the page behind it.
            elements = self._dialog_first(elements)
            record = {element.index: record[element.index] for element in elements
                      if element.index in record}

        excerpt = " ".join(text_parts)
        text_truncated = len(excerpt) > _MAX_TEXT_EXCERPT
        if text_truncated:
            excerpt = excerpt[:_MAX_TEXT_EXCERPT].rstrip() + "…"

        scan_id = uuid.uuid4().hex[:8]
        self._last_scan = _ScanRecord(
            scan_id=scan_id, handle=window.handle, elements=record,
        )
        return ScreenPageObservation(
            "observed",
            handle=window.handle,
            title=_clean(info.name or window.page_title, 120),
            url=self._read_url(wrapper),
            elements=tuple(elements),
            headings=tuple(headings),
            text_excerpt=excerpt,
            text_truncated=text_truncated,
            image_labels=tuple(image_labels),
            image_count=image_count,
            truncated=truncated,
            blocking_dialog=blocking_dialog,
            scan_id=scan_id,
            elapsed_seconds=time.time() - started,
        )

    @staticmethod
    def _dialog_first(elements: list[ScreenElement]) -> list[ScreenElement]:
        ordered = [item for item in elements if item.in_dialog]
        ordered += [item for item in elements if not item.in_dialog]
        return [
            ScreenElement(
                index=position,
                role=item.role,
                label=item.label,
                value=item.value,
                href=item.href,
                disabled=item.disabled,
                rect=item.rect,
                click_point=item.click_point,
                in_dialog=item.in_dialog,
                in_main=item.in_main,
            )
            for position, item in enumerate(ordered)
        ]

    def _walk(self, root: Any) -> list[tuple[Any, frozenset[str]]]:
        """Depth-first nodes paired with the landmark roles above them."""
        collected: list[tuple[Any, frozenset[str]]] = []
        deadline = time.time() + _WALK_BUDGET_SECONDS

        def _descend(node: Any, ancestry: frozenset[str], depth: int) -> None:
            if depth > _MAX_WALK_DEPTH or len(collected) >= _MAX_WALK_NODES:
                return
            if time.time() > deadline:
                return
            try:
                children = node.children()
            except Exception:
                return
            for child in children:
                collected.append((child, ancestry))
                role = self._aria_role(child)
                if role in _DIALOG_ROLES or role in _CHROME_LANDMARKS or role in _MAIN_LANDMARKS:
                    child_ancestry = ancestry | {role}
                else:
                    child_ancestry = ancestry
                _descend(child, child_ancestry, depth + 1)

        _descend(root, frozenset(), 0)
        return collected

    @staticmethod
    def _aria_role(info: Any) -> str:
        try:
            return str(info.element.CurrentAriaRole or "").strip().lower()
        except Exception:
            return ""

    def _read_node(
        self, info: Any,
    ) -> tuple[str, str, str, str, tuple[int, int, int, int], bool | None]:
        """(role, label, value, href, rect, enabled) for one node."""
        try:
            control_type = str(info.control_type or "")
        except Exception:
            control_type = ""
        role = self._aria_role(info)
        if role not in _INTERACTIVE_ARIA_ROLES and role not in {
            "heading", "text", "image", "document",
        }:
            # No usable ARIA role: fall back to the coarse control type so a
            # non-Chromium engine still yields something operable.
            if control_type in _INTERACTIVE_CONTROL_TYPES or control_type in {
                "Text", "Image", "Document",
            }:
                role = _CONTROL_TYPE_TO_ROLE.get(control_type, "")
            else:
                role = ""
        if not role:
            return "", "", "", "", (0, 0, 0, 0), None
        try:
            label = _clean(info.name)
        except Exception:
            label = ""
        try:
            rectangle = info.rectangle
            rect = (
                int(rectangle.left), int(rectangle.top),
                int(rectangle.right), int(rectangle.bottom),
            )
        except Exception:
            rect = (0, 0, 0, 0)
        value = ""
        href = ""
        if role in _TEXT_ENTRY_ROLES:
            value = _clean(self.read_value(info), 120)
        elif role in _LINK_ROLES:
            candidate = _clean(self.read_value(info), _MAX_HREF_LENGTH)
            if candidate.startswith(("http://", "https://", "file:", "about:")):
                href = candidate
        enabled: bool | None
        try:
            enabled = bool(info.enabled)
        except Exception:
            enabled = None
        return role, label, value, href, rect, enabled

    @staticmethod
    def _is_readable_text(label: str) -> bool:
        """Keep prose out of which serialized state has leaked.

        Web apps routinely park JSON in an accessible name -- a live YouTube
        page exposed a player-state blob that way. Reporting that back as
        "visible page text" is worse than saying nothing.
        """
        text = str(label or "").strip()
        if len(text) < 2:
            return False
        if text[0] in "{[<":
            return False
        return not (text.count('"') >= 2 and ":" in text)

    @staticmethod
    def read_value(info: Any) -> str:
        """A field's current text via UIA ValuePattern.

        UIA_ValueValuePropertyId. Read through the property rather than the
        pattern object because it is one cross-process call instead of two,
        and this runs for every text field in every scan.
        """
        element = getattr(info, "element", None)
        if element is None:
            return ""
        try:
            return str(element.GetCurrentPropertyValue(_UIA_VALUE_VALUE_PROPERTY) or "")
        except Exception:
            return ""

    @staticmethod
    def _click_point(rect: tuple[int, int, int, int]) -> tuple[int, int] | None:
        left, top, right, bottom = rect
        if right <= left or bottom <= top:
            return None
        return ((left + right) // 2, (top + bottom) // 2)

    @staticmethod
    def _within(point: tuple[int, int], viewport: Any) -> bool:
        try:
            return (
                viewport.left <= point[0] <= viewport.right
                and viewport.top <= point[1] <= viewport.bottom
            )
        except Exception:
            return False

    def _read_url(self, wrapper: Any) -> str:
        """The omnibox value, normalized to a URL.

        Chromium hides the scheme in the address bar, so "example.com/x"
        comes back rather than "https://example.com/x".
        """
        try:
            edits = wrapper.descendants(control_type="Edit")
        except Exception:
            return ""
        for edit in edits:
            try:
                value = _clean(self.read_value(edit.element_info), 400)
            except Exception:
                continue
            if not value or " " in value:
                continue
            if value.startswith(("http://", "https://", "about:", "file:")):
                return value
            if "." in value.split("/")[0]:
                return f"https://{value}"
        return ""

    # ------------------------------------------------------------------
    # resolution

    def resolve(
        self,
        observation: ScreenPageObservation,
        index: int,
        *,
        expected_label: str = "",
        expected_scan_id: str = "",
    ) -> ElementLookup:
        """Re-resolve an index against a freshly taken observation.

        Freshness is the caller's job -- ScreenBrowserControl re-observes
        immediately before every action, so what is resolved here is always
        the live page.  This method's job is *identity*: that index still
        has to name the thing the caller meant.

        ``expected_scan_id`` is therefore not required to match. It cannot:
        acting re-scans, which mints a new id by design. What it does is
        decide how strict to be -- when the caller is working from an older
        scan, an ``expected_label`` becomes mandatory, because without one
        there is nothing left to prove the index still means the same
        element rather than whatever scrolled into that slot.
        """
        record = self._last_scan
        if record is None or record.scan_id != observation.scan_id:
            return ElementLookup(
                "stale_scan",
                message="That page scan is no longer current; I re-scanned instead.",
            )
        if expected_scan_id and expected_scan_id != record.scan_id and not expected_label:
            return ElementLookup(
                "stale_scan",
                message=(
                    "The page was re-scanned since you chose that element and "
                    "I have no label to check it against, so I did not act on it."
                ),
            )
        entry = record.elements.get(int(index))
        if entry is None:
            return ElementLookup(
                "unknown_index",
                message=f"There is no element [{index}] on the page I scanned.",
            )
        node, element = entry
        if expected_label and _clean(expected_label) != element.label:
            return ElementLookup(
                "changed",
                message=(
                    f"Element [{index}] is now {element.label!r}, not "
                    f"{_clean(expected_label)!r}, so I did not click it."
                ),
            )
        return ElementLookup("resolved", element=element, node=node)
