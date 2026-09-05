from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ResearchResult:
    """Evidence collected for one current-fact question."""

    evidence: str
    queries: tuple[str, ...]


class ResearchAgent:
    """Run a small, evidence-first workflow for externally changing facts."""

    def __init__(
        self,
        search: Callable[[str, int], str],
        *,
        search_structured: Callable[[str, int], list[dict[str, str]]] | None = None,
        now: Callable[[], datetime] = datetime.now,
        locale: object | None = None,
    ) -> None:
        self._search = search
        self._search_structured = search_structured
        self._now = now
        # Optional (None keeps every existing caller and test unchanged).
        # When present, a query that names no place at all is searched in
        # the user's own market -- "second-hand phone marketplaces" should
        # not silently return US results for a user in Korea. A query that
        # already names a destination is never touched.
        self._locale = locale

    def _localized(self, query: str) -> str:
        if self._locale is None:
            return query
        try:
            return self._locale.localize_query(query)
        except Exception:
            return query

    def research(
        self,
        *,
        request: str,
        search_query: str,
        max_results: int = 5,
        verify: bool = False,
        query_is_resolved: bool = False,
    ) -> ResearchResult:
        """
        Search the router's query and independently verify temporal answers.

        The second query is intentionally generated locally rather than by the
        answer model. This prevents stale model knowledge from deciding which
        edition, release, office holder, or other changing fact is current.
        """
        query = " ".join((search_query or request).split())
        if not query_is_resolved:
            query = self._localized(query)
        if not query:
            raise RuntimeError("The research query was empty.")

        queries = [query]
        if verify:
            verification_query = self._verification_query(request, query)
            if verification_query.casefold() != query.casefold():
                queries.append(verification_query)

        evidence_sections: list[str] = []
        successful_queries: list[str] = []
        errors: list[str] = []

        for index, candidate in enumerate(queries, start=1):
            result = str(self._search(candidate, max_results)).strip()
            if self._is_failed_result(result):
                errors.append(result or "No results were returned.")
                continue

            successful_queries.append(candidate)
            evidence_sections.append(
                f"SEARCH {index}: {candidate}\n{result}"
            )

        if not evidence_sections:
            detail = "; ".join(errors) or "No useful evidence was returned."
            raise RuntimeError(detail)

        return ResearchResult(
            evidence="\n\n".join(evidence_sections),
            queries=tuple(successful_queries),
        )

    def research_structured(
        self,
        *,
        search_query: str,
        max_results: int = 5,
        query_is_resolved: bool = False,
    ) -> tuple[dict[str, str], ...]:
        """One search, returning raw per-result data (title/url/summary)
        rather than concatenated prose -- for a caller that needs real
        source attribution per item (a URL, not just an evidence blob),
        such as WebSearchActionPlanner populating ExtractedItem provenance.
        Deliberately simpler than research(): no verification-query
        doubling -- whether to escalate beyond a search at all is the task
        planner's own verification_level decision, not this method's job.
        """
        if self._search_structured is None:
            raise RuntimeError("Structured search is not available.")
        query = " ".join(str(search_query).split())
        if not query_is_resolved:
            query = self._localized(query)
        if not query:
            raise RuntimeError("The research query was empty.")
        results = self._search_structured(query, max_results)
        if not results:
            raise RuntimeError("No useful evidence was returned.")
        return tuple(results)

    def _verification_query(self, request: str, query: str) -> str:
        as_of = self._now().strftime("%Y-%m-%d")
        # The router query is deliberately self-contained and may have repaired
        # ambiguity in the original wording (for example, "recent World Cup"
        # becomes the latest completed FIFA men's tournament). Rebuilding the
        # verification search from the raw request throws that repair away and
        # can retrieve an unrelated national-team match instead.
        base = " ".join((query or request).split())
        return f"official source {base} as of {as_of}"

    @staticmethod
    def _is_failed_result(result: str) -> bool:
        normalized = result.strip().casefold()
        return (
            not normalized
            or normalized.startswith("web search failed:")
            or normalized.startswith("no useful web search results")
            or normalized.startswith("the search query was empty")
        )
