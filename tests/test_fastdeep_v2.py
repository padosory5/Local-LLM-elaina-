"""Corpus v2's evaluation (evals/fastdeep_v2.py), before anything is frozen.

The evaluator is frozen with the corpus, so it is checked here first: the
roles the corpus gives each turn, that the v2 suite never reaches v1's
frozen fingerprint, the freeze record, and the whole analysis -- labels,
routing measures, outcomes, latency and the verdict -- over synthetic runs
of all five arms whose answer is known in advance.
"""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from brain import fastdeep_router as router
from evals import corpus
from evals import fastdeep_analysis as fd
from evals import fastdeep_v2 as v2
from evals.phase2 import M8, M27


class CorpusTests(unittest.TestCase):

    def test_every_scored_turn_has_a_role(self):
        role = v2.roles()
        self.assertNotIn("", set(role.values()))
        self.assertEqual(Counter(role.values()),
                         {v2.FIRST: 30, v2.CONFUSION: 14, v2.DEEPENING: 6, v2.STAY_FAST: 10})
        self.assertEqual(Counter(v2.groups().values()), {"A": 10, "B": 6, "C": 6, "D": 4, "E": 4})

    def test_the_corpus_is_sound_beside_v1(self):
        self.assertEqual(v2.corpus_problems(), [])

    def test_no_subject_or_utterance_is_v1s(self):
        v1 = corpus.load(corpus.ALL_SUITES)
        said = {t.say.strip().lower() for s in v1 for t in s.turns}
        ids = {s.id for s in v1}
        for scenario in v2.raw_corpus()["scenarios"]:
            self.assertNotIn(scenario["id"], ids)
            for turn in scenario["turns"]:
                self.assertNotIn(turn["say"].strip().lower(), said)

    def test_held_out_is_visible_only_inside(self):
        before = corpus.ALL_SUITES
        with v2.held_out():
            self.assertIn(v2.SUITE, corpus.ALL_SUITES)
        self.assertEqual(corpus.ALL_SUITES, before)
        self.assertNotIn(v2.SUITE, corpus.ALL_SUITES)

    def test_placing_v2_leaves_v1s_fingerprint_alone(self):
        before = fd.fingerprint()
        with _placed_corpus():
            self.assertEqual(fd.fingerprint(), before)
            with v2.held_out():
                self.assertNotEqual(fd.fingerprint(), before)
                self.assertTrue(any(s.suite == v2.SUITE
                                    for s in corpus.load(corpus.ALL_SUITES)))


class FreezeRecordTests(unittest.TestCase):

    def test_the_record_names_everything_the_protocol_fixes(self):
        found = v2.record()
        for section in ("protocol", "corpus", "analysis", "rubric", "evaluator", "router", "switch",
                        "system_under_test", "models", "runtime", "arms", "criteria"):
            self.assertIn(section, found)
        self.assertEqual(found["router"]["file"], "brain/fastdeep_router.py")
        self.assertEqual(set(found["arms"]), set(v2.ORDER))
        self.assertEqual(found["criteria"]["precision_min"], 0.70)
        self.assertEqual(found["criteria"]["stay_fast_sent_max"], 0)

    def test_a_change_anywhere_is_a_difference(self):
        frozen = {"a": {"b": 1, "frozen_at": "x"}, "c": [1, 2]}
        self.assertEqual(v2._differences(frozen, {"a": {"b": 1, "frozen_at": "y"}, "c": [1, 2]}), [])
        self.assertEqual(len(v2._differences(frozen, {"a": {"b": 2}, "c": [1, 2]})), 1)

    def test_nothing_runs_unfrozen(self):
        with mock.patch.object(v2, "FROZEN", Path(tempfile.gettempdir()) / "no-such-freeze.json"):
            self.assertTrue(v2.verify())


# ------------------------------------------------------------ synthetic runs

SUBSTANCE_DEFECT = "answers_the_ask"


@contextlib.contextmanager
def _placed_corpus():
    """v1's scenario files and v2's, in a scratch directory the loader reads."""
    scratch = Path(tempfile.mkdtemp())
    try:
        for path in corpus.SCENARIOS.glob("*.json"):
            shutil.copy(path, scratch / path.name)
        shutil.copy(v2.corpus_file(), scratch / f"{v2.SUITE}.json")
        with mock.patch.object(corpus, "SCENARIOS", scratch):
            yield scratch
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _outcome(key, *, defect=False, seconds=4.0):
    return fd.TurnOutcome(
        scenario=key[0], index=key[1], suite=v2.SUITE, first=key[1] == 0, complete=True,
        judge_error=False, checks_failed=frozenset(),
        properties={SUBSTANCE_DEFECT: "no" if defect else "yes"},
        correctness=(0, 0), seconds=seconds, route={}, route_seconds=0.5)


