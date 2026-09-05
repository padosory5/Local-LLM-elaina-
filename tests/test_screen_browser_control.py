import unittest

from tools.browser_control.safe_browser import SafeBrowserControl
from tools.screen_control.cursor_driver import InputResult
from tools.screen_browser.page_observer import (
    ElementLookup,
    ScreenElement,
    ScreenPageObservation,
)
from tools.screen_browser.screen_browser_control import ScreenBrowserControl


def _element(index=0, role="button", label="Open", **kwargs):
    fields = {
        "value": "",
        "href": "",
        "disabled": False,
        "rect": (100, 100, 200, 140),
        "click_point": (150, 120),
        "in_dialog": False,
        "in_main": True,
    }
    fields.update(kwargs)
    return ScreenElement(index=index, role=role, label=label, **fields)


def _observation(*elements, url="https://example.com", title="Example", scan="s1", handle=1):
    return ScreenPageObservation(
        "observed", handle=handle, title=title, url=url,
        text_excerpt="Rendered destination content" if elements else "",
        elements=tuple(elements), scan_id=scan,
    )


class _FakeObserver:
    """A page that changes only once the cursor has actually done something.

    Modelled this way rather than as a queue because the control layer
    legitimately observes more than once per action -- it re-scans after
    focusing the window, since focusing can move or resize it.
    """

    def __init__(self, observations, lookup=None, cursor=None):
        queued = list(observations)
        self.before = queued[0]
        self.after = queued[1] if len(queued) > 1 else queued[0]
        self.cursor = cursor
        self._lookup = lookup
        self.available = True
        self.calls = 0

    def observe(self, window=None):
        self.calls += 1
        if self.cursor is not None and self.cursor.acted:
            return self.after
        return self.before

    def resolve(self, observation, index, *, expected_label="", expected_scan_id=""):
        if self._lookup is not None:
            return self._lookup
        for element in observation.elements:
            if element.index == index:
                if expected_label and expected_label != element.label:
                    return ElementLookup(
                        "changed",
                        message=f"Element [{index}] is now {element.label!r}.",
                    )
                return ElementLookup("resolved", element=element, node=object())
        return ElementLookup("unknown_index", message="no such element")


class _LaunchableObserver(_FakeObserver):
    def __init__(self, before, after):
        super().__init__([before, after])
        self.launched = False

    def observe(self, window=None):
        self.calls += 1
        if not self.launched:
            return ScreenPageObservation(
                "no_browser", message="No browser window is open right now.",
            )
        if self.cursor is not None and self.cursor.acted:
            return self.after
        return self.before


class _LoadingObserver(_FakeObserver):
    """Expose the URL first, then the renderer's settled accessibility tree."""

    def __init__(self, before, after_sequence):
        super().__init__([before])
        self.after_sequence = list(after_sequence)
        self.position = 0

    def observe(self, window=None):
        self.calls += 1
        if self.cursor is None or ("enter",) not in self.cursor.presses:
            return self.before
        item = self.after_sequence[min(self.position, len(self.after_sequence) - 1)]
        self.position += 1
        return item


class _FakeCursor:
    def __init__(self, *, result=None):
        self.available = True
        self.clicks = []
        self.typed = []
        self.presses = []
        self.scrolls = []
        self.acted = False
        self._result = result or InputResult("done")

    def click(self, point):
        self.clicks.append(point)
        self.acted = True
        return self._result

    def type_text(self, text):
        self.typed.append(text)
        self.acted = True
        return self._result

    def press(self, *keys):
        self.presses.append(keys)
        self.acted = True
        return self._result

    def clear_field(self):
        self.presses.append(("clear",))
        return self._result

    def scroll(self, point, notches):
        self.scrolls.append((point, notches))
        self.acted = True
        return self._result


