import unittest

from tools.browser_control.browser_connection import BrowserConnectionResult
from tools.browser_control.browser_control import (
    BrowserControl,
    is_committing_element,
    is_credential_field,
    is_download_link,
    is_outbound_text_field,
    is_payment_element,
)


class _FakeLocator:
    def __init__(
        self,
        *,
        label="",
        element_type="",
        count=1,
        value="",
        checked=None,
        bounding_box=(0, 0, 10, 10),
        href="",
        has_download_attribute=False,
    ):
        self.label = label
        self.element_type = element_type
        self._count = count
        self._value = value
        self._checked = checked
        self._bounding_box = bounding_box
        self.href = href
        self.has_download_attribute = has_download_attribute
        self.clicked = False
        self.filled_text = None
        self.selected = None
        self.scrolled_into_view = False
        self.click_raises = False
        self.fill_raises = False

    def count(self):
        return self._count

    def evaluate(self, script):
        # Check the most specific/unique marker first -- distinguishes the
        # label-computation script from the download-info script.
        if "hasDownloadAttribute" in script:
            return {
                "href": self.href,
                "hasDownloadAttribute": self.has_download_attribute,
            }
        return self.label

    def get_attribute(self, name):
        return self.element_type if name == "type" else None

    def click(self, timeout=None):
        if self.click_raises:
            raise RuntimeError("click failed")
        self.clicked = True

    def fill(self, text, timeout=None):
        if self.fill_raises:
            raise RuntimeError("fill failed")
        self.filled_text = text
        self._value = text

    def input_value(self):
        return self._value

    def select_option(self, label=None, value=None, timeout=None):
        self.selected = label or value
        self._value = self.selected

    def is_checked(self):
        if self._checked is None:
            raise RuntimeError("no checked state")
        return self._checked

    def scroll_into_view_if_needed(self, timeout=None):
        self.scrolled_into_view = True

    def bounding_box(self):
        return self._bounding_box


class _FakePage:
    def __init__(self, *, url="https://example.com", locators=None):
        self.url = url
        self._locators = locators or {}
        self.goto_calls = []
        self.brought_to_front = False

    def locator(self, selector):
        element_id = selector.split('"')[1]
        return self._locators.get(element_id, _FakeLocator(count=0))

    def goto(self, url, timeout=None, wait_until=None):
        self.goto_calls.append(url)
        self.url = url

    def bring_to_front(self):
        self.brought_to_front = True


class _FakeObserver:
    def __init__(self, page, *, connected=True):
        self._page = page
        self._connected = connected

    def _ensure_connected(self):
        if self._connected:
            return BrowserConnectionResult("connected", browser=object(), playwright=object())
        return BrowserConnectionResult("unavailable", "Browser control isn't installed.")

    def _resolve_page(self, tab_index):
        return self._page

    def resolve_navigable_page(self, tab_index):
        return self._page


class ClassifierTests(unittest.TestCase):
    def test_committing_keywords_are_detected(self):
        for label in ("Submit", "Confirm reservation", "Send message", "Download report"):
            self.assertTrue(is_committing_element(label), label)

    def test_payment_words_are_not_merely_committing(self):
        # Payment completion is a stricter tier than "needs confirmation" --
        # it must never be reachable through is_committing_element's
        # confirm-and-proceed path.
        for label in ("Pay now", "Buy now", "Place order"):
            self.assertFalse(is_committing_element(label), label)

    def test_ordinary_navigation_is_not_committing(self):
        for label in ("Home", "Next", "Search", "Learn more"):
            self.assertFalse(is_committing_element(label), label)

    def test_korean_committing_keywords_are_detected(self):
        for label in ("예약하기", "구독"):
            self.assertTrue(is_committing_element(label), label)

    def test_korean_payment_words_are_not_merely_committing(self):
        for label in ("결제", "구매", "결제하기"):
            self.assertFalse(is_committing_element(label), label)

    def test_payment_keywords_are_detected(self):
        for label in ("Pay", "Buy now", "Place order", "Confirm payment", "결제하기", "구매"):
            self.assertTrue(is_payment_element(label), label)

    def test_ordinary_checkout_navigation_is_not_a_payment_element(self):
        # "Go to checkout" is usually navigation to a review page, not an
        # actual charge -- only the real commit action is hard-blocked.
        for label in ("Checkout", "View cart", "Continue"):
            self.assertFalse(is_payment_element(label), label)

    def test_download_link_detected_by_label(self):
        self.assertTrue(is_download_link("Download report", "", False))

    def test_download_link_detected_by_download_attribute(self):
        self.assertTrue(is_download_link("Report", "/files/report", True))

    def test_download_link_detected_by_file_extension(self):
        for href in ("/files/report.pdf", "/archive.zip", "/setup.exe"):
            self.assertTrue(is_download_link("Report", href, False), href)

    def test_ordinary_link_is_not_a_download(self):
        self.assertFalse(is_download_link("Learn more", "/about", False))

    def test_password_type_is_always_a_credential_field(self):
        self.assertTrue(is_credential_field("Enter your info", "password"))

    def test_credential_looking_labels_are_detected(self):
        for label in ("Credit card number", "CVV", "Social Security Number"):
            self.assertTrue(is_credential_field(label, "text"), label)

    def test_ordinary_fields_are_not_credential_fields(self):
        for label in ("Search", "Email", "Message"):
            self.assertFalse(is_credential_field(label, "text"), label)

    def test_outbound_text_fields_are_distinguished_from_search(self):
        self.assertTrue(is_outbound_text_field("Message", "text"))
        self.assertTrue(is_outbound_text_field("Reply", "textarea"))
        self.assertFalse(is_outbound_text_field("Search", "search"))


