import unittest

from tools.screen_browser.browser_window import BrowserWindow
from tools.screen_browser.page_observer import ScreenPageObservation, ScreenPageObserver


class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _RawElement:
    """Stands in for IUIAutomationElement (ARIA role + value property)."""

    def __init__(self, aria="", value=""):
        self.CurrentAriaRole = aria
        self._value = value

    def GetCurrentPropertyValue(self, property_id):
        return self._value


class _Info:
    """A fake UIAElementInfo: what page_observer actually reads."""

    def __init__(
        self,
        control_type="Custom",
        name="",
        *,
        aria="",
        value="",
        rect=(0, 0, 10, 10),
        enabled=True,
        children=(),
    ):
        self.control_type = control_type
        self.name = name
        self.rectangle = _Rect(*rect)
        self.enabled = enabled
        self.element = _RawElement(aria, value)
        self._children = list(children)

    def children(self):
        return list(self._children)


class _Wrapper:
    """A fake pywinauto window wrapper."""

    def __init__(self, document=None, edits=()):
        self._document = document
        self._edits = list(edits)

    def descendants(self, control_type=None):
        if control_type == "Document":
            return [_Node(self._document)] if self._document else []
        if control_type == "Edit":
            return [_Node(edit) for edit in self._edits]
        return []


class _Node:
    def __init__(self, info):
        self.element_info = info


class _Desktop:
    def __init__(self, wrapper):
        self._wrapper = wrapper

    def window(self, handle=None):
        return self._wrapper


VIEWPORT = (0, 0, 1000, 800)


def _window(handle=1):
    return BrowserWindow(handle, "Page - Whale", 99, "whale.exe", True, (0, 0, 1000, 800))


def _observer(document, edits=(), sleeps=None):
    wrapper = _Wrapper(document, edits)
    return ScreenPageObserver(
        desktop=_Desktop(wrapper),
        sleeper=(sleeps.append if sleeps is not None else (lambda seconds: None)),
    )


def _document(*children, name="Example Page"):
    return _Info("Document", name, aria="document", rect=VIEWPORT, children=children)


class ObservationTests(unittest.TestCase):
    def test_interactive_elements_are_indexed_with_click_points(self):
        document = _document(
            _Info("Button", "Search", aria="button", rect=(100, 100, 200, 140)),
            _Info("Hyperlink", "Learn more", aria="link", rect=(100, 200, 300, 230)),
        )
        result = _observer(document).observe(_window())
        self.assertEqual(result.status, "observed")
        self.assertEqual([e.index for e in result.elements], [0, 1])
        self.assertEqual([e.role for e in result.elements], ["button", "link"])
        self.assertEqual(result.elements[0].click_point, (150, 120))
        self.assertEqual(result.elements[1].label, "Learn more")

    def test_aria_role_wins_over_control_type(self):
        # Chromium reports the authored role; a div styled as a button is
        # a Group by control type but role=button to a screen reader.
        document = _document(
            _Info("Group", "Buy", aria="button", rect=(10, 10, 60, 40)),
        )
        result = _observer(document).observe(_window())
        self.assertEqual([e.role for e in result.elements], ["button"])

    def test_control_type_is_the_fallback_without_aria(self):
        document = _document(
            _Info("Button", "Go", aria="", rect=(10, 10, 60, 40)),
        )
        result = _observer(document).observe(_window())
        self.assertEqual([e.role for e in result.elements], ["button"])

    def test_offscreen_elements_are_dropped(self):
        # A real YouTube skip link measured at y=-960: a valid rectangle
        # that is nowhere the pointer should ever go.
        document = _document(
            _Info("Button", "Skip navigation", aria="button",
                  rect=(100, -1000, 300, -960)),
            _Info("Button", "Visible", aria="button", rect=(100, 100, 200, 140)),
        )
        result = _observer(document).observe(_window())
        self.assertEqual([e.label for e in result.elements], ["Visible"])

    def test_zero_sized_elements_are_dropped(self):
        document = _document(
            _Info("Button", "Hidden", aria="button", rect=(50, 50, 50, 50)),
        )
        self.assertEqual(_observer(document).observe(_window()).elements, ())

    def test_duplicate_overlapping_elements_are_collapsed(self):
        document = _document(
            _Info("Hyperlink", "Open", aria="link", rect=(100, 100, 200, 140)),
            _Info("Hyperlink", "Open", aria="link", rect=(101, 101, 201, 141)),
        )
        result = _observer(document).observe(_window())
        self.assertEqual(len(result.elements), 1)

    def test_disabled_state_is_reported_not_hidden(self):
        document = _document(
            _Info("Button", "Next", aria="button", rect=(10, 10, 60, 40),
                  enabled=False),
        )
        element = _observer(document).observe(_window()).elements[0]
        self.assertTrue(element.disabled)
        self.assertIn("[disabled]", element.display)

    def test_text_field_value_is_read_back(self):
        document = _document(
            _Info("Edit", "Search", aria="textbox", value="laptops",
                  rect=(10, 10, 200, 40)),
        )
        element = _observer(document).observe(_window()).elements[0]
        self.assertEqual(element.value, "laptops")

    def test_headings_are_collected_separately(self):
        document = _document(
            _Info("Text", "Best laptops of 2026", aria="heading",
                  rect=(10, 10, 400, 40)),
            _Info("Button", "Filter", aria="button", rect=(10, 60, 80, 90)),
        )
        result = _observer(document).observe(_window())
        self.assertEqual(result.headings, ("Best laptops of 2026",))
        self.assertEqual([e.label for e in result.elements], ["Filter"])

    def test_serialized_state_is_kept_out_of_page_text(self):
        # A live YouTube page exposed a player-state JSON blob as an
        # accessible name; reporting it as "visible page text" is noise.
        document = _document(
            _Info("Text", '{"mode":"full","isActive":true}', aria="text",
                  rect=(10, 10, 400, 40)),
            _Info("Text", "A real sentence of page text.", aria="text",
                  rect=(10, 50, 400, 80)),
        )
        result = _observer(document).observe(_window())
        self.assertEqual(result.text_excerpt, "A real sentence of page text.")

    def test_dialog_controls_are_listed_first_and_flagged(self):
        dialog = _Info(
            "Pane", "Cookie notice", aria="dialog", rect=(200, 200, 800, 500),
            children=[
                _Info("Button", "Reject all", aria="button",
                      rect=(220, 400, 320, 440)),
            ],
        )
        document = _document(
            _Info("Hyperlink", "Behind the dialog", aria="link",
                  rect=(10, 10, 100, 40)),
            dialog,
        )
        result = _observer(document).observe(_window())
        self.assertTrue(result.blocking_dialog)
        self.assertEqual(result.elements[0].label, "Reject all")
        self.assertTrue(result.elements[0].in_dialog)
        self.assertEqual([e.index for e in result.elements], [0, 1])

    def test_url_comes_from_the_address_bar(self):
        document = _document(_Info("Button", "x", aria="button", rect=(1, 1, 9, 9)))
        edits = [_Info("Edit", "Address bar", value="example.com/page?q=1")]
        result = _observer(document, edits).observe(_window())
        self.assertEqual(result.url, "https://example.com/page?q=1")

    def test_full_scheme_in_address_bar_is_preserved(self):
        document = _document(_Info("Button", "x", aria="button", rect=(1, 1, 9, 9)))
        edits = [_Info("Edit", "Address bar", value="https://example.com/a")]
        self.assertEqual(
            _observer(document, edits).observe(_window()).url,
            "https://example.com/a",
        )

    def test_search_text_in_the_omnibox_is_not_treated_as_a_url(self):
        document = _document(_Info("Button", "x", aria="button", rect=(1, 1, 9, 9)))
        edits = [_Info("Edit", "Address bar", value="best laptops 2026")]
        self.assertEqual(_observer(document, edits).observe(_window()).url, "")


