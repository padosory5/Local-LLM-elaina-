"""The explanation contract as a controlled production experiment.

Pinned: the flag's three modes and that nothing but arm B can run in her own
use (no plan, no extra model call); every explanation turn's arm is logged
and no other turn is touched; the evaluation arms still take precedence;
the contract is Stage C's arm B, word for word; the strict concrete_first
and leads_with_the_answer definitions are unchanged; the production
contract is the revision (a direct answer first, an example only when it
helps) while Stage C's text survives word for word as a comparison arm; and
the real-turn evaluation reads what the turns log.
"""

import hashlib
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from brain import explain_contract as ec
from evals import corpus
from evals import explanation_experiment as xp
from tests.test_explain_contract import _clean_env, _prompt_text, _turns
from tests.turn_harness import build_engine

GPS_QUESTION = "How does GPS know where I am?"


class _Config:
    def __init__(self, value):
        self.value = value

    def get(self, *keys, default=None, required=True):
        return default if self.value is ... else self.value


def _experiment(mode, said):
    return _turns(said, **{ec.EXPERIMENT_ENV: mode})


class FlagTests(unittest.TestCase):

    def test_the_modes(self):
        with _clean_env():
            for value, mode in (("off", "off"), ("on", "on"), ("split", "split"), ("SPLIT ", "split"),
                                (False, "off"), (True, "on"), (..., "off"), (None, "off")):
                with self.subTest(value=value):
                    self.assertEqual(ec.experiment_mode(_Config(value)), mode)

    def test_no_planner_can_be_switched_on_in_her_own_use(self):
        with _clean_env():
            for value in ("plan", "plan_file", "contract", "C"):
                with self.subTest(value=value), mock.patch("builtins.print"):
                    self.assertEqual(ec.experiment_mode(_Config(value)), ec.EXPERIMENT_OFF)

    def test_the_environment_overrides_the_file(self):
        with _clean_env(**{ec.EXPERIMENT_ENV: "on"}):
            self.assertEqual(ec.experiment_mode(_Config("off")), "on")

    def test_split_is_random_per_turn_and_even(self):
        arms = Counter(ec.production_variant("split", f"{n:012x}")[0] for n in range(2000))
        self.assertEqual(set(arms), {ec.CONTRACT, ec.CURRENT})
        self.assertLess(abs(arms[ec.CONTRACT] - 1000), 100)
        self.assertEqual(ec.production_variant("split", "abc"), ec.production_variant("split", "abc"))
        self.assertEqual(ec.production_variant("on", "abc"), (ec.CONTRACT, "flag on"))
        self.assertEqual(ec.production_variant("off", "abc"), (ec.CURRENT, "flag off"))

    def test_the_shipped_setting_is_off_until_the_revision_is_checked(self):
        from config.loader import Config
        with _clean_env():
            self.assertEqual(ec.experiment_mode(Config()), ec.EXPERIMENT_OFF)
        # The test harness pins it off, so no other test meets a random arm.
        self.assertEqual(build_engine()._explanation_experiment, ec.EXPERIMENT_OFF)


class FrozenTests(unittest.TestCase):
    """What the experiment must not move."""

    def test_the_production_contract_is_the_revision(self):
        # d398489d1bd7 is the version the explanation check passed
        # (docs/EXPLANATION_CHECK_RESULTS.md); cc22900f17a9 adds rule 5, a
        # verdict first when they ask whether they have it right
        # (simulated learners, 2026-10-02).
        self.assertEqual(ec.CONTRACT_VERSION, "cc22900f17a9")
        text = ec.requirements("en", max_words=130)
        self.assertIn("1. Answer their question directly in the first sentence", text)
        self.assertIn("Never open with an example or a story that delays the answer.", text)
        self.assertNotIn("Open with a particular, everyday situation", text)

    def test_stage_c_arm_b_survives_word_for_word_for_comparison(self):
        self.assertEqual(ec.STAGE_C_CONTRACT_VERSION, "35c01efc844a")
        self.assertIs(ec.text_for(ec.CONTRACT_STAGE_C), ec.STAGE_C_CONTRACT_TEXT)
        self.assertIs(ec.text_for(ec.PLAN), ec.STAGE_C_CONTRACT_TEXT)
        self.assertIs(ec.text_for(ec.CONTRACT), ec.CONTRACT_TEXT)

    def test_the_strict_definitions_are_unchanged(self):
        self.assertEqual(corpus.rubric_fingerprint(), "c4fac3a80008")
        definitions = corpus.definitions()
        found = {name: hashlib.sha256(definitions[name].encode("utf-8")).hexdigest()[:12]
                 for name in ("concrete_first", "leads_with_the_answer")}
        self.assertEqual(found, {"concrete_first": "91de5eed10d4",
                                 "leads_with_the_answer": "54beb20889be"})


