import unittest
from datetime import datetime

from agents.research_agent import ResearchAgent


class ResearchAgentTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
