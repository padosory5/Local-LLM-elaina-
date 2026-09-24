"""The number she worked out is the one thing the answer may not lose.

Measured live on 2026-09-12, running
``scripts/live_contamination_check.py --case arithmetic_in_a_social_thread``
-- three turns of sympathy, then "what's 2+2":

    Elaina: That's the result of 2 plus 2.
    Elaina: That's the result.

The router read the turn as a calculation both times and the answer path
lost the answer. ``AnswerCompletionGuard`` exists to stop exactly that and
could not see the first one: it asked whether the reply contained a digit,
and "2 plus 2" contains two of them. The operands are the question's own
numbers; the answer is the one that was not there before.

The two halves are tested here as one turn each, because the unit tests in
test_response_policy.py prove the rule and this proves it is *reached*: a
rewrite that loses the result is refused, and a result lost after every
check has run is put back.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from tests.turn_harness import ScriptedClient, build_engine


ASKED = "what's 2+2"
CALCULATION = {
    "intent": "calculation",
    "confidence": 0.99,
    "normalized_request": "2 + 2",
    "reason": "The user is asking for a mathematical calculation.",
    "topic": "mathematics",
    "is_follow_up": False,
    "speech_act": "information_request",
}
# Long enough that the voice-length rewrite runs, which is what it takes
# for a second model pass to get its hands on a finished answer.
DRAFT = (
    "2 + 2 equals 4. Adding two and two together gives four every time. "
    "That is the whole of it, and there is nothing else to work out here."
)
# What came back live, with the result gone and the operands still in it.
LOSES_IT = "That’s the result of 2 plus 2."


class RewriteClient(ScriptedClient):
    """Answers correctly, then rewrites the answer away.

    The rewrite is recognised by the section header the engine puts in
    front of the draft, so this stays a test of the engine's own prompt
    rather than of a call count.
    """

    def chat(self, **kwargs):
        messages = kwargs.get("messages") or []
        blob = " ".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict)
        )
        if "DRAFT ANSWER" in blob or "deferred or stopped" in blob:
            return {"message": {"content": LOSES_IT}}
        return super().chat(**kwargs)


class TheResultSurvivesTheAnswerPathTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({ASKED: CALCULATION})
        client = RewriteClient(routes={ASKED: CALCULATION}, reply=DRAFT)
        self.engine.client = client
        for owner in (self.engine.intent_router,
                      getattr(self.engine, "task_intent_gate", None)):
            if owner is not None and hasattr(owner, "client"):
                owner.client = client

    def tearDown(self):
        self.engine.close()

    def test_a_rewrite_that_drops_the_result_is_refused(self):
        with contextlib.redirect_stdout(io.StringIO()):
            reply = self.engine.chat(ASKED)

        self.assertIn("4", reply)

    def test_two_drafts_without_a_number_are_answered_by_computing_it(self):
        # The other half, measured on the full contamination suite: the
        # first draft had no number, the completion guard asked again, the
        # second had none either, and "That's the result." went out.
        client = RewriteClient(routes={ASKED: CALCULATION}, reply=LOSES_IT)
        self.engine.client = client
        self.engine.intent_router.client = client

        with contextlib.redirect_stdout(io.StringIO()):
            reply = self.engine.chat(ASKED)

        self.assertIn("4", reply)

    def test_only_plain_arithmetic_is_computed(self):
        # A word problem is the calculation planner's job, and a request
        # that is not an expression is nobody's: neither is answered by
        # this last resort.
        for request in (
            "Split a 650 dollar total among three people.",
            "the square root of 17",
            "",
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    self.engine._say_the_arithmetic(request), "",
                )
        self.assertIn("4", self.engine._say_the_arithmetic("2 + 2"))

    def test_a_result_lost_after_every_check_is_put_back(self):
        # The last guard in the answer path, standing in for whichever one
        # drops it on the day: the finished reply no longer says four.
        self.engine._their_name_not_hers = lambda said, reply: LOSES_IT

        with contextlib.redirect_stdout(io.StringIO()):
            reply = self.engine.chat(ASKED)

        self.assertIn("4", reply)
        self.assertNotIn("Adding two and two", reply)


if __name__ == "__main__":
    unittest.main()
