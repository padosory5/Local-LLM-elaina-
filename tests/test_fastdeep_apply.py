"""FAST/DEEP routing acted on, for an evaluation arm only (brain/fastdeep_apply.py).

Pinned: her own use is record-only -- unset, every turn is answered by the
model that always answers it; a misconfigured arm cannot start; and in the
two corpus v2 arms the switched turn is written by the DEEP model, with the
conversation as it stands and the soft stages off, and the turn after it is
back on FAST.
"""

import contextlib
import io
import os
import unittest
from unittest import mock

from brain import fastdeep_apply as apply
from brain import fastdeep_router as router
from brain import response_stages
from core import turn_trace
from tests.test_turn_trace import _traced
from tests.turn_harness import build_engine

DEEP_MODEL = "deep-model-under-test"

EXPLAIN = {"intent": "knowledge_question", "confidence": 1.0,
           "normalized_request": "How do noise-cancelling headphones work?",
           "speech_act": "information_request", "answer_shape": "explain",
           "information_freshness": "stable"}
AGAIN = dict(EXPLAIN, normalized_request="Explain noise-cancelling headphones another way.",
             is_follow_up=True)
ROUTES = {"headphones work": EXPLAIN, "another way": AGAIN}
CONVERSATION = ("How do noise-cancelling headphones work?",
                "Can you explain that another way?",
                "What's 12 times 3?")


def _clean_env(**settings):
    env = {k: v for k, v in os.environ.items()
           if k not in (apply.ROUTE_ENV, apply.MODEL_ENV, "ELAINA_SOFT_STAGES")}
    env.update(settings)
    return mock.patch.dict(os.environ, env, clear=True)


def _answer_models(record: dict) -> set:
    return {c.get("model") for c in record.get("model_calls") or ()
            if str(c.get("caller", "")).endswith(":collect_answer")}


def _converse(**settings):
    """The three turns under an arm's settings: the records, the engine's
    own words model, and whether the soft stages were on at each check."""
    soft_seen: dict[int, list[bool]] = {}
    real = response_stages.soft_stages_on
    current = [0]

    def spy():
        value = real()
        soft_seen.setdefault(current[0], []).append(value)
        return value

    with _clean_env(**settings):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine(ROUTES)
        _traced(engine)
        engine.client.reply = ("A microphone hears the noise and the speaker plays its "
                               "mirror image, so the two cancel.")
        records = []
        try:
            with mock.patch.object(response_stages, "soft_stages_on", spy):
                for number, said in enumerate(CONVERSATION):
                    current[0] = number
                    with contextlib.redirect_stdout(io.StringIO()):
                        engine.chat(said)
                    records.append(turn_trace.last().as_record())
        finally:
            engine.close()
    return records, engine.conversation_model, soft_seen


class ConfigurationTests(unittest.TestCase):

    def test_unset_is_record_only(self):
        with _clean_env():
            self.assertEqual(apply.configured(), ("", ""))

    def test_an_arm_names_its_policy_and_model(self):
        for policy in (apply.ROUTER, apply.AFTER_FIRST):
            with self.subTest(policy=policy), _clean_env(**{
                    apply.ROUTE_ENV: policy, apply.MODEL_ENV: DEEP_MODEL}):
                self.assertEqual(apply.configured(), (policy, DEEP_MODEL))

    def test_a_misconfigured_arm_cannot_start(self):
        for settings in ({apply.ROUTE_ENV: "always"},
                         {apply.ROUTE_ENV: apply.ROUTER}):
            with self.subTest(settings=settings), _clean_env(**settings):
                with self.assertRaises(ValueError):
                    apply.configured()
                with self.assertRaises(ValueError), \
                        contextlib.redirect_stdout(io.StringIO()):
                    build_engine(ROUTES)

    def test_which_turns_switch(self):
        cases = (
            ("", "DEEP", 2, ""),
            (apply.ROUTER, "DEEP", 1, DEEP_MODEL),
            (apply.ROUTER, "FAST", 2, ""),
            (apply.AFTER_FIRST, "FAST", 1, ""),
            (apply.AFTER_FIRST, "FAST", 2, DEEP_MODEL),
            (apply.AFTER_FIRST, "DEEP", 3, DEEP_MODEL),
        )
        for policy, decision, number, expected in cases:
            with self.subTest(policy=policy, decision=decision, number=number):
                self.assertEqual(apply.words_model(decision, turn_number=number,
                                                   policy=policy, model=DEEP_MODEL), expected)


class HerOwnUseTests(unittest.TestCase):

    def test_record_only_answers_every_turn_fast(self):
        records, words, soft = _converse()
        self.assertEqual(records[1]["context"]["shadow_route"]["decision"], router.DEEP)
        for record in records:
            self.assertEqual(_answer_models(record), {words})
            self.assertNotIn("fastdeep_applied", record["context"])
        self.assertTrue(all(all(values) for values in soft.values()))


class EvaluationArmTests(unittest.TestCase):

    def test_routed_switches_the_deep_turn_and_comes_back(self):
        records, words, soft = _converse(**{apply.ROUTE_ENV: apply.ROUTER,
                                            apply.MODEL_ENV: DEEP_MODEL})
        decisions = [r["context"]["shadow_route"]["decision"] for r in records]
        self.assertEqual(decisions, [router.FAST, router.DEEP, router.FAST])
        self.assertEqual(_answer_models(records[0]), {words})
        # The DEEP turn: written by the DEEP model, after FAST's first answer,
        # under DEEP's setting.
        self.assertEqual(_answer_models(records[1]), {DEEP_MODEL})
        self.assertEqual(records[1]["context"]["model"], DEEP_MODEL)
        self.assertEqual(records[1]["context"]["fastdeep_applied"],
                         {"policy": apply.ROUTER, "model": DEEP_MODEL, "decision": router.DEEP})
        self.assertIn(False, soft.get(1, []))
        # Back on FAST, with the soft stages on again.
        self.assertNotIn(DEEP_MODEL, _answer_models(records[2]))
        self.assertNotIn("fastdeep_applied", records[2]["context"])
        self.assertTrue(all(soft.get(2, [True])))
        self.assertTrue(response_stages.soft_stages_on())

    def test_late_switches_every_turn_after_the_first(self):
        records, words, _soft = _converse(**{apply.ROUTE_ENV: apply.AFTER_FIRST,
                                             apply.MODEL_ENV: DEEP_MODEL})
        self.assertEqual(_answer_models(records[0]), {words})
        self.assertEqual(_answer_models(records[1]), {DEEP_MODEL})
        # The router keeps the arithmetic turn FAST; LATE switches it anyway.
        self.assertEqual(records[2]["context"]["shadow_route"]["decision"], router.FAST)
        self.assertEqual(records[2]["context"]["fastdeep_applied"]["decision"], router.FAST)
        self.assertEqual(_answer_models(records[2]), {DEEP_MODEL})


if __name__ == "__main__":
    unittest.main()
