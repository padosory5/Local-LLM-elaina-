"""The discovery offer has to name the user's own market's sites.

"Best second-hand websites" answered with Craigslist, for a user in Korea,
is not a worse answer -- it is a useless one. These pin the offer text to
the market the goal is actually about, and pin it short enough to say
aloud.
"""

import unittest

from brain.task_discovery_policy import TaskDiscoveryPolicy
from brain.user_locale import UserLocale


class LocalisedDiscoveryOfferTests(unittest.TestCase):
    def setUp(self):
        self.korea = UserLocale(country="KR", city="Seoul")
        self.states = UserLocale(country="US")

    def _offer(self, goal, locale, *, browser_ready=True):
        advice = TaskDiscoveryPolicy.advise(
            goal, browser_ready=browser_ready, locale=locale,
        )
        return advice.offer_text if advice is not None else ""

    def test_the_offer_names_the_users_own_marketplaces(self):
        offer = self._offer(
            "what are the best second hand websites to buy a used phone",
            self.korea,
        )

        self.assertIn("당근마켓", offer)
        self.assertNotIn("Craigslist", offer)

    def test_the_same_request_names_different_sites_elsewhere(self):
        offer = self._offer(
            "what are the best second hand websites to buy a used phone",
            self.states,
        )

        self.assertIn("Facebook Marketplace", offer)
        self.assertNotIn("당근마켓", offer)

    def test_a_destination_abroad_uses_that_destinations_market(self):
        offer = self._offer("best restaurants to go in Seoul", self.states)

        self.assertIn("네이버 지도", offer)

    def test_guam_hotel_research_uses_global_us_booking_sources(self):
        offer = self._offer("book me a hotel in Guam", self.korea)
        self.assertIn("Booking.com", offer)
        self.assertIn("What dates", offer)

    def test_an_unknown_market_falls_back_without_naming_wrong_sites(self):
        offer = self._offer(
            "find a good hotel near the city in Hong Kong", self.korea,
        )

        self.assertNotIn("야놀자", offer)
        self.assertIn("overview", offer)

    def test_no_locale_at_all_still_produces_a_usable_offer(self):
        offer = self._offer("find a good hotel to stay in", None)

        self.assertIn("overview", offer)

    def test_every_offer_is_short_enough_to_say_aloud(self):
        for goal, locale in (
            ("what are the best second hand websites to buy a used phone", self.korea),
            ("find a good hotel near the city in Hong Kong", self.korea),
            ("best restaurants to go in Seoul", self.states),
        ):
            with self.subTest(goal=goal):
                self.assertLessEqual(len(self._offer(goal, locale).split()), 30)

    def test_control_mode_off_says_so_instead_of_offering_live_research(self):
        offer = self._offer(
            "best second hand sites to buy a used phone",
            self.korea,
            browser_ready=False,
        )

        self.assertIn("Desktop Control Mode is off", offer)


class ShortHintTests(unittest.TestCase):
    def test_only_the_first_couple_of_examples_are_spoken(self):
        self.assertEqual(
            TaskDiscoveryPolicy._short_hint("dates, area, nightly budget, or guest count"),
            "Dates or area",
        )

    def test_an_empty_hint_degrades_gracefully(self):
        self.assertEqual(TaskDiscoveryPolicy._short_hint(""), "A preference")


if __name__ == "__main__":
    unittest.main()
