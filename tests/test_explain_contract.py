"""The Stage C explanation arms (brain/explain_contract.py).

Pinned: her own use is untouched -- unset, an explanation turn's prompt is
the production prompt and no plan is made; a misconfigured arm cannot
start; the contract takes out exactly the order and length rules it
replaces (held to their sources, so a change there fails here) and touches
nothing on other turns; arm C makes one 8B planning call and falls back to
the contract when the plan is unusable; arm D reads its plan from a file
and makes no call.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from brain import conversation_style
from brain import explain_contract as ec
from brain.personality_loader import PersonalityLoader
from brain.response_policy import ResponseLimits
from core import turn_trace
from tests.test_turn_trace import _traced
from tests.turn_harness import build_engine

GPS = {"intent": "knowledge_question", "confidence": 1.0,
       "normalized_request": "How does GPS know where I am?",
       "speech_act": "information_request", "answer_shape": "explain",
       "information_freshness": "stable"}
PLAN = {"understand": "how a phone finds its position", "scene": "your phone hears four satellites",
        "answer": "it times their signals and works out the distances",
        "terms": [{"term": "satellite", "plain": "a machine circling Earth"},
                  {"term": "signal", "plain": "a radio message"},
                  {"term": "extra", "plain": "dropped"}],
        "depth": "mechanism"}


def _clean_env(**settings):
    env = {k: v for k, v in os.environ.items() if k not in (ec.ARM_ENV, ec.PLANS_ENV)}
    env.update(settings)
    return mock.patch.dict(os.environ, env, clear=True)


def _turns(said, *, plan_reply=None, **settings):
    """Run the turns; return (answer prompts, planner calls, records)."""
    prompts, planner_calls, records = [], [], []
    with _clean_env(**settings):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({"GPS know": GPS})
        scripted = engine.client
        real = scripted.chat

        def scripted_chat(**kwargs):
            # Underneath the trace recorder, so its calls are recorded too.
            system = str((kwargs.get("messages") or [{}])[0].get("content", ""))
            if system.startswith("You plan how an answer will be explained"):
                planner_calls.append(kwargs)
                return {"message": {"content": plan_reply if plan_reply is not None
                                    else json.dumps(PLAN)}}
            if kwargs.get("stream"):
                prompts.append(kwargs["messages"])
            return real(**kwargs)

        scripted.chat = scripted_chat
        scripted.reply = "Your phone listens for satellites and times their signals."
        _traced(engine)
        try:
            for line in said:
                with contextlib.redirect_stdout(io.StringIO()):
                    engine.chat(line)
                records.append(turn_trace.last().as_record())
        finally:
            engine.close()
    return prompts, planner_calls, records


def _prompt_text(messages) -> tuple[str, str]:
    return str(messages[0]["content"]), str(messages[-1]["content"])


class ConfigurationTests(unittest.TestCase):

    def test_unset_is_production(self):
        with _clean_env():
            self.assertEqual(ec.configured(), ("", {}))

    def test_a_misconfigured_arm_cannot_start(self):
        for settings in ({ec.ARM_ENV: "everything"}, {ec.ARM_ENV: ec.PLAN_FILE}):
            with self.subTest(settings=settings), _clean_env(**settings):
                with self.assertRaises(ValueError):
                    ec.configured()

    def test_the_plan_file_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plans.json"
            path.write_text(json.dumps({"plans": {"q": "{}"}}), encoding="utf-8")
            with _clean_env(**{ec.ARM_ENV: ec.PLAN_FILE, ec.PLANS_ENV: str(path)}):
                self.assertEqual(ec.configured(), (ec.PLAN_FILE, {"q": "{}"}))


class WhatTheContractReplacesTests(unittest.TestCase):
    """The copied and removed text is held to its sources."""

    def test_the_kept_requirements_are_productions_own_words(self):
        for language in ("en", "ko"):
            production = ResponseLimits(max_words=130, max_sentences=6, goal="GOAL").instruction(
                language=language)
            for rule in ec._KEPT_REQUIREMENTS[language]:
                with self.subTest(language=language, rule=rule[:30]):
                    self.assertIn(rule, production)

    def test_result_before_background_and_the_goal_are_replaced(self):
        production = ResponseLimits(max_words=130, max_sentences=6, goal="GOAL").instruction()
        contract = ec.requirements("en", max_words=130)
        self.assertIn("Give the requested result before reactions", production)
        self.assertNotIn("Give the requested result before reactions", contract)
        self.assertNotIn("GOAL", contract)
        self.assertIn("At most 4 sentences and at most 130 words", contract)
        self.assertIn("4문장, 130단어 이내로", ec.requirements("ko", max_words=130))

    def test_the_superseded_persona_rules_are_the_order_and_length_rules(self):
        loader = PersonalityLoader()
        expected = {"en": ("Short by default", "Lead with the answer", "If the user asks for an explanation"),
                    "ko": ("한두 문장", "답부터 말합니다", "설명을 요청하시면")}
        for language, phrases in expected.items():
            rules = loader.sections(language)
            found = [rules[section][index] for section, index in ec.SUPERSEDED_PERSONA_RULES]
            for rule, phrase in zip(found, phrases):
                with self.subTest(language=language, phrase=phrase):
                    self.assertIn(phrase, rule)
            kept = ec.persona_without_conflicts(loader.load(language), rules)
            for rule in found:
                self.assertNotIn(rule, kept)
            self.assertEqual(len(loader.load(language).splitlines()) - 3, len(kept.splitlines()))

    def test_the_superseded_style_lines_are_in_the_answer_block(self):
        block = conversation_style.style_instruction(conversation_style.ANSWER, "en")
        for line in ec.SUPERSEDED_STYLE_LINES:
            self.assertIn(line, block.splitlines())
        trimmed = ec.style_without_conflicts(block)
        self.assertEqual(len(block.splitlines()) - 2, len(trimmed.splitlines()))
        self.assertIn("Never open by repeating what they just said.", trimmed)


class PlanTests(unittest.TestCase):

    def test_a_plan_is_read_and_kept_small(self):
        plan, why = ec.parse_plan(json.dumps(PLAN))
        self.assertEqual(why, "")
        self.assertEqual(len(plan.terms), 2)
        self.assertEqual(plan.depth, "mechanism")
        rendered = ec.render_plan(plan, "en")
        self.assertIn("Situation to open with: your phone hears four satellites", rendered)
        self.assertIn("satellite (a machine circling Earth)", rendered)

    def test_an_unusable_plan_says_why(self):
        long = " ".join(["word"] * 41)
        for text, why in (("not json", "not JSON"), ("[]", "not an object"),
                          (json.dumps({"answer": "a"}), "no scene"),
                          (json.dumps({"scene": "s", "answer": long}), "answer longer than 40 words"),
                          (json.dumps({"scene": "s", "answer": "a", "terms": "x"}), "terms is not a list")):
            with self.subTest(why=why):
                self.assertEqual(ec.parse_plan(text), (None, why))

    def test_the_planner_sees_the_questions_only(self):
        messages = ec.planner_messages(["How does a dishwasher work?", "Why special soap?"], "en")
        self.assertIn("- How does a dishwasher work?", messages[1]["content"])
        self.assertTrue(messages[1]["content"].endswith("Question:\nWhy special soap?"))
        self.assertIn("Write every value in Korean.", ec.planner_messages(["왜?"], "ko")[0]["content"])


class HerOwnUseTests(unittest.TestCase):

    def test_unset_leaves_the_explanation_prompt_as_production_builds_it(self):
        prompts, planner, records = _turns(["How does GPS know where I am?"])
        system, last = _prompt_text(prompts[0])
        self.assertIn("Lead with the answer. State the fact", system)
        self.assertIn("Give the requested result before reactions", last)
        self.assertIn("show one concrete case or worked example", last)
        self.assertIn("Lead with the answer in plain spoken words.", last)
        self.assertNotIn("HOW TO EXPLAIN THIS", last)
        self.assertEqual(planner, [])
        self.assertNotIn("explanation_contract", records[0]["context"])


class ArmTests(unittest.TestCase):

    def test_the_contract_replaces_the_conflicting_rules_on_an_explanation(self):
        prompts, planner, records = _turns(
            ["How does GPS know where I am?", "What's 12 times 3?"], **{ec.ARM_ENV: ec.CONTRACT})
        system, last = _prompt_text(prompts[0])
        for gone in ("Lead with the answer. State the fact", "Short by default",
                     "If the user asks for an explanation"):
            self.assertNotIn(gone, system)
        self.assertIn("[KNOWLEDGE AND CALCULATIONS]", system)
        self.assertNotIn("Give the requested result before reactions", last)
        self.assertNotIn("show one concrete case or worked example", last)
        self.assertNotIn("Lead with the answer in plain spoken words.", last)
        self.assertIn("HOW TO EXPLAIN THIS", last)
        self.assertIn("No customer-service lines", last)
        self.assertEqual(planner, [])
        self.assertEqual(records[0]["context"]["explanation_contract"], {"arm": ec.CONTRACT, "plan": None})
        # Not an explanation: built as production builds it.
        self.assertNotIn("explanation_contract", records[1]["context"])

    def test_the_plan_arm_makes_one_8b_call_and_follows_its_plan(self):
        prompts, planner, records = _turns(["How does GPS know where I am?"], **{ec.ARM_ENV: ec.PLAN})
        self.assertEqual(len(planner), 1)
        self.assertEqual(planner[0]["model"], "qwen3:8b")
        _system, last = _prompt_text(prompts[0])
        self.assertIn("Situation to open with: your phone hears four satellites", last)
        note = records[0]["context"]["explanation_contract"]
        self.assertEqual((note["arm"], note["source"], note["unusable"]), (ec.PLAN, "8B", ""))
        callers = [c.get("caller", "") for c in records[0].get("model_calls") or ()]
        self.assertTrue(any(c.endswith(":_explanation_plan") for c in callers), callers)

    def test_an_unusable_plan_falls_back_to_the_contract(self):
        prompts, planner, records = _turns(["How does GPS know where I am?"], plan_reply="no plan",
                                           **{ec.ARM_ENV: ec.PLAN})
        _system, last = _prompt_text(prompts[0])
        self.assertIn("HOW TO EXPLAIN THIS", last)
        self.assertNotIn("PLAN FOR THIS ANSWER", last)
        note = records[0]["context"]["explanation_contract"]
        self.assertEqual((note["plan"], note["unusable"]), (None, "not JSON"))

    def test_the_plan_file_arm_reads_its_plan_and_calls_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plans.json"
            path.write_text(json.dumps({"plans": {
                ec.plan_key(["How does GPS know where I am?"]): json.dumps(PLAN)}}), encoding="utf-8")
            prompts, planner, records = _turns(
                ["How does GPS know where I am?", "How does GPS know where I am?"],
                **{ec.ARM_ENV: ec.PLAN_FILE, ec.PLANS_ENV: str(path)})
        self.assertEqual(planner, [])
        self.assertIn("Situation to open with", _prompt_text(prompts[0])[1])
        # The second time the questions so far are two: no plan was written for that.
        note = records[1]["context"]["explanation_contract"]
        self.assertEqual((note["plan"], note["unusable"]), (None, "no plan for this question"))


if __name__ == "__main__":
    unittest.main()
