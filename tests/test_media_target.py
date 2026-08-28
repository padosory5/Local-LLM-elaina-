import unittest

from brain.desktop_action_planner import DesktopActionPlanner, _completion_contract
from brain.media_target import MediaTarget, parse_spotify_media_target
from tools.computer_control.windows_ui_observer import ControlInfo


class MediaTargetParsingTests(unittest.TestCase):
    def test_title_and_artist_are_kept_separate(self):
        target = parse_spotify_media_target(
            "Play Bang Bang by IVE in Spotify for me."
        )

        self.assertEqual(target.title, "Bang Bang")
        self.assertEqual(target.artist, "IVE")
        self.assertEqual(target.search_query, "Bang Bang IVE")

    def test_compound_search_wording_is_supported(self):
        target = parse_spotify_media_target(
            "Search for Bang Bang from IVE and open that music in Spotify to play."
        )

        self.assertEqual(target.title, "Bang Bang")
        self.assertEqual(target.artist, "IVE")

    def test_non_spotify_and_generic_music_requests_are_not_hijacked(self):
        self.assertIsNone(parse_spotify_media_target("Play a song in Spotify"))
        self.assertIsNone(parse_spotify_media_target("Play Bang Bang in YouTube"))


class ExactSpotifyActivationTests(unittest.TestCase):
    """The line between reaching a result and activating the wrong one."""

    def setUp(self):
        self.target = MediaTarget("Spotify", "Bang Bang", "IVE")
        self.controls = (
            ControlInfo("Button", "Search", element_id="scan-e0"),
            ControlInfo("Button", "Play", element_id="scan-e1"),
            ControlInfo("Hyperlink", "Bang Bang Radio", element_id="scan-e2"),
            ControlInfo("Hyperlink", "Bang Bang", element_id="scan-e3"),
            ControlInfo("Hyperlink", "IVE", element_id="scan-e4"),
        )

    def _refusal(self, tool="click_control", *, control="", element_id=""):
        return DesktopActionPlanner._media_activation_refusal(
            self.target,
            tool,
            {"window": "Spotify", "control": control, "element_id": element_id},
            self.controls,
        )

    # -- preparation is ordinary work ---------------------------------------

    def test_the_apps_own_search_can_still_be_clicked(self):
        self.assertIsNone(self._refusal(element_id="scan-e0"))

    def test_navigation_that_shares_no_word_with_the_title_is_allowed(self):
        self.assertIsNone(self._refusal(control="Home"))

    # -- activation must be the exact title, played not opened ---------------

    def test_a_single_click_on_the_title_is_redirected_to_play_media_item(self):
        refusal = self._refusal(element_id="scan-e3")

        self.assertEqual(refusal.status, "wrong_media_target")
        self.assertIn("play_media_item", refusal.message)

    def test_generic_play_is_blocked_before_the_cursor_moves(self):
        refusal = self._refusal(element_id="scan-e1")

        self.assertEqual(refusal.status, "wrong_media_target")
        self.assertFalse(refusal.succeeded)

    def test_radio_result_is_blocked_before_the_cursor_moves(self):
        refusal = self._refusal(element_id="scan-e2")

        self.assertEqual(refusal.status, "wrong_media_target")

    def test_playing_the_exact_title_with_nearby_artist_is_allowed(self):
        self.assertIsNone(
            self._refusal("play_media_item", element_id="scan-e3")
        )

    def test_playing_a_radio_row_is_blocked(self):
        refusal = self._refusal("play_media_item", element_id="scan-e2")

        self.assertEqual(refusal.status, "wrong_media_target")

    def test_title_plus_artist_is_not_treated_as_the_title_label(self):
        refusal = self._refusal(
            "play_media_item", control="Bang Bang by IVE",
        )

        self.assertEqual(refusal.status, "wrong_media_target")

    def test_same_title_without_nearby_artist_is_blocked(self):
        controls = (
            ControlInfo("Hyperlink", "Bang Bang", element_id="scan-e0"),
            ControlInfo("Hyperlink", "Jessie J", element_id="scan-e1"),
        )

        refusal = DesktopActionPlanner._media_activation_refusal(
            self.target,
            "play_media_item",
            {"window": "Spotify", "element_id": "scan-e0"},
            controls,
        )

        self.assertEqual(refusal.status, "wrong_media_target")
        self.assertIn("artist", refusal.message)

    def test_completion_requires_title_activation_and_artist_search_context(self):
        contract = _completion_contract("Play Bang Bang by IVE in Spotify")
        self.assertEqual(contract.activation_target_terms, frozenset({"bang"}))
        self.assertEqual(contract.activation_context_terms, frozenset({"ive"}))


if __name__ == "__main__":
    unittest.main()
