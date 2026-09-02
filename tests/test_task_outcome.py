"""The five terminal states, and the table that must not fall behind.

The classification is only worth anything if it stays complete. A planner
gaining a new failure code without classifying it here would silently land
in the fallback -- so the drift test below reads every ``failure_code=``
literal in the source and fails if one is unknown.
"""

import re
import unittest
from pathlib import Path

from brain import task_outcome
from brain.task_outcome import (
    CANCELLED,
    NEEDS_USER_INPUT,
    RETRYABLE_FAILURE,
    SUCCESS,
    TERMINAL_FAILURE,
    UNVERIFIED,
    VERIFIED,
    classify,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ASSIGNED = re.compile(r"""failure_code=["']([a-z_]+)["']""")


class DriftTests(unittest.TestCase):
    """Every code the planners emit is classified here."""

    def test_no_emitted_failure_code_is_unclassified(self):
        emitted = set()
        for folder in ("brain", "tools", "agents"):
            for path in (PROJECT_ROOT / folder).rglob("*.py"):
                emitted.update(
                    _ASSIGNED.findall(path.read_text(encoding="utf-8"))
                )

        self.assertTrue(emitted, "found no failure codes to check")
        unclassified = emitted - set(task_outcome.KNOWN_FAILURE_CODES)
        self.assertEqual(
            unclassified, set(),
            "these failure codes are emitted but not classified in "
            "brain/task_outcome.py -- add each to the group that says what "
            "should happen next",
        )

    def test_the_groups_do_not_overlap(self):
        groups = (
            task_outcome._CANCELLED,
            task_outcome._NEEDS_USER_INPUT,
            task_outcome._VERIFICATION_FAILED,
            task_outcome._RETRYABLE,
            task_outcome._TERMINAL,
        )
        seen: set[str] = set()
        for group in groups:
            self.assertEqual(
                seen & group, set(), "a code appears in two groups",
            )
            seen |= group


class ClassificationTests(unittest.TestCase):

    def test_a_plain_success_is_success(self):
        self.assertEqual(classify("done").outcome, SUCCESS)

    def test_success_records_whether_anything_observed_it(self):
        self.assertEqual(classify("done", observed=True).verification, VERIFIED)
        self.assertEqual(classify("done").verification, UNVERIFIED)
        self.assertTrue(classify("done", observed=True).verified)
        self.assertFalse(classify("done").verified)

    def test_a_verification_failure_is_never_success(self):
        # The rule this phase exists to enforce: the tool ran, the expected
        # state did not appear, and that is not a completed goal.
        for code in task_outcome._VERIFICATION_FAILED:
            with self.subTest(code=code):
                for status in ("done", "failed"):
                    outcome = classify(status, code)
                    self.assertNotEqual(outcome.outcome, SUCCESS)
                    self.assertTrue(outcome.is_verification_failure)

    def test_cancellation_outranks_a_failed_status(self):
        outcome = classify("failed", "user_took_over")

        self.assertEqual(outcome.outcome, CANCELLED)
        self.assertFalse(outcome.may_retry)

    def test_missing_information_asks(self):
        for code in ("needs_clarification", "direct_target_ambiguous"):
            with self.subTest(code=code):
                self.assertEqual(
                    classify("failed", code).outcome, NEEDS_USER_INPUT,
                )

    def test_terminal_failures_never_invite_a_retry(self):
        for code in task_outcome._TERMINAL:
            with self.subTest(code=code):
                outcome = classify("failed", code)
                self.assertEqual(outcome.outcome, TERMINAL_FAILURE)
                self.assertFalse(outcome.may_retry)

    def test_retryable_failures_may_retry(self):
        for code in task_outcome._RETRYABLE:
            with self.subTest(code=code):
                self.assertTrue(classify("failed", code).may_retry)

    def test_a_first_miss_retries_and_a_repeated_one_does_not(self):
        # The distinction the planner already relies on to recover by
        # changing approach.
        self.assertTrue(classify("failed", "direct_target_not_found").may_retry)
        self.assertEqual(
            classify("failed", "repeated_not_found").outcome, TERMINAL_FAILURE,
        )

    def test_an_unknown_failure_gets_one_attempt_not_a_loop(self):
        outcome = classify("failed", "something_nobody_modelled")

        self.assertEqual(outcome.outcome, RETRYABLE_FAILURE)

    def test_an_unrecognised_status_stops_rather_than_guessing(self):
        self.assertEqual(
            classify("wat", "").outcome, TERMINAL_FAILURE,
        )

    def test_observed_cannot_rescue_a_failure(self):
        outcome = classify("failed", "planner_stalled", observed=True)

        self.assertEqual(outcome.outcome, TERMINAL_FAILURE)
        self.assertFalse(outcome.verified)


if __name__ == "__main__":
    unittest.main()
