"""The four fixes made after the Phase 3 rerun, besides the response budget.

* Every model call asks for one context window large enough for the
  router's prompt and answer (core/model_context.py).
* The router reports what shape of answer a request wants
  (``answer_shape``), and a computed answer is a statement.
* Mathematical notation is not a collection of value claims: the grounded
  value validator reads claims from prose (brain/spoken_notation.py).
"""

import json
import os
import unittest
from unittest.mock import patch

from brain.grounded_values import GroundedValueGuard
from brain.spoken_notation import notation_spans, prose
from core import model_context


class _Recorder:
    def __init__(self):
        self.calls = []
        self.host = "h"

    def chat(self, *args, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": "ok"}}

    def generate(self, *args, **kwargs):
        self.calls.append(kwargs)
        return {"response": "ok"}


class ContextWindowTests(unittest.TestCase):

    def test_every_call_asks_for_the_configured_window(self):
        inner = _Recorder()
        client = model_context.ContextSizedClient(inner, 6144)
        client.chat(model="m", messages=[], options={"temperature": 0})
        client.generate(model="m", prompt="x")
        self.assertEqual([c["options"]["num_ctx"] for c in inner.calls], [6144, 6144])
        self.assertEqual(inner.calls[0]["options"]["temperature"], 0)

    def test_a_call_that_sets_its_own_window_keeps_it(self):
        inner = _Recorder()
        model_context.ContextSizedClient(inner, 6144).chat(options={"num_ctx": 2048})
        self.assertEqual(inner.calls[0]["options"]["num_ctx"], 2048)

    def test_zero_leaves_ollama_its_default(self):
        inner = _Recorder()
        model_context.ContextSizedClient(inner, 0).chat(options={})
        self.assertNotIn("num_ctx", inner.calls[0]["options"])

    def test_attributes_pass_through(self):
        inner = _Recorder()
        client = model_context.ContextSizedClient(inner, 6144)
        client.reply = "set"
        self.assertEqual(inner.reply, "set")
        self.assertEqual(client.host, "h")

    def test_the_window_is_configured(self):
        class Config:
            def get(self, *keys, default=None, required=False):
                return 8192

        with patch.dict(os.environ, {"ELAINA_NUM_CTX": ""}):
            self.assertEqual(model_context.configured(Config()), 8192)
        with patch.dict(os.environ, {"ELAINA_NUM_CTX": "4096"}):
            self.assertEqual(model_context.configured(Config()), 4096)

    def test_the_router_prompt_and_answer_fit_the_window(self):
        # The measured worst case: a 4,081-token prompt and a 360-token answer.
        self.assertGreater(model_context.DEFAULT_NUM_CTX, 4081 + 360 + 512)


class AnswerShapeTests(unittest.TestCase):

    def test_the_router_reads_the_shape(self):
        from brain.intent_router import SemanticIntentRouter

        for given, expected in (("example", "example"), ("State", "state"),
                                ("lecture", ""), (None, "")):
            payload = {"intent": "knowledge_question", "confidence": 0.9,
                       "normalized_request": "q"}
            if given is not None:
                payload["answer_shape"] = given
            decision = SemanticIntentRouter._parse_decision(json.dumps(payload), "q")
            self.assertEqual(decision.answer_shape, expected, given)

    def test_a_computed_answer_is_a_statement(self):
        from brain.domain_resolver import claim

        for said in ("What time is it?", "what's 6 times 7", "convert 3 miles to km"):
            self.assertEqual(claim(said).decision().answer_shape, "state", said)


class NotationIsNotAClaimTests(unittest.TestCase):

    SEARCHED = "Course listing: MATH 207 covers sequences and series."

    def test_notation_is_found_by_its_grammar(self):
        for text in ("$ a + b\\cdot c $", "y = 3t - 4", "g(t)² + 2t", "k! / (k - 1)!"):
            self.assertTrue(notation_spans(text), text)
        for text in ("It costs $40-$60.", "Call 206-555-0101.", "It runs at 60 Hz.",
                     "We ate 3 + 2 cookies."):
            self.assertFalse(notation_spans(text), text)

    def test_prose_keeps_the_length_and_the_claims(self):
        text = "Roughly $ 2 + y $ units, at $15 each."
        self.assertEqual(len(prose(text)), len(text))
        self.assertIn("$15", prose(text))

    def test_an_expression_in_an_answer_is_not_an_unsupported_value(self):
        reply = ("A series can stand in for a function: $ 1 - t + \\frac{t^2}{2} "
                 "- \\frac{t^3}{6} $ is the start of one. It trades the function "
                 "for a polynomial.")
        self.assertFalse(GroundedValueGuard.needs_correction(
            reply, evidence=self.SEARCHED, action_performed=False, grounded_subject=True))
        self.assertEqual(GroundedValueGuard.findings(reply, [("search", self.SEARCHED)]), [])

    def test_a_claim_beside_an_expression_is_still_checked(self):
        reply = "The course uses $ 1 + t $ as its first example and costs $450."
        found = GroundedValueGuard.findings(reply, [("search", self.SEARCHED)])
        self.assertEqual([f["value"] for f in found], ["450"])
        self.assertTrue(GroundedValueGuard.needs_correction(
            reply, evidence=self.SEARCHED, action_performed=False, grounded_subject=True))


if __name__ == "__main__":
    unittest.main()
