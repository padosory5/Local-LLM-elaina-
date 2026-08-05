import unittest

from tools.windows_ui_observer import WindowInfo, WindowsUIObserver


class _FakeElementInfo:
    def __init__(self, control_type, name, *, visible=True, enabled=True):
        self.control_type = control_type
        self.name = name
        self.visible = visible
        self.enabled = enabled


class _FakeElement:
    def __init__(self, control_type, name, *, visible=True, enabled=True):
        self.element_info = _FakeElementInfo(
            control_type, name, visible=visible, enabled=enabled,
        )

    def is_visible(self):
        return self.element_info.visible

    def is_enabled(self):
        return self.element_info.enabled


class _FakeWindow:
    def __init__(
        self,
        title,
        class_name="Dialog",
        descendants=None,
        broken=False,
        handle=None,
        process_id=None,
    ):
        self._title = title
        self._class_name = class_name
        self._descendants = descendants or []
        self._broken = broken
        self.handle = handle
        self._process_id = process_id

    def window_text(self):
        if self._broken:
            raise RuntimeError("window closed mid-read")
        return self._title

    def friendly_class_name(self):
        return self._class_name

    def descendants(self):
        return self._descendants

    def process_id(self):
        return self._process_id


class _FakeDesktop:
    def __init__(self, windows):
        self._windows = windows

    def windows(self):
        return self._windows


