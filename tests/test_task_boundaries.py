# -*- coding: utf-8 -*-
"""One task at a time, and switching tasks changes who owns the state.

The contamination this suite exists to stop, measured live: a turn asking
for restaurants in Gangnam held two gaming monitors as its candidates, and
"good gaming monitor" as one of its constraints, because the turn was read
as *the same problem*.

The cause was a boundary made of lookup tables. ``about_the_same_thing``
declared a new problem only when ``category_for`` knew both sides, or when
the new head noun appeared in ``_VARIANTS`` -- thirteen words, and "monitor"
is not one of them. A monitor problem had neither, so nothing could fire and
every later request continued it.

Both sides already knew the noun. ``problem._thing()`` returned "monitor"
the whole time; nothing compared it to "restaurants". These tests hold that
comparison in place, and hold the two things it must not break: a follow-up
that refers to what is open, and an explicit return to what is not.
"""

from __future__ import annotations

import unittest

import contextlib
import io

from brain import recommendation_state as rs
from brain import result_state
from brain.task_session import TaskSessionStore
from security.capability_offer import CapabilityOfferGate
from tests.turn_harness import build_engine


def _store_with(said, *, names=("ASUS ROG Swift PG27AQWP-W",
                                "Alienware AW2524HF")):
    """A session holding one task, with candidates and evidence against it."""
    store = TaskSessionStore()
    problem = store.note_recommendation_turn(said, subject=said)
    store.record_candidates(
        tuple(
            result_state.Candidate(name=name, url=f"https://example.com/{i}")
            for i, name in enumerate(names)
        ),
        evidence=(f"evidence for {said}",),
    )
    return store, problem


class ANewThingIsANewTaskTests(unittest.TestCase):

    SWITCHES = (
        ("find me a good gaming monitor",
         "find me some good restaurants in Gangnam"),
        ("recommend me some mechanical keyboards",
         "find me a good gaming monitor"),
        ("show me hotels in Seoul",
         "what mechanical keyboard should I buy?"),
        ("find me some good restaurants in Gangnam",
         "find me a good gaming monitor"),
    )

    def test_the_task_identity_changes(self):
        for first, second in self.SWITCHES:
            with self.subTest(second=second):
                store, before = _store_with(first)

                after = store.note_recommendation_turn(second, subject=second)

                self.assertNotEqual(after.id, before.id)

    def test_no_candidate_survives(self):
        for first, second in self.SWITCHES:
            with self.subTest(second=second):
                store, _ = _store_with(first)

                store.note_recommendation_turn(second, subject=second)

                self.assertEqual(store.results().items, ())

    def test_no_constraint_survives(self):
        store, _ = _store_with("find me a 1440p 144Hz monitor under 500000 KRW")

        after = store.note_recommendation_turn(
            "find me a quiet restaurant in Gangnam under 30000 KRW",
            subject="find me a quiet restaurant in Gangnam under 30000 KRW",
        )

        carried = " ".join(slot.value for slot in after.constraints).casefold()
        for leaked in ("monitor", "1440p", "144hz", "500000"):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, carried)

    def test_no_evidence_survives(self):
        store, _ = _store_with("find me a good gaming monitor")

        after = store.note_recommendation_turn(
            "find me some good restaurants in Gangnam",
            subject="find me some good restaurants in Gangnam",
        )

        self.assertEqual(after.evidence, ())

    def test_an_empty_new_task_stays_empty(self):
        # The failure this replaces was a fallback: ``candidates=found or
        # problem.candidates``. A new task with nothing found yet must show
        # nothing, never the last task's results.
        store, _ = _store_with("find me a good gaming monitor")

        store.note_recommendation_turn(
            "find me some good restaurants in Gangnam",
            subject="find me some good restaurants in Gangnam",
        )
        store.record_candidates((), evidence=("restaurant evidence",))

        self.assertEqual(store.results().items, ())


