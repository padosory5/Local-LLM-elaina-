"""Cookie walls, promo modals, and screen-reader-only traps.

Three obstacles sit between a freshly opened page and a useful answer, and
none of them are the model's job to reason around:

* a consent wall, which makes everything behind it inert;
* a modal, whose own controls are the only reachable ones while it is up;
* hidden "skip to content" links, which every accessible site parks
  off-screen as the *first* focusable element -- so they were being
  scanned as the most obvious thing to click, and clicking one spent
  Playwright's whole actionability timeout before failing.

The dismissal judgement itself lives in BrowserControl (exact reject-only
label, verified privacy container, ordinary actionability-checked click,
confirmed closed afterwards). These tests cover that classifier and the
planner loop that drives it automatically, so a banner never costs the
model a round -- or hides the page entirely.
"""

import unittest

from brain.browser_action_planner import BrowserActionPlanner
from tools.browser_control.browser_control import (
    BrowserActionResult,
    is_safe_privacy_rejection,
)
from tools.browser_control.browser_observer import PageElement, PageObservation
from tests.test_browser_action_planner import FakeClient, FakeControl, FakeObserver


class PrivacyRejectionLabelTests(unittest.TestCase):
    """A label alone can authorise only the reject half of consent."""

    def test_explicit_rejections_are_recognised(self):
        for label in (
            "Reject all", "Reject", "Decline all", "Refuse all",
            "Only essential", "Only necessary cookies", "Essential only",
            "Continue without", "모두 거부", "거부", "필수만",
        ):
            with self.subTest(label=label):
                self.assertTrue(is_safe_privacy_rejection(label))

    def test_nothing_accept_shaped_is_ever_auto_clicked(self):
        # Declining tracking for the user is defensible; agreeing is not.
        for label in (
            "Accept all", "I agree", "Allow cookies", "Consent",
            "Accept necessary cookies", "동의", "허용", "Sign in",
        ):
            with self.subTest(label=label):
                self.assertFalse(is_safe_privacy_rejection(label))

    def test_an_ordinary_control_is_not_a_consent_control(self):
        for label in ("Close", "X", "Search", "Reject this pull request", ""):
            with self.subTest(label=label):
                self.assertFalse(is_safe_privacy_rejection(label))

    def test_whitespace_does_not_defeat_the_match(self):
        self.assertTrue(is_safe_privacy_rejection("  Reject   all \n"))


def _observation(*labels, tab_index=0, scan_id="scan1", status="observed"):
    return PageObservation(
        status,
        url="https://example.com",
        title="Example",
        elements=tuple(
            PageElement(id=f"{scan_id}-e{i}", tag="button", role="", label=label)
            for i, label in enumerate(labels)
        ),
        tab_index=tab_index,
        scan_id=scan_id,
    )


class _ScriptedObserver(FakeObserver):
    """Returns a different page on each scan, as a real one does after a
    banner is dismissed."""

    def __init__(self, observations):
        super().__init__()
        self._observations = list(observations)
        self.scan_calls = 0

    def describe_page(self, tab_index=None, **kwargs):
        self.scan_calls += 1
        if len(self._observations) > 1:
            return self._observations.pop(0)
        return self._observations[0]


class _DismissingControl(FakeControl):
    def __init__(self, *, result=None, **kwargs):
        super().__init__(**kwargs)
        self._result = result or BrowserActionResult(
            "dismissed_privacy_overlay", "Rejected optional privacy choices.",
            verified=True,
        )
        self.dismiss_calls = []

    def dismiss_privacy_overlay(self, tab_index, element_id, **kwargs):
        self.dismiss_calls.append((tab_index, element_id))
        return self._result


def _planner(observer, control):
    return BrowserActionPlanner(
        client=FakeClient([]), model="qwen3:8b", keep_alive=-1,
        observer=observer, control=control,
    )


