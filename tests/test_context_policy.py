import unittest

from brain.context_policy import should_include_grounded_context


class GroundedContextPolicyTests(unittest.TestCase):
    def test_unrelated_live2d_question_does_not_receive_eros_context(self):
        self.assertFalse(should_include_grounded_context(
            has_statement=True,
            intent="conversation",
            is_follow_up=False,
            topic_shift=True,
        ))

    def test_real_follow_up_can_use_recent_verified_context(self):
        self.assertTrue(should_include_grounded_context(
            has_statement=True,
            intent="conversation",
            is_follow_up=True,
            topic_shift=False,
        ))

    def test_fact_check_keeps_context_for_reconciliation(self):
        self.assertTrue(should_include_grounded_context(
            has_statement=True,
            intent="fact_check",
            is_follow_up=False,
            topic_shift=True,
        ))


if __name__ == "__main__":
    unittest.main()
