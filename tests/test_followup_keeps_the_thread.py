"""A follow-up keeps the conversation it follows.

A turn starts clean when it is about something else: the person says so, the
router says the topic moved, or the subject the turn is filed under shares no
word with the subject the conversation was filed under. The last is a
comparison of two short labels, and a question about a word she has just used
gets a label of its own:

    온실효과가 정확히 뭐야?            (greenhouse effect)
    ... 긴 파장의 적외선입니다 ...
    적외선? 그거 뭐야?                (infrared radiation)
    -> 적외선은 ... 전자레인지나 열화상 카메라에서 사용됩니다.
    전자레인지? ... 그걸로 지구가 더워진다는 게 무슨 말이야?
    -> 전자레인지가 지구를 더워진다는 건, 그 장치가 전력 소비를 통해
       온실가스를 배출하기 때문입니다.

Starting clean moves a line: everything before it is retired for the turns
that follow as well, so one definition asked in passing ended the thread.

Measured over every recorded turn after the first of its conversation (1,135,
2026-10-08): the label comparison dropped the conversation on 225. On 167 of
them the router had called the turn a follow-up and had not said the topic
moved, and every one of those was inside one explanation -- 16 of 91
follow-ups in the last simulated-learner pilot. On the other 58 the router
had not called it a follow-up, and 47 of those were what the rule is for: the
next unrelated question ("Who wrote Pride and Prejudice?" after "What's 15% of
80?").

So the label comparison decides only for a turn the router did not call a
follow-up. What the person says, and the router saying the topic moved, start
a turn clean as they did.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace

from brain.intent_router import IntentDecision
from tests.turn_harness import ScriptedClient, build_engine


def _route(asked: str, topic: str, **more) -> IntentDecision:
    return IntentDecision(
        intent="knowledge_question", confidence=0.99, normalized_request=asked,
        reason="scripted", topic=topic, speech_act="information_request", **more,
    )


class TheDecisionTests(unittest.TestCase):
    """``_context_for_turn``: what the turn may carry."""

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.addCleanup(self.engine.close)

    def _inherits(self, held: str, route: IntentDecision) -> bool:
        self.engine._subject_before_turn = held
        with contextlib.redirect_stdout(io.StringIO()):
            decided = self.engine._context_for_turn(route, SimpleNamespace(subject=route.topic))
        return decided.inherit_history

    # -- what changed

    def test_a_follow_up_filed_under_another_subject_keeps_the_conversation(self):
        for held, asked, topic in (
            ("greenhouse effect", "What is infrared radiation?", "infrared radiation"),
            ("AI response generation", "What is learning in AI?", "AI learning"),
            ("model quantization", "What is 8-bit?", "bit depth"),
        ):
            with self.subTest(asked=asked):
                self.assertTrue(self._inherits(held, _route(asked, topic, is_follow_up=True)))

    # -- what did not

    def test_the_next_unrelated_question_starts_clean(self):
        for held, asked, topic in (
            ("calculation", "Who wrote Pride and Prejudice?", "literature"),
            ("emotions", "2 + 2", "mathematics"),
        ):
            with self.subTest(asked=asked):
                self.assertFalse(self._inherits(held, _route(asked, topic)))

    def test_a_follow_up_the_router_says_moved_on_starts_clean(self):
        self.assertFalse(self._inherits(
            "greenhouse effect",
            _route("What is a good film for tonight?", "films", is_follow_up=True, topic_shift=True),
        ))

    def test_a_follow_up_that_drops_what_it_followed_starts_clean(self):
        self.assertFalse(self._inherits(
            "wireless mouse",
            _route("actually forget the mouse, what's a good film for tonight?", "films",
                   is_follow_up=True),
        ))

    def test_the_same_subject_keeps_the_conversation_either_way(self):
        for follow_up in (True, False):
            with self.subTest(follow_up=follow_up):
                self.assertTrue(self._inherits(
                    "coffee", _route("How long should cold brew steep?", "coffee brewing",
                                     is_follow_up=follow_up),
                ))


GREENHOUSE = "What exactly is the greenhouse effect?"
FIRST_REPLY = "Greenhouse gases let sunlight in and absorb the infrared heat the ground gives off."
INFRARED = "Infrared? What's that?"
AUSTEN = "Who wrote Pride and Prejudice?"


def _scripted(asked: str, topic: str, **more) -> dict:
    return {
        "intent": "knowledge_question", "confidence": 0.99, "normalized_request": asked,
        "reason": "scripted", "topic": topic, "speech_act": "information_request",
        "information_freshness": "stable", "answer_shape": "explain", **more,
    }


class TheWholeTurnTests(unittest.TestCase):
    """The decision as two turns of ``ChatEngine.chat()`` reach it."""

    def _second_turn(self, second: str, route: dict) -> tuple[str, bool]:
        routes = {GREENHOUSE: _scripted(GREENHOUSE, "greenhouse effect"), second: route}
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine(routes)
        self.addCleanup(engine.close)
        client = ScriptedClient(routes=routes, reply=FIRST_REPLY)
        engine.client = client
        for owner in (engine.intent_router, getattr(engine, "task_intent_gate", None)):
            if owner is not None and hasattr(owner, "client"):
                owner.client = client
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            engine.chat(GREENHOUSE)
            seen = len(client.prompts)
            engine.chat(second)
        # Whether any prompt of the second turn was shown her first reply as history.
        carried = any(
            isinstance(message, dict) and message.get("role") == "assistant"
            and FIRST_REPLY in str(message.get("content"))
            for prompt in client.prompts[seen:] for message in prompt
        )
        return log.getvalue(), carried

    def test_a_question_about_a_word_she_used_is_answered_inside_the_thread(self):
        log, carried = self._second_turn(
            INFRARED, _scripted("What is infrared radiation?", "infrared radiation", is_follow_up=True),
        )

        self.assertNotIn("Starting clean", log)
        self.assertTrue(carried)

    def test_the_next_unrelated_question_is_answered_on_its_own(self):
        log, carried = self._second_turn(AUSTEN, _scripted(AUSTEN, "literature"))

        self.assertIn("Starting clean", log)
        self.assertFalse(carried)


if __name__ == "__main__":
    unittest.main()
