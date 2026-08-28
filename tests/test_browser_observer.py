import threading
import unittest

from tools.browser_control.browser_connection import BrowserConnectionResult
from tools.browser_control.browser_observer import BrowserObserver


class _FakePage:
    def __init__(self, *, url, title, elements=None, text=""):
        self.url = url
        self._title = title
        self._elements = elements if elements is not None else []
        self._text = text

    def title(self):
        return self._title

    def evaluate(self, script):
        if "querySelectorAll" in script:
            return self._elements
        return self._text


class _LoadingPage(_FakePage):
    def __init__(self, *, element_batches, **kwargs):
        super().__init__(**kwargs)
        self._element_batches = list(element_batches)
        self.scan_calls = 0

    def evaluate(self, script):
        if "querySelectorAll" not in script:
            return self._text
        self.scan_calls += 1
        if self._element_batches:
            return self._element_batches.pop(0)
        return self._elements


class _StillLoadingPage(_FakePage):
    def evaluate(self, script):
        if script == "document.readyState":
            return "loading"
        return super().evaluate(script)


class _SemanticPage(_FakePage):
    def __init__(self, *, summary, **kwargs):
        super().__init__(**kwargs)
        self._summary = summary

    def evaluate(self, script):
        if "__ELAINA_CONTENT_SUMMARY__" in script:
            return dict(self._summary)
        return super().evaluate(script)


class _LegacyTitleLocator:
    def text_content(self, *, timeout=None):
        if timeout != 750:
            raise AssertionError("expected the bounded legacy title lookup")
        return "Recovered title"


class _LegacyPlaywrightPage:
    """Simulates Playwright versions whose Page.title has no timeout kwarg."""

    def title(self):
        raise AssertionError("the unbounded Page.title fallback must not run")

    def locator(self, selector):
        if selector != "title":
            raise AssertionError("unexpected selector")
        return _LegacyTitleLocator()


class _FakeContext:
    def __init__(self, pages):
        self.pages = pages
        self.new_page_calls = 0

    def new_page(self):
        self.new_page_calls += 1
        page = _FakePage(url="about:blank", title="")
        self.pages.append(page)
        return page


class _FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts


class _FakeConnection:
    def __init__(self, result: BrowserConnectionResult):
        self.result = result
        self.connect_calls = 0

    def connect(self, *, allow_isolated_launch=False):
        self.connect_calls += 1
        return self.result


class _FakeActiveWindow:
    def __init__(self, title):
        self.title = title


class _FakeUIObserver:
    """Stands in for Phase 4B's WindowsUIObserver, whose real window title
    is the one signal a CDP-attached page can't fake -- see
    BrowserObserver._active_tab_index."""

    def __init__(self, *, active_window_title=None, available=True):
        self._active_window_title = active_window_title
        self.available = available

    def get_active_window(self):
        if self._active_window_title is None:
            return None
        return _FakeActiveWindow(self._active_window_title)


def _connected(browser) -> BrowserConnectionResult:
    return BrowserConnectionResult("connected", browser=browser, playwright=object())