def _control(observer, cursor=None, *, owns_point=True, focused=True):
    cursor = cursor or _FakeCursor()
    # The fake page needs to know when the action happened.
    observer.cursor = cursor
    return ScreenBrowserControl(
        observer=observer,
        cursor=cursor,
        safe_browser=SafeBrowserControl(opener=lambda url: None),
        sleeper=lambda seconds: None,
        window_at_point=lambda point: 1 if owns_point else 999,
        focuser=lambda handle: focused,
    )


class SafetyTests(unittest.TestCase):
    def test_payment_control_is_refused_outright(self):
        observer = _FakeObserver([_observation(_element(label="Pay now"))])
        cursor = _FakeCursor()
        result = _control(observer, cursor).click(0)
        self.assertEqual(result.status, "refused")
        self.assertEqual(cursor.clicks, [])

    def test_credential_field_is_refused_outright(self):
        observer = _FakeObserver(
            [_observation(_element(role="textbox", label="Password"))],
        )
        cursor = _FakeCursor()
        result = _control(observer, cursor).fill(0, "hunter2")
        self.assertEqual(result.status, "refused")
        self.assertEqual(cursor.typed, [])

    def test_committing_control_needs_confirmation_first(self):
        observer = _FakeObserver([_observation(_element(label="Submit order"))])
        cursor = _FakeCursor()
        result = _control(observer, cursor).click(0)
        self.assertEqual(result.status, "confirmation_required")
        self.assertEqual(cursor.clicks, [])

    def test_confirmed_committing_control_is_allowed_through(self):
        observer = _FakeObserver([
            _observation(_element(label="Submit order")),
            _observation(_element(label="Submit order"), url="https://example.com/done"),
        ])
        cursor = _FakeCursor()
        result = _control(observer, cursor).click(0, confirmed=True)
        self.assertEqual(result.status, "clicked")
        self.assertEqual(cursor.clicks, [(150, 120)])

    def test_download_link_needs_confirmation_first(self):
        # Chromium exposes the href through UIA, so a direct file link is
        # recognisable even when its label says nothing about downloading.
        observer = _FakeObserver([
            _observation(_element(role="link", label="Q3 report",
                                  href="https://example.com/report.pdf")),
        ])
        cursor = _FakeCursor()
        result = _control(observer, cursor).click(0)
        self.assertEqual(result.status, "confirmation_required")
        self.assertIn("Downloading", result.message)
        self.assertEqual(cursor.clicks, [])

    def test_confirmed_download_link_is_allowed(self):
        observer = _FakeObserver([
            _observation(_element(role="link", label="Q3 report",
                                  href="https://example.com/report.pdf")),
            _observation(_element(role="link", label="Q3 report"),
                         url="https://example.com/report.pdf"),
        ])
        cursor = _FakeCursor()
        result = _control(observer, cursor).click(0, confirmed=True)
        self.assertEqual(result.status, "clicked")

    def test_local_and_file_link_targets_are_refused(self):
        for target in (
            "file:///C:/Users/secrets.txt",
            "http://127.0.0.1:8080/admin",
            "http://192.168.1.1/",
        ):
            with self.subTest(target=target):
                observer = _FakeObserver([
                    _observation(_element(role="link", label="Click me",
                                          href=target)),
                ])
                cursor = _FakeCursor()
                result = _control(observer, cursor).click(0)
                self.assertEqual(result.status, "refused")
                self.assertEqual(cursor.clicks, [])

    def test_ordinary_web_link_is_not_refused(self):
        observer = _FakeObserver([
            _observation(_element(role="link", label="Learn more",
                                  href="https://example.com/about")),
            _observation(_element(role="link", label="About"),
                         url="https://example.com/about"),
        ])
        result = _control(observer).click(0)
        self.assertEqual(result.status, "clicked")

    def test_a_covered_element_is_not_clicked(self):
        # Another window owns that pixel, so clicking would hit something
        # the user never asked about.
        observer = _FakeObserver([_observation(_element())])
        cursor = _FakeCursor()
        result = _control(observer, cursor, owns_point=False).click(0)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(cursor.clicks, [])

    def test_unfocusable_window_is_not_clicked_at_coordinates(self):
        observer = _FakeObserver([_observation(_element())])
        cursor = _FakeCursor()
        result = _control(observer, cursor, focused=False).click(0)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(cursor.clicks, [])

    def test_disabled_element_is_not_clicked(self):
        observer = _FakeObserver([_observation(_element(disabled=True))])
        cursor = _FakeCursor()
        result = _control(observer, cursor).click(0)
        self.assertEqual(result.status, "not_actionable")
        self.assertEqual(cursor.clicks, [])

    def test_changed_element_is_refused(self):
        observer = _FakeObserver([_observation(_element(label="Buy now"))])
        cursor = _FakeCursor()
        result = _control(observer, cursor).click(0, expected_label="Learn more")
        self.assertEqual(result.status, "not_found")
        self.assertEqual(cursor.clicks, [])