def _write_run(root: Path, name: str, number: int, plan) -> dict:
    """One synthetic run: plan(key) -> (defect, seconds, decision, switched, loads)."""
    path = root / f"{name}-{number}"
    (path / "traces").mkdir(parents=True)
    results, turns = [], {}
    for scenario in v2.raw_corpus()["scenarios"]:
        stored, records = [], []
        for index, _turn in enumerate(scenario["turns"]):
            key = (scenario["id"], index)
            defect, seconds, decision, switched, loads = plan(key)
            turn_id = f"{name}-{number}-{key[0]}-{index}"
            stored.append({"index": index, "said": _turn["say"], "display": "reply",
                           "seconds": seconds, "scored": True, "trace": {"turn_id": turn_id}})
            context = {"shadow_route": {"decision": decision, "reason": "synthetic",
                                        "confidence": "", "continuation": []}}
            if switched:
                context["fastdeep_applied"] = {"policy": "router", "model": M27,
                                               "decision": decision}
            records.append({"turn_id": turn_id, "context": context,
                            "model_calls": [{"model": model, "load_duration": int(s * 1e9)}
                                            for model, s in loads]})
            turns[key] = _outcome(key, defect=defect, seconds=seconds)
        results.append({"scenario": scenario["id"], "suite": v2.SUITE, "turns": stored})
        (path / "traces" / f"{scenario['id']}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return {"path": path, "run": {"results": results, "judged": {}}, "turns": turns}


def _arms(root: Path, *, extra_send=None):
    """Five arms whose verdict is known: FAST fails every confusion follow-up
    on substance, the 27B fixes it, and ROUTED sends exactly the follow-ups
    in scope (plus ``extra_send`` in run 1)."""
    role = v2.roles()

    def fast(key):
        return role[key] == v2.CONFUSION, 4.0, router.FAST, False, ()

    def late(key):
        return False, (14.0 if key[1] else 4.0), router.FAST, key[1] > 0, ()

    def routed_plan(number):
        last_sent = [False]       # turns are planned in the order they run

        def plan(key):
            send = role[key] in v2.IN_SCOPE or (number == 1 and key == extra_send)
            reload = () if send or not last_sent[0] else ((M8, 4.2),)
            last_sent[0] = send
            if send:
                return False, 14.0, router.DEEP, True, ((M27, 8.0),)
            return role[key] == v2.CONFUSION, 8.0 if reload else 4.0, router.FAST, False, reload
        return plan

    runs = {}
    for number in range(1, v2.RUNS + 1):
        runs.setdefault("fast", []).append(_write_run(root, "pf2-fast", number, fast))
        runs.setdefault("routed", []).append(_write_run(root, "pf2-routed", number, routed_plan(number)))
        runs.setdefault("late", []).append(_write_run(root, "pf2-late", number, late))
        runs.setdefault("split", []).append(_write_run(root, "pf2-split", number, late))
        runs.setdefault("deep", []).append(_write_run(root, "pf2-deep", number, late))
    return runs


class AnalysisTests(unittest.TestCase):

    def _analyse(self, **arms):
        scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, scratch, True)
        runs = _arms(scratch, **arms)
        with _placed_corpus(), \
                mock.patch.object(v2, "_arm_runs", lambda name: runs[name]), \
                mock.patch.object(v2, "verify", lambda: []), \
                mock.patch.object(v2, "BLIND", scratch / "none.json"):
            found = v2.analyse()
            text = v2.report()
        return found, text

    def test_known_runs_give_the_known_verdict(self):
        found, text = self._analyse()
        r = found["routing"]
        # 14 confusion follow-ups are stable DEEP+; ROUTED sends them and the
        # 6 deepening follow-ups in every run.
        self.assertEqual(len(r["deep_plus_in_scope"]), 14)
        self.assertEqual(r["precision"], (14 * 3, 20 * 3))
        self.assertEqual(r["recall"], (14 * 3, 14 * 3))
        self.assertEqual(r["stay_fast_sent"], [])
        self.assertEqual(r["deep_worse_sent"], [])
        # Every third turn is unlabelled: its two arms had different histories.
        self.assertFalse([k for k in found["labels"] if k[1] >= 2])
        # Latency: 20 DEEP turns per run, each followed by a reload.
        self.assertEqual(len(found["latency"]["deep"]), 60)
        self.assertEqual(set(found["latency"]["reload"]), {4.2})
        rows = {name: met for name, _m, _t, met in v2.verdict(found)}
        self.assertTrue(all(rows.values()), rows)
        self.assertIn("**ACCEPTED**", text)
        self.assertIn("correct escalation | 14", text)
        self.assertIn("unneeded escalation (cost only) | 6", text)

    def test_one_should_stay_fast_escalation_fails_it(self):
        found, text = self._analyse(extra_send=("v2c_heat_pump_thanks", 1))
        self.assertEqual(found["routing"]["stay_fast_sent"], [(("v2c_heat_pump_thanks", 1), 0)])
        self.assertIn("**NOT ACCEPTED**", text)

    def test_outcomes(self):
        label = lambda name, kinds=("same",): fd.TurnLabel(  # noqa: E731
            key=("x", 1), label=name,
            comparisons=[fd.Comparison(k) for k in (kinds if len(kinds) == 3 else kinds * 3)])
        cases = (
            (label("stable DEEP+"), v2.CONFUSION, True, 0, 3, "correct escalation"),
            (label("stable DEEP+"), v2.CONFUSION, False, 0, 3, "routing miss: missed escalation"),
            (label("stable DEEP+"), v2.FIRST, False, 0, 3, "FAST model failure (outside the router's scope)"),
            (label("stable DEEP-worse"), v2.DEEPENING, True, 3, 0, "DEEP model failure: made it worse"),
            (label(fd.UNSTABLE), v2.CONFUSION, True, 0, 0, "neither model good enough"),
            (label(fd.UNSTABLE, ("deep+", "deep-worse", "same")), v2.CONFUSION, False, 2, 1,
             "unstable / non-repeatable"),
            (label("pending DEEP+"), v2.CONFUSION, False, 0, 3, "unstable / non-repeatable (judge-only)"),
            (label(fd.UNSTABLE), v2.STAY_FAST, True, 3, 3, "routing miss: escalated a should-stay-FAST turn"),
            (None, v2.STAY_FAST, False, 1, 0, "FAST model failure (return turn)"),
            (label(fd.UNSTABLE), v2.DEEPENING, True, 3, 3, "unneeded escalation (cost only)"),
        )
        for lab, role, sent, fast_ok, deep_ok, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(v2.attribute(lab, role, sent, fast_ok, deep_ok, 3), expected)


if __name__ == "__main__":
    unittest.main()
