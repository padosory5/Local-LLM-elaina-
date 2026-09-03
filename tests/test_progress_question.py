"""Asking what she is doing has an answer she already knows.

B-46, from session 2. One turn after a lookup she had offered and never
started:

    User:   Why is it taking so long?
    Elaina: What would you like me to do next?

Nothing was running, and nothing said so. The information exists -- a
turn in flight, an offer parked and unanswered, or neither -- and the
question was answered as if it were a fresh request for work.

The trigger for it was B-45 (a "Yeah" that started nothing), and that is
fixed. This is the other half: when the person asks how it is going, the
answer is what is actually going on, including "nothing is".
"""

import unittest

from brain import progress_question
from tests.turn_harness import build_engine


class ReadingTheQuestionTests(unittest.TestCase):

    def test_the_live_turn_asks_about_progress(self):
        for said in (
            "Why is it taking so long?",
            "why is this taking so long",
            "are you doing it?",
            "is it done yet",
            "what's happening",
            "did you find anything yet",
            "are you still working on it",
            "아직이야?",
        ):
            with self.subTest(said=said):
                self.assertTrue(progress_question.asks_about_progress(said))

    def test_an_ordinary_turn_does_not(self):
        for said in (
            "what time is it",
            "why is Seattle so expensive",
            "how long is the flight",
            "can you find me a studio",
            "that took a while",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    progress_question.asks_about_progress(said), said,
                )


class AnsweringFromWhatSheKnowsTests(unittest.TestCase):

    def test_nothing_running_says_so(self):
        engine = build_engine()
        engine.capability_offer.clear()

        answer = engine._progress_report()

        self.assertTrue(answer)
        self.assertNotIn("what would you like me to do next", answer.casefold())
        self.assertIn("not", answer.casefold())

    def test_a_parked_offer_is_named_as_waiting(self):
        engine = build_engine()
        engine.capability_offer.offer(
            capability_id="web_search",
            goal="find real places to travel in Washington",
            offer_text="want me to look up real ones?",
        )

        answer = engine._progress_report().casefold()

        self.assertTrue(
            "wait" in answer or "say" in answer or "go" in answer, answer,
        )

    def test_the_report_is_short_enough_to_say(self):
        engine = build_engine()

        self.assertLessEqual(len(engine._progress_report().split()), 25)


if __name__ == "__main__":
    unittest.main()