class WindowsUIObserverTests(unittest.TestCase):
    def test_lists_windows_and_marks_the_active_one(self):
        desktop = _FakeDesktop([
            _FakeWindow("Notepad"),
            _FakeWindow("Calculator"),
        ])
        observer = WindowsUIObserver(
            desktop=desktop,
            foreground_window=lambda: "Calculator",
        )

        windows = observer.list_windows()

        self.assertEqual([w.title for w in windows], ["Notepad", "Calculator"])
        self.assertFalse(windows[0].is_active)
        self.assertTrue(windows[1].is_active)
        self.assertEqual(observer.get_active_window().title, "Calculator")

    def test_skips_windows_with_no_title(self):
        desktop = _FakeDesktop([_FakeWindow(""), _FakeWindow("Notepad")])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        windows = observer.list_windows()

        self.assertEqual([w.title for w in windows], ["Notepad"])

    def test_window_snapshot_includes_stable_surface_metadata(self):
        target = _FakeWindow(
            "GitHub - Chrome",
            class_name="Chrome_WidgetWin_1",
            handle=4123,
            process_id=991,
        )
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([target]),
            foreground_window=lambda: "GitHub - Chrome",
        )

        window = observer.get_active_window()

        self.assertEqual(window.handle, 4123)
        self.assertEqual(window.process_id, 991)
        self.assertEqual(window.class_name, "Chrome_WidgetWin_1")
        self.assertEqual(window.identity, "hwnd:4123")

    def test_a_window_that_errors_while_reading_is_skipped_not_fatal(self):
        desktop = _FakeDesktop([
            _FakeWindow("", broken=True),
            _FakeWindow("Notepad"),
        ])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        windows = observer.list_windows()

        self.assertEqual([w.title for w in windows], ["Notepad"])

    def test_describe_window_finds_by_partial_case_insensitive_match(self):
        target = _FakeWindow(
            "Sound Settings",
            descendants=[
                _FakeElement("ComboBox", "Choose a device for speaking"),
                _FakeElement("Slider", "Input volume"),
                _FakeElement("Button", "Start test"),
            ],
        )
        desktop = _FakeDesktop([_FakeWindow("Notepad"), target])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        observation = observer.describe_window("sound")

        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.title, "Sound Settings")
        self.assertEqual(len(observation.controls), 3)
        self.assertEqual(observation.controls[0].name, "Choose a device for speaking")

    def test_describe_window_finds_a_korean_titled_window_by_english_name(self):
        target = _FakeWindow(
            "제목 없음 - 메모장",
            descendants=[_FakeElement("Document", "텍스트 편집기")],
        )
        desktop = _FakeDesktop([target])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        observation = observer.describe_window("notepad")

        self.assertEqual(observation.status, "observed")
        self.assertEqual(observation.title, "제목 없음 - 메모장")

    def test_find_window_prefers_a_literal_match_over_a_translated_one(self):
        literal = _FakeWindow("Notepad")
        translated = _FakeWindow("메모장")
        desktop = _FakeDesktop([translated, literal])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        found = observer.find_window("notepad")

        self.assertIs(found, literal)

    def test_find_window_matches_english_query_against_korean_settings_title(self):
        target = _FakeWindow("설정")
        desktop = _FakeDesktop([_FakeWindow("Notepad"), target])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        # A substring like "sound settings" still contains the aliased
        # "settings" key, so it should resolve to the one real Settings
        # window regardless of which page the user actually meant.
        found = observer.find_window("sound settings")

        self.assertIs(found, target)

    def test_find_window_uses_captured_identity_even_if_title_changes(self):
        target = _FakeWindow(
            "A different page - Chrome",
            class_name="Chrome_WidgetWin_1",
            handle=515,
            process_id=82,
        )
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([target]), foreground_window=lambda: "",
        )
        captured = WindowInfo(
            title="GitHub - Chrome",
            handle=515,
            process_id=82,
            class_name="Chrome_WidgetWin_1",
        )

        self.assertIs(observer.find_window(captured), target)

    def test_find_window_never_falls_back_by_title_for_stale_snapshot(self):
        replacement = _FakeWindow(
            "GitHub - Chrome",
            class_name="Chrome_WidgetWin_1",
            handle=999,
            process_id=83,
        )
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([replacement]), foreground_window=lambda: "",
        )
        captured = WindowInfo(
            title="GitHub - Chrome",
            handle=515,
            process_id=82,
            class_name="Chrome_WidgetWin_1",
        )

        self.assertIsNone(observer.find_window(captured))

    def test_pid_only_snapshot_uses_title_to_choose_same_process_window(self):
        other_tab = _FakeWindow(
            "Gmail - Chrome",
            class_name="Chrome_WidgetWin_1",
            process_id=82,
        )
        target = _FakeWindow(
            "GitHub - Chrome",
            class_name="Chrome_WidgetWin_1",
            process_id=82,
        )
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([other_tab, target]), foreground_window=lambda: "",
        )
        captured = WindowInfo(
            title="GitHub - Chrome",
            process_id=82,
            class_name="Chrome_WidgetWin_1",
        )

        self.assertIs(observer.find_window(captured), target)

    def test_pid_only_snapshot_fails_closed_for_duplicate_window_identity(self):
        first = _FakeWindow(
            "GitHub - Chrome",
            class_name="Chrome_WidgetWin_1",
            process_id=82,
        )
        second = _FakeWindow(
            "GitHub - Chrome",
            class_name="Chrome_WidgetWin_1",
            process_id=82,
        )
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([first, second]), foreground_window=lambda: "",
        )
        captured = WindowInfo(
            title="GitHub - Chrome",
            process_id=82,
            class_name="Chrome_WidgetWin_1",
        )

        self.assertIsNone(observer.find_window(captured))

    def test_pid_only_snapshot_does_not_follow_a_changed_title(self):
        replacement = _FakeWindow(
            "Settings - Chrome",
            class_name="Chrome_WidgetWin_1",
            process_id=82,
        )
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([replacement]), foreground_window=lambda: "",
        )
        captured = WindowInfo(
            title="GitHub - Chrome",
            process_id=82,
            class_name="Chrome_WidgetWin_1",
        )

        self.assertIsNone(observer.find_window(captured))

    def test_describe_window_reports_not_found(self):
        desktop = _FakeDesktop([_FakeWindow("Notepad")])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        observation = observer.describe_window("Spotify")

        self.assertEqual(observation.status, "not_found")

    def test_describe_window_filters_out_unnamed_elements(self):
        target = _FakeWindow(
            "App",
            descendants=[
                _FakeElement("Pane", ""),
                _FakeElement("Group", ""),
                _FakeElement("Button", "Save"),
            ],
        )
        desktop = _FakeDesktop([target])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        observation = observer.describe_window("App")

        self.assertEqual(len(observation.controls), 1)
        self.assertEqual(observation.controls[0].name, "Save")

    def test_describe_window_prioritizes_interactive_controls_and_deduplicates(self):
        descendants = [
            *[_FakeElement("Text", f"Static label {index}") for index in range(90)],
            _FakeElement("Edit", "Search"),
            _FakeElement("Edit", "Search"),
            _FakeElement("Button", "Play"),
        ]
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([_FakeWindow("Spotify", descendants=descendants)]),
            foreground_window=lambda: "",
        )

        observation = observer.describe_window("Spotify")

        self.assertEqual(
            [item.name for item in observation.controls[:2]],
            ["Search", "Play"],
        )
        self.assertEqual(
            sum(item.name == "Search" for item in observation.controls), 1
        )
        self.assertTrue(observation.truncated)

    def test_describe_window_omits_known_hidden_controls(self):
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([_FakeWindow(
                "App",
                descendants=[
                    _FakeElement("Button", "Hidden", visible=False),
                    _FakeElement("Button", "Visible"),
                ],
            )]),
            foreground_window=lambda: "",
        )

        observation = observer.describe_window("App")

        self.assertEqual([item.name for item in observation.controls], ["Visible"])

    def test_find_control_prefers_exact_name_over_earlier_partial_match(self):
        partial = _FakeElement("Button", "Search settings")
        exact = _FakeElement("Button", "Search")
        window = _FakeWindow("App", descendants=[partial, exact])
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([window]), foreground_window=lambda: "",
        )

        self.assertIs(observer.find_control(window, "Search"), exact)

    def test_find_control_uses_expected_role_to_disambiguate(self):
        button = _FakeElement("Button", "Search")
        edit = _FakeElement("Edit", "Search")
        window = _FakeWindow("App", descendants=[button, edit])
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([window]), foreground_window=lambda: "",
        )

        found = observer.find_control(
            window, "Search", expected_roles={"Edit"},
        )

        self.assertIs(found, edit)

    def test_find_control_rejects_an_ambiguous_exact_match(self):
        first = _FakeElement("Button", "Settings")
        second = _FakeElement("Button", "Settings")
        window = _FakeWindow("App", descendants=[first, second])
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([window]), foreground_window=lambda: "",
        )

        lookup = observer.resolve_control(
            window, "Settings", expected_roles={"Button"},
        )

        self.assertEqual(lookup.status, "ambiguous")
        self.assertIsNone(observer.find_control(window, "Settings"))

    def test_find_control_skips_disabled_exact_match(self):
        disabled = _FakeElement("Button", "Search", enabled=False)
        enabled = _FakeElement("Button", "Search music")
        window = _FakeWindow("App", descendants=[disabled, enabled])
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([window]), foreground_window=lambda: "",
        )

        self.assertIs(observer.find_control(window, "Search"), enabled)

    def test_find_control_preserves_english_korean_alias_resolution(self):
        korean = _FakeElement("Button", "설정")
        window = _FakeWindow("App", descendants=[korean])
        observer = WindowsUIObserver(
            desktop=_FakeDesktop([window]), foreground_window=lambda: "",
        )

        self.assertIs(observer.find_control(window, "Settings"), korean)

    def test_describe_window_reports_empty_when_nothing_is_accessible(self):
        # Matches games, custom-rendered UIs, and heavily customized Electron
        # apps that expose no usable accessibility tree.
        target = _FakeWindow("Game", descendants=[_FakeElement("Pane", "")])
        desktop = _FakeDesktop([target])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        observation = observer.describe_window("Game")

        self.assertEqual(observation.status, "empty")

    def test_describe_window_truncates_element_count(self):
        many = [_FakeElement("Button", f"Item {i}") for i in range(200)]
        target = _FakeWindow("Big App", descendants=many)
        desktop = _FakeDesktop([target])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        observation = observer.describe_window("Big App")

        self.assertLessEqual(len(observation.controls), 80)
        self.assertTrue(observation.truncated)

    def test_describe_window_truncates_long_element_names(self):
        long_name = "x" * 500
        target = _FakeWindow(
            "App",
            descendants=[_FakeElement("Text", long_name)],
        )
        desktop = _FakeDesktop([target])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        observation = observer.describe_window("App")

        self.assertLessEqual(len(observation.controls[0].name), 80)

    def test_unavailable_when_no_desktop_backend(self):
        observer = WindowsUIObserver(desktop=None)
        observer._desktop = None  # simulate pywinauto not being importable

        self.assertFalse(observer.available)
        self.assertEqual(observer.list_windows(), ())
        self.assertEqual(observer.describe_window("anything").status, "unavailable")

    def test_as_tree_text_matches_readable_format(self):
        target = _FakeWindow(
            "Sound Settings",
            descendants=[_FakeElement("ComboBox", "Choose a device for speaking")],
        )
        desktop = _FakeDesktop([target])
        observer = WindowsUIObserver(desktop=desktop, foreground_window=lambda: "")

        text = observer.describe_window("Sound Settings").as_tree_text()

        self.assertIn("Window: Sound Settings", text)
        self.assertIn("ComboBox: Choose a device for speaking", text)


if __name__ == "__main__":
    unittest.main()
