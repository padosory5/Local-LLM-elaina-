from __future__ import annotations

from ddgs import DDGS


class WebSearchTool:
    def search_web(
        self,
        query: str,
        max_results: int = 3,
    ) -> str:
        """
        Search the web for current information.

        Args:
            query: A focused search query.
            max_results: Maximum number of results.

        Returns:
            Formatted web-search results.
        """
        query = query.strip()

        if not query:
            return "The search query was empty."

        max_results = max(1, min(max_results, 10))

        try:
            results = DDGS(timeout=10).text(
                query,
                max_results=max_results,
            )
        except Exception as error:
            return f"Web search failed: {error}"

        results = list(results)

        if not results:
            return "No useful web search results were found."

        formatted_results: list[str] = []

        for index, result in enumerate(results, start=1):
            title = result.get("title", "Untitled result")
            url = result.get("href", "")
            summary = result.get("body", "")

            formatted_results.append(
                f"""
            [{index}]

            {summary}
            """
            )

        return "\n\n".join(formatted_results)