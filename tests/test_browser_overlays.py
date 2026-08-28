"""Privacy overlays preserve the observe-then-act safety boundary."""

import unittest

from brain.browser_action_planner import BrowserActionPlanner, _ObservationState
from tools.browser_control.browser_connection import BrowserConnectionResult
from tools.browser_control.browser_control import BrowserActionResult
from tools.browser_control.browser_observer import (
    BrowserObserver,
    PageElement,
    PageObservation,
)


class _ReadOnlyPage:
    def __init__(self, elements):
        self.url = "https://example.com"
        self._elements = elements
        self.scripts = []

    def title(self):
        return "Example"

    def evaluate(self, script):
        self.scripts.append(script)
        if "__ELAINA_CONTENT_SUMMARY__" in script:
            return {
                "marker": "__ELAINA_CONTENT_SUMMARY__",
                "headings": ["Example"],
                "text": "Cookie choices and page content",
                "textLength": 31,
                "images": [],
                "imageCount": 0,
            }
        return self._elements


class _FakeContext:
    def __init__(self, page):
        self.pages = [page]


class _FakeBrowser:
    def __init__(self, page):
        self.contexts = [_FakeContext(page)]


class _FakeConnection:
    def __init__(self, page):
        self.browser = _FakeBrowser(page)

    def connect(self, *, allow_isolated_launch=False):
        return BrowserConnectionResult("connected", browser=self.browser)


def _observer_for(page):
    return BrowserObserver(connection=_FakeConnection(page))


class ReadOnlyOverlayObservationTests(unittest.TestCase):
    def test_observer_marks_a_verified_reject_candidate_without_clicking(self):
        page = _ReadOnlyPage([{
            "id": "scan-e0", "tag": "button", "role": "", "type": "button",
            "label": "Reject all", "disabled": False, "inDialog": True,
            "inPrivacyDialog": True, "isPrivacyDismissal": True,
        }])

        observation = _observer_for(page).describe_page()

        self.assertEqual(observation.status, "observed")
        self.assertTrue(observation.blocking_dialog)
        self.assertTrue(observation.elements[0].is_privacy_dismissal)
        self.assertEqual(observation.dismissed_overlays, ())
        self.assertTrue(page.scripts)
        self.assertTrue(all(".click(" not in script for script in page.scripts))

    def test_accept_is_visible_but_never_marked_as_safe_rejection(self):
        page = _ReadOnlyPage([{
            "id": "scan-e0", "tag": "button", "role": "", "type": "button",
            "label": "Accept all", "disabled": False, "inDialog": True,
            "inPrivacyDialog": True, "isPrivacyDismissal": False,
        }])

        observation = _observer_for(page).describe_page()

        self.assertTrue(observation.blocking_dialog)
        self.assertFalse(observation.elements[0].is_privacy_dismissal)


class _PlannerObserver:
    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = 0

    def describe_page(self, tab_index=None, *, query=""):
        self.calls += 1
        return self.observations.pop(0)


class _PlannerControl:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def dismiss_privacy_overlay(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _privacy_observation(*, candidates=1):
    elements = tuple(
        PageElement(
            id=f"scan-e{index}", tag="button", role="",
            label="Reject all" if index == 0 else "Only essential",
            in_dialog=True, in_privacy_dialog=True,
            is_privacy_dismissal=True,
        )
        for index in range(candidates)
    )
    return PageObservation(
        "observed", url="https://example.com", title="Example",
        elements=elements, tab_index=0, scan_id="scan", blocking_dialog=True,
    )


class PlannerPrivacyHandlingTests(unittest.TestCase):
    def test_one_verified_candidate_uses_control_then_rescans(self):
        initial = _privacy_observation()
        refreshed = PageObservation(
            "observed", url="https://example.com", title="Example",
            elements=(PageElement("new-e0", "a", "", "Products"),),
            tab_index=0, scan_id="new-scan", text_excerpt="Products and prices",
        )
        observer = _PlannerObserver([initial, refreshed])
        control = _PlannerControl(BrowserActionResult(
            "dismissed_privacy_overlay", "Rejected optional privacy choices.",
            verified=True,
        ))
        planner = BrowserActionPlanner(
            client=object(), model="test", keep_alive=0,
            observer=observer, control=control,
        )

        digest = planner._post_navigation_digest(_ObservationState(observations={}))

        self.assertEqual(len(control.calls), 1)
        self.assertEqual(observer.calls, 2)
        self.assertIn("Rejected optional privacy choices", digest)
        self.assertIn("Products and prices", digest)

    def test_ambiguous_reject_candidates_are_described_not_clicked(self):
        initial = _privacy_observation(candidates=2)
        observer = _PlannerObserver([initial])
        control = _PlannerControl(BrowserActionResult(
            "dismissed_privacy_overlay", "should not run", verified=True,
        ))
        planner = BrowserActionPlanner(
            client=object(), model="test", keep_alive=0,
            observer=observer, control=control,
        )

        digest = planner._post_navigation_digest(_ObservationState(observations={}))

        self.assertEqual(control.calls, [])
        self.assertIn("a dialog is open", digest)


if __name__ == "__main__":
    unittest.main()
