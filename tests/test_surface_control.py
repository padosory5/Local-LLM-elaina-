import unittest

from tools.browser_control.browser_control import BrowserActionResult
from tools.browser_control.browser_observer import PageElement, PageObservation, PageTextResult
from tools.surface_control import (
    BrowserSurfaceAdapter,
    DesktopSurfaceAdapter,
    SurfaceActionResult,
)
from tools.computer_control.windows_ui_control import UIActionResult
from tools.computer_control.windows_ui_observer import ControlInfo, WindowObservation


class _FakeWindowsObserver:
    def __init__(self, *, describe_result=None):
        self.describe_result = describe_result
        self.describe_calls = []

    def describe_window(self, window):
        self.describe_calls.append(window)
        return self.describe_result


class _FakeWindowsControl:
    def __init__(self, *, result=None):
        self.result = result
        self.click_calls = []
        self.type_calls = []
        self.select_calls = []
        self.scroll_calls = []

    def click_control(self, title_query, control_name, *, confirmed=False):
        self.click_calls.append((title_query, control_name, confirmed))
        return self.result

    def type_text(self, title_query, control_name, text):
        self.type_calls.append((title_query, control_name, text))
        return self.result

    def select_option(self, title_query, control_name, option):
        self.select_calls.append((title_query, control_name, option))
        return self.result

    def scroll_control(self, title_query, control_name, direction):
        self.scroll_calls.append((title_query, control_name, direction))
        return self.result


class _FakeBrowserObserver:
    def __init__(self, *, describe_result=None, text_result=None):
        self.describe_result = describe_result
        self.text_result = text_result
        self.read_text_calls = []

    def describe_page(self, tab_index=None, *, query=""):
        return self.describe_result

    def read_text(self, tab_index=None):
        self.read_text_calls.append(tab_index)
        return self.text_result


class _FakeBrowserControl:
    def __init__(self, *, result=None):
        self.result = result
        self.click_calls = []
        self.fill_calls = []
        self.select_calls = []
        self.scroll_calls = []

    def click(self, tab_index, element_id, *, confirmed=False, **kwargs):
        self.click_calls.append((tab_index, element_id, confirmed, kwargs))
        return self.result

    def fill(self, tab_index, element_id, text, *, confirmed=False, **kwargs):
        self.fill_calls.append((tab_index, element_id, text, confirmed, kwargs))
        return self.result

    def select_option(self, tab_index, element_id, option, **kwargs):
        self.select_calls.append((tab_index, element_id, option, kwargs))
        return self.result

    def scroll_to(self, tab_index, element_id, **kwargs):
        self.scroll_calls.append((tab_index, element_id, kwargs))
        return self.result


class SurfaceActionResultTests(unittest.TestCase):
    def test_succeeded_status_set(self):
        succeeding = {"clicked", "typed", "filled", "selected", "scrolled"}
        excluded = {"focused", "navigated", "confirmation_required", "failed", "not_found"}
        for status in succeeding:
            self.assertTrue(SurfaceActionResult(status, "").succeeded, status)
        for status in excluded:
            self.assertFalse(SurfaceActionResult(status, "").succeeded, status)


