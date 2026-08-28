import unittest

from tools.computer_control.windows_ui_observer import ControlLookup, WindowInfo
from tools.screen_control.cursor_driver import InputResult
from tools.screen_control.screen_ui_control import ScreenUIControl


class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _ElementInfo:
    def __init__(self, rect, handle=1):
        self.rectangle = _Rect(*rect)
        self.handle = handle


class _Control:
    """A live UIA control the driver will click."""

    def __init__(self, rect=(100, 100, 300, 140), value=""):
        self.element_info = _ElementInfo(rect)
        self.value = value

    def get_value(self):
        return self.value


class _Window:
    def __init__(self, handle=1, title="Spotify Premium", minimized=False):
        self.handle = handle
        self.title = title
        self.minimized = minimized


class _Observer:
    """Serves control lookups, optionally only after the tree "wakes"."""

    def __init__(self, lookup=None, window=None, cold_looks=0):
        self.available = True
        self._lookup = lookup
        self._window = window if window is not None else _Window()
        self.cold_looks = cold_looks
        self.resolve_calls = 0

    def find_window(self, target):
        return self._window

    def resolve_control_by_id(self, window, element_id):
        return self._resolve()

    def resolve_control(self, window, control_name, *, expected_roles=None):
        return self._resolve()

    def _resolve(self):
        self.resolve_calls += 1
        if self.resolve_calls <= self.cold_looks:
            return ControlLookup("not_found", message="nothing there yet")
        return self._lookup

    @staticmethod
    def _safe_text(window):
        return getattr(window, "title", "")


class _Cursor:
    """A cursor whose typing actually changes the field it is typing into.

    The settling verifier deliberately distinguishes "the accessibility tree
    has not caught up" from "the value really did not change", so a fake
    whose value is identical before and after typing is read -- correctly --
    as typing that never landed.
    """

    def __init__(self, *, result=None, field=None):
        self.field = field
        self.available = True
        self.clicks = []
        self.double_clicks = []
        self.typed = []
        self.presses = []
        self.scrolls = []
        self._result = result or InputResult("done")

    def click(self, point):
        self.clicks.append(point)
        return self._result

    def double_click(self, point):
        self.double_clicks.append(point)
        return self._result

    def type_text(self, text):
        self.typed.append(text)
        if self.field is not None and self._result.succeeded:
            self.field.value = text
        return self._result

    def clear_field(self):
        self.presses.append(("clear",))
        if self.field is not None and self._result.succeeded:
            self.field.value = ""
        return self._result

    def press(self, *keys):
        self.presses.append(keys)
        return self._result

    def scroll(self, point, notches):
        self.scrolls.append((point, notches))
        return self._result

    @staticmethod
    def point_is_on_screen(point):
        return 0 <= point[0] < 2560 and 0 <= point[1] < 1440


def _control(
    observer, cursor=None, *, owns_point=True, focused=True,
):
    control = ScreenUIControl(
        observer=observer,
        cursor=cursor or _Cursor(),
        sleeper=lambda seconds: None,
        window_at_point=lambda point: 1 if owns_point else 999,
    )
    control._bring_forward = lambda window: (
        (True, "test") if focused else (False, "would not come forward")
    )
    return control


def _matched(name="Search", role="Edit", rect=(100, 100, 300, 140), value=""):
    return ControlLookup(
        "matched", control=_Control(rect, value), role=role, name=name,
    )


