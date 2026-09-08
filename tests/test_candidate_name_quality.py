# -*- coding: utf-8 -*-
"""What a card is called, and when it should not be a card at all.

Four classes measured live across seven dogfood queries, all of them
reaching the window as cards:

    review headline   Excellent location for Myeongdong exploring
                      REVIEW: Memorable stay in Myeongdong!!!!
    duplicate variant ROYAL HOTEL SEOUL / ROYAL HOTEL SEOUL (South Korea)
    site cruft        Tmark Hotel Myeongdong ... - KAYAK
    cut short         H27E6 27
    category          High Refresh Gaming Computer Monitors - 4K, OLED & HDR

Each rule is structural rather than a phrase list, and each has a matching
set of things it must leave alone -- the point of the phase is precision
without losing recall to broad guessing.
"""

from __future__ import annotations

import unittest

from brain import candidate_fit as cf
from brain import response_surface as sf
from brain import result_state as rs


HOTELS = "good hotels in Seoul"
MONITORS = "good gaming monitor"
KEYBOARDS = "mechanical keyboards"


def _cards(names, subject=HOTELS, urls=None):
    urls = urls or [f"https://example.com/{i}" for i in range(len(names))]
    return [
        item.name for item in sf.from_candidates(
            [
                rs.Candidate(name=name, url=url)
                for name, url in zip(names, urls)
            ],
            subject=subject,
        ).items
    ]


class AReviewIsNotTheThingReviewedTests(unittest.TestCase):

    def test_a_sentence_about_a_stay(self):
        self.assertTrue(
            cf.reads_as_commentary("Excellent location for Myeongdong exploring"),
        )

    def test_an_explicit_review_headline(self):
        self.assertTrue(
            cf.reads_as_commentary("REVIEW: Memorable stay in Myeongdong!!!!"),
        )

    def test_shouting_is_a_headline(self):
        self.assertTrue(cf.reads_as_commentary("Memorable stay in Myeongdong!!!!"))

    def test_the_address_of_one_persons_review(self):
        # The listing sites keep the hotel and one stay at it at different
        # addresses, which is better evidence than any wording.
        self.assertTrue(cf.reads_as_commentary(
            "Great little hotel in a good spot",
            "https://www.tripadvisor.com/ShowUserReviews-g294197-d106-r8",
        ))

    def test_the_entity_page_at_the_same_site_is_kept(self):
        self.assertEqual(
            cf.reads_as_commentary(
                "HOTEL28 MYEONGDONG - Prices & Hotel Reviews (Seoul, South Korea)",
                "https://www.tripadvisor.com/Hotel_Review-g294197-d10692374",
            ),
            "",
        )

    def test_a_single_exclamation_is_not_shouting(self):
        # "Best Price on Jongno M and Lucky Hotel in Seoul + Reviews!" is a
        # real hotel. One exclamation mark must not throw it away.
        self.assertEqual(
            cf.reads_as_commentary(
                "Best Price on Jongno M and Lucky Hotel in Seoul + Reviews!",
            ),
            "",
        )

    def test_real_names_are_not_sentences(self):
        for name in (
            "Hotel Inspiroom Jongro",
            "Sofitel Ambassador Seoul Hotel",
            "GANGNAM MYEONOK, Seoul - 34 Nonhyeon-ro 152-gil",
            "ASUS ROG Swift OLED PG32UCDM Gen3",
            "Keychron Q1 Max",
            "Hotel Morning Sky in Seoul, South Korea",
            "Seoul DDJ STAY 대동장",
        ):
            with self.subTest(name=name):
                self.assertEqual(cf.reads_as_commentary(name), "")


