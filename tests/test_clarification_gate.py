"""Phase 3: act, act and say the assumption, or ask exactly one question.

The complaint this answers: "she's not even asking me stuff when she
doesn't have a clear goal -- she will just do the things she is programmed
to." The gate is the one place that decides, and the middle exit is what
keeps it from becoming the opposite problem: an assistant that asks about
everything is as unusable as one that never asks.
"""

import unittest

from brain.deliberation import (
    ACT,
    ACT_AND_SAY,
    ASK,
    ClarificationGate,
    Goal,
    Slot,
    decide,
    interpret,
)
from brain.deliberation.goal import SOURCE_ASKED, SOURCE_WORLD


class GatePolicyTests(unittest.TestCase):
    def test_a_complete_request_is_acted_on_without_a_question(self):
        decision = decide(interpret("Play Bang Bang by IVE in Spotify."))

        self.assertEqual(decision.action, ACT)
        self.assertEqual(decision.question, "")

    def test_a_request_naming_no_song_asks_which_one(self):
        decision = decide(interpret("Play some music in Spotify"))

        self.assertEqual(decision.action, ASK)
        self.assertEqual(decision.missing, "title")
        self.assertIn("Which song", decision.question)

    def test_a_collection_she_can_play_is_acted_on_not_asked_about(self):
        # "Play my liked songs" names a place rather than an item, and she
        # has a procedure for that place. Nothing is missing.
        decision = decide(
            interpret("Play any songs from my liked list in Spotify")
        )

        self.assertEqual(decision.action, ACT)
        self.assertEqual(decision.goal.kind, "play_collection")
        self.assertEqual(decision.goal.value("collection"), "liked songs")

    def test_a_collection_that_names_no_particular_one_still_asks(self):
        # "My playlist" is not a playlist. Guessing which one would be the
        # confident wrong answer this layer exists to prevent.
        decision = decide(
            interpret("Play something from my playlist in Spotify")
        )

        self.assertEqual(decision.action, ASK)
        self.assertIn("a whole playlist", decision.question)

    def test_a_vague_request_acts_on_what_she_last_played_and_says_so(self):
        decision = decide(
            interpret("Play some music in Spotify"), recent_subject="Bang Bang",
        )

        self.assertEqual(decision.action, ACT_AND_SAY)
        # Once the title is known it is a track request, so the skill that
        # serves those is the one that runs.
        self.assertEqual(decision.goal.kind, "play_track")
        self.assertEqual(decision.goal.value("title"), "Bang Bang")
        self.assertIn("Bang Bang", decision.assumption)
        self.assertIn("say the word", decision.assumption)

    def test_a_filled_in_value_is_marked_as_not_something_they_said(self):
        decision = decide(
            interpret("Play some music in Spotify"), recent_subject="Bang Bang",
        )

        self.assertEqual(len(decision.goal.assumptions), 1)
        self.assertEqual(decision.goal.slots["title"].source, SOURCE_WORLD)

    def test_a_missing_value_for_an_ordinary_request_is_asked_about(self):
        goal = Goal(kind="text_input", utterance="type something in Notepad")

        decision = decide(goal)

        self.assertEqual(decision.action, ASK)
        self.assertEqual(decision.missing, "text")

    def test_a_request_she_cannot_read_at_all_asks_rather_than_guesses(self):
        decision = decide(interpret(""))

        self.assertEqual(decision.action, ASK)
        self.assertIn("say it another way", decision.question)


