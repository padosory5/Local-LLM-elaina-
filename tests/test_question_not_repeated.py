"""A question she just asked is not asked again, word for word.

Measured in three of three English dogfood runs:

    You:     anyway. decent cheap headphone brand?
    Elaina:  Over-ear or in-ear?
    You:     under 100
    Elaina:  Over-ear or in-ear?
    You:     you sure about that?
    Elaina:  Are you looking for over-ear or in-ear style headphones?

The branch that re-said the pending question was written for "yeah" -- an
acknowledgement must not become a typed constraint -- and it caught "under
100" too, which was an answer, just to a different question. Repeated turn
after turn, it was the largest single source of English self-repetition in
the quality metric.

Asked once and not answered, the question is dropped and the turn is read on
its own. The first time a reply misses, the old behaviour stands.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain.deliberation.goal import Goal
from tests.turn_harness import build_engine

QUESTION = "Over-ear or in-ear?"


class TheQuestionIsNotRepeatedTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
            self.problem = self.engine.task_sessions.note_recommendation_turn(
                "recommend a decent cheap headphone brand", subject="headphones",
            )
        self.engine.clarification.offer(
            goal=Goal(kind="recommendation", utterance="headphones"),
            slot="type", question=QUESTION, task_id=self.problem.id,
        )

    def tearDown(self):
        self.engine.close()

    def _route(self, said: str):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._route_turn(said, timings={})

    def test_an_answer_to_something_else_is_not_met_with_the_question_again(self):
        self.engine.conversation.add("user", "decent cheap headphone brand?")
        self.engine.conversation.add("assistant", QUESTION)

        routing = self._route("under 100")

        self.assertNotEqual(routing.locked_response, QUESTION)
        self.assertIsNone(self.engine.clarification.peek())

    def test_the_first_miss_keeps_the_old_behaviour(self):
        # Not asked on the turn before -- the gate still holds it, as it
        # was written to.
        self.engine.conversation.add("assistant", "Sure.")

        routing = self._route("yeah")

        self.assertEqual(routing.locked_response, QUESTION)


if __name__ == "__main__":
    unittest.main()