class OneCardPerEntityTests(unittest.TestCase):

    def test_a_region_qualifier_is_the_same_hotel(self):
        # A third, real hotel is present so the result is still a shortlist:
        # collapsing a pair to one leaves one, and one is not a shortlist.
        self.assertEqual(
            _cards(["ROYAL HOTEL SEOUL", "ROYAL HOTEL SEOUL (South Korea)",
                    "Hotel Inspiroom Jongro"]),
            ["ROYAL HOTEL SEOUL", "Hotel Inspiroom Jongro"],
        )

    def test_a_longer_way_of_saying_it_is_the_same_hotel(self):
        self.assertEqual(
            _cards(["Hotel Morning Sky in Seoul, South Korea",
                    "Hotel Morning Sky", "Lotte Hotel World"]),
            ["Hotel Morning Sky in Seoul, South Korea", "Lotte Hotel World"],
        )

    def test_collapsing_to_one_leaves_no_shortlist(self):
        # And that is correct: two cards for one hotel is worse than none.
        self.assertEqual(
            _cards(["ROYAL HOTEL SEOUL", "ROYAL HOTEL SEOUL (South Korea)"]),
            [],
        )

    def test_the_category_word_does_not_make_a_second_card(self):
        # Everything that differs between these is the word searched for.
        self.assertEqual(
            _cards(["HOTEL THE BOTANIK SEWOON MYEONGDONG (Seoul, South)",
                    "BOTANIK SEWOON MYEONGDONG", "Hotel28 Myeongdong"],
                   subject="hotels Myeongdong"),
            ["HOTEL THE BOTANIK SEWOON MYEONGDONG (Seoul, South)",
             "Hotel28 Myeongdong"],
        )

    def test_a_model_suffix_is_not_a_category_word(self):
        # "Keychron Q1" is inside "Keychron Q1 Max" and they are two
        # products. Containment alone would have lost one.
        self.assertEqual(
            _cards(["Keychron Q1 Max", "Keychron Q1", "Akko 5075B"],
                   subject="good 75% keyboard"),
            ["Keychron Q1 Max", "Keychron Q1", "Akko 5075B"],
        )

    def test_different_hotels_are_different_cards(self):
        self.assertEqual(
            _cards(["Lotte Hotel World", "Lotte Hotel Seoul",
                    "Hotel Inspiroom Jongro"]),
            ["Lotte Hotel World", "Lotte Hotel Seoul", "Hotel Inspiroom Jongro"],
        )

    def test_different_models_of_one_brand_are_different_cards(self):
        # Q1 Max and Q1 are two products. Collapsing on label similarity
        # alone would lose one of them.
        self.assertEqual(
            _cards(["Keychron Q1 Max", "Keychron Q5 Max", "Keychron Q1"],
                   subject=KEYBOARDS),
            ["Keychron Q1 Max", "Keychron Q5 Max", "Keychron Q1"],
        )


class TheSitesOwnNameIsNotTheThingsTests(unittest.TestCase):

    def test_a_tail_that_names_the_publisher_is_dropped(self):
        self.assertEqual(
            sf._card_name(
                "Tmark Hotel Myeongdong. Seoul Hotel Deals - KAYAK",
                "https://www.kayak.com/hotels/tmark",
            ),
            # "Deals" goes too, as listing noise the older rule removes.
            "Tmark Hotel Myeongdong. Seoul Hotel",
        )

    def test_it_is_read_from_the_address_not_a_list(self):
        self.assertNotIn(
            "Best Buy",
            sf._card_name(
                "HP OMEN 31.5 QHD 165Hz Gaming Monitor - Best Buy",
                "https://www.bestbuy.com/site/hp-omen",
            ),
        )

    def test_a_dash_that_is_part_of_the_name_survives(self):
        self.assertEqual(
            sf._card_name(
                "Dell S2722DGM - 27 inch Curved Gaming Monitor",
                "https://www.dell.com/p/s2722dgm",
            ),
            "Dell S2722DGM - 27 inch Curved Gaming Monitor",
        )

    def test_an_address_that_follows_a_dash_survives(self):
        self.assertIn(
            "Nonhyeon-ro",
            sf._card_name(
                "Gangnam Myeonok, Seoul - 34 Nonhyeon-ro 152-gil",
                "https://www.tripadvisor.com/Restaurant_Review-g294197",
            ),
        )


class SeveralOfThemIsNotOneTests(unittest.TestCase):

    def test_a_plural_of_the_request_is_the_category(self):
        self.assertTrue(
            sf._refuses_a_card("High Refresh Gaming Computer Monitors", MONITORS),
        )

    def test_a_title_cut_mid_phrase(self):
        self.assertTrue(sf._refuses_a_card("H27E6 27", "1440p monitors"))

    def test_a_model_number_is_not_a_cut(self):
        for name in ("Keychron Q1 Max", "ASUS ROG Swift OLED PG32UCDM Gen3",
                     "Alienware AW2524HF", "Hotel28 Myeongdong"):
            with self.subTest(name=name):
                self.assertFalse(sf._refuses_a_card(name, MONITORS))

    def test_a_plural_that_is_part_of_a_name_survives(self):
        # "Four Seasons" is a hotel; "seasons" is not what was asked for.
        self.assertFalse(sf._refuses_a_card("Four Seasons Hotel Seoul", HOTELS))

    def test_real_places_still_pass(self):
        for name in ("Hotel Inspiroom Jongro", "Sofitel Ambassador Seoul Hotel",
                     "Lotte Hotel World"):
            with self.subTest(name=name):
                self.assertFalse(sf._refuses_a_card(name, HOTELS))


if __name__ == "__main__":
    unittest.main()