class VerificationTests(unittest.TestCase):
    def test_click_that_changes_the_page_is_verified(self):
        observer = _FakeObserver([
            _observation(_element(label="Learn more")),
            _observation(_element(label="Home"), url="https://iana.org"),
        ])
        result = _control(observer).click(0)
        self.assertEqual(result.status, "clicked")
        self.assertIs(result.verified, True)
        self.assertEqual(result.url, "https://iana.org")

    def test_click_that_changes_nothing_is_not_called_a_success(self):
        observer = _FakeObserver([_observation(_element(label="Learn more"))])
        result = _control(observer).click(0)
        self.assertEqual(result.status, "click_unverified")
        self.assertFalse(result.succeeded)
        self.assertIs(result.verified, False)

    def test_cursor_failure_is_reported_not_swallowed(self):
        observer = _FakeObserver([_observation(_element())])
        cursor = _FakeCursor(
            result=InputResult("user_took_over", "You moved the mouse."),
        )
        result = _control(observer, cursor).click(0)
        self.assertEqual(result.status, "user_took_over")
        self.assertFalse(result.succeeded)


class FillTests(unittest.TestCase):
    def test_typed_value_read_back_confirms_the_fill(self):
        field = _element(role="textbox", label="Search")
        observer = _FakeObserver([
            _observation(field),
            _observation(_element(role="textbox", label="Search", value="laptops")),
        ])
        cursor = _FakeCursor()
        result = _control(observer, cursor).fill(0, "laptops")
        self.assertEqual(result.status, "filled")
        self.assertIs(result.verified, True)
        self.assertEqual(cursor.typed, ["laptops"])
        # The field is clicked first: typing goes wherever focus is.
        self.assertEqual(cursor.clicks, [(150, 120)])

    def test_value_that_did_not_land_is_not_called_filled(self):
        observer = _FakeObserver([
            _observation(_element(role="textbox", label="Search")),
            _observation(_element(role="textbox", label="Search", value="")),
        ])
        result = _control(observer).fill(0, "laptops")
        self.assertEqual(result.status, "fill_unverified")
        self.assertIs(result.verified, False)

    def test_non_text_element_is_rejected(self):
        observer = _FakeObserver([_observation(_element(role="button"))])
        result = _control(observer).fill(0, "text")
        self.assertEqual(result.status, "not_actionable")

    def test_overlong_text_is_refused_before_anything_is_typed(self):
        observer = _FakeObserver([_observation(_element(role="textbox"))])
        cursor = _FakeCursor()
        result = _control(observer, cursor).fill(0, "x" * 5000)
        self.assertEqual(result.status, "refused")
        self.assertEqual(cursor.typed, [])


