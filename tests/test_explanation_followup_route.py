"""An explanation is not about the present because it is asked in the present tense.

``_apply_factual_source_policy`` sends a knowledge question to web search
unless the router marked it stable, timeless and not needing evidence. The 8B
writes ``time_scope: current`` for most questions asked in the present tense
-- 58% of the follow-ups inside an explanation, where the 27B writes it for
under 1% -- and that one field was enough:

    아 근데 내가 가진 주식 수만큼 받는 거야?
        knowledge_question, explain, stable, current, no evidence asked for
        -> web_search ("Factual source policy requires external evidence ...")

Measured over every recorded turn (2026-10-11, 2,918 turns; the policy
replayed from the router's own output, 1,489 of 1,489 as recorded): it turned
541 of the 8B's 1,505 knowledge questions into searches, 239 of them on
``time_scope`` alone. 120 of those were follow-ups the router itself read as
explaining. Their 115 distinct questions were read by hand; not one needs a
lookup. Each search cost about seven seconds and changed nothing measurable in
the reply.

So on a follow-up read as explain or example, ``time_scope: current`` alone no
longer makes it a search. Everything else that does -- changing or unknown
freshness, the router asking for evidence, a record, the future, advice on
health, money or law -- still does, and a first question is routed as before.
"""

from __future__ import annotations

import unittest

from brain.intent_router import IntentDecision, SemanticIntentRouter


def _asked(request: str, **fields) -> IntentDecision:
    """A knowledge question as the 8B labels a follow-up inside an explanation."""
    base = dict(
        intent="knowledge_question", confidence=0.95, normalized_request=request,
        is_follow_up=True, answer_shape="explain", information_freshness="stable",
        requires_external_evidence=False, time_scope="current",
    )
    return IntentDecision(**{**base, **fields})


def _routed(decision: IntentDecision) -> IntentDecision:
    return SemanticIntentRouter._apply_factual_source_policy(decision)


class WhatChangedTests(unittest.TestCase):

    RECORDED = (
        "How much dividend do I receive based on the number of shares I own?",
        "why does using fewer bits make inference faster",
        "What are those numbers exactly? Are they learned through training?",
        "why does heat not escape even though sunlight enters?",
        "Does AI like ChatGPT always look up answers on the internet?",
        "What causes the moon to appear as a little slice every month?",
    )

    def test_a_follow_up_explanation_in_the_present_tense_stays_an_explanation(self):
        for request in self.RECORDED:
            with self.subTest(request=request):
                decided = _routed(_asked(request))
                self.assertEqual(decided.intent, "knowledge_question")
                self.assertFalse(decided.requires_external_evidence)
                self.assertEqual(decided.search_query, "")

    def test_the_same_for_a_follow_up_asking_for_an_example(self):
        self.assertEqual(
            _routed(_asked("Give an example of rounding a weight to 8 bits", answer_shape="example")).intent,
            "knowledge_question",
        )


class WhatDidNotTests(unittest.TestCase):

    def assertSearched(self, decision: IntentDecision) -> None:
        decided = _routed(decision)
        self.assertEqual(decided.intent, "web_search")
        self.assertTrue(decided.requires_external_evidence)
        self.assertTrue(decided.search_query)

    def test_a_first_question_is_routed_as_before(self):
        self.assertSearched(_asked("comparison of graphics card performance between 4070 and 4080",
                                   is_follow_up=False))

    def test_a_fact_about_the_present_still_goes_to_a_source(self):
        # "How much does it cost?" inside any thread: a state of the world, now.
        self.assertSearched(_asked("How much does the iPhone 18 Pro cost?", answer_shape="state"))
        self.assertSearched(_asked("Who is the CEO of the company?", answer_shape=""))

    def test_what_the_router_called_changing_still_goes(self):
        for freshness in ("changing", "live", "unknown"):
            with self.subTest(freshness=freshness):
                self.assertSearched(_asked("why did the stock market drop today?",
                                           information_freshness=freshness))

    def test_evidence_the_router_asked_for_is_still_fetched(self):
        self.assertSearched(_asked("why is the new policy controversial?", requires_external_evidence=True))

    def test_the_future_still_goes(self):
        self.assertSearched(_asked("how will interest rates change next year?", time_scope="future"))

    def test_a_record_still_goes(self):
        self.assertSearched(_asked("What is the most-watched drama in South Korea?"))

    def test_advice_on_health_money_or_law_still_goes(self):
        self.assertSearched(_asked("why should I take this medication with food?",
                                   recommendation_needed=True, advice_domain="health"))

    def test_a_timeless_explanation_was_never_searched(self):
        for follow_up in (True, False):
            with self.subTest(follow_up=follow_up):
                decided = _routed(_asked("why is the sky blue?", time_scope="timeless", is_follow_up=follow_up))
                self.assertEqual(decided.intent, "knowledge_question")


if __name__ == "__main__":
    unittest.main()
