"""The person's sentence handed back with a new ending is still an echo.

The echo guard stripped a first sentence only when it was *exactly* the
person's sentence. Korean hands a sentence back by changing its ending,
which is precisely the part an exact test compares -- measured four times in
five Korean dogfood runs:

    You:     회사에서 하루 종일 회의만 했어
    Elaina:  회사에서 하루 종일 회의만 했네요. 쉬는 시간에 ...
             회사에서 하루 종일 회의만 했군요. ...
             회사에서 하루 종일 회의만 했나 보네요. ...
             회사에서 하루 종일 회의만 했습니다. ...

-- in the person's own 반말 verb, not the honorific she uses, and counted as
request_restated every time. English does the same with a pronoun swap.
Compared as characters, so it holds in both languages.

Most of this file is what must *not* be stripped: a question back, an
answer that reuses the subject's words, and a short empathic reply that
shares a word or two.
"""

from __future__ import annotations

import unittest

from brain.response_quality import ResponseQualityGuard as Guard

MEETINGS = "회사에서 하루 종일 회의만 했어"


class ReEndedEchoesAreStrippedTests(unittest.TestCase):

    def test_the_measured_korean_echoes(self):
        for reply, rest in (
            ("회사에서 하루 종일 회의만 했네요. 쉬는 시간에 잠시 휴식을 취하셨으면 좋겠습니다.",
             "쉬는 시간에 잠시 휴식을 취하셨으면 좋겠습니다."),
            ("회사에서 하루 종일 회의만 했군요. 쉬어가시면 좋겠습니다.",
             "쉬어가시면 좋겠습니다."),
            ("회사에서 하루 종일 회의만 했나 보네요. 힘드셨겠습니다.",
             "힘드셨겠습니다."),
            ("회사에서 하루 종일 회의만 했습니다. 힘들었겠습니다.",
             "힘들었겠습니다."),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(Guard.strip_current_turn_echo(reply, MEETINGS), rest)

    def test_the_english_mirror(self):
        self.assertEqual(
            Guard.strip_current_turn_echo(
                "You're moving to Seattle next month. That's exciting!",
                "I'm moving to Seattle next month",
            ),
            "That's exciting!",
        )


class RealRepliesAreKeptTests(unittest.TestCase):

    def test_nothing_else_is_stripped(self):
        for said, reply in (
            # A question back is a follow-up.
            ("just back to back meetings honestly",
             "Another meeting? That's back-to-back. Take a breath between them."),
            # An answer that reuses the subject's words -- the closest call,
            # 0.75 against the 0.8 line.
            ("what's the capital of France",
             "The capital of France is Paris. It is lovely in spring."),
            ("간단한 걸로",
             "간단한 걸로 '부대찌개'를 추천합니다. 간단하면서도 포만감 있는 메뉴입니다."),
            # Short empathy that shares a word or two is what a person says.
            ("회의만 했어", "회의만 하셨군요. 힘드셨겠습니다."),
            ("오늘 좀 힘들었어", "오늘 많이 힘드셨군요. 푹 쉬십시오."),
            ("그건 봤어", "그건 보셨군요. 다른 작품을 찾아보겠습니다."),
            ("how do I make kimchi stew",
             "To make kimchi stew, simmer kimchi with pork. Serve with rice."),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(Guard.strip_current_turn_echo(reply, said), reply)

    def test_an_echo_that_is_the_whole_reply_is_not_stripped_to_nothing(self):
        # That case belongs to is_pure_echo, which regenerates instead.
        reply = "회사에서 하루 종일 회의만 했네요."

        self.assertEqual(Guard.strip_current_turn_echo(reply, MEETINGS), reply)


if __name__ == "__main__":
    unittest.main()