class SelectTests(unittest.TestCase):
    def test_combobox_selection_is_verified_from_its_accessible_value(self):
        observer = _FakeObserver([
            _observation(_element(role="combobox", label="Guests", value="1 adult")),
            _observation(_element(role="combobox", label="Guests", value="2 adults")),
        ])
        cursor = _FakeCursor()

        result = _control(observer, cursor).select_option(0, "2 adults")

        self.assertEqual(result.status, "selected")
        self.assertIs(result.verified, True)
        self.assertEqual(cursor.clicks, [(150, 120)])
        self.assertEqual(cursor.typed, ["2 adults"])
        self.assertIn(("enter",), cursor.presses)

    def test_unreadable_selection_is_not_claimed(self):
        observer = _FakeObserver([
            _observation(_element(role="combobox", label="Guests", value="1 adult")),
            _observation(_element(role="combobox", label="Guests", value="1 adult")),
        ])

        result = _control(observer).select_option(0, "2 adults")

        self.assertEqual(result.status, "select_unverified")
        self.assertIs(result.verified, False)

    def test_regular_button_is_not_treated_as_a_select(self):
        observer = _FakeObserver([
            _observation(_element(role="button", label="Guests")),
        ])
        result = _control(observer).select_option(0, "2 adults")
        self.assertEqual(result.status, "not_actionable")


class NavigationTests(unittest.TestCase):
    def test_navigation_waits_for_a_stable_meaningful_accessibility_tree(self):
        url = "https://www.google.com/search?q=eiffel+tower"
        empty = _observation(url=url, scan="empty")
        transient = _observation(
            _element(role="link", label="", href="https://en.wikipedia.org/wiki/Eiffel_Tower"),
            url=url, scan="transient",
        )
        ready = ScreenPageObservation(
            "observed", handle=1, title="Eiffel Tower - Google Search",
            url=url,
            elements=(
                _element(
                    role="link", label="Eiffel Tower - Wikipedia",
                    href="https://en.wikipedia.org/wiki/Eiffel_Tower",
                ),
            ),
            headings=("Eiffel Tower",), scan_id="ready",
        )
        observer = _LoadingObserver(
            _observation(_element(), url="https://start.example"),
            [empty, transient, ready, ready],
        )
        cursor = _FakeCursor()

        result = _control(observer, cursor).navigate(url)

        self.assertEqual(result.status, "navigated")
        self.assertGreaterEqual(observer.position, 4)

    def test_navigation_uses_the_address_bar_and_verifies_the_landing(self):
        observer = _FakeObserver([
            _observation(_element(), url="https://start.example"),
            _observation(_element(), url="https://example.com"),
        ])
        cursor = _FakeCursor()
        result = _control(observer, cursor).navigate("https://example.com")
        self.assertEqual(result.status, "navigated")
        self.assertIn(("ctrl", "l"), cursor.presses)
        self.assertIn(("enter",), cursor.presses)
        self.assertEqual(cursor.typed, ["https://example.com"])

    def test_cold_about_blank_tab_does_not_block_address_bar_navigation(self):
        observer = _FakeObserver([
            ScreenPageObservation(
                "cold_tree", handle=1, title="New Tab", url="about:blank",
                message="no document yet",
            ),
            _observation(_element(), url="https://example.com"),
        ])
        cursor = _FakeCursor()

        result = _control(observer, cursor).navigate("https://example.com")

        self.assertEqual(result.status, "navigated")
        self.assertIn(("ctrl", "l"), cursor.presses)
        self.assertEqual(cursor.typed, ["https://example.com"])

    def test_navigation_launches_and_binds_a_browser_when_none_is_open(self):
        before = ScreenPageObservation(
            "cold_tree", handle=7, title="New Tab", url="about:blank",
            message="no document yet",
        )
        observer = _LaunchableObserver(
            before, _observation(_element(), url="https://example.com", handle=7),
        )
        cursor = _FakeCursor()
        observer.cursor = cursor
        launched = []

        control = ScreenBrowserControl(
            observer=observer,
            cursor=cursor,
            safe_browser=SafeBrowserControl(opener=lambda url: None),
            sleeper=lambda seconds: None,
            window_at_point=lambda point: 7,
            focuser=lambda handle: handle == 7,
            window_launcher=lambda: (
                launched.append(True), setattr(observer, "launched", True)
            ),
        )
        result = control.navigate("https://example.com")

        self.assertEqual(result.status, "navigated")
        self.assertEqual(launched, [True])
        self.assertIn(("ctrl", "l"), cursor.presses)

    def test_about_blank_is_never_accepted_as_a_successful_redirect(self):
        observer = _FakeObserver([
            _observation(_element(), url="https://start.example"),
            _observation(_element(), url="about:blank"),
        ])

        result = _control(observer).navigate("https://example.com")

        self.assertEqual(result.status, "navigate_unverified")
        self.assertIs(result.verified, False)

    def test_redirect_reports_where_it_actually_landed(self):
        observer = _FakeObserver([
            _observation(_element(), url="https://start.example"),
            _observation(_element(), url="https://www.elsewhere.example/path"),
        ])
        result = _control(observer).navigate("https://example.com")
        self.assertEqual(result.status, "navigate_unverified")
        self.assertEqual(result.navigation.classification, "wrong_destination")
        self.assertEqual(result.url, "https://www.elsewhere.example/path")

    def test_navigation_that_did_not_move_is_not_claimed(self):
        observer = _FakeObserver([_observation(_element(), url="https://start.example")])
        result = _control(observer).navigate("https://example.com")
        self.assertEqual(result.status, "navigate_unverified")
        self.assertIs(result.verified, False)

    def test_local_and_file_targets_are_refused(self):
        for target in ("file:///C:/secrets.txt", "http://127.0.0.1:8080", "not a url"):
            with self.subTest(target=target):
                observer = _FakeObserver([_observation(_element())])
                cursor = _FakeCursor()
                result = _control(observer, cursor).navigate(target)
                self.assertEqual(result.status, "refused")
                self.assertEqual(cursor.typed, [])

    def test_search_goes_through_the_configured_engine_only(self):
        observer = _FakeObserver([
            _observation(_element(), url="https://start.example"),
            _observation(
                _element(
                    role="link", label="Laptop results",
                    href="https://shop.example/laptops",
                ),
                url="https://www.google.com/search?q=laptops",
            ),
        ])
        cursor = _FakeCursor()
        result = _control(observer, cursor).search("laptops")
        self.assertEqual(result.status, "navigated")
        self.assertTrue(cursor.typed[0].startswith("https://www.google.com/search?q="))

    def test_same_page_comparison_ignores_scheme_and_www(self):
        control = _control(_FakeObserver([_observation(_element())]))
        self.assertTrue(control._same_page("example.com/a", "https://www.example.com/a/"))
        self.assertFalse(control._same_page("example.com/a", "https://example.com/b"))


