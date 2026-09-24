"""A removed promise is replaced in the turn's language.

Measured in a paired Korean run:

    You:     그렇구나
    [Commitment Guard] Removed a promise with no ability behind it.
    Elaina:  What would you like me to do next?

The only reply of 576 Korean turns across the parity measurements with no
Korean in it. The replacement was an English literal inside chat_engine,
and this guard runs after the reply-language check, so nothing caught it.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from brain import chat_engine, guard_lines
from tests.turn_harness import build_engine

# No ability matches what she promised, which is the branch that replaces.
NO_ABILITY = SimpleNamespace(matched=False, capability=None)
PROMISE = "Let me check that for you."


class TheReplacementFollowsTheTurnTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def _enforce(self, reply: str, said: str, language: str) -> str:
        self.engine._turn_language = language
        with mock.patch.object(
            chat_engine.CapabilityRegistry, "match", return_value=NO_ABILITY,
        ), contextlib.redirect_stdout(io.StringIO()):
            return self.engine._enforce_action_commitment(
                reply, user_input=said, action_performed=False,
            )

    def test_a_korean_turn_hears_korean(self):
        self.assertEqual(
            self._enforce(PROMISE, "그렇구나", "ko"),
            guard_lines.say("next_step_question", "ko"),
        )

    def test_an_english_turn_is_unchanged(self):
        self.assertEqual(
            self._enforce(PROMISE, "ok", "en"),
            "What would you like me to do next?",
        )

    def test_a_sentence_that_stands_alone_is_kept_instead(self):
        self.assertEqual(
            self._enforce(f"내일 비가 올 것 같습니다. {PROMISE}", "그렇구나", "ko"),
            "내일 비가 올 것 같습니다.",
        )


if __name__ == "__main__":
    unittest.main()
