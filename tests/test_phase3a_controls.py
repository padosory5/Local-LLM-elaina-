import tempfile
import unittest
from pathlib import Path

from security.policy import PolicyEngine
from tools.computer_control.computer_control import (
    ComputerActionRequest,
    ComputerControl,
)
from tools.browser_control.safe_browser import SafeBrowserControl
from tools.computer_control.safe_filesystem import SafeFilesystemControl
from tools.computer_control.windows_app_catalog import AppEntry, WindowsAppCatalog
from tools.computer_control.windows_process_control import ProcessInfo, ProcessResolution


class SafeBrowserControlTests(unittest.TestCase):
    def test_spoken_site_can_expand_to_matching_https_domain(self):
        result = SafeBrowserControl().resolve(
            "YouTube",
            "https://www.youtube.com",
        )

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.url, "https://www.youtube.com")

    def test_model_cannot_substitute_an_unspoken_domain(self):
        result = SafeBrowserControl().resolve(
            "YouTube",
            "https://example.com",
        )

        self.assertEqual(result.status, "invalid_target")

    def test_plain_hostname_defaults_to_https(self):
        result = SafeBrowserControl().resolve("github.com")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.url, "https://github.com")

    def test_credentials_and_non_web_schemes_are_rejected(self):
        control = SafeBrowserControl()

        self.assertEqual(
            control.resolve("example.com", "https://user:secret@example.com").status,
            "invalid_target",
        )
        self.assertEqual(
            control.resolve("example.com", "file:///C:/secret.txt").status,
            "invalid_target",
        )

    def test_local_navigation_is_disabled_by_default(self):
        self.assertEqual(
            SafeBrowserControl().resolve("localhost", "http://localhost:3000").status,
            "blocked",
        )

    def test_only_a_validated_url_reaches_the_opener(self):
        opened = []
        control = SafeBrowserControl(opener=lambda url: opened.append(url) or True)
        resolution = control.resolve("example.com")

        control.open(resolution.url)

        self.assertEqual(opened, ["https://example.com"])


class SafeFilesystemControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name).resolve()
        self.root = base / "allowed"
        self.recycle = base / "recycle"
        self.root.mkdir()
        self.recycle.mkdir()

        def recycle(path):
            path.rename(self.recycle / path.name)

        self.control = SafeFilesystemControl([self.root], recycler=recycle)

    def tearDown(self):
        self.temporary.cleanup()

    def test_creates_one_empty_file_without_overwriting(self):
        resolution = self.control.resolve_creation(
            name="notes.txt",
            location=str(self.root),
        )

        self.assertEqual(resolution.status, "resolved")
        self.control.create_file(resolution.path)
        self.assertEqual(Path(resolution.path).read_text(encoding="utf-8"), "")
        with self.assertRaises(FileExistsError):
            self.control.create_file(resolution.path)

    def test_creates_one_folder(self):
        resolution = self.control.resolve_creation(
            name="Receipts",
            location=str(self.root),
        )

        self.control.create_folder(resolution.path)

        self.assertTrue(Path(resolution.path).is_dir())

    def test_rejects_path_traversal_and_absolute_target_names(self):
        for name in ("../escape", "sub/folder", "C:\\outside.txt"):
            with self.subTest(name=name):
                result = self.control.resolve_creation(
                    name=name,
                    location=str(self.root),
                )
                self.assertEqual(result.status, "invalid_target")

    def test_rejects_parent_outside_allowlist(self):
        outside = self.root.parent
        result = self.control.resolve_creation(
            name="blocked.txt",
            location=str(outside),
        )

        self.assertEqual(result.status, "outside_allowed")

    def test_requires_an_explicit_location(self):
        result = self.control.resolve_creation(name="notes.txt", location="")

        self.assertEqual(result.status, "needs_location")

    def test_deletes_a_file_recoverably_after_exact_resolution(self):
        source = self.root / "notes.txt"
        source.write_text("keep this recoverable", encoding="utf-8")
        resolution = self.control.resolve_deletion(
            name="notes.txt",
            location=str(self.root),
            expected_kind="file",
        )

        self.assertEqual(resolution.status, "resolved")
        self.control.delete_file(resolution.path)

        self.assertFalse(source.exists())
        self.assertEqual(
            (self.recycle / "notes.txt").read_text(encoding="utf-8"),
            "keep this recoverable",
        )

    def test_deletes_a_nonempty_folder_recoverably(self):
        source = self.root / "Notes"
        source.mkdir()
        (source / "idea.txt").write_text("idea", encoding="utf-8")
        resolution = self.control.resolve_deletion(
            name="Notes",
            location=str(self.root),
            expected_kind="folder",
        )

        self.control.delete_folder(resolution.path)

        self.assertFalse(source.exists())
        self.assertTrue((self.recycle / "Notes" / "idea.txt").is_file())

    def test_delete_refuses_a_file_folder_type_mismatch(self):
        (self.root / "notes.txt").write_text("notes", encoding="utf-8")

        result = self.control.resolve_deletion(
            name="notes.txt",
            location=str(self.root),
            expected_kind="folder",
        )

        self.assertEqual(result.status, "wrong_type")

    def test_delete_reports_a_missing_item_without_mutation(self):
        result = self.control.resolve_deletion(
            name="missing.txt",
            location=str(self.root),
            expected_kind="file",
        )

        self.assertEqual(result.status, "item_not_found")


