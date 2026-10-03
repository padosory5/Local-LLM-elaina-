"""Stage C's evaluation (evals/stagec.py), before anything is frozen.

The evaluator and its decision rules are frozen with the corpus, so they are
checked here first: the held-out corpus and its separation from every
earlier set, the freeze record, the decision on each outcome, the blind
sample, and the whole analysis over synthetic runs of the four arms whose
answer is known in advance.
"""

import contextlib
import json
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from brain import explain_contract as ec
from evals import corpus
from evals import fastdeep_analysis as fd
from evals import fastdeep_v2 as v2
from evals import stagec


class CorpusTests(unittest.TestCase):

    def test_the_shape_of_the_held_out_corpus(self):
        raw = stagec.raw_corpus()
        self.assertEqual(len(raw["scenarios"]), 24)
        self.assertEqual(len(stagec.heldout_keys(raw)), 30)
        self.assertEqual(Counter(s["language"] for s in raw["scenarios"]), {"en": 12, "ko": 12})
        self.assertEqual(sum(1 for s in raw["scenarios"] if len(s["turns"]) == 2), 6)
        concrete_modes = set(corpus.rubric()["concrete_first"]["applies_to"])
        modes = {t["mode"] for s in raw["scenarios"] for t in s["turns"]}
        self.assertLessEqual(modes, concrete_modes)

    def test_it_is_sound_beside_v1_and_v2(self):
        self.assertEqual(stagec.corpus_problems(), [])

    def test_nothing_in_it_comes_from_an_earlier_set(self):
        earlier = corpus.load(corpus.ALL_SUITES) + corpus._load_suite(v2.corpus_file())
        ids = {s.id for s in earlier}
        said = {t.say.strip().lower() for s in earlier for t in s.turns}
        texts = " ".join(p.read_text(encoding="utf-8").lower() for p in
                         [*corpus.SCENARIOS.glob("*.json"), corpus.CONTAMINATION]
                         if p.name != f"{stagec.SUITE}.json")
        subjects = ("bread", "microwave", "touchscreen", "onion", "soda", "two-factor", "half-life",
                    "leap year", "guitar", "dishwasher", "fever", "credit card", "구름", "인덕션",
                    "근육", "qr", "공매도", "기댓값", "보험", "정전기", "발전소", "엘리베이터", "터널",
                    "자전거")
        for scenario in stagec.raw_corpus()["scenarios"]:
            self.assertNotIn(scenario["id"], ids)
            for turn in scenario["turns"]:
                self.assertNotIn(turn["say"].strip().lower(), said)
        self.assertEqual([w for w in subjects if w in texts], [])

    def test_the_reference_turns_are_the_21_diagnosis_turns(self):
        v2_turns = {(s.id, i) for s in corpus._load_suite(v2.corpus_file()) for i, _ in s.scored}
        self.assertEqual(len(stagec.REFERENCE), 21)
        self.assertLessEqual(set(stagec.REFERENCE), v2_turns)
        self.assertEqual(len(stagec.REFERENCE_SCENARIOS), 19)

    def test_held_out_is_visible_only_inside(self):
        before = corpus.ALL_SUITES
        with stagec.held_out():
            self.assertIn(stagec.SUITE, corpus.ALL_SUITES)
            self.assertIn(stagec.REFERENCE_SUITE, corpus.ALL_SUITES)
        self.assertEqual(corpus.ALL_SUITES, before)

    def test_placing_it_leaves_v1s_fingerprint_alone(self):
        before = fd.fingerprint()
        with _placed():
            self.assertEqual(fd.fingerprint(), before)

    def test_d_has_a_plan_for_every_chain_of_questions(self):
        chains = stagec.plan_chains()
        self.assertEqual(len(chains), 30 + 38)
        self.assertEqual(chains[ec.plan_key(["구름은 왜 하늘에 떠 있어?"])], "ko")


class FreezeRecordTests(unittest.TestCase):

    def test_the_record_holds_the_exact_texts_and_the_criteria(self):
        found = stagec.record()
        for section in ("protocol", "corpus", "reference", "analysis", "rubric", "evaluator", "arms",
                        "arm_texts", "arm_files", "system_under_test", "models", "runtime", "criteria"):
            self.assertIn(section, found)
        texts = found["arm_texts"]
        self.assertIn("HOW TO EXPLAIN THIS", texts["B"]["requirements"]["en"])
        self.assertIn("설명하는 방법", texts["B"]["requirements"]["ko"])
        self.assertEqual(len(texts["B"]["persona_rules_removed"]["en"]), 3)
        self.assertEqual(texts["C"]["planner_prompt"], ec.PLANNER_PROMPT)
        self.assertEqual(found["criteria"]["net_gain_min"], 0.20)
        self.assertEqual(found["arms"]["A"]["env"].get(ec.ARM_ENV), None)
        self.assertEqual(found["arms"]["D"]["env"][ec.ARM_ENV], ec.PLAN_FILE)

    def test_nothing_runs_unfrozen(self):
        with mock.patch.object(stagec, "FROZEN", Path(tempfile.gettempdir()) / "no-such-stagec.json"):
            self.assertTrue(stagec.verify())


