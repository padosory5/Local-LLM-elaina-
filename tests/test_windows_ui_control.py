import unittest

from tools.computer_control.windows_ui_control import (
    WindowsUIControl,
    is_committing_control,
    is_credential_field,
)
from tools.computer_control.windows_ui_observer import WindowInfo, WindowsUIObserver


_UNCHANGED = object()


class _FakeElementInfo:
    def __init__(self, control_type, name, *, visible=True, enabled=True):
        self.control_type = control_type
        self.name = name
        self.visible = visible
        self.enabled = enabled


class _FakeControl:
    def __init__(
        self,
        control_type,
        name,
        *,
        invoke_raises=False,
        visible=True,
        enabled=True,
        value="",
        value_after_typing=_UNCHANGED,
        selection_after_select=_UNCHANGED,
    ):
        self.element_info = _FakeElementInfo(
            control_type, name, visible=visible, enabled=enabled,
        )
        self.focused = False
        self.invoked = False
        self.clicked = False
        self.typed_text = None
        self.selected_option = None
        self.scrolled = None
        self._invoke_raises = invoke_raises
        self._value = value
        self._value_after_typing = value_after_typing
        self._selection_after_select = selection_after_select

    def is_visible(self):
        return self.element_info.visible

    def is_enabled(self):
        return self.element_info.enabled

    def set_focus(self):
        self.focused = True

    def invoke(self):
        if self._invoke_raises:
            raise RuntimeError("no Invoke pattern on this control")
        self.invoked = True

    def click_input(self):
        self.clicked = True

    def type_keys(self, text, with_spaces=True, with_tabs=False, pause=None):
        self.typed_text = text
        self.type_pause = pause
        if self._value_after_typing is _UNCHANGED:
            self._value += text
        else:
            self._value = self._value_after_typing

    def get_value(self):
        return self._value

    def select(self, option):
        self.selected_option = option
        if self._selection_after_select is _UNCHANGED:
            self._selected_text = option
        else:
            self._selected_text = self._selection_after_select

    def selected_text(self):
        return getattr(self, "_selected_text", "")

    def scroll(self, direction, amount):
        self.scrolled = (direction, amount)

    def has_keyboard_focus(self):
        return self.focused


class _DocumentTextRange:
    def __init__(self, text_source):
        self._text_source = text_source

    def GetText(self, max_length):
        return self._text_source()


class _DocumentTextPattern:
    def __init__(self, text_source):
        self.DocumentRange = _DocumentTextRange(text_source)


class _FakeDocumentControl(_FakeControl):
    """A rich-text/Document-role control exposing only TextPattern, not
    ValuePattern -- matching a real Windows 11 Notepad editable surface,
    where get_value()/iface_value both raise."""

    def get_value(self):
        raise AttributeError("no ValuePattern on this control")

    @property
    def iface_value(self):
        raise AttributeError("no ValuePattern on this control")

    @property
    def iface_text(self):
        return _DocumentTextPattern(lambda: self._value)


class _SlowToSettleControl(_FakeControl):
    """An Edit control whose get_value() reports the typed text only after
    a few reads, simulating an Electron/Chromium app's accessibility tree
    updating a beat behind its DOM."""

    def __init__(self, control_type, name, *, settles_after_reads):
        super().__init__(control_type, name)
        self._settles_after_reads = settles_after_reads
        self.value_reads = 0

    def get_value(self):
        self.value_reads += 1
        if self.value_reads < self._settles_after_reads:
            return ""
        return self._value


class _FakeWindow:
    def __init__(
        self,
        title,
        descendants=None,
        *,
        handle=None,
        process_id=None,
        class_name="Dialog",
    ):
        self._title = title
        self._descendants = descendants or []
        self.focused = False
        self.handle = handle
        self._process_id = process_id
        self._class_name = class_name

    def window_text(self):
        return self._title

    def descendants(self):
        return self._descendants

    def process_id(self):
        return self._process_id

    def friendly_class_name(self):
        return self._class_name

    def set_focus(self):
        self.focused = True

    def has_focus(self):
        return self.focused


