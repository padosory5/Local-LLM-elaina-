"""One task's constraints must not steer the next one's search.

Ten of the twenty-six issues from the first dogfooding session are this
one fault wearing different clothes. A rental problem opened early in the
conversation stayed open for the rest of it, and every later turn that
happened to contain a number, a date or a noun was folded into it:

    Constraints: housing_type=studio, budget=206-221, exclusion=Zillow,
                 area=September 13th
    [Query] text: studio apartments September 13th 206-221 in South Korea

That query was built to answer "can you give me the University of
Washington's contact information". The budget is half a phone number.

The failures are separable and each is tested on its own:

* a bare number range is not money when it is one link of a longer chain;
* an infinitive is not a thing that was asked for;
* a turn refines the open problem only when the constraint is essentially
  all it says -- otherwise it is a different subject;
* "No, ..." followed by a clause is a contradiction, not a new topic;
* a proper name does not end at its first lowercase word.
"""

import unittest

from brain import recommendation_state as state
from brain.conversation_focus import read_background, read_correction


def names(text: str) -> dict[str, str]:
    return {slot.name: slot.value for slot in state.read_constraints(text)}


class MoneyIsNotEveryNumberTests(unittest.TestCase):
    """B-07. "206-221-7857" became budget=206-221."""

    def test_a_phone_number_is_not_a_budget(self):
        said = "Okay, I searched it up and the phone number is 206-221-7857. You are wrong."

        self.assertNotIn(state.BUDGET, names(said))

    def test_a_longer_digit_chain_is_never_money(self):
        for said in (
            "call 206-221-7857",
            "my number is 010-1234-5678",
            "the code is 12-34-56",
        ):
            with self.subTest(said=said):
                self.assertNotIn(state.BUDGET, names(said))

    def test_real_budgets_are_untouched(self):
        for said, budget in (
            ("from $1000 to $1500", "$1000 to $1500"),
            ("under 500,000 won", "500,000 won"),
            ("500,000 to 800,000 won", "500,000 to 800,000 won"),
            ("around 1000-1500", "1000-1500"),
            ("2000-3000 dollars", "2000-3000 dollars"),
            ("my budget is 1000 to 1500", "1000 to 1500"),
        ):
            with self.subTest(said=said):
                self.assertEqual(names(said).get(state.BUDGET), budget)


class AnInfinitiveIsNotAThingTests(unittest.TestCase):
    """B-04. "I just want to talk about you" -> preference="to talk about you"."""

    def test_wanting_to_do_something_names_no_thing(self):
        said = (
            "No, I just want to talk about you, my rents and stuff. So on "
            "September 13th, I'm moving to University of Washington, "
            "Seattle, and then I still haven't found a rent."
        )

        self.assertNotIn(state.PREFERENCE, names(said))

    def test_an_infinitive_with_no_object_names_nothing(self):
        for said in (
            "I want to talk about you",
            "I want to think about it",
        ):
            with self.subTest(said=said):
                self.assertNotIn(state.PREFERENCE, names(said))

    def test_the_noun_inside_an_infinitive_is_the_thing(self):
        # "to get an internship in summer 2027" is not a searchable phrase;
        # "internship" is. The infinitive is grammar around the thing.
        self.assertEqual(
            names("I want to get an internship in summer 2027")
            .get(state.PREFERENCE),
            "internship",
        )

    def test_wanting_a_thing_still_names_it(self):
        for said, thing in (
            ("I want a guitar", "guitar"),
            ("I'm looking for a mechanical keyboard", "mechanical keyboard"),
            ("where can I buy a guitar in Seoul", "guitar"),
            ("I want to get a guitar", "guitar"),
        ):
            with self.subTest(said=said):
                self.assertEqual(names(said).get(state.PREFERENCE), thing)


class ARefinementIsMostlyTheConstraintTests(unittest.TestCase):
    """B-05, B-07, B-13, B-23. "if incoming: return True" was the hole.

    Any turn carrying a budget, an area or a quality was folded into the
    open problem, whatever else it said. A whole sentence about a phone
    number contains digits, so it refined a rental search.
    """

    def _rental(self) -> state.RecommendationProblem:
        problem = state.start("apartments", domain="apartments")
        return state.update(problem, "just like a studio, $1000 to $1500")

    def test_a_sentence_about_something_else_starts_a_new_problem(self):
        for said in (
            "Okay, I searched it up and the phone number is 206-221-7857. You are wrong.",
            "I want to get an internship in summer 2027. When should I start applying?",
            "Also, I'm packing up my PC and shipping it to Seattle from Korea.",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    state.about_the_same_thing(self._rental(), said),
                    "an unrelated sentence was folded into the open task",
                )

    def test_a_turn_that_is_only_a_constraint_still_refines(self):
        for said in (
            "just like a studio",
            "from $1000 to $1500",
            "under 500,000 won",
            "somewhere near Gangnam",
            "a one-bedroom",
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    state.about_the_same_thing(self._rental(), said),
                    "a plain answer was treated as a change of subject",
                )

    def test_a_constraint_about_the_known_thing_still_refines(self):
        problem = state.start("guitar")
        problem = state.update(problem, "I want a guitar")

        self.assertTrue(
            state.about_the_same_thing(
                problem, "make it under $500 for the guitar",
            )
        )

    def test_the_stale_contact_query_is_not_built_at_all(self):
        # The end-to-end shape of B-05: the turn that asked for a phone
        # number must not inherit studio/budget/Zillow.
        problem = self._rental()
        said = "Can you give me the contact information?"

        if state.about_the_same_thing(problem, said):
            # Continuing is defensible for a bare follow-up; inventing a
            # rental query for it is not.
            query = state.update(problem, said).search_query(said)
            self.assertNotIn("206", query)


