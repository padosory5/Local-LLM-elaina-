import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.computer_control.windows_process_control import ProcessInfo, WindowsProcessControl


class WindowsProcessControlTests(unittest.TestCase):
    def test_graceful_close_posts_to_verified_window_and_checks_exit(self):
        control = WindowsProcessControl()
        process = ProcessInfo(1234, "Example", "Example window")

        with patch.object(control, "_post_close_messages", return_value=1) as post:
            with patch.object(control, "_wait_until_stopped", return_value=True):
                status = control.close((process,))

        self.assertEqual(status, "closed")
        post.assert_called_once_with({1234})

    def test_graceful_close_fails_without_an_app_owned_window(self):
        control = WindowsProcessControl()

        status = control.close((ProcessInfo(1234, "Helper"),))

        self.assertEqual(status, "failed")
        self.assertIn("top-level window", control.last_error)

    @unittest.skipUnless(os.name == "nt", "Windows native process test")
    def test_force_quit_terminates_a_real_temporary_process(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=flags,
        )
        try:
            process = ProcessInfo(
                child.pid,
                Path(sys.executable).stem,
                path=sys.executable,
            )

            status = WindowsProcessControl().force_quit((process,))
            child.wait(timeout=5)

            self.assertEqual(status, "force_quit")
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    @unittest.skipUnless(os.name == "nt", "Windows native process test")
    def test_executable_identity_mismatch_is_never_terminated(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=flags,
        )
        try:
            status, error = WindowsProcessControl._terminate_verified_process(
                ProcessInfo(child.pid, "definitely-not-python")
            )

            self.assertEqual(status, "failed")
            self.assertIn("identity changed", error)
            self.assertIsNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