class BookingPreconditionTests(unittest.TestCase):
    """Committing to something needs its inputs; looking around does not."""

    def test_a_booking_without_dates_is_asked_about_before_anything_opens(self):
        decision = decide(interpret("Book me a hotel in Guam"))

        self.assertEqual(decision.action, ASK)
        self.assertEqual(decision.missing, "dates")
        self.assertIn("check-in and check-out", decision.question)

    def test_a_booking_with_dates_proceeds(self):
        decision = decide(
            interpret("Book me a hotel in Guam on 2026-09-01 to 2026-09-04")
        )

        self.assertEqual(decision.action, ACT)
        self.assertEqual(decision.goal.value("dates"), "2026-09-01 to 2026-09-04")

    def test_looking_around_is_not_blocked_on_the_same_inputs(self):
        # The task planner already offers the dates/area/budget conversation
        # for research. Asking again here would be a second question for one
        # request, and would interrupt a task that had already answered it.
        decision = decide(interpret("Find hotels in Guam"))

        self.assertEqual(decision.action, ACT)
        self.assertEqual(decision.goal.kind, "research")

    def test_the_answer_keeps_the_original_request_and_adds_the_dates(self):
        decision = decide(interpret("Book me a hotel in Guam"))
        gate = ClarificationGate()
        pending = gate.offer(
            goal=decision.goal,
            slot=decision.missing,
            question=decision.question,
            template=decision.template,
        )

        completed = pending.completed("2026-09-01 to 2026-09-04")

        self.assertEqual(completed.kind, "booking")
        self.assertEqual(completed.value("dates"), "2026-09-01 to 2026-09-04")
        self.assertIn("Guam", completed.value("subject"))
        self.assertEqual(decide(completed).action, ACT)

    def test_a_category_with_no_such_requirement_is_not_delayed(self):
        decision = decide(interpret("Find the cheapest second-hand RTX 5080"))

        self.assertEqual(decision.action, ACT)


class AnswerBindingTests(unittest.TestCase):
    def setUp(self):
        self.gate = ClarificationGate()
        decision = decide(interpret("Play some music in Spotify"))
        self.pending = self.gate.offer(
            goal=decision.goal,
            slot=decision.missing,
            question=decision.question,
            template=decision.template,
        )

    def test_an_answer_completes_the_original_request(self):
        completed = self.pending.completed("Bang Bang by IVE")

        self.assertEqual(completed.kind, "play_track")
        self.assertEqual(completed.value("title"), "Bang Bang")
        self.assertEqual(completed.value("artist"), "IVE")

    def test_the_answered_value_is_recorded_as_having_been_asked_for(self):
        completed = self.pending.completed("Bang Bang by IVE")

        self.assertEqual(completed.slots["title"].source, SOURCE_ASKED)

    def test_a_new_instruction_is_not_swallowed_as_an_answer(self):
        for reply in (
            "no, open Discord instead",
            "never mind",
            "pause the music",
        ):
            with self.subTest(reply=reply):
                self.assertFalse(self.pending.reads_as_answer(reply))

    def test_a_short_reply_naming_a_song_is_an_answer(self):
        self.assertTrue(self.pending.reads_as_answer("Bang Bang by IVE"))

    def test_a_paragraph_is_a_change_of_subject_not_an_answer(self):
        self.assertFalse(self.pending.reads_as_answer(
            "actually I was thinking about what to have for dinner tonight "
            "and whether the weather will hold up tomorrow"
        ))

    def test_only_one_question_is_held_at_a_time(self):
        self.gate.offer(
            goal=Goal(kind="search", utterance="search"),
            slot="query",
            question="What should I search for?",
            template="",
        )

        self.assertEqual(self.gate.peek().slot, "query")

    def test_an_expired_question_is_no_longer_answerable(self):
        gate = ClarificationGate(expiry_seconds=15)
        gate.offer(
            goal=self.pending.goal, slot="title",
            question="Which song?", template="Play {answer} in Spotify.",
        )
        gate._pending = gate._pending.__class__(
            goal=gate._pending.goal,
            slot=gate._pending.slot,
            question=gate._pending.question,
            template=gate._pending.template,
            created_at=0.0,
            expires_at=0.0,
        )

        self.assertIsNone(gate.peek())

    def test_a_question_with_no_template_is_not_bound_automatically(self):
        gate = ClarificationGate()
        pending = gate.offer(
            goal=Goal(kind="text_input", utterance="type in Notepad"),
            slot="text",
            question="What would you like me to type?",
            template="",
        )

        self.assertFalse(pending.bindable)
        self.assertFalse(pending.reads_as_answer("see you at six"))
        self.assertIsNone(pending.completed("see you at six"))


if __name__ == "__main__":
    unittest.main()
