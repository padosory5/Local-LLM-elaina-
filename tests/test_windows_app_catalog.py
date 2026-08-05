import unittest

from tools.windows_app_catalog import AppEntry, WindowsAppCatalog


class BilingualAppResolutionTests(unittest.TestCase):
    """A catalog app's real registered display name can be in a different
    language than the name a user or the router names it in -- on a
    Korean-locale system, Windows' Settings app is only ever registered as
    "설정", never "Settings"."""

    def test_resolves_an_english_query_against_a_korean_display_name(self):
        catalog = WindowsAppCatalog(entries=(
            AppEntry.create("설정", "uwp", "windows.immersivecontrolpanel"),
        ))

        result = catalog.resolve("Settings")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.entry.display_name, "설정")

    def test_resolves_a_korean_query_against_an_english_display_name(self):
        catalog = WindowsAppCatalog(entries=(
            AppEntry.create("Notepad", "executable", "C:/Windows/notepad.exe"),
        ))

        result = catalog.resolve("메모장")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.entry.display_name, "Notepad")

    def test_prefers_a_literal_match_over_a_translated_one(self):
        catalog = WindowsAppCatalog(entries=(
            AppEntry.create("Settings", "uwp", "some.other.settings.app"),
            AppEntry.create("설정", "uwp", "windows.immersivecontrolpanel"),
        ))

        result = catalog.resolve("Settings")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.entry.display_name, "Settings")

    def test_unrelated_query_still_reports_not_found(self):
        catalog = WindowsAppCatalog(entries=(
            AppEntry.create("설정", "uwp", "windows.immersivecontrolpanel"),
        ))

        result = catalog.resolve("Definitely Not Installed")

        self.assertEqual(result.status, "not_found")


class FuzzyMisheardNameTests(unittest.TestCase):
    """STT can mishear a stylized brand name ("Battle.net" -> "battle
    nest") the same way whichever way it's spoken. This must surface the
    close real match to confirm, not silently guess and not just give up."""

    def test_a_likely_stt_mishearing_is_offered_as_a_single_candidate(self):
        catalog = WindowsAppCatalog(entries=(
            AppEntry.create(
                "Battle.net Launcher", "shortcut", "C:/Apps/Battle.net.lnk",
            ),
        ))

        result = catalog.resolve("battle nest")

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.candidates, ("Battle.net Launcher",))

    def test_an_unrelated_query_is_not_forced_into_a_fuzzy_match(self):
        catalog = WindowsAppCatalog(entries=(
            AppEntry.create(
                "Battle.net Launcher", "shortcut", "C:/Apps/Battle.net.lnk",
            ),
            AppEntry.create("Discord", "shortcut", "C:/Apps/Discord.lnk"),
        ))

        result = catalog.resolve("Spotify")

        self.assertEqual(result.status, "not_found")


if __name__ == "__main__":
    unittest.main()
