"""A standing instruction in Korean is a standing instruction.

From a replay of a real Korean session:

    You:     그래 다음부턴 그렇게 설명해라
    Elaina:  응답하라 1997의 누적 시청자 수는 정확히 알려드릴 수 없습니다...

"Ok, explain it like that from now on" -- and she answered the previous
question again. Standing orders only knew "always / from now on / whenever /
every time", anchored on the first word, so the Korean form was invisible,
and so was the English one with an "ok" in front.

And once recognised, the reply was an English f-string: a Korean
instruction would have been answered "Alright -- I'll 그렇게 설명해라 from
now on."
"""

from __future__ import annotations

import unittest

from brain import guard_lines
from brain.standing_orders import read_instruction


class KoreanInstructionsAreReadTests(unittest.TestCase):

    def test_the_measured_turn_is_an_instruction(self):
        kind, note, _ = read_instruction("그래 다음부턴 그렇게 설명해라")

        self.assertEqual(kind, "note")
        self.assertEqual(note, "그렇게 설명해라")

    def test_the_other_ways_of_saying_it(self):
        for said in (
            "앞으로 존댓말 써줘",
            "이제부터 짧게 대답해",
            "다음부터는 너무 길게 말하지 마",
            "앞으로는 출처도 같이 알려주세요",
        ):
            with self.subTest(said=said):
                self.assertEqual(read_instruction(said)[0], "note")

    def test_a_statement_about_the_future_is_not_an_instruction(self):
        # 앞으로 opens these too. The ending is what makes an instruction.
        for said in (
            "앞으로 비가 온대",
            "앞으로 3일 동안 바빠",
            "다음부터 수업이 있어",
        ):
            with self.subTest(said=said):
                self.assertEqual(read_instruction(said), ("", "", ""))


class EnglishWithALeadInTests(unittest.TestCase):

    def test_an_acknowledgement_in_front_no_longer_hides_it(self):
        self.assertEqual(
            read_instruction("ok, from now on keep it short"),
            ("note", "keep it short", ""),
        )

    def test_the_plain_form_is_unchanged(self):
        self.assertEqual(
            read_instruction("from now on keep it short"),
            ("note", "keep it short", ""),
        )


class TheReplyIsInTheTurnsLanguageTests(unittest.TestCase):

    def test_every_standing_reply_exists_in_both_languages(self):
        for name in ("standing_repair", "standing_fact", "standing_note",
                     "standing_forget"):
            with self.subTest(name=name):
                english = guard_lines.say(name, "en")
                korean = guard_lines.say(name, "ko")
                self.assertTrue(english)
                self.assertTrue(any("가" <= c <= "힣" for c in korean))
                self.assertNotEqual(english, korean)

    def test_the_korean_note_does_not_quote_an_imperative_back(self):
        # "알겠습니다. 앞으로 그렇게 설명해라 하겠습니다" is not Korean.
        line = guard_lines.say("standing_note", "ko").format(
            first="그렇게 설명해라",
        )

        self.assertNotIn("설명해라", line)
        self.assertIn("앞으로", line)


if __name__ == "__main__":
    unittest.main()
