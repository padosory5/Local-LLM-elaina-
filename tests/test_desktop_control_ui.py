import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DesktopControlUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (
            PROJECT_ROOT / "desktop" / "renderer" / "index.html"
        ).read_text(encoding="utf-8")
        cls.javascript = (
            PROJECT_ROOT / "desktop" / "renderer" / "app.js"
        ).read_text(encoding="utf-8")
        cls.styles = (
            PROJECT_ROOT / "desktop" / "renderer" / "style.css"
        ).read_text(encoding="utf-8")
        cls.websocket = (
            PROJECT_ROOT / "core" / "websocket_server.py"
        ).read_text(encoding="utf-8")

    def test_main_screen_has_an_off_by_default_accessible_toggle(self):
        self.assertIn('id="computer-control-button"', self.html)
        self.assertIn('aria-pressed="false"', self.html)
        self.assertIn("Control Off", self.html)
        self.assertIn("disabled", self.html)

    def test_toggle_waits_for_authoritative_backend_state(self):
        self.assertIn('command: "get_computer_control_mode"', self.javascript)
        self.assertIn('command: "set_computer_control_mode"', self.javascript)
        self.assertIn('case "computer_control_mode_changed"', self.javascript)
        self.assertIn('"computer_control_mode_changed"', self.websocket)

    def test_toggle_reuses_the_existing_button_visual_language(self):
        self.assertIn("#computer-control-button", self.styles)
        self.assertIn("#computer-control-button.active", self.styles)
        self.assertIn("var(--connected)", self.styles)


if __name__ == "__main__":
    unittest.main()
