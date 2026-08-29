import unittest
from datetime import datetime

from agents.research_agent import ResearchAgent
from brain.user_locale import UserLocale


class ResearchAgentTests(unittest.TestCase):
    def test_global_world_cup_query_is_not_localized_to_korea(self):
        calls = []

        agent = ResearchAgent(
            lambda query, max_results: calls.append(query) or "Evidence",
            locale=UserLocale(country="KR", city="Seoul"),
        )
        agent.research(
            request="Who won the recent World Cup?",
            search_query="latest completed FIFA Men's World Cup champion",
            verify=True,
        )

        self.assertEqual(len(calls), 2)
        self.assertTrue(all("FIFA Men's World Cup" in query for query in calls))
        self.assertTrue(all("South Korea" not in query for query in calls))
        self.assertIn("official source", calls[1])

    def test_temporal_winner_uses_independent_verification_query(self):
        calls = []

        def search(query, max_results):
            calls.append((query, max_results))
            return f"Evidence for {query}"

        agent = ResearchAgent(
            search,
            now=lambda: datetime(2026, 8, 3),
        )
        result = agent.research(
            request="Who won the latest FIFA World Cup?",
            search_query=(
                "latest completed edition FIFA World Cup winner "
                "as of 2026-08-03"
            ),
            verify=True,
        )

        self.assertEqual(len(calls), 2)
        self.assertIn("official source", calls[1][0])
        self.assertIn("2026-08-03", calls[1][0])
        self.assertEqual(len(result.queries), 2)

    def test_stable_lookup_uses_only_router_query(self):
        calls = []

        def search(query, max_results):
            calls.append((query, max_results))
            return "Useful evidence"

        agent = ResearchAgent(search)
        result = agent.research(
            request="Where is the Eiffel Tower?",
            search_query="Eiffel Tower location",
        )

        self.assertEqual(calls, [("Eiffel Tower location", 5)])
        self.assertEqual(result.queries, ("Eiffel Tower location",))

    def test_failed_primary_search_can_use_verification_evidence(self):
        calls = []

        def search(query, max_results):
            calls.append(query)
            if len(calls) == 1:
                return "No useful web search results were found."
            return "Official release evidence"

        agent = ResearchAgent(
            search,
            now=lambda: datetime(2026, 8, 3),
        )
        result = agent.research(
            request="When was the latest Qwen model released?",
            search_query="latest Qwen model release",
            verify=True,
        )

        self.assertEqual(result.evidence.count("SEARCH"), 1)
        self.assertIn("Official release evidence", result.evidence)


class ResearchStructuredTests(unittest.TestCase):
    def test_returns_raw_per_result_data(self):
        def search(query, max_results):
            return "unused"

        def search_structured(query, max_results):
            return [
                {"title": "Ocean View Resort", "url": "https://a.example", "summary": "$180/night"},
            ]

        agent = ResearchAgent(search, search_structured=search_structured)

        result = agent.research_structured(search_query="hotels in Guam")

        self.assertEqual(
            result,
            ({"title": "Ocean View Resort", "url": "https://a.example", "summary": "$180/night"},),
        )

    def test_raises_when_no_structured_search_was_provided(self):
        agent = ResearchAgent(lambda query, max_results: "unused")

        with self.assertRaises(RuntimeError):
            agent.research_structured(search_query="hotels in Guam")

    def test_raises_on_empty_query(self):
        agent = ResearchAgent(
            lambda query, max_results: "unused",
            search_structured=lambda query, max_results: [],
        )

        with self.assertRaises(RuntimeError):
            agent.research_structured(search_query="   ")

    def test_raises_when_no_results_found(self):
        agent = ResearchAgent(
            lambda query, max_results: "unused",
            search_structured=lambda query, max_results: [],
        )

        with self.assertRaises(RuntimeError):
            agent.research_structured(search_query="hotels in Guam")

    def test_does_not_double_the_query_unlike_research(self):
        calls = []

        def search_structured(query, max_results):
            calls.append(query)
            return [{"title": "A", "url": "https://a.example", "summary": "..."}]

        agent = ResearchAgent(
            lambda query, max_results: "unused",
            search_structured=search_structured,
        )

        agent.research_structured(search_query="hotels in Guam")

        self.assertEqual(calls, ["hotels in Guam"])


if __name__ == "__main__":
    unittest.main()
