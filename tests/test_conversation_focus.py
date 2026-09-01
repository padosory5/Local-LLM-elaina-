"""One answer to "what are we talking about", and everything reading it.

The conversation that made this necessary, measured live:

    "I'm moving to Seattle on September 18."
    "Do you know which university is there?"   -> finds UW
    "Yep, I'm going there."                    -> "Seattle's a great place"
    "No, I mean I'm going to UW."              -> the same words, again

The router logged the correction. The goal layer still logged "moving to
Seattle", because its subject came from a field the correction never
touched -- and two turns later, "which apps do people use for rentals
there" searched "apps for finding rental properties", with Seattle and UW
both dropped.
"""

from __future__ import annotations

import unittest

from brain import conversation_focus as cf
from brain.response_quality import ResponseQualityGuard
from brain.task_session import TaskSessionStore

CONVERSATION = (
    ("I'm moving to Seattle on September 18.", "moving to Seattle"),
    ("Do you know which university is there?", "universities in Seattle"),
    ("Yep, I'm going there.", "moving to Seattle"),
    ("No, I mean I'm going to UW.", "moving to Seattle"),
    ("Where should I look for rent near my school?", "rent near school"),
    ("Which apps do people use for rentals there?", "rental apps"),
)


def _replay(turns=CONVERSATION):
    focus = cf.start()
    for text, subject in turns:
        focus = cf.update(focus, text, subject=subject)
    return focus


class CorrectionsWinTests(unittest.TestCase):

    def test_a_correction_replaces_the_subject(self):
        focus = _replay(CONVERSATION[:4])

        self.assertEqual(focus.subject, "UW")

    def test_the_routers_stale_subject_is_ignored_on_a_correction(self):
        # The router said "moving to Seattle" on that very turn.
        focus = cf.update(
            cf.start("moving to Seattle"),
            "No, I mean I'm going to UW.",
            subject="moving to Seattle",
        )

        self.assertEqual(focus.subject, "UW")

    def test_every_phrasing_of_a_correction_is_read(self):
        for said, expected in (
            ("No, I mean I'm going to UW.", "UW"),
            ("I meant the second one.", "second one"),
            ("Actually, I'm going to UW.", "UW"),
            ("I'm talking about UW.", "UW"),
            ("No, the rental apps.", "rental apps"),
        ):
            with self.subTest(said=said):
                self.assertEqual(cf.read_correction(said), expected)

    def test_an_ordinary_turn_is_not_a_correction(self):
        for said in (
            "Where should I look for rent near my school?",
            "Find Korean BBQ near me.",
            "Yep, I'm going there.",
        ):
            with self.subTest(said=said):
                self.assertEqual(cf.read_correction(said), "")

    def test_what_was_replaced_is_recorded_not_lost(self):
        focus = _replay(CONVERSATION[:4])

        self.assertTrue(focus.superseded)

    def test_a_pointer_does_not_become_the_subject(self):
        # "Yep, I'm going there" names nothing.
        before = _replay(CONVERSATION[:2])
        after = cf.update(
            before, "Yep, I'm going there.", subject="moving to Seattle",
        )

        self.assertEqual(after.subject, before.subject)


class BackgroundTests(unittest.TestCase):

    def test_explicit_school_beats_a_generic_router_topic(self):
        focus = cf.update(
            cf.start(), "I'm going to UW in Seattle.", subject="education",
        )
        self.assertEqual(focus.subject, "University of Washington")
        self.assertEqual(focus.background.get("location"), "Seattle")

    def test_the_location_survives_a_correction_about_something_else(self):
        # "going to UW" was matching the location pattern, so the
        # correction about which school replaced the city.
        focus = _replay(CONVERSATION[:4])

        self.assertEqual(focus.background.get("location"), "Seattle")

    def test_the_date_is_kept(self):
        focus = _replay(CONVERSATION[:1])

        self.assertEqual(focus.background.get("when"), "September 18")

    def test_the_corrected_subject_outlives_the_turn(self):
        # "rent near my school" three turns later still means near UW.
        focus = _replay()

        self.assertEqual(focus.background.get("about"), "UW")

    def test_a_date_stays_out_of_the_search_box(self):
        focus = _replay(CONVERSATION[:1])

        self.assertNotIn("September", " ".join(focus.query_context()))


class ContextReachesTheQueryTests(unittest.TestCase):
    """The rental failure, stated as a requirement."""

    def test_the_established_context_is_available_to_a_query(self):
        focus = _replay()

        context = " ".join(focus.query_context())

        self.assertIn("UW", context)
        self.assertIn("Seattle", context)

    def test_the_subject_leads(self):
        focus = _replay()

        self.assertEqual(focus.query_context()[0], "rental apps")


class StoreTests(unittest.TestCase):

    def test_the_focus_lives_in_the_session_store(self):
        store = TaskSessionStore()
        for text, subject in CONVERSATION:
            store.note_turn(text, subject=subject)

        self.assertEqual(store.focus().subject, "rental apps")
        self.assertEqual(store.focus().background.get("about"), "UW")

    def test_clearing_the_session_clears_the_focus(self):
        store = TaskSessionStore()
        store.note_turn("I'm moving to Seattle.", subject="moving")

        store.clear()

        self.assertIsNone(store.focus())

    def test_an_override_is_scoped_to_the_open_task(self):
        store = TaskSessionStore()
        store.note_recommendation_turn(
            "I want Korean BBQ.", subject="Korean BBQ",
        )

        self.assertTrue(store.note_source_override("Google Maps"))
        self.assertEqual(store.source_override(), "Google Maps")

    def test_a_clarifying_turn_keeps_the_override(self):
        store = TaskSessionStore()
        store.note_recommendation_turn("I want sushi.", subject="sushi")
        store.note_source_override("Google Maps")

        store.note_recommendation_turn("Gangnam.", subject="sushi")

        self.assertEqual(store.source_override(), "Google Maps")

    def test_a_new_task_drops_the_override(self):
        store = TaskSessionStore()
        store.note_recommendation_turn("I want sushi.", subject="sushi")
        store.note_source_override("Google Maps")

        store.note_recommendation_turn(
            "I'm thinking about getting a guitar.", subject="guitar",
        )

        self.assertEqual(store.source_override(), "")


class FinalResponseGuardTests(unittest.TestCase):
    """The check has to see the text that is actually about to be said."""

    HISTORY = [
        {"role": "user", "content": "yep I'm going there"},
        {
            "role": "assistant",
            "content": "That's awesome! Seattle's a great place to start.",
        },
    ]
    SAME = "That's awesome! Seattle's a great place to start."

    def test_a_correction_answered_identically_is_rejected(self):
        # No guard line appeared live, because the guard had run and passed
        # several transformations earlier.
        self.assertTrue(ResponseQualityGuard.should_retry(
            self.SAME, "no I mean I'm going to UW", self.HISTORY,
        ))

    def test_a_correction_answered_differently_is_fine(self):
        self.assertFalse(ResponseQualityGuard.should_retry(
            "UW is in the U District, north of downtown.",
            "no I mean I'm going to UW",
            self.HISTORY,
        ))

    def test_asking_for_it_again_still_works(self):
        for said in ("say that again", "tell me again", "repeat that"):
            with self.subTest(said=said):
                self.assertFalse(ResponseQualityGuard.should_retry(
                    self.SAME, said, self.HISTORY,
                ))

    def test_an_unrelated_new_question_is_not_a_correction(self):
        self.assertFalse(ResponseQualityGuard.should_retry(
            self.SAME, "what's the weather like", self.HISTORY,
        ))


if __name__ == "__main__":
    unittest.main()