class ColdTreeTests(unittest.TestCase):
    def test_missing_document_retries_then_reports_cold_tree(self):
        sleeps = []
        result = _observer(None, sleeps=sleeps).observe(_window())
        self.assertEqual(result.status, "cold_tree")
        # Retried rather than declaring the page empty on first look.
        self.assertTrue(sleeps)
        self.assertIn("accessibility tree", result.message)

    def test_cold_tree_is_never_reported_as_an_empty_page(self):
        result = _observer(None).observe(_window())
        self.assertNotEqual(result.status, "observed")
        self.assertEqual(result.elements, ())


class WindowSelectionTests(unittest.TestCase):
    def test_no_browser_open_is_reported(self):
        observer = ScreenPageObserver(desktop=_Desktop(_Wrapper()))
        observer.finder = _NoWindows()
        result = observer.observe()
        self.assertEqual(result.status, "no_browser")
        self.assertIn("No browser window", result.message)

    def test_ambiguous_windows_ask_rather_than_guess(self):
        observer = ScreenPageObserver(desktop=_Desktop(_Wrapper()))
        observer.finder = _AmbiguousWindows()
        result = observer.observe()
        self.assertEqual(result.status, "no_browser")
        self.assertIn("none has focus", result.message)


class _NoWindows:
    def active_window(self):
        return None

    def list_windows(self):
        return ()

    def window_for_handle(self, handle):
        return None


class _AmbiguousWindows(_NoWindows):
    def list_windows(self):
        return (
            BrowserWindow(1, "A - Whale", 1, "whale.exe"),
            BrowserWindow(2, "B - Whale", 1, "whale.exe"),
        )


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.document = _document(
            _Info("Button", "Search", aria="button", rect=(100, 100, 200, 140)),
            _Info("Hyperlink", "Learn more", aria="link", rect=(100, 200, 300, 230)),
        )
        self.observer = _observer(self.document)
        self.observation = self.observer.observe(_window())

    def test_matching_index_and_label_resolves(self):
        lookup = self.observer.resolve(
            self.observation, 1, expected_label="Learn more",
        )
        self.assertEqual(lookup.status, "resolved")
        self.assertEqual(lookup.element.label, "Learn more")

    def test_changed_label_at_the_same_index_is_refused(self):
        lookup = self.observer.resolve(
            self.observation, 1, expected_label="Buy now",
        )
        self.assertEqual(lookup.status, "changed")
        self.assertIn("did not click it", lookup.message)

    def test_unknown_index_is_refused(self):
        lookup = self.observer.resolve(self.observation, 9)
        self.assertEqual(lookup.status, "unknown_index")

    def test_observation_from_another_scan_is_refused(self):
        stale = ScreenPageObservation("observed", scan_id="notarealscan")
        lookup = self.observer.resolve(stale, 0)
        self.assertEqual(lookup.status, "stale_scan")

    def test_older_scan_id_still_resolves_when_the_label_confirms_identity(self):
        # Acting always re-scans, so the caller's scan id is expected to be
        # older. The label is what proves the index still means the same
        # element -- requiring the ids to match would refuse every click.
        lookup = self.observer.resolve(
            self.observation, 0,
            expected_label="Search", expected_scan_id="anolderscan",
        )
        self.assertEqual(lookup.status, "resolved")

    def test_older_scan_id_without_a_label_is_refused(self):
        lookup = self.observer.resolve(
            self.observation, 0, expected_scan_id="anolderscan",
        )
        self.assertEqual(lookup.status, "stale_scan")


if __name__ == "__main__":
    unittest.main()