class DoubleClickTests(unittest.TestCase):
    """Playing a list row is a double-click, and only ever that."""

    def test_a_track_row_is_double_clicked_at_its_centre(self):
        observer = _Observer(_matched("Bang Bang", role="Hyperlink"))
        cursor = _Cursor()

        result = _control(observer, cursor).double_click_control(
            "Spotify", "Bang Bang",
        )

        self.assertEqual(result.status, "clicked")
        self.assertEqual(cursor.double_clicks, [(200, 120)])
        # Two single clicks are a different gesture; the app would read them
        # as selecting the row twice, not as playing it.
        self.assertEqual(cursor.clicks, [])

    def test_a_covered_row_is_not_double_clicked(self):
        observer = _Observer(_matched("Bang Bang", role="Hyperlink"))
        cursor = _Cursor()

        result = _control(observer, cursor, owns_point=False).double_click_control(
            "Spotify", "Bang Bang",
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(cursor.double_clicks, [])

    def test_a_committing_row_still_needs_confirmation(self):
        observer = _Observer(_matched("Delete playlist", role="ListItem"))
        cursor = _Cursor()

        result = _control(observer, cursor).double_click_control(
            "Spotify", "Delete playlist",
        )

        self.assertEqual(result.status, "confirmation_required")
        self.assertEqual(cursor.double_clicks, [])

    def test_taking_the_mouse_back_stops_the_double_click(self):
        observer = _Observer(_matched("Bang Bang", role="Hyperlink"))
        cursor = _Cursor(result=InputResult("user_took_over", "You moved it."))

        result = _control(observer, cursor).double_click_control(
            "Spotify", "Bang Bang",
        )

        self.assertEqual(result.status, "user_took_over")


class ClickTests(unittest.TestCase):
    def test_a_resolved_control_is_clicked_at_its_centre(self):
        observer = _Observer(_matched("Play", role="Button"))
        cursor = _Cursor()
        result = _control(observer, cursor).click_control("Spotify", "Play")
        self.assertEqual(result.status, "clicked")
        self.assertEqual(cursor.clicks, [(200, 120)])

    def test_a_committing_control_needs_confirmation_first(self):
        observer = _Observer(_matched("Delete playlist", role="Button"))
        cursor = _Cursor()
        result = _control(observer, cursor).click_control(
            "Spotify", "Delete playlist",
        )
        self.assertEqual(result.status, "confirmation_required")
        self.assertEqual(cursor.clicks, [])

    def test_a_confirmed_committing_control_is_clicked(self):
        observer = _Observer(_matched("Delete playlist", role="Button"))
        cursor = _Cursor()
        result = _control(observer, cursor).click_control(
            "Spotify", "Delete playlist", confirmed=True,
        )
        self.assertEqual(result.status, "clicked")
        self.assertEqual(len(cursor.clicks), 1)

    def test_a_control_with_no_screen_position_is_not_clicked(self):
        # A minimized window reports its controls at (-32000, -32000);
        # clicking there would move the pointer off the desktop.
        observer = _Observer(_matched(rect=(-32000, -32000, -31900, -31960)))
        cursor = _Cursor()
        result = _control(observer, cursor).click_control("Spotify", "Search")
        self.assertEqual(result.status, "not_actionable")
        self.assertEqual(cursor.clicks, [])

    def test_a_covered_control_is_not_clicked(self):
        observer = _Observer(_matched("Play", role="Button"))
        cursor = _Cursor()
        result = _control(observer, cursor, owns_point=False).click_control(
            "Spotify", "Play",
        )
        self.assertEqual(result.status, "blocked")
        self.assertEqual(cursor.clicks, [])

    def test_a_window_that_will_not_come_forward_is_not_clicked_at(self):
        observer = _Observer(_matched("Play", role="Button"))
        cursor = _Cursor()
        result = _control(observer, cursor, focused=False).click_control(
            "Spotify", "Play",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(cursor.clicks, [])

    def test_the_user_taking_over_is_reported_not_swallowed(self):
        observer = _Observer(_matched("Play", role="Button"))
        cursor = _Cursor(
            result=InputResult("user_took_over", "You moved the mouse."),
        )
        result = _control(observer, cursor).click_control("Spotify", "Play")
        self.assertEqual(result.status, "user_took_over")
        self.assertFalse(result.succeeded)

    def test_a_cold_tree_is_retried_before_giving_up(self):
        # CEF apps expose nothing until queried: Spotify measured 25 nodes
        # cold and 1465 warm. One cold look is not evidence of an empty app.
        observer = _Observer(_matched("Play", role="Button"), cold_looks=2)
        result = _control(observer).click_control("Spotify", "Play")
        self.assertEqual(result.status, "clicked")
        self.assertEqual(observer.resolve_calls, 3)

    def test_a_tree_that_never_wakes_reports_not_found(self):
        observer = _Observer(_matched(), cold_looks=99)
        cursor = _Cursor()
        result = _control(observer, cursor).click_control("Spotify", "Play")
        self.assertEqual(result.status, "not_found")
        self.assertEqual(cursor.clicks, [])


class TypingTests(unittest.TestCase):
    def test_typing_clicks_the_field_first_then_types(self):
        # This is the whole point of the driver: the field is clicked
        # directly, so the keystrokes land somewhere known.
        lookup = _matched("Search", role="Edit")
        observer = _Observer(lookup)
        cursor = _Cursor(field=lookup.control)
        result = _control(observer, cursor).type_text(
            "Spotify", "Search", "Blinding",
        )
        self.assertEqual(result.status, "typed")
        self.assertEqual(cursor.clicks, [(200, 120)])
        self.assertIn(("clear",), cursor.presses)
        self.assertEqual(cursor.typed, ["Blinding"])

    def test_a_credential_field_is_refused_outright(self):
        observer = _Observer(_matched("Password", role="Edit"))
        cursor = _Cursor()
        result = _control(observer, cursor).type_text(
            "App", "Password", "hunter2",
        )
        self.assertEqual(result.status, "refused")
        self.assertEqual(cursor.typed, [])

    def test_overlong_text_is_bounded(self):
        lookup = _matched("Search", role="Edit")
        observer = _Observer(lookup)
        cursor = _Cursor(field=lookup.control)
        _control(observer, cursor).type_text("App", "Search", "x" * 5000)
        self.assertEqual(len(cursor.typed[0]), 500)

    def test_submit_presses_enter_after_typing(self):
        lookup = _matched("Search", role="Edit")
        observer = _Observer(lookup)
        cursor = _Cursor(field=lookup.control)
        _control(observer, cursor).type_text(
            "Spotify", "Search", "Blinding", submit=True,
        )
        self.assertIn(("enter",), cursor.presses)

    def test_a_value_that_did_not_land_is_not_reported_as_typed(self):
        # Keystrokes went somewhere else: the field still reads empty.
        observer = _Observer(_matched("Search", role="Edit", value=""))
        result = _control(observer, _Cursor()).type_text(
            "Spotify", "Search", "Blinding",
        )
        self.assertEqual(result.status, "verification_failed")
        self.assertIs(result.verified, False)


class KeyTests(unittest.TestCase):
    def test_press_key_sends_the_chord(self):
        observer = _Observer(_matched())
        cursor = _Cursor()
        result = _control(observer, cursor).press_key("Spotify", "enter")
        self.assertEqual(result.status, "typed")
        self.assertEqual(cursor.presses, [("enter",)])

    def test_press_key_needs_the_window_in_front(self):
        observer = _Observer(_matched())
        cursor = _Cursor()
        result = _control(observer, cursor, focused=False).press_key(
            "Spotify", "enter",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(cursor.presses, [])


class ScrollTests(unittest.TestCase):
    def test_scroll_down_sends_negative_notches(self):
        observer = _Observer(_matched("Results", role="List"))
        cursor = _Cursor()
        result = _control(observer, cursor).scroll_control(
            "Spotify", "Results", "down",
        )
        self.assertEqual(result.status, "scrolled")
        self.assertLess(cursor.scrolls[0][1], 0)

    def test_an_unknown_direction_is_refused(self):
        observer = _Observer(_matched("Results"))
        result = _control(observer).scroll_control("Spotify", "Results", "sideways")
        self.assertEqual(result.status, "refused")


class InterfaceParityTests(unittest.TestCase):
    def test_it_can_stand_in_for_the_invoke_driver(self):
        from tools.computer_control.windows_ui_control import WindowsUIControl

        for name in (
            "focus_window", "click_control", "click_then_type", "type_text",
            "select_option", "scroll_control", "available",
        ):
            with self.subTest(method=name):
                self.assertTrue(hasattr(ScreenUIControl, name))
                self.assertTrue(hasattr(WindowsUIControl, name))


if __name__ == "__main__":
    unittest.main()
