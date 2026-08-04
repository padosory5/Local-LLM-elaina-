import os
import unittest
from io import StringIO
from unittest.mock import patch

from scripts.console_style import GREEN, RED, RESET, colors_enabled, status_label


class ConsoleStyleTests(unittest.TestCase):
    def test_pass_is_green_when_color_is_forced(self):
        self.assertEqual(status_label(True, force=True), f"{GREEN}PASS{RESET}")

    def test_fail_is_red_when_color_is_forced(self):
        self.assertEqual(status_label(False, force=True), f"{RED}FAIL{RESET}")

    def test_labels_are_plain_when_color_is_disabled(self):
        self.assertEqual(status_label(True, force=False), "PASS")
        self.assertEqual(status_label(False, force=False), "FAIL")

    def test_redirected_output_does_not_receive_escape_codes(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(colors_enabled(StringIO()))

    def test_no_color_overrides_force_color_environment_variable(self):
        with patch.dict(
            os.environ,
            {"NO_COLOR": "1", "FORCE_COLOR": "1"},
            clear=True,
        ):
            self.assertFalse(colors_enabled(StringIO()))


if __name__ == "__main__":
    unittest.main()
