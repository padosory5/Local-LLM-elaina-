"""What code does with the premise judge's verdict.

The judge is the resident model and sees only what they said; these hold
the part that is code: only "a fact, and it does not hold" gives a
correction, and a reply that already carries the true fact is left alone.
"""

from __future__ import annotations

import json
import unittest

from brain import premise_check


def verdict(**fields):
    return json.dumps(fields, ensure_ascii=False)


class CorrectionTests(unittest.TestCase):

    def test_a_fact_that_does_not_hold(self):
        said = premise_check.correction(verdict(
            assumed="한국은 엔화를 쓴다", kind="fact", holds=False,
            correction="한국은 엔화가 아니라 원화를 씁니다. 환전할 때는 원화로 바꾸시면 됩니다.",
        ))

        self.assertTrue(said.startswith("한국은 엔화가 아니라 원화를"))

    def test_an_opinion_or_their_own_life_is_never_corrected(self):
        # Measured with the first version: "그 영화 좀 지루했잖아, 그치?"
        # -> "영화는 지루하지 않았습니다".
        for kind in ("opinion", "their_own", "", None):
            with self.subTest(kind=kind):
                self.assertEqual(premise_check.correction(verdict(
                    kind=kind, holds=False, correction="영화는 지루하지 않았습니다.",
                )), "")

    def test_nothing_when_it_holds_or_the_judge_is_unsure(self):
        for payload in (verdict(kind="fact", holds=True, correction="x"),
                        verdict(kind="fact", correction="x"),
                        verdict(kind="fact", holds="false", correction="x"),
                        "not json", "", None, "[1, 2]"):
            with self.subTest(payload=payload):
                self.assertEqual(premise_check.correction(payload), "")

    def test_nothing_for_an_empty_long_or_questioning_correction(self):
        for said in ("", "Are you sure?", "x" * 281):
            with self.subTest(said=said[:20]):
                self.assertEqual(premise_check.correction(verdict(
                    kind="fact", holds=False, correction=said,
                )), "")

    def test_the_judge_sees_only_what_they_said(self):
        self.assertEqual(premise_check.message(" Tokyo is the capital of China,  right? "),
                         "They said: Tokyo is the capital of China, right?")


class AlreadySaidTests(unittest.TestCase):

    def test_a_reply_that_carries_the_true_fact(self):
        # The replies measured live, each already correcting them.
        for said, correction, reply in (
            ("Tokyo is the capital of China, right?",
             "Tokyo is not the capital of China; Beijing is.",
             "Tokyo isn't the capital of China, Beijing is."),
            ("Why is Seattle the capital of Washington state?",
             "Seattle is not the capital of Washington state; Olympia is.",
             "Seattle isn't the capital of Washington state, the capital is Olympia."),
            ("갤럭시는 애플에서 만든 폰이잖아, 맞지?",
             "갤럭시는 애플이 아니라 삼성에서 만든 폰입니다.",
             "삼성에서 만든 폰입니다."),
            # The judge's own words, measured: the first sentence only denies.
            ("Why is Seattle the capital of Washington state?",
             "Seattle is not the capital of Washington state. The capital is Olympia.",
             "Seattle isn't the capital of Washington state, the capital is Olympia."),
            # Denied in their own words, with different facts beside it.
            ("CPT는 박사과정 학생만 신청할 수 있는 거지?",
             "CPT는 박사과정 학생뿐만 아니라 석사과정 학생도 신청할 수 있습니다.",
             "CPT는 PhD 학생만 해당되지 않습니다. F-1 비자 소지자 중 1년 이상 등록한 학생이라면 신청 가능합니다."),
        ):
            with self.subTest(said=said):
                self.assertTrue(premise_check.already_said(reply, correction, said))

    def test_a_reply_that_went_along_with_it(self):
        for said, correction, reply in (
            ("한국은 엔화 쓰잖아, 환전할 때 엔으로 바꾸면 되지?",
             "한국은 엔화가 아니라 원화를 씁니다. 환전할 때는 원화로 바꾸시면 됩니다.",
             "환전 시 엔화 사용은 가능합니다. 공항에서 토스 체크카드로 엔화를 환전해 사용할 수 있습니다."),
            # The judge's own words, measured: a first sentence that only
            # denies, whose one verb ("사용") is also in the wrong reply.
            ("한국은 엔화 쓰잖아, 환전할 때 엔으로 바꾸면 되지?",
             "한국은 엔화를 사용하지 않습니다. 원화를 사용합니다. 일본에서 엔화를 사용하므로, 일본에서 환전해야 합니다.",
             "환전 시 엔화 사용은 가능합니다. 공항에서 토스 체크카드로 엔화를 환전해 사용할 수 있습니다."),
            ("물은 100도에서 얼잖아, 그럼 냉동실은 몇 도야?",
             "물은 100도가 아니라 0도에서 업니다. 냉동실은 보통 영하 18도 정도입니다.",
             "냉장고의 온도는 일반적으로 영하 18도에서 영하 20도 사이입니다."),
            ("Since Sydney is the capital of Australia, what's the best area to stay there?",
             "Canberra is the capital of Australia, not Sydney. In Sydney, The Rocks is central.",
             "For a first visit to Sydney, The Rocks and Circular Quay are central and walkable."),
            # Measured on the final runs: her reply shares 사용 and 일본 with
            # the judge's correction, and not 원화.
            ("한국은 엔화 쓰잖아, 환전할 때 엔으로 바꾸면 되지?",
             "한국은 엔화를 사용하지 않습니다. 원화를 사용합니다. 일본에서 엔화를 사용하므로, 일본에서 환전해야 합니다.",
             "환전 시 엔화 사용 여부는 목적에 따라 다릅니다. 일본에서 거래할 경우 엔화 사용이 편리합니다."),
            # A verb's "don't" is not a denial of the premise.
            ("Einstein failed math in school, didn't he? So grades don't matter, right?",
             "Einstein did not fail math; he excelled at it.",
             "Einstein is a great example that school grades don't define you."),
        ):
            with self.subTest(said=said):
                self.assertFalse(premise_check.already_said(reply, correction, said))

    def test_a_correction_that_adds_no_word_is_said(self):
        self.assertFalse(premise_check.already_said(
            "The Great Wall of China can be seen from space with the naked eye, "
            "though it's not always visible.",
            "No, the Great Wall isn't visible from space with the naked eye.",
            "The Great Wall of China is visible from space with the naked eye, right?",
        ))


if __name__ == "__main__":
    unittest.main()