class ListTabsTests(unittest.TestCase):
    def test_does_not_guess_an_active_tab_when_multiple_tabs_are_unidentified(self):
        # With no native foreground-title evidence, the old implementation
        # selected the last-created page. That can be an unrelated background
        # tab, so browser actions now fail closed until a page is identified.
        pages = [
            _FakePage(url="https://a.com", title="A"),
            _FakePage(url="https://b.com", title="B"),
        ]
        browser = _FakeBrowser([_FakeContext(pages)])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        tabs = observer.list_tabs()

        self.assertEqual(len(tabs), 2)
        self.assertFalse(tabs[0].is_active)
        self.assertFalse(tabs[1].is_active)

    def test_cross_checks_the_real_window_title_when_available(self):
        pages = [
            _FakePage(url="https://a.com", title="A"),
            _FakePage(url="https://b.com", title="B"),
        ]
        browser = _FakeBrowser([_FakeContext(pages)])
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(browser)),
            ui_observer=_FakeUIObserver(active_window_title="A - Whale"),
        )

        tabs = observer.list_tabs()

        self.assertTrue(tabs[0].is_active)
        self.assertFalse(tabs[1].is_active)

    def test_never_lists_a_browser_extension_page_as_a_tab(self):
        pages = [
            _FakePage(
                url="chrome-extension://abc/index.html", title="",
            ),
            _FakePage(url="https://a.com", title="A"),
        ]
        browser = _FakeBrowser([_FakeContext(pages)])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        tabs = observer.list_tabs()

        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0].url, "https://a.com")
        self.assertTrue(tabs[0].is_active)

    def test_never_lists_a_transient_blank_tab_as_an_actionable_page(self):
        pages = [
            _FakePage(url="about:blank", title=""),
            _FakePage(url="https://a.com", title="A"),
        ]
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext(pages)]))),
        )

        tabs = observer.list_tabs()

        self.assertEqual([(item.index, item.url) for item in tabs], [(0, "https://a.com")])

    def test_reuses_the_connection_across_calls(self):
        browser = _FakeBrowser([_FakeContext([_FakePage(url="https://a.com", title="A")])])
        connection = _FakeConnection(_connected(browser))
        observer = BrowserObserver(connection=connection)

        observer.list_tabs()
        observer.list_tabs()

        self.assertEqual(connection.connect_calls, 1)

    def test_reconnects_fresh_when_called_from_a_different_thread(self):
        # Playwright's sync API is strictly single-thread, and Elaina
        # spawns a new thread per user turn -- reusing a connection
        # cached from a different (likely finished) thread has been seen
        # to hang rather than raise, so it must never be reused at all.
        connection = _FakeConnection(_connected(_FakeBrowser([_FakeContext(
            [_FakePage(url="https://a.com", title="A")],
        )])))
        observer = BrowserObserver(connection=connection)

        observer.list_tabs()
        self.assertEqual(connection.connect_calls, 1)

        other_thread = threading.Thread(target=observer.list_tabs)
        other_thread.start()
        other_thread.join()

        self.assertEqual(connection.connect_calls, 2)

    def test_returns_the_connection_failure_directly(self):
        failure = BrowserConnectionResult("not_debug_enabled", "Reopen with the shortcut.")
        observer = BrowserObserver(connection=_FakeConnection(failure))

        result = observer.list_tabs()

        self.assertEqual(result.status, "not_debug_enabled")

    def test_legacy_playwright_title_lookup_stays_bounded(self):
        self.assertEqual(
            BrowserObserver._safe_title(_LegacyPlaywrightPage()),
            "Recovered title",
        )


