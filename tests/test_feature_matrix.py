import json
import unittest
from collections import Counter
from pathlib import Path

from brain.intent_router import (
    ADVICE_DOMAINS,
    ALLOWED_INTENTS,
    INFORMATION_FRESHNESS_VALUES,
)


MATRIX_PATH = Path(__file__).with_name("feature_matrix.json")


class FeatureMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        cls.cases = cls.matrix["cases"]

    def test_case_ids_are_unique_and_required_fields_are_present(self):
        ids = [case.get("id") for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in self.cases:
            with self.subTest(case=case.get("id")):
                self.assertTrue(case.get("feature"))
                self.assertTrue(case.get("input"))
                self.assertIn(case.get("tier"), {"smoke", "extended"})
                self.assertTrue(case.get("expected"))

    def test_each_feature_has_several_natural_language_variants(self):
        minimum = int(self.matrix["minimum_variants_per_feature"])
        counts = Counter(case["feature"] for case in self.cases)
        self.assertGreaterEqual(len(counts), 15)
        for feature, count in counts.items():
            with self.subTest(feature=feature):
                self.assertGreaterEqual(count, minimum)

    def test_expected_router_values_use_supported_schema(self):
        supported_fields = {
            "intent",
            "action_requested",
            "action_target",
            "computer_operation",
            "computer_location",
            "computer_url",
            "screen_target",
            "memory_relevant",
            "detailed_response",
            "recommendation_needed",
            "urgent_safety",
            "advice_domain",
            "information_freshness",
            "requires_external_evidence",
            "verification_required",
        }
        for case in self.cases:
            expected = case["expected"]
            with self.subTest(case=case["id"]):
                self.assertFalse(set(expected) - supported_fields)
                if "intent" in expected:
                    self.assertIn(expected["intent"], ALLOWED_INTENTS)
                if "advice_domain" in expected:
                    self.assertIn(expected["advice_domain"], ADVICE_DOMAINS)
                if "information_freshness" in expected:
                    self.assertIn(
                        expected["information_freshness"],
                        INFORMATION_FRESHNESS_VALUES,
                    )

    def test_write_features_only_classify_and_never_execute(self):
        write_features = {
            "project_edit",
            "git_commit",
            "git_publish",
            "agent_create",
            "calendar_action",
        }
        for case in self.cases:
            if case["feature"] not in write_features:
                continue
            with self.subTest(case=case["id"]):
                self.assertNotIn("execute", case)
                self.assertTrue(case["expected"].get("action_requested"))


if __name__ == "__main__":
    unittest.main()