class ContradictionIsNotANewSubjectTests(unittest.TestCase):
    """B-09. "No, I can see the images. Thank you." became the subject."""

    def test_a_clause_after_no_is_not_a_correction(self):
        for said in (
            "No, I can see the images. Thank you.",
            "No, that isn't what happened.",
            "No, you are wrong.",
            "No, it doesn't work like that.",
        ):
            with self.subTest(said=said):
                self.assertEqual(read_correction(said), "")

    def test_naming_the_thing_after_no_still_corrects(self):
        for said, meant in (
            ("No, the blue one.", "blue one"),
            ("No, Zillow.", "Zillow"),
            ("No, Korean barbecue.", "Korean barbecue"),
        ):
            with self.subTest(said=said):
                self.assertEqual(read_correction(said), meant)

    def test_the_explicit_corrections_are_untouched(self):
        for said, meant in (
            ("No, I meant Zillow", "Zillow"),
            ("I mean the second one", "second one"),
            ("I'm talking about the hotel", "hotel"),
        ):
            with self.subTest(said=said):
                self.assertEqual(read_correction(said), meant)


class AProperNameSurvivesItsLowercaseWordsTests(unittest.TestCase):
    """B-01. "moving to University of Washington, Seattle" -> "University"."""

    def test_the_whole_school_and_its_city_are_kept(self):
        said = (
            "So on September 13th, I'm moving to University of Washington, "
            "Seattle, and then I still haven't found a rent."
        )

        self.assertEqual(
            read_background(said).get("location"),
            "University of Washington, Seattle",
        )

    def test_a_plain_city_is_unchanged(self):
        self.assertEqual(
            read_background("I'm moving to Seattle.").get("location"),
            "Seattle",
        )

    def test_a_lowercase_sentence_continuation_is_not_swallowed(self):
        # The direction that would be worse than truncating: a name that
        # runs on into the rest of the sentence.
        found = read_background(
            "I'm moving to Seattle and then I still haven't found a rent."
        )

        self.assertEqual(found.get("location"), "Seattle")


class TheMarketDoesNotOverrideANamedPlaceTests(unittest.TestCase):
    """The other half of B-01: "... University in South Korea".

    Conversation context is appended to a query bare, with no preposition
    in front of it, and the placeless test only looked for prepositional
    phrases. So a query naming a university in Seattle read as placeless
    and had the user's home market added to it.
    """

    def _locale(self):
        from brain.user_locale import UserLocale

        return UserLocale()

    def test_a_named_city_keeps_the_market_out(self):
        query = "studio apartments $1000 to $1500 University of Washington, Seattle"

        self.assertEqual(
            self._locale().localize_query(
                query, category="realestate", assume_local=True,
            ),
            query,
        )

    def test_a_placeless_query_is_still_localized(self):
        self.assertIn(
            "South Korea",
            self._locale().localize_query(
                "packing peanuts", category="", assume_local=True,
            ),
        )

    def test_an_infinitive_is_still_not_a_destination(self):
        # The false positive this test was originally written against: a
        # Seoul user asking for easy-to-eat dinner got Nha Trang.
        self.assertIn(
            "South Korea",
            self._locale().localize_query(
                "soft easy to eat dinner restaurants",
                category="restaurant", assume_local=True,
            ),
        )


class ASentenceIsNotAThingTests(unittest.TestCase):
    """B-12, B-13. "What kind of open did you have in mind?"

    ``_thing()`` takes the last word of the subject. When the subject is a
    whole utterance that word is whatever the sentence ended on.
    """

    def test_no_dimension_is_asked_about_a_sentence(self):
        problem = state.start(
            "Okay, thank you. Also, I want to get an internship in summer "
            "2027. When should I start applying? And then do you know when "
            "applications are open"
        )
        problem = state.update(problem, "when do applications open", subject="")

        self.assertEqual(problem.missing_dimension(), "")

    def test_a_real_subject_is_still_asked_about(self):
        problem = state.start("guitar")
        problem = state.update(problem, "I want to buy a guitar")

        self.assertEqual(problem.missing_dimension(), state.TYPE)


if __name__ == "__main__":
    unittest.main()
