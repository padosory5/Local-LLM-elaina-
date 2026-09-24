"""A Korean question is a question, with or without a question mark.

``_REQUEST_SHAPE`` decides whether a turn the model wanted to look up was
actually asked for. When it says "remark", the lookup is withheld and she
answers from what she already knows. Every English line in it looks at the
front of the sentence -- a fronted wh-word, an inverted auxiliary -- and
Korean does neither: the question word stays where the answer goes, and
speech-to-text drops the question mark.

Measured on ``tests/request_shape_matrix.json`` before the fix: 2 of 16
Korean questions read as questions, against 7 of 7 English ones. In a real
session (``runtime/koreanSession2.log``) that sent

    시애틀에서 인천공항까지가는데 몇시간 걸려
    얼마나 많은 사람들이 봤는데
    워싱턴 대학교에 유명한거 어떤게 있어

down the remark path, and the second came back with an invented viewer
count. Both halves of the matrix are asserted: the remark half is the
failure the rule was written to prevent ("Spotify won't play anything
today." answered with a web search).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brain.intent_router import reads_as_request

MATRIX = json.loads(
    Path(__file__).with_name("request_shape_matrix.json").read_text(
        encoding="utf-8",
    )
)


class TheMatrixTests(unittest.TestCase):

    def test_every_case_reads_the_way_it_should(self):
        for case in MATRIX["cases"]:
            with self.subTest(said=case["said"]):
                got = "request" if reads_as_request(case["said"]) else "remark"
                self.assertEqual(got, case["expect"], case.get("why", ""))

    def test_both_languages_and_both_outcomes_are_measured(self):
        seen = {(case["language"], case["expect"]) for case in MATRIX["cases"]}

        self.assertEqual(
            seen,
            {("ko", "request"), ("ko", "remark"),
             ("en", "request"), ("en", "remark")},
        )


class WhereAQuestionWordIsNotAskingTests(unittest.TestCase):
    """Each exclusion exists because the word has a non-question reading."""

    def test_the_non_question_readings_stay_remarks(self):
        for said in (
            "뭐 그냥 그래",            # 뭐 as a filler
            "어떤 친구가 그러던데",      # 어떤 as "a certain"
            "몇 번을 봐도 좋더라",       # 몇 inside a concessive
            "언제나 고마워",            # 언제나 is "always"
            "왜냐하면 너무 비싸서",       # 왜냐하면 is "because"
            "우리 언니",                # a noun ending in 니
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_request(said))

    def test_the_question_endings_ask_without_a_question_word(self):
        for said in (
            "비행기가 제일 빠른가",
            "그 섬에 카지노가 있니",
            "오늘 문 여나요",
            "그거 맞는지",
            "같이 갈까",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_request(said))

    def test_english_is_unchanged(self):
        self.assertFalse(reads_as_request("Spotify won't play anything today."))
        self.assertTrue(reads_as_request("how long is the flight to Incheon"))


if __name__ == "__main__":
    unittest.main()
