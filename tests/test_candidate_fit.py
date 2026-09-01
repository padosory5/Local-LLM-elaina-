"""Checking what came back against what was asked for.

Getting the constraints into the query turned out not to be enough. With an
open recommendation holding ``electric`` and ``~500,000 won``, the query was
right and the top recommendation was this:

    "Yamaha APX500 Acoustic-Electric Guitar"

Nothing had compared the candidate to the constraint, so a result whose
title contains "electric" inside "acoustic-electric" read as a match.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from brain import candidate_fit as cf
from brain import recommendation_state as rs
from brain.grounded_values import unverified_entities
from brain.task_session import TaskSessionStore
from brain.user_locale import UserLocale


def _guitar_problem():
    store = TaskSessionStore()
    for turn in (
        "I'm thinking about getting a guitar.",
        "Electric.",
        "About 500,000 won.",
    ):
        problem = store.note_recommendation_turn(turn, subject="guitar")
    return problem


ACOUSTIC = {
    "title": "Yamaha APX500 Acoustic-Electric Guitar",
    "summary": "Thin-body acoustic-electric, 450,000 won",
}
ELECTRIC = {
    "title": "Cort X250 Electric Guitar",
    "summary": "Electric guitar, 480,000 won",
}
EXPENSIVE = {
    "title": "Gibson Les Paul Standard Electric",
    "summary": "Electric guitar, 3,900,000 won",
}


class HardConflictsTests(unittest.TestCase):

    def test_the_live_failure_is_rejected(self):
        fits = cf.evaluate([ACOUSTIC], _guitar_problem())

        self.assertTrue(fits[0].rejected)
        self.assertIn("electric", fits[0].conflicts)

    def test_naming_the_other_kind_beats_containing_the_right_word(self):
        # "Acoustic-Electric" contains "electric" and is not an electric
        # guitar. The opposing kind is decisive either way.
        fits = cf.evaluate([ACOUSTIC], _guitar_problem())

        self.assertNotIn("electric", fits[0].matches)

    def test_a_real_electric_fits(self):
        fits = cf.evaluate([ELECTRIC], _guitar_problem())

        self.assertFalse(fits[0].rejected)
        self.assertIn("electric", fits[0].matches)

    def test_over_budget_is_a_conflict(self):
        fits = cf.evaluate([EXPENSIVE], _guitar_problem())

        self.assertTrue(fits[0].rejected)

    def test_a_little_over_budget_is_not(self):
        near = {"title": "Cort Electric Guitar", "summary": "560,000 won"}

        fits = cf.evaluate([near], _guitar_problem())

        self.assertFalse(fits[0].rejected)

    def test_budget_range_uses_its_upper_endpoint(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "Studio apartment near UW in Seattle for $1000 to $1300.",
            subject="apartments",
        )

        fits = cf.evaluate([{
            "title": "University District Studio",
            "summary": "Studio apartment, $1,295 monthly rent",
        }], problem)

        self.assertFalse(fits[0].rejected)
        self.assertIn("$1000 to $1300", fits[0].matches)

    def test_room_is_not_an_unchecked_studio(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "Studio apartment near UW in Seattle for $1000 to $1300.",
            subject="apartments",
        )

        fits = cf.evaluate([{
            "title": "Room for rent",
            "summary": "$1,100 monthly rent near UW",
        }], problem)

        self.assertTrue(fits[0].rejected)
        self.assertIn("studio", fits[0].conflicts)

    def test_unconfirmed_housing_type_prevents_confident_ranking(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "Studio apartment near UW in Seattle for $1000 to $1300.",
            subject="apartments", location="Seattle", anchor="UW",
        )
        problem = replace(problem, location="Seattle", anchor="UW")

        fits = cf.evaluate([{
            "title": "Apartment near UW",
            "summary": "$1,195 monthly rent",
        }], problem)

        self.assertIn("studio", cf.unresolved_constraints(fits, problem))
        self.assertFalse(cf.confident(fits, problem))

    def test_plural_rental_search_page_is_a_source_not_a_listing(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "Studio apartment near UW in Seattle for $1000 to $1300.",
            subject="apartments", location="Seattle", anchor="UW",
        )
        problem = replace(problem, location="Seattle", anchor="UW")

        fits = cf.evaluate([{
            "title": "Studio Apartments For Rent in University District Seattle",
            "url": "https://example.com/university-district/studios/",
            "summary": "Seattle studios from $1,100",
        }], problem)

        self.assertEqual(fits[0].verdict, "SOURCE")
        self.assertFalse(fits[0].viable)

    def test_marketplace_home_page_is_a_source_not_a_listing(self):
        store = TaskSessionStore()
        problem = replace(
            store.note_recommendation_turn(
                "Studio apartment for $1000 to $1300.", subject="apartments",
            ),
            location="Seattle",
        )

        fits = cf.evaluate([{
            "title": "Search for Monthly Furnished Rentals | Furnished Finder",
            "url": "https://www.furnishedfinder.com/",
            "summary": "Find apartments near you in Seattle from $1,100",
        }], problem)

        self.assertEqual(fits[0].verdict, "SOURCE")
        self.assertFalse(fits[0].viable)

    def test_explicit_wrong_city_is_a_hard_conflict(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "Studio apartment near UW in Seattle for $1000 to $1300.",
            subject="apartments", location="Seattle", anchor="UW",
        )
        problem = replace(problem, location="Seattle", anchor="UW")

        fits = cf.evaluate([{
            "title": "Pittsburgh Studio",
            "summary": "Studio apartment for $1,200 in Pittsburgh PA",
        }], problem)

        self.assertTrue(fits[0].rejected)
        self.assertIn("location Seattle", fits[0].conflicts)

    def test_named_seattle_studio_with_rent_is_confident(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "Studio apartment near UW in Seattle for $1000 to $1300.",
            subject="apartments", location="Seattle", anchor="UW",
        )
        problem = replace(problem, location="Seattle", anchor="UW")

        fits = cf.evaluate([{
            "title": "DP Studios",
            "summary": "Studio, $1,269 monthly rent, 802 NE 43rd St, Seattle WA",
        }], problem)

        self.assertTrue(cf.confident(fits, problem))


class RankingTests(unittest.TestCase):

    def test_a_mismatch_can_never_rank_first(self):
        fits = cf.evaluate([ACOUSTIC, EXPENSIVE, ELECTRIC], _guitar_problem())

        self.assertEqual(fits[0].name, ELECTRIC["title"])
        self.assertTrue(all(fit.rejected for fit in fits[1:]))

    def test_a_mismatch_is_kept_rather_than_hidden(self):
        # Saying "not that one, it's acoustic" is more use than silently
        # returning fewer results.
        fits = cf.evaluate([ACOUSTIC, ELECTRIC], _guitar_problem())

        self.assertEqual(len(fits), 2)

    def test_the_shortlist_marks_which_is_which(self):
        text = cf.shortlist_text(
            cf.evaluate([ACOUSTIC, ELECTRIC], _guitar_problem()),
        )

        self.assertIn("[FITS]", text)
        self.assertIn("[MISMATCH]", text)

    def test_the_reason_is_stated(self):
        fits = cf.evaluate([ACOUSTIC], _guitar_problem())

        self.assertIn("conflicts with electric", fits[0].because())

    def test_the_log_names_what_was_rejected_and_why(self):
        block = cf.log_block(
            cf.evaluate([ACOUSTIC, ELECTRIC], _guitar_problem()),
            chosen=ELECTRIC["title"], why="fits electric",
        )

        self.assertIn("[Recommendation Reasoning]", block)
        self.assertIn("Rejected:", block)
        self.assertIn("Selected:", block)


class FoodConstraintsTests(unittest.TestCase):
    """The same check, on the dinner problem."""

    def _soft_problem(self):
        store = TaskSessionStore()
        for turn in (
            "What should I eat for dinner?",
            "I have a sore throat and want something easy to eat.",
        ):
            problem = store.note_recommendation_turn(turn, subject="dinner")
        return problem

    def test_a_matching_place_fits(self):
        fits = cf.evaluate(
            [{"title": "Juk Story", "summary": "Easy to eat rice porridge"}],
            self._soft_problem(),
        )

        self.assertFalse(fits[0].rejected)

    def test_an_unknown_field_is_not_a_conflict(self):
        # Not knowing is not the same as contradicting.
        fits = cf.evaluate(
            [{"title": "Some Restaurant", "summary": "A restaurant"}],
            self._soft_problem(),
        )

        self.assertFalse(fits[0].rejected)
        self.assertTrue(fits[0].unknown)


class ClarificationTests(unittest.TestCase):
    """One useful question, never a questionnaire."""

    def test_the_first_question_is_the_kind(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "I'm thinking about getting a guitar.", subject="guitar",
        )

        self.assertEqual(problem.missing_dimension(), rs.TYPE)
        self.assertEqual(problem.question_for(rs.TYPE), "Electric or acoustic?")

    def test_budget_comes_only_after_the_kind_is_settled(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "I'm thinking about getting a guitar.", subject="guitar",
        )
        store.note_dimension_asked(rs.TYPE)
        problem = store.note_recommendation_turn("Electric.", subject="guitar")

        self.assertEqual(problem.missing_dimension(), rs.BUDGET)

    def test_nothing_is_left_to_ask_once_both_are_known(self):
        problem = _guitar_problem()

        self.assertEqual(problem.missing_dimension(), "")

    def test_an_unlisted_thing_is_still_asked_in_general_terms(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "I'm thinking about getting a ukulele.", subject="ukulele",
        )

        self.assertIn("what kind", problem.question_for(rs.TYPE).casefold())

    def test_advice_is_never_interrogated(self):
        # "What should I eat for dinner" is low-stakes. Suggest something.
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "What should I eat for dinner?", subject="dinner",
        )

        self.assertEqual(problem.missing_dimension(), "")

    def test_a_question_is_asked_only_once(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "I'm thinking about getting a guitar.", subject="guitar",
        )
        store.note_dimension_asked(rs.TYPE)
        problem = store.note_recommendation_turn(
            "Hmm.", subject="guitar",
        )

        self.assertNotEqual(problem.missing_dimension(), rs.TYPE)


class LocaleTests(unittest.TestCase):
    """A recommendation is answered in the market they can actually use."""

    def setUp(self):
        self.locale = UserLocale(country="KR", city="Seoul")

    def test_a_plural_noun_localizes(self):
        # The trailing \b closed the whole alternation, so "hotel" matched
        # and "hotels" did not -- which is how a Seoul user asking for
        # easy-to-eat dinner restaurants got one in Nha Trang.
        self.assertIn("Seoul", self.locale.localize_query("hotels"))
        self.assertIn("Seoul", self.locale.localize_query("restaurants"))

    def test_an_infinitive_is_not_a_destination(self):
        # "easy to eat dinner" looks exactly like "flights to Tokyo".
        self.assertIn(
            "Seoul",
            self.locale.localize_query("easy to eat dinner restaurants"),
        )

    def test_a_named_destination_is_still_respected(self):
        for query in (
            "hotels in Hong Kong", "restaurants in Nha Trang",
            "flights to Tokyo",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.locale.localize_query(query), query)

    def test_a_global_question_is_left_alone(self):
        for query in ("latest World Cup winner", "usd to krw exchange rate"):
            with self.subTest(query=query):
                self.assertEqual(self.locale.localize_query(query), query)

    def test_a_purchase_can_ask_for_its_own_market(self):
        query = "electric guitar around 500,000 won"

        self.assertEqual(self.locale.localize_query(query), query)
        self.assertIn(
            "Seoul", self.locale.localize_query(query, assume_local=True),
        )

    def test_a_purchase_problem_knows_it_is_one(self):
        self.assertTrue(_guitar_problem().real_world)


class EntityGroundingTests(unittest.TestCase):
    """Do not send someone to a shop nothing found."""

    def test_an_invented_shop_is_caught(self):
        invented = unverified_entities(
            "You might want to check out local music stores in Seoul like "
            "Melody House or Guitar Center Korea.",
            request="I'm thinking about getting a guitar",
        )

        self.assertIn("Melody House", invented)

    def test_the_convenience_store_is_caught(self):
        # GS25 sells crisps, not guitars.
        invented = unverified_entities(
            "You could try checking local stores like GS25 or Hanaro.",
            request="where can I buy a guitar",
        )

        self.assertIn("GS25", invented)
        self.assertIn("Hanaro", invented)

    def test_naming_a_dish_needs_no_evidence(self):
        self.assertEqual(
            unverified_entities(
                "How about a bowl of bibimbap with grilled beef?",
                request="what should I eat for dinner",
            ),
            (),
        )

    def test_soft_food_advice_needs_no_evidence(self):
        self.assertEqual(
            unverified_entities(
                "You could try soft foods like oatmeal, yogurt, or "
                "scrambled eggs to soothe your throat.",
                request="I have a sore throat",
            ),
            (),
        )

    def test_a_shop_the_search_returned_is_fine(self):
        self.assertEqual(
            unverified_entities(
                "You could try Nakwon Musical Instruments Arcade in Seoul.",
                evidence=(
                    "Nakwon Musical Instruments Arcade is a large music "
                    "market in Seoul"
                ),
                request="where can I buy a guitar",
            ),
            (),
        )

    def test_a_city_is_not_a_business(self):
        self.assertNotIn(
            "Seoul",
            unverified_entities(
                "There are lots of music shops in Seoul.",
                request="where can I buy a guitar",
            ),
        )


class UncheckedIsNotAFitTests(unittest.TestCase):
    """Not knowing is its own answer."""

    def test_a_candidate_matching_nothing_is_unchecked(self):
        # A restaurant listing rarely says "soft" in its title. Calling
        # those a fit is how a sore-throat dinner came back recommending
        # Korean BBQ at a place in Myeongdong.
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "What should I eat for dinner?", subject="dinner",
        )
        problem = store.note_recommendation_turn(
            "I have a sore throat, something soft.", subject="dinner",
        )

        fits = cf.evaluate(
            [{"title": "Best Dinner Restaurants in Seoul", "summary": ""}],
            problem,
        )

        self.assertEqual(fits[0].verdict, "UNCHECKED")

    def test_the_three_verdicts_are_distinct(self):
        problem = _guitar_problem()
        fits = cf.evaluate(
            [ACOUSTIC, ELECTRIC, {"title": "Guitars", "summary": ""}],
            problem,
        )
        verdicts = {fit.name: fit.verdict for fit in fits}

        self.assertEqual(verdicts[ELECTRIC["title"]], "FITS")
        self.assertEqual(verdicts[ACOUSTIC["title"]], "MISMATCH")
        self.assertEqual(verdicts["Guitars"], "UNCHECKED")


class SubjectAndContinuationTests(unittest.TestCase):
    """What the problem is about, when the router's topic is unhelpful."""

    def test_the_named_thing_beats_the_routers_topic(self):
        # Measured live: "I want a guitar" was topic'd "personal desire",
        # and the summary read "electric guitar personal desire around
        # 500,000 won".
        store = TaskSessionStore()
        for turn in ("I want a guitar.", "Electric.", "About 500,000 won."):
            problem = store.note_recommendation_turn(
                turn, subject="personal desire",
            )

        self.assertNotIn("personal desire", problem.search_query())
        self.assertIn("electric", problem.search_query())

    def test_a_bare_quality_continues_the_problem(self):
        store = TaskSessionStore()
        store.note_recommendation_turn("I want a guitar.", subject="guitar")
        problem = store.note_recommendation_turn(
            "Electric.", subject="personal desire",
        )

        self.assertIn("electric", problem.values(rs.ATTRIBUTE))

    def test_a_bare_thing_still_starts_a_new_one(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "I have a sore throat and want something soft.", subject="dinner",
        )
        problem = store.note_recommendation_turn(
            "I want a guitar.", subject="guitar",
        )

        self.assertEqual(problem.values(rs.SITUATION), ())

    def test_a_bare_follow_up_survives_a_flagged_topic_shift(self):
        # Measured live: the router flagged "Find some places." as a topic
        # shift three turns into a sore-throat dinner, the problem
        # restarted, and the search returned travel destinations from
        # Harper's Bazaar. A turn that names no topic cannot be a shift to
        # another one.
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "What should I eat for dinner?", subject="dinner",
        )
        store.note_recommendation_turn(
            "I have a sore throat, something soft.", subject="dinner",
        )
        problem = store.note_recommendation_turn(
            "Find some places.", subject="places", topic_shift=True,
        )

        self.assertIn("soft", problem.values(rs.ATTRIBUTE))
        self.assertIn("sore throat", problem.values(rs.SITUATION))

    def test_a_named_topic_shift_is_still_honoured(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "I have a sore throat, something soft.", subject="dinner",
        )
        problem = store.note_recommendation_turn(
            "I'm thinking about getting a guitar.",
            subject="guitar", topic_shift=True,
        )

        self.assertEqual(problem.values(rs.SITUATION), ())

    def test_where_asks_for_places_not_for_a_kind(self):
        # "Where can I buy a guitar in Seoul?" wants shops, and answering
        # "electric or acoustic?" answers a question nobody asked.
        self.assertTrue(rs.asks_where("Where can I buy a guitar in Seoul?"))
        self.assertFalse(rs.asks_where("I want a guitar."))

    def test_a_buying_verb_names_the_thing(self):
        found = rs.read_constraints("Where can I buy a guitar in Seoul?")

        self.assertEqual(
            [(slot.name, slot.value) for slot in found],
            [(rs.PREFERENCE, "guitar")],
        )

    def test_the_router_flag_is_not_required_to_open_a_problem(self):
        # "I want a guitar." came back as plain conversation with
        # recommendation_needed false, so nothing opened at all.
        self.assertTrue(rs.starts_a_recommendation("I want a guitar."))
        self.assertTrue(rs.starts_a_recommendation("I want Korean BBQ."))
        self.assertFalse(rs.starts_a_recommendation("what is the weather"))


class EntityGroundingIsPhraseLevelTests(unittest.TestCase):

    def test_recombining_grounded_words_is_not_grounding(self):
        # "guitar" from the request plus "center" from anywhere let "Guitar
        # Center" through -- and it has no branch in Seoul.
        invented = unverified_entities(
            "I recommend checking out Guitar Center in Seoul.",
            evidence="Native Instruments software and hardware",
            request="Where can I buy a guitar in Seoul?",
        )

        self.assertIn("Guitar Center", invented)

    def test_a_name_the_search_actually_returned_passes(self):
        self.assertEqual(
            unverified_entities(
                "I recommend checking out Nakwon Musical Instruments Arcade.",
                evidence="Nakwon Musical Instruments Arcade, Seoul",
                request="where can I buy a guitar",
            ),
            (),
        )

    def test_a_sentence_boundary_does_not_join_two_names(self):
        # "... in South Korea. I can look up stores" was read as a business
        # called "South Korea. I".
        self.assertEqual(
            unverified_entities(
                "Let me help you find a guitar in South Korea. I can look "
                "up local stores.",
                request="I want a guitar",
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
