"""A ranking is a record, and a record needs a source.

From a replay of a real Korean session. "한국에서 가장 많이 보는 드라마는
뭐야" was classified as stable knowledge and answered from memory -- with
'응답하라 1997' in the original session and with '도깨비' in the replay. Two
runs, two different wrong dramas: that is recall noise, not a fact she has.
The turns after it were spent defending the answer, ending in an invented
viewer count.

The model calls these stable because the answer sounds like trivia. What
makes it a record is that it is a count somebody measured -- ratings,
sales, views, box office -- so the factual source policy now refuses to
treat it as local knowledge.

The rule is deliberately about measured popularity and not about every
superlative. "The tallest mountain in the world" is settled; asserting that
it stays a direct answer is half of this file.
"""

from __future__ import annotations

import unittest

from brain.intent_router import (
    IntentDecision,
    SemanticIntentRouter,
    _ASKS_FOR_A_RECORD,
)


def _knowledge(request: str, *, query: str = "") -> IntentDecision:
    return IntentDecision(
        intent="knowledge_question",
        confidence=0.95,
        normalized_request=request,
        reason="stable fact",
        search_query=query,
        information_freshness="stable",
        requires_external_evidence=False,
    )


class WhatCountsAsARecordTests(unittest.TestCase):

    RECORDS = (
        "What is the most-watched drama in South Korea?",
        "best selling phone of 2025",
        "highest grossing Korean movie",
        "top rated anime",
        "most popular song in Korea right now",
        "한국에서 가장 많이 보는 드라마는 뭐야",
        "역대 흥행 1위 영화",
        "이 드라마 시청률 얼마야",
    )
    SETTLED = (
        "what is the capital of Korea",
        "who wrote Hamlet",
        "what is the tallest mountain in the world",
        "how do I make kimchi stew",
        "what does OLED mean",
        "가장 높은 산은 어디야",
        "김치찌개 만드는 법",
    )

    def test_records_are_recognised(self):
        for said in self.RECORDS:
            with self.subTest(said=said):
                self.assertTrue(_ASKS_FOR_A_RECORD.search(said))

    def test_settled_facts_are_not(self):
        for said in self.SETTLED:
            with self.subTest(said=said):
                self.assertIsNone(_ASKS_FOR_A_RECORD.search(said))


class ThePolicySendsARecordToASourceTests(unittest.TestCase):

    def test_a_ranking_the_model_called_stable_goes_to_web_search(self):
        # The router rewrites the Korean question into English before the
        # policy sees it, which is why the policy reads the paraphrase.
        decided = SemanticIntentRouter._apply_factual_source_policy(
            _knowledge("What is the most-watched drama in South Korea?"),
        )

        self.assertEqual(decided.intent, "web_search")
        self.assertTrue(decided.requires_external_evidence)

    def test_a_settled_superlative_stays_a_direct_answer(self):
        decided = SemanticIntentRouter._apply_factual_source_policy(
            _knowledge("What is the tallest mountain in the world?"),
        )

        self.assertEqual(decided.intent, "knowledge_question")


if __name__ == "__main__":
    unittest.main()
