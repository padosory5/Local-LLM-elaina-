"""Session 4: what the turn says, and what she does with it.

Five failures, three root causes.

**The clock's arithmetic was left to the model** (S4-01):

    Elaina: It's 1:20 AM in Seattle right now. The time there is 13 hours
            behind Korea Standard Time.

The local time was right -- B-57's routing fix held and no search ran --
but Seattle is sixteen hours behind Korea on 3 September, not thirteen.
The context handed over two clocks and no relationship between them, so
the model worked the difference out itself and got it wrong. That is
arithmetic, and arithmetic is not the model's job here.

**An explicitly named place and entity were dropped from queries**
(S4-02, S4-03), by two different mechanisms, both of them "held state
beats the current turn":

    [Query] studio apartments University of Washington $1,500 in South Korea
    [Query] I-20 form processing in South Korea

The first already had a place in it and the locale appended another. The
second lost "University of Washington" entirely, because the problem's
held subject outranked the request that named it.

**A bare definite reference went unresolved** (S4-04):

    User:   Yeah, use browser control and then open the website.
    Elaina: Clicked 'Example Domain Example Domain

"The website" points at the university office site under discussion. The
raw utterance went to the planner as its target, so it searched blind.

**And a number the user had just given came back changed** (S4-05):

    User:   My budget is 1500. Repeat that back to me.
    Elaina: Your budget is 150.
"""

import re
import unittest


class TheClockDoesItsOwnArithmeticTests(unittest.TestCase):
    """S4-01."""

    def _context(self, question="What time is it in Seattle right now?"):
        from tests.turn_harness import build_engine

        return build_engine().build_time_context(question)

    def test_the_difference_is_stated_not_left_to_be_worked_out(self):
        self.assertRegex(self._context(), r"\d+ hours? (?:behind|ahead of)")

    def test_the_difference_is_the_real_one(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        here = datetime.now().astimezone().utcoffset().total_seconds()
        there = datetime.now(
            ZoneInfo("America/Los_Angeles"),
        ).utcoffset().total_seconds()
        expected = int(abs(here - there) // 3600)

        stated = re.search(r"(\d+) hours? (?:behind|ahead of)", self._context())

        self.assertIsNotNone(stated)
        self.assertEqual(int(stated.group(1)), expected)

    def test_the_direction_is_right(self):
        # Seattle is behind Korea, never ahead of it.
        self.assertIn("behind", self._context())

    def test_a_question_naming_nowhere_says_nothing_about_offsets(self):
        self.assertNotRegex(
            self._context("what time is it"), r"hours? (?:behind|ahead)",
        )


class AnExplicitPlaceIsNotOverriddenTests(unittest.TestCase):
    """S4-02. The query already said where."""

    def _query_for(self, problem):
        from tests.turn_harness import build_engine

        return build_engine()._localised(problem, problem.search_query())

    def test_a_query_the_problem_placed_is_not_localised(self):
        from brain import recommendation_state as state

        problem = state.start("apartments", domain="apartments")
        problem = state.update(
            problem,
            "a studio near UW for $1500",
            anchor="University of Washington",
        )

        query = self._query_for(problem)

        self.assertNotIn("South Korea", query)
        self.assertIn("Washington", query)

    def test_a_placeless_query_is_still_localised(self):
        from brain import recommendation_state as state

        problem = state.start("packing peanuts")
        problem = state.update(problem, "where can I buy packing peanuts")

        self.assertIn("South Korea", self._query_for(problem))


class TheNamedEntitySurvivesTests(unittest.TestCase):
    """S4-03. "University of Washington" was in the request, not the query."""

    def test_a_name_the_request_introduces_reaches_the_query(self):
        from brain import recommendation_state as state

        problem = state.start("I-20 form processing")
        problem = state.update(problem, "how long does I-20 processing take")

        query = problem.search_query(
            "Find contact information for the University of Washington "
            "regarding I-20 verification"
        )

        self.assertIn("University of Washington", query)

    def test_an_ordinary_follow_up_adds_no_noise(self):
        from brain import recommendation_state as state

        problem = state.start("packing peanuts")
        problem = state.update(problem, "where can I buy packing peanuts")

        query = problem.search_query("where can I get some")

        self.assertNotIn("University", query)
        self.assertIn("packing peanuts", query.casefold())


class ABareDefiniteReferenceIsResolvedTests(unittest.TestCase):
    """S4-04. "the website" pointed at something she had just named."""

    SAID_BEFORE = (
        "The University of Washington International Student Services office "
        "handles I-20 questions."
    )

    def _resolve(self, said, before=None):
        from brain import browser_progress

        return browser_progress.resolve_named_choice(
            said, said_before=self.SAID_BEFORE if before is None else before,
        )

    def test_the_live_turn_resolves(self):
        chosen = self._resolve(
            "Yeah, use browser control and then open the website.",
        )

        self.assertIn("Washington", chosen)

    def test_other_bare_definites_resolve_too(self):
        for said in ("open the site", "open that page", "go to the website"):
            with self.subTest(said=said):
                self.assertTrue(self._resolve(said), said)

    def test_a_turn_naming_its_own_site_is_untouched(self):
        self.assertEqual(self._resolve("open zillow.com"), "")

    def test_nothing_named_before_resolves_to_nothing(self):
        self.assertEqual(
            self._resolve(
                "open the website", before="Sure, I can help with that.",
            ),
            "",
        )


class ANumberTheUserGaveIsNotMutatedTests(unittest.TestCase):
    """S4-05."""

    def _engine(self):
        from tests.turn_harness import build_engine

        return build_engine()

    def test_the_live_mutation_is_caught(self):
        corrected = self._engine()._enforce_grounded_values(
            "Your budget is 150.",
            user_input="My budget is 1500. Repeat that back to me.",
            action_performed=False,
        )

        self.assertNotIn("150.", corrected)

    def test_repeating_it_correctly_is_allowed(self):
        answer = "Your budget is 1500."

        self.assertEqual(
            self._engine()._enforce_grounded_values(
                answer,
                user_input="My budget is 1500. Repeat that back to me.",
                action_performed=False,
            ),
            answer,
        )

    def test_casual_general_knowledge_is_still_left_alone(self):
        # The exemption this guard has always had: a turn where the user
        # stated no value of their own is ordinary conversation.
        answer = "A coffee in Seoul is about 5,000 won."

        self.assertEqual(
            self._engine()._enforce_grounded_values(
                answer,
                user_input="how much is coffee there",
                action_performed=False,
            ),
            answer,
        )


if __name__ == "__main__":
    unittest.main()
