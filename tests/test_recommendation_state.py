"""A recommendation is a problem that stays open, not a shape of answer.

The three turns that made this necessary, measured live:

    "What should I eat for dinner?"          -> Korean BBQ
    "I have a sore throat, something easy."  -> soft ramen or bibimbap
    "Pull up some spots for me."             -> Korean BBQ again

The third turn routed correctly and searched with none of what the second
had established. Every test below is about what has to survive from one
turn to the next, and -- just as important -- what must not.
"""

from __future__ import annotations

import unittest

from brain import recommendation_state as rs
from brain.deliberation.goal import (
    SOURCE_ASKED,
    SOURCE_RESEARCH,
    SOURCE_UTTERANCE,
)
from brain.task_session import TaskSessionStore


def _run(turns, *, subject=""):
    problem = rs.start(
        subject or turns[0], domain=rs.domain_for(turns[0]),
    )
    for turn in turns:
        text, said_about = turn if isinstance(turn, tuple) else (turn, subject)
        problem = rs.update(problem, text, subject=said_about)
    return problem


class ConstraintsAreReadFromTheWordsTests(unittest.TestCase):

    def test_a_situation_is_kept_verbatim(self):
        # The architecture carries "sore throat" into the reasoning. What a
        # sore throat implies is not its business.
        found = rs.read_constraints("I have a sore throat.")

        self.assertEqual(
            [(s.name, s.value) for s in found], [(rs.SITUATION, "sore throat")],
        )

    def test_a_situation_keeps_its_verb(self):
        found = rs.read_constraints("Actually my throat hurts.")

        self.assertEqual(found[0].value, "throat hurts")

    def test_something_x_is_a_quality_not_a_thing(self):
        found = rs.read_constraints("I want something soft.")

        self.assertEqual(found[0].name, rs.ATTRIBUTE)
        self.assertEqual(found[0].value, "soft")

    def test_a_named_thing_is_a_preference(self):
        found = rs.read_constraints("I want Korean BBQ.")

        self.assertEqual(found[0].name, rs.PREFERENCE)
        self.assertEqual(found[0].value, "Korean BBQ")

    def test_a_qualifier_survives_whole(self):
        # Two patterns match this sentence and cut it in different places.
        # "easy" alone would be a weaker constraint than "easy to eat".
        found = rs.read_constraints(
            "I have a sore throat and want something easy to eat.",
        )
        values = [s.value for s in found if s.name == rs.ATTRIBUTE]

        self.assertEqual(values, ["easy to eat"])

    def test_a_bare_amount_is_a_budget(self):
        # The existing budget reader wants "under"/"up to" in front of it.
        found = rs.read_constraints("About 500,000 won.")

        self.assertEqual(found[0].name, rs.BUDGET)

    def test_an_unrecognised_sentence_contributes_nothing(self):
        # Deliberately incomplete rather than speculative.
        self.assertEqual(rs.read_constraints("Hmm, I'm not sure yet."), ())


class ProvenanceIsKeptTests(unittest.TestCase):

    def test_what_the_person_said_is_marked_as_said(self):
        found = rs.read_constraints("I want something soft.")

        self.assertEqual(found[0].source, SOURCE_UTTERANCE)

    def test_an_answer_to_a_question_is_marked_as_asked(self):
        found = rs.read_short_reply("Electric.")

        self.assertEqual(found[0].source, SOURCE_ASKED)

    def test_research_is_a_source_of_its_own(self):
        found = rs.read_constraints(
            "I want something soft.", source=SOURCE_RESEARCH,
        )

        self.assertEqual(found[0].source, SOURCE_RESEARCH)
        self.assertTrue(found[0].is_assumed is False)

    def test_an_instruction_is_not_an_answer(self):
        # "Show me some" is three words and a request. Read as a constraint
        # it went straight into the search query.
        self.assertEqual(rs.read_short_reply("Show me some"), ())
        self.assertEqual(rs.read_short_reply("Pull up some"), ())


