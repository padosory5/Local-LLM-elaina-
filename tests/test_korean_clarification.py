"""Asked in Korean, the question is written in Korean, and answered in it.

``question_for`` wrote English only. The ask act is locked against
rewording, so the reply-language guard translated it -- and "Over-ear or
in-ear?" came back, in two of three paired Korean runs, as

    이어폰은 이어폰이냐, 이어폰이냐?

both options the same word, in 반말. Written in Korean it is her register
and names two different things; and a Korean answer to it ("인이어로") has
to count as an answer, or the question comes straight back.
"""

from __future__ import annotations

import unittest

from brain import recommendation_state as rs
from brain.conversation_style import REGISTER_DRIFT, RoboticTells
from brain.task_session import TaskSessionStore


def _problem(subject: str):
    return TaskSessionStore().note_recommendation_turn(
        f"recommend a cheap and good {subject}", subject=subject,
    )


def _drifts(text: str) -> bool:
    return any(
        finding.failure == REGISTER_DRIFT
        for finding in RoboticTells.inspect(text, language="ko")
    )


class TheQuestionTests(unittest.TestCase):

    def test_the_measured_question(self):
        self.assertEqual(
            _problem("headphones").question_for(rs.TYPE, language="ko"),
            "오버이어와 인이어 중 어느 쪽이 좋으신가요?",
        )

    def test_the_particle_follows_the_sound(self):
        # 렉 ends in a consonant and takes 과; 어 does not and takes 와.
        self.assertEqual(
            _problem("guitar").question_for(rs.TYPE, language="ko"),
            "일렉과 어쿠스틱 중 어느 쪽이 좋으신가요?",
        )

    def test_every_kind_has_a_korean_word(self):
        for thing, options in rs._VARIANTS.items():
            for option in options:
                with self.subTest(thing=thing, option=option):
                    self.assertIn(option, rs._VARIANTS_KO)

    def test_every_korean_question_is_her_register(self):
        for thing in rs._VARIANTS:
            problem = _problem(thing)
            for dimension in (rs.TYPE, rs.BUDGET):
                question = problem.question_for(dimension, language="ko")
                with self.subTest(thing=thing, question=question):
                    self.assertTrue(question)
                    self.assertFalse(_drifts(question))

    def test_the_general_questions(self):
        problem = _problem("ukulele")

        self.assertEqual(
            problem.question_for(rs.TYPE, language="ko"),
            "어떤 종류를 생각하고 계신가요?",
        )
        self.assertEqual(
            problem.question_for(rs.BUDGET, language="ko"),
            "예산은 어느 정도로 생각하고 계신가요?",
        )

    def test_english_is_unchanged(self):
        self.assertEqual(
            _problem("headphones").question_for(rs.TYPE), "Over-ear or in-ear?",
        )


class TheAnswerTests(unittest.TestCase):

    HEADPHONES = ("over-ear", "in-ear")

    def test_korean_answers_name_the_option(self):
        for said, option in (
            ("인이어", "in-ear"),
            ("인이어로", "in-ear"),
            ("이어폰으로 할게", "in-ear"),
            ("오버이어요", "over-ear"),
            ("헤드셋형이 좋아", "over-ear"),
        ):
            with self.subTest(said=said):
                self.assertEqual(rs._option_named(said, self.HEADPHONES), option)
                slot = rs.answer_for_dimension(
                    rs.TYPE, said, options=self.HEADPHONES,
                )
                self.assertEqual(slot.value, option)

    def test_a_word_inside_another_word_is_not_named(self):
        self.assertEqual(rs._option_named("맥주 한잔 하고 싶다", ("Windows", "Mac")), "")
        self.assertEqual(rs._option_named("새벽에 봤어", ("new", "used")), "")

    def test_both_named_is_a_question(self):
        self.assertEqual(
            rs._option_named("오버이어랑 인이어 차이가 뭐야", self.HEADPHONES), "",
        )

    def test_the_subject_already_naming_one_is_not_asked(self):
        problem = TaskSessionStore().note_recommendation_turn(
            "인이어 헤드폰 추천해줘", subject="인이어 headphones",
        )

        self.assertNotEqual(problem.missing_dimension(), rs.TYPE)


if __name__ == "__main__":
    unittest.main()
