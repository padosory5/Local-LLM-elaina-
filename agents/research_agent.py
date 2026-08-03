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
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._search = search
        self._now = now

    def research(
        self,
        *,
        request: str,
        search_query: str,
        max_results: int = 5,
        verify: bool = False,
    ) -> ResearchResult:
        """
        Search the router's query and independently verify temporal answers.

        The second query is intentionally generated locally rather than by the
        answer model. This prevents stale model knowledge from deciding which
        edition, release, office holder, or other changing fact is current.
        """
        query = " ".join((search_query or request).split())
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

    def _verification_query(self, request: str, query: str) -> str:
        as_of = self._now().strftime("%Y-%m-%d")
        base = " ".join((request or query).split())
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