class AutomaticDismissalTests(unittest.TestCase):
    def test_a_consent_wall_is_cleared_and_the_page_re_scanned(self):
        observer = _ScriptedObserver([
            _observation("Reject all", "Accept all"),
            _observation("Search", "First result"),
        ])
        control = _DismissingControl()

        observation = _planner(observer, control)._describe_page(None)

        self.assertEqual(control.dismiss_calls, [(0, "scan1-e0")])
        self.assertEqual(observation.dismissed_overlays, ("Reject all",))
        # The returned scan is the page *behind* the banner.
        self.assertEqual(
            [element.label for element in observation.elements],
            ["Search", "First result"],
        )

    def test_a_page_with_no_consent_wall_costs_nothing(self):
        observer = _ScriptedObserver([_observation("Search", "First result")])
        control = _DismissingControl()

        observation = _planner(observer, control)._describe_page(None)

        self.assertEqual(control.dismiss_calls, [])
        self.assertEqual(observation.dismissed_overlays, ())
        self.assertEqual(observer.scan_calls, 1)

    def test_a_refused_dismissal_leaves_the_banner_visible_to_the_model(self):
        # Pretending a banner is gone when it isn't would be worse than
        # showing it -- the model must be able to see and report it.
        observer = _ScriptedObserver([_observation("Reject all")])
        control = _DismissingControl(
            result=BrowserActionResult(
                "refused", "That reject control is not inside a verified privacy dialog.",
            ),
        )

        observation = _planner(observer, control)._describe_page(None)

        self.assertEqual(len(control.dismiss_calls), 1)
        self.assertEqual(observation.dismissed_overlays, ())
        self.assertEqual(
            [element.label for element in observation.elements], ["Reject all"],
        )

    def test_an_unverified_dismissal_is_not_reported_as_cleared(self):
        observer = _ScriptedObserver([_observation("Reject all")])
        control = _DismissingControl(
            result=BrowserActionResult(
                "verification_failed", "The privacy dialog is still visible.",
                verified=False,
            ),
        )

        observation = _planner(observer, control)._describe_page(None)

        self.assertEqual(observation.dismissed_overlays, ())

    def test_dismissal_is_bounded_on_a_page_that_keeps_producing_banners(self):
        observer = _ScriptedObserver([_observation("Reject all")])
        control = _DismissingControl()

        observation = _planner(observer, control)._describe_page(None)

        self.assertEqual(len(control.dismiss_calls), 2)
        self.assertEqual(len(observation.dismissed_overlays), 2)

    def test_a_failed_scan_is_never_dismissed_against(self):
        observer = _ScriptedObserver([_observation(status="unavailable")])
        control = _DismissingControl()

        observation = _planner(observer, control)._describe_page(None)

        self.assertEqual(control.dismiss_calls, [])
        self.assertEqual(observation.status, "unavailable")

    def test_a_control_without_the_dismissal_api_still_scans(self):
        # This is a real runtime path, not just a test double: every
        # browser call reaches BrowserControl through BrowserService's
        # facade, and the facade was missing this method entirely -- so
        # consent handling silently never ran in the application while
        # passing every direct-observer test. A missing method must
        # degrade to "banner left visible", never to a crashed scan.
        observer = _ScriptedObserver([_observation("Reject all", "Search")])

        observation = _planner(observer, FakeControl())._describe_page(None)

        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.dismissed_overlays, ())
        self.assertEqual(
            [element.label for element in observation.elements],
            ["Reject all", "Search"],
        )

    def test_the_service_facade_exposes_the_dismissal_call(self):
        # The bug above, pinned at its source.
        from tools.browser_control.browser_service import _BrowserControlFacade

        self.assertTrue(
            callable(getattr(_BrowserControlFacade, "dismiss_privacy_overlay", None))
        )


class ScanVisibilityTests(unittest.TestCase):
    """The scan script itself, checked through its Python-side contract."""

    def test_the_scan_script_survives_python_escaping(self):
        from tools.browser_control.browser_observer import _SCAN_SCRIPT

        # A broken \\b or \\s would leave a literal control character and
        # silently change what the regex matches.
        self.assertNotIn("\x08", _SCAN_SCRIPT)
        self.assertIn("clipHidesEverything", _SCAN_SCRIPT)
        self.assertIn("inDialog", _SCAN_SCRIPT)
        self.assertIn("inMain", _SCAN_SCRIPT)

    def test_screen_reader_clip_is_measured_not_pattern_matched(self):
        # Wikipedia hides its skip link with rect(0,0,0,0) and Google with
        # rect(1px,1px,1px,1px); both must be treated as hidden, so the
        # script measures the clip box instead of matching one spelling.
        from tools.browser_control.browser_observer import _SCAN_SCRIPT

        self.assertIn("(right - left) <= 1", _SCAN_SCRIPT)
        self.assertIn("(bottom - top) <= 1", _SCAN_SCRIPT)

    def test_off_screen_and_transparent_elements_are_excluded(self):
        from tools.browser_control.browser_observer import _SCAN_SCRIPT

        self.assertIn("rect.right <= 0", _SCAN_SCRIPT)
        self.assertIn("style.opacity === '0'", _SCAN_SCRIPT)

    def test_labels_are_computed_only_for_the_ranked_top_slice(self):
        # computeLabel reads innerText and forces a layout; running it over
        # every candidate cost 10.7s on a dense results page for elements
        # the 120-element cap was about to discard anyway.
        from tools.browser_control.browser_observer import _SCAN_SCRIPT

        self.assertIn("LABEL_BUDGET", _SCAN_SCRIPT)
        # Ranking must happen before labelling, or the budget would drop
        # elements that mattered.
        self.assertLess(
            _SCAN_SCRIPT.index("shortlist.sort"),
            _SCAN_SCRIPT.index("const label = computeLabel(item.el)"),
        )


if __name__ == "__main__":
    unittest.main()
