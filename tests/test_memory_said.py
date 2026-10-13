"""What they said is written down before anything is made of it.

A memory existed only at the end of a background thread -- an extraction call,
a consolidation call, then the row, up to 13 seconds later -- and the desktop
window stops the backend with ``taskkill /F``. So what a person told her just
before closing the window was never written.

``memory.said.SaidJournal`` puts the sentence in the database in the turn
itself. Whatever is still ``waiting`` when she next starts is finished then
(tests/test_memory_kept_as_said.py, ``ChatEngine._finish_what_was_left``).
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from memory.said import KEPT, MOST_TRIES, PASSED, WAITING, Said, SaidJournal


class JournalTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "memory.db")
        self.engine = create_engine(f"sqlite:///{self.path}")
        self.addCleanup(self.engine.dispose)
        self.sessions = sessionmaker(bind=self.engine)
        self.journal = SaidJournal(self.sessions, self.engine)

    def row(self, said_id) -> Said:
        with self.sessions() as session:
            return session.get(Said, said_id)

    def test_what_they_said_is_there_at_once_and_exactly(self):
        said_id = self.journal.note("  I'm a junior   at UW. ")

        row = self.row(said_id)
        self.assertEqual(row.text, "I'm a junior at UW.")
        self.assertEqual(row.state, WAITING)
        self.assertIsNone(row.memory_id)
        self.assertIsNotNone(row.said_at)

    def test_nothing_is_written_for_nothing(self):
        self.assertIsNone(self.journal.note("   "))
        self.assertEqual(self.journal.waiting(), [])

    def test_kept_names_the_memory_it_became(self):
        said_id = self.journal.note("My sister lives in Busan.")
        self.journal.settle(said_id, memory_id=7)

        row = self.row(said_id)
        self.assertEqual((row.state, row.memory_id), (KEPT, 7))
        self.assertEqual(self.journal.waiting(), [])
        self.assertEqual(self.journal.behind(7), ["My sister lives in Busan."])

    def test_kept_without_knowing_which_memory_is_still_kept(self):
        said_id = self.journal.note("I'm vegetarian.")
        self.journal.settle(said_id, kept=True)

        row = self.row(said_id)
        self.assertEqual((row.state, row.memory_id), (KEPT, None))

    def test_nothing_to_keep_is_settled_too(self):
        said_id = self.journal.note("Okay, thanks.")
        self.journal.settle(said_id)

        self.assertEqual(self.row(said_id).state, PASSED)
        self.assertEqual(self.journal.waiting(), [])

    def test_what_was_left_is_still_there_after_she_is_closed(self):
        first = self.journal.note("I'm allergic to peanuts.")
        second = self.journal.note("내 여동생은 부산에 살아")
        self.journal.settle(first, memory_id=1)
        self.engine.dispose()     # the process is gone

        engine = create_engine(f"sqlite:///{self.path}")
        self.addCleanup(engine.dispose)
        again = SaidJournal(sessionmaker(bind=engine), engine)
        self.assertEqual(again.waiting(), [(second, "내 여동생은 부산에 살아")])

    def test_oldest_first(self):
        ids = [self.journal.note(text) for text in ("one thing", "another thing", "a third thing")]
        self.assertEqual([said_id for said_id, _ in self.journal.waiting()], ids)

    def test_a_sentence_that_keeps_failing_is_left_alone(self):
        said_id = self.journal.note("I have asthma.")
        for attempt in range(1, MOST_TRIES + 1):
            self.assertEqual([i for i, _ in self.journal.waiting()], [said_id])
            self.assertEqual(self.journal.tried(said_id), attempt)
        self.assertEqual(self.journal.waiting(), [])
        self.assertEqual(self.row(said_id).state, WAITING)   # not lost: still in the database

    def test_two_sentences_behind_one_memory(self):
        first = self.journal.note("I'm going to UW in Seattle.")
        second = self.journal.note("no I mean I'm going to UW in Tacoma")
        self.journal.settle(first, memory_id=3)
        self.journal.settle(second, memory_id=3)
        self.assertEqual(self.journal.behind(3),
                         ["I'm going to UW in Seattle.", "no I mean I'm going to UW in Tacoma"])

    def test_a_database_that_cannot_be_written_does_not_stop_the_turn(self):
        def broken():
            raise RuntimeError("database is locked")

        journal = SaidJournal(broken, self.engine)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertIsNone(journal.note("I live in Seattle."))
            journal.settle(1, memory_id=2)
            self.assertEqual(journal.waiting(), [])
        self.assertIn("Could not write down what they said", log.getvalue())

    def test_a_database_from_before_gets_the_table(self):
        engine = create_engine(f"sqlite:///{self.path}.old")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("create table memories (id integer primary key, content varchar)")
            connection.exec_driver_sql("insert into memories (content) values ('The user is vegetarian.')")
        journal = SaidJournal(sessionmaker(bind=engine), engine)
        self.assertIsNotNone(journal.note("I live in Seattle."))
        with engine.begin() as connection:
            self.assertEqual(connection.exec_driver_sql("select content from memories").fetchall(),
                             [("The user is vegetarian.",)])


if __name__ == "__main__":
    unittest.main()
