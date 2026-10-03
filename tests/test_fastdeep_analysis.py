"""The FAST/DEEP analysis rules (docs/FAST_DEEP_PROTOCOL.md, amended).

These pin the definitions the baseline is read with, so that they cannot
drift after the baseline is seen: the tiers, what one comparison is, what
makes a turn stable, where a category comes from, and the pressure rule.
"""

import inspect
import unittest
from collections import Counter

from evals import corpus
from evals import fastdeep_analysis as fd


def _turn(defects=(), *, checks_failed=(), route=None, complete=True, key=("s", 0),
          seconds=2.0, route_seconds=1.0, error=False):
    properties = {name: "no" for name in defects}
    if error:
        properties["answers_the_ask"] = "error"
    return fd.TurnOutcome(
        scenario=key[0], index=key[1], suite="explanation", first=key[1] == 0,
        complete=complete, judge_error=error,
        checks_failed=frozenset(checks_failed), properties=properties,
        correctness=(0, 0), seconds=seconds, route=route or {},
        route_seconds=route_seconds,
    )


class TierTests(unittest.TestCase):

    def test_the_protocol_tiers(self):
        for name in ("answers_the_ask", "takes_the_turn_as_meant", "accurate",
                     "adapts_to_confusion", "utc_offset", "must_match_any", "quantity",
                     "must_not_contain", "language"):
            self.assertEqual(fd.tier(name), "substance", name)
        for name in ("says_what_it_solves", "concrete_first", "terms_explained"):
            self.assertEqual(fd.tier(name), "explanation", name)
        for name in ("only_what_helps", "leads_with_the_answer", "no_service_phrasing",
                     "notation_earned", "no_unrelated_remark"):
            self.assertEqual(fd.tier(name), "form", name)
        for name in ("not_searched", "no_cards", "speakable", "display_math_intact"):
            self.assertEqual(fd.tier(name), "pipeline", name)


class ComparisonTests(unittest.TestCase):

    def test_an_explanation_defect_fixed_is_deep_plus(self):
        found = fd.compare_turn(_turn(["terms_explained"]), _turn())
        self.assertEqual(found.kind, "deep+")
        self.assertFalse(found.judge_dependent)

    def test_a_form_defect_added_does_not_undo_it(self):
        found = fd.compare_turn(_turn(["terms_explained"]), _turn(["only_what_helps"]))
        self.assertEqual(found.kind, "deep+")

    def test_a_substance_defect_added_blocks_it(self):
        found = fd.compare_turn(_turn(["terms_explained"]), _turn(["answers_the_ask"]))
        self.assertEqual(found.kind, "deep-worse")

    def test_the_mirror_is_deep_worse(self):
        self.assertEqual(fd.compare_turn(_turn(), _turn(["accurate"])).kind, "deep-worse")

    def test_an_explanation_traded_for_another_is_mixed(self):
        found = fd.compare_turn(_turn(["concrete_first"]), _turn(["terms_explained"]))
        self.assertEqual(found.kind, "mixed")

    def test_form_differences_alone_never_make_deep_plus(self):
        found = fd.compare_turn(_turn(["only_what_helps", "no_service_phrasing"]), _turn())
        self.assertEqual(found.kind, "form-only")

    def test_pipeline_checks_are_not_a_model_difference(self):
        found = fd.compare_turn(_turn(checks_failed=["not_searched"]), _turn())
        self.assertEqual(found.kind, "same")

    def test_a_correctness_check_fixed_is_substance(self):
        found = fd.compare_turn(_turn(checks_failed=["must_match_any"]), _turn())
        self.assertEqual(found.kind, "deep+")

    def test_resting_only_on_a_below_bar_property_is_judge_dependent(self):
        self.assertTrue(fd.compare_turn(_turn(["concrete_first"]), _turn()).judge_dependent)
        self.assertTrue(fd.compare_turn(_turn(["adapts_to_confusion"]), _turn()).judge_dependent)
        self.assertFalse(fd.compare_turn(_turn(["concrete_first", "terms_explained"]),
                                         _turn()).judge_dependent)

    def test_an_incomplete_or_errored_verdict_set_is_not_compared(self):
        self.assertEqual(fd.compare_turn(_turn(complete=False), _turn()).kind, "excluded")
        self.assertEqual(fd.compare_turn(_turn(), _turn(error=True)).kind, "excluded")
        self.assertEqual(fd.compare_turn(None, _turn()).kind, "excluded")


