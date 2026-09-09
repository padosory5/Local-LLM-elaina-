"""She may not say a thing never happened on the model's memory alone.

From a real Korean session. Asked about 육이오 전쟁 -- the Sino-Korean
reading of 6.25, the date the Korean War began -- she said it never
happened. One turn later, told "한국전쟁이 육이오 전쟁이야", she answered:

    한국전쟁은 1950년 6월 25일 시작된 전쟁으로, 육이오 전쟁이라는 별명도
    있습니다. 공식 명칭은 한국전쟁입니다.

She had the fact the whole time. The router had chosen ``direct_answer``
with ``freshness_required: false`` -- reasonably, for a settled historical
question -- so nothing looked anything up, and the model's failure to
connect the reading to the event was spoken as a fact about the world.

Every grounding guard written before this one checks what a reply asserts
is *there*. This is the other direction, and the two are not symmetric in
cost: an invented price is a value the user can check, while "that never
happened" ends the conversation and sounds more certain than the honest
answer would have.
"""

from __future__ import annotations

import unittest

from brain import existence_claims


class DenialsAreFoundInBothLanguagesTests(unittest.TestCase):
    """Rule 4. Korean denies existence in a shape English does not have."""

    KOREAN_DENIALS = (
        "육이오 전쟁이라는 전쟁은 존재하지 않습니다.",
        "그런 전쟁은 없습니다.",
        "그 전쟁은 일어나지 않았습니다.",
        "육이오 전쟁이라는 사건은 없습니다.",
        "역사적으로 그런 일은 없었습니다.",
        "그 인물은 가공의 인물입니다.",
        "그것은 허구입니다.",
    )
    ENGLISH_DENIALS = (
        "There is no such war.",
        "That event never happened.",
        "The Korean war of 6/25 does not exist.",
        "That didn't happen.",
        "There was no city called Zelovia.",
        "It isn't a real place.",
        "The story is fictional.",
    )

    def test_every_denial_is_caught(self):
        for said in self.KOREAN_DENIALS + self.ENGLISH_DENIALS:
            with self.subTest(said=said):
                self.assertTrue(
                    existence_claims.denies_existence(said),
                    f"{said!r} says something is not there",
                )


class HonestSentencesSurviveTests(unittest.TestCase):
    """The half that decides whether the guard is usable.

    Korean says 없다 constantly and almost none of it is a denial of
    existence, so a pattern anchored on that verb alone would censor
    ordinary replies -- which is the failure mode that made an earlier
    guard in this project flag its own correct answers.
    """

    KEPT = (
        # A hedge is a claim about her, and it is true.
        "제가 아는 한 그런 전쟁은 들어본 적이 없습니다.",
        "I'm not aware of any such war.",
        # Ordinary uses of the same verbs.
        "문제 없습니다.",
        "재고가 없습니다.",
        "There is no charge for that.",
        # Disagreement is not an existence claim.
        "아니요, 그건 맞지 않습니다.",
        "No, that's not right -- it started in 1950.",
        # Saying she looked and could not find it.
        "I couldn't find it in the sources I checked.",
        "그건 확실하지 않습니다. 한번 찾아보겠습니다.",
        # The correct answer she gave one turn later.
        "한국전쟁은 1950년 6월 25일 시작된 전쟁으로, 육이오 전쟁이라는 "
        "별명도 있습니다.",
    )

    def test_none_of_these_read_as_a_denial(self):
        for said in self.KEPT:
            with self.subTest(said=said):
                self.assertEqual(
                    existence_claims.denies_existence(said), (),
                    f"{said!r} is not a claim that something is not there",
                )


class EvidenceMakesADenialFairTests(unittest.TestCase):

    DENIAL = "그런 지점은 존재하지 않습니다."

    def test_nothing_checked_leaves_it_unsupported(self):
        self.assertEqual(
            existence_claims.unsupported(self.DENIAL, searched=False),
            (self.DENIAL,),
        )

    def test_a_search_that_ran_supports_it(self):
        # The point of searching is that she may say so afterwards.
        self.assertEqual(
            existence_claims.unsupported(self.DENIAL, searched=True), (),
        )

    def test_held_evidence_supports_it(self):
        self.assertEqual(
            existence_claims.unsupported(
                self.DENIAL, searched=False,
                evidence="No branches found in this area.",
            ),
            (),
        )


class OnlyTheDenialIsRemovedTests(unittest.TestCase):

    def test_the_rest_of_the_reply_stays(self):
        reply = (
            "육이오 전쟁이라는 전쟁은 존재하지 않습니다. "
            "한국전쟁이라면 1950년에 시작되었습니다."
        )
        denials = existence_claims.denies_existence(reply)

        self.assertEqual(len(denials), 1)
        self.assertEqual(
            existence_claims.without(reply, denials),
            "한국전쟁이라면 1950년에 시작되었습니다.",
        )

    def test_a_reply_that_is_only_a_denial_empties(self):
        reply = "There is no such war."

        self.assertEqual(
            existence_claims.without(
                reply, existence_claims.denies_existence(reply),
            ),
            "",
        )


class TheEngineReplacesItTests(unittest.TestCase):

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def test_an_unchecked_korean_denial_becomes_an_offer_to_look(self):
        self.engine._turn_language = "ko"

        reply = self.engine._enforce_existence_claims(
            "육이오 전쟁이라는 전쟁은 존재하지 않습니다.", searched=False,
        )

        self.assertNotIn("존재하지 않습니다", reply)
        self.assertIn("찾아볼까요", reply)
        # In her language, whole. The guard-line table is what makes that
        # true, and a reply in two languages is the failure it exists for.
        self.assertNotIn("look it up", reply)

    def test_an_unchecked_english_denial_becomes_an_offer_to_look(self):
        self.engine._turn_language = "en"

        reply = self.engine._enforce_existence_claims(
            "There is no such war.", searched=False,
        )

        self.assertIn("look it up", reply)

    def test_a_searched_denial_is_left_alone(self):
        self.engine._turn_language = "en"
        said = "I checked, and there is no such branch."

        self.assertEqual(
            self.engine._enforce_existence_claims(said, searched=True), said,
        )

    def test_an_ordinary_reply_is_untouched(self):
        self.engine._turn_language = "ko"
        said = "한국전쟁은 1950년 6월 25일에 시작되었습니다. 문제 없습니다."

        self.assertEqual(
            self.engine._enforce_existence_claims(said, searched=False), said,
        )


if __name__ == "__main__":
    unittest.main()
