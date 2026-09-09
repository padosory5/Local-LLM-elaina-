"""A reply may not carry a script she does not speak.

From a real Korean session (``runtime/koreanSession.log``, line 256), the
last sentence of a three-sentence reply:

    안녕하세요. 도움이 되었ようで 다행입니다. 궁금한 점이 또 있으시면
    언제든지 말씀해 주십시오.

"ようで" is Japanese, glued onto a Korean verb stem. What makes it a code
problem rather than a prompt problem is the log above the line: that draft
had already been regenerated once and rewritten twice, and the rewrites did
not touch it. Small multilingual models bleed between the CJK languages,
and this project's standing rule is that a confirmed behaviour gets a
deterministic guard.

The repair is by sentence rather than by character, because deleting the
run leaves "도움이 되었 다행입니다" -- a broken verb, and a sentence she
cannot stand behind. The other two sentences are fine and are still said.
"""

from __future__ import annotations

import unittest

from brain.text_filter import TextFilter


LEAKED = (
    "안녕하세요. 도움이 되었ようで 다행입니다. "
    "궁금한 점이 또 있으시면 언제든지 말씀해 주십시오."
)


class KanaNeverReachesSpeechTests(unittest.TestCase):

    def test_the_measured_sentence_loses_only_the_broken_clause(self):
        repaired = TextFilter.without_foreign_script(LEAKED)

        self.assertNotIn("ようで", repaired)
        self.assertIn("안녕하세요.", repaired)
        self.assertIn("언제든지 말씀해 주십시오.", repaired)
        # Not the character-level repair: that would leave "되었 다행입니다".
        self.assertNotIn("되었 다행", repaired)

    def test_a_reply_that_is_only_the_leak_comes_back_empty(self):
        # So the caller can put a guard line there instead of speaking a
        # broken sentence or going silent.
        self.assertEqual(
            TextFilter.without_foreign_script("도움이 되었ようで 다행입니다."),
            "",
        )

    def test_katakana_counts_too(self):
        self.assertEqual(
            TextFilter.without_foreign_script(
                "네, 알겠습니다. ソウル 날씨는 맑습니다.",
            ),
            "네, 알겠습니다.",
        )

    def test_ordinary_replies_are_untouched(self):
        for said in (
            "평범한 한국어 문장입니다. 문제 없습니다.",
            "The monitor is 27 inches. It costs $299.",
            "네, 한번 찾아보겠습니다.",
            "",
        ):
            with self.subTest(said=said):
                self.assertEqual(TextFilter.without_foreign_script(said), said)

    def test_hanja_is_korean_and_stays(self):
        # 한자 is real Korean and "한자로 어떻게 써?" is a question she is
        # allowed to answer. Only kana has no reading in either language.
        said = "한자로는 大韓民國이라고 씁니다."

        self.assertEqual(TextFilter.without_foreign_script(said), said)

    def test_the_speech_filter_applies_it(self):
        spoken = TextFilter.for_voice_response(LEAKED)

        self.assertNotIn("ようで", spoken)
        self.assertIn("안녕하세요.", spoken)


if __name__ == "__main__":
    unittest.main()