class ConstraintsAccumulateTests(unittest.TestCase):

    def test_the_second_turn_adds_to_the_first(self):
        problem = _run([
            ("What should I eat for dinner?", "dinner"),
            ("I have a sore throat and want something easy to eat.", "dinner"),
        ])

        self.assertIn("easy to eat", problem.values(rs.ATTRIBUTE))
        self.assertIn("sore throat", problem.values(rs.SITUATION))

    def test_qualities_stack_but_a_budget_replaces(self):
        problem = _run([
            ("I want something soft.", "food"),
            ("something mild too", "food"),
            ("under 20,000 won", "food"),
            ("actually up to 30,000 won", "food"),
        ])

        self.assertEqual(len(problem.values(rs.BUDGET)), 1)
        self.assertIn("mild", problem.values(rs.ATTRIBUTE))
        self.assertIn("soft", problem.values(rs.ATTRIBUTE))


class SupersessionTests(unittest.TestCase):
    """What was asked for can stop being what is wanted."""

    def test_a_revision_retires_the_earlier_preference(self):
        problem = _run([
            ("I want Korean BBQ.", "Korean BBQ"),
            ("Actually my throat hurts, something soft.", "Korean BBQ"),
        ])

        self.assertIn("Korean BBQ", problem.retired_values)
        self.assertNotIn("Korean BBQ", problem.values(rs.PREFERENCE))

    def test_a_new_situation_alone_supersedes(self):
        # No "actually" needed: learning the person has a sore throat is
        # itself reason to stop standing behind the barbecue.
        problem = _run([
            ("I want Korean BBQ.", "Korean BBQ"),
            ("I have a sore throat.", "Korean BBQ"),
        ])

        self.assertIn("Korean BBQ", problem.retired_values)

    def test_the_situation_survives_a_later_revision(self):
        # A sore throat does not stop being true because they changed their
        # mind about dinner.
        problem = _run([
            ("I have a sore throat.", "dinner"),
            ("I want Korean BBQ.", "dinner"),
            ("Actually, something else.", "dinner"),
        ])

        self.assertIn("sore throat", problem.values(rs.SITUATION))

    def test_a_retired_value_cannot_reach_the_query(self):
        # The router still sees the whole history, so its topic on the next
        # turn can come back as the very thing that was just retired.
        problem = _run([
            ("I want Korean BBQ.", "Korean BBQ"),
            ("Actually my throat hurts, something soft.", "Korean BBQ"),
        ])

        query = problem.search_query("Korean BBQ places")

        self.assertNotIn("bbq", query.casefold())
        self.assertIn("soft", query)


