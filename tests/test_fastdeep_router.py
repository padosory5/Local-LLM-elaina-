"""The shadow FAST/DEEP router (brain/fastdeep_router.py).

Two things are pinned: the rule itself, and that it is only a shadow -- the
decision is recorded in the turn's trace and the turn is answered exactly as
before.
"""

import contextlib
import dataclasses
import inspect
import io
import unittest

from brain import fastdeep_router as router
from core import turn_trace
from tests.test_turn_trace import _traced
from tests.turn_harness import build_engine


def _signals(**fields):
    base = {"intent": "knowledge_question", "budget": "explain", "shape": "explain"}
    base.update(fields)
    return router.ShadowSignals(**base)


class RuleTests(unittest.TestCase):

    def test_a_first_explanation_stays_fast(self):
        found = router.decide(_signals())
        self.assertEqual(found.decision, router.FAST)
        self.assertIn("first explanation", found.reason)

    def test_an_explanation_building_on_the_last_one_goes_deep(self):
        for fields, held in (
            ({"is_follow_up": True}, ("router_follow_up",)),
            ({"previous_budget": "explain"}, ("after_explanation",)),
            ({"intent": "clarification", "budget": "elaborate"}, ("re_explanation",)),
        ):
            with self.subTest(fields=fields):
                found = router.decide(_signals(**fields))
                self.assertEqual(found.decision, router.DEEP)
                self.assertEqual(found.continuation, held)
                self.assertEqual(found.confidence, "low")

    def test_agreeing_signals_make_it_high_confidence(self):
        found = router.decide(_signals(is_follow_up=True, previous_budget="elaborate"))
        self.assertEqual((found.decision, found.confidence), (router.DEEP, "high"))

    def test_what_stays_fast(self):
        for fields, reason in (
            ({"domain_claimed": True, "is_follow_up": True}, "deterministic"),
            ({"budget": "value", "shape": "value", "is_follow_up": True}, "not an explanation"),
            ({"is_follow_up": True, "topic_shift": True}, "new topic"),
        ):
            with self.subTest(fields=fields):
                found = router.decide(_signals(**fields))
                self.assertEqual(found.decision, router.FAST)
                self.assertIn(reason, found.reason)

    def test_nothing_but_the_turns_own_signals_decides(self):
        # No phrase, no scenario, no model call: the decision is a function
        # of these fields alone.
        self.assertEqual(list(inspect.signature(router.decide).parameters), ["signals"])
        names = {f.name for f in dataclasses.fields(router.ShadowSignals)}
        self.assertNotIn("said", names)
        self.assertNotIn("text", names)

    def test_the_record_carries_the_signals(self):
        record = router.decide(_signals(is_follow_up=True)).as_record()
        self.assertEqual(set(record), {"decision", "reason", "confidence", "continuation", "signals"})
        self.assertTrue(record["signals"]["is_follow_up"])


class ShadowOnlyTests(unittest.TestCase):

    def test_a_deep_decision_is_recorded_and_not_acted_on(self):
        explain = {"intent": "knowledge_question", "confidence": 1.0,
                   "normalized_request": "How do noise-cancelling headphones work?",
                   "speech_act": "information_request", "answer_shape": "explain",
                   "information_freshness": "stable"}
        again = dict(explain, normalized_request="Explain noise-cancelling headphones another way.",
                     is_follow_up=True)
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({"headphones work": explain, "another way": again})
        _traced(engine)
        engine.client.reply = ("A microphone hears the noise and the speaker plays its "
                               "mirror image, so the two cancel.")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                engine.chat("How do noise-cancelling headphones work?")
                first = turn_trace.last().as_record()
                engine.chat("Can you explain that another way?")
                second = turn_trace.last().as_record()
        finally:
            engine.close()

        self.assertEqual(first["context"]["shadow_route"]["decision"], router.FAST)
        shadow = second["context"]["shadow_route"]
        self.assertEqual(shadow["decision"], router.DEEP)
        self.assertEqual(shadow["confidence"], "high")
        self.assertEqual(set(shadow["continuation"]), {"router_follow_up", "after_explanation"})
        # The answer is written by the model that always writes it.
        answer_models = {c.get("model") for c in second.get("model_calls") or ()
                         if str(c.get("caller", "")).endswith(":collect_answer")}
        self.assertEqual(answer_models, {engine.conversation_model})
        self.assertEqual(second["context"]["model"], engine.conversation_model)

    def test_the_first_turn_has_no_previous_explanation(self):
        explain = {"intent": "knowledge_question", "confidence": 1.0,
                   "normalized_request": "Why does ice float?",
                   "speech_act": "information_request", "answer_shape": "explain",
                   "information_freshness": "stable"}
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({"ice float": explain})
        _traced(engine)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                engine.chat("Why does ice float?")
            shadow = turn_trace.last().as_record()["context"]["shadow_route"]
        finally:
            engine.close()
        self.assertEqual(shadow["signals"]["previous_budget"], "")
        self.assertEqual(shadow["decision"], router.FAST)


if __name__ == "__main__":
    unittest.main()
