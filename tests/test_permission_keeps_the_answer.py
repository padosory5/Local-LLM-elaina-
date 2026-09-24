"""An answer with an offer attached is not a permission question.

Measured in the slip demo. The person confirmed "CBT? -- 응, CPT", the
corrected question was looked up, and the reply was the CPT document list
with an offer clause on the end of the same sentence. The redundant-
permission guard read the one sentence as the question, and replaced the
whole reply with a status line:

    You:     응
    Elaina:  완료했습니다.

The English run lost its answer the same way, as "That's done." A bare
question ("찾아볼까요?") after the work is done is still replaced -- that is
what the guard is for.

The offer detectors are patched so the test exercises this branch itself,
not whichever way a model happens to phrase an offer.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from brain import chat_engine
from brain.action_commitment import OFFER
from tests.turn_harness import build_engine

ANSWER = (
    "CPT 신청 시 필요한 서류는 학업 관련 실습 계획서, 학과 승인서, 취업 제안서, "
    "학생 증명서 등이며, 더 자세히 확인해 드릴까요?"
)


class ThePermissionGuardKeepsTheAnswerTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"

    def tearDown(self):
        self.engine.close()

    def _guard(self, reply: str) -> str:
        with mock.patch.object(chat_engine, "offered_action", return_value=True), \
                mock.patch.object(chat_engine, "speech_act_of", return_value=OFFER), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.engine._refuse_redundant_permission(
                reply,
                decision=SimpleNamespace(acts=True),
                route=SimpleNamespace(action_requested=True),
                action_performed=True,
            )

    def test_an_answer_with_an_offer_attached_is_kept(self):
        guarded = self._guard(ANSWER)

        self.assertIn("취업 제안서", guarded)
        self.assertNotIn("완료했습니다", guarded)

    def test_a_bare_permission_question_is_still_replaced(self):
        self.assertNotEqual(self._guard("찾아볼까요?"), "찾아볼까요?")


if __name__ == "__main__":
    unittest.main()
