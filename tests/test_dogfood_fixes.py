"""Three faults found by dogfooding on turns nothing had been tuned against.

All three are the same shape: a guard that works, wired to a signal that
cannot see the case. Two of them are Rule 4 -- an English-calibrated rule
applied silently to Korean -- and the third is a model boolean overruling a
deterministic decision.
"""

import unittest

from brain import grounded_values, memory_gate
from brain.chat_engine import DEICTIC_REFERENCE
from brain.grounded_values import GroundedValueGuard


class KoreanPricesWereInvisibleTests(unittest.TestCase):
    """The oldest honesty guard in the project could not see a Korean price.

    ``_MONEY`` ended the 원 branch on ``\\b``, which needs a non-word
    character after it. Korean attaches its particles straight to the noun
    -- 10,000원에, 8,000원입니다 -- so it never gets one. "10,000원 입니다",
    with a space, matched; nobody writes it that way.
    """

    def test_a_price_with_a_particle_attached_is_seen(self):
        for said in (
            "10,000원에 먹을 수 있습니다",
            "약 8,000원입니다",
            "500원짜리",
            "가격은 12,000원이고 맛있습니다",
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    grounded_values._digits(said), f"invisible price: {said}",
                )

    def test_english_prices_still_work(self):
        for said in ("it is 10,000 won.", "₩10,000", "$249.99"):
            with self.subTest(said=said):
                self.assertTrue(grounded_values._digits(said))

    def test_won_inside_an_ordinary_word_is_not_a_price(self):
        """3원소 is "three elements", not three won."""
        for said in ("3원소", "원소 3개", "2026년에 방영", "오후 2시"):
            with self.subTest(said=said):
                self.assertEqual(grounded_values._digits(said), set(), said)


class PricingAPlaceSheNamedTests(unittest.TestCase):
    """Naming an establishment *and* what it charges is a claim.

    The guard stands down when nothing was looked up, because most numbers
    in conversation are general knowledge. That is right for "a coffee in
    Seoul is about 5,000 won" and wrong for this, from an unseen Korean
    arc with no search behind it:

        서울에 있는 '김치찌개 전문점'에서 10,000원에 먹을 수 있습니다.

    The restaurant and the price were both invented, and the person is
    being told where to go and what it costs.
    """

    INVENTED = (
        "간단한 음식이라면 김치찌개를 추천합니다. "
        "서울에 있는 '김치찌개 전문점'에서 10,000원에 먹을 수 있습니다."
    )

    def test_it_fires_with_nothing_looked_up(self):
        self.assertTrue(
            GroundedValueGuard.needs_correction(
                self.INVENTED, evidence="", action_performed=False,
                grounded_subject=False,
            )
        )

    def test_the_repair_keeps_the_dish_and_drops_the_restaurant(self):
        repaired = GroundedValueGuard.correct_values(
            self.INVENTED, evidence="", offer="확인해 보지 않았습니다.",
        )
        self.assertIn("김치찌개를 추천합니다", repaired)
        self.assertNotIn("10,000", repaired)
        self.assertNotIn("전문점", repaired)

    def test_both_halves_are_required(self):
        """A quoted name alone is often a film; a price alone is chat."""
        for said in (
            "김치찌개는 보통 8,000원 정도입니다.",
            "'기생충'은 좋은 영화입니다.",
            "A coffee in Seoul is about 5,000 won.",
            "오늘 저녁은 '떡볶이' 어떠세요?",
        ):
            with self.subTest(said=said):
                self.assertFalse(grounded_values._prices_a_named_place(said))

    def test_it_reads_english_quotes_too(self):
        self.assertTrue(
            grounded_values._prices_a_named_place(
                'Try the "Kimchi Stew House" in Seoul, about 10,000 won.'
            )
        )


class KoreanFollowUpsHadNoSubjectHintTests(unittest.TestCase):
    """A3's follow-up hint had never once fired in Korean.

    ``DEICTIC_REFERENCE`` is what tells the prompt that a subjectless turn
    is about the thing just discussed -- the fix that stopped "which one
    would you choose?" being answered about graphics cards. Korean carries
    the same job on 그건 / 그 중에 / 번째, none of which look like the
    English shapes, so every Korean follow-up reached the model with
    nothing saying what it was about:

        you: 그럼 영화는?   her: 영화에 대해 말씀해 주십시오.
        you: 그건 봤어      her: 영화는 봤습니다. 어떤 영화였어요?
    """

    def test_korean_demonstratives_are_follow_ups(self):
        for said in (
            "그건 봤어", "그럼 영화는?", "그건 어때?", "저건 별로야",
            "그 중에 뭐가 나아?", "두 번째 걸로", "어느 게 나아?",
            "둘 중에 뭐가 좋아?",
        ):
            with self.subTest(said=said):
                self.assertTrue(DEICTIC_REFERENCE.search(said), said)

    def test_ordinary_korean_is_not_a_follow_up(self):
        """The cost of a false positive is a subject hint that misleads."""
        for said in (
            "안녕", "오늘 좀 힘들었어", "저녁 뭐 먹지?",
            "회사에서 회의만 했어", "고마워 잘자", "영화 추천해줘",
        ):
            with self.subTest(said=said):
                self.assertIsNone(DEICTIC_REFERENCE.search(said), said)

    def test_english_is_unchanged(self):
        self.assertTrue(DEICTIC_REFERENCE.search("which one would you choose?"))
        self.assertTrue(DEICTIC_REFERENCE.search("the second one"))
        self.assertIsNone(DEICTIC_REFERENCE.search("what is 2+2"))


class TheExtractorDoesNotOverruleTheGateTests(unittest.TestCase):
    """The last model boolean in the memory path.

    A7 replaced the two above it and stopped one layer short. Measured on
    an unseen turn: "i'm vegetarian by the way" opened the gate, started
    the store, and was dropped by the extractor -- the database afterwards
    held "The user is doing well." and not the diet. She then said "I
    don't have anything saved about that", which was honest and wrong.
    """

    def test_the_gate_recognises_the_sentence_that_was_dropped(self):
        self.assertTrue(
            memory_gate.carries_something_to_remember("i'm vegetarian by the way")
        )

    def test_the_engine_keeps_it_when_the_extractor_declines(self):
        import inspect

        from brain.chat_engine import ChatEngine

        source = inspect.getsource(ChatEngine._store_memory_candidate)
        self.assertIn("carries_something_to_remember", source)
        # The refusal alone must no longer end the store.
        self.assertNotIn('if not memory["save"]:\n                    return', source)


if __name__ == "__main__":
    unittest.main()
