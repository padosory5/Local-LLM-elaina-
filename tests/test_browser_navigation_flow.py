"""Open the page, see it, and be ready to act -- in one tool round.

Two problems this covers, both found live:

* "she just opens an about:blank page and loads forever" -- a slow page
  that has genuinely committed to the right destination must not be
  reported as a failure just because some long-poll request keeps the
  load state pending.
* the model clicking ids from the page it was on *before* navigating,
  because a navigation's result was a bare "Searched for X." with nothing
  in it. Observing automatically on every navigation is the pattern
  production browser agents converge on, and it removes that failure at
  the source while saving a whole model round.
"""

import unittest

from brain.browser_action_planner import BrowserActionPlanner
from tools.browser_control.browser_control import BrowserActionResult, BrowserControl
from tools.browser_control.browser_observer import PageElement, PageObservation
from tests.test_browser_action_planner import (
    FakeClient,
    FakeControl,
    FakeObserver,
    _message,
    _tool_call,
)


RESULTS_PAGE = PageObservation(
    "observed",
    url="https://www.google.com/search?q=hotels",
    title="hotels - Google Search",
    elements=(
        PageElement(id="scan1-e0", tag="a", role="", label="Hotel One"),
        PageElement(id="scan1-e1", tag="a", role="", label="Hotel Two"),
    ),
    tab_index=0,
    scan_id="scan1",
)


class AutoObserveAfterNavigationTests(unittest.TestCase):
    def _planner(self, client, *, observation=RESULTS_PAGE):
        return BrowserActionPlanner(
            client=client,
            model="qwen3:8b",
            keep_alive=-1,
            observer=FakeObserver(page_observation=observation),
            control=FakeControl(
                search_result=BrowserActionResult(
                    "navigated", "Searched for 'hotels'.",
                    url=RESULTS_PAGE.url, verified=True,
                ),
                click_result=BrowserActionResult(
                    "clicked", "Clicked Hotel One.", element_id="scan1-e0",
                    element_label="Hotel One", verified=True,
                ),
            ),
        )

    def test_a_search_result_already_carries_the_page_contents(self):
        client = FakeClient([
            _message(tool_calls=[_tool_call("search", query="hotels")]),
            _message(content="Hotel One and Hotel Two came up."),
        ])
        planner = self._planner(client)

        result = planner.act("find hotels and list them")

        self.assertEqual(result.status, "done")
        # The tool result the model saw for its *next* decision.
        tool_message = next(
            message["content"]
            for message in client.calls[-1]["messages"]
            if message.get("role") == "tool"
        )
        self.assertIn("Searched for 'hotels'.", tool_message)
        self.assertIn("Hotel One", tool_message)
        self.assertIn("scan1-e0", tool_message)

    def test_the_model_can_click_immediately_without_a_separate_scan(self):
        # This is the round that used to be wasted -- and the one whose
        # absence produced "that element was not in the latest live page
        # scan" when the model guessed instead.
        client = FakeClient([
            _message(tool_calls=[_tool_call("search", query="hotels")]),
            _message(tool_calls=[_tool_call("click_element", element_id="scan1-e0")]),
            _message(content="Opened Hotel One."),
        ])
        planner = self._planner(client)

        result = planner.act("find hotels and open the first one")

        self.assertEqual(result.status, "done")
        self.assertEqual(planner.control.click_calls, [(0, "scan1-e0", False)])

    def test_the_page_digest_never_becomes_the_spoken_summary(self):
        # Found live: a task step's spoken result was the whole
        # post-navigation digest -- element ids, tags and all -- because
        # the navigation's confirmation message is what overrides the
        # model's own answer when nothing was observed afterwards.
        client = FakeClient([
            _message(tool_calls=[_tool_call("search", query="naver")]),
            # A failure-shaped reply forces the confirmation-message path.
            _message(content="I can't get any further here."),
        ])
        planner = self._planner(client)

        result = planner.act("open naver")

        self.assertNotIn("scan1-e0", result.summary)
        self.assertNotIn("Page after navigation", result.summary)

    def test_an_id_slip_on_an_earlier_page_does_not_doom_a_later_one(self):
        # Found live: the model used a stale element id, spent its single
        # recovery, navigated somewhere else entirely, slipped once more on
        # the fresh page, and the whole task was abandoned. A new page has
        # new ids, so a mistake on the previous one is not evidence about
        # this one.
        client = FakeClient([
            _message(tool_calls=[_tool_call("click_element", element_id="stale-a")]),
            _message(tool_calls=[_tool_call("search", query="hotels")]),
            _message(tool_calls=[_tool_call("click_element", element_id="stale-b")]),
            _message(tool_calls=[_tool_call("describe_page")]),
            _message(tool_calls=[_tool_call("click_element", element_id="scan1-e0")]),
            _message(content="Opened Hotel One."),
        ])
        planner = self._planner(client)

        result = planner.act("find hotels and open the first one")

        self.assertEqual(result.status, "done")
        self.assertEqual(planner.control.click_calls, [(0, "scan1-e0", False)])

    def test_a_page_that_cannot_be_scanned_yet_says_so_plainly(self):
        client = FakeClient([
            _message(tool_calls=[_tool_call("search", query="hotels")]),
            _message(content="The page is still coming up."),
        ])
        planner = self._planner(
            client, observation=PageObservation("empty"),
        )

        planner.act("find hotels")

        tool_message = next(
            message["content"]
            for message in client.calls[-1]["messages"]
            if message.get("role") == "tool"
        )
        self.assertIn("didn't expose elements yet", tool_message)

    def test_an_observer_failure_never_turns_a_real_navigation_into_an_error(self):
        class _ExplodingObserver(FakeObserver):
            def describe_page(self, tab_index=None, **kwargs):
                raise RuntimeError("CDP hiccup")

        client = FakeClient([
            _message(tool_calls=[_tool_call("search", query="hotels")]),
            _message(content="I searched for hotels."),
        ])
        planner = BrowserActionPlanner(
            client=client, model="qwen3:8b", keep_alive=-1,
            observer=_ExplodingObserver(),
            control=FakeControl(
                search_result=BrowserActionResult(
                    "navigated", "Searched for 'hotels'.",
                    url=RESULTS_PAGE.url, verified=True,
                ),
            ),
        )

        result = planner.act("find hotels")

        self.assertEqual(result.status, "done")

    def test_the_digest_is_capped_so_a_dense_page_stays_readable(self):
        crowded = PageObservation(
            "observed", url="https://shop.example", title="Shop",
            elements=tuple(
                PageElement(id=f"scan1-e{i}", tag="a", role="", label=f"Item {i}")
                for i in range(80)
            ),
            tab_index=0, scan_id="scan1",
        )
        client = FakeClient([
            _message(tool_calls=[_tool_call("search", query="shop")]),
            _message(content="Lots of items are listed."),
        ])
        planner = self._planner(client, observation=crowded)

        planner.act("find items")

        tool_message = next(
            message["content"]
            for message in client.calls[-1]["messages"]
            if message.get("role") == "tool"
        )
        self.assertIn("more elements", tool_message)
        self.assertNotIn("scan1-e79", tool_message)


