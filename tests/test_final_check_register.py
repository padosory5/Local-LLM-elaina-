"""The final check's regeneration is said in her register too.

Measured in a paired Korean run:

    You:     그러게 말이야
    [Response Guard] The final text repeated the previous answer after a
                     new or corrected message; regenerating once.
    Elaina:  지금은 조금 힘들었겠어요. 조금씩 회복해 가시면 좋겠습니다.

"겠어요 -> 겠습니다" is in the converter's table. The regeneration happens
after the voice pass, where the conversion runs, so the new text went out
without ever meeting it.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from brain import guard_lines
from brain.response_quality import ResponseQualityGuard
from tests.turn_harness import build_engine

REGENERATED = "지금은 조금 힘들었겠어요. 조금씩 회복해 가시면 좋겠습니다."


class TheRegenerationIsConvertedTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"
        self.engine.conversation.add("user", "회사에서 하루 종일 회의만 했어")
        self.engine.conversation.add("assistant", "오늘 많이 힘드셨겠습니다.")

    def tearDown(self):
        self.engine.close()

    def _check(self, retries=(True, False)) -> str:
        # By default the draft repeats and the regeneration does not.
        with mock.patch.object(
            ResponseQualityGuard, "should_retry", side_effect=list(retries),
        ), mock.patch.object(
            self.engine.client, "chat",
            return_value={"message": {"content": REGENERATED}},
        ), contextlib.redirect_stdout(io.StringIO()):
            return self.engine._final_response_check(
                "오늘 많이 힘드셨겠습니다.",
                user_input="그러게 말이야",
                messages=[],
                model="test",
                temperature=0.2,
                num_predict=80,
                keep_alive=None,
                max_words=60,
                max_sentences=2,
            )

    def test_the_regenerated_reply_is_in_her_register(self):
        spoken = self._check()

        self.assertNotIn("겠어요", spoken)
        self.assertIn("힘들었겠습니다", spoken)

    def test_giving_up_is_said_in_the_turns_language(self):
        # Both attempts repeat. The line that says so was an English
        # literal, heard as English in a Korean conversation.
        spoken = self._check(retries=(True, True))

        self.assertEqual(
            spoken, guard_lines.say("answered_wrong_thing", "ko"),
        )
        self.assertNotIn("Sorry", spoken)


if __name__ == "__main__":
    unittest.main()