class FakeProcesses:
    def __init__(self):
        self.closed = []
        self.force_quit_calls = []
        self.resolution = ProcessResolution(
            "resolved",
            (ProcessInfo(10, "Discord", "Discord"),),
        )

    def resolve(self, entry):
        return self.resolution

    def close(self, processes):
        self.closed.append(tuple(processes))
        return "closed"

    def force_quit(self, processes):
        self.force_quit_calls.append(tuple(processes))
        return "force_quit"


class StructuredComputerControlTests(unittest.TestCase):
    def setUp(self):
        self.entry = AppEntry.create(
            "Discord",
            "executable",
            "C:/Apps/Discord.exe",
        )
        self.processes = FakeProcesses()
        self.opened_urls = []
        self.control = ComputerControl(
            PolicyEngine(),
            catalog=WindowsAppCatalog(entries=(self.entry,)),
            launcher=lambda _entry: None,
            processes=self.processes,
            browser=SafeBrowserControl(
                opener=lambda url: self.opened_urls.append(url) or True
            ),
        )

    def test_graceful_close_uses_resolved_catalog_entry(self):
        ready = self.control.prepare(ComputerActionRequest("close_app", "Discord"))
        result = self.control.execute(ready.prepared)

        self.assertEqual(result.status, "closed")
        self.assertEqual(len(self.processes.closed), 1)

    def test_force_quit_uses_same_generic_process_backend(self):
        ready = self.control.prepare(
            ComputerActionRequest("force_quit_app", "Discord")
        )
        unconfirmed = self.control.execute(ready.prepared)
        result = self.control.execute(ready.prepared, confirmed=True)

        self.assertEqual(unconfirmed.status, "confirmation_required")
        self.assertEqual(result.status, "force_quit")
        self.assertEqual(len(self.processes.force_quit_calls), 1)

    def test_open_url_executes_only_resolved_url(self):
        ready = self.control.prepare(ComputerActionRequest(
            "open_url",
            "YouTube",
            url="https://youtube.com",
        ))
        result = self.control.execute(ready.prepared)

        self.assertEqual(result.status, "url_dispatched")
        self.assertEqual(self.opened_urls, ["https://youtube.com"])

    def test_delete_requires_confirmation_and_uses_prepared_exact_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            recycle = root / "recycle"
            allowed = root / "allowed"
            recycle.mkdir()
            allowed.mkdir()
            source = allowed / "Notes"
            source.mkdir()
            filesystem = SafeFilesystemControl(
                [allowed],
                recycler=lambda path: path.rename(recycle / path.name),
            )
            control = ComputerControl(
                PolicyEngine(),
                catalog=WindowsAppCatalog(entries=(self.entry,)),
                filesystem=filesystem,
            )
            ready = control.prepare(ComputerActionRequest(
                "delete_folder",
                "Notes",
                location=str(allowed),
            ))

            unconfirmed = control.execute(ready.prepared)
            self.assertEqual(unconfirmed.status, "confirmation_required")
            self.assertTrue(source.is_dir())

            confirmed = control.execute(ready.prepared, confirmed=True)

            self.assertFalse(source.exists())
            self.assertEqual(confirmed.status, "folder_deleted")
            self.assertTrue((recycle / "Notes").is_dir())


if __name__ == "__main__":
    unittest.main()
