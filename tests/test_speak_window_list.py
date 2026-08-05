import unittest

from brain.chat_engine import ChatEngine
from tools.windows_ui_observer import WindowInfo


class SpeakWindowListTests(unittest.TestCase):
    def test_no_windows(self):
        self.assertEqual(
            ChatEngine._speak_window_list(()),
            "I don't see any windows open right now.",
        )

    def test_single_window(self):
        windows = (WindowInfo(title="Notepad", is_active=True),)

        self.assertEqual(
            ChatEngine._speak_window_list(windows),
            "You have one window open: Notepad.",
        )

    def test_names_the_currently_active_window(self):
        # Regression: "What window is in front right now?" had no correct
        # answer -- list_windows named every open window but never said
        # which one actually had focus.
        windows = (
            WindowInfo(title="Spotify", is_active=False),
            WindowInfo(title="Notepad", is_active=True),
        )

        summary = ChatEngine._speak_window_list(windows)

        self.assertIn("Notepad is currently in front", summary)

    def test_no_active_window_omits_the_in_front_clause(self):
        windows = (
            WindowInfo(title="Spotify", is_active=False),
            WindowInfo(title="Notepad", is_active=False),
        )

        summary = ChatEngine._speak_window_list(windows)

        self.assertNotIn("in front", summary)


if __name__ == "__main__":
    unittest.main()