class ClickTests(unittest.TestCase):
    def test_click_ordinary_element_executes_immediately(self):
        locator = _FakeLocator(label="Learn more")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0")

        self.assertEqual(result.status, "clicked")
        self.assertTrue(locator.clicked)

    def test_click_committing_element_requires_confirmation_first(self):
        locator = _FakeLocator(label="Submit Order")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0")

        self.assertEqual(result.status, "confirmation_required")
        self.assertFalse(locator.clicked)

    def test_click_committing_element_executes_once_confirmed(self):
        locator = _FakeLocator(label="Submit Order")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0", confirmed=True)

        self.assertEqual(result.status, "clicked")
        self.assertTrue(locator.clicked)

    def test_click_payment_element_is_refused_outright(self):
        locator = _FakeLocator(label="Pay now")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0")

        self.assertEqual(result.status, "refused")
        self.assertFalse(locator.clicked)

    def test_click_payment_element_stays_refused_even_when_confirmed(self):
        # "Payments ... should remain user-only" is a harder line than
        # "needs confirmation" -- confirmed=True must never bypass it.
        locator = _FakeLocator(label="Buy now")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0", confirmed=True)

        self.assertEqual(result.status, "refused")
        self.assertFalse(locator.clicked)

    def test_click_download_link_requires_confirmation_first(self):
        locator = _FakeLocator(label="Annual Report", href="/files/report.pdf")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0")

        self.assertEqual(result.status, "confirmation_required")
        self.assertFalse(locator.clicked)

    def test_click_download_link_executes_once_confirmed(self):
        locator = _FakeLocator(label="Annual Report", href="/files/report.pdf")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0", confirmed=True)

        self.assertEqual(result.status, "clicked")
        self.assertTrue(locator.clicked)

    def test_click_verifies_via_url_change(self):
        locator = _FakeLocator(label="Next page")

        class NavigatingPage(_FakePage):
            def locator(self, selector):
                real = super().locator(selector)
                return real

        page = NavigatingPage(locators={"e0": locator})
        original_click = locator.click

        def click_and_navigate(timeout=None):
            original_click(timeout=timeout)
            page.url = "https://example.com/page2"

        locator.click = click_and_navigate
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0")

        self.assertTrue(result.verified)

    def test_click_not_found_when_element_id_is_stale(self):
        page = _FakePage(locators={})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e99")

        self.assertEqual(result.status, "not_found")

    def test_click_ambiguous_when_multiple_elements_match(self):
        locator = _FakeLocator(label="Dup", count=2)
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(0, "e0")

        self.assertEqual(result.status, "ambiguous")

    def test_click_reports_connection_failure(self):
        page = _FakePage()
        control = BrowserControl(observer=_FakeObserver(page, connected=False))

        result = control.click(0, "e0")

        self.assertEqual(result.status, "unavailable")

    def test_click_refuses_a_stale_page_or_scan_before_dispatching(self):
        locator = _FakeLocator(label="Images")
        page = _FakePage(url="https://google.example/other", locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.click(
            0, "e0", expected_label="Images",
            expected_url="https://google.example/search",
            expected_scan_id="fresh-scan",
        )

        self.assertEqual(result.status, "stale")
        self.assertFalse(locator.clicked)


class FillTests(unittest.TestCase):
    def test_fill_ordinary_field_succeeds_and_verifies(self):
        locator = _FakeLocator(label="Search Wikipedia", element_type="search")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.fill(0, "e0", "Laufey")

        self.assertEqual(result.status, "filled")
        self.assertEqual(locator.filled_text, "Laufey")
        self.assertTrue(result.verified)

    def test_fill_refuses_password_type_fields(self):
        locator = _FakeLocator(label="Enter your info", element_type="password")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.fill(0, "e0", "hunter2")

        self.assertEqual(result.status, "refused")
        self.assertIsNone(locator.filled_text)

    def test_fill_refuses_credential_looking_labels(self):
        locator = _FakeLocator(label="Credit card number", element_type="text")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.fill(0, "e0", "4111111111111111")

        self.assertEqual(result.status, "refused")
        self.assertIsNone(locator.filled_text)

    def test_fill_reports_failed_verification(self):
        locator = _FakeLocator(label="Search", element_type="search")
        locator.fill = lambda text, timeout=None: setattr(locator, "_value", "wrong value")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.fill(0, "e0", "Laufey")

        self.assertEqual(result.status, "verification_failed")

    def test_fill_message_field_requires_confirmation_before_pasting(self):
        locator = _FakeLocator(label="Message", element_type="text")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        pending = control.fill(0, "e0", "hello")
        self.assertEqual(pending.status, "confirmation_required")
        self.assertIsNone(locator.filled_text)

        confirmed = control.fill(0, "e0", "hello", confirmed=True)

        self.assertEqual(confirmed.status, "filled")
        self.assertEqual(locator.filled_text, "hello")


class SelectScrollNavigateTests(unittest.TestCase):
    def test_select_option_success(self):
        locator = _FakeLocator(label="Sort by")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.select_option(0, "e0", "Price: low to high")

        self.assertEqual(result.status, "selected")
        self.assertEqual(locator.selected, "Price: low to high")

    def test_scroll_to_success(self):
        locator = _FakeLocator(label="Reviews section")
        page = _FakePage(locators={"e0": locator})
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.scroll_to(0, "e0")

        self.assertEqual(result.status, "scrolled")
        self.assertTrue(locator.scrolled_into_view)

    def test_navigate_success(self):
        page = _FakePage(url="https://example.com")
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.navigate(0, "https://hotels.example/search")

        self.assertEqual(result.status, "navigated")
        self.assertEqual(page.goto_calls, ["https://hotels.example/search"])
        # A step happening silently behind whatever the user is doing is
        # exactly the confusion this surfaces the window against.
        self.assertTrue(page.brought_to_front)

    def test_navigate_reports_failure(self):
        page = _FakePage()

        def raising_goto(url, timeout=None, wait_until=None):
            raise RuntimeError("DNS failure")

        page.goto = raising_goto
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.navigate(0, "https://nonexistent.invalid")

        self.assertEqual(result.status, "failed")

    def test_navigate_refuses_a_private_network_url(self):
        page = _FakePage()
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.navigate(0, "http://127.0.0.1:8080/admin")

        self.assertEqual(result.status, "refused")
        self.assertEqual(page.goto_calls, [])

    def test_search_success(self):
        page = _FakePage(url="https://example.com")
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.search(0, "hotels in Guam")

        self.assertEqual(result.status, "navigated")
        self.assertEqual(
            page.goto_calls,
            ["https://www.google.com/search?q=hotels+in+Guam"],
        )
        self.assertTrue(page.brought_to_front)

    def test_navigate_success_even_if_bring_to_front_is_unsupported(self):
        # Some pages/embedders may not expose bring_to_front -- that must
        # stay cosmetic-only and never turn a real, completed navigation
        # into a reported failure.
        page = _FakePage(url="https://example.com")

        def raising_bring_to_front():
            raise RuntimeError("not supported")

        page.bring_to_front = raising_bring_to_front
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.navigate(0, "https://hotels.example/search")

        self.assertEqual(result.status, "navigated")

    def test_search_reports_failure(self):
        page = _FakePage()

        def raising_goto(url, timeout=None, wait_until=None):
            raise RuntimeError("timed out")

        page.goto = raising_goto
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.search(0, "hotels in Guam")

        self.assertEqual(result.status, "failed")

    def test_search_refuses_an_empty_query(self):
        page = _FakePage()
        control = BrowserControl(observer=_FakeObserver(page))

        result = control.search(0, "   ")

        self.assertEqual(result.status, "refused")
        self.assertEqual(page.goto_calls, [])


if __name__ == "__main__":
    unittest.main()