def _decision(**changes):
    a = {"blind_rate": 0.8, "net": {("A", "B"): (8, 0, 8 / 30), ("B", "C"): (2, 1, 1 / 30),
                                    ("B", "D"): (3, 0, 0.1), ("C", "D"): (0, 0, 0.0)},
         "regressions": {arm: ([], []) for arm in ("B", "C", "D")},
         "plan_latency": 1.1, "unusable": {"C": (1, 60), "D": (0, 60)}}
    for key, value in changes.items():
        if key == "net":
            a["net"] = {**a["net"], **value}
        elif key == "regressions":
            a["regressions"] = {**a["regressions"], **value}
        else:
            a[key] = value
    return stagec.decide(a)


class DecisionTests(unittest.TestCase):

    def test_b_alone_is_enough_and_c_is_not_added_because_it_exists(self):
        found = _decision()
        self.assertTrue(found["b_improves"])
        self.assertFalse(found["c_adds"])
        self.assertTrue(found["outcome"].startswith("B:"))

    def test_c_adds_value(self):
        found = _decision(net={("B", "C"): (7, 0, 7 / 30)})
        self.assertTrue(found["outcome"].startswith("C:"))

    def test_c_fails_on_cost_or_reliability(self):
        self.assertFalse(_decision(net={("B", "C"): (7, 0, 7 / 30)}, plan_latency=1.6)["c_adds"])
        self.assertFalse(_decision(net={("B", "C"): (7, 0, 7 / 30)}, unusable={"C": (4, 60), "D": (0, 60)})["c_adds"])

    def test_regressions_reject_an_arm(self):
        self.assertFalse(_decision(regressions={"B": ([("x", 0), ("y", 0)], [])})["b_improves"])
        self.assertFalse(_decision(regressions={"B": ([], [("x", 0), ("y", 0), ("z", 0)])})["b_improves"])
        self.assertTrue(_decision(regressions={"B": ([("x", 0)], [("x", 0), ("y", 0)])})["b_improves"])

    def test_an_unconfirmed_judge_rejects_the_gain(self):
        for rate in (0.69, None):
            found = _decision(blind_rate=rate, net={("B", "C"): (7, 0, 7 / 30)})
            self.assertEqual((found["b_improves"], found["c_adds"]), (False, False))
            self.assertTrue(found["outcome"].startswith("neither"))

    def test_d_passing_where_c_does_not(self):
        found = _decision(net={("B", "D"): (7, 0, 7 / 30)})
        self.assertIn("the 8B cannot write it", found["outcome"])


# ------------------------------------------------------------ synthetic runs


@contextlib.contextmanager
def _placed():
    scratch = Path(tempfile.mkdtemp())
    try:
        for path in corpus.SCENARIOS.glob("*.json"):
            shutil.copy(path, scratch / path.name)
        shutil.copy(stagec.corpus_file(), scratch / f"{stagec.SUITE}.json")
        with mock.patch.object(corpus, "SCENARIOS", scratch):
            yield scratch
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _outcome(key, defects=()):
    properties = {"answers_the_ask": "yes", "leads_with_the_answer": "yes",
                  "concrete_first": "yes", "terms_explained": "yes"}
    for name in defects:
        properties[name] = "no"
    return fd.TurnOutcome(scenario=key[0], index=key[1], suite=stagec.SUITE, first=key[1] == 0,
                          complete=True, judge_error=False, checks_failed=frozenset(),
                          properties=properties, correctness=(0, 0), seconds=5.0, route={},
                          route_seconds=0.5)


