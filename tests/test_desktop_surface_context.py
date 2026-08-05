import threading
import unittest

from brain.chat_engine import ChatEngine
from tools.windows_ui_observer import WindowInfo


class _Observer:
    def __init__(self, active):
        self.active = active

    def get_active_window(self):
        return self.active


class _ComputerControl:
    def __init__(self, active):
        self.ui_observer = _Observer(active)


def _engine(active):
    engine = ChatEngine.__new__(ChatEngine)
    engine.computer_control = _ComputerControl(active)
    engine._desktop_surface_lock = threading.Lock()
    engine._captured_desktop_surface = {}
    engine._turn_desktop_surface = {}
    engine._last_desktop_surface = {}
    return engine


class DesktopSurfaceContextTests(unittest.TestCase):
    def test_browser_surface_keeps_stable_foreground_metadata(self):
        engine = _engine(WindowInfo(
            title="Settings · sample/repository - Google Chrome",
            app_name="Chrome_WidgetWin_1",
            is_active=True,
            handle=4123,
            process_id=991,
            class_name="Chrome_WidgetWin_1",
        ))

        surface = engine._capture_active_desktop_surface()

        self.assertEqual(surface["kind"], "browser")
        self.assertEqual(surface["handle"], 4123)
        self.assertEqual(surface["process_id"], 991)
        self.assertEqual(surface["identity"], "hwnd:4123")

    def test_utterance_surface_is_consumed_once_and_frozen_for_the_turn(self):
        browser = WindowInfo(
            title="GitHub - Google Chrome",
            app_name="Chrome_WidgetWin_1",
            is_active=True,
            handle=7,
        )
        engine = _engine(browser)
        captured = engine._capture_active_desktop_surface()
        engine._captured_desktop_surface = dict(captured)

        turn_surface = engine._begin_desktop_turn()
        engine.computer_control.ui_observer.active = WindowInfo(
            title="Settings",
            app_name="ApplicationFrameWindow",
            is_active=True,
            handle=9,
        )

        self.assertEqual(turn_surface["title"], "GitHub - Google Chrome")
        self.assertEqual(
            engine._desktop_surface_for_turn()["title"],
            "GitHub - Google Chrome",
        )
        self.assertEqual(engine._captured_desktop_surface, {})

    def test_assistant_overlay_focus_keeps_the_last_external_surface(self):
        engine = _engine(WindowInfo(
            title="Elaina",
            app_name="Chrome_WidgetWin_1",
            is_active=True,
            handle=99,
        ))
        engine._last_desktop_surface = {
            "title": "sample/repository - Google Chrome",
            "application": "Chrome_WidgetWin_1",
            "kind": "browser",
            "identity": "hwnd:7",
            "handle": 7,
            "process_id": 8,
        }

        captured = engine._capture_active_desktop_surface()

        self.assertEqual(
            captured["title"],
            "sample/repository - Google Chrome",
        )
        self.assertEqual(captured["handle"], 7)


if __name__ == "__main__":
    unittest.main()