class _FakeDesktop:
    def __init__(self, windows):
        self._windows = windows

    def windows(self):
        return self._windows


class ControlClassifierTests(unittest.TestCase):
    def test_committing_keywords_are_detected(self):
        for name in ("Send", "Submit Order", "Delete account", "Confirm purchase"):
            self.assertTrue(is_committing_control(name), name)

    def test_ordinary_navigation_is_not_committing(self):
        for name in ("Next", "Back", "Menu", "Search", "Settings"):
            self.assertFalse(is_committing_control(name), name)

    def test_credential_fields_are_detected(self):
        for name in ("Password", "Enter your PIN", "Credit card number"):
            self.assertTrue(is_credential_field(name), name)

    def test_ordinary_fields_are_not_credential_fields(self):
        for name in ("Search", "Username", "Message"):
            self.assertFalse(is_credential_field(name), name)

    def test_korean_committing_keywords_are_detected(self):
        # This machine runs Korean-language app UI; verified live against
        # real Notepad/Settings text during development.
        for name in ("삭제", "제출", "결제", "보내기", "설치", "동의"):
            self.assertTrue(is_committing_control(name), name)

    def test_korean_ordinary_navigation_is_not_committing(self):
        for name in ("취소", "다음", "뒤로"):
            self.assertFalse(is_committing_control(name), name)

    def test_korean_credential_fields_are_detected(self):
        for name in ("비밀번호", "신용카드"):
            self.assertTrue(is_credential_field(name), name)


