"""Phase 2: what an action assumes, and what would prove it worked.

The case that made this necessary passed the old check: the search box
held "bang bang IVE", "After LIKE IVE" was typed without clearing, and the
box ended up holding both. Asked "does it contain the requested text?" the
field said yes, and an action that had done the wrong thing was reported
as verified.
"""

import unittest

from tools.computer_control.action_contract import (
    blind_typing_effect,
    field_is_empty,
    replacement_effect,
)


class PreconditionTests(unittest.TestCase):
    def test_an_empty_field_is_ready(self):
        check = field_is_empty("")

        self.assertTrue(check.holds)
        self.assertFalse(check.failed)

    def test_a_field_holding_text_is_not_ready_and_says_what_is_there(self):
        check = field_is_empty("bang bang IVE")

        self.assertTrue(check.failed)
        self.assertIn("bang bang IVE", check.evidence)

    def test_an_unreadable_field_is_neither_ready_nor_violated(self):
        check = field_is_empty(None)

        self.assertIsNone(check.holds)
        self.assertFalse(check.failed)

    def test_whitespace_alone_does_not_count_as_contents(self):
        self.assertTrue(field_is_empty("   ").holds)


class ReplacementEffectTests(unittest.TestCase):
    def test_an_exact_match_is_the_strongest_result(self):
        check = replacement_effect("After LIKE IVE", "", "After LIKE IVE")

        self.assertTrue(check.holds)
        self.assertIn("exactly", check.evidence)

    def test_an_append_is_a_failure_however_much_of_it_is_present(self):
        check = replacement_effect(
            "After LIKE IVE", "bang bang IVE", "bang bang IVEAfter LIKE IVE",
        )

        self.assertFalse(check.holds)
        self.assertIn("added onto it", check.evidence)

    def test_extra_decoration_around_the_value_still_counts(self):
        # An app that trims, pads or decorates its own value has not made
        # the mistake this check hunts for -- the old contents are gone.
        check = replacement_effect("Laufey", "", "Laufey ")

        self.assertTrue(check.holds)

    def test_retyping_the_same_value_is_not_mistaken_for_an_append(self):
        check = replacement_effect("Laufey", "Laufey", "Laufey")

        self.assertTrue(check.holds)

    def test_a_readable_field_without_the_text_is_a_failure(self):
        check = replacement_effect("Laufey", "", "something else")

        self.assertFalse(check.holds)

    def test_an_unreliable_reading_is_inconclusive_rather_than_failed(self):
        check = replacement_effect(
            "Laufey", "", "something else", high_confidence=False,
        )

        self.assertIsNone(check.holds)

    def test_an_unreadable_field_after_typing_is_inconclusive(self):
        check = replacement_effect("Laufey", "", None)

        self.assertIsNone(check.holds)

    def test_clearing_a_field_is_proved_by_it_being_empty(self):
        self.assertTrue(replacement_effect("", "old text", "").holds)
        self.assertFalse(replacement_effect("", "old text", "old text").holds)


class BlindTypingTests(unittest.TestCase):
    def test_typing_with_no_readable_field_is_never_reported_as_proved(self):
        check = blind_typing_effect("'검색하기'")

        self.assertIsNone(check.holds)
        self.assertIn("keyboard focus", check.evidence)


if __name__ == "__main__":
    unittest.main()
