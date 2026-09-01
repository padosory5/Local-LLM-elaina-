import unittest

from brain.browser_action_planner import BrowserActionPlanner, _ObservationState
from tools.browser_control.browser_observer import PageElement, PageObservation
from tools.screen_browser.browser_window import BrowserWindow
from tools.screen_browser.page_observer import ScreenElement, ScreenPageObservation
from tools.screen_browser.screen_browser_service import (
    ScreenBrowserObserverAdapter,
    _element_index,
    to_page_observation,
)


class _FakeFinder:
    def __init__(self, windows):
        self._windows = list(windows)

    def list_windows(self):
        return tuple(self._windows)

    def window_for_handle(self, handle):
        return next((w for w in self._windows if w.handle == handle), None)

    def active_window(self):
        return next((w for w in self._windows if w.is_active), None)


class _FakeScreenObserver:
    def __init__(self, by_handle, active_handle):
        self._by_handle = by_handle
        self._active = active_handle
        self.observed = []

    def observe(self, window=None):
        handle = window if window is not None else self._active
        self.observed.append(window)
        return self._by_handle[handle]


def _screen_observation(handle, *, url="https://example.com", elements=()):
    return ScreenPageObservation(
        "observed", handle=handle, title="Example", url=url,
        elements=tuple(elements), scan_id=f"scan{handle}",
    )


def _screen_element(index=0, role="link", label="Learn more", **kwargs):
    fields = {
        "value": "", "href": "", "disabled": False, "rect": (0, 0, 10, 10),
        "click_point": (5, 5), "in_dialog": False, "in_main": True,
    }
    fields.update(kwargs)
    return ScreenElement(index=index, role=role, label=label, **fields)


def _window(handle, *, active=False):
    return BrowserWindow(handle, f"Page {handle} - Whale", 1, "whale.exe", active)


class ElementIdTests(unittest.TestCase):
    def test_scan_scoped_id_parses_back_to_its_index(self):
        self.assertEqual(_element_index("abc123-e7"), 7)

    def test_bare_number_is_accepted(self):
        self.assertEqual(_element_index("3"), 3)

    def test_nonsense_is_rejected_rather_than_guessed(self):
        for value in ("", "garbage", "abc-ex", None):
            with self.subTest(value=value):
                self.assertIsNone(_element_index(value))


class TranslationTests(unittest.TestCase):
    def test_elements_become_scan_scoped_page_elements(self):
        observation = _screen_observation(1, elements=[_screen_element()])
        page = to_page_observation(observation, 0)
        self.assertEqual(page.status, "observed")
        self.assertEqual(page.elements[0].id, "scan1-e0")
        self.assertEqual(page.elements[0].label, "Learn more")
        self.assertEqual(page.tab_index, 0)

    def test_link_targets_survive_the_translation(self):
        # Chromium exposes an anchor's href through UIA ValuePattern, so the
        # planner's href-based logic keeps working on this driver.
        observation = _screen_observation(
            1, elements=[_screen_element(href="https://example.com/a")],
        )
        element = to_page_observation(observation, 0).elements[0]
        self.assertEqual(element.href, "https://example.com/a")
        self.assertFalse(element.is_ad)
        # The ordinal-result logic tests for an anchor tag.
        self.assertEqual(element.tag, "a")

    def test_paid_placements_are_flagged_from_their_target(self):
        # Measured on a live results page: the sponsored link pointed at
        # googleadservices.com, which is the only signal this driver has.
        observation = _screen_observation(1, elements=[
            _screen_element(href="https://www.googleadservices.com/pagead/aclk?sa=L"),
        ])
        self.assertTrue(to_page_observation(observation, 0).elements[0].is_ad)

    def test_a_link_without_a_target_is_not_called_an_ad(self):
        observation = _screen_observation(1, elements=[_screen_element(href="")])
        self.assertFalse(to_page_observation(observation, 0).elements[0].is_ad)

    def test_unlabeled_elements_are_still_addressable(self):
        observation = _screen_observation(1, elements=[_screen_element(label="")])
        page = to_page_observation(observation, 0)
        self.assertEqual(page.elements[0].label, "(unlabeled)")

    def test_privacy_reject_inside_a_dialog_is_flagged(self):
        observation = _screen_observation(
            1, elements=[_screen_element(label="Reject all", in_dialog=True)],
        )
        element = to_page_observation(observation, 0).elements[0]
        self.assertTrue(element.in_privacy_dialog)
        self.assertTrue(element.is_privacy_dismissal)

    def test_reject_outside_a_dialog_is_not_flagged(self):
        observation = _screen_observation(
            1, elements=[_screen_element(label="Reject all", in_dialog=False)],
        )
        element = to_page_observation(observation, 0).elements[0]
        self.assertFalse(element.is_privacy_dismissal)

    def test_failed_observation_carries_its_message(self):
        page = to_page_observation(
            ScreenPageObservation("cold_tree", message="never woke up"), 0,
        )
        self.assertEqual(page.status, "cold_tree")
        self.assertEqual(page.message, "never woke up")
        self.assertEqual(page.elements, ())

    def test_accessible_image_labels_and_total_count_survive_translation(self):
        observation = ScreenPageObservation(
            "observed", handle=1, title="Hotels", url="https://example.com",
            image_labels=("Ocean-view room", "Pool"), image_count=5,
            scan_id="scan1",
        )
        page = to_page_observation(observation, 0)
        self.assertEqual([item.label for item in page.images], ["Ocean-view room", "Pool"])
        self.assertEqual(page.image_count, 5)


