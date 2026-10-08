"""After a question, the sentence that mirrors it is the answer.

The final check takes a restatement of the person's message off the front of
a reply. One of its readings is a first sentence at least 80% alike to what
they said -- their own sentence handed back with the ending changed, which is
how Korean echoes ("회사에서 하루 종일 회의만 했어" -> "... 했네요").

Reviewed over every recorded turn (evals/echo_strip_replay.py, 2026-10-08):
that reading had fired twelve times, and never on something they had told
her. Every one was a question, and what went was its answer:

    What is the freezing point of water in Fahrenheit?
    -> [The freezing point of water is 32 degrees Fahrenheit.] That's when
       it turns from liquid to ice ...
    Okay, so it doesn't mean it's a big difference, right?
    -> [No, it doesn't mean it's a big difference.] Significance just means
       the result is probably real ...

The first kind was put back by the last check on a value question, without
its second sentence. The second kind, six yes/no verdicts, was simply gone.

So the reading applies only when they told her something: she is reacting
(the style layer's act) and what they said did not end in a question mark.
Both halves, because each alone has a hole. The router read that "..., right?"
as a statement when it was asked out of its thread, and a spoken question
often arrives without its mark. An exact repeat of their words, and a
restatement before a dash, are stripped as they were.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain.response_quality import ResponseQualityGuard as Guard
from core import turn_trace
from tests.turn_harness import ScriptedClient, build_engine

STRIPPED = "Removed a restatement of the current message"

FREEZING = "What is the freezing point of water in Fahrenheit?"
THE_ANSWER = "The freezing point of water is 32 degrees Fahrenheit."
FREEZING_DRAFT = f"{THE_ANSWER} That's when it turns from liquid to ice."

CHECK = "Okay, so it doesn't mean it's a big difference, right?"
THE_VERDICT = "No, it doesn’t mean it’s a big difference."
CHECK_DRAFT = f"{THE_VERDICT} Significance just means the result is probably real, not due to luck."

SHADOW = "So it's like the Earth's shadow only comes into play during an eclipse?"
SHADOW_VERDICT = "Yes, the Earth's shadow only comes into play during an eclipse."

MOVING = "I'm moving to Seattle next month"
MOVING_DRAFT = "You're moving to Seattle next month. That's exciting!"
MEETINGS = "회사에서 하루 종일 회의만 했어"
MEETINGS_DRAFT = "회사에서 하루 종일 회의만 했네요. 힘드셨겠습니다."


class TheReadingTests(unittest.TestCase):
    """``strip_current_turn_echo`` told whether a re-ended sentence is an echo."""

    def test_after_a_question_the_mirror_is_kept(self):
        for said, reply in (
            (FREEZING, FREEZING_DRAFT),
            (CHECK, CHECK_DRAFT),
            (SHADOW, f"{SHADOW_VERDICT} During a lunar eclipse the Earth blocks the sunlight."),
        ):
            with self.subTest(said=said):
                # Told nothing, the reading is as it was -- and takes the answer.
                self.assertNotEqual(Guard.strip_current_turn_echo(reply, said), reply)
                self.assertEqual(
                    Guard.strip_current_turn_echo(reply, said, re_ended=False), reply,
                )

    def test_their_exact_words_are_stripped_either_way(self):
        reply = "I see. Have a wonderful day."
        for re_ended in (True, False):
            with self.subTest(re_ended=re_ended):
                self.assertEqual(
                    Guard.strip_current_turn_echo(reply, "I see", re_ended=re_ended),
                    "Have a wonderful day.",
                )

    def test_a_restatement_before_a_dash_is_stripped_either_way(self):
        said = "I'm moving to Seattle on 9/13"
        reply = "You're moving to Seattle on 9/13—pack layers, it rains."
        stripped = Guard.strip_current_turn_echo(reply, said)
        self.assertNotEqual(stripped, reply)
        self.assertEqual(Guard.strip_current_turn_echo(reply, said, re_ended=False), stripped)


def _route(intent: str, speech_act: str, asked: str, **more) -> dict:
    return {
        "intent": intent, "confidence": 0.99, "normalized_request": asked,
        "reason": "scripted", "speech_act": speech_act,
        "information_freshness": "stable", "is_follow_up": False, **more,
    }


class TheWholeTurnTests(unittest.TestCase):
    """The reading as ``ChatEngine.chat()`` reaches it."""

    def _turn(self, said: str, route: dict, draft: str) -> tuple[str, str]:
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({said: route})
        self.addCleanup(engine.close)
        client = ScriptedClient(routes={said: route}, reply=draft)
        engine.client = client
        for owner in (engine.intent_router, getattr(engine, "task_intent_gate", None)):
            if owner is not None and hasattr(owner, "client"):
                owner.client = client
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            reply = engine.chat(said)
        return reply, log.getvalue()

    # -- what changed

    def test_an_answer_that_mirrors_the_question_is_kept(self):
        reply, log = self._turn(
            FREEZING,
            _route("knowledge_question", "information_request", FREEZING, answer_shape="state"),
            FREEZING_DRAFT,
        )

        self.assertNotIn(STRIPPED, log)
        self.assertEqual(reply, FREEZING_DRAFT)

    def test_the_verdict_on_an_understanding_check_is_kept(self):
        # As the router read it inside its thread on every recorded run.
        reply, log = self._turn(
            CHECK,
            _route("conversation", "correction", CHECK, answer_shape="explain", is_follow_up=True),
            CHECK_DRAFT,
        )

        self.assertNotIn(STRIPPED, log)
        self.assertTrue(reply.startswith("No, it doesn"), reply)

    def test_a_question_read_as_a_statement_keeps_its_answer(self):
        # As the router read the same words out of their thread: she is
        # "reacting", and only the question mark says otherwise.
        reply, log = self._turn(
            CHECK, _route("conversation", "statement", CHECK), CHECK_DRAFT,
        )

        self.assertNotIn(STRIPPED, log)
        self.assertTrue(reply.startswith("No, it doesn"), reply)

    def test_a_question_that_arrives_without_its_mark_keeps_its_answer(self):
        # Speech does not always bring the mark; the router knew it was asked.
        said = FREEZING.rstrip("?")
        reply, log = self._turn(
            said,
            _route("knowledge_question", "information_request", said, answer_shape="state"),
            FREEZING_DRAFT,
        )

        self.assertNotIn(STRIPPED, log)
        self.assertIn(THE_ANSWER, reply)

    # -- what did not

    def test_their_own_sentence_handed_back_is_still_stripped(self):
        for said, draft, rest in (
            (MOVING, MOVING_DRAFT, "That's exciting!"),
            (MEETINGS, MEETINGS_DRAFT, "힘드셨겠습니다."),
        ):
            with self.subTest(said=said):
                _reply, log = self._turn(said, _route("conversation", "statement", said), draft)

                self.assertIn(STRIPPED, log)
                # What the check itself left. A later stage may say more: with
                # nothing of theirs left in the reply, she acknowledges it.
                check = next(step for step in turn_trace.last().as_record()["steps"]
                             if step["name"] == "final_check")
                self.assertEqual(check["after"], rest)


if __name__ == "__main__":
    unittest.main()
