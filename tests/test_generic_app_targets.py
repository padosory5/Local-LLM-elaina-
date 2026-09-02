"""Closing "my browser" has to close the browser that is open.

B-50, from the second dogfooding session:

    User:   close my browser for me.
    [Computer Control] action=close_app target=browser status=not_running
    Elaina: I can't find Default Browser running.

    User:   close whale
    [Computer Control] target=Whale status=closed

"Default Browser" is a synthetic catalogue entry that exists so "open a
browser" can hand off to the Windows default handler. It has no process
and no window, so it can never be *closed* -- and resolving a close
request onto it produces a sentence about an application the person has
never heard of, while their actual browser sits there open.

A generic word names a role, not a program. For an operation that acts on
something already running, the role is resolved against what is actually
running.
"""

import unittest

from tools.computer_control.windows_app_catalog import WindowsAppCatalog


class RunningWindows:
    """Stands in for the live window observer."""

    def __init__(self, *titles):
        self.titles = list(titles)

    def list_windows(self):
        return [{"title": title, "process": title} for title in self.titles]


class AGenericNameIsARoleTests(unittest.TestCase):

    def test_browser_resolves_to_the_one_that_is_running(self):
        catalog = WindowsAppCatalog()
        resolved = catalog.resolve_running(
            "browser",
            running=("Whale", "Spotify Premium", "Discord"),
        )

        self.assertEqual(resolved, "Whale")

    def test_my_browser_is_the_same_request(self):
        catalog = WindowsAppCatalog()

        self.assertEqual(
            catalog.resolve_running(
                "my browser", running=("Google Chrome", "Notepad"),
            ),
            "Google Chrome",
        )

    def test_nothing_running_resolves_to_nothing(self):
        catalog = WindowsAppCatalog()

        self.assertEqual(
            catalog.resolve_running("browser", running=("Notepad", "Spotify")),
            "",
        )

    def test_a_named_app_is_left_alone(self):
        # "close whale" already worked and must keep working untouched.
        catalog = WindowsAppCatalog()

        self.assertEqual(
            catalog.resolve_running("Whale", running=("Whale", "Chrome")),
            "",
            "a named application must not be re-resolved by role",
        )

    def test_the_synthetic_entry_is_never_a_close_target(self):
        # The drift guard: "Default Browser" exists to *open* something.
        catalog = WindowsAppCatalog()

        self.assertTrue(
            catalog.is_launch_only("Default Browser"),
        )


if __name__ == "__main__":
    unittest.main()