class DesktopSurfaceAdapterTests(unittest.TestCase):
    def test_describe_maps_observed_controls_and_echoes_window_as_token(self):
        observer = _FakeWindowsObserver(
            describe_result=WindowObservation(
                "observed",
                title="Notepad",
                controls=(
                    ControlInfo(
                        role="Edit", name="Text editor", value="hello",
                        is_visible=True, is_enabled=True, is_actionable=True,
                    ),
                    ControlInfo(role="Button", name="Save", is_enabled=False),
                ),
                truncated=True,
            ),
        )
        adapter = DesktopSurfaceAdapter(observer=observer, control=_FakeWindowsControl())

        observation = adapter.describe("Notepad")

        self.assertEqual(observer.describe_calls, ["Notepad"])
        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.title, "Notepad")
        self.assertTrue(observation.truncated)
        self.assertEqual(observation.surface_token, "Notepad")
        self.assertEqual(len(observation.elements), 2)
        first = observation.elements[0]
        self.assertEqual(first.role, "Edit")
        self.assertEqual(first.label, "Text editor")
        self.assertEqual(first.value, "hello")
        self.assertEqual(first.element_ref, "Text editor")
        self.assertEqual(first.identity_kind, "fuzzy_name")
        self.assertFalse(observation.elements[1].is_enabled)

    def test_describe_passes_through_non_observed_status(self):
        observer = _FakeWindowsObserver(
            describe_result=WindowObservation(
                "not_found", title="Ghost", message="I couldn't find that window.",
            ),
        )
        adapter = DesktopSurfaceAdapter(observer=observer, control=_FakeWindowsControl())

        observation = adapter.describe("Ghost")

        self.assertEqual(observation.status, "not_found")
        self.assertEqual(observation.elements, ())
        self.assertEqual(observation.message, "I couldn't find that window.")
        self.assertEqual(observation.surface_token, "Ghost")

    def test_click_translates_call_and_result(self):
        control = _FakeWindowsControl(
            result=UIActionResult(
                "clicked", "Clicked Save.", control_name="Save",
                verified=True, evidence="toggled",
            ),
        )
        adapter = DesktopSurfaceAdapter(observer=_FakeWindowsObserver(), control=control)
        observation = self._observation_with_token("Notepad")
        element = observation.elements[0]

        result = adapter.click(observation, element, confirmed=True)

        self.assertEqual(control.click_calls, [("Notepad", "Save", True)])
        self.assertEqual(result.status, "clicked")
        self.assertEqual(result.element_ref, "Save")
        self.assertTrue(result.verified)
        self.assertEqual(result.evidence, "toggled")

    def test_fill_select_scroll_translate_calls(self):
        control = _FakeWindowsControl(
            result=UIActionResult("typed", "Typed.", control_name="Save"),
        )
        adapter = DesktopSurfaceAdapter(observer=_FakeWindowsObserver(), control=control)
        observation = self._observation_with_token("Notepad")
        element = observation.elements[0]

        adapter.fill(observation, element, "hello")
        adapter.select(observation, element, "Option A")
        adapter.scroll(observation, element, "down")

        self.assertEqual(control.type_calls, [("Notepad", "Save", "hello")])
        self.assertEqual(control.select_calls, [("Notepad", "Save", "Option A")])
        self.assertEqual(control.scroll_calls, [("Notepad", "Save", "down")])

    def test_read_text_is_unsupported_when_observed(self):
        adapter = DesktopSurfaceAdapter(
            observer=_FakeWindowsObserver(), control=_FakeWindowsControl(),
        )
        observation = self._observation_with_token("Notepad")

        result = adapter.read_text(observation)

        self.assertEqual(result.status, "unsupported")

    def test_read_text_passes_through_non_observed_status(self):
        adapter = DesktopSurfaceAdapter(
            observer=_FakeWindowsObserver(), control=_FakeWindowsControl(),
        )
        observer = _FakeWindowsObserver(
            describe_result=WindowObservation("not_found", message="gone"),
        )
        adapter = DesktopSurfaceAdapter(observer=observer, control=_FakeWindowsControl())
        observation = adapter.describe("Ghost")

        result = adapter.read_text(observation)

        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.message, "gone")

    @staticmethod
    def _observation_with_token(window):
        observer = _FakeWindowsObserver(
            describe_result=WindowObservation(
                "observed", title=window,
                controls=(ControlInfo(role="Button", name="Save"),),
            ),
        )
        adapter = DesktopSurfaceAdapter(observer=observer, control=_FakeWindowsControl())
        return adapter.describe(window)