class WindowsUIControlTests(unittest.TestCase):
    def _control(self, window):
        desktop = _FakeDesktop([window])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")
        return WindowsUIControl(observer=observer), window

    def test_focus_window_success(self):
        window = _FakeWindow("Notepad")
        control, window = self._control(window)

        result = control.focus_window("notepad")

        self.assertEqual(result.status, "focused")
        self.assertTrue(window.focused)
        self.assertTrue(result.verified)
        self.assertTrue(result.evidence)

    def test_focus_window_not_found(self):
        control, _ = self._control(_FakeWindow("Notepad"))

        result = control.focus_window("Spotify")

        self.assertEqual(result.status, "not_found")

    def test_focus_window_accepts_a_frozen_surface_snapshot(self):
        window = _FakeWindow(
            "A different GitHub page - Chrome",
            handle=515,
            process_id=82,
            class_name="Chrome_WidgetWin_1",
        )
        control, _ = self._control(window)
        snapshot = WindowInfo(
            title="GitHub - Chrome",
            handle=515,
            process_id=82,
            class_name="Chrome_WidgetWin_1",
        )

        result = control.focus_window(snapshot)

        self.assertEqual(result.status, "focused")
        self.assertTrue(window.focused)

    def test_click_ordinary_control_executes_immediately(self):
        button = _FakeControl("Button", "Next")
        window = _FakeWindow("Setup Wizard", descendants=[button])
        control, _ = self._control(window)

        result = control.click_control("Setup Wizard", "Next")

        self.assertEqual(result.status, "clicked")
        self.assertTrue(button.invoked)
        self.assertTrue(result.evidence)

    def test_click_committing_control_requires_confirmation_first(self):
        button = _FakeControl("Button", "Submit Order")
        window = _FakeWindow("Checkout", descendants=[button])
        control, _ = self._control(window)

        result = control.click_control("Checkout", "Submit Order")

        self.assertEqual(result.status, "confirmation_required")
        self.assertFalse(button.invoked)
        self.assertFalse(button.clicked)

    def test_click_committing_control_executes_once_confirmed(self):
        button = _FakeControl("Button", "Submit Order")
        window = _FakeWindow("Checkout", descendants=[button])
        control, _ = self._control(window)

        result = control.click_control("Checkout", "Submit Order", confirmed=True)

        self.assertEqual(result.status, "clicked")
        self.assertTrue(button.invoked)

    def test_click_falls_back_to_real_click_when_invoke_pattern_missing(self):
        button = _FakeControl("Button", "Next", invoke_raises=True)
        window = _FakeWindow("App", descendants=[button])
        control, _ = self._control(window)

        result = control.click_control("App", "Next")

        self.assertEqual(result.status, "clicked")
        self.assertTrue(button.clicked)

    def test_click_keyboard_focus_alone_is_not_a_verified_outcome(self):
        class _FocusAfterInvokeControl(_FakeControl):
            def invoke(self):
                super().invoke()
                self.focused = True

        button = _FocusAfterInvokeControl("Button", "Next")
        window = _FakeWindow("App", descendants=[button])
        control, _ = self._control(window)

        result = control.click_control("App", "Next")

        self.assertEqual(result.status, "clicked")
        self.assertTrue(button.invoked)
        self.assertIsNone(result.verified)
        self.assertIn("no changed state", result.evidence)

    def test_click_control_not_found(self):
        window = _FakeWindow("App", descendants=[])
        control, _ = self._control(window)

        result = control.click_control("App", "Nonexistent")

        self.assertEqual(result.status, "not_found")

    def test_click_control_refuses_ambiguous_same_name_targets(self):
        first = _FakeControl("Button", "Settings")
        second = _FakeControl("Button", "Settings")
        window = _FakeWindow("App", descendants=[first, second])
        control, _ = self._control(window)

        result = control.click_control("App", "Settings")

        self.assertEqual(result.status, "ambiguous")
        self.assertFalse(first.invoked)
        self.assertFalse(second.invoked)

    def test_click_control_ignores_hidden_duplicate(self):
        hidden = _FakeControl("Button", "Settings", visible=False)
        visible = _FakeControl("Button", "Settings")
        window = _FakeWindow("App", descendants=[hidden, visible])
        control, _ = self._control(window)

        result = control.click_control("App", "Settings")

        self.assertEqual(result.status, "clicked")
        self.assertFalse(hidden.invoked)
        self.assertTrue(visible.invoked)

    def test_type_text_into_edit_field_succeeds(self):
        field = _FakeControl("Edit", "Search")
        window = _FakeWindow("Spotify", descendants=[field])
        control, _ = self._control(window)

        result = control.type_text("Spotify", "Search", "Laufey")

        self.assertEqual(result.status, "typed")
        self.assertEqual(field.typed_text, "Laufey")
        self.assertTrue(field.focused)
        self.assertTrue(result.verified)
        self.assertIn("requested characters", result.evidence)

    def test_type_text_prefers_edit_over_same_named_button(self):
        button = _FakeControl("Button", "Search")
        field = _FakeControl("Edit", "Search")
        window = _FakeWindow("Spotify", descendants=[button, field])
        control, _ = self._control(window)

        result = control.type_text("Spotify", "Search", "BTS")

        self.assertEqual(result.status, "typed")
        self.assertIsNone(button.typed_text)
        self.assertEqual(field.typed_text, "BTS")

    def test_type_text_tolerates_a_slow_accessibility_tree_update(self):
        # Regression: measured live against Spotify -- an Electron/Chromium
        # app updates its accessibility tree a beat behind the DOM, so the
        # very first read-back after typing saw a stale (empty) value even
        # though the real search box was correctly updated moments later.
        # That produced a false "verification_failed" for typing that had
        # actually worked.
        # Reads: 1) the before-typing snapshot, 2) the first post-typing
        # verification attempt (still stale), 3) the retry that settles --
        # proving the retry recovered rather than accepting the first read.
        field = _SlowToSettleControl("Edit", "Search", settles_after_reads=3)
        window = _FakeWindow("Spotify", descendants=[field])
        control, _ = self._control(window)

        result = control.type_text("Spotify", "Search", "From The Start")

        self.assertEqual(result.status, "typed")
        self.assertTrue(result.verified)
        self.assertEqual(field.value_reads, 3)

    def test_type_text_reports_failed_readable_postcondition(self):
        field = _FakeControl(
            "Edit", "Search", value_after_typing="different text",
        )
        window = _FakeWindow("Spotify", descendants=[field])
        control, _ = self._control(window)

        result = control.type_text("Spotify", "Search", "BTS")

        self.assertEqual(result.status, "verification_failed")
        self.assertFalse(result.succeeded)
        self.assertFalse(result.verified)
        self.assertNotIn("different text", result.evidence)

    def test_type_text_uses_a_pause_between_keystrokes(self):
        # Regression test: measured live against this system's real
        # Notepad, typing without a pause silently dropped characters
        # ("Phase 4B.2 planner test" landed as just "Phase"). A pause of
        # 0 or None would silently reintroduce that data-loss bug.
        field = _FakeControl("Edit", "Search")
        window = _FakeWindow("App", descendants=[field])
        control, _ = self._control(window)

        control.type_text("App", "Search", "hello")

        self.assertIsNotNone(field.type_pause)
        self.assertGreater(field.type_pause, 0)

    def test_type_text_refuses_credential_looking_fields(self):
        field = _FakeControl("Edit", "Password")
        window = _FakeWindow("Login", descendants=[field])
        control, _ = self._control(window)

        result = control.type_text("Login", "Password", "hunter2")

        self.assertEqual(result.status, "refused")
        self.assertIsNone(field.typed_text)

    def test_type_text_refuses_non_text_roles(self):
        button = _FakeControl("Button", "Search")
        window = _FakeWindow("App", descendants=[button])
        control, _ = self._control(window)

        result = control.type_text("App", "Search", "hello")

        self.assertEqual(result.status, "refused")

    def test_select_option_success(self):
        combo = _FakeControl("ComboBox", "Choose a device for speaking")
        window = _FakeWindow("Sound Settings", descendants=[combo])
        control, _ = self._control(window)

        result = control.select_option(
            "Sound Settings", "Choose a device", "Headset Microphone"
        )

        self.assertEqual(result.status, "selected")
        self.assertEqual(combo.selected_option, "Headset Microphone")
        self.assertTrue(result.verified)

    def test_select_option_reports_failed_readable_postcondition(self):
        combo = _FakeControl(
            "ComboBox",
            "Choose a device",
            selection_after_select="Speakers",
        )
        window = _FakeWindow("Sound Settings", descendants=[combo])
        control, _ = self._control(window)

        result = control.select_option(
            "Sound Settings", "Choose a device", "Headset Microphone",
        )

        self.assertEqual(result.status, "verification_failed")
        self.assertFalse(result.verified)

    def test_scroll_control_success(self):
        listbox = _FakeControl("List", "Results")
        window = _FakeWindow("App", descendants=[listbox])
        control, _ = self._control(window)

        result = control.scroll_control("App", "Results", "down")

        self.assertEqual(result.status, "scrolled")
        self.assertEqual(listbox.scrolled[0], "down")

    def test_scroll_control_rejects_invalid_direction(self):
        listbox = _FakeControl("List", "Results")
        window = _FakeWindow("App", descendants=[listbox])
        control, _ = self._control(window)

        result = control.scroll_control("App", "Results", "sideways")

        self.assertEqual(result.status, "failed")
        self.assertIsNone(listbox.scrolled)

    def test_click_control_by_element_id_invokes_the_same_control_the_name_path_would(self):
        button = _FakeControl("Button", "Next")
        window = _FakeWindow("Setup Wizard", descendants=[button])
        control, _ = self._control(window)
        observation = control.observer.describe_window("Setup Wizard")
        element_id = observation.controls[0].element_id

        result = control.click_control(
            "Setup Wizard", "", element_id=element_id,
        )

        self.assertEqual(result.status, "clicked")
        self.assertTrue(button.invoked)

    def test_click_control_prefers_element_id_over_a_conflicting_control_name(self):
        real_target = _FakeControl("Button", "Next")
        decoy = _FakeControl("Button", "Cancel")
        window = _FakeWindow(
            "Setup Wizard", descendants=[real_target, decoy],
        )
        control, _ = self._control(window)
        observation = control.observer.describe_window("Setup Wizard")
        real_id = next(
            c.element_id for c in observation.controls if c.name == "Next"
        )

        result = control.click_control(
            "Setup Wizard", "Cancel", element_id=real_id,
        )

        self.assertEqual(result.status, "clicked")
        self.assertTrue(real_target.invoked)
        self.assertFalse(decoy.invoked)

    def test_click_control_by_element_id_still_requires_confirmation_for_a_committing_control(self):
        button = _FakeControl("Button", "Submit Order")
        window = _FakeWindow("Checkout", descendants=[button])
        control, _ = self._control(window)
        observation = control.observer.describe_window("Checkout")
        element_id = observation.controls[0].element_id

        result = control.click_control(
            "Checkout", "", element_id=element_id,
        )

        self.assertEqual(result.status, "confirmation_required")
        self.assertFalse(button.invoked)

    def test_click_control_with_a_stale_element_id_fails_safely_with_a_re_observe_hint(self):
        button = _FakeControl("Button", "Next")
        window = _FakeWindow("Setup Wizard", descendants=[button])
        control, _ = self._control(window)
        control.observer.describe_window("Setup Wizard")  # superseded scan
        observation = control.observer.describe_window("Setup Wizard")
        stale_id = observation.controls[0].element_id
        control.observer.describe_window("Setup Wizard")  # invalidates stale_id

        result = control.click_control(
            "Setup Wizard", "", element_id=stale_id,
        )

        self.assertEqual(result.status, "not_found")
        self.assertFalse(button.invoked)
        self.assertIn("describe_window", result.message)

    def test_type_text_by_element_id_still_refuses_a_credential_field(self):
        field = _FakeControl("Edit", "Password")
        window = _FakeWindow("Login", descendants=[field])
        control, _ = self._control(window)
        observation = control.observer.describe_window("Login")
        element_id = observation.controls[0].element_id

        result = control.type_text(
            "Login", "", "hunter2", element_id=element_id,
        )

        self.assertEqual(result.status, "refused")
        self.assertIsNone(field.typed_text)

    def test_type_text_by_element_id_still_refuses_a_non_text_role(self):
        button = _FakeControl("Button", "Search")
        window = _FakeWindow("App", descendants=[button])
        control, _ = self._control(window)
        observation = control.observer.describe_window("App")
        element_id = observation.controls[0].element_id

        result = control.type_text(
            "App", "", "hello", element_id=element_id,
        )

        self.assertEqual(result.status, "refused")

    def test_type_text_verifies_via_text_pattern_when_no_value_pattern_exists(self):
        # Reproduced live against Windows 11's real Notepad: a Document-role
        # control's keystrokes genuinely land, but _read_text_value's
        # ValuePattern-only reads can't see them, so a fully correct action
        # was reported as verification_failed.
        editor = _FakeDocumentControl("Document", "텍스트 편집기")
        window = _FakeWindow("Notepad", descendants=[editor])
        control, _ = self._control(window)

        result = control.type_text("Notepad", "텍스트 편집기", "hello world")

        self.assertEqual(result.status, "typed")
        self.assertTrue(result.verified)

    def test_type_text_by_element_id_succeeds_into_a_document_role(self):
        editor = _FakeControl("Document", "텍스트 편집기")
        window = _FakeWindow("Notepad", descendants=[editor])
        control, _ = self._control(window)
        observation = control.observer.describe_window("Notepad")
        element_id = observation.controls[0].element_id

        result = control.type_text(
            "Notepad", "", "hello world", element_id=element_id,
        )

        self.assertEqual(result.status, "typed")
        self.assertEqual(editor.typed_text, "hello world")

    def test_unavailable_when_no_desktop_backend(self):
        observer = WindowsUIObserver(desktop=None)
        observer._desktop = None
        control = WindowsUIControl(observer=observer)

        self.assertFalse(control.available)
        self.assertEqual(control.focus_window("anything").status, "unavailable")
        self.assertEqual(
            control.click_control("anything", "anything").status, "unavailable"
        )


if __name__ == "__main__":
    unittest.main()
