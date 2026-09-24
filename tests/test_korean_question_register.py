"""A Korean question in her register, where the conversion is mechanical.

The register converter left every question alone, so every 해요체 question
she asked counted as drift -- and they were a large share of the Korean-only
failures in the dogfood transcripts:

    영화는 보셨어요?
    그만두셨어요?
    저녁은 김치찌개 어때요?

Three shapes convert without morphology the module does not have, into the
forms the person chose for her questions (~나요?, ~(으)신가요?). Everything
else is still left alone, because a wrong conjugation ("배고파요?" needs
"배고프신가요?") is worse than the drift it would replace.
"""

from __future__ import annotations

import unittest

from brain import korean_register
from brain.conversation_style import REGISTER_DRIFT, RoboticTells


def _drifts(text: str) -> bool:
    return any(
        finding.failure == REGISTER_DRIFT
        for finding in RoboticTells.inspect(text, language="ko")
    )


class TheMechanicalShapesTests(unittest.TestCase):

    CONVERTED = (
        ("영화는 보셨어요?", "영화는 보셨나요?"),
        ("그만두셨어요?", "그만두셨나요?"),
        ("어제 뭐 먹었어요?", "어제 뭐 먹었나요?"),
        ("저녁은 김치찌개 어때요?", "저녁은 김치찌개 어떠신가요?"),
        ("시간 있어요?", "시간 있나요?"),
        ("재미없어요?", "재미없나요?"),
    )

    def test_each_converts(self):
        for said, formal in self.CONVERTED:
            with self.subTest(said=said):
                self.assertEqual(korean_register.to_formal(said), formal)

    def test_what_they_convert_to_is_her_register(self):
        # The detector the metric uses agrees, which is the point: the
        # conversion is only worth having if it stops counting as drift.
        for said, _ in self.CONVERTED:
            with self.subTest(said=said):
                self.assertTrue(_drifts(said))
                self.assertFalse(_drifts(korean_register.to_formal(said)))

    def test_inside_a_longer_reply(self):
        self.assertEqual(
            korean_register.to_formal("그 드라마 재미있습니다. 혹시 보셨어요?"),
            "그 드라마 재미있습니다. 혹시 보셨나요?",
        )


class WhatStaysAloneTests(unittest.TestCase):

    def test_irregular_and_already_formal_questions_are_untouched(self):
        for said in (
            "드라마 보고 배고파요?",        # needs 배고프신가요? -- not mechanical
            "잘 지내세요?",
            "확인해 드릴까요?",             # how the register asks permission
            "오늘은 어떤 하루 보내고 있나요?",
        ):
            with self.subTest(said=said):
                self.assertEqual(korean_register.to_formal(said), said)

    def test_the_evidential_is_not_made_an_order(self):
        # Measured in the paired baseline: "했나 보세요" (it seems you did)
        # came out "했나 보십시오" (observe that you did).
        for said in ("회사에서 하루 종일 회의만 했나 보세요.", "많이 피곤한가 보세요."):
            with self.subTest(said=said):
                self.assertNotIn("보십시오", korean_register.to_formal(said))

    def test_a_real_request_still_becomes_formal(self):
        self.assertEqual(
            korean_register.to_formal("여기 한번 보세요."), "여기 한번 보십시오.",
        )

    def test_statements_still_convert_as_before(self):
        self.assertEqual(
            korean_register.to_formal("많은 사랑을 받았어요."),
            "많은 사랑을 받았습니다.",
        )


if __name__ == "__main__":
    unittest.main()
