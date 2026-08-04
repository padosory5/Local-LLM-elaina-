import unittest

from security.policy import PolicyEngine
from tools.computer_control import (
    ComputerControl,
    takeover_authorized,
    transcript_names_target,
)
from tools.windows_app_catalog import (
    AppEntry,
    WindowsAppCatalog,
    app_name_aliases,
    normalize_app_name,
)


class ComputerControlTests(unittest.TestCase):
    def setUp(self):
        self.entries = (
            AppEntry.create("Discord", "shortcut", "C:/Apps/Discord.lnk"),
            AppEntry.create(
                "Battle.net Launcher",
                "shortcut",
                "C:/Apps/Battle.net.lnk",
            ),
            AppEntry.create("Steam", "shortcut", "C:/Apps/Steam.lnk"),
            AppEntry.create(
                "Visual Studio Code",
                "shortcut",
                "C:/Apps/Visual Studio Code.lnk",
            ),
            AppEntry.create(
                "Default Browser",
                "browser",
                "https://www.google.com",
                aliases=("browser", "web browser", "default browser"),
            ),
        )
        self.launched = []
        self.catalog = WindowsAppCatalog(entries=self.entries)
        self.control = ComputerControl(
            PolicyEngine(),
            catalog=self.catalog,
            launcher=self.launched.append,
        )

    def test_authorization_word_is_standalone_and_case_insensitive(self):
        self.assertTrue(takeover_authorized("Takeover, open Spotify."))
        self.assertFalse(takeover_authorized("takeovers are interesting"))
        self.assertFalse(takeover_authorized("open Spotify"))

    def test_target_must_be_named_in_the_transcript(self):
        self.assertTrue(
            transcript_names_target("Open my web browser.", "browser")
        )
        self.assertTrue(
            transcript_names_target("Launch Battle.net.", "Battle net")
        )
        self.assertFalse(
            transcript_names_target("Open PowerShell.", "Spotify")
        )

    def test_name_normalization_ignores_case_spaces_and_punctuation(self):
        self.assertEqual(normalize_app_name("Battle.net"), "battlenet")
        self.assertEqual(normalize_app_name("battle NET"), "battlenet")

    def test_catalog_derives_battlenet_and_vscode_aliases(self):
        self.assertIn("battlenet", app_name_aliases("Battle.net Launcher"))
        self.assertIn("vscode", app_name_aliases("Visual Studio Code"))

    def test_installed_app_names_resolve_without_a_hardcoded_app_list(self):
        for query, expected in (
            ("Discord", "Discord"),
            ("battle net", "Battle.net Launcher"),
            ("Steam", "Steam"),
            ("VSCode", "Visual Studio Code"),
            ("browser", "Default Browser"),
        ):
            with self.subTest(query=query):
                result = self.control.resolve_app(query)
                self.assertEqual(result.status, "resolved")
                self.assertEqual(result.display_name, expected)

    def test_resolve_never_launches_before_consent(self):
        result = self.control.resolve_app("Discord")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(self.launched, [])

    def test_open_app_launches_only_the_catalog_descriptor(self):
        result = self.control.open_app("Discord")

        self.assertTrue(result.succeeded)
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.launched[0].display_name, "Discord")

    def test_pending_consent_can_launch_the_exact_stored_entry(self):
        resolved = self.control.resolve_app("Battle.net")
        result = self.control.open_entry(resolved.entry_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(self.launched[0].display_name, "Battle.net Launcher")

    def test_unknown_targets_never_reach_the_launcher(self):
        result = self.control.open_app("Definitely Not Installed")

        self.assertEqual(result.status, "not_found")
        self.assertEqual(self.launched, [])

    def test_ambiguous_aliases_never_reach_the_launcher(self):
        catalog = WindowsAppCatalog(entries=(
            AppEntry.create(
                "Visual Studio Code",
                "shortcut",
                "C:/Apps/Code.lnk",
                aliases=("editor",),
            ),
            AppEntry.create(
                "Another Editor",
                "shortcut",
                "C:/Apps/Another.lnk",
                aliases=("editor",),
            ),
        ))
        control = ComputerControl(
            PolicyEngine(),
            catalog=catalog,
            launcher=self.launched.append,
        )

        result = control.open_app("editor")

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(self.launched, [])

    def test_disabled_control_never_reaches_the_launcher(self):
        control = ComputerControl(
            PolicyEngine(),
            enabled=False,
            catalog=self.catalog,
            launcher=lambda _entry: self.fail("launcher ran"),
        )

        result = control.open_app("Discord")

        self.assertEqual(result.status, "disabled")

    def test_launcher_errors_are_reported_without_raising(self):
        def fail_to_open(_entry):
            raise OSError("launch failed")

        control = ComputerControl(
            PolicyEngine(),
            catalog=self.catalog,
            launcher=fail_to_open,
        )

        result = control.open_app("Discord")

        self.assertEqual(result.status, "failed")
        self.assertIn("couldn't open Discord", result.message)

    def test_open_app_policy_is_local_and_requires_no_second_approval(self):
        policy = PolicyEngine().get("computer.open_app")

        self.assertEqual(policy.risk, "local_reversible_action")
        self.assertFalse(policy.approval_required)

    def test_filesystem_delete_policy_is_recoverable_and_confirmed(self):
        policy = PolicyEngine().get("filesystem.delete")

        self.assertEqual(policy.risk, "recoverable_destructive_action")
        self.assertTrue(policy.approval_required)


if __name__ == "__main__":
    unittest.main()