class ScrollTests(unittest.TestCase):
    def test_scroll_reports_movement_only_when_the_page_changed(self):
        observer = _FakeObserver([
            _observation(_element(label="Top")),
            _observation(_element(label="Further down")),
        ])
        cursor = _FakeCursor()
        result = _control(observer, cursor).scroll("down")
        self.assertEqual(result.status, "scrolled")
        self.assertEqual(cursor.scrolls[0][1], -4)

    def test_scroll_that_does_nothing_is_reported_honestly(self):
        observer = _FakeObserver([_observation(_element(label="Top"))])
        result = _control(observer).scroll("down")
        self.assertEqual(result.status, "click_unverified")

    def test_unknown_direction_is_refused(self):
        observer = _FakeObserver([_observation(_element())])
        self.assertEqual(_control(observer).scroll("sideways").status, "refused")


class UnobservablePageTests(unittest.TestCase):
    def test_cold_tree_blocks_actions_with_an_honest_message(self):
        observer = _FakeObserver([
            ScreenPageObservation("cold_tree", handle=1, message="never woke up"),
        ])
        cursor = _FakeCursor()
        result = _control(observer, cursor).click(0)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.message, "never woke up")
        self.assertEqual(cursor.clicks, [])


if __name__ == "__main__":
    unittest.main()
