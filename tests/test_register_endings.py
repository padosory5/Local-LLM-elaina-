"""Three endings the paired baseline measured drifting, and what they become.

    오늘 하루 정말 힘드셨나 보죠.       -> 오늘 하루 정말 힘드셨겠습니다.
    2시 이후 커피를 안 마시시는 거죠.   -> 2시 이후 커피를 안 마시시는군요.
    헤드폰은 정상이니 걱정 없으세요.    -> 헤드폰은 정상이니 걱정 없으십니다.

The first was left for the re-say, which returned it still drifting three
times in three Korean evening runs. The person has said the evidential
("~나 봐요", "~보죠") is not how she talks; the presumptive is, and she
already uses it -- "힘드셨겠습니다" is in her clean replies. The third was
worse than drift: the general 세요 rule made it "걱정 없으십시오", an order
to be without worry.

Most of this file is what must *not* change, because 나 and 가 in front of
보다 are also ordinary verbs.
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


class TheMeasuredEndingsTests(unittest.TestCase):

    CONVERTED = (
        ("오늘 하루 정말 힘드셨나 보죠.", "오늘 하루 정말 힘드셨겠습니다."),
        ("오늘 회의 많았나 보죠.", "오늘 회의 많았겠습니다."),
        ("회사에서 하루 종일 회의만 했나 보세요.",
         "회사에서 하루 종일 회의만 했겠습니다."),
        ("요즘 자주 깨시나 봐요.", "요즘 자주 깨시겠습니다."),
        ("많이 피곤하신가 봐요.", "많이 피곤하시겠습니다."),
        ("많이 피곤한가 보죠.", "많이 피곤하겠습니다."),
        ("날씨가 좋은가 봐요.", "날씨가 좋겠습니다."),
        ("2시 이후 커피를 안 마시시는 거죠.", "2시 이후 커피를 안 마시시는군요."),
        ("헤드폰은 정상이니 걱정 없으세요.", "헤드폰은 정상이니 걱정 없으십니다."),
        ("중요한 일이라면 결과가 중요하겠죠.", "중요한 일이라면 결과가 중요하겠습니다."),
        ("내일 비가 온대요.", "내일 비가 온다고 합니다."),
        ("힘들었겠지.", "힘들었겠습니다."),
        ("영화를 말씀하시는 거죠?", "영화를 말씀하시는 건가요?"),
        ("안 마시시는 거죠?", "안 마시시는 건가요?"),
        ("그 헤드폰은 배터리가 오래 간대요.", "그 헤드폰은 배터리가 오래 간다고 합니다."),
    )

    def test_each_converts(self):
        for said, formal in self.CONVERTED:
            with self.subTest(said=said):
                self.assertEqual(korean_register.to_formal(said), formal)

    def test_what_they_become_is_her_register(self):
        for said, _ in self.CONVERTED:
            with self.subTest(said=said):
                self.assertTrue(_drifts(said))
                self.assertFalse(_drifts(korean_register.to_formal(said)))

    def test_inside_a_longer_reply(self):
        self.assertEqual(
            korean_register.to_formal(
                "오늘 회의 많았나 보죠. 푹 쉬십시오."
            ),
            "오늘 회의 많았겠습니다. 푹 쉬십시오.",
        )


class WhatStaysAloneTests(unittest.TestCase):

    def test_verbs_that_only_look_evidential(self):
        for said in (
            "친구를 한번 만나 보세요.",      # 만나다 -- try meeting them
            "그 식당에 한번 가 보세요.",     # 가다 -- try going
            "그런가 보죠.",                  # 그렇다 needs its ㅎ back
        ):
            with self.subTest(said=said):
                self.assertNotIn("겠습니다", korean_register.to_formal(said))

    def test_other_uses_of_geojyo(self):
        for said in (
            "그런 거죠.",                    # not after 는
            "그런 거죠?",                    # nor as a question
        ):
            with self.subTest(said=said):
                self.assertEqual(korean_register.to_formal(said), said)

    def test_an_imperative_is_still_an_imperative(self):
        self.assertEqual(
            korean_register.to_formal("편하게 계세요."), "편하게 계십시오.",
        )


if __name__ == "__main__":
    unittest.main()
