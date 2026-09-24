"""The task boundary, scored in both directions.

``about_the_same_thing`` is asked once per turn, and everything downstream
follows what it says: candidates, constraints, evidence, and the search
query. It had tests from the first dogfood session and never had a number,
so a regression in either direction looked identical from outside.

The matrix beside this holds both directions on purpose. Splitting too
eagerly loses three turns of established context -- the failure the
follow-up branch exists to prevent -- so a case list of only the splits
would score 100% on a function that always said "different".

Measured before the fix in ``scripts/task_boundary_report.py``:

    must continue  10/10 (100%)
    must split      3/8   (38%)

Every Korean split failed, and so did the English case whose query became
"studio apartments September 13th 206-221 in South Korea".
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.task_boundary_report import decide

MATRIX_PATH = Path(__file__).with_name("boundary_matrix.json")
MATRIX = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


class TheBoundaryHoldsInBothDirectionsTests(unittest.TestCase):

    def test_every_case_decides_the_way_it_should(self):
        for case in MATRIX["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    decide(case), case["expect"],
                    f"{case['id']}: {case['why']}",
                )

    def test_the_matrix_measures_both_directions(self):
        # The instrument's own honesty check. A matrix that drifted to all
        # splits would go on passing while saying nothing.
        wanted = [case["expect"] for case in MATRIX["cases"]]

        self.assertGreaterEqual(wanted.count("continue"), 6)
        self.assertGreaterEqual(wanted.count("split"), 6)

    def test_both_languages_are_measured(self):
        # Rule 4. The Korean half is where the whole failure lived: every
        # Korean split was wrong at baseline, because the check that would
        # have caught it reads English request grammar.
        languages = {case.get("language") for case in MATRIX["cases"]}

        self.assertEqual(languages, {"en", "ko"})
        for language in ("en", "ko"):
            with self.subTest(language=language):
                here = [
                    case["expect"] for case in MATRIX["cases"]
                    if case.get("language") == language
                ]
                self.assertIn("continue", here)
                self.assertIn("split", here)


if __name__ == "__main__":
    unittest.main()