class QueryConstructionTests(unittest.TestCase):
    """What is actually sent, given everything established."""

    def test_the_dinner_query_carries_the_constraints(self):
        problem = _run([
            ("What should I eat for dinner?", "dinner"),
            ("I have a sore throat and want something easy to eat.", "dinner"),
            ("Pull up some spots for me.", "dinner spots"),
        ])

        query = problem.search_query("pull up some spots for me")

        self.assertIn("easy to eat", query)
        self.assertIn("dinner", query)

    def test_the_request_wrapper_is_not_the_query(self):
        problem = _run([
            ("I want Korean BBQ.", "Korean BBQ"),
            ("Actually my throat hurts, something soft.", "Korean BBQ"),
        ])

        query = problem.search_query("show me some places")

        for word in ("show", "me", "some"):
            self.assertNotIn(word, query.split())

    def test_the_guitar_query_carries_type_and_budget(self):
        problem = _run([
            ("I'm thinking about getting a guitar.", "guitar"),
            ("Electric.", "guitar"),
            ("About 500,000 won.", "guitar"),
            ("Show me some.", "electric guitar"),
        ])

        query = problem.search_query("show me some").casefold()

        self.assertIn("electric", query)
        self.assertIn("guitar", query)
        self.assertIn("500,000", query)

    def test_the_housing_type_survives_the_wrong_domain(self):
        # Session 5. "a studio near the University of Washington" was
        # classified `hotel` rather than `apartments`, so it took the
        # general branch -- where housing type was not read at all -- and
        # searched "accommodation University of Washington". The one word
        # the person had specified was the one word dropped. A constraint
        # they gave may not vanish because a classifier chose a bucket.
        problem = rs.update(
            rs.start("accommodation", domain="hotel"),
            "Can you find me a studio near the University of Washington "
            "with a budget?",
        )

        query = problem.search_query(
            "Find a studio near the University of Washington",
        ).casefold()

        self.assertIn("studio", query)
        self.assertIn("university of washington", query)

    def test_a_situation_stays_out_of_the_search_box(self):
        # "sore throat restaurants" finds clinics.
        problem = _run([
            ("I have a sore throat and want something easy to eat.", "dinner"),
        ])

        self.assertNotIn("sore throat", problem.search_query("dinner"))

    def test_the_situation_still_reaches_the_reasoning(self):
        problem = _run([
            ("I have a sore throat and want something easy to eat.", "dinner"),
        ])

        self.assertIn("sore throat", problem.reasoning_context())

    def test_a_weak_subject_loses_to_the_category_noun(self):
        problem = _run([
            ("I want Korean BBQ.", "Korean BBQ"),
            ("Actually my throat hurts, something soft.", "places"),
        ])

        self.assertIn("restaurants", problem.search_query("show me places"))

    def test_asking_for_places_names_the_kind_of_place(self):
        # "easy to eat dinner" is advice; "easy to eat dinner restaurants"
        # is a list of places, which is what "pull up some spots" asked for.
        problem = _run([
            ("What should I eat for dinner?", "dinner"),
            ("I have a sore throat and want something easy to eat.", "dinner"),
        ])

        query = problem.search_query("Pull up some spots for me.")

        self.assertIn("restaurants", query)

    def test_asking_for_advice_does_not(self):
        problem = _run([
            ("I have a sore throat and want something easy to eat.", "dinner"),
        ])

        query = problem.search_query(
            "I have a sore throat and want something easy to eat.",
        )

        self.assertNotIn("restaurants", query)

    def test_a_word_is_not_repeated(self):
        problem = _run([
            ("I'm thinking about getting a guitar.", "guitar"),
            ("Electric.", "guitar"),
        ])

        words = problem.search_query("electric guitar").casefold().split()

        self.assertEqual(len(words), len(set(words)))


class TopicIsolationTests(unittest.TestCase):
    """Guitars must not inherit dinner."""

    def test_an_unrelated_subject_is_a_different_problem(self):
        problem = _run([
            ("I have a sore throat and want something easy to eat.", "dinner"),
        ])

        self.assertFalse(rs.about_the_same_thing(problem, "guitar"))

    def test_a_follow_up_that_says_nothing_continues_the_problem(self):
        problem = _run([
            ("I have a sore throat and want something easy to eat.", "dinner"),
        ])

        self.assertTrue(
            rs.about_the_same_thing(problem, "pull up some spots for me"),
        )

    def test_a_declared_topic_shift_always_starts_over(self):
        problem = _run([("I want something soft.", "dinner")])

        self.assertFalse(
            rs.about_the_same_thing(problem, "dinner", topic_shift=True),
        )

    def test_switching_topics_in_the_store_drops_the_constraints(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "I have a sore throat and want something easy to eat.",
            subject="dinner",
        )

        guitar = store.note_recommendation_turn(
            "I'm thinking about getting a guitar.", subject="guitar",
        )

        self.assertEqual(guitar.values(rs.SITUATION), ())
        self.assertNotIn("easy to eat", guitar.values(rs.ATTRIBUTE))
        self.assertNotIn("throat", guitar.search_query("show me some"))