class StableLabelTests(unittest.TestCase):
    PLUS = fd.Comparison("deep+", frozenset({"terms_explained"}))
    JUDGED = fd.Comparison("deep+", frozenset({"concrete_first"}), judge_dependent=True)
    WORSE = fd.Comparison("deep-worse", broken=frozenset({"accurate"}))
    SAME = fd.Comparison("same")
    MIXED = fd.Comparison("mixed")

    def test_two_of_three_make_it_stable(self):
        self.assertEqual(fd.label_turn([self.PLUS, self.PLUS, self.SAME]), "stable DEEP+")
        self.assertEqual(fd.label_turn([self.WORSE, self.SAME, self.WORSE]), "stable DEEP-worse")

    def test_one_lucky_generation_is_not_ground_truth(self):
        self.assertEqual(fd.label_turn([self.PLUS, self.SAME, self.SAME]), fd.UNSTABLE)
        self.assertEqual(fd.label_turn([self.PLUS, self.WORSE, self.SAME]), fd.UNSTABLE)

    def test_mixed_counts_toward_neither(self):
        self.assertEqual(fd.label_turn([self.PLUS, self.MIXED, self.MIXED]), fd.UNSTABLE)

    def test_a_judge_dependent_comparison_waits_for_a_person(self):
        self.assertEqual(fd.label_turn([self.JUDGED, self.JUDGED, self.SAME]), "pending DEEP+")
        self.assertEqual(fd.label_turn([self.JUDGED, self.PLUS, self.SAME]), "pending DEEP+")
        self.assertEqual(fd.label_turn([self.JUDGED, self.PLUS, self.SAME], confirmed={1}),
                         "stable DEEP+")
        # Confirming a comparison that is not judge-dependent changes nothing.
        self.assertEqual(fd.label_turn([self.PLUS, self.SAME, self.SAME], confirmed={2}),
                         fd.UNSTABLE)


class CategoryTests(unittest.TestCase):

    def test_a_category_is_read_from_the_corpus_alone(self):
        # Amendment 4: nothing a model or a router said can move a turn.
        self.assertEqual(list(inspect.signature(fd.category).parameters), ["scenario", "index"])

    def test_every_scored_turn_has_the_protocols_category(self):
        counts = Counter(cat for _, cat in fd.corpus_index().values())
        self.assertEqual(counts, Counter({
            "D1": 9, "D2": 4, "D4": 2, "D8": 2, "C": 19,
            "F1": 4, "F2": 4, "F3": 11, "F4": 9, "F5": 3, "F6": 6,
        }))
        self.assertEqual(sum(counts.values()),
                         sum(len(s.scored) for s in corpus.load(corpus.ALL_SUITES)))

    def test_the_routers_reading_does_not_change_the_category(self):
        key = ("index_fund_what_do_you_mean", 1)
        fast = {"path": None, "run": None,
                "turns": {key: _turn(key=key, route={"intent": "conversation", "budget": "explain"})}}
        deep = {"path": None, "run": None,
                "turns": {key: _turn(key=key, route={"intent": "clarification", "budget": "elaborate"})}}
        labels = fd.label_turns([fast], [deep])
        self.assertEqual(labels[key].category, "D1")


