"""A temperature in the unit the question was asked in.

Measured three runs running on the everyday-answers check: "물은 몇 도에서
끓어?" answered "물의 끓는점은 212°F입니다". The fact was right, the unit was
the one the English turns before it had been using.

The market is the United States now; the unit follows the *language of the
question*, because that is what the person asking it thinks in.
"""

from __future__ import annotations

import unittest

from brain import units


class AKoreanQuestionGetsCelsiusTests(unittest.TestCase):

    def test_the_measured_replies(self):
        for reply, expected in (
            ("물의 끓는점은 212°F입니다.", "물의 끓는점은 섭씨 100도입니다."),
            ("물의 끓는점은 212도 화씨입니다.", "물의 끓는점은 섭씨 100도입니다."),
            ("물의 동결점은 화씨 32도입니다.", "물의 동결점은 섭씨 0도입니다."),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(units.in_celsius(reply, "물은 몇 도에서 끓어?"), expected)

    def test_an_english_unit_word_inside_a_korean_sentence(self):
        # No word boundary exists between "F" and 입, which is why the first
        # version of this matched nothing at all.
        self.assertEqual(
            units.in_celsius("오늘 68 degrees Fahrenheit입니다.", "오늘 몇 도야?"),
            "오늘 섭씨 20도입니다.",
        )

    def test_a_decimal_that_matters_is_kept(self):
        self.assertEqual(units.in_celsius("체온은 98.6°F입니다.", "체온 몇 도야?"),
                         "체온은 섭씨 37도입니다.")
        self.assertEqual(units.in_celsius("100.4°F입니다.", "몇 도야?"),
                         "섭씨 38도입니다.")

    def test_below_freezing(self):
        # Korean carries the minus as a word, and says a negative Celsius
        # the same way: 영하 4°F is -4 F, which is -20 C, said "영하 20도".
        self.assertEqual(units.in_celsius("영하 4°F입니다.", "몇 도야?"),
                         "영하 20도입니다.")
        self.assertEqual(units.in_celsius("-4°F입니다.", "몇 도야?"),
                         "영하 20도입니다.")
        self.assertEqual(units.in_celsius("32°F입니다.", "몇 도야?"),
                         "섭씨 0도입니다.")


class WhatIsLeftAloneTests(unittest.TestCase):

    def test_a_question_that_asked_for_fahrenheit(self):
        for said in ("물은 화씨로 몇 도에서 끓어?", "화씨 기준으로 알려줘"):
            with self.subTest(said=said):
                self.assertEqual(units.in_celsius("화씨 212도입니다.", said),
                                 "화씨 212도입니다.")

    def test_an_answer_already_in_celsius(self):
        reply = "물의 끓는점은 100도입니다."
        self.assertEqual(units.in_celsius(reply, "물은 몇 도에서 끓어?"), reply)

    def test_an_answer_with_no_temperature(self):
        reply = "서울은 지금 흐립니다."
        self.assertEqual(units.in_celsius(reply, "서울 날씨 어때?"), reply)

    def test_a_number_that_is_not_a_temperature(self):
        reply = "68번 버스를 타시면 됩니다."
        self.assertEqual(units.in_celsius(reply, "몇 번 버스야?"), reply)

    def test_nothing_at_all(self):
        self.assertEqual(units.in_celsius("", "몇 도야?"), "")


if __name__ == "__main__":
    unittest.main()
