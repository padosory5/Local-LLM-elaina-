"""Read-only Chrome DevTools Protocol browser observation (Phase 4C.1).

Connects to Elaina's dedicated CDP browser session and only ever looks: it
never clicks, types, navigates, or changes any page state. Acting on what is
observed here is Phase 4C.2 and belongs in a separate, more careful module,
the same observe-then-act split already proven in Phase 4B
(windows_ui_observer.py / windows_ui_control.py).

Page content -- text, labels, form values -- is read and returned as plain
data for Elaina to read aloud or reason about. It is never treated as
instructions: a page that says "ignore previous instructions" is just text
on a page, exactly like any other visible label. See brain/browser_action_
planner.py for how that boundary is enforced at the prompt level.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from tools.browser_control.browser_connection import BrowserConnection, BrowserConnectionResult
from tools.computer_control.windows_ui_observer import WindowsUIObserver

_MAX_ELEMENTS = 120
_MAX_LABEL_LENGTH = 120
_MAX_TEXT_LENGTH = 4000
_DOM_READY_WAIT_SECONDS = 2.5
_SCAN_RETRY_COUNT = 3

# Never a tab a user could mean by "this page" -- background/service surfaces
# a browser extension keeps open, not something in the visible tab strip.
_NON_TAB_URL_PREFIXES = (
    "chrome-extension://", "whale-extension://", "extension://",
    "devtools://", "about:blank", "chrome://newtab", "whale://newtab",
)
# Narrower than _NON_TAB_URL_PREFIXES: a blank new-tab page has nothing to
# describe or click, so describe_page/click_element correctly ignore it --
# but navigating one *into* something useful is the ordinary first action
# of a session, so resolve_navigable_page() must still be able to target
# it. Only genuinely non-navigable technical surfaces stay excluded here.
_NON_NAVIGABLE_URL_PREFIXES = (
    "chrome-extension://", "whale-extension://", "extension://", "devtools://",
)

# Shared with tools/browser_control.py's single-element label lookup, so a
# committing-action safety check always sees the exact same label the model
# was shown in the last describe_page() scan -- never a weaker, separately
# derived guess that could miss a real "Submit"/"Pay" label.
_LABEL_LOGIC_JS = """
    const labelledBy = (node) => {
      const ids = (node.getAttribute('aria-labelledby') || '')
        .split(/\s+/).filter(Boolean);
      const text = ids.map((id) => {
        const labelled = document.getElementById(id);
        return labelled ? (labelled.innerText || labelled.textContent || '') : '';
      }).join(' ').trim();
      return text;
    };
    const associatedLabel = (node) => {
      // Many real search/form fields (e.g. Wikipedia's search box) carry no
      // inline aria-label/placeholder at all -- their label lives in a
      // separate <label> element, associated either by a for="id"
      // reference or by containment. Measured live: without this, a real,
      // visible, fillable search input was silently skipped entirely.
      if (node.id) {
        const forLabel = document.querySelector(
          `label[for="${CSS.escape(node.id)}"]`
        );
        if (forLabel && forLabel.innerText) return forLabel.innerText;
      }
      const wrappingLabel = node.closest('label');
      if (wrappingLabel && wrappingLabel.innerText) return wrappingLabel.innerText;
      return '';
    };
    // A real <label> (explicit or wrapping) is authoritative semantic
    // labeling and must outrank a placeholder hint. Measured live: a card
    // number field with no aria-label fell through to its placeholder
    // ("1234 5678 9012 3456") instead of its real label ("Card number"),
    // which would have made the credential-field classifier miss it
    // entirely -- a placeholder is example text, never the field's
    // identity.
    const computeLabel = (el) => (
      el.getAttribute('aria-label') ||
      labelledBy(el) ||
      associatedLabel(el) ||
      el.innerText ||
      el.getAttribute('alt') ||
      (el.querySelector && el.querySelector('img[alt]') || {}).alt ||
      el.value ||
      el.placeholder ||
      el.title ||
      el.name ||
      ''
    ).trim().replace(/\\s+/g, ' ').slice(0, 120);
