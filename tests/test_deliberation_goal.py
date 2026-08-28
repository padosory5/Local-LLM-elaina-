"""Phase 1: a request read into slots, and the boundary that creates.

The bug class these close: a request travelling from the microphone to the
keyboard as one unbroken string. Asked to type something while holding a
sentence, the model typed the sentence -- "Play any songs from my liked
list" landed in Spotify's search box on top of the previous query. A value
the request named may be typed; the request restating itself may not.
"""

import unittest

from brain.deliberation import Goal, Slot, interpret
from brain.deliberation.goal import SOURCE_PROFILE, SOURCE_UTTERANCE


class InterpretationTests(unittest.TestCase):
    def test_a_named_track_becomes_title_artist_and_query(self):
        goal = interpret("Play Bang Bang by IVE in Spotify.")

        self.assertEqual(goal.kind, "play_track")
        self.assertEqual(goal.value("title"), "Bang Bang")
        self.assertEqual(goal.value("artist"), "IVE")
        self.assertEqual(goal.value("query"), "Bang Bang IVE")

    def test_a_request_naming_a_place_becomes_a_collection_goal(self):
        goal = interpret("Play any songs from my liked list in Spotify")

        self.assertEqual(goal.kind, "play_collection")
        self.assertEqual(goal.value("collection"), "liked songs")
        # Nothing here may be entered anywhere. That is the whole point.
        self.assertEqual(goal.typeable_values(), ())

    def test_a_request_that_names_no_track_and_no_known_place_stays_unnamed(self):
        goal = interpret("Play something from my playlist in Spotify")

        self.assertEqual(goal.kind, "play_unnamed")
        self.assertEqual(goal.typeable_values(), ())

    def test_quoted_text_is_the_value_not_the_sentence(self):
        goal = interpret("Type 'see you at six' in Notepad")

        self.assertEqual(goal.kind, "text_input")
        self.assertEqual(goal.value("text"), "see you at six")

    def test_a_search_query_excludes_the_place_to_search(self):
        # Looking for a flight is a request to choose among live options,
        # so it reads as research -- but the value she would type is still
        # the thing asked for, not the place she was told to look.
        goal = interpret("Search for cheap flights to Guam in the browser")

        self.assertEqual(goal.kind, "research")
        self.assertEqual(goal.value("query"), "cheap flights to Guam")

    def test_a_plain_lookup_is_still_a_search(self):
        goal = interpret("Search for the tallest building in Seoul")

        self.assertEqual(goal.kind, "search")
        self.assertEqual(goal.value("query"), "the tallest building in Seoul")

    def test_the_routers_appended_original_wording_is_read_too(self):
        goal = interpret(
            "Play Bang Bang by IVE in Spotify.\n"
            "Original user request: 스포티파이에서 뱅뱅 틀어줘"
        )

        self.assertEqual(goal.kind, "play_track")
        self.assertEqual(goal.value("title"), "Bang Bang")

    def test_a_request_naming_no_value_says_so(self):
        goal = interpret("Pause the music in Spotify")

        self.assertEqual(goal.kind, "generic")
        self.assertEqual(goal.typeable_values(), ())


class TypedValueBoundaryTests(unittest.TestCase):
    def test_a_slot_value_may_be_typed(self):
        goal = interpret("Play Bang Bang by IVE in Spotify.")

        self.assertTrue(goal.permits_typing("Bang Bang IVE"))
        self.assertTrue(goal.permits_typing("Bang Bang"))

    def test_the_request_itself_may_not_be_typed(self):
        goal = interpret("Play something from my liked list in Spotify")

        self.assertFalse(goal.permits_typing("Play any songs from my liked list"))
        self.assertTrue(goal.reads_as_instruction("play any songs from my liked list"))

    def test_a_reordered_restatement_is_still_the_request(self):
        goal = interpret("Search for cheap flights to Guam in the browser")

        self.assertFalse(
            goal.permits_typing("browser search for cheap flights to Guam")
        )

    def test_ordinary_prose_the_request_named_is_allowed(self):
        goal = interpret("Type 'see you at six' in Notepad")

        self.assertTrue(goal.permits_typing("see you at six"))

    def test_a_value_that_merely_starts_with_a_verb_word_is_allowed_by_slot(self):
        # "Play Store" is a thing to search for, not an instruction -- the
        # slot is what settles it, before any verb heuristic runs.
        goal = interpret("Search for play store in the browser")

        self.assertTrue(goal.permits_typing("play store"))

    def test_nothing_may_be_typed_for_a_request_that_named_nothing(self):
        goal = interpret("Pause the music in Spotify")

        self.assertFalse(goal.permits_typing("Pause the music in Spotify"))
        self.assertIn("did not name", goal.refusal_hint())


class ProvenanceTests(unittest.TestCase):
    def test_a_value_nobody_said_is_marked_as_an_assumption(self):
        goal = Goal(
            kind="play_collection",
            utterance="play something",
            slots={
                "collection": Slot(
                    "collection", "liked songs", SOURCE_PROFILE, 0.6,
                ),
            },
        )

        self.assertEqual(len(goal.assumptions), 1)
        self.assertTrue(goal.assumptions[0].is_assumed)

    def test_a_value_the_person_said_is_not_an_assumption(self):
        goal = Goal(
            kind="text_input",
            utterance="type hello",
            slots={"text": Slot("text", "hello", SOURCE_UTTERANCE)},
        )

        self.assertEqual(goal.assumptions, ())

    def test_slots_cannot_be_edited_after_the_fact(self):
        goal = interpret("Play Bang Bang by IVE in Spotify.")

        with self.assertRaises(TypeError):
            goal.slots["title"] = Slot("title", "something else")


if __name__ == "__main__":
    unittest.main()
