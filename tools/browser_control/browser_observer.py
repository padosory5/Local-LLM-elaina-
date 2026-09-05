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
# How many elements the scan will compute an accessible label for.
# Labelling reads innerText and forces a layout, so it is the whole
# cost of a scan on a dense page; ranking happens before it, so the
# budget only ever discards elements that were already lowest
# priority. Comfortably above _MAX_ELEMENTS because unlabelled
# elements are dropped rather than returned.
_LABEL_BUDGET = 300
_MAX_LABEL_LENGTH = 120
_MAX_TEXT_LENGTH = 4000
_MAX_SNAPSHOT_TEXT_LENGTH = 1600
_MAX_SNAPSHOT_HEADINGS = 12
_MAX_SNAPSHOT_IMAGES = 12
_DOM_READY_WAIT_SECONDS = 2.5
_SCAN_RETRY_COUNT = 3
# Below this, the page is more likely still rendering than genuinely this
# sparse -- worth one more look. A page that really is this small (a login
# wall, an error page) just pays the bounded retries once.
_MIN_RENDERED_ELEMENTS = 8
# Long enough for a client-side framework to paint its first content pass.
_SCAN_RETRY_INTERVAL_SECONDS = 0.4

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
# A transient startup tab is not an actionable page, but calling it "not
# found" while the controlled browser visibly sits at about:blank makes a
# recoverable launch/navigation delay look like a missing browser. Keep this
# narrow: extension and DevTools surfaces are never reported as user pages.
_TRANSIENT_STARTUP_URL_PREFIXES = (
    "about:blank", "chrome://newtab", "whale://newtab",
)
_PAGE_READY_STATE_SCRIPT = "document.readyState"

# A search result's accessible label is the whole result block, breadcrumb
# and all: "Novotel Citygate Hong Kong Booking.com > ... > Hotels in Hong
# Kong". Read back aloud, that is unintelligible -- and it was, live, both
# in the confirmation question and in the spoken result.
#
# This only ever produces the *spoken* form. Every safety and verification
# path keeps the raw label, because that is what has to match the real
# element on the page.
_LABEL_BREADCRUMB = re.compile(r"\s*(?:›|»|>|\||·|—|\.{3}|…)\s*")
_LABEL_TRAILING_DOMAIN = re.compile(
    r"\s+\S+\.(?:com|net|org|io|co\.kr|co\.uk|jp|kr)\s*$", re.IGNORECASE,
)
_SPOKEN_LABEL_MAX = 60


def spoken_label(label: str, *, limit: int = _SPOKEN_LABEL_MAX) -> str:
    """The part of an element label worth saying out loud."""
    text = " ".join(str(label or "").split()).strip()
    if not text:
        return ""
    head = _LABEL_BREADCRUMB.split(text)[0].strip() or text
    head = _LABEL_TRAILING_DOMAIN.sub("", head).strip() or head
    if len(head) <= limit:
        return head
    clipped = head[:limit].rsplit(" ", 1)[0].strip()
    return clipped or head[:limit].strip()

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