class ARefinementIsTheSameTaskTests(unittest.TestCase):
    """The regression-sensitive half. None of these may start a new task."""

    CONTINUATIONS = (
        ("find me a few good hotels in Seoul", "anything cheaper?"),
        ("recommend me some mechanical keyboards", "what about the second one?"),
        ("find me a good gaming monitor", "open the ASUS one"),
        ("find me some good restaurants in Gangnam", "which one would you pick?"),
    )

    def test_the_task_identity_is_kept(self):
        for first, second in self.CONTINUATIONS:
            with self.subTest(second=second):
                store, before = _store_with(first)

                after = store.note_recommendation_turn(
                    second, subject="", follow_up=True,
                )

                self.assertEqual(after.id, before.id)

    def test_the_candidates_are_kept(self):
        for first, second in self.CONTINUATIONS:
            with self.subTest(second=second):
                store, _ = _store_with(first)

                store.note_recommendation_turn(
                    second, subject="", follow_up=True,
                )

                self.assertEqual(len(store.results().items), 2)

    def test_an_ordinal_still_resolves(self):
        store, _ = _store_with(
            "find me a few good hotels in Seoul",
            names=("Hotel Inspiroom Jongro", "Sofitel Ambassador Seoul Hotel"),
        )

        store.note_recommendation_turn(
            "what about the second one?", subject="", follow_up=True,
        )

        self.assertEqual(
            store.results().at(1).name, "Sofitel Ambassador Seoul Hotel",
        )

    def test_naming_the_same_thing_again_refines(self):
        store, before = _store_with("find me a good gaming monitor")

        after = store.note_recommendation_turn(
            "find me a cheaper gaming monitor",
            subject="find me a cheaper gaming monitor",
        )

        self.assertEqual(after.id, before.id)


class TheHeadNounComparisonTests(unittest.TestCase):
    """Asked of the words, not of a table that has to know the domain."""

    def test_a_different_noun_is_a_different_problem(self):
        problem = rs.update(
            rs.start("find me a good gaming monitor"),
            "find me a good gaming monitor",
        )

        self.assertTrue(rs.names_another_thing(problem, ["good restaurants"]))

    def test_the_same_noun_is_not(self):
        problem = rs.update(
            rs.start("find me a good gaming monitor"),
            "find me a good gaming monitor",
        )

        self.assertFalse(rs.names_another_thing(problem, ["curved monitor"]))

    def test_a_plural_is_the_same_noun(self):
        problem = rs.update(
            rs.start("find me a hotel in Seoul"), "find me a hotel in Seoul",
        )

        self.assertFalse(rs.names_another_thing(problem, ["good hotels"]))

    def test_it_does_not_need_the_noun_to_be_in_any_table(self):
        # "monitor" is absent from _VARIANTS and category_for returns "" for
        # it. That is exactly why the boundary failed, and exactly what this
        # must no longer depend on.
        self.assertNotIn("monitor", rs._VARIANTS)
        self.assertEqual(rs.category_for("gaming monitor"), "")

        problem = rs.update(
            rs.start("find me a good gaming monitor"),
            "find me a good gaming monitor",
        )

        self.assertTrue(rs.names_another_thing(problem, ["mechanical keyboards"]))