class HerOwnUseTests(unittest.TestCase):

    def test_off_answers_as_before_and_still_logs_the_arm(self):
        prompts, planner, records = _experiment("off", [GPS_QUESTION])
        _system, last = _prompt_text(prompts[0])
        self.assertIn("Give the requested result before reactions", last)
        self.assertNotIn("HOW TO EXPLAIN THIS", last)
        logged = records[0]["context"]["explanation_experiment"]
        self.assertEqual((logged["mode"], logged["variant"]), ("off", ec.CURRENT))
        self.assertNotIn("explanation_contract", records[0]["context"])

    def test_on_applies_the_contract_with_no_extra_call(self):
        prompts, planner, records = _experiment("on", [GPS_QUESTION, "What's 12 times 3?"])
        system, last = _prompt_text(prompts[0])
        self.assertIn("HOW TO EXPLAIN THIS", last)
        self.assertNotIn("PLAN FOR THIS ANSWER", last)
        self.assertNotIn("Lead with the answer. State the fact", system)
        self.assertEqual(planner, [])
        callers = [c.get("caller", "").rsplit(":", 1)[-1] for c in records[0].get("model_calls") or ()]
        self.assertEqual(callers, ["route", "collect_answer"])
        logged = records[0]["context"]["explanation_experiment"]
        self.assertEqual((logged["mode"], logged["variant"], logged["contract_version"]),
                         ("on", ec.CONTRACT, ec.CONTRACT_VERSION))
        self.assertEqual(records[0]["context"]["explanation_contract"], {"arm": ec.CONTRACT, "plan": None})
        # Not an explanation: untouched, and nothing logged.
        self.assertNotIn("explanation_experiment", records[1]["context"])
        self.assertNotIn("explanation_contract", records[1]["context"])

    def test_split_gives_each_turn_one_arm_and_says_which(self):
        prompts, planner, records = _experiment("split", [GPS_QUESTION] * 12)
        arms = []
        for messages, record in zip(prompts, records):
            logged = record["context"]["explanation_experiment"]
            arms.append(logged["variant"])
            self.assertEqual(logged["variant"], ec.production_variant("split", record["turn_id"])[0])
            contract = "HOW TO EXPLAIN THIS" in _prompt_text(messages)[1]
            self.assertEqual(contract, logged["variant"] == ec.CONTRACT)
        self.assertEqual(set(arms), {ec.CONTRACT, ec.CURRENT})
        self.assertEqual(planner, [])

    def test_the_stage_c_comparison_arm_runs_the_stage_c_text(self):
        prompts, _planner, records = _turns([GPS_QUESTION], **{
            ec.EXPERIMENT_ENV: "off", ec.ARM_ENV: ec.CONTRACT_STAGE_C})
        last = _prompt_text(prompts[0])[1]
        self.assertIn("Open with a particular, everyday situation", last)
        self.assertEqual(records[0]["context"]["explanation_contract"]["arm"], ec.CONTRACT_STAGE_C)

    def test_an_evaluation_arm_takes_precedence(self):
        _prompts, _planner, records = _turns([GPS_QUESTION], **{
            ec.EXPERIMENT_ENV: "off", ec.ARM_ENV: ec.CONTRACT})
        self.assertNotIn("explanation_experiment", records[0]["context"])
        self.assertEqual(records[0]["context"]["explanation_contract"]["arm"], ec.CONTRACT)


class RealTurnEvaluationTests(unittest.TestCase):

    def _traces(self, folder: Path) -> Path:
        _prompts, _planner, records = _experiment("split", [GPS_QUESTION] * 8 + ["What's 12 times 3?"])
        root = folder / "turn_trace"
        root.mkdir()
        (root / "2026-10-02.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records),
                                               encoding="utf-8")
        return root

    def test_the_summary_and_the_blind_sample_read_what_the_turns_log(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self._traces(Path(folder))
            turns = xp.experiment_turns(xp.read_records(root))
            self.assertEqual(len(turns), 8)
            self.assertTrue(all((t["arm"] == ec.CONTRACT) == t["applied"] for t in turns))
            text = xp.summary(turns)
            self.assertIn("8 explanation turns", text)
            self.assertNotIn("disagree", text)
            out = Path(folder) / "out"
            arms = Counter(t["arm"] for t in turns)
            count = xp.export_blind(turns, per_arm=2, out=out)
            self.assertEqual(count, sum(min(2, n) for n in arms.values()))
            labelled = json.loads((out / "blind.json").read_text(encoding="utf-8"))
            for item in labelled["items"]:
                self.assertNotIn("arm", item)
                self.assertEqual(set(item["labels"]), {"concrete_first", "leads_with_the_answer"})
                item["labels"] = {"concrete_first": "no", "leads_with_the_answer": "yes"}
            (out / "blind.json").write_text(json.dumps(labelled), encoding="utf-8")
            contract = min(2, arms[ec.CONTRACT])
            self.assertIn(f"| contract | {contract} | 0 | {contract} | 0 |", xp.label_rates(out))


if __name__ == "__main__":
    unittest.main()
