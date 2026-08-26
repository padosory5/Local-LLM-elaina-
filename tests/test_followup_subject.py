"""A bare follow-up must carry the previous turn's subject with it.

Found live: right after Elaina named three Hong Kong hotels, "check the
price on the browser" reached the browser planner with no subject at all,
and the planner honestly answered "I cannot check prices without knowing
which website or product you're referring to." The information existed one
turn earlier; nothing carried it across.
"""

import unittest

from brain.chat_engine import ChatEngine
from brain.task_session import TaskSessionStore


HOTELS = "The Peninsula Hong Kong, Park Hyatt Hong Kong, and Grand Hyatt Hong Kong."


class _Item:
    def __init__(self, name):
        self.name = name


class _TaskState:
    def __init__(self, names):
        self.goal = "hotels in Seoul"
        self.collected_items = [_Item(name) for name in names]
        self.collected_information = ("Found some hotels.",)


def _engine(*, statement=HOTELS, session_names=()):
    engine = ChatEngine.__new__(ChatEngine)
    engine._grounded_context = (
        {"subject": "places to stay", "statement": statement, "source": "web search"}
        if statement
        else {}
    )
    engine.task_sessions = TaskSessionStore()
    if session_names:
        engine.task_sessions.remember(_TaskState(session_names))
    return engine


class FollowupSubjectTests(unittest.TestCase):
    def test_a_bare_attribute_request_borrows_the_previous_subject(self):
        engine = _engine()

        self.assertEqual(
            engine._followup_subject("check the price on the browser"), HOTELS,
        )

    def test_a_deictic_request_borrows_the_previous_subject(self):
        engine = _engine()

        self.assertEqual(engine._followup_subject("open the first one"), HOTELS)

    def test_a_request_naming_a_site_carries_no_borrowed_context(self):
        engine = _engine()

        self.assertEqual(engine._followup_subject("open youtube.com"), "")

    def test_a_request_naming_its_own_subject_carries_no_borrowed_context(self):
        # Otherwise an unrelated earlier topic could redirect a
        # self-contained goal.
        engine = _engine()

        self.assertEqual(
            engine._followup_subject("check the price for The Peninsula Hong Kong"), "",
        )

    def test_an_unrelated_page_action_carries_no_borrowed_context(self):
        engine = _engine()

        self.assertEqual(engine._followup_subject("click Images"), "")
        self.assertEqual(engine._followup_subject("search google for pizza"), "")

    def test_task_results_stand_in_when_there_is_no_grounded_statement(self):
        engine = _engine(statement="", session_names=("Hotel Cappuccino", "L7 Hongdae"))

        subject = engine._followup_subject("check the price on the browser")

        self.assertIn("Hotel Cappuccino", subject)
        self.assertIn("L7 Hongdae", subject)

    def test_nothing_remembered_means_no_context_rather_than_a_guess(self):
        engine = _engine(statement="")

        self.assertEqual(engine._followup_subject("check the price"), "")

    def test_an_empty_request_is_handled(self):
        self.assertEqual(_engine()._followup_subject("  "), "")

    def test_borrowed_context_is_length_capped(self):
        engine = _engine(statement="x" * 900)

        self.assertEqual(len(engine._followup_subject("check the price")), 400)


if __name__ == "__main__":
    unittest.main()
