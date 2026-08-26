"""Verified webpage actions: click, fill, select, scroll, navigate (4C.2/4C.3).

This is the only module in the browser-control system that can change
anything on a page -- browser_observer.py stays strictly read-only. Every
target here is a real, live data-elaina-id assigned by browser_observer.py's
DOM scan moments earlier; nothing is ever acted on just because the model
claimed an id existed.

Three safety tiers, independent of any confirmation:
- An element whose accessible label suggests a committing action (submit,
  send, post, download, reserve, agree, change account settings, ...)
  needs a separate confirmation, the same gate already used for
  force-quit, delete, and ui_action elsewhere in the desktop control
  system.
- An element that would complete a payment (pay, buy, place order, ...) is
  refused outright -- not even confirmable. "Payments ... should remain
  user-only" is a harder line than "needs confirmation": a confirmation
  reply can be misheard or misclassified, but a flat refusal cannot.
- A field that looks like a credential or payment-detail field is refused
  outright for the same reason. The user enters those themselves.

Page content read back here (labels, values, hrefs) is untrusted data
about what the page displays, never an instruction. See
brain/browser_action_planner.py for how that boundary is enforced in the
tool-calling loop itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from tools.browser_control.browser_observer import (
    _LABEL_LOGIC_JS,
    BrowserObserver,
    spoken_label,
)
from tools.browser_control.safe_browser import SafeBrowserControl

_MAX_FILL_LENGTH = 500

# The exact same label logic describe_page()'s scan uses, evaluated for one
# already-located element -- so a committing-action check never sees a
# weaker, separately derived label than what the model was shown.
_ELEMENT_LABEL_SCRIPT = f"""
(el) => {{
{_LABEL_LOGIC_JS}
  return computeLabel(el);
}}
"""

# Reads the two page attributes a label alone can't reveal: the real link
# destination and whether the browser itself was told to treat this as a
# download (the HTML5 download attribute). Measured against real sites:
# a direct file link ("Report.pdf") often carries no "download" wording in
# its visible label at all.
_ELEMENT_DOWNLOAD_INFO_SCRIPT = """
(el) => ({
  // Match BrowserObserver's scan: resolve relative links before comparing
  // the just-observed fingerprint with the live element.
  href: el.href || el.getAttribute('href') || '',
  hasDownloadAttribute: el.hasAttribute('download'),
})
"""

# Cookie/privacy dismissal is intentionally a separate action from a normal
# click. BrowserObserver only marks a candidate; this live check repeats the
# narrow label + container test on the exact just-observed DOM node before a
# normal Playwright click is allowed. It never accepts consent, closes a
# login/promo surface, or uses JavaScript's click() escape hatch.
_PRIVACY_DISMISSAL_CHECK_SCRIPT = r"""
(el) => {
  const normalise = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const label = normalise(
    el.getAttribute('aria-label') || el.innerText || el.value || el.title || ''
  );
  const rejectOnly = /^(?:reject(?:\s+all)?|decline(?:\s+all)?|refuse(?:\s+all)?|only\s+(?:essential|necessary)(?:\s+cookies)?|(?:essential|necessary)\s+only|continue\s+without|모두\s*거부|거부|필수만|필수\s*쿠키만|동의하지\s*않(?:기|음)?)$/i;
  const never = /accept|agree|allow|consent|enable|subscribe|sign|login|log\s*in|register|buy|pay|order|submit|동의|허용|구독|가입|결제/i;
  const hint = /cookie|consent|gdpr|privacy|cmp|onetrust|didomi|quantcast|trustarc|sp_message/i;
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none' && style.opacity !== '0';
  };
  let container = null;
  let node = el;
  while (node && node !== document.body) {
    const style = window.getComputedStyle(node);
    const dialog = node.matches && node.matches(
      '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog[open]'
    );
    const clues = [node.id || '', String(node.className || ''),
      node.getAttribute && (node.getAttribute('aria-label') || ''),
      (node.innerText || '').slice(0, 800)].join(' ');
    if ((dialog || style.position === 'fixed' || style.position === 'sticky') &&
        visible(node) && hint.test(clues)) {
      container = node;
      break;
    }
    node = node.parentElement;
  }
  return { label, safe: !!container && rejectOnly.test(label) && !never.test(label) };
}
"""

_PRIVACY_DIALOG_PRESENT_SCRIPT = r"""
() => {
  const hint = /cookie|consent|gdpr|privacy|cmp|onetrust|didomi|quantcast|trustarc|sp_message/i;
  const visible = (node) => {
    const rect = node.getBoundingClientRect();
    const style = window.getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none' && style.opacity !== '0';
  };
  return Array.from(document.querySelectorAll(
    '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog[open], [id], [class]'
  )).some((node) => {
    const style = window.getComputedStyle(node);
    const dialog = node.matches && node.matches(
      '[role="dialog"], [role="alertdialog"], [aria-modal="true"], dialog[open]'
    );
    const fixed = style.position === 'fixed' || style.position === 'sticky';
    const clues = [node.id || '', String(node.className || ''),
      node.getAttribute('aria-label') || '', (node.innerText || '').slice(0, 800)].join(' ');
    return (dialog || fixed) && visible(node) && hint.test(clues);
  });
}
"""

_DOWNLOADABLE_FILE_EXTENSIONS = (
    ".pdf", ".zip", ".rar", ".7z", ".exe", ".msi", ".dmg", ".pkg",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
    ".apk", ".iso", ".tar", ".gz", ".bin", ".dat",
)

_COMMITTING_KEYWORDS = (
    "send", "submit", "post", "publish", "confirm",
    "delete", "remove", "discard", "accept", "agree", "allow",
    "install", "uninstall", "unsubscribe", "sign out", "log out",
    "deactivate", "book", "reserve", "reservation", "download", "apply",
    "subscribe", "donate", "sign up", "register",
    # Account-settings changes get the same pause-and-confirm treatment.
    "change password", "change email", "update payment", "update billing",
    "delete account", "close account", "change account",
    # Matched against real Korean-language pages, the same allowlist
    # pattern already proven for 4B's Windows UI committing-control check.
    "전송", "보내기", "제출", "게시", "확인",
    "삭제", "제거", "수락", "동의", "허용", "설치", "로그아웃", "비활성화",
    "예약", "다운로드", "신청", "구독", "가입", "비밀번호 변경", "계정 삭제",
)

# "Payments ... should remain user-only" -- a stricter line than the
# generic committing tier above. checkout/order alone are deliberately not
# here: "Go to checkout" is usually just navigation to a review page, not
# an actual charge, and stays confirmable via _COMMITTING_KEYWORDS's more
# specific "checkout now"/"place order" phrases below covering the real
# commit action.
# Word-boundary matching, because substring matching refused ordinary
# research links: "buyingguides" and "Buyer reviews" both contain "buy".
# The bare verbs are listed as whole words; brand and phrase forms that a
# whole-word verb would miss are spelled out alongside them. Korean has no
# word boundaries, so those stay substrings.
_PAYMENT_PATTERN = re.compile(
    # Whole words only. "payment"/"paypal" are named because the old
    # substring "pay" matched them and that coverage is deliberately kept;
    # bare "checkout" is deliberately absent, because reaching a checkout
    # page is navigation, not a charge.
    r"\b(?:buy|pay|payments?|purchase|purchases|paypal|payco)\b"
    r"|\bcheckout\s+now\b"
    # Real buttons put words in the middle -- "Place your order" was missed
    # entirely by the old exact-phrase entries.
    r"|\bplace\b[^.!?]{0,16}\border\b"
    r"|\bcomplete\b[^.!?]{0,16}\b(?:order|purchase)\b"
    r"|\bconfirm\b[^.!?]{0,16}\b(?:purchase|payment)\b"
    r"|\b(?:submit|process)\b[^.!?]{0,16}\bpayment\b"
    r"|결제|구매|주문\s*완료|구매\s*확정",
    re.IGNORECASE,
)

_CREDENTIAL_KEYWORDS = (
    "password", "passcode", "pin", "secret", "ssn", "social security",
    "credit card", "card number", "cvv", "cvc", "expiry", "expiration",
    "routing number", "account number", "iban", "swift",
    # "Payments ... should remain user-only" covers the whole payment/
    # billing flow, not just the card number itself.
    "billing", "payment method", "payment info",
    "비밀번호", "암호", "신용카드", "카드 번호", "결제 정보", "청구지",
)

_OUTBOUND_TEXT_KEYWORDS = (
    "message", "comment", "reply", "post", "chat", "feedback", "review",
    "question", "note", "description", "caption", "bio", "status",
    "메시지", "댓글", "답글", "게시", "채팅", "후기", "리뷰", "소개",
)

_SCROLLABLE_ROLES = frozenset({"a", "button", "div", "section", "li", "img"})

_PRIVACY_REJECT_ONLY = re.compile(
    r"^(?:reject(?:\s+all)?|decline(?:\s+all)?|refuse(?:\s+all)?|"
    r"only\s+(?:essential|necessary)(?:\s+cookies)?|"
    r"(?:essential|necessary)\s+only|continue\s+without|"
    r"모두\s*거부|거부|필수만|필수\s*쿠키만|동의하지\s*않(?:기|음)?)$",
    re.IGNORECASE,
)
_PRIVACY_NEVER = re.compile(
    r"accept|agree|allow|consent|enable|subscribe|sign|login|log\s*in|"
    r"register|buy|pay|order|submit|동의|허용|구독|가입|결제",
    re.IGNORECASE,
)


def is_committing_element(label: str) -> bool:
    """True if activating this element is a consequential, not-undoable step."""
    lowered = label.casefold()
    return any(keyword in lowered for keyword in _COMMITTING_KEYWORDS)


def is_payment_element(label: str) -> bool:
    """True if activating this element would complete a purchase or charge.

    Refused outright, never confirmable -- payments stay user-only.
    """
    return bool(_PAYMENT_PATTERN.search(str(label or "")))


def is_download_link(label: str, href: str, has_download_attribute: bool) -> bool:
    """True if activating this element would start a file download.

    Checked independently of the label: a direct file link often carries
    no "download" wording at all.
    """
    if has_download_attribute:
        return True
    if is_committing_element(label) and "download" in label.casefold():
        return True
    lowered_href = str(href).casefold().split("?")[0].split("#")[0]
    return lowered_href.endswith(_DOWNLOADABLE_FILE_EXTENSIONS)


def is_credential_field(label: str, element_type: str) -> bool:
    """True if filling this field would mean entering a credential/payment
    detail for the user -- refused outright, never confirmable."""
    if element_type.casefold() == "password":
        return True
    lowered = label.casefold()
    return any(keyword in lowered for keyword in _CREDENTIAL_KEYWORDS)


def is_outbound_text_field(label: str, element_type: str) -> bool:
    """Whether filling this field would paste text for another party/site.

    Search, filter, and ordinary preference fields remain immediate because
    the user directly asked to operate them.  A message/comment/review field
    can expose private or unintended text externally once a later Send button
    is clicked, so we pause before inserting it at all.
    """
    if element_type.casefold() in {"search"}:
        return False
    lowered = label.casefold()
    return any(keyword in lowered for keyword in _OUTBOUND_TEXT_KEYWORDS)


def is_safe_privacy_rejection(label: str) -> bool:
    """Whether a *label alone* can be the reject-only half of CMP safety.

    The caller must additionally prove the element lives inside a visible
    privacy container. Keeping this pure helper public makes the two halves
    explicit in tests and prevents a future generic-click path from treating
    a bare ``Reject`` label as automatic authority.
    """
    normalised = " ".join(str(label or "").split()).strip()
    return bool(
        normalised
        and _PRIVACY_REJECT_ONLY.fullmatch(normalised)
        and not _PRIVACY_NEVER.search(normalised)
    )


# Post-click evidence is opportunistic: a probe that cannot answer quickly
# has nothing useful to say, and "no readable postcondition" is already a
# supported result. Anything longer is pure waiting on a detached element.
_PROBE_TIMEOUT_MS = 1000


def _bounded(probe: Callable[..., Any], *args: Any) -> Any:
    """Run a Playwright probe with a short timeout.

    Falls back to the bare call for injected doubles whose signatures do
    not accept ``timeout``.
    """
    try:
        return probe(*args, timeout=_PROBE_TIMEOUT_MS)
    except TypeError:
        return probe(*args)


def _short_error(error: Exception) -> str:
    """One line of a Playwright error, never its whole call log."""
    return str(error).splitlines()[0].strip()[:160]


def _is_actionability_timeout(error: Exception) -> bool:
    """Whether Playwright gave up waiting for an element to be clickable."""
    text = str(error).casefold()
    return "timeout" in text and (
        "locator.click" in text
        or "locator.fill" in text
        or "waiting for" in text
    )


@dataclass(frozen=True)
class BrowserActionResult:
    status: str
    message: str
    element_id: str = ""
    element_label: str = ""
    url: str = ""
    # True means a readable postcondition proved the action, False means a
    # readable postcondition contradicted it, and None means the page did
    # not expose enough state to verify independently.
    verified: bool | None = None
    evidence: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {
            "clicked", "filled", "selected", "scrolled", "navigated",
            "dismissed_privacy_overlay",
        }


class BrowserControl:
    """Click, fill, select, scroll, and navigate -- only on verified elements."""

    def __init__(self, *, observer: BrowserObserver | None = None) -> None:
        self.observer = observer or BrowserObserver()

    @property
    def available(self) -> bool:
        return self.observer._ensure_connected().status == "connected"

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
        page, locator, label, element_type, error = self._resolve_element(
            tab_index, element_id, expected_label, expected_url,
            expected_scan_id, expected_href,
        )
        if error is not None:
            return error

        if is_payment_element(label):
            return BrowserActionResult(
                "refused",
                f"{label!r} looks like it completes a payment -- please do that yourself.",
                element_id=element_id, element_label=label,
            )

        before_url = page.url
        if not confirmed:
            href, has_download_attribute = self._read_download_info(locator)
            if is_download_link(label, href, has_download_attribute):
                return BrowserActionResult(
                    "confirmation_required",
                    "Downloading "
                    f"{spoken_label(label)!r} needs confirmation first.",
                    element_id=element_id, element_label=label, url=before_url,
                )
            if is_committing_element(label):
                return BrowserActionResult(
                    "confirmation_required",
                    "Clicking "
                    f"{spoken_label(label)!r} needs confirmation first.",
                    element_id=element_id, element_label=label, url=before_url,
                )

        before_state = self._interactive_state(locator)
        before_page_count = self._page_count(page)
        try:
            locator.click(timeout=5000)
        except Exception as error:
            # Playwright waits for actionability and then reports a
            # multi-line call log. That trace is addressed to a developer,
            # not to the user, and "it's on the page but can't be clicked"
            # is a recoverable miss the planner should re-scan from -- not
            # a hard stop. Found live clicking a skip link, which every
            # accessible site parks off-screen as its first focusable
            # element.
            if _is_actionability_timeout(error):
                return BrowserActionResult(
                    "not_found",
                    f"{spoken_label(label)!r} is on the page but isn't "
                    "clickable right now (it may be hidden, covered, or "
                    "off-screen). Re-scan and choose a different element.",
                    element_id=element_id, element_label=label,
                )
            return BrowserActionResult(
                "failed",
                f"I couldn't click {spoken_label(label)!r}: "
                f"{_short_error(error)}",
                element_id=element_id, element_label=label,
            )
        verified, evidence = self._verify_click(
            page, locator, before_url, before_state, before_page_count,
        )
        if str(page.url) != str(before_url) and hasattr(self.observer, "prefer_page"):
            self.observer.prefer_page(str(page.url))
        return BrowserActionResult(
            "clicked", f"Clicked {spoken_label(label)}.",
            element_id=element_id, element_label=label, url=page.url,
            verified=verified, evidence=evidence,
        )

    def dismiss_privacy_overlay(
        self,
        tab_index: int | None,
        element_id: str,
        *,
        expected_label: str = "",
        expected_url: str = "",
        expected_scan_id: str = "",
        expected_href: str = "",
    ) -> BrowserActionResult:
        """Reject optional privacy consent through the ordinary click path.

        This method deliberately accepts only exact reject/essential-only
        controls inside a currently-visible privacy container. It never
        clicks a generic close button, accepts terms, or works around a
        blocked action with JavaScript. A successful click is reported only
        after the privacy surface is no longer observable.
        """
        page, locator, label, _element_type, error = self._resolve_element(
            tab_index, element_id, expected_label, expected_url,
            expected_scan_id, expected_href,
        )
        if error is not None:
            return error
        if not is_safe_privacy_rejection(label):
            return BrowserActionResult(
                "refused",
                f"{spoken_label(label)!r} is not a safe privacy-rejection control.",
                element_id=element_id,
                element_label=label,
                url=str(getattr(page, "url", "") or ""),
            )
        try:
            safety = locator.evaluate(_PRIVACY_DISMISSAL_CHECK_SCRIPT)
        except Exception as exc:
            return BrowserActionResult(
                "refused",
                "I couldn't verify that this control belongs to a privacy dialog, so I left it alone.",
                element_id=element_id,
                element_label=label,
                url=str(getattr(page, "url", "") or ""),
                evidence=_short_error(exc),
            )
        if not isinstance(safety, dict) or not bool(safety.get("safe")):
            return BrowserActionResult(
                "refused",
                "That reject control is not inside a verified privacy dialog, so I left it alone.",
                element_id=element_id,
                element_label=label,
                url=str(getattr(page, "url", "") or ""),
            )
        try:
            # This is intentionally a normal Playwright locator click:
            # actionability checks protect against a covered, moving, or
            # disabled target and no force/coordinate fallback is permitted.
            locator.click(timeout=5000)
        except Exception as exc:
            if _is_actionability_timeout(exc):
                return BrowserActionResult(
                    "not_found",
                    f"{spoken_label(label)!r} is no longer clickable; I stopped before guessing.",
                    element_id=element_id,
                    element_label=label,
                    url=str(getattr(page, "url", "") or ""),
                )
            return BrowserActionResult(
                "failed",
                f"I couldn't dismiss {spoken_label(label)!r}: {_short_error(exc)}",
                element_id=element_id,
                element_label=label,
                url=str(getattr(page, "url", "") or ""),
            )
        try:
            still_present = bool(page.evaluate(_PRIVACY_DIALOG_PRESENT_SCRIPT))
        except Exception as exc:
            return BrowserActionResult(
                "verification_failed",
                f"I clicked {spoken_label(label)!r}, but couldn't verify that the privacy dialog closed.",
                element_id=element_id,
                element_label=label,
                url=str(getattr(page, "url", "") or ""),
                verified=False,
                evidence=_short_error(exc),
            )
        if still_present:
            return BrowserActionResult(
                "verification_failed",
                f"I clicked {spoken_label(label)!r}, but the privacy dialog is still visible.",
                element_id=element_id,
                element_label=label,
                url=str(getattr(page, "url", "") or ""),
                verified=False,
                evidence="A visible cookie/privacy dialog remained after the click.",
            )
        return BrowserActionResult(
            "dismissed_privacy_overlay",
            f"Rejected optional privacy choices with {spoken_label(label)}.",
            element_id=element_id,
            element_label=label,
            url=str(getattr(page, "url", "") or ""),
            verified=True,
            evidence="The verified privacy dialog was no longer visible after the click.",
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
        page, locator, label, element_type, error = self._resolve_element(
            tab_index, element_id, expected_label, expected_url,
            expected_scan_id, expected_href,
        )
        if error is not None:
            return error

        if is_credential_field(label, element_type):
            return BrowserActionResult(
                "refused",
                f"{label!r} looks like a credential field -- please enter that yourself.",
                element_id=element_id, element_label=label,
            )

        if is_outbound_text_field(label, element_type) and not confirmed:
            return BrowserActionResult(
                "confirmation_required",
                f"Pasting into {label!r} needs confirmation first.",
                element_id=element_id,
                element_label=label,
                url=page.url,
            )

        text = str(text)[:_MAX_FILL_LENGTH]
        try:
            locator.fill(text, timeout=5000)
        except Exception as error:
            return BrowserActionResult(
                "failed", f"I couldn't fill {label!r}: {error}",
                element_id=element_id, element_label=label,
            )
        verified, evidence = self._verify_filled(locator, text)
        if verified is False:
            return BrowserActionResult(
                "verification_failed",
                f"Typing was sent to {label}, but the field did not report the requested text.",
                element_id=element_id, element_label=label, url=page.url,
                verified=False, evidence=evidence,
            )
        return BrowserActionResult(
            "filled", f"Filled {label}.",
            element_id=element_id, element_label=label, url=page.url,
            verified=verified, evidence=evidence,
        )

    def select_option(
        self,
        tab_index: int | None,
        element_id: str,
        option: str,
        *,
        expected_label: str = "",
        expected_url: str = "",
        expected_scan_id: str = "",
        expected_href: str = "",
    ) -> BrowserActionResult:
        page, locator, label, element_type, error = self._resolve_element(
            tab_index, element_id, expected_label, expected_url,
            expected_scan_id, expected_href,
        )
        if error is not None:
            return error

        try:
            locator.select_option(label=option, timeout=5000)
        except Exception:
            try:
                locator.select_option(value=option, timeout=5000)
            except Exception as error:
                return BrowserActionResult(
                    "failed",
                    f"I couldn't select {option!r} in {label!r}: {error}",
                    element_id=element_id, element_label=label,
                )
        verified, evidence = self._verify_selected(locator, option)
        if verified is False:
            return BrowserActionResult(
                "verification_failed",
                f"{label} did not report {option!r} as selected.",
                element_id=element_id, element_label=label, url=page.url,
                verified=False, evidence=evidence,
            )
        return BrowserActionResult(
            "selected", f"Selected {option} in {label}.",
            element_id=element_id, element_label=label, url=page.url,
            verified=verified, evidence=evidence,
        )

    def scroll_to(
        self,
        tab_index: int | None,
        element_id: str,
        *,
        expected_label: str = "",
        expected_url: str = "",
        expected_scan_id: str = "",
        expected_href: str = "",
    ) -> BrowserActionResult:
        page, locator, label, element_type, error = self._resolve_element(
            tab_index, element_id, expected_label, expected_url,
            expected_scan_id, expected_href,
        )
        if error is not None:
            return error

        try:
            locator.scroll_into_view_if_needed(timeout=5000)
        except Exception as error:
            return BrowserActionResult(
                "failed", f"I couldn't scroll to {label!r}: {error}",
                element_id=element_id, element_label=label,
            )
        verified, evidence = self._verify_in_view(locator)
        if verified is False:
            return BrowserActionResult(
                "verification_failed",
                f"I scrolled, but {label} still isn't visible.",
                element_id=element_id, element_label=label, url=page.url,
                verified=False, evidence=evidence,
            )
        return BrowserActionResult(
            "scrolled", f"Scrolled to {label}.",
            element_id=element_id, element_label=label, url=page.url,
            verified=verified, evidence=evidence,
        )

    def navigate(
        self, tab_index: int | None, url: str, *, allow_isolated_launch: bool = False,
    ) -> BrowserActionResult:
        # This defensive validation (blocks file:, localhost, and
        # private-network destinations) protects direct callers even though
        # BrowserActionPlanner's own "search"/"open_url" tools already only
        # ever pass a goal-grounded address here, never raw page content.
        resolution = SafeBrowserControl().resolve(str(url), str(url))
        if resolution.status != "resolved":
            return BrowserActionResult(
                "refused", resolution.message or "That address is not allowed.",
            )
        return self._goto(
            tab_index, resolution.url,
            success_message=f"Opened {{url}}.",
            failure_message=f"I couldn't open {url!r}: {{error}}",
            evidence="The page's real URL is the requested address after navigation.",
            allow_isolated_launch=allow_isolated_launch,
        )

    def search(
        self, tab_index: int | None, query: str, *, allow_isolated_launch: bool = False,
    ) -> BrowserActionResult:
        # The domain is fixed by local configuration (SafeBrowserControl's
        # search_url_template) -- only the query text, always percent-
        # encoded, comes from the model, so this can never be turned into
        # navigation to an arbitrary model- or page-suggested destination.
        resolution = SafeBrowserControl().resolve_search(str(query))
        if resolution.status != "resolved":
            return BrowserActionResult(
                "refused", resolution.message or "That search could not be started.",
            )
        return self._goto(
            tab_index, resolution.url,
            success_message=f"Searched for {query!r}.",
            failure_message=f"I couldn't search for {query!r}: {{error}}",
            evidence="The page's real URL is the search results address.",
            allow_isolated_launch=allow_isolated_launch,
        )

    def _goto(
        self,
        tab_index: int | None,
        url: str,
        *,
        success_message: str,
        failure_message: str,
        evidence: str,
        allow_isolated_launch: bool = False,
    ) -> BrowserActionResult:
        result = self.observer._ensure_connected(
            allow_isolated_launch=allow_isolated_launch,
        )
        if result.status != "connected":
            return BrowserActionResult("unavailable", result.message)
        # Unlike click/fill/describe, navigation must be able to target a
        # blank new-tab page -- that's the ordinary starting point of a
        # session, not an edge case.
        page = self.observer.resolve_navigable_page(tab_index)
        if page is None:
            return BrowserActionResult("not_found", "I couldn't find that browser tab.")
        still_loading = False
        try:
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
        except Exception as error:
            # A timeout does not mean the navigation failed -- heavy pages
            # routinely commit (URL changed, DOM building) while some
            # long-poll request keeps the load state pending. Found live as
            # "the browser opened but Elaina said she couldn't": treat a
            # commit to the right destination as success and read whatever
            # the page has, rather than reporting a page the user can SEE
            # as unreachable.
            if self._navigation_committed(page, url):
                still_loading = True
            else:
                # A hard failure (DNS, refused, interstitial) gets one retry
                # -- transient resolution hiccups on a fresh profile are
                # common right after a cold browser launch. Re-check the
                # real URL after that retry too: navigation can commit during
                # its final wait, and must not be reported as a failure.
                try:
                    # Shorter than the first attempt: this exists for a
                    # transient miss (a fresh profile's first DNS lookup),
                    # not to wait out a site that is refusing us. A full
                    # second 15s made a blocked site cost 30 seconds of
                    # silence before the user heard anything.
                    page.goto(url, timeout=8000, wait_until="domcontentloaded")
                except Exception:
                    if self._navigation_committed(page, url):
                        still_loading = True
                    else:
                        return BrowserActionResult(
                            "failed",
                            failure_message.format(error=error),
                            url=page.url,
                        )
        if hasattr(self.observer, "prefer_page"):
            self.observer.prefer_page(str(page.url))
        try:
            page.bring_to_front()
        except Exception:
            # Cosmetic only -- surfacing the window so a step doesn't happen
            # silently behind whatever the user is doing must never turn a
            # real, completed navigation into a reported failure.
            pass
        return BrowserActionResult(
            "navigated",
            success_message.format(url=page.url)
            + (" The page is still loading parts of itself." if still_loading else ""),
            url=page.url,
            verified=True,
            evidence=evidence,
        )

    @staticmethod
    def _navigation_committed(page: Any, requested_url: str) -> bool:
        """Whether the page actually reached the requested destination.

        Compares hosts only: redirects (http->https, m.-prefixes, consent
        interstitials on the same site) are still the navigation the caller
        asked for. A page still sitting on about:blank or the previous
        site's host is a real failure.
        """
        try:
            actual = urlsplit(str(getattr(page, "url", "") or ""))
            requested = urlsplit(str(requested_url))
        except ValueError:
            return False
        actual_host = (actual.hostname or "").casefold().removeprefix("www.")
        requested_host = (
            (requested.hostname or "").casefold().removeprefix("www.")
        )
        if not actual_host or not requested_host:
            return False
        return (
            actual_host == requested_host
            or actual_host.endswith("." + requested_host)
            or requested_host.endswith("." + actual_host)
        )

    def _resolve_element(
        self,
        tab_index: int | None,
        element_id: str,
        expected_label: str,
        expected_url: str = "",
        expected_scan_id: str = "",
        expected_href: str = "",
    ) -> tuple[Any, Any, str, str, BrowserActionResult | None]:
        result = self.observer._ensure_connected()
        if result.status != "connected":
            return None, None, "", "", BrowserActionResult("unavailable", result.message)
        page = self.observer._resolve_page(tab_index)
        if page is None:
            return None, None, "", "", BrowserActionResult(
                "not_found", "I couldn't find that browser tab.",
            )

        if expected_url and str(page.url) != str(expected_url):
            return None, None, "", "", BrowserActionResult(
                "stale",
                "That page changed before I could act, so I stopped instead of "
                "using the old confirmation.",
                element_id=element_id,
            )

        selector = f'[data-elaina-id="{element_id}"]'
        if expected_scan_id:
            selector += f'[data-elaina-scan="{expected_scan_id}"]'
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception as error:
            return None, None, "", "", BrowserActionResult(
                "error", f"I couldn't inspect that page: {error}", element_id=element_id,
            )
        if count == 0:
            return None, None, "", "", BrowserActionResult(
                "not_found",
                f"I couldn't find element {element_id!r} on this page. "
                "It may have changed since it was last observed -- "
                "describe the page again before acting.",
                element_id=element_id,
            )
        if count > 1:
            return None, None, "", "", BrowserActionResult(
                "ambiguous",
                f"More than one element matches {element_id!r}.",
                element_id=element_id,
            )

        try:
            label = str(locator.evaluate(_ELEMENT_LABEL_SCRIPT) or "").strip()
        except Exception:
            label = ""
        try:
            element_type = str(locator.get_attribute("type") or "")
        except Exception:
            element_type = ""
        label = label or expected_label or element_id
        if expected_label and self._normalise_label(label) != self._normalise_label(expected_label):
            return None, None, "", "", BrowserActionResult(
                "stale",
                "That element changed before I could act, so I stopped instead "
                "of using the old target.",
                element_id=element_id,
                element_label=label,
                url=str(page.url),
            )
        if expected_href:
            live_href, _ = self._read_download_info(locator)
            if live_href != expected_href:
                return None, None, "", "", BrowserActionResult(
                    "stale",
                    "That link changed before I could act, so I stopped instead "
                    "of using the old target.",
                    element_id=element_id,
                    element_label=label,
                    url=str(page.url),
                )
        return page, locator, label, element_type, None

    @staticmethod
    def _read_download_info(locator: Any) -> tuple[str, bool]:
        try:
            info = locator.evaluate(_ELEMENT_DOWNLOAD_INFO_SCRIPT) or {}
        except Exception:
            return "", False
        return str(info.get("href", "")), bool(info.get("hasDownloadAttribute", False))

    @staticmethod
    def _verify_click(
        page: Any,
        locator: Any,
        before_url: str,
        before_state: tuple[str, ...],
        before_page_count: int | None,
    ) -> tuple[bool | None, str]:
        try:
            if page.url != before_url:
                return True, "The page navigated to a new URL after the click."
        except Exception:
            pass
        try:
            # Bounded: after a click that navigated, this element is
            # detached and an unbounded probe waits the full default
            # actionability timeout before raising.
            checked = _bounded(locator.is_checked)
            return True, f"The control reports checked={checked}."
        except Exception:
            pass
        after_state = BrowserControl._interactive_state(locator)
        if before_state and after_state and before_state != after_state:
            return True, "The control's exposed state changed after the click."
        after_page_count = BrowserControl._page_count(page)
        if (
            before_page_count is not None
            and after_page_count is not None
            and after_page_count > before_page_count
        ):
            return True, "The click opened a new browser tab."
        return None, "The click completed, but the page exposes no changed state."

    @staticmethod
    def _interactive_state(locator: Any) -> tuple[str, ...]:
        values = []
        for attribute in ("aria-pressed", "aria-selected", "aria-expanded", "value"):
            try:
                # Four unbounded probes against a detached element were
                # two minutes of the 150s a navigating click used to cost.
                value = _bounded(locator.get_attribute, attribute)
            except Exception:
                value = None
            values.append("" if value is None else str(value))
        return tuple(values)

    @staticmethod
    def _page_count(page: Any) -> int | None:
        try:
            context = getattr(page, "context", None)
            pages = getattr(context, "pages", None)
            return len(pages) if pages is not None else None
        except Exception:
            return None

    @staticmethod
    def _normalise_label(value: str) -> str:
        return " ".join(str(value).casefold().split())

    @staticmethod
    def _verify_filled(locator: Any, expected: str) -> tuple[bool | None, str]:
        try:
            actual = str(locator.input_value())
        except Exception:
            return None, "The field exposes no readable value after filling."
        if expected and expected in actual:
            return True, "The field's value contains the requested text."
        if not expected:
            return True, "The field was cleared as requested."
        return False, "The field's value did not contain the requested text."

    @staticmethod
    def _verify_selected(locator: Any, expected: str) -> tuple[bool | None, str]:
        try:
            actual = locator.input_value()
        except Exception:
            return None, "The control exposes no readable selected value."
        if expected.casefold() in str(actual).casefold():
            return True, "The control reports the requested option as selected."
        return False, "The control reports a different selected value."

    @staticmethod
    def _verify_in_view(locator: Any) -> tuple[bool | None, str]:
        try:
            box = locator.bounding_box()
        except Exception:
            return None, "The element exposes no readable position."
        if box is None:
            return None, "The element exposes no readable position."
        return True, "The element's bounding box is available after scrolling."
