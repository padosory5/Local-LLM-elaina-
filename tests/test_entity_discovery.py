# -*- coding: utf-8 -*-
"""Reading the names of things out of pages that are about things.

The acquisition half of the entity-oriented work. The surface layer refuses
a card to "The 16 Best Seoul Hotels" and is right to; this is what stops
that being the end of the turn, by taking the sixteen names out of it.

Every string below is a real snippet or title measured from the live index
during this work. The rule that matters most is the one about invention: a
name returned here is always a literal span of retrieved evidence, because
a plausible hotel that does not exist is far worse than one fewer card.
"""

from __future__ import annotations

import unittest

from brain import entity_discovery as ed


HOTELS = "good hotels in Seoul"
KEYBOARDS = "mechanical keyboards"
MONITORS = "gaming monitor"


class NamesAreReadOutOfEvidenceTests(unittest.TestCase):

    def test_the_sentence_pointing_at_a_thing(self):
        found = ed.names_in(
            "The best full-size mechanical keyboard we've tested is the "
            "Keychron Q5 Max .",
            subject=KEYBOARDS, host="rtings.com",
        )

        self.assertEqual(found, ("Keychron Q5 Max",))

    def test_a_verdict_after_a_colon(self):
        found = ed.names_in(
            "Best full-size mechanical keyboard: Corsair K70 Max.",
            subject=KEYBOARDS, host="eurogamer.net",
        )

        self.assertEqual(found, ("Corsair K70 Max",))

    def test_a_list_of_picks_yields_each_one(self):
        found = ed.names_in(
            "Our three top picks for the best mechanical keyboard: the "
            "Keychron V3 Max, the Keychron V5 Max, and the Keychron V6 Max. "
            "Michael Hession/NYT Wirecutter.",
            subject=KEYBOARDS, host="nytimes.com",
        )

        self.assertEqual(
            found,
            ("Keychron V3 Max", "Keychron V5 Max", "Keychron V6 Max"),
        )

    def test_a_photo_credit_is_not_a_product(self):
        # "Michael Hession" reads exactly like a product name, which is why
        # a proper-noun run on its own is never enough.
        found = ed.names_in(
            "Photograph by Michael Hession for our keyboard guide.",
            subject=KEYBOARDS, host="nytimes.com",
        )

        self.assertEqual(found, ())

    def test_a_model_code_carries_itself(self):
        found = ed.names_in(
            "The best gaming monitor we've tested is the ASUS ROG Swift "
            "OLED PG32UCDM Gen3. It's a premium 4k, 240Hz monitor.",
            subject=MONITORS, host="rtings.com",
        )

        self.assertEqual(found, ("ASUS ROG Swift OLED PG32UCDM Gen3",))

    def test_a_place_is_recognised_by_the_noun_that_was_asked_for(self):
        found = ed.names_in(
            "Book Anto Hotel Seoul, Seoul on Tripadvisor: See traveller "
            "reviews, 21 candid photos, and great deals for Anto Hotel Seoul.",
            subject=HOTELS, host="tripadvisor.co.uk",
        )

        self.assertIn("Anto Hotel Seoul", found)

    def test_korean_prose_yields_nothing_rather_than_guesses(self):
        """A known limit, asserted so it stays a limit and not a surprise.

        Capitalisation is what marks where a name starts and stops, and
        Korean has none -- so a Korean sentence is one long run with no
        boundaries in it. Rather than guess, discovery declines: Korean
        candidates still arrive the way they always have, as results whose
        own URL identifies one record. Extraction is an addition for pages
        that describe several things, not a replacement for that path.
        """
        self.assertEqual(
            ed.names_in("가장 좋은 호텔은 롯데호텔 월드 입니다.",
                        subject="좋은 호텔", host="example.com"),
            (),
        )


