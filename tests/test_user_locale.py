import unittest

from brain.user_locale import UserLocale


class UserLocaleBasicsTests(unittest.TestCase):
    def test_a_known_country_resolves_language_currency_and_sites(self):
        locale = UserLocale(country="KR", city="Seoul")

        self.assertEqual(locale.country_code, "KR")
        self.assertEqual(locale.language, "ko")
        self.assertEqual(locale.context.currency, "KRW")
        self.assertEqual(locale.context.home, "Seoul, South Korea")
        self.assertIn("당근마켓", locale.sites_for("secondhand"))

    def test_an_unknown_country_never_raises_and_keeps_a_usable_default(self):
        locale = UserLocale(country="ZZ")

        self.assertEqual(locale.country_code, "ZZ")
        self.assertEqual(locale.language, "en")
        self.assertEqual(locale.sites_for("secondhand"), ())

    def test_config_preferred_sites_override_the_built_in_table(self):
        locale = UserLocale(
            country="KR",
            preferred_sites={"secondhand": ["번개장터"], "boats": "SailNow, BoatMart"},
        )

        self.assertEqual(locale.sites_for("secondhand"), ("번개장터",))
        self.assertEqual(locale.sites_for("boats"), ("SailNow", "BoatMart"))
        # Categories the override doesn't mention are untouched.
        self.assertIn("야놀자", locale.sites_for("hotel"))

    def test_default_sources_have_execution_host_scope(self):
        locale = UserLocale(country="KR", city="Seoul")
        self.assertEqual(
            locale.source_hosts_for_goal("secondhand", "find a used RTX 5080"),
            ("daangn.com", "bunjang.co.kr", "joongna.com"),
        )

    def test_custom_source_names_do_not_get_guessed_hostnames(self):
        locale = UserLocale(
            country="KR", preferred_sites={"secondhand": ["My Local Market"]},
        )
        self.assertEqual(
            locale.source_hosts_for_goal("secondhand", "find a used phone"),
            (),
        )


class MarketResolutionTests(unittest.TestCase):
    """Which country's market a request is about -- the thing that decides
    whether a Korean user gets 야놀자 or nothing at all."""

    def setUp(self):
        self.korea = UserLocale(country="KR", city="Seoul")
        self.states = UserLocale(country="US")

    def test_a_request_naming_no_place_uses_the_users_own_market(self):
        self.assertEqual(self.korea.market_for("best second-hand sites"), "KR")
        self.assertEqual(self.states.market_for("best second-hand sites"), "US")

    def test_a_city_abroad_selects_that_citys_market_not_the_users(self):
        # The user's own country is irrelevant when they ask about Tokyo.
        self.assertEqual(self.korea.market_for("hotels in Tokyo"), "JP")
        self.assertEqual(self.states.market_for("restaurants in Seoul"), "KR")

    def test_guam_uses_us_hotel_sources(self):
        self.assertEqual(self.korea.market_for("book a hotel in Guam"), "US")
        self.assertEqual(
            self.korea.source_hosts_for_goal("hotel", "book a hotel in Guam")[0],
            "booking.com",
        )

    def test_a_place_name_hidden_inside_a_word_is_not_a_destination(self):
        # Found live: a bare substring scan read "us" out of "used" and
        # answered "best second hand websites to buy a used phone" with
        # Craigslist -- for a user in Korea.
        self.assertEqual(
            self.korea.market_for("best second hand websites to buy a used phone"),
            "KR",
        )
        self.assertEqual(self.korea.market_for("find us a hotel"), "KR")
        self.assertEqual(
            self.korea.sites_for_goal("secondhand", "buy a used phone")[0][0],
            "당근마켓",
        )

    def test_an_unknown_destination_yields_no_sites_rather_than_wrong_ones(self):
        # Hong Kong has no site table, so the honest result is silence --
        # suggesting 야놀자 for a Hong Kong hotel would be worse than a
        # plain search.
        sites, market = self.korea.sites_for_goal(
            "hotel", "find a good hotel near the city in Hong Kong",
        )

        self.assertEqual(sites, ())
        self.assertEqual(market, "")
        self.assertEqual(self.korea.site_guidance("hotel", goal="hotels in Hong Kong"), "")

    def test_site_guidance_names_the_destinations_own_sites(self):
        guidance = self.states.site_guidance("restaurant", goal="best restaurants in Seoul")

        self.assertIn("네이버 지도", guidance)
        self.assertIn("South Korea", guidance)

    def test_site_guidance_at_home_names_the_home_market(self):
        guidance = self.korea.site_guidance("secondhand", goal="best second-hand sites")

        self.assertIn("당근마켓", guidance)
        self.assertIn("South Korea", guidance)


class QueryLocalizationTests(unittest.TestCase):
    def test_a_query_that_already_names_a_place_is_left_exactly_alone(self):
        locale = UserLocale(country="KR")

        # Adding "in South Korea" here would make the results wrong, not
        # more local.
        self.assertEqual(
            locale.localize_query("hotel prices in Hong Kong"),
            "hotel prices in Hong Kong",
        )

    def test_a_placeless_query_is_pinned_to_the_users_market(self):
        locale = UserLocale(country="KR", city="Seoul")

        self.assertEqual(
            locale.localize_query("second-hand phone marketplaces"),
            "second-hand phone marketplaces in Seoul",
        )

    def test_an_english_default_market_leaves_queries_untouched(self):
        locale = UserLocale(country="US")

        self.assertEqual(
            locale.localize_query("second-hand phone marketplaces"),
            "second-hand phone marketplaces",
        )

    def test_an_empty_query_stays_empty(self):
        self.assertEqual(UserLocale(country="KR").localize_query("   "), "")


class LocaleContextTextTests(unittest.TestCase):
    def test_the_prompt_block_states_the_country_and_currency(self):
        text = UserLocale(country="KR", city="Seoul").context_text()

        self.assertIn("Seoul, South Korea", text)
        self.assertIn("KRW", text)
        self.assertIn("destination", text)


if __name__ == "__main__":
    unittest.main()
