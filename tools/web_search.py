from __future__ import annotations

from ddgs import DDGS


class WebSearchTool:
    def search_web_structured(
        self,
        query: str,
        max_results: int = 3,
    ) -> list[dict[str, str]]:
        """Search the web and return raw per-result data.

        Each item has title/url/summary. An empty query or a search that
        finds nothing both return an empty list -- only a genuine search-
        backend failure raises -- so a caller needing structured evidence
        (source, not just prose) doesn't have to string-match
        search_web()'s human-readable messages to tell them apart.
        """
        query = query.strip()

        if not query:
            return []

        max_results = max(1, min(max_results, 10))

        results = DDGS(timeout=10).text(query, max_results=max_results)

        return [
            {
                "title": result.get("title", "Untitled result"),
                "url": result.get("href", ""),
                "summary": result.get("body", ""),
            }
            for result in results
        ]

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

        # A backend failure raises, exactly as search_web_structured
        # documents, instead of being flattened into a sentence.
        #
        # It used to come back as "Web search failed: <exception>", and two
        # things went wrong with that. The string was handed to the model
        # as research evidence, so a network blip became the material an
        # answer was built from; and ChatEngine.search_web cached whatever
        # it got, so the blip was served as evidence for the rest of the
        # cache window. The only thing standing between the two was
        # ResearchAgent matching the English prefix of this sentence --
        # a guard any rewording disarms, which is the failure mode this
        # project has a standing rule against.
        results = self.search_web_structured(query, max_results)

        if not results:
            return "No useful web search results were found."

        formatted_results = [
            f"[{index}] {result['title']}\n"
            f"Source: {result['url'] or '(source URL unavailable)'}\n"
            f"Snippet: {result['summary'] or '(no snippet available)'}"
            for index, result in enumerate(results, start=1)
        ]

        return "\n\n".join(formatted_results)