class TheCategoryIsNotOneOfItsMembersTests(unittest.TestCase):
    """Every one of these was discovered as a candidate before the rule."""

    def test_a_plural_of_the_thing_asked_for_is_the_category(self):
        for name, subject in (
            ("Custom Mechanical Keyboards", KEYBOARDS),
            ("Mechanic Keyboards", KEYBOARDS),
            ("Seoul Hotels", HOTELS),
            ("Gaming Monitors 2026", MONITORS),
            # Still the category when the request itself was plural.
            ("Gangnam Station Lunch Restaurants", "good restaurants Gangnam"),
        ):
            with self.subTest(name=name):
                self.assertNotIn(
                    name,
                    ed.names_in(f"We rank the {name} of the year.",
                                subject=subject, host="example.com"),
                )

    def test_a_qualifier_on_the_category_is_still_the_category(self):
        for name in ("Curved Gaming Monitor", "Luxury Collection Hotel"):
            with self.subTest(name=name):
                subject = MONITORS if "Monitor" in name else HOTELS
                self.assertNotIn(
                    name,
                    ed.names_in(f"Our pick is the {name}.",
                                subject=subject, host="example.com"),
                )

    def test_a_brand_and_a_category_is_not_a_model(self):
        # "Dell Monitor" names a range, not a thing you can be shown.
        self.assertNotIn(
            "Dell Monitor",
            ed.names_in("The best is the Dell Monitor.",
                        subject=MONITORS, host="example.com"),
        )

    def test_a_specification_is_not_a_name(self):
        # "2K QHD 1440P" is shaped exactly like a model number and belongs
        # to nobody. Measured live: it was discovered as a monitor.
        self.assertEqual(
            ed.names_in("Shop 2K QHD 1440P displays.",
                        subject=MONITORS, host="newegg.com"),
            (),
        )

    def test_the_site_is_not_one_of_the_things_it_lists(self):
        self.assertNotIn(
            "NYT Wirecutter",
            ed.names_in("Reviewed by NYT Wirecutter staff.",
                        subject=KEYBOARDS, host="nytimes.com"),
        )


class NothingIsInventedTests(unittest.TestCase):

    def test_every_name_appears_in_the_evidence(self):
        text = (
            "The best full-size mechanical keyboard we've tested is the "
            "Keychron Q5 Max, and the runner up is the Corsair K70 Max."
        )

        for name in ed.names_in(text, subject=KEYBOARDS, host="rtings.com"):
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_a_run_on_string_is_not_a_name(self):
        # Measured live from a listing page whose markup arrived without
        # spaces: 47 characters of four hotels stuck together.
        found = ed.names_in(
            "Parnas Seoul Myeongdong IIGLAD MapoHotel28 MyeongdongNine Tree "
            "Premier Insadong",
            subject=HOTELS, host="tripadvisor.com",
        )

        for name in found:
            with self.subTest(name=name):
                self.assertLessEqual(len(name), ed.MAX_CHARS)

    def test_nothing_at_all_is_a_fine_answer(self):
        self.assertEqual(ed.names_in("", subject=HOTELS), ())
        self.assertEqual(
            ed.names_in("Find the best deals on travel accommodations.",
                        subject=HOTELS, host="hotelscombined.com"),
            (),
        )


class OneResultAtATimeTests(unittest.TestCase):

    def test_each_name_remembers_where_it_was_read(self):
        found = ed.from_results([
            {
                "title": "The 5 Best Mechanical Keyboards of 2026",
                "url": "https://www.rtings.com/keyboard/reviews/best/mechanical",
                "summary": "The best one we've tested is the Keychron Q5 Max.",
            },
        ], subject=KEYBOARDS)

        self.assertEqual(
            found,
            (("Keychron Q5 Max",
              "https://www.rtings.com/keyboard/reviews/best/mechanical"),),
        )

    def test_the_same_name_from_two_pages_is_kept_once(self):
        page = {
            "title": "Best keyboards",
            "summary": "Our pick: the Keychron Q5 Max.",
        }
        found = ed.from_results([
            dict(page, url="https://a.example.com/best"),
            dict(page, url="https://b.example.com/best"),
        ], subject=KEYBOARDS)

        self.assertEqual(len(found), 1)


class VerifyingWhatWasFoundTests(unittest.TestCase):
    """A discovered name must not be attached to an unrelated address."""

    def test_a_page_about_the_thing_is_accepted(self):
        self.assertTrue(ed.is_about(
            "Keychron Q5 Max",
            "Keychron Q5 Max QMK Wireless Mechanical Keyboard "
            "https://www.keychron.com/products/keychron-q5-max",
        ))

    def test_a_different_thing_is_refused(self):
        self.assertFalse(ed.is_about(
            "Sofitel Ambassador Seoul Hotel",
            "Grand Hyatt Seoul Hotel - book now on Agoda",
        ))

    def test_the_rarest_word_has_to_be_there(self):
        self.assertFalse(ed.is_about(
            "Hotel Inspiroom Jongro",
            "Hotels in Jongro, Seoul - book now",
        ))


if __name__ == "__main__":
    unittest.main()
