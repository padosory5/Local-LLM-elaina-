"""A memory may say less than the person did. It may not say more.

What she keeps about someone is written by a model (memory/extractor.py), and
the model does not always stop at rewording. Each of these was measured in a
real store or a restart check:

    나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어
        -> "The user is majoring in Computer Engineering at Washington University"
    나 수업 끝나고 보통 젠레스 존 제로 해   -> "... plays Genres Zero ..."
    우리 집 강아지 이름은 콩이야            -> "The user's dog is named Kongi."
    I'm going to UW in Seattle.             -> "... the University of Washington in Seattle."

``memory.faithful.added`` says what a memory states that their sentence did
not, without asking a model, and the store keeps their own words when it finds
anything (tests/test_memory_kept_as_said.py).
"""

from __future__ import annotations

import unittest

from memory import faithful


class NothingAddedTests(unittest.TestCase):
    """Rewording that says the same thing is theirs."""

    def test_third_person_is_not_an_addition(self):
        for said, content in (
            ("My name is Aiden.", "The user's name is Aiden."),
            ("I study Electrical Engineering.", "The user studies Electrical Engineering."),
            ("I live in Seattle.", "The user lives in Seattle."),
            ("I'm allergic to peanuts, so please keep that in mind.", "The user is allergic to peanuts."),
            ("By the way I'm vegetarian. What's a quick dinner I can make tonight?", "The user is vegetarian."),
            ("I don't drink coffee after 2pm.", "The user does not drink coffee after 2 PM."),
            ("My birthday is March 14th.", "The user's birthday is March 14th."),
            ("I work at Amazon as a backend engineer.", "The user works at Amazon as a backend engineer."),
            ("no I mean I'm going to UW in Tacoma", "The user is going to UW in Tacoma."),
        ):
            with self.subTest(said=said):
                self.assertEqual(faithful.added(said, content), set())

    def test_the_same_in_korean(self):
        for said, content in (
            ("우리 집 강아지 이름은 콩이야", "사용자의 강아지 이름은 콩입니다."),
            ("나 수업 끝나고 보통 젠레스 존 제로 해", "사용자는 수업이 끝나면 보통 젠레스 존 제로를 합니다."),
            ("나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어", "사용자는 워싱턴 대학교에서 컴퓨터공학을 전공하고 있습니다."),
            ("내 여동생은 부산에 살아", "사용자의 여동생은 부산에 살아."),
            ("나 매운 거 못 먹어", "사용자는 매운 거를 못 먹습니다."),
        ):
            with self.subTest(said=said):
                self.assertEqual(faithful.added(said, content), set())

    def test_leaving_something_out_is_allowed(self):
        self.assertEqual(
            faithful.added("I usually go to the gym after work, it keeps me sane.",
                           "The user goes to the gym after work."), set())


class SomethingAddedTests(unittest.TestCase):
    """A value they did not say."""

    def test_the_measured_ones(self):
        for said, content, extra in (
            ("나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어",
             "The user is majoring in Computer Engineering at Washington University.", "washington"),
            ("나 수업 끝나고 보통 젠레스 존 제로 해", "The user usually plays Genres Zero after class.", "genres"),
            ("우리 집 강아지 이름은 콩이야", "The user's dog is named Kongi.", "kongi"),
            ("I'm going to UW in Seattle.",
             "The user is attending the University of Washington in Seattle.", "washington"),
        ):
            with self.subTest(said=said):
                self.assertIn(extra, faithful.added(said, content))

    def test_a_different_value(self):
        self.assertIn("peanuts", faithful.added("I'm allergic to shellfish.", "The user is allergic to peanuts."))
        self.assertIn("서울에", faithful.added("내 여동생은 부산에 살아", "사용자의 여동생은 서울에 삽니다."))
        self.assertEqual(faithful.added("나 워싱턴 주립대 다녀", "사용자는 워싱턴 대학교에 다닙니다."), {"대학교에"})
        self.assertTrue(faithful.added("나 공포 영화 진짜 싫어해", "사용자는 공포 영화를 좋아합니다."))

    def test_a_number_they_did_not_say(self):
        # The same number, meaning something else.
        self.assertTrue(faithful.added("I turn 25 in December.", "The user is 25 years old."))
        self.assertEqual(faithful.added("I'm 24 years old.", "The user is 24 years old."), set())
        self.assertIn("2023", faithful.added("I graduated from Yonsei.", "The user graduated from Yonsei in 2023."))
        self.assertIn("2", faithful.added("I have two younger brothers.", "The user has 2 younger brothers."))

    def test_a_not_that_was_not_theirs(self):
        self.assertIn("not", faithful.added("I eat pork.", "The user does not eat pork."))
        self.assertIn("not", faithful.added("I don't eat pork.", "The user eats pork."))
        self.assertIn("not", faithful.added("나 매운 거 못 먹어", "사용자는 매운 거를 먹습니다."))

    def test_a_guess_at_what_they_meant(self):
        # Plausible, and not what was said.
        self.assertTrue(faithful.added("I have a job interview on the 20th.",
                                       "The user has a job interview at Google on the 20th."))
        self.assertTrue(faithful.added("My sister lives in Busan.", "The user's younger sister lives in Busan."))

    def test_a_memory_in_the_other_language_is_not_theirs(self):
        self.assertTrue(faithful.added("내 여동생은 부산에 살아", "The user's sister lives in Busan."))
        self.assertTrue(faithful.added("My sister lives in Busan.", "사용자의 여동생은 부산에 삽니다."))


class TheirOwnWordsTests(unittest.TestCase):

    def test_marked_as_theirs(self):
        self.assertEqual(faithful.as_said("  I'm a junior   at UW. "), 'They said: "I\'m a junior at UW."')
        self.assertEqual(faithful.as_said("나 스타트업에서 개발자로 일해"), 'They said: "나 스타트업에서 개발자로 일해"')

    def test_what_they_said_adds_nothing_to_itself(self):
        for said in ("I'm a junior at UW.", "작년에 미국으로 이사 왔어", "I don't eat pork."):
            with self.subTest(said=said):
                self.assertEqual(faithful.added(said, faithful.as_said(said)), set())


if __name__ == "__main__":
    unittest.main()
