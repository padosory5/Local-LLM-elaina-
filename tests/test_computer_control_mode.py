import unittest

from security.computer_control_mode import ComputerControlMode


class ComputerControlModeTests(unittest.TestCase):
    def test_mode_starts_off_and_toggles_explicitly(self):
        mode = ComputerControlMode()

        self.assertFalse(mode.enabled)
        self.assertTrue(mode.set_enabled(True))
        self.assertTrue(mode.enabled)
        self.assertFalse(mode.set_enabled(False))
        self.assertFalse(mode.enabled)

if __name__ == "__main__":
    unittest.main()
