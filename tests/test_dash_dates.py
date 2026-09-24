"""A date is not a range, and neither is a phone number.

From an English verification session, spoken aloud:

    As of 2026 to 09 to 10, the most watched Korean dramas include ...

The speech filter turns a dash between two numbers into "to" -- right for
"$100-$200" and "3-5 days", wrong for "2026-09-10". The comment above the
rule already said a date keeps its dashes; the pattern did not. A phone
number is the same shape: 206-221-7857 would have been two ranges.
"""

from __future__ import annotations

import unittest

from brain.text_filter import TextFilter


class DatesAndNumbersKeepTheirDashesTests(unittest.TestCase):

    def test_an_iso_date_is_left_alone(self):
        said = TextFilter.natural_dashes(
            "As of 2026-09-10, the most watched dramas include these.",
        )

        self.assertIn("2026-09-10", said)
        self.assertNotIn(" to ", said)

    def test_phone_numbers_are_left_alone(self):
        for number in ("206-221-7857", "010-1234-5678", "02-123-4567"):
            with self.subTest(number=number):
                said = TextFilter.natural_dashes(f"The number is {number}.")
                self.assertIn(number, said)

    def test_a_range_is_still_said_to(self):
        self.assertIn("$100 to $200", TextFilter.natural_dashes("$100-$200"))
        self.assertIn("3 to 5 days", TextFilter.natural_dashes("3-5 days"))

    def test_a_word_with_a_dash_is_still_left_alone(self):
        self.assertIn("27-inch", TextFilter.natural_dashes("a 27-inch monitor"))

    def test_the_speech_filter_applies_it(self):
        spoken = TextFilter.for_speech("As of 2026-09-10 it is still airing.")

        self.assertIn("2026-09-10", spoken)


if __name__ == "__main__":
    unittest.main()