# Observation is deliberately read-only.  Older code used JavaScript's
# ``HTMLElement.click()`` here to dismiss cookie banners before a scan. That
# was an unsafe exception to the observe-then-act boundary: it could click a
# page-provided "Reject" control outside a real privacy dialog and bypassed
# Playwright's ordinary actionability checks.  The scan now *marks* a narrow,
# verified privacy-rejection candidate. BrowserControl performs the optional
# low-risk action through the normal revalidation path and verifies that the
# banner actually went away.


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
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
  const PRIVACY_HINT = /cookie|consent|gdpr|privacy|cmp|onetrust|didomi|quantcast|trustarc|sp_message/i;
  const REJECT_ONLY = /^(?:reject(?:\\s+all)?|decline(?:\\s+all)?|refuse(?:\\s+all)?|only\\s+(?:essential|necessary)(?:\\s+cookies)?|(?:essential|necessary)\\s+only|continue\\s+without|모두\\s*거부|거부|필수만|필수\\s*쿠키만|동의하지\\s*않(?:기|음)?)$/i;
  const NEVER_CONSENT = /accept|agree|allow|consent|enable|subscribe|sign|login|log\\s*in|register|buy|pay|order|submit|동의|허용|구독|가입|결제/i;
  // Only ever called for an element whose own label is an exact
  // reject-only string (see PASS 2b) -- typically one element on a page.
  // Calling it for every element instead cost seven seconds a scan,
  // because innerText near the top of the tree is the whole document.
  const privacyContainerFor = (el) => {{
    let node = el;
    // A consent dialog is never buried twenty levels down; the bound stops
    // a deep tree from turning this into a document-wide read.
    for (let depth = 0; node && node !== document.body && depth < 12; depth++) {{
      const style = window.getComputedStyle(node);
      const dialog = node.matches && node.matches(
        '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog[open]'
      );
      const fixedOverlay = (style.position === 'fixed' || style.position === 'sticky');
      if (dialog || fixedOverlay) {{
        const clues = [
          node.id || '', String(node.className || ''),
          node.getAttribute && (node.getAttribute('aria-label') || ''),
          // innerText forces a layout, so it is read only for a container
          // already shaped like an overlay, and only when it is small
          // enough to be one.
          (node.childElementCount <= 40 ? (node.innerText || '') : '').slice(0, 800),
        ].join(' ');
        if (PRIVACY_HINT.test(clues)) return node;
      }}
      node = node.parentElement;
    }}
    return null;
  }};
  // PASS 1 -- cheap per-element work only. computeLabel() reads
  // innerText and forces a layout, so it is deliberately NOT called here:
  // on a dense results page that alone cost ten seconds for elements that
  // were about to be discarded by the 120-element cap.
  const shortlist = [];
  for (const el of nodes) {{
    const rect = el.getBoundingClientRect();
    const hasBox = rect.width > 0 && rect.height > 0;
    if (!hasBox) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const style = window.getComputedStyle(el);
    // A "skip to content" link is the first focusable element on nearly
    // every accessible site, and it is parked off-screen until focused --
    // real dimensions, real href, completely unclickable. Measured live on
    // Wikipedia and Google: it was scanned as element zero, so it was the
    // model's most obvious "first link", and clicking it spent Playwright's
    // full actionability timeout before failing. The same three tricks
    // hide screen-reader-only text everywhere, so all three are excluded.
    const offScreen = rect.right <= 0 || rect.bottom <= 0 ||
      rect.left >= viewportWidth + 2000;
    // The screen-reader-only idiom clips an element to nothing. Both
    // classic spellings appear in the wild -- rect(0,0,0,0) on Wikipedia
    // and rect(1px,1px,1px,1px) on Google -- so measure the clip rather
    // than pattern-match one of them. clip is rect(top,right,bottom,left),
    // making the visible box (right-left) x (bottom-top).
    const clipHidesEverything = (value) => {{
      if (!value || value === 'auto') return false;
      const parts = value.match(/-?[\d.]+/g);
      if (!parts || parts.length < 4) return false;
      const [top, right, bottom, left] = parts.map(Number);
      return (right - left) <= 1 || (bottom - top) <= 1;
    }};
    const clipped = style.clipPath === 'inset(50%)' ||
      clipHidesEverything(style.clip);
    if (
      offScreen || clipped ||
      style.visibility === 'hidden' || style.display === 'none' ||
      style.opacity === '0'
    ) continue;
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
    // A modal's own controls are the only ones reachable while it is
    // open; everything behind it is painted but inert. Recording this
    // lets the planner deal with the dialog first instead of clicking
    // through it into nothing.
    const inDialog = !!el.closest(
      '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog[open]'
    );
    // Page chrome (site nav, header, footer, cookie bars) crowds out the
    // content a goal is actually about. This does not drop anything -- it
    // only decides what the model reads first on a dense page.
    const inMain = !!el.closest('main, [role="main"], article, #content, #bodyContent')
      || !el.closest('nav, header, footer, aside, [role="navigation"], [role="banner"], [role="contentinfo"]');
    shortlist.push({{
      el: el, href: href, isAd: isAd, inDialog: inDialog, inMain: inMain,
    }});
  }}
  // Rank before labelling, so the elements that survive the cap are the
  // ones worth spending a layout on: a modal's controls first (nothing
  // behind one is reachable), then real content, then site chrome, then
  // ads. Array.prototype.sort is stable, so document order is preserved
  // within each band.
  shortlist.sort((a, b) => (
    (a.inDialog === b.inDialog ? 0 : a.inDialog ? -1 : 1) ||
    (a.inMain === b.inMain ? 0 : a.inMain ? -1 : 1) ||
    (a.isAd === b.isAd ? 0 : a.isAd ? 1 : -1)
  ));

  // PASS 2a -- reads only. computeLabel() reads innerText, which forces
  // a synchronous layout. Interleaving those reads with the setAttribute
  // writes below made every element pay for its own reflow: measured at
  // 7.0s on a large article whose actual per-phase work summed to 21ms.
  // Batching the reads means one layout for all of them.
  const LABEL_BUDGET = __ELAINA_LABEL_BUDGET__;
  const labelled = [];
  for (const item of shortlist) {{
    if (labelled.length >= LABEL_BUDGET) break;
    const label = computeLabel(item.el);
    // An element with no accessible name at all is not something anyone
    // can be told to click, so it is dropped rather than returned blank.
    if (!label) continue;
    labelled.push({{ item: item, label: label }});
  }}

  // PASS 2b -- writes only. Nothing here reads back a laid-out value, so
  // the invalidations these cause are never forced to resolve.
  for (const entry of labelled) {{
    const item = entry.item;
    const el = item.el;
    const label = entry.label;
    // The container walk is expensive, and isPrivacyDismissal needs the
    // label test to pass anyway -- so test the label first and walk only
    // for the handful of elements that could possibly qualify.
    const rejectLabelled = REJECT_ONLY.test(label) && !NEVER_CONSENT.test(label);
    const inPrivacyDialog = rejectLabelled && !!privacyContainerFor(el);
    // This is not permission to click. It is a conservative UI hint passed
    // to BrowserControl, which repeats these checks against the live element
    // and uses Playwright's actionability/verification path before acting.
    const isPrivacyDismissal = inPrivacyDialog && rejectLabelled;
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
      href: item.href,
      isAd: item.isAd,
      inDialog: item.inDialog,
      inPrivacyDialog: inPrivacyDialog,
      isPrivacyDismissal: isPrivacyDismissal,
      inMain: item.inMain,
    }});
  }}
  return results;
}}
"""

# A compact, semantic companion to the interactive scan.  This is bounded
# intentionally: feeding an entire retailer or travel site into a planner is
# slower and less reliable than giving it the headings, meaningful page text,
# and image labels first. Images without an accessible text alternative are
# counted but not hallucinated as descriptions.
_CONTENT_SUMMARY_SCRIPT = r"""
() => {
  const marker = '__ELAINA_CONTENT_SUMMARY__';
  const normalise = (value, limit) => String(value || '')
    .replace(/\s+/g, ' ').trim().slice(0, limit);
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none' &&
      style.opacity !== '0';
  };
  const root = document.querySelector(
    'main, [role="main"], article, #content, #main, #bodyContent'
  ) || document.body || document.documentElement;
  const headings = [];
  const seenHeadings = new Set();
  for (const node of Array.from(root.querySelectorAll(
    'h1, h2, h3, [role="heading"]'
  ))) {
    if (!visible(node)) continue;
    const text = normalise(node.innerText || node.textContent, 180);
    const key = text.toLocaleLowerCase();
    if (text && !seenHeadings.has(key)) {
      seenHeadings.add(key);
      headings.push(text);
      if (headings.length >= 12) break;
    }
  }
  const fullText = normalise(root.innerText || root.textContent, 12000);
  const images = [];
  const seenImages = new Set();
  let imageCount = 0;
  for (const node of Array.from(root.querySelectorAll(
    'img, [role="img"], svg[role="img"]'
  ))) {
    if (!visible(node)) continue;
    imageCount += 1;
    const label = normalise(
      node.getAttribute('alt') || node.getAttribute('aria-label') ||
      node.getAttribute('title') || '',
      160,
    );
    const key = label.toLocaleLowerCase();
    if (label && !seenImages.has(key) && images.length < 12) {
      seenImages.add(key);
      images.push(label);
    }
  }
  return {
    marker,
    headings,
    text: fullText.slice(0, 1600),
    textLength: fullText.length,
    images,
    imageCount,
  };
}
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
    # Inside an open [role=dialog]/aria-modal container. A modal's own
    # controls are the only ones actually reachable while it is up, so the
    # planner needs to know which those are.
    in_dialog: bool = False
    # A visible dialog/overlay whose own metadata/text identifies it as a
    # cookie/privacy consent surface. This is intentionally narrower than
    # ``in_dialog``: login, newsletter, and promotional modals must never be
    # treated as a cookie prompt.
    in_privacy_dialog: bool = False
    # A reject/essential-only control in a verified privacy surface. It is a
    # candidate only; BrowserControl rechecks the live DOM before it clicks.
    is_privacy_dismissal: bool = False
    # In the page's main content rather than its site chrome. Only affects
    # reading order on a dense page; nothing is ever dropped for it.
    in_main: bool = True
    # Where it sits on the screen, in physical pixels: (left, top, right,
    # bottom). Zero when the observer has no geometry -- the CDP path does
    # not -- so nothing may depend on it being present. What it is for is
    # telling identical labels apart: two links both reading ABOUT are
    # distinguishable only by what they are next to.
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass(frozen=True)
class PageImage:
    """An accessible semantic label for a visible page image.

    We intentionally do not invent a caption for images that only expose
    pixels/canvas. ``PageObservation.image_count`` lets Elaina explain that
    such images exist without pretending to know what they show.
    """

    label: str


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
    # Filled only by BrowserActionPlanner after BrowserControl has safely
    # dismissed a verified privacy overlay and re-observed the page.
    dismissed_overlays: tuple[str, ...] = ()
    # True when a modal dialog is still covering the page after dismissal:
    # its controls are listed first, and nothing behind it is clickable.
    blocking_dialog: bool = False
    # Semantic page snapshot returned with the control scan. This is a
    # bounded digest, not an assertion that every page pixel is accessible.
    headings: tuple[str, ...] = ()
    text_excerpt: str = ""
    text_truncated: bool = False
    images: tuple[PageImage, ...] = ()
    image_count: int = 0


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
        self._preferred_page_ref: Any | None = None

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
        self._preferred_page_ref = None

    def prefer_page(self, url: str) -> None:
        """Remember an Elaina-opened page for the next terse follow-up."""
        url = str(url or "").strip()
        if self._preferred_page_ref is not None and self._safe_url(self._preferred_page_ref) != url:
            self._preferred_page_ref = None
        self._preferred_page_url = url

    def bind_page(self, page: Any) -> None:
        """Keep the dispatched Page identity while this CDP connection lives."""
        self._preferred_page_ref = page
        self._preferred_page_url = self._safe_url(page)

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
            startup_page = self._transient_startup_page()
            if startup_page is not None:
                return PageObservation(
                    "loading",
                    url=self._safe_url(startup_page),
                    title=self._safe_title(startup_page),
                    message=(
                        "The controlled browser is still on its blank startup "
                        "page. I won't inspect or act until navigation reaches "
                        "a real page."
                    ),
                )
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
        headings, text_excerpt, text_truncated, images, image_count = (
            self._content_summary(page)
        )
        scan_id = uuid.uuid4().hex[:16]
        scan_script = _SCAN_SCRIPT.replace(
            "__ELAINA_SCAN_ID__", json.dumps(scan_id),
        ).replace(
            "__ELAINA_LABEL_BUDGET__", str(_LABEL_BUDGET),
        )
        raw_elements: list[dict[str, Any]] = []
        last_error: Exception | None = None
        query_terms = self._query_terms(query)
        for attempt in range(_SCAN_RETRY_COUNT):
            try:
                raw_elements = list(page.evaluate(scan_script) or ())
                # A single-page app frequently paints its shell first and
                # its content a beat later. Accepting that first scan hands
                # the model a page that does not exist yet -- measured on a
                # YouTube results page: 3 elements immediately after
                # navigation, 120 one second later.
                # A caller-supplied query is the strongest possible signal
                # that the page is ready: the control it asked for is
                # actually there. Only a query-less scan has to fall back
                # to "does this look rendered yet".
                if query_terms:
                    settled = self._elements_match_query(
                        raw_elements, query_terms,
                    )
                else:
                    settled = len(raw_elements) >= _MIN_RENDERED_ELEMENTS
                if settled or attempt + 1 >= _SCAN_RETRY_COUNT:
                    break
            except Exception as error:
                last_error = error
                break
            time.sleep(_SCAN_RETRY_INTERVAL_SECONDS)
        if last_error is not None:
            return PageObservation(
                "error", message=f"I couldn't inspect that page: {last_error}",
            )

        raw_elements = self._prioritize_elements(raw_elements, query)

        # While a modal is open, its controls are the only ones actually
        # reachable -- list them first so the model deals with the dialog
        # before trying to click anything painted behind it. Below that,
        # main content outranks site chrome: on a dense page the element
        # cap would otherwise be spent on menus, login links, and language
        # pickers before reaching what the goal is about.
        raw_elements.sort(
            key=lambda item: (
                0 if item.get("inDialog") else 1,
                0 if item.get("inMain", True) else 1,
                0 if not item.get("isAd") else 1,
            ),
        )
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
                in_dialog=bool(item.get("inDialog", False)),
                in_privacy_dialog=bool(item.get("inPrivacyDialog", False)),
                is_privacy_dismissal=bool(item.get("isPrivacyDismissal", False)),
                in_main=bool(item.get("inMain", True)),
            )
            for item in raw_elements[:_MAX_ELEMENTS]
        )
        truncated = len(raw_elements) > _MAX_ELEMENTS
        blocking_dialog = any(element.in_dialog for element in elements)

        observation_metadata = {
            "headings": headings,
            "text_excerpt": text_excerpt,
            "text_truncated": text_truncated,
            "images": images,
            "image_count": image_count,
        }
        if not elements:
            if self._page_is_loading(page):
                return PageObservation(
                    "loading",
                    url=self._safe_url(page),
                    title=self._safe_title(page),
                    tab_index=resolved_index,
                    message=(
                        "The page is still loading and has not exposed usable "
                        "controls yet. I stopped before treating it as an empty "
                        "page."
                    ),
                    **observation_metadata,
                )
            if headings or text_excerpt or images or image_count:
                return PageObservation(
                    "observed",
                    url=self._safe_url(page),
                    title=self._safe_title(page),
                    tab_index=resolved_index,
                    scan_id=scan_id,
                    **observation_metadata,
                )
            return PageObservation(
                "empty",
                url=self._safe_url(page),
                title=self._safe_title(page),
                tab_index=resolved_index,
                message=(
                    "This page doesn't expose any interactive elements "
                    "Elaina can use."
                ),
                **observation_metadata,
            )
        return PageObservation(
            "observed",
            url=self._safe_url(page),
            title=self._safe_title(page),
            elements=elements,
            truncated=truncated,
            tab_index=resolved_index,
            scan_id=scan_id,
            blocking_dialog=blocking_dialog,
            **observation_metadata,
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

    def _ensure_connected(
        self, *, allow_isolated_launch: bool = False,
    ) -> BrowserConnectionResult:
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
        result = self.connection.connect(
            allow_isolated_launch=allow_isolated_launch,
        )
        self._connect_result = result
        if result.status == "connected":
            self._browser = result.browser
            self._playwright = result.playwright
            self._connect_thread_id = current_thread_id
            # Generic "open this URL" actions travel through
            # BrowserConnection.open_url(), while browser-page actions use
            # BrowserControl.navigate().  Carry the former's verified final
            # URL into the same safe active-page fallback so the next terse
            # follow-up ("click that" / "read this page") does not lose its
            # tab identity merely because Electron regained focus.
            opened_url = str(
                getattr(self.connection, "last_opened_url", "") or "",
            ).strip()
            if opened_url:
                self.prefer_page(opened_url)
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
            # No tab index means anything, because there are no tabs: on a
            # cold start every index is equally invalid, so honouring the
            # model's guessed `tab: 0` produced a hard "I couldn't find
            # that browser tab" instead of opening the session's first
            # page. Found live -- the planner burned every round retrying
            # search/open_url against a browser that had no pages yet, and
            # reported that it could not check anything.
            #
            # An out-of-range index against *existing* tabs is still a real
            # mistake and still returns None below.
            #
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
            if active_index is not None:
                return pages[active_index]
            # Undecidable active tab. Reading or clicking would stop here,
            # because acting on the wrong page is dangerous -- but this is
            # navigation in Elaina's own isolated browser, where any tab is
            # a valid place to point at a new URL. Found live: two leftover
            # tabs made every search fail with "I couldn't find that
            # browser tab" until the browser was closed by hand.
            preferred = self._preferred_page(pages)
            return preferred if preferred is not None else pages[-1]
        if 0 <= tab_index < len(pages):
            return pages[tab_index]
        # An out-of-range index is a model slip, not a reason to refuse to
        # navigate: this is Elaina's own isolated browser and any tab is a
        # valid place to point at a new URL. Found live -- the planner kept
        # guessing a stale index and every search came back "I couldn't
        # find that browser tab", burning its whole round budget.
        # describe_page/click_element still resolve strictly.
        preferred = self._preferred_page(pages)
        return preferred if preferred is not None else pages[-1]

    def _preferred_page(self, pages: list[Any]) -> Any | None:
        """The page Elaina herself most recently opened, if it is still open."""
        for page in pages:
            if page is self._preferred_page_ref:
                return page
        preferred = str(self._preferred_page_url or "").strip()
        if not preferred:
            return None
        matches = [page for page in pages if self._safe_url(page) == preferred]
        return matches[0] if len(matches) == 1 else None

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
        if tab_index is not None and 0 <= tab_index < len(pages):
            return pages[tab_index], tab_index
        # Either no index was given, or the one given does not exist. A
        # non-existent index is a model slip, not a different request, so
        # it resolves exactly like "no index": the tab Windows says is in
        # front, else the page Elaina herself opened. It never falls back
        # to an arbitrary tab -- if neither is decidable this still fails
        # closed, because reading or clicking the wrong page is the
        # dangerous case.
        #
        # Found live: the planner guessed a stale index, got a hard "I
        # couldn't determine the active browser tab", guessed again, and
        # burned its entire round budget in that loop without ever reading
        # the page it had just opened.
        active_index = self._active_tab_index(pages)
        return (
            (pages[active_index], active_index)
            if active_index is not None
            else (None, None)
        )


    def _transient_startup_page(self) -> Any | None:
        """The sole blank/new-tab surface while browser startup is in flight.

        It is deliberately not returned from ``_resolve_page_with_index``:
        callers may describe this state, but may never read or act on a
        transient tab as if it were user-requested content.
        """
        pages = [
            page
            for context in getattr(self._browser, "contexts", ())
            for page in getattr(context, "pages", ())
            if self._is_transient_startup_url(self._safe_url(page))
        ]
        return pages[0] if len(pages) == 1 else None

    @staticmethod
    def _is_transient_startup_url(url: str) -> bool:
        return str(url or "").casefold().startswith(
            _TRANSIENT_STARTUP_URL_PREFIXES,
        )

    def _page_is_loading(self, page: Any) -> bool:
        if self._is_transient_startup_url(self._safe_url(page)):
            return True
        try:
            state = page.evaluate(_PAGE_READY_STATE_SCRIPT)
        except Exception:
            return False
        return str(state or "").strip().casefold() == "loading"

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
            for index, page in enumerate(pages):
                if page is self._preferred_page_ref:
                    return index
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
    def _content_summary(
        page: Any,
    ) -> tuple[tuple[str, ...], str, bool, tuple[PageImage, ...], int]:
        """Read a bounded semantic snapshot without performing an action.

        A failed/unsupported evaluation is intentionally indistinguishable
        from an empty semantic snapshot. The interactive scan remains useful
        in that case, and the caller never fabricates text or image captions.
        """
        try:
            raw = page.evaluate(_CONTENT_SUMMARY_SCRIPT)
        except Exception:
            return (), "", False, (), 0
        if not isinstance(raw, dict) or raw.get("marker") != "__ELAINA_CONTENT_SUMMARY__":
            return (), "", False, (), 0

        def strings(value: Any, limit: int, item_limit: int) -> tuple[str, ...]:
            if not isinstance(value, (list, tuple)):
                return ()
            seen: set[str] = set()
            result: list[str] = []
            for item in value:
                text = " ".join(str(item or "").split()).strip()[:limit]
                key = text.casefold()
                if text and key not in seen:
                    seen.add(key)
                    result.append(text)
                    if len(result) >= item_limit:
                        break
            return tuple(result)

        headings = strings(raw.get("headings"), 180, _MAX_SNAPSHOT_HEADINGS)
        text = " ".join(str(raw.get("text", "") or "").split()).strip()
        text = text[:_MAX_SNAPSHOT_TEXT_LENGTH]
        try:
            text_length = max(0, int(raw.get("textLength", len(text))))
        except (TypeError, ValueError):
            text_length = len(text)
        images = tuple(
            PageImage(label=label)
            for label in strings(raw.get("images"), 160, _MAX_SNAPSHOT_IMAGES)
        )
        try:
            image_count = max(0, int(raw.get("imageCount", len(images))))
        except (TypeError, ValueError):
            image_count = len(images)
        return headings, text, text_length > len(text), images, image_count

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