class BrowserSurfaceAdapterTests(unittest.TestCase):
    def test_describe_maps_observed_elements_and_resolves_surface_token(self):
        observer = _FakeBrowserObserver(
            describe_result=PageObservation(
                "observed",
                url="https://example.com/search",
                title="Search",
                elements=(
                    PageElement(
                        id="scan1-e0", tag="a", role="", label="Images",
                        href="https://example.com/images",
                    ),
                    PageElement(
                        id="scan1-e1", tag="button", role="button",
                        label="Search", disabled=True,
                    ),
                ),
                truncated=True,
                tab_index=2,
                scan_id="scan1",
            ),
        )
        adapter = BrowserSurfaceAdapter(observer=observer, control=_FakeBrowserControl())

        # Pass tab_index=None (unresolved) -- the surface_token must come
        # from the *resolved* observation.tab_index, not this raw input.
        observation = adapter.describe(None, query="images")

        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.url, "https://example.com/search")
        self.assertEqual(observation.scan_id, "scan1")
        self.assertTrue(observation.truncated)
        self.assertEqual(observation.surface_token, 2)
        self.assertEqual(len(observation.elements), 2)
        first = observation.elements[0]
        self.assertEqual(first.role, "a")  # falls back to tag when role is blank
        self.assertEqual(first.label, "Images")
        self.assertEqual(first.href, "https://example.com/images")
        self.assertEqual(first.element_ref, "scan1-e0")
        self.assertEqual(first.identity_kind, "scan_id")
        self.assertTrue(first.is_enabled)
        self.assertFalse(observation.elements[1].is_enabled)

    def test_describe_passes_through_non_observed_status(self):
        observer = _FakeBrowserObserver(
            describe_result=PageObservation(
                "not_found", message="I couldn't determine the active tab.",
            ),
        )
        adapter = BrowserSurfaceAdapter(observer=observer, control=_FakeBrowserControl())

        observation = adapter.describe(None)

        self.assertEqual(observation.status, "not_found")
        self.assertEqual(observation.elements, ())
        self.assertIsNone(observation.surface_token)

    def test_click_translates_call_with_staleness_metadata(self):
        control = _FakeBrowserControl(
            result=BrowserActionResult(
                "clicked", "Clicked Images.", element_id="scan1-e0",
                verified=True, evidence="url changed",
            ),
        )
        adapter = BrowserSurfaceAdapter(observer=_FakeBrowserObserver(), control=control)
        observation, element = self._observation_and_element()

        result = adapter.click(observation, element, confirmed=True)

        self.assertEqual(len(control.click_calls), 1)
        tab_index, element_id, confirmed, kwargs = control.click_calls[0]
        self.assertEqual(tab_index, 2)
        self.assertEqual(element_id, "scan1-e0")
        self.assertTrue(confirmed)
        self.assertEqual(
            kwargs,
            {
                "expected_label": "Images",
                "expected_url": "https://example.com/search",
                "expected_scan_id": "scan1",
                "expected_href": "https://example.com/images",
            },
        )
        self.assertEqual(result.status, "clicked")
        self.assertEqual(result.element_ref, "scan1-e0")
        self.assertTrue(result.verified)

    def test_fill_select_scroll_translate_calls(self):
        control = _FakeBrowserControl(
            result=BrowserActionResult("filled", "Filled.", element_id="scan1-e0"),
        )
        adapter = BrowserSurfaceAdapter(observer=_FakeBrowserObserver(), control=control)
        observation, element = self._observation_and_element()

        adapter.fill(observation, element, "hello", confirmed=True)
        adapter.select(observation, element, "Option A")
        adapter.scroll(observation, element)

        fill_call = control.fill_calls[0]
        self.assertEqual(fill_call[:3], (2, "scan1-e0", "hello"))
        self.assertTrue(fill_call[3])
        select_call = control.select_calls[0]
        self.assertEqual(select_call[:3], (2, "scan1-e0", "Option A"))
        scroll_call = control.scroll_calls[0]
        self.assertEqual(scroll_call[:2], (2, "scan1-e0"))

    def test_read_text_passes_through_page_text_result(self):
        observer = _FakeBrowserObserver(
            describe_result=PageObservation(
                "observed",
                url="https://example.com/search",
                title="Search",
                elements=(
                    PageElement(
                        id="scan1-e0", tag="a", role="", label="Images",
                        href="https://example.com/images",
                    ),
                ),
                tab_index=2,
                scan_id="scan1",
            ),
            text_result=PageTextResult(
                "observed", url="https://example.com", title="Example",
                text="hello world",
            ),
        )
        adapter = BrowserSurfaceAdapter(observer=observer, control=_FakeBrowserControl())
        observation, _ = self._observation_and_element(observer=observer)

        result = adapter.read_text(observation)

        self.assertEqual(observer.read_text_calls, [2])
        self.assertEqual(result.status, "observed")
        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.title, "Example")

    def test_read_text_short_circuits_on_non_observed_observation(self):
        observer = _FakeBrowserObserver()
        adapter = BrowserSurfaceAdapter(observer=observer, control=_FakeBrowserControl())
        from tools.surface_control import SurfaceObservation

        observation = SurfaceObservation("not_found", message="gone")

        result = adapter.read_text(observation)

        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.message, "gone")
        self.assertEqual(observer.read_text_calls, [])

    @staticmethod
    def _observation_and_element(*, observer=None):
        observer = observer or _FakeBrowserObserver(
            describe_result=PageObservation(
                "observed",
                url="https://example.com/search",
                title="Search",
                elements=(
                    PageElement(
                        id="scan1-e0", tag="a", role="", label="Images",
                        href="https://example.com/images",
                    ),
                ),
                tab_index=2,
                scan_id="scan1",
            ),
        )
        adapter = BrowserSurfaceAdapter(observer=observer, control=_FakeBrowserControl())
        observation = adapter.describe(None)
        return observation, observation.elements[0]


if __name__ == "__main__":
    unittest.main()
