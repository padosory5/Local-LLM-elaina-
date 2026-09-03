"""One question, and only when the answer would change the answer.

B-29, from the second dogfooding session. Three separate flows, all the
same shape:

    User:   Do you have their contact information...
    Elaina: What kind of time did you have in mind?
    User:   anytime.
    Elaina: Got it. What sort of budget are you thinking?

    User:   Also, I'm trying to get some internships in 2027 summer...
    Elaina: What kind of preparation did you have in mind?
    User:   I don't know.
    Elaina: Got it. What sort of budget are you thinking?

    User:   we can get that international driving permit.
    Elaina: What sort of budget are you thinking?

``missing_dimension`` already documents the rule it is meant to follow --
"at most one, and only when the answer would genuinely change which
candidates come back" -- but the gate was ``purchase``, and ``_PURCHASE``
contains "get". "Get an internship", "get a permit" and "get their
contact information" are all acquisitions and none of them is a purchase
with a budget.

So both dimensions need the thing to have a known shape: two obvious
kinds worth splitting on, or a discovery category that has a market. A
question about "what kind of summer" is worse than no question.
"""

import unittest

from brain import recommendation_state as state


def opened(said: str):
    problem = state.start(said, domain=state.domain_for(said))
    return state.update(problem, said)


class NoQuestionWorthAskingTests(unittest.TestCase):

    def test_the_live_flows_ask_nothing(self):
        for said in (
            "Do you have their contact information for the I-20",
            "Where can I get an international driving permit?",
            "we can get that international driving permit",
            "Also, I'm trying to get some internships in 2027 summer",
            "I'm trying to get an internship, when should I start applying",
        ):
            with self.subTest(said=said):
                problem = opened(said)
                self.assertEqual(
                    problem.missing_dimension(), "",
                    f"asked {problem.question_for(problem.missing_dimension())!r}",
                )

    def test_no_question_ever_names_a_word_that_is_not_a_thing(self):
        for said in (
            "Where can I get an international driving permit?",
            "Also, I'm trying to get some internships in 2027 summer",
        ):
            with self.subTest(said=said):
                problem = opened(said)
                question = problem.question_for(problem.missing_dimension())
                for nonsense in ("summer", "permit", "time", "preparation"):
                    self.assertNotIn(nonsense, question)


class QuestionsStillWorthAskingTests(unittest.TestCase):
    """The half that must not be lost."""

    def test_a_thing_with_two_obvious_kinds_is_still_split(self):
        problem = opened("where can I buy a guitar in Seoul")

        self.assertEqual(problem.missing_dimension(), state.TYPE)
        self.assertEqual(problem.question_for(state.TYPE), "Electric or acoustic?")

    def test_housing_is_still_asked_about(self):
        problem = opened("I want to rent a place near UW")

        self.assertEqual(problem.missing_dimension(), state.HOUSING_TYPE)

    def test_budget_still_follows_a_settled_kind(self):
        problem = opened("I want to rent a place near UW")
        problem = state.update(problem, "just like a studio")

        self.assertEqual(problem.missing_dimension(), state.BUDGET)

    def test_a_known_category_is_still_asked_about(self):
        problem = opened("I'm looking for a laptop")

        self.assertEqual(problem.missing_dimension(), state.TYPE)


if __name__ == "__main__":
    unittest.main()


class AnsweringTheQuestionAskedTests(unittest.TestCase):
    """B-30 and B-31: replies that plainly answer, and were not read.

        Elaina: What sort of budget are you thinking?
        User:   1500
        [Router] clarification: The reply did not contain a value for the
                 pending dimension.
        Elaina: What sort of budget are you thinking?
        User:   $1500
        [Recommendation Reasoning] Decision: record budget

        Elaina: What type of housing did you have in mind?
        User:   same as I said.
        Elaina: What type of housing did you have in mind?

    A bare number is not money to the constraint reader, which is right in
    open conversation and wrong as the answer to "what's your budget". And
    re-asking a question verbatim after someone points back at their own
    earlier answer is the worst of both.
    """

    def test_a_bare_number_answers_a_budget_question(self):
        for said, value in (("1500", "1500"), ("about 1500", "1500"),
                            ("1500 or so", "1500"), ("2,000", "2,000")):
            with self.subTest(said=said):
                slot = state.answer_for_dimension(state.BUDGET, said)
                self.assertIsNotNone(slot, said)
                self.assertEqual(slot.value, value)

    def test_the_written_form_still_works(self):
        slot = state.answer_for_dimension(state.BUDGET, "$1500")

        self.assertIsNotNone(slot)
        self.assertIn("1500", slot.value)

    def test_a_bare_number_is_not_a_budget_anywhere_else(self):
        # Only the answer to a budget question. In open conversation "1500"
        # is a number, and reading it as money is how a phone number became
        # a rental budget in session 1.
        self.assertEqual(
            [slot.name for slot in state.read_constraints("1500")], [],
        )
        self.assertIsNone(
            state.answer_for_dimension(state.HOUSING_TYPE, "1500"),
        )

    def test_an_acknowledgement_still_answers_nothing(self):
        for said in ("yeah", "ok", "sure"):
            with self.subTest(said=said):
                self.assertIsNone(
                    state.answer_for_dimension(state.BUDGET, said)
                )

    def test_pointing_back_at_an_earlier_answer_is_recognised(self):
        for said in (
            "same as I said",
            "same as before",
            "the same one",
            "same thing",
            "like I said earlier",
        ):
            with self.subTest(said=said):
                self.assertTrue(state.points_at_an_earlier_answer(said), said)

    def test_an_ordinary_answer_is_not_a_back_reference(self):
        for said in ("a studio", "1500", "electric", "I don't know"):
            with self.subTest(said=said):
                self.assertFalse(state.points_at_an_earlier_answer(said), said)


class ASubjectIsWhatIsBeingAskedForTests(unittest.TestCase):
    """B-43. The condition became the subject.

        User:   What if you have a car? Like, give me like some good places
                to travel along Washington State.
        [Query] text: Washington State cars Seattle
        Elaina: Book a van rental in Seattle early for the best rates!

    "If you have a car" is a condition on the question, not the thing
    being asked about. It supplied the category, the whole utterance
    became the subject, and the query came out about renting vehicles.
    """

    def _opened(self, said: str):
        problem = state.start(said, domain=state.domain_for(said))
        return state.update(problem, said)

    def test_the_query_is_about_the_thing_asked_for(self):
        query = self._opened(
            "What if you have a car? Like, give me like some good places "
            "to travel along Washington State."
        ).search_query().casefold()

        self.assertIn("places", query)
        self.assertIn("washington", query)

    def test_the_subject_is_a_phrase(self):
        problem = self._opened(
            "What if you have a car? Like, give me like some good places "
            "to travel along Washington State."
        )

        self.assertLessEqual(len(problem.subject.split()), 10, problem.subject)
        self.assertNotIn("what if", problem.subject.casefold())

    def test_an_ordinary_short_request_is_untouched(self):
        for said in (
            "where can I buy a guitar in Seoul",
            "I want to rent a place near UW",
            "good restaurants in Seattle",
        ):
            with self.subTest(said=said):
                problem = self._opened(said)
                self.assertTrue(problem.subject)
                self.assertLessEqual(len(problem.subject.split()), 10)
