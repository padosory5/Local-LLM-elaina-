import unittest

from brain.desktop_action_planner import DesktopActionPlanner, _completion_contract
from brain.media_target import (
    MediaTarget,
    classify_spotify_media_request,
    parse_spotify_media_target,
)
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


class UnnamedMediaRequestTests(unittest.TestCase):
    """A request that names no track must never become a search query.

    Measured live: "Play any songs from my liked list" was read as
    title="any songs", artist="my liked list", and the whole sentence was
    typed into Spotify's search box on top of the previous query.
    """

    def test_a_collection_request_names_no_track(self):
        request = classify_spotify_media_request(
            "Play any songs from my liked list in Spotify"
        )

        self.assertEqual(request.kind, "unclear")
        self.assertIsNone(request.target)
        self.assertEqual(request.collection, "liked songs")
        self.assertIn("Which song", request.question)

    def test_a_library_is_never_read_as_the_artist(self):
        # "from my liked songs" splits on the same word as "by IVE".
        request = classify_spotify_media_request(
            "Play Bohemian Rhapsody from my liked songs in Spotify"
        )

        self.assertEqual(request.kind, "track")
        self.assertEqual(request.target.title, "Bohemian Rhapsody")
        self.assertEqual(request.target.artist, "")

    def test_quantities_and_categories_name_no_track(self):
        for goal in (
            "Play some music in Spotify",
            "Play a song in Spotify",
            "Play something in Spotify",
            "Play some kpop in Spotify",
            "Spotify, play anything",
        ):
            with self.subTest(goal=goal):
                self.assertEqual(
                    classify_spotify_media_request(goal).kind, "unclear"
                )
                self.assertIsNone(parse_spotify_media_target(goal))

    def test_a_real_title_that_opens_with_a_quantifier_still_counts(self):
        # "Some Nights" is a song, not a quantity of nights.
        request = classify_spotify_media_request(
            "Play Some Nights by fun in Spotify"
        )

        self.assertEqual(request.kind, "track")
        self.assertEqual(request.target.title, "Some Nights")

    def test_a_named_track_is_unaffected(self):
        request = classify_spotify_media_request(
            "Play Bang Bang by IVE in Spotify for me."
        )

        self.assertEqual(request.kind, "track")
        self.assertEqual(request.target.title, "Bang Bang")
        self.assertEqual(request.target.artist, "IVE")


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

    def test_a_korean_play_button_is_as_generic_as_an_english_one(self):
        # Measured live on Korean Spotify: the bare "재생하기" (Play) read as
        # a named item because this check only knew English words, so it
        # was clicked -- starting whatever happened to be queued.
        for label in ("재생하기", "Play", "일시 정지하기"):
            with self.subTest(label=label):
                self.assertTrue(DesktopActionPlanner._is_generic_control(label))

    def test_a_label_naming_a_track_is_not_generic(self):
        for label in ("BANG BANG 재생하기", "Bang Bang", "재생 목록"):
            with self.subTest(label=label):
                self.assertFalse(DesktopActionPlanner._is_generic_control(label))

    def test_a_korean_play_control_is_refused_for_a_named_track(self):
        controls = (
            ControlInfo("Button", "재생하기", element_id="k-e0"),
            ControlInfo("Hyperlink", "Bang Bang", element_id="k-e1"),
            ControlInfo("Hyperlink", "IVE", element_id="k-e2"),
        )

        refusal = DesktopActionPlanner._media_activation_refusal(
            MediaTarget("Spotify", "Bang Bang", "IVE"),
            "click_control",
            {"window": "Spotify", "element_id": "k-e0"},
            controls,
        )

        self.assertIsNotNone(refusal)
        self.assertEqual(refusal.status, "wrong_media_target")

    def test_completion_requires_title_activation_and_artist_search_context(self):
        contract = _completion_contract("Play Bang Bang by IVE in Spotify")
        self.assertEqual(contract.activation_target_terms, frozenset({"bang"}))
        self.assertEqual(contract.activation_context_terms, frozenset({"ive"}))


if __name__ == "__main__":
    unittest.main()
