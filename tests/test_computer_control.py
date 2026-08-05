import unittest

from security.policy import PolicyEngine
from tools.computer_control import (
    ComputerActionRequest,
    ComputerControl,
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

    def test_target_grounds_against_a_model_completed_domain_suffix(self):
        # "Can you open github" -> the model may reasonably resolve the
        # target to "github.com" even though the user never said ".com".
        # The suffix the user never spoke must not defeat grounding.
        self.assertTrue(
            transcript_names_target(
                "Can you open github on my web browser", "github.com"
            )
        )
        self.assertTrue(
            transcript_names_target(
                "open reddit please", "reddit.com"
            )
        )
        # A completely different site must still be rejected.
        self.assertFalse(
            transcript_names_target(
                "Can you open github on my web browser", "gitlab.com"
            )
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

    def test_observe_ui_policy_is_read_only_and_never_confirmed(self):
        policy = PolicyEngine().get("computer.observe_ui")

        self.assertEqual(policy.risk, "read_only")
        self.assertFalse(policy.approval_required)


class _FakeObserverWindow:
    def __init__(self, title, is_active=False):
        self.title = title
        self.is_active = is_active


class _FakeObservation:
    def __init__(self, status, title="", controls=(), message=""):
        self.status = status
        self.title = title
        self.controls = controls
        self.message = message

    def as_tree_text(self):
        return self.message or f"Window: {self.title}"


class _FakeControl:
    def __init__(self, role, name):
        self.role = role
        self.name = name


class _FakeUIObserver:
    def __init__(self, windows=(), observation=None):
        self._windows = windows
        self._observation = observation or _FakeObservation("not_found", message="not found")

    def list_windows(self):
        return self._windows

    def get_active_window(self):
        for window in self._windows:
            if window.is_active:
                return window
        return None

    def describe_window(self, _query):
        return self._observation


class Phase4B1ObservationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = WindowsAppCatalog(entries=())

    def _control(self, ui_observer):
        return ComputerControl(
            PolicyEngine(),
            catalog=self.catalog,
            ui_observer=ui_observer,
        )

    def test_list_windows_never_requires_confirmation(self):
        self.assertFalse(
            ComputerControl.requires_extra_confirmation("list_windows")
        )
        self.assertFalse(
            ComputerControl.requires_extra_confirmation("describe_window")
        )

    def test_list_windows_prepares_and_executes_without_a_target(self):
        observer = _FakeUIObserver(windows=[
            _FakeObserverWindow("Notepad"),
            _FakeObserverWindow("Calculator", is_active=True),
        ])
        control = self._control(observer)

        prepared = control.prepare(
            ComputerActionRequest("list_windows", "")
        )
        self.assertEqual(prepared.status, "prepared")

        result = control.execute(prepared.prepared)

        self.assertEqual(result.status, "windows_listed")
        self.assertIn("Notepad", result.message)
        self.assertIn("Calculator", result.message)
        self.assertTrue(result.succeeded)

    def test_describe_window_reports_the_underlying_observation_status(self):
        observer = _FakeUIObserver(
            observation=_FakeObservation(
                "observed",
                title="Sound Settings",
                controls=(_FakeControl("ComboBox", "Choose a device"),),
                message="Window: Sound Settings\n- ComboBox: Choose a device",
            )
        )
        control = self._control(observer)

        prepared = control.prepare(
            ComputerActionRequest("describe_window", "Sound Settings")
        )
        result = control.execute(prepared.prepared)

        self.assertEqual(result.status, "window_described")
        self.assertEqual(result.display_name, "Sound Settings")
        self.assertIn("Choose a device", result.message)

    def test_describe_window_not_found_does_not_claim_success(self):
        observer = _FakeUIObserver(
            observation=_FakeObservation("not_found", message="not found")
        )
        control = self._control(observer)

        prepared = control.prepare(
            ComputerActionRequest("describe_window", "Nonexistent")
        )
        result = control.execute(prepared.prepared)

        self.assertEqual(result.status, "not_found")
        self.assertFalse(result.succeeded)

    def test_observation_operations_respect_the_control_mode_disabled_flag(self):
        observer = _FakeUIObserver(windows=[_FakeObserverWindow("Notepad")])
        control = ComputerControl(
            PolicyEngine(),
            catalog=self.catalog,
            ui_observer=observer,
            enabled=False,
        )

        result = control.prepare(ComputerActionRequest("list_windows", ""))

        self.assertEqual(result.status, "disabled")


if __name__ == "__main__":
    unittest.main()