class RoutingTests(unittest.TestCase):

    def setUp(self):
        self.keys = [("a", 0), ("b", 0), ("c", 0)]
        explain = {"budget": "explain", "shape": "explain"}
        value = {"budget": "value", "shape": "value"}
        self.fast = {"turns": {
            ("a", 0): _turn(["terms_explained"], key=("a", 0), route=explain),
            ("b", 0): _turn(key=("b", 0), route=value),
            ("c", 0): _turn(key=("c", 0), route=explain),
        }}
        self.deep = {"turns": {
            ("a", 0): _turn(key=("a", 0), seconds=9.0, route_seconds=7.0),
            ("b", 0): _turn(key=("b", 0), seconds=9.0, route_seconds=7.0),
            ("c", 0): _turn(["accurate"], key=("c", 0), seconds=9.0, route_seconds=7.0),
        }}
        self.labels = {
            ("a", 0): fd.TurnLabel(("a", 0), "stable DEEP+"),
            ("b", 0): fd.TurnLabel(("b", 0), fd.UNSTABLE),
            ("c", 0): fd.TurnLabel(("c", 0), "stable DEEP-worse"),
        }

    def test_the_bounds(self):
        none = fd.score_policy("all FAST", self.fast, self.deep, self.labels, (0.0, 0.0))
        every = fd.score_policy("all DEEP", self.fast, self.deep, self.labels, (0.0, 0.0))
        oracle = fd.score_policy(fd.ORACLE, self.fast, self.deep, self.labels, (0.0, 0.0))
        self.assertEqual(none["utilization"], (0, 3))
        self.assertEqual(every["recall"], (1, 1))
        self.assertEqual(every["worse_sent"], (1, 1))
        self.assertEqual(oracle["recall"], (1, 1))
        self.assertEqual(oracle["precision"], (1, 1))
        # FAST's answers to the turns left with it pass too.
        self.assertEqual(oracle["turns_passing"], (3, 3))
        self.assertEqual(every["turns_passing"], (2, 3))

    def test_a_policy_reads_the_fast_routers_output(self):
        found = fd.score_policy("P1 budget explain|elaborate", self.fast, self.deep, self.labels,
                                (0.0, 0.0))
        self.assertEqual(found["sent"], {("a", 0), ("c", 0)})
        self.assertEqual(found["precision"], (1, 2))
        self.assertEqual(found["worse_sent"], (1, 1))

    def test_a_deep_turn_pays_the_swap(self):
        # Every turn DEEP: the 8B's route 1.0 + the 27B's answer (9.0 - 7.0)
        # + the 27B's load 7.4.
        every = fd.score_policy("all DEEP", self.fast, self.deep, self.labels, (7.4, 1.7))
        self.assertAlmostEqual(every["p50"], 10.4)

    def test_the_turn_after_a_deep_turn_reloads_the_8b(self):
        fast = {"turns": {("s", 0): _turn(["terms_explained"], key=("s", 0)),
                          ("s", 1): _turn(key=("s", 1))}}
        deep = {"turns": {("s", 0): _turn(key=("s", 0), seconds=9.0, route_seconds=7.0),
                          ("s", 1): _turn(key=("s", 1), seconds=9.0, route_seconds=7.0)}}
        labels = {("s", 0): fd.TurnLabel(("s", 0), "stable DEEP+"),
                  ("s", 1): fd.TurnLabel(("s", 1), fd.UNSTABLE)}
        found = fd.score_policy(fd.ORACLE, fast, deep, labels, (7.4, 1.7))
        # 10.4 for the DEEP turn; 2.0 + 1.7 for the FAST turn after it.
        self.assertAlmostEqual(found["p50"], (10.4 + 3.7) / 2)
        self.assertAlmostEqual(found["p95"], 3.7)


class PressureTests(unittest.TestCase):

    def test_below_sixty_percent_of_normal_is_pressure(self):
        normal = {"m": 40.0}
        self.assertTrue(fd.pressure_affected({"model": "m", "tokens_per_second": 23.9,
                                              "eval_count": 100}, normal))
        self.assertFalse(fd.pressure_affected({"model": "m", "tokens_per_second": 24.1,
                                               "eval_count": 100}, normal))

    def test_what_cannot_be_judged_is_not_counted(self):
        normal = {"m": 40.0}
        self.assertIsNone(fd.pressure_affected({"model": "m", "tokens_per_second": 5,
                                                "eval_count": 3}, normal))
        self.assertIsNone(fd.pressure_affected({"model": "other", "tokens_per_second": 5,
                                                "eval_count": 100}, normal))


if __name__ == "__main__":
    unittest.main()
