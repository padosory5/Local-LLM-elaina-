"""Spoken element labels, separate from the ones used for verification.

A search result's accessible label is the entire result block. Read aloud,
"Click Novotel Citygate Hong Kong Booking.com > ... > Hotels in Hong Kong?"
is unintelligible -- and that is exactly what a live confirmation question
and a live "Clicked ..." result both said.
"""

import unittest

from tools.browser_control.browser_observer import spoken_label


class SpokenLabelTests(unittest.TestCase):
    def test_a_breadcrumb_result_keeps_only_its_name(self):
        self.assertEqual(
            spoken_label("Novotel Citygate Hong Kong Booking.com › ... › Hotels in Hong Kong"),
            "Novotel Citygate Hong Kong",
        )

    def test_other_breadcrumb_separators_are_handled(self):
        for raw in (
            "Best Hotels | Booking.com | Deals",
            "Best Hotels » Booking.com » Deals",
            "Best Hotels · Booking.com",
            "Best Hotels ... Booking.com",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(spoken_label(raw), "Best Hotels")

    def test_a_plain_control_label_is_untouched(self):
        for raw in ("Images", "Book now", "Search", "Add to cart"):
            with self.subTest(raw=raw):
                self.assertEqual(spoken_label(raw), raw)

    def test_a_trailing_bare_domain_is_dropped(self):
        self.assertEqual(
            spoken_label("The Peninsula Hong Kong hotels.com"),
            "The Peninsula Hong Kong",
        )

    def test_a_long_label_is_clipped_on_a_word_boundary(self):
        result = spoken_label("word " * 40)

        self.assertLessEqual(len(result), 60)
        self.assertFalse(result.endswith("wor"))

    def test_an_empty_label_stays_empty(self):
        self.assertEqual(spoken_label(""), "")
        self.assertEqual(spoken_label(None), "")

    def test_a_label_that_is_only_a_separator_is_not_emptied(self):
        # Better to say something odd than to say nothing at all about
        # what is being clicked.
        self.assertTrue(spoken_label("›"))


if __name__ == "__main__":
    unittest.main()