class TabIndexTests(unittest.TestCase):
    def setUp(self):
        self.windows = [_window(100, active=True), _window(200)]
        self.finder = _FakeFinder(self.windows)
        self.screen = _FakeScreenObserver(
            {
                100: _screen_observation(100, url="https://first.example"),
                200: _screen_observation(200, url="https://second.example"),
            },
            active_handle=100,
        )
        self.adapter = ScreenBrowserObserverAdapter(self.screen, self.finder)

    def test_tabs_are_browser_windows_foreground_first(self):
        tabs = self.adapter.list_tabs()
        self.assertEqual([t.index for t in tabs], [0, 1])
        self.assertTrue(tabs[0].is_active)

    def test_observation_is_always_stamped_with_a_real_index(self):
        # BrowserActionPlanner files an observation without an index as an
        # ambiguous fallback, which costs a wasted round later.
        page = self.adapter.describe_page()
        self.assertEqual(page.tab_index, 0)

    def test_a_named_tab_is_observed(self):
        page = self.adapter.describe_page(1)
        self.assertEqual(page.url, "https://second.example")
        self.assertEqual(page.tab_index, 1)

    def test_a_tab_that_does_not_exist_reports_the_window_actually_read(self):
        # Falling back to the active window is fine; labelling that page
        # with the requested number would not be.
        page = self.adapter.describe_page(9)
        self.assertEqual(page.url, "https://first.example")
        self.assertEqual(page.tab_index, 0)

    def test_read_text_uses_headings_and_visible_text(self):
        self.screen._by_handle[100] = ScreenPageObservation(
            "observed", handle=100, title="Example", url="https://first.example",
            headings=("A heading",), text_excerpt="Some body text.",
            scan_id="scan100",
        )
        result = self.adapter.read_text()
        self.assertEqual(result.status, "observed")
        self.assertEqual(result.text, "A heading Some body text.")
        self.assertEqual(result.tab_index, 0)

    def test_default_observation_stays_bound_to_the_same_window_across_steps(self):
        first = self.adapter.describe_page()
        self.assertEqual(first.url, "https://first.example")
        self.finder._windows = [
            _window(100, active=False), _window(200, active=True),
        ]

        second = self.adapter.describe_page()

        self.assertEqual(second.url, "https://first.example")
        self.assertEqual(self.screen.observed[-1], 100)

    def test_binding_moves_only_when_an_explicit_tab_index_is_selected(self):
        self.adapter.describe_page()
        chosen = self.adapter.describe_page(1)
        continued = self.adapter.describe_page()
        self.assertEqual(chosen.url, "https://second.example")
        self.assertEqual(continued.url, "https://second.example")

    def test_prefer_page_binds_the_window_opened_by_desktop_control(self):
        self.finder._windows = [
            _window(100, active=False), _window(200, active=True),
        ]

        self.adapter.prefer_page("https://www.zillow.com/homes/for_rent/")
        page = self.adapter.describe_page()

        self.assertEqual(page.url, "https://second.example")
        self.assertEqual(
            self.adapter._preferred_page_url,
            "https://www.zillow.com/homes/for_rent/",
        )


class PlannerElementLookupTests(unittest.TestCase):
    """The planner must trust the element id over a model-invented tab."""

    def setUp(self):
        self.observation = PageObservation(
            "observed",
            url="https://example.com",
            elements=(PageElement(id="scan1-e0", tag="a", role="link",
                                  label="Learn more"),),
            tab_index=0,
            scan_id="scan1",
        )
        self.state = _ObservationState(observations={0: self.observation})
        self.state.latest_tab_index = 0

    def test_correct_tab_resolves(self):
        tab, observation, element = BrowserActionPlanner._observed_element(
            0, "scan1-e0", self.state,
        )
        self.assertEqual(tab, 0)
        self.assertEqual(element.label, "Learn more")

    def test_hallucinated_tab_number_still_resolves_by_element_id(self):
        # Reproduced live: qwen3:8b asked for "tab 1" with one window open
        # and was told to re-scan a page it had just been shown.
        tab, observation, element = BrowserActionPlanner._observed_element(
            1, "scan1-e0", self.state,
        )
        self.assertEqual(tab, 0)
        self.assertIsNotNone(element)
        self.assertIs(observation, self.observation)

    def test_unknown_element_id_is_still_refused(self):
        tab, observation, element = BrowserActionPlanner._observed_element(
            1, "scan1-e99", self.state,
        )
        self.assertIsNone(element)

    def test_element_id_from_an_older_scan_is_not_matched(self):
        tab, observation, element = BrowserActionPlanner._observed_element(
            0, "scan0-e0", self.state,
        )
        self.assertIsNone(element)

    def test_fallback_observation_is_searched_too(self):
        state = _ObservationState(observations={})
        state.fallback_observation = self.observation
        tab, observation, element = BrowserActionPlanner._observed_element(
            3, "scan1-e0", state,
        )
        self.assertIsNotNone(element)


if __name__ == "__main__":
    unittest.main()