"""

# Each interactive element found gets a short-lived id (data-elaina-id)
# written directly onto the live DOM node. The model must choose one of
# these real ids -- it can never invent a CSS selector or coordinate, the
# same "resolve against live state, never a guess" rule Phase 4B enforces
# for Windows UI Automation control names.
_SCAN_SCRIPT = f"""
() => {{
{_LABEL_LOGIC_JS}
  const scanId = __ELAINA_SCAN_ID__;
  const SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role="button"]', '[role="link"]', '[role="checkbox"]',
    '[role="radio"]', '[role="tab"]', '[role="menuitem"]',
    '[role="option"]', '[contenteditable="true"]', '[onclick]',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');
  const allNodes = (root) => {{
    const nodes = Array.from(root.querySelectorAll(SELECTOR));
    // Open shadow roots are part of the page's live, inspectable DOM but
    // document.querySelectorAll() does not cross them.  Playwright locators
    // do, so include them in the scan as well.  Closed roots remain private.
    for (const host of Array.from(root.querySelectorAll('*'))) {{
      if (host.shadowRoot) nodes.push(...allNodes(host.shadowRoot));
    }}
    return nodes;
  }};
  const nodes = allNodes(document);
  const results = [];
  let index = 0;
  for (const el of nodes) {{
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
    if (!visible || el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const label = computeLabel(el);
    if (!label) continue;
    // Keep the resolved browser destination rather than the raw attribute.
    // Relative href values (for example Google's "/search?udm=2") otherwise
    // cannot be compared reliably to the live link when BrowserControl
    // re-resolves it moments later.  This value is still page *data*, not an
    // instruction or a model-provided navigation target.
    const href = el.href || el.getAttribute('href') || '';
    // Search engines frequently interleave advertisements with real results.
    // Mark the common semantic/ad containers here so an ordinal request such
    // as "open the first hotel result" can deliberately skip paid links,
    // rather than making a language model guess from an arbitrary label.
    const isAd = !!el.closest(
      '[data-text-ad], [data-ad-details], [data-ad-impression], '
      + '[aria-label="Ads"], [aria-label="Sponsored"]'
    ) || /(?:^|[?&/])(?:aclk|adurl)=/i.test(href);
    const id = scanId + '-e' + index;
    el.setAttribute('data-elaina-id', id);
    el.setAttribute('data-elaina-scan', scanId);
    index += 1;
    results.push({{
      id: id,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      type: el.type || '',
      label: label,
      disabled: !!el.disabled,
      href: href,
      isAd: isAd,
    }});
  }}
  return results;
}}
"""


@dataclass(frozen=True)
class TabInfo:
    index: int
    title: str
    url: str
    is_active: bool = False


@dataclass(frozen=True)
class PageElement:
    id: str
    tag: str
    role: str
    label: str
    element_type: str = ""
    disabled: bool = False
    href: str = ""
    # Page-provided classification only guides which observed link is a
    # sensible candidate for an ordinal result request.  It never authorizes
    # a click by itself; BrowserControl still re-resolves the exact DOM node.
    is_ad: bool = False


@dataclass(frozen=True)
class PageObservation:
    status: str
    url: str = ""
    title: str = ""
    elements: tuple[PageElement, ...] = ()
    truncated: bool = False
    message: str = ""
    tab_index: int | None = None
    scan_id: str = ""


@dataclass(frozen=True)
class PageTextResult:
    status: str
    url: str = ""
    title: str = ""
    text: str = ""
    truncated: bool = False
    message: str = ""
    tab_index: int | None = None


class BrowserObserver:
    """List open tabs and describe one page's real, live-scanned content."""

    def __init__(
        self,
        *,
        connection: BrowserConnection | None = None,
        ui_observer: WindowsUIObserver | None = None,
    ) -> None:
        self.connection = connection or BrowserConnection()
        # Optional: lets _active_tab_index cross-check against the real OS
        # window title (Phase 4B), the one signal CDP attachment doesn't
        # scramble. None (the default) skips straight to the in-page
        # fallback -- deliberately opt-in so tests stay deterministic.
        self.ui_observer = ui_observer
        self._browser: Any = None
        self._playwright: Any = None
        self._connect_result: BrowserConnectionResult | None = None
        # Playwright's sync API is strictly single-thread: a connection
        # created on one thread can never be reused from another, and doing
        # so has been seen to hang rather than raise cleanly. Elaina spawns
        # a new thread per user turn (see main.py's dispatch_response), so
        # a BrowserObserver that outlives a single turn WILL be called from
        # a different thread than the one that created its connection --
        # this is tracked so _ensure_connected can detect that and
        # reconnect fresh instead of handing out a dead-thread handle.
        self._connect_thread_id: int | None = None
        # A page opened by Elaina is the only safe fallback when Windows does
        # not expose a foreground browser title (for example, after the
        # Electron text box receives focus).  Never silently choose the most
        # recently created unrelated tab.
        self._preferred_page_url = ""

    def close(self) -> None:
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._playwright = None
        self._connect_result = None
        self._connect_thread_id = None

    def prefer_page(self, url: str) -> None:
        """Remember an Elaina-opened page for the next terse follow-up."""
        self._preferred_page_url = str(url or "").strip()

    def list_tabs(self) -> tuple[TabInfo, ...] | BrowserConnectionResult:
        result = self._ensure_connected()
        if result.status != "connected":
            return result
        pages = self._all_pages()
        active_index = self._active_tab_index(pages)
        return tuple(
            TabInfo(
                index=index,
                title=self._safe_title(page),
                url=self._safe_url(page),
                is_active=index == active_index,
            )
            for index, page in enumerate(pages)
        )

    def describe_page(
        self,
        tab_index: int | None = None,
        *,
        query: str = "",
    ) -> PageObservation:
        result = self._ensure_connected()
        if result.status != "connected":
            return PageObservation("unavailable", message=result.message)

        page, resolved_index = self._resolve_page_with_index(tab_index)
        if page is None:
            return PageObservation(
                "not_found",
                message=(
                    "I couldn't determine the active browser tab. If none is "
                    "open yet, use search or open_url to open one -- that "
                    "works even with none open. Otherwise select the page "
                    "you mean and try again, or name a tab first."
                ),
            )

        self._wait_for_dom_ready(page)
        scan_id = uuid.uuid4().hex[:16]
        scan_script = _SCAN_SCRIPT.replace(
            "__ELAINA_SCAN_ID__", json.dumps(scan_id),
        )
        raw_elements: list[dict[str, Any]] = []
        last_error: Exception | None = None
        query_terms = self._query_terms(query)
        for attempt in range(_SCAN_RETRY_COUNT):
            try:
                raw_elements = list(page.evaluate(scan_script) or ())
                if (
                    raw_elements
                    and (
                        not query_terms
                        or self._elements_match_query(raw_elements, query_terms)
                    )
                ) or attempt + 1 >= _SCAN_RETRY_COUNT:
                    break
            except Exception as error:
                last_error = error
                break
            time.sleep(0.15)
        if last_error is not None:
            return PageObservation(
                "error", message=f"I couldn't inspect that page: {last_error}",
            )

        raw_elements = self._prioritize_elements(raw_elements, query)

        elements = tuple(
            PageElement(
                id=str(item.get("id", "")),
                tag=str(item.get("tag", "")),
                role=str(item.get("role", "")),
                label=str(item.get("label", ""))[:_MAX_LABEL_LENGTH],
                element_type=str(item.get("type", "")),
                disabled=bool(item.get("disabled", False)),
                href=str(item.get("href", "")),
                is_ad=bool(item.get("isAd", False)),
            )
            for item in raw_elements[:_MAX_ELEMENTS]
        )
        truncated = len(raw_elements) > _MAX_ELEMENTS

        if not elements:
            return PageObservation(
                "empty",
                url=self._safe_url(page),
                title=self._safe_title(page),
                tab_index=resolved_index,
                message=(
                    "This page doesn't expose any interactive elements "
                    "Elaina can use."
                ),
            )
        return PageObservation(
            "observed",
            url=self._safe_url(page),
            title=self._safe_title(page),
            elements=elements,
            truncated=truncated,
            tab_index=resolved_index,
            scan_id=scan_id,
        )

    def read_text(self, tab_index: int | None = None) -> PageTextResult:
        result = self._ensure_connected()
        if result.status != "connected":
            return PageTextResult("unavailable", message=result.message)

        page, resolved_index = self._resolve_page_with_index(tab_index)
        if page is None:
            return PageTextResult(
                "not_found",
                message="I couldn't determine the active browser tab.",
            )
        self._wait_for_dom_ready(page)
        try:
            raw_text = str(page.evaluate("document.body ? document.body.innerText : ''"))
        except Exception as error:
            return PageTextResult(
                "error", message=f"I couldn't read that page: {error}",
            )
        text = raw_text.strip()
        if not text:
            return PageTextResult(
                "empty",
                url=self._safe_url(page),
                title=self._safe_title(page),
                tab_index=resolved_index,
                message="This page doesn't have any readable text content.",
            )
        truncated = len(text) > _MAX_TEXT_LENGTH
        return PageTextResult(
            "observed",
            url=self._safe_url(page),
            title=self._safe_title(page),
            text=text[:_MAX_TEXT_LENGTH],
            truncated=truncated,
            tab_index=resolved_index,
        )

    def _ensure_connected(self) -> BrowserConnectionResult:
        current_thread_id = threading.get_ident()
        if (
            self._connect_result is not None
            and self._connect_result.status == "connected"
        ):
            if self._connect_thread_id == current_thread_id:
                return self._connect_result
            # A connected handle from a different thread is never valid to
            # reuse -- tear it down and reconnect fresh on this one.
            self.close()
        result = self.connection.connect()
        self._connect_result = result
        if result.status == "connected":
            self._browser = result.browser
            self._playwright = result.playwright
            self._connect_thread_id = current_thread_id
        return result

    def _all_pages(self) -> list[Any]:
        return [
            page
            for context in getattr(self._browser, "contexts", ())
            for page in getattr(context, "pages", ())
            if not self._safe_url(page).startswith(_NON_TAB_URL_PREFIXES)
        ]

    def resolve_navigable_page(self, tab_index: int | None) -> Any:
        """Find a page to navigate, including a blank new-tab page.

        Used by BrowserControl.navigate/search (never by an observation
        call): a session's very first browser action ordinarily starts
        from a blank tab, which _resolve_page deliberately excludes since
        there is nothing there yet to describe or click.
        """
        result = self._ensure_connected()
        if result.status != "connected":
            return None
        pages = [
            page
            for context in getattr(self._browser, "contexts", ())
            for page in getattr(context, "pages", ())
            if not self._safe_url(page).startswith(_NON_NAVIGABLE_URL_PREFIXES)
        ]
        if not pages:
            if tab_index is not None:
                # A specific tab was requested; there is nothing to
                # substitute it with.
                return None
            # Playwright's CDP attach to an externally-launched browser
            # (this project always launches its own, never Playwright's
            # own chromium.launch()) does not reliably enumerate that
            # browser's very first default tab -- BrowserConnection.open_url
            # hits the identical gap on a cold launch and already falls
            # back to creating a page outright; mirror that fix here so a
            # session's first search/open_url doesn't spuriously fail.
            return self._new_page()
        if tab_index is None:
            active_index = self._active_tab_index(pages)
            return pages[active_index] if active_index is not None else None
        if 0 <= tab_index < len(pages):
            return pages[tab_index]
        return None

    def _new_page(self) -> Any:
        try:
            contexts = list(getattr(self._browser, "contexts", ()) or ())
            if not contexts:
                return None
            return contexts[0].new_page()
        except Exception:
            return None

    def _resolve_page(self, tab_index: int | None) -> Any:
        page, _ = self._resolve_page_with_index(tab_index)
        return page

    def _resolve_page_with_index(self, tab_index: int | None) -> tuple[Any, int | None]:
        pages = self._all_pages()
        if not pages:
            return None, None
        if tab_index is None:
            active_index = self._active_tab_index(pages)
            return (
                (pages[active_index], active_index)
                if active_index is not None
                else (None, None)
            )
        if 0 <= tab_index < len(pages):
            return pages[tab_index], tab_index
        return None, None

    def _active_tab_index(self, pages: list[Any]) -> int | None:
        """Find which real tab the user is actually looking at.

        Once Elaina's CDP client is attached, Chromium stops backgrounding
        any attached tab for automation's sake -- every page's own
        document.hasFocus()/visibilityState unconditionally reports itself
        as focused and visible, in every real connection this class ever
        makes, so neither is a usable signal here, not even as a
        fallback. The browser's own top-level window title still reflects
        only the one tab actually on top, since Windows -- not Chromium's
        automation-suppressed page state -- decides that; cross-check
        against it (Phase 4B). If that title is unavailable, use only a page
        Elaina herself just opened; otherwise fail closed rather than acting
        on an arbitrary background tab.
        """
        if not pages:
            return None
        if len(pages) == 1:
            return 0
        if self.ui_observer is not None and self.ui_observer.available:
            active_window = self.ui_observer.get_active_window()
            window_title = active_window.title if active_window else ""
            if window_title:
                matches = [
                    index for index, page in enumerate(pages)
                    if self._title_matches_window(
                        self._safe_title(page), window_title,
                    )
                ]
                if len(matches) == 1:
                    return matches[0]
        if self._preferred_page_url:
            # Prefer an exact live URL before the deliberately looser search
            # redirect comparison below. A Google result page can acquire
            # extra tracking parameters, but an exact Elaina-opened tab is a
            # stronger identity signal.
            exact_matches = [
                index for index, page in enumerate(pages)
                if self._preferred_page_url == self._safe_url(page)
            ]
            matches = exact_matches or [
                index for index, page in enumerate(pages)
                if self._urls_refer_to_same_page(
                    self._preferred_page_url, self._safe_url(page),
                )
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                # ``prefer_page`` is assigned only after Elaina itself opens
                # or successfully navigates a controlled tab. Chromium/CDP
                # returns pages in creation order, so a duplicate exact URL
                # belongs to the most recently controlled copy rather than
                # an arbitrary unrelated background tab. This narrowly fixes
                # repeated identical searches without restoring the old
                # global "last tab wins" fallback.
                return matches[-1]
        return None

    @staticmethod
    def _urls_refer_to_same_page(expected: str, actual: str) -> bool:
        if not expected or not actual:
            return False
        if expected == actual:
            return True
        try:
            left, right = urlsplit(expected), urlsplit(actual)
        except ValueError:
            return False
        if (
            left.scheme == right.scheme
            and left.netloc == right.netloc
            and left.path == right.path
            and left.query == right.query
        ):
            return True
        # Search engines commonly add tracking, locale, or interface
        # parameters after navigation.  Preserve the identity of an
        # Elaina-opened search when its host/path and actual search query
        # remain the same, but do not equate arbitrary pages merely because
        # they share a host.
        if left.scheme != right.scheme or left.netloc != right.netloc:
            return False
        left_query = dict(parse_qsl(left.query))
        right_query = dict(parse_qsl(right.query))
        return bool(
            left.path == right.path
            and left_query.get("q")
            and left_query.get("q") == right_query.get("q")
        )

    @staticmethod
    def _title_matches_window(page_title: str, window_title: str) -> bool:
        """Match browser title decorations without guessing a background tab."""
        page_key = re.sub(r"\s+", " ", str(page_title or "")).strip().casefold()
        window_key = re.sub(r"\s+", " ", str(window_title or "")).strip().casefold()
        if not page_key or not window_key:
            return False
        # Chromium normally appends a browser name (for example
        # " - Whale"). Require that title boundary rather than accepting an
        # arbitrary substring: a tab called "You" must not match an active
        # "YouTube" window.
        if window_key == page_key:
            return True
        suffix = window_key.removeprefix(page_key)
        return bool(
            suffix
            and window_key.startswith(page_key)
            and suffix[0] in {" ", "-", "–", "—", "|"}
        )

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        return [
            term for term in re.findall(r"[^\W_]+", str(query).casefold())
            if term not in {
                "a", "an", "the", "this", "that", "page", "button",
                "link", "tab", "control", "click", "press", "tap", "open",
                "show", "please", "for", "me", "on", "in", "to",
            }
        ]

    @staticmethod
    def _elements_match_query(
        elements: list[dict[str, Any]], terms: list[str],
    ) -> bool:
        if not terms:
            return True
        return any(
            all(
                term in " ".join(re.findall(
                    r"[^\W_]+", str(item.get("label", "")).casefold(),
                ))
                for term in terms
            )
            for item in elements
        )

    @classmethod
    def _prioritize_elements(
        cls,
        elements: list[dict[str, Any]], query: str,
    ) -> list[dict[str, Any]]:
        terms = cls._query_terms(query)
        if not terms:
            return elements

        def rank(item: dict[str, Any]) -> tuple[int, int]:
            label = str(item.get("label", "")).casefold()
            normalized = " ".join(re.findall(r"[^\W_]+", label))
            matches = sum(term in normalized for term in terms)
            exact = normalized == " ".join(terms)
            return (0 if exact else 1 if matches == len(terms) else 2, -matches)

        return sorted(elements, key=rank)

    @staticmethod
    def _wait_for_dom_ready(page: Any) -> None:
        wait_for_load_state = getattr(page, "wait_for_load_state", None)
        if not callable(wait_for_load_state):
            return
        try:
            wait_for_load_state("domcontentloaded", timeout=int(_DOM_READY_WAIT_SECONDS * 1000))
        except Exception:
            # Dynamic pages and pages held at a consent dialog can stay in a
            # loading state forever.  The bounded scan retries below still
            # give their already-rendered controls a chance to appear.
            pass

    @staticmethod
    def _safe_title(page: Any) -> str:
        try:
            return str(page.title(timeout=750) or "")
        except TypeError:
            # Older Playwright releases do not accept ``timeout`` on
            # Page.title(). Calling the bare method here is not safe: on a
            # transient blank tab it can wait for the default ~30 seconds.
            # Locator.text_content() does accept a timeout on those releases.
            try:
                return str(page.locator("title").text_content(timeout=750) or "")
            except Exception:
                # Minimal test doubles may only expose title(), and do not
                # have a locator API. They are synchronous, so this retains
                # compatibility without reintroducing the live-page wait.
                if not hasattr(page, "locator"):
                    try:
                        return str(page.title() or "")
                    except Exception:
                        pass
                return ""
        except Exception:
            return ""

    @staticmethod
    def _safe_url(page: Any) -> str:
        try:
            return str(page.url)
        except Exception:
            return ""