def _write(root: Path, name: str, number: int, defects, note, seconds):
    path = root / f"{name}-{number}"
    (path / "traces").mkdir(parents=True)
    raw = stagec.raw_corpus()
    reference = {s["id"]: s for s in json.loads(v2.corpus_file().read_text(encoding="utf-8"))["scenarios"]}
    scenarios = [(s, stagec.SUITE) for s in raw["scenarios"]] + [
        (reference[i], v2.SUITE) for i in stagec.REFERENCE_SCENARIOS]
    results, turns = [], {}
    for scenario, suite in scenarios:
        stored, records = [], []
        for index, turn in enumerate(scenario["turns"]):
            key = (scenario["id"], index)
            turn_id = f"{name}-{number}-{key[0]}-{index}"
            stored.append({"index": index, "said": turn["say"], "display": f"reply {name}",
                           "seconds": seconds, "scored": True, "trace": {"turn_id": turn_id}})
            records.append({"turn_id": turn_id, "context": {"explanation_contract": note}})
            turns[key] = _outcome(key, defects)
        results.append({"scenario": scenario["id"], "suite": suite, "turns": stored})
        (path / "traces" / f"{scenario['id']}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return {"path": path, "run": {"results": results, "judged": {}}, "turns": turns}


class AnalysisTests(unittest.TestCase):
    """A fails concrete_first and terms_explained everywhere; B, C and D fix
    both. B's gain over A is therefore stable, and C adds nothing over B."""

    def _analyse(self):
        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, scratch, True)
        plan = {"arm": ec.PLAN, "plan": {"scene": "s"}, "source": "8B", "unusable": ""}
        spec = {"A": (("concrete_first", "terms_explained"), None, 5.0),
                "B": ((), {"arm": ec.CONTRACT, "plan": None}, 5.0),
                "C": ((), plan, 6.0),
                "D": ((), {**plan, "arm": ec.PLAN_FILE, "source": "file"}, 5.0)}
        runs = {arm: [_write(scratch, stagec.ARMS[arm].name, n, *spec[arm]) for n in (1, 2, 3)]
                for arm in stagec.ORDER}
        with _placed(), mock.patch.object(stagec, "_arm_runs", lambda name: runs[name]), \
                mock.patch.object(stagec, "read_blind", lambda: (0.85, {"credited": 20})), \
                mock.patch.object(stagec, "verify", lambda: []):
            found = stagec.analyse()
            text = stagec.report()
        return found, text

    def test_known_runs_give_the_known_decision(self):
        found, text = self._analyse()
        self.assertEqual(found["net"][("A", "B")], (30, 0, 1.0))
        self.assertEqual(found["net"][("B", "C")][2], 0.0)
        self.assertEqual(found["unusable"]["C"], (0, 90))
        self.assertAlmostEqual(found["plan_latency"], 1.0)
        self.assertEqual(found["regressions"]["B"], ([], []))
        self.assertTrue(stagec.decide(found)["outcome"].startswith("B:"))
        self.assertIn("**B: one contract produces the improvement", text)
        self.assertIn("| concrete-first | 30/30 | 0/30 |", text)


class BlindSampleTests(unittest.TestCase):

    def test_the_labelled_file_hides_the_arm_and_the_key_holds_it(self):
        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, scratch, True)
        runs = {arm: [_write(scratch, stagec.ARMS[arm].name, n,
                             ("concrete_first",) if arm == "A" else (), None, 5.0) for n in (1, 2, 3)]
                for arm in stagec.ORDER}
        with _placed(), mock.patch.object(stagec, "_arm_runs", lambda name: runs[name]), \
                mock.patch.object(stagec, "ROOT", scratch), \
                mock.patch.object(stagec, "BLIND", scratch / "blind.json"), \
                mock.patch.object(stagec, "BLIND_KEY", scratch / "blind_key.json"):
            count = stagec.export_blind()
            labelled = json.loads((scratch / "blind.json").read_text(encoding="utf-8"))
            key = json.loads((scratch / "blind_key.json").read_text(encoding="utf-8"))
            self.assertEqual(count, 28)
            self.assertEqual(Counter(k["judge"] for k in key["items"]), {"yes": 20, "no": 8})
            self.assertEqual(Counter(k["arm"] for k in key["items"] if k["judge"] == "yes"),
                             {"B": 7, "C": 7, "D": 6})
            for item in labelled["items"]:
                self.assertEqual(set(item) & {"arm", "run", "judge", "scenario"}, set())
            self.assertEqual(stagec.read_blind()[0], None)
            for item in labelled["items"]:
                item["labels"]["concrete_first"] = "yes"
            (scratch / "blind.json").write_text(json.dumps(labelled), encoding="utf-8")
            rate, counts = stagec.read_blind()
            self.assertEqual(rate, 1.0)
            self.assertEqual(counts["distractors_agreed"], 0)


if __name__ == "__main__":
    unittest.main()
