"""A fact they told her is not echoed back to them as a question.

The replies are the ones measured on the misunderstanding check, where the
prompt already said not to do this.
"""

from __future__ import annotations

import unittest

from brain.told_not_asked import without_asking_back


class TakenOutTests(unittest.TestCase):

    def test_the_whole_reply_was_the_echo(self):
        self.assertEqual(without_asking_back("다음 달에 이사하시나요?", "저 다음 달에 이사가요"), "")

    def test_the_rest_is_kept(self):
        self.assertEqual(
            without_asking_back(
                "다음 주 금요일에 시애틀로 돌아가시나요? 편안하게 가시길 바랍니다.",
                "다음 주 금요일에 시애틀로 돌아가",
            ),
            "편안하게 가시길 바랍니다.",
        )

    def test_a_one_word_echo(self):
        # Measured: "이사하시나요? 다음 주 금요일 시애틀로 돌아가실 거입니다."
        self.assertEqual(without_asking_back("이사하시나요?", "저 다음 달에 이사가요"), "")

    def test_in_english(self):
        self.assertEqual(
            without_asking_back(
                "You're going back to Seattle next Friday? Safe travels.",
                "I'm going back to Seattle next Friday.",
            ),
            "Safe travels.",
        )


class LeftAloneTests(unittest.TestCase):

    def test_a_real_follow_up(self):
        reply = "좋네요. 어떤 이유로 이사하세요?"
        self.assertEqual(without_asking_back(reply, "저 다음 달에 이사가요"), reply)

    def test_when_they_asked_rather_than_told(self):
        reply = "다음 주 금요일에 시애틀로 돌아가시나요?"
        self.assertEqual(without_asking_back(reply, "내가 언제 시애틀로 돌아간다고 했지?"), reply)

    def test_a_reply_with_no_question(self):
        reply = "알겠습니다. 다음 달에 이사하시는군요."
        self.assertEqual(without_asking_back(reply, "저 다음 달에 이사가요"), reply)

    def test_ordinary_conversation(self):
        reply = "What kind of movies do you like?"
        self.assertEqual(without_asking_back(reply, "recommend a movie"), reply)


class WorthCheckingTests(unittest.TestCase):

    def test_what_takes_something_for_granted(self):
        from brain.told_not_asked import worth_checking
        for said in ("Why is Seattle the capital of Washington state?",
                     "Einstein failed math in school, didn't he?",
                     "Tokyo is the capital of China, right?",
                     "왜 하늘은 파래?", "물은 100도에서 얼잖아",
                     "Since there's a casino on Bainbridge Island, which one should I go to?"):
            with self.subTest(said=said):
                self.assertTrue(worth_checking(said))
        for said in ("What's the capital of China?", "recommend a movie", "내 여동생은 부산에 살아"):
            with self.subTest(said=said):
                self.assertFalse(worth_checking(said))


class RepeatsTests(unittest.TestCase):

    def test_her_last_answer_again(self):
        from brain.told_not_asked import repeats
        # Measured after "아니 CPT 말고 OPT".
        earlier = "CPT 신청 서류는 학업계획서, 연구계획서, 추천서가 필요합니다."
        self.assertTrue(repeats(earlier, earlier))

    def test_a_new_answer_about_the_right_one(self):
        from brain.told_not_asked import repeats
        # Measured: retried without the history, this came back 685,000.
        earlier = ("The population of Portland is approximately 657,000 as of the latest "
                   "estimates.")
        self.assertFalse(repeats("The population of Portland is approximately 68,000 as of "
                                 "the latest estimates.", earlier))
        self.assertFalse(repeats("OPT needs an I-765 and a new I-20.", earlier))


class TheQuestionBeforeTests(unittest.TestCase):
    """Her previous answer, translated. Measured on the basics check."""

    def test_the_number_crosses_and_the_question_does_not(self):
        from brain.told_not_asked import answers_the_one_before
        earlier = "The freezing point of water is 32 degrees Fahrenheit."
        self.assertTrue(answers_the_one_before(
            "물의 동결점은 32도 화씨입니다.", "물은 몇 도에서 끓어?", earlier))

    def test_a_real_answer_to_the_new_question(self):
        from brain.told_not_asked import answers_the_one_before
        earlier = "The freezing point of water is 32 degrees Fahrenheit."
        for reply in ("물의 끓는점은 100도입니다.", "물은 100도에서 끓습니다."):
            with self.subTest(reply=reply):
                self.assertFalse(answers_the_one_before(
                    reply, "물은 몇 도에서 끓어?", earlier))

    def test_no_number_is_not_a_repeat(self):
        from brain.told_not_asked import answers_the_one_before
        self.assertFalse(answers_the_one_before(
            "물은 섭씨에서 끓습니다.", "물은 몇 도에서 끓어?",
            "The freezing point of water is 32 degrees Fahrenheit."))


class AsTheirsTests(unittest.TestCase):

    def test_their_sister_said_back_as_hers(self):
        from brain.told_not_asked import as_theirs
        # Measured.
        self.assertEqual(as_theirs("내 여동생이 부산에 삽니다. 알고 있습니다.", "내 여동생은 부산에 살아"),
                         "여동생이 부산에 삽니다. 알고 있습니다.")
        self.assertEqual(as_theirs("My sister got married last year? How lovely.",
                                   "My sister got married last year."),
                         "Your sister got married last year? How lovely.")

    def test_left_alone(self):
        from brain.told_not_asked import as_theirs
        # A question: she may speak of her own name.
        self.assertEqual(as_theirs("My name is Elaina.", "What's my name?"), "My name is Elaina.")
        self.assertEqual(as_theirs("여동생분이 부산에 계시는군요.", "내 여동생은 부산에 살아"),
                         "여동생분이 부산에 계시는군요.")


if __name__ == "__main__":
    unittest.main()
