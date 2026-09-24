"""A guard's disclaimer is said once, not at the end of every turn.

Measured in the Korean dogfood runs: three dinner turns in a row each ended

    아직 확인해 보지 않아서, 추측으로 말씀드리지는 않겠습니다.

The value guard was right to drop an unchecked figure each time. Saying so
three times in a row was not -- it is the robotic repetition the metric
counts, and it was one of the larger shares of it. English did the same
with "Sources disagree on that one...".

Once she has said it on the turn before, a later unchecked figure is dropped
quietly when the rest of the answer survives. If nothing survives, the line
is still said: the reply cannot be empty, and cannot keep the figure.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from brain import chat_engine, guard_lines
from tests.turn_harness import build_engine

DRAFT = "김치찌개는 간단하게 만들 수 있습니다. 한 그릇에 12,000원입니다."
EVIDENCE = "A page about how to make kimchi stew at home."


class SaidOnceTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"
        # As in the live runs, where computer control is off: with the
        # browser available the guard parks an offer to go and check
        # instead, and a parked offer is always said exactly as written.
        patcher = mock.patch.object(
            chat_engine.CapabilityRegistry, "is_available", return_value=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.engine.close()

    def _guard(self, reply: str) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._enforce_grounded_values(
                reply,
                user_input="간단한 걸로",
                action_performed=False,
                research_evidence=EVIDENCE,
                trusted_result=False,
                searched=True,
            )

    def test_the_first_time_it_is_said(self):
        guarded = self._guard(DRAFT)

        self.assertNotIn("12,000원", guarded)
        self.assertIn("간단하게 만들 수 있습니다", guarded)
        self.assertIn(guard_lines.say("unverified_figure_searched", "ko"), guarded)

    def test_the_turn_after_it_is_not_said_again(self):
        first = self._guard(DRAFT)
        self.engine.conversation.add("user", "간단한 걸로")
        self.engine.conversation.add("assistant", first)

        second = self._guard(DRAFT)

        self.assertNotIn("12,000원", second)
        self.assertEqual(second, "김치찌개는 간단하게 만들 수 있습니다.")

    def test_with_nothing_left_the_line_is_still_said(self):
        first = self._guard(DRAFT)
        self.engine.conversation.add("assistant", first)

        only_the_figure = self._guard("한 그릇에 12,000원입니다.")

        self.assertNotIn("12,000원", only_the_figure)
        self.assertTrue(only_the_figure.strip())


if __name__ == "__main__":
    unittest.main()
