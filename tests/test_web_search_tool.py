import unittest
from unittest.mock import patch

from tools.web_search import WebSearchTool


class SearchWebStructuredTests(unittest.TestCase):
    def test_returns_title_url_summary_per_result(self):
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            fake_ddgs.return_value.text.return_value = [
                {"title": "Ocean View Resort", "href": "https://a.example", "body": "$180/night"},
                {"title": "Guam Beach Hotel", "href": "https://b.example", "body": "$120/night"},
            ]

            results = tool.search_web_structured("hotels in Guam")

        self.assertEqual(
            results,
            [
                {"title": "Ocean View Resort", "url": "https://a.example", "summary": "$180/night"},
                {"title": "Guam Beach Hotel", "url": "https://b.example", "summary": "$120/night"},
            ],
        )

    def test_empty_query_returns_empty_list_without_calling_ddgs(self):
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            results = tool.search_web_structured("   ")

        self.assertEqual(results, [])
        fake_ddgs.assert_not_called()

    def test_no_results_returns_empty_list(self):
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            fake_ddgs.return_value.text.return_value = []

            results = tool.search_web_structured("nothing relevant")

        self.assertEqual(results, [])

    def test_backend_failure_raises(self):
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            fake_ddgs.return_value.text.side_effect = RuntimeError("network down")

            with self.assertRaises(RuntimeError):
                tool.search_web_structured("hotels in Guam")


class SearchWebTests(unittest.TestCase):
    """Regression coverage for the refactor -- search_web()'s existing
    human-readable behavior must be unchanged now that it's built from
    search_web_structured()."""

    def test_formats_results_as_before(self):
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            fake_ddgs.return_value.text.return_value = [
                {"title": "Ocean View Resort", "href": "https://a.example", "body": "$180/night"},
            ]

            result = tool.search_web("hotels in Guam")

        self.assertEqual(
            result,
            "[1] Ocean View Resort\nSource: https://a.example\nSnippet: $180/night",
        )

    def test_empty_query_message(self):
        tool = WebSearchTool()

        result = tool.search_web("   ")

        self.assertEqual(result, "The search query was empty.")

    def test_no_results_message(self):
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            fake_ddgs.return_value.text.return_value = []

            result = tool.search_web("nothing relevant")

        self.assertEqual(result, "No useful web search results were found.")

    def test_a_backend_failure_raises_instead_of_reading_like_evidence(self):
        """A4: a failure that comes back as a sentence gets used as research.

        This used to return "Web search failed: network down", which went
        into the prompt as the material for an answer and into
        ChatEngine's search cache as the material for every later answer
        to the same query. What kept the two apart was ResearchAgent
        matching the English prefix of that sentence -- so improving the
        wording would have silently turned failures into evidence.
        """
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            fake_ddgs.return_value.text.side_effect = RuntimeError("network down")

            with self.assertRaises(RuntimeError):
                tool.search_web("hotels in Guam")

    def test_no_results_is_still_an_answer_not_a_failure(self):
        """Finding nothing is a real outcome and stays a returned string."""
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            fake_ddgs.return_value.text.return_value = []

            self.assertEqual(
                tool.search_web("hotels in Guam"),
                "No useful web search results were found.",
            )

    def test_missing_url_and_summary_use_placeholders(self):
        tool = WebSearchTool()
        with patch("tools.web_search.DDGS") as fake_ddgs:
            fake_ddgs.return_value.text.return_value = [{"title": "Untitled result"}]

            result = tool.search_web("hotels in Guam")

        self.assertIn("(source URL unavailable)", result)
        self.assertIn("(no snippet available)", result)


if __name__ == "__main__":
    unittest.main()