class ResolveNavigablePageTests(unittest.TestCase):
    """A session's very first browser action ordinarily starts from a
    blank tab -- unlike list_tabs/describe_page, resolve_navigable_page
    must still be able to target it, since navigating it into something
    useful is the point, not an edge case."""

    def test_finds_a_blank_new_tab_page_when_it_is_the_only_tab(self):
        blank = _FakePage(url="chrome://newtab/", title="")
        browser = _FakeBrowser([_FakeContext([blank])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        page = observer.resolve_navigable_page(None)

        self.assertIs(page, blank)

    def test_still_excludes_extension_and_devtools_pages(self):
        extension = _FakePage(url="chrome-extension://abc/index.html", title="")
        blank = _FakePage(url="about:blank", title="")
        browser = _FakeBrowser([_FakeContext([extension, blank])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        page = observer.resolve_navigable_page(None)

        self.assertIs(page, blank)

    def test_returns_none_when_not_connected(self):
        failure = BrowserConnectionResult("not_debug_enabled", "Reopen with the shortcut.")
        observer = BrowserObserver(connection=_FakeConnection(failure))

        self.assertIsNone(observer.resolve_navigable_page(None))

    def test_resolves_by_explicit_tab_index_among_navigable_pages(self):
        pages = [
            _FakePage(url="https://a.com", title="A"),
            _FakePage(url="https://b.com", title="B"),
        ]
        browser = _FakeBrowser([_FakeContext(pages)])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        page = observer.resolve_navigable_page(1)

        self.assertIs(page, pages[1])

    def test_creates_a_page_when_the_browser_reports_none_at_all(self):
        # The real-world gap this guards: Playwright's CDP attach to an
        # externally-launched browser (this project never uses Playwright's
        # own chromium.launch()) does not reliably enumerate that browser's
        # very first default tab, so contexts[0].pages can legitimately be
        # empty on a session's first search/open_url even though a real
        # window is open. BrowserConnection.open_url hits the identical gap
        # on a cold launch and already falls back to creating a page.
        context = _FakeContext([])
        browser = _FakeBrowser([context])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        page = observer.resolve_navigable_page(None)

        self.assertIsNotNone(page)
        self.assertEqual(context.new_page_calls, 1)

    def test_creates_the_first_page_even_when_a_tab_index_was_guessed(self):
        # With zero tabs open, no index means anything -- every one is
        # equally invalid. Found live: the planner guessed `tab: 0` on a
        # cold start, got a hard "I couldn't find that browser tab", and
        # burned every round retrying instead of opening the session's
        # first page.
        context = _FakeContext([])
        browser = _FakeBrowser([context])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        page = observer.resolve_navigable_page(0)

        self.assertIsNotNone(page)
        self.assertEqual(context.new_page_calls, 1)

    def test_an_out_of_range_index_navigates_an_existing_tab_not_a_new_one(self):
        # Navigation only: pointing a tab at a new URL in Elaina's own
        # isolated browser is safe wherever it lands, so a stale index
        # falls back to a real tab. Found live -- the planner kept guessing
        # an index and every search came back "I couldn't find that browser
        # tab", burning its whole round budget without navigating once.
        # describe_page/click_element still resolve strictly, because
        # acting on the wrong page is the dangerous case.
        existing = _FakePage(url="https://example.com", title="Example")
        context = _FakeContext([existing])
        browser = _FakeBrowser([context])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        page = observer.resolve_navigable_page(5)

        self.assertIs(page, existing)
        self.assertEqual(context.new_page_calls, 0)


class DescribePageTests(unittest.TestCase):
    def test_describe_page_includes_bounded_text_headings_and_image_labels(self):
        page = _SemanticPage(
            url="https://shop.example/rtx-5080",
            title="RTX 5080 listings",
            elements=[{
                "id": "e0", "tag": "button", "role": "", "type": "button",
                "label": "Lowest price", "disabled": False,
            }],
            summary={
                "marker": "__ELAINA_CONTENT_SUMMARY__",
                "headings": ["Used RTX 5080", "Search results"],
                "text": "Used RTX 5080 listings from verified sellers",
                "textLength": 2000,
                "images": ["RTX 5080 product photo"],
                "imageCount": 3,
            },
        )
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext([page])]))),
        )

        observation = observer.describe_page()

        self.assertEqual(observation.headings, ("Used RTX 5080", "Search results"))
        self.assertIn("verified sellers", observation.text_excerpt)
        self.assertTrue(observation.text_truncated)
        self.assertEqual(observation.images[0].label, "RTX 5080 product photo")
        self.assertEqual(observation.image_count, 3)

    def test_static_semantic_page_is_observed_even_without_buttons(self):
        page = _SemanticPage(
            url="https://example.com/article",
            title="Article",
            elements=[],
            summary={
                "marker": "__ELAINA_CONTENT_SUMMARY__",
                "headings": ["A useful article"],
                "text": "Readable content",
                "textLength": 16,
                "images": [],
                "imageCount": 0,
            },
        )
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext([page])]))),
        )

        observation = observer.describe_page()

        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.elements, ())
        self.assertEqual(observation.text_excerpt, "Readable content")

    def test_describes_the_active_tab_by_default(self):
        elements = [
            {"id": "e0", "tag": "button", "role": "", "type": "", "label": "Search", "disabled": False},
            {"id": "e1", "tag": "a", "role": "", "type": "", "label": "Home", "disabled": False},
        ]
        background_page = _FakePage(url="https://other.example", title="Other")
        active_page = _FakePage(
            url="https://hotels.example", title="Hotels", elements=elements,
        )
        browser = _FakeBrowser([_FakeContext([background_page, active_page])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))
        observer.prefer_page("https://hotels.example")

        observation = observer.describe_page()

        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.title, "Hotels")
        self.assertEqual(len(observation.elements), 2)
        self.assertEqual(observation.elements[0].id, "e0")
        self.assertEqual(observation.elements[0].label, "Search")

    def test_refuses_to_describe_an_arbitrary_background_tab_without_identity(self):
        pages = [
            _FakePage(url="https://google.example", title="Google", elements=[]),
            _FakePage(url="https://spotify.example", title="Spotify", elements=[]),
        ]
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext(pages)]))),
        )

        observation = observer.describe_page()

        self.assertEqual(observation.status, "not_found")
        self.assertIn("couldn't determine", observation.message)

    def test_reports_a_blank_startup_tab_as_loading_not_missing(self):
        page = _FakePage(url="about:blank", title="")
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext([page])]))),
        )

        observation = observer.describe_page()

        self.assertEqual(observation.status, "loading")
        self.assertEqual(observation.url, "about:blank")
        self.assertIn("blank startup", observation.message)

    def test_reports_an_empty_loading_page_as_loading_not_empty(self):
        page = _StillLoadingPage(
            url="https://hotels.example/search", title="Hotels", elements=[],
        )
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext([page])]))),
        )

        observation = observer.describe_page()

        self.assertEqual(observation.status, "loading")
        self.assertIn("still loading", observation.message)

    def test_describes_a_specific_tab_by_index(self):
        pages = [
            _FakePage(url="https://a.com", title="A", elements=[
                {"id": "e0", "tag": "a", "role": "", "type": "", "label": "Link A", "disabled": False},
            ]),
            _FakePage(url="https://b.com", title="B", elements=[
                {"id": "e0", "tag": "a", "role": "", "type": "", "label": "Link B", "disabled": False},
            ]),
        ]
        browser = _FakeBrowser([_FakeContext(pages)])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        observation = observer.describe_page(1)

        self.assertEqual(observation.title, "B")
        self.assertEqual(observation.elements[0].label, "Link B")

    def test_reports_empty_when_no_interactive_elements_exist(self):
        page = _FakePage(url="https://static.example", title="Static", elements=[])
        browser = _FakeBrowser([_FakeContext([page])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        observation = observer.describe_page()

        self.assertEqual(observation.status, "empty")

    def test_preserves_resolved_link_and_ad_metadata_from_the_dom_scan(self):
        page = _FakePage(
            url="https://www.google.com/search?q=guam",
            title="Google",
            elements=[{
                "id": "e0", "tag": "a", "role": "", "type": "",
                "label": "Sponsored hotel", "disabled": False,
                "href": "https://ads.example/hotel", "isAd": True,
            }],
        )
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext([page])]))),
        )

        observation = observer.describe_page()

        self.assertEqual(observation.elements[0].href, "https://ads.example/hotel")
        self.assertTrue(observation.elements[0].is_ad)

    def test_a_stale_tab_index_resolves_like_no_index_rather_than_failing(self):
        # A non-existent index is a model slip, not a different request.
        # Found live: the planner guessed a stale index, got a hard "I
        # couldn't determine the active browser tab", guessed again, and
        # burned its whole round budget in that loop without ever reading
        # the page it had just opened. With one tab open there is no
        # ambiguity about which page is meant.
        page = _FakePage(
            url="https://a.com", title="A",
            elements=[{
                "id": "e0", "tag": "a", "role": "", "type": "",
                "label": "Only link", "disabled": False,
            }],
        )
        browser = _FakeBrowser([_FakeContext([page])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        observation = observer.describe_page(5)

        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.tab_index, 0)

    def test_an_undecidable_page_still_fails_closed(self):
        # The safety property is unchanged: with several unidentified tabs
        # and nothing Elaina opened herself, reading one would be a guess.
        pages = [
            _FakePage(url="https://a.com", title="A"),
            _FakePage(url="https://b.com", title="B"),
        ]
        browser = _FakeBrowser([_FakeContext(pages)])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        self.assertEqual(observer.describe_page(5).status, "not_found")
        self.assertEqual(observer.describe_page().status, "not_found")


    def test_truncates_a_very_large_element_list(self):
        elements = [
            {"id": f"e{i}", "tag": "a", "role": "", "type": "", "label": f"Item {i}", "disabled": False}
            for i in range(200)
        ]
        page = _FakePage(url="https://big.example", title="Big", elements=elements)
        browser = _FakeBrowser([_FakeContext([page])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        observation = observer.describe_page()

        self.assertLessEqual(len(observation.elements), 120)
        self.assertTrue(observation.truncated)

    def test_retries_a_partial_dom_scan_until_the_requested_control_appears(self):
        page = _LoadingPage(
            url="https://google.example/search?q=guam",
            title="Google",
            elements=[],
            element_batches=[
                [{"id": "e0", "tag": "a", "role": "", "type": "", "label": "News", "disabled": False}],
                [{"id": "e1", "tag": "a", "role": "", "type": "", "label": "Images", "disabled": False}],
            ],
        )
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext([page])]))),
        )

        observation = observer.describe_page(query="Images")

        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.elements[0].label, "Images")
        self.assertEqual(page.scan_calls, 2)

    def test_uses_an_elaina_opened_page_as_the_safe_fallback(self):
        pages = [
            _FakePage(url="https://other.example", title="Other"),
            _FakePage(url="https://google.example/search?q=guam", title="Google"),
        ]
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext(pages)]))),
        )
        observer.prefer_page("https://google.example/search?q=guam")

        tabs = observer.list_tabs()

        self.assertFalse(tabs[0].is_active)
        self.assertTrue(tabs[1].is_active)

    def test_prefers_the_newest_controlled_copy_of_an_identical_search(self):
        pages = [
            _FakePage(url="https://google.example/search?q=guam", title="Old search"),
            _FakePage(url="https://other.example", title="Other"),
            _FakePage(url="https://google.example/search?q=guam", title="New search"),
        ]
        observer = BrowserObserver(
            connection=_FakeConnection(_connected(_FakeBrowser([_FakeContext(pages)]))),
        )
        observer.prefer_page("https://google.example/search?q=guam")

        tabs = observer.list_tabs()

        self.assertFalse(tabs[0].is_active)
        self.assertTrue(tabs[2].is_active)

    def test_keeps_a_redirected_search_page_as_the_preferred_page(self):
        self.assertTrue(BrowserObserver._urls_refer_to_same_page(
            "https://www.google.com/search?q=best+hotels+in+Guam",
            "https://www.google.com/search?q=best+hotels+in+Guam&hl=en",
        ))
        self.assertFalse(BrowserObserver._urls_refer_to_same_page(
            "https://www.google.com/search?q=best+hotels+in+Guam",
            "https://www.google.com/search?q=flights+to+Guam",
        ))

    def test_connection_failure_is_surfaced_without_a_real_scan(self):
        failure = BrowserConnectionResult("unavailable", "Browser control isn't installed.")
        observer = BrowserObserver(connection=_FakeConnection(failure))

        observation = observer.describe_page()

        self.assertEqual(observation.status, "unavailable")


class ReadTextTests(unittest.TestCase):
    def test_reads_the_active_page_body_text(self):
        page = _FakePage(
            url="https://hotels.example", title="Hotels",
            text="Grand Hotel - $120/night\nSeaside Inn - $95/night",
        )
        browser = _FakeBrowser([_FakeContext([page])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        result = observer.read_text()

        self.assertEqual(result.status, "observed")
        self.assertIn("Grand Hotel", result.text)

    def test_reports_empty_for_a_blank_page(self):
        page = _FakePage(url="https://blank.example", title="Blank", text="   ")
        browser = _FakeBrowser([_FakeContext([page])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        result = observer.read_text()

        self.assertEqual(result.status, "empty")

    def test_truncates_very_long_page_text(self):
        page = _FakePage(
            url="https://long.example", title="Long", text="x" * 10000,
        )
        browser = _FakeBrowser([_FakeContext([page])])
        observer = BrowserObserver(connection=_FakeConnection(_connected(browser)))

        result = observer.read_text()

        self.assertLessEqual(len(result.text), 4000)
        self.assertTrue(result.truncated)


if __name__ == "__main__":
    unittest.main()
