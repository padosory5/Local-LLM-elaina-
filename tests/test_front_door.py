"""Move 1: the request is read before a model is asked where to send it.

Measured on the live system before this existed, three runs each:

    "book me a hotel in guam"   -> web_search      (the gate never fired)
    "play my liked songs"       -> browser_action  (the skill never ran)

Both are requests the interpreter can read outright. These tests pin the
two halves of the fix: that such a request is claimed and sent to the
planner that owns its gate, and — just as important — that anything the
interpreter cannot read is left alone for the router.
"""

import unittest

from brain.deliberation import decide, front_door, interpret
from brain.skills import skill_for


class ClaimedRequestTests(unittest.TestCase):
    """What she can read, she routes herself."""

    def test_a_named_track_goes_to_the_desktop_planner_with_its_slots(self):
        route = front_door.read("play bang bang by ive")

        self.assertIsNotNone(route)
        self.assertEqual(route.operation, "ui_action")
        self.assertEqual(route.goal.kind, "play_track")
        # The artist is kept exactly as it was said -- lowercase here,
        # because that is how it was typed. Matching is case-insensitive.
        self.assertEqual(route.goal.value("artist").casefold(), "ive")

    def test_the_request_that_started_all_this_reaches_its_skill(self):
        route = front_door.read("play my liked songs")

        self.assertIsNotNone(route)
        self.assertEqual(route.operation, "ui_action")
        self.assertEqual(route.goal.kind, "play_collection")
        self.assertEqual(skill_for(route.goal).name, "play_collection")

    def test_naming_the_app_is_not_required_to_be_understood(self):
        # The old parser demanded the literal word "spotify", so the
        # natural phrasing typed as nothing and matched no skill.
        with_app = front_door.read("play my liked songs in spotify")
        without = front_door.read("play my liked songs")

        self.assertEqual(with_app.goal.kind, without.goal.kind)

    def test_a_booking_reaches_the_planner_that_asks_for_dates(self):
        route = front_door.read("book me a hotel in guam")

        self.assertIsNotNone(route)
        self.assertEqual(route.operation, "browser_action")
        self.assertEqual(route.goal.kind, "booking")
        # And the gate there will ask before anything is opened.
        self.assertTrue(decide(route.goal).asks)

    def test_a_request_naming_no_song_still_goes_where_it_can_be_asked_about(self):
        route = front_door.read("play some music")

        self.assertIsNotNone(route)
        self.assertEqual(route.operation, "ui_action")
        self.assertTrue(decide(route.goal).asks)

    def test_liked_songs_shuffle_and_find_stay_in_the_media_flow(self):
        shuffle = front_door.read("click shuffle on my liked list")
        find = front_door.read("scroll down on my liked list and find 아무노래")

        self.assertEqual(shuffle.goal.kind, "shuffle_collection")
        self.assertEqual(shuffle.goal.value("collection"), "liked songs")
        self.assertEqual(find.goal.kind, "find_in_collection")
        self.assertEqual(find.goal.value("collection"), "liked songs")
        self.assertEqual(find.goal.value("title"), "아무노래")


class LeftToTheRouterTests(unittest.TestCase):
    """What she cannot read, she does not guess at."""

    def test_conversation_is_never_claimed(self):
        for text in (
            "hey how was your day",
            "what do you think about that film",
            "i'm tired",
        ):
            with self.subTest(text=text):
                self.assertIsNone(front_door.read(text))

    def test_a_play_request_that_is_not_music_is_left_alone(self):
        # "Play chess" is not a song title, and assuming it was would be
        # exactly the confident wrong answer this layer exists to prevent.
        self.assertIsNone(front_door.read("play chess"))

    def test_another_app_named_outright_is_left_alone(self):
        self.assertIsNone(front_door.read("play a song on youtube"))
        self.assertIsNone(front_door.read("play lofi in the browser"))

    def test_a_control_named_play_is_a_click_not_a_song(self):
        # Measured: this parsed as a media request for a track called
        # "in Spotify", and the media guard then refused an ordinary click.
        self.assertIsNone(front_door.read("click Play in Spotify"))
        self.assertEqual(interpret("click Play in Spotify").kind, "generic")

    def test_ordinary_searches_still_go_through_the_router(self):
        # The task planner owns the research conversation; claiming it here
        # would be a second opinion on a question already being asked. Such
        # a request is still *read* -- so the gate can ask about it before
        # any path begins -- but it carries no destination of its own.
        research = front_door.read("find hotels in guam")
        self.assertIsNotNone(research)
        self.assertEqual(research.operation, "")
        self.assertFalse(research.asks)

        self.assertIsNone(front_door.read("what is the tallest building in seoul"))

    def test_an_app_command_still_goes_through_the_router(self):
        # close/open are grounded well by the router today, and it weighs
        # context this deliberately does not look at.
        self.assertIsNone(front_door.read("close spotify"))


if __name__ == "__main__":
    unittest.main()