class _StubObserver:
    def __init__(self, page):
        self._page = page
        self.preferred = ""

    def _ensure_connected(self, *, allow_isolated_launch=False):
        from tools.browser_control.browser_connection import BrowserConnectionResult

        return BrowserConnectionResult("connected", browser=object())

    def resolve_navigable_page(self, tab_index):
        return self._page

    def prefer_page(self, url):
        self.preferred = url


class _SlowPage:
    """A page whose load state never settles, as heavy real sites do."""

    def __init__(self, *, final_url, fail_times=1):
        self.url = "about:blank"
        self._final_url = final_url
        self._fail_times = fail_times
        self.goto_calls = 0

    def goto(self, url, *, timeout=None, wait_until=None):
        self.goto_calls += 1
        if self.goto_calls <= self._fail_times:
            # Chromium commits the navigation, then the load state hangs.
            self.url = self._final_url
            raise TimeoutError("Timeout 15000ms exceeded")
        self.url = self._final_url

    def bring_to_front(self):
        pass


class NavigationCommitTests(unittest.TestCase):
    """A page the user can SEE must never be reported as unreachable."""

    def test_a_timeout_after_reaching_the_destination_counts_as_navigated(self):
        page = _SlowPage(final_url="https://www.booking.com/searchresults.html")
        control = BrowserControl(observer=_StubObserver(page))

        result = control.navigate(None, "https://booking.com/searchresults.html")

        self.assertEqual(result.status, "navigated")
        self.assertTrue(result.verified)
        self.assertIn("still loading", result.message)
        self.assertEqual(page.goto_calls, 1)

    def test_a_redirect_to_a_subdomain_still_counts_as_the_same_destination(self):
        page = _SlowPage(final_url="https://m.example.com/deals")
        control = BrowserControl(observer=_StubObserver(page))

        result = control.navigate(None, "https://example.com/deals")

        self.assertEqual(result.status, "navigated")

    def test_a_timeout_still_on_about_blank_is_retried_then_reported(self):
        class _StuckPage(_SlowPage):
            def goto(self, url, *, timeout=None, wait_until=None):
                self.goto_calls += 1
                raise TimeoutError("Timeout 15000ms exceeded")

        page = _StuckPage(final_url="https://example.com")
        control = BrowserControl(observer=_StubObserver(page))

        result = control.navigate(None, "https://example.com")

        self.assertEqual(result.status, "failed")
        # One retry, because a fresh profile's first DNS lookup can miss.
        self.assertEqual(page.goto_calls, 2)

    def test_a_transient_failure_succeeds_on_the_retry(self):
        page = _SlowPage(final_url="https://example.com", fail_times=0)
        control = BrowserControl(observer=_StubObserver(page))

        result = control.navigate(None, "https://example.com")

        self.assertEqual(result.status, "navigated")
        self.assertNotIn("still loading", result.message)


class NavigationCommitHelperTests(unittest.TestCase):
    def test_host_comparison_ignores_www_and_scheme(self):
        committed = BrowserControl._navigation_committed
        page = _SlowPage(final_url="https://www.example.com/x")
        page.url = "https://www.example.com/x"

        self.assertTrue(committed(page, "http://example.com/anything"))

    def test_a_different_site_is_not_a_commit(self):
        committed = BrowserControl._navigation_committed
        page = _SlowPage(final_url="https://other.test")
        page.url = "https://other.test"

        self.assertFalse(committed(page, "https://example.com"))

    def test_about_blank_is_never_a_commit(self):
        committed = BrowserControl._navigation_committed
        page = _SlowPage(final_url="")
        page.url = "about:blank"

        self.assertFalse(committed(page, "https://example.com"))


if __name__ == "__main__":
    unittest.main()
