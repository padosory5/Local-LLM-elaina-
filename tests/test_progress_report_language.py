""""How's it going?" is answered in the language it was asked in.

``progress_question`` recognises the question in Korean -- "아직이야?",
"어떻게 돼 가?" -- and ``_progress_report`` answered all three states in
English literals: found by sweeping chat_engine for sentences returned
after the reply-language check, after "그렇구나" was answered "What would
you like me to do next?" in a paired Korean run.
"""

from __future__ import annotations

import contextlib
import io
import threading
import unittest

from brain import guard_lines
from tests.turn_harness import build_engine


class TheProgressReportFollowsTheTurnTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"

    def tearDown(self):
        self.engine._active_turn_cancel = None
        self.engine.close()

    def test_still_working(self):
        self.engine._active_turn_cancel = threading.Event()

        self.assertEqual(
            self.engine._progress_report(),
            guard_lines.say("progress_working", "ko"),
        )

    def test_waiting_for_a_go_ahead(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.capability_offer.offer(
                capability_id="web_search",
                goal="Find real, checkable options for: 드라마",
                offer_text="실제로 찾아볼까요?",
                task_id="",
                task_query="",
            )

        spoken = self.engine._progress_report()

        self.assertEqual(spoken, guard_lines.say("progress_waiting", "ko"))
        self.assertNotIn("Find real", spoken)

    def test_nothing_running(self):
        self.assertEqual(
            self.engine._progress_report(),
            guard_lines.say("progress_idle", "ko"),
        )

    def test_english_is_unchanged(self):
        self.engine._turn_language = "en"

        self.assertEqual(
            self.engine._progress_report(),
            "Nothing's running right now. Want me to start it?",
        )


if __name__ == "__main__":
    unittest.main()
