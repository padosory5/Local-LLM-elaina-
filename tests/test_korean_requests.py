"""Reading Korean requests on their own terms.

Her user speaks Korean, and until now Korean reached the model loop with no
typed goal, no skill and no gate -- every guarantee built this week applied
to the English phrasing only. Korean is not English with different words:
the verb comes last, the app is a locative ("스포티파이*에서*"), the
performer comes first ("아이브*의* 뱅뱅"), and particles attach directly to
nouns, which is why a `\\b` after a Korean word never matches.
"""

import unittest

from brain.deliberation import decide, front_door, interpret
from brain.media_target import classify_spotify_media_request
from brain.skills import skill_for


class KoreanMediaTests(unittest.TestCase):
    def test_a_named_track_with_the_app_as_a_locative(self):
        goal = interpret("스포티파이에서 뱅뱅 틀어줘")

        self.assertEqual(goal.kind, "play_track")
        self.assertEqual(goal.value("title"), "뱅뱅")
        # The app is where, not what: it must not end up in the title.
        self.assertNotIn("스포티파이", goal.value("title"))

    def test_the_performer_comes_first_in_korean(self):
        goal = interpret("아이브의 뱅뱅 틀어줘")

        self.assertEqual(goal.value("title"), "뱅뱅")
        self.assertEqual(goal.value("artist"), "아이브")

    def test_liked_songs_by_its_korean_name(self):
        goal = interpret("좋아요 표시한 곡 틀어줘")

        self.assertEqual(goal.kind, "play_collection")
        self.assertEqual(goal.value("collection"), "liked songs")
        self.assertEqual(skill_for(goal).name, "play_collection")

    def test_a_request_naming_no_song_asks_in_korean_too(self):
        for said in ("노래 좀 틀어줘", "아무 노래나 틀어줘", "음악 틀어줘"):
            with self.subTest(said=said):
                goal = interpret(said)
                self.assertEqual(goal.kind, "play_unnamed")
                self.assertTrue(decide(goal).asks)

    def test_a_playlist_that_names_no_particular_one_still_asks(self):
        goal = interpret("내 플레이리스트 틀어줘")

        self.assertEqual(goal.kind, "play_unnamed")
        self.assertTrue(decide(goal).asks)

    def test_polite_and_plain_verb_endings_both_read(self):
        for said in (
            "뱅뱅 재생해줘", "뱅뱅 들려줘", "뱅뱅 틀어주세요", "뱅뱅 틀어",
        ):
            with self.subTest(said=said):
                request = classify_spotify_media_request(f"스포티파이에서 {said}")
                self.assertEqual(request.kind, "track")
                self.assertEqual(request.target.title, "뱅뱅")


class KoreanBoundaryTests(unittest.TestCase):
    """What Korean must *not* be read as."""

    def test_another_app_named_in_korean_is_left_alone(self):
        # 유튜브에서: the particle attaches to the noun, so a word-boundary
        # match would have missed it and claimed a YouTube request.
        self.assertIsNone(front_door.read("유튜브에서 뱅뱅 틀어줘"))
        self.assertEqual(
            classify_spotify_media_request("유튜브에서 뱅뱅 틀어줘").kind, "none",
        )

    def test_ordinary_korean_conversation_is_not_a_request(self):
        for said in ("오늘 날씨 어때", "잘 지냈어?", "고마워"):
            with self.subTest(said=said):
                self.assertIsNone(front_door.read(said))

    def test_a_korean_app_command_still_goes_through_the_router(self):
        # Closing an app is grounded well by the router already; this door
        # claims only what a skill serves.
        self.assertIsNone(front_door.read("스포티파이 꺼줘"))


class KoreanRoutingTests(unittest.TestCase):
    def test_korean_media_requests_reach_the_desktop_planner(self):
        for said in (
            "스포티파이에서 뱅뱅 틀어줘",
            "좋아요 표시한 곡 틀어줘",
            "노래 좀 틀어줘",
        ):
            with self.subTest(said=said):
                route = front_door.read(said)
                self.assertIsNotNone(route)
                self.assertEqual(route.operation, "ui_action")


if __name__ == "__main__":
    unittest.main()
