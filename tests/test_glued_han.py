"""Chinese written into a Korean word is taken out like kana; 한자 is not.

Measured in a paired Korean run:

    음, 그렇군요. 다른 작품도 있습니다. ... 원하시면详细介绍해드리겠습니다.

and in the person's own session, "청양椒". The kana guard deliberately
leaves Han alone, because 한자 is real Korean and a question about it
deserves an answer that contains it. What separates the two is where the
character sits: glued inside a Hangul word, or in front of the syllable that
makes it a verb, is the model's Chinese bleeding through. Written next to a
word -- in brackets, before a particle, on its own -- is 한자.

Only the characters go, never the sentence (tests/test_foreign_script.py).
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain.text_filter import TextFilter


def _clean(text: str) -> str:
    with contextlib.redirect_stdout(io.StringIO()):
        return TextFilter.without_foreign_script(text)


class GluedHanIsDroppedTests(unittest.TestCase):

    def test_the_measured_run_goes_and_the_sentence_stays(self):
        self.assertEqual(
            _clean("음, 그렇군요. 다른 작품도 있습니다. 원하시면详细介绍해드리겠습니다."),
            "음, 그렇군요. 다른 작품도 있습니다. 원하시면해드리겠습니다.",
        )

    def test_inside_a_korean_word(self):
        self.assertEqual(
            _clean("청양椒를 넣으면 맵습니다. 양은 취향대로 조절하십시오."),
            "청양를 넣으면 맵습니다. 양은 취향대로 조절하십시오.",
        )


class HanjaStaysTests(unittest.TestCase):

    def test_an_answer_about_hanja_is_untouched(self):
        for said in (
            "사랑은 한자로 愛라고 씁니다.",
            "한자(漢字)는 중국에서 온 문자입니다.",
            "漢字로 쓰면 이렇습니다.",
            "한자 漢字는 두 글자입니다.",
        ):
            with self.subTest(said=said):
                self.assertEqual(_clean(said), said)

    def test_kana_is_still_taken_out(self):
        self.assertEqual(
            _clean("좋습니다. 도움이 되었ようで 다행입니다."), "좋습니다. 도움이 되었 다행입니다.",
        )


if __name__ == "__main__":
    unittest.main()