class StoreTests(unittest.TestCase):
    """One store, not two."""

    def test_the_problem_lives_in_the_session_store(self):
        store = TaskSessionStore()
        store.note_recommendation_turn("I want something soft.", subject="food")

        self.assertIsNotNone(store.active_recommendation())

    def test_clearing_the_session_clears_the_problem(self):
        store = TaskSessionStore()
        store.note_recommendation_turn("I want something soft.", subject="food")

        store.clear()

        self.assertIsNone(store.active_recommendation())

    def test_constraints_are_readable_as_task_preferences(self):
        # The same shape TaskState already uses, so the planner path and the
        # conversational path describe a constraint the same way.
        problem = _run([("I want something soft.", "food")])

        self.assertEqual(
            problem.as_task_preferences().get(rs.ATTRIBUTE), "soft",
        )

    def test_evidence_is_recorded_against_the_open_problem(self):
        store = TaskSessionStore()
        store.note_recommendation_turn("I want something soft.", subject="food")

        store.record_candidates((), evidence=("a search result",))

        self.assertEqual(
            store.active_recommendation().evidence, ("a search result",),
        )


class TheWordsThatAreReadTests(unittest.TestCase):
    """The person's own sentence, not the router's paraphrase of it."""

    def test_the_paraphrase_loses_what_supersession_depends_on(self):
        # Measured live: the router turned "Actually my throat hurts,
        # something soft" into "Throat hurts, something soft". Without
        # "actually" the revision is invisible and without "my" the
        # situation reader has nothing to match -- so the Korean BBQ it was
        # meant to retire stayed in the problem and went on into the query.
        raw = "Actually my throat hurts, something soft."
        paraphrase = "Throat hurts, something soft"

        self.assertTrue(rs.revises(raw))
        self.assertFalse(rs.revises(paraphrase))
        self.assertIn(
            rs.SITUATION, [s.name for s in rs.read_constraints(raw)],
        )
        self.assertNotIn(
            rs.SITUATION, [s.name for s in rs.read_constraints(paraphrase)],
        )

    def test_a_revision_never_starts_a_new_problem(self):
        problem = _run([("I want Korean BBQ.", "Korean BBQ")])

        self.assertTrue(rs.about_the_same_thing(
            problem, "Actually my throat hurts, something soft.",
            subject="health",
        ))

    def test_a_drifting_router_subject_is_not_adopted(self):
        # Two turns into a sore-throat dinner the router offered "places to
        # visit", which would have made this a travel search.
        problem = _run([
            ("I want Korean BBQ.", "Korean BBQ"),
            ("Actually my throat hurts, something soft.", "health"),
            ("Show me some places.", "places to visit"),
        ])

        self.assertNotIn("visit", problem.search_query("Show me some places."))


class AskingToSeeOptionsTests(unittest.TestCase):
    """"Show me some" is a request for real options, not for advice."""

    def test_the_asking_is_recognised(self):
        for said in (
            "Show me some.", "Pull up some spots for me.",
            "Show me some places.", "Find me a few.",
            "Look up some options.",
        ):
            with self.subTest(said=said):
                self.assertTrue(rs.wants_to_see_options(said))

    def test_ordinary_conversation_is_not(self):
        for said in (
            "What should I eat for dinner?", "Electric.",
            "I have a sore throat.", "That sounds good.",
        ):
            with self.subTest(said=said):
                self.assertFalse(rs.wants_to_see_options(said))


class LogTests(unittest.TestCase):

    def test_the_context_block_names_every_part(self):
        problem = _run([
            ("I want Korean BBQ.", "Korean BBQ"),
            ("Actually my throat hurts, something soft.", "Korean BBQ"),
        ])

        block = problem.log_block()

        for heading in (
            "[Recommendation Context]", "Subject:", "Constraints:",
            "Superseded:", "Candidates:", "Evidence:",
        ):
            self.assertIn(heading, block)

    def test_the_log_shows_where_a_constraint_came_from(self):
        problem = _run([("I want something soft.", "food")])

        self.assertIn("[utterance]", problem.log_block())


if __name__ == "__main__":
    unittest.main()
