"""Where what the person did is kept: the activity log and their recordings."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from memory.activity_log import Activity, ActivityLog
from memory.routines import Routine, RoutineBook


class ActivityLogTests(unittest.TestCase):

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="elaina-log-"))
        self.now = 1_000_000.0
        self.log = ActivityLog(self.directory / "a.db", retention_days=14,
                               clock=lambda: self.now)

    def tearDown(self):
        self.log.close()

    def add(self, at, kind="click", **fields):
        return self.log.add(Activity(at=at, kind=kind, **fields))

    def test_rows_come_back_oldest_first_within_a_window(self):
        for at in (100.0, 300.0, 200.0):
            self.add(self.now - at, target=str(at))

        rows = self.log.recent(since=self.now - 250)

        self.assertEqual([row.target for row in rows], ["200.0", "100.0"])

    def test_by_kind_and_by_what_they_contain(self):
        self.add(self.now - 5, "page", target="CPT application - UW", url="https://uw.edu/cpt")
        self.add(self.now - 4, "page", target="lofi hip hop", url="https://youtube.com")
        self.add(self.now - 3, "click", target="CPT")

        pages = self.log.recent(kinds=("page",), containing="cpt")

        self.assertEqual([row.target for row in pages], ["CPT application - UW"])

    def test_forgetting_a_window_deletes_only_it(self):
        self.add(self.now - 7200)
        self.add(self.now - 60)

        self.assertEqual(self.log.forget(since=self.now - 3600), 1)
        self.assertEqual(self.log.count(), 1)
        self.assertEqual(self.log.forget(everything=True), 1)
        self.assertEqual(self.log.count(), 0)

    def test_forgetting_nothing_named_deletes_nothing(self):
        self.add(self.now)
        self.assertEqual(self.log.forget(), 0)

    def test_old_rows_go_by_themselves(self):
        self.add(self.now - 15 * 86400)
        self.add(self.now - 86400)

        self.assertEqual(self.log.prune(), 1)
        self.assertEqual(self.log.count(), 1)

    def test_a_row_can_be_finished_later(self):
        row = self.add(self.now, "fullscreen", app="game.exe", detail={"handle": 4})
        self.log.extend(row, until=self.now + 60)

        [activity] = self.log.recent()
        self.assertEqual(activity.detail, {"handle": 4, "until": self.now + 60})


class RoutineBookTests(unittest.TestCase):

    def setUp(self):
        self.book = RoutineBook(Path(tempfile.mkdtemp(prefix="elaina-routines-")))

    def routine(self, name, *targets):
        return Routine(
            name=name, recorded_at=1_700_000_000.0, said="record this",
            steps=[Activity(at=1_700_000_000.0 + i, kind="click", app="chrome.exe",
                            target=target, detail={"point": [i, i]})
                   for i, target in enumerate(targets)],
        )

    def test_a_saved_recording_comes_back_as_it_was(self):
        self.book.save(self.routine("morning setup", "구독", "검색"))

        [routine] = self.book.all()

        self.assertEqual(routine.name, "morning setup")
        self.assertEqual([step.target for step in routine.steps], ["구독", "검색"])
        self.assertEqual(routine.steps[1].detail, {"point": [1, 1]})

    def test_it_is_a_readable_file(self):
        path, _ = self.book.save(self.routine("아침 준비", "구독"))

        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# A recording Elaina made"))
        self.assertIn("아침 준비", text)

    def test_the_same_name_replaces_it(self):
        self.book.save(self.routine("morning setup", "a"))

        _, replaced = self.book.save(self.routine("Morning Setup", "b"))

        self.assertTrue(replaced)
        self.assertEqual([r.steps[0].target for r in self.book.all()], ["b"])

    def test_the_longest_name_in_a_sentence_wins(self):
        self.book.save(self.routine("morning setup", "a"))
        self.book.save(self.routine("morning setup 2", "b"))

        self.assertEqual(self.book.find("do morning setup 2 please").name, "morning setup 2")
        self.assertEqual(self.book.find("do Morning  Setup").name, "morning setup")

    def test_forgetting_moves_it_aside(self):
        path, _ = self.book.save(self.routine("morning setup", "a"))

        self.book.forget("morning setup")

        self.assertEqual(self.book.all(), [])
        self.assertTrue((path.parent / "forgotten" / path.name).exists())


if __name__ == "__main__":
    unittest.main()
