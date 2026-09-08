# -*- coding: utf-8 -*-
"""A candidate has to be in the place it was asked for.

Measured live, answering "find me some good restaurants in Gangnam":

    GANGNAM RESTAURANT, Stevenage    -- Hertfordshire, England
    Gangnam, Lynnwood                -- Washington State

Both carry "Gangnam" in the name, which is why matching on the requested
location was never enough: a Korean restaurant abroad is named after the
place it came from. This is the more dangerous failure than a listicle --
a listicle looks wrong, and these look right.

What a listing also states is where it *is*, in one of two places: an
"in <somewhere>" phrase in its summary, or a ", <somewhere>" after its name.
The rule reads those and nothing else, so it is silent whenever the result
does not say.
"""

from __future__ import annotations

import unittest

from brain import candidate_fit as cf


class SomewhereElseIsRefusedTests(unittest.TestCase):

    def test_a_summary_that_says_where_it_is(self):
        why = cf.elsewhere(
            "GANGNAM RESTAURANT, Stevenage",
            "https://www.tripadvisor.ca/Restaurant_Review-g190726-d11437154",
            "See 186 unbiased reviews of Gangnam Restaurant, ranked #13 "
            "among 161 restaurants in Stevenage.",
            "Gangnam",
        )

        self.assertIn("Stevenage", why)

    def test_a_title_that_says_where_it_is(self):
        # The same result on a run whose snippet carried no location at all.
        why = cf.elsewhere(
            "Gangnam, Lynnwood - Menu, Reviews (306), Photos (35)",
            "https://www.restaurantji.com/wa/lynnwood/gangnam-restaurant-/",
            "Latest reviews, photos and ratings.",
            "Gangnam",
        )

        self.assertIn("Lynnwood", why)

    def test_it_reaches_the_verdict(self):
        problem = _gangnam()
        fits = cf.evaluate(
            [{
                "title": "Gangnam, Lynnwood - Menu, Reviews",
                "url": "https://www.restaurantji.com/wa/lynnwood/gangnam/",
                "summary": "Latest reviews, photos and ratings.",
            }],
            problem,
            shape=cf.PLACE,
        )

        self.assertFalse(fits[0].viable)


class TheSamePlaceIsKeptTests(unittest.TestCase):
    """The half that must not over-fire."""

    def test_a_wider_place_in_the_same_market(self):
        # Gangnam is in Seoul. Naming the city is not naming somewhere else.
        self.assertEqual(
            cf.elsewhere(
                "GANGNAM MYEONOK, Seoul - 34 Nonhyeon-ro 152-gil",
                "https://www.tripadvisor.com/Restaurant_Review-g294197-d4032315",
                "See 64 reviews of Gangnam Myeonok, ranked #306 of 19,189 "
                "restaurants in Seoul.",
                "Gangnam",
            ),
            "",
        )

    def test_the_country_after_the_city(self):
        self.assertEqual(
            cf.elsewhere("Hotel Morning Sky in Seoul, South Korea", "", "",
                         "Seoul"),
            "",
        )

    def test_a_result_that_never_says_where_it_is(self):
        self.assertEqual(
            cf.elsewhere("Hotel Inspiroom Jongro", "", "A quiet hotel.",
                         "Seoul"),
            "",
        )

    def test_a_request_with_no_location_checks_nothing(self):
        self.assertEqual(
            cf.elsewhere("Gangnam, Lynnwood", "", "reviews in Lynnwood", ""),
            "",
        )

    def test_a_real_candidate_still_reaches_the_verdict(self):
        problem = _gangnam()
        fits = cf.evaluate(
            [{
                "title": "GANGNAM MYEONOK, Seoul - 34 Nonhyeon-ro 152-gil",
                "url": "https://www.tripadvisor.com/Restaurant_Review-g294197-d4",
                "summary": "ranked #306 of 19,189 restaurants in Seoul. "
                           "Open daily, reservations available.",
            }],
            problem,
            shape=cf.PLACE,
        )

        self.assertFalse(fits[0].shape_problem)


def _gangnam():
    from brain import recommendation_state as rs

    said = "find me some good restaurants in Gangnam"
    return rs.update(rs.start(said), said)


if __name__ == "__main__":
    unittest.main()
