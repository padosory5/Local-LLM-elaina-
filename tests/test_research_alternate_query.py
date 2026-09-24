"""A Korean turn searches in Korean *and* in the router's English.

From a Korean session, with the search running on the person's own words:

    You:     시애틀에서 인천공항까지가는데 몇시간 걸려
    [Tool]   Searching web for: 시애틀에서 인천공항까지가는데 몇시간 걸려
    [Tool]   Searching web for: official source 시애틀에서 인천공항까지가는데 ...
    Elaina:  서울에서 인천공항까지 약 1시간 30분 정도 걸립니다.

The Korean web reads that sentence as airport transit from Seoul, and the
second search was the same sentence again. The router's English query,
"travel time from Seattle to Incheon Airport", was never searched. Now the
second search is the other language instead of a repeat.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from agents.research_agent import ResearchAgent


class _Recording:
    def __init__(self):
        self.queries: list[str] = []

    def __call__(self, query: str, max_results: int) -> str:
        self.queries.append(query)
        return f"results for {query}"


def _agent(search):
    return ResearchAgent(search, now=lambda: datetime(2026, 9, 10))


class TheOtherLanguageTests(unittest.TestCase):

    KOREAN = "시애틀에서 인천공항까지가는데 몇시간 걸려"
    ENGLISH = "travel time from Seattle to Incheon Airport"

    def test_verifying_searches_the_english_instead_of_repeating(self):
        search = _Recording()

        _agent(search).research(
            request=self.ENGLISH, search_query=self.KOREAN, verify=True,
            query_is_resolved=True, alternate_query=self.ENGLISH,
        )

        self.assertEqual(search.queries[0], self.KOREAN)
        self.assertEqual(len(search.queries), 2)
        self.assertIn(self.ENGLISH, search.queries[1])
        self.assertNotIn(self.KOREAN, search.queries[1])

    def test_not_verifying_still_searches_both(self):
        search = _Recording()

        _agent(search).research(
            request=self.ENGLISH, search_query=self.KOREAN, verify=False,
            query_is_resolved=True, alternate_query=self.ENGLISH,
        )

        self.assertEqual(search.queries, [self.KOREAN, self.ENGLISH])

    def test_an_alternate_in_the_same_language_changes_nothing(self):
        # Only the other language is new information. Two English
        # phrasings of one question is a repeat, and the old verification
        # query stands.
        search = _Recording()

        _agent(search).research(
            request=self.ENGLISH, search_query=self.ENGLISH, verify=True,
            query_is_resolved=True, alternate_query="Seattle Incheon flight",
        )

        self.assertEqual(len(search.queries), 2)
        self.assertTrue(search.queries[1].startswith("official source"))
        self.assertIn(self.ENGLISH, search.queries[1])

    def test_no_alternate_is_the_old_behaviour(self):
        search = _Recording()

        _agent(search).research(
            request=self.ENGLISH, search_query=self.ENGLISH, verify=False,
            query_is_resolved=True,
        )

        self.assertEqual(search.queries, [self.ENGLISH])


if __name__ == "__main__":
    unittest.main()