class GoingBackToAnEarlierTaskTests(unittest.TestCase):
    """Historical state becomes current only when asked for by name."""

    def test_an_explicit_return_is_recognised(self):
        for said, wanted in (
            ("actually, back to those hotels", "hotels"),
            ("let's go back to the monitors", "monitors"),
            ("back to those hotels in Seoul", "hotels in Seoul"),
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    rs.returns_to_earlier(said).casefold().startswith(
                        wanted.split()[0],
                    ),
                )

    def test_an_ordinary_turn_is_not_a_return(self):
        for said in (
            "find me a good gaming monitor",
            "anything cheaper?",
            "what about the second one?",
        ):
            with self.subTest(said=said):
                self.assertEqual(rs.returns_to_earlier(said), "")

    def test_the_earlier_task_and_its_candidates_come_back(self):
        store, hotels = _store_with(
            "find me a few good hotels in Seoul",
            names=("Hotel Inspiroom Jongro", "Sofitel Ambassador Seoul Hotel"),
        )
        store.note_recommendation_turn(
            "now find me a gaming monitor", subject="now find me a gaming monitor",
        )
        store.record_candidates(
            (result_state.Candidate(name="Alienware AW2524HF"),
             result_state.Candidate(name="ASUS ROG Swift PG27AQWP-W")),
            evidence=("monitor evidence",),
        )

        restored = store.note_recommendation_turn(
            "actually, back to those hotels", subject="",
        )

        self.assertEqual(restored.id, hotels.id)
        self.assertEqual(
            [item.name for item in store.results().items],
            ["Hotel Inspiroom Jongro", "Sofitel Ambassador Seoul Hotel"],
        )

    def test_an_ordinal_after_a_return_resolves_against_the_restored_set(self):
        store, _ = _store_with(
            "find me a few good hotels in Seoul",
            names=("Hotel Inspiroom Jongro", "Sofitel Ambassador Seoul Hotel"),
        )
        store.note_recommendation_turn(
            "now find me a gaming monitor", subject="now find me a gaming monitor",
        )
        store.record_candidates(
            (result_state.Candidate(name="Alienware AW2524HF"),
             result_state.Candidate(name="ASUS ROG Swift PG27AQWP-W")),
            evidence=("monitor evidence",),
        )
        store.note_recommendation_turn(
            "actually, back to those hotels", subject="",
        )

        self.assertEqual(
            store.results().at(1).name, "Sofitel Ambassador Seoul Hotel",
        )

    def test_nothing_comes_back_that_was_never_there(self):
        store, _ = _store_with("find me a good gaming monitor")

        self.assertIsNone(store.reactivate("back to those hotels"))

    def test_the_task_being_left_is_kept_rather_than_destroyed(self):
        store, _ = _store_with("find me a few good hotels in Seoul")
        monitors = store.note_recommendation_turn(
            "now find me a gaming monitor", subject="now find me a gaming monitor",
        )
        store.note_recommendation_turn(
            "actually, back to those hotels", subject="",
        )

        # The monitors are superseded, not gone: going back again works.
        returned = store.note_recommendation_turn(
            "back to the monitors", subject="",
        )

        self.assertEqual(returned.id, monitors.id)


class ConsentBelongsToTheTaskItWasOfferedForTests(unittest.TestCase):
    """"Yeah" answers the question that is open, not one that has been left.

    Elaina: "Want me to look up some monitors?"
    User:   "Actually find me restaurants in Gangnam."
    User:   "Yeah."

    The "yeah" must not run the monitor lookup. Before this, the only thing
    between the offer and the acceptance was a ninety-second expiry -- and
    time is not the boundary that matters, because the person can change
    subject in five seconds and answer in ten.
    """

    def _offered(self, task_id):
        gate = CapabilityOfferGate()
        gate.offer(
            capability_id="web_search",
            goal="look up some monitors",
            offer_text="Want me to look up some monitors?",
            task_id=task_id,
        )
        return gate

    def test_an_offer_answers_for_the_task_it_was_made_about(self):
        gate = self._offered("monitor-task")

        self.assertTrue(gate.belongs_to("monitor-task"))

    def test_it_does_not_answer_for_a_task_that_replaced_it(self):
        gate = self._offered("monitor-task")

        self.assertFalse(gate.belongs_to("restaurant-task"))

    def test_an_offer_with_no_task_still_belongs_to_the_conversation(self):
        # Not every offer pauses a recommendation. One that never had a task
        # is answerable whatever is open, which is what it always was.
        gate = self._offered("")

        self.assertTrue(gate.belongs_to("restaurant-task"))

    def test_switching_tasks_drops_the_offer_in_a_real_turn(self):
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            engine = build_engine(routes={})
            monitors = engine.task_sessions.note_recommendation_turn(
                "find me a good gaming monitor",
                subject="find me a good gaming monitor",
            )
            engine.capability_offer.offer(
                capability_id="web_search",
                goal="look up some monitors",
                offer_text="Want me to look up some monitors?",
                task_id=monitors.id,
            )
            engine.chat("actually find me restaurants in Gangnam")

        self.assertIsNone(engine.capability_offer.peek())


if __name__ == "__main__":
    unittest.main()
