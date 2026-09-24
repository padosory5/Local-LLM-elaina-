from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
        alternate_query: str = "",
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
        # The same question in the other language, when there is one.
        #
        # A Korean turn is searched on the person's own words, which finds
        # Korean pages and keeps proper nouns as they said them. It also
        # finds the *Korean web's* reading of the words: "시애틀에서
        # 인천공항까지 가는데 몇시간 걸려" came back as airport transit from
        # Seoul, the second search was the same sentence again with
        # "official source" in front, and she answered "서울에서 인천공항까지
        # 약 1시간 30분". The router's English query -- "travel time from
        # Seattle to Incheon Airport" -- was sitting unused.
        #
        # So the second search is the other language instead of a repeat.
        # It costs nothing extra when verifying, which already runs two, and
        # one search when not.
        other = " ".join(str(alternate_query or "").split())
        if other and self._in_another_script(query, other):
            if not query_is_resolved:
                other = self._localized(other)
            queries.append(
                self._verification_query(request, other) if verify else other
            )
        elif verify:
            verification_query = self._verification_query(request, query)
            if verification_query.casefold() != query.casefold():
                queries.append(verification_query)

        evidence_sections: list[str] = []
        successful_queries: list[str] = []
        errors: list[str] = []

        def searched(candidate: str):
            try:
                return str(self._search(candidate, max_results)).strip()
            except Exception as error:
                # The search backend fell over. It reaches the caller as a
                # failure rather than as a sentence that happens to start
                # with "Web search failed:", which is how this was detected
                # before and how it would have stopped being detected the
                # first time someone improved the wording.
                return error

        # Both searches at once. They are independent -- the person's own
        # words, and the same question in the other language or the
        # verification query -- and run one after the other the turn pays
        # for two round trips. Measured against the DuckDuckGo backend:
        # 1.4-3.1s each, which is most of what a search turn costs.
        if len(queries) > 1:
            with ThreadPoolExecutor(max_workers=len(queries)) as pool:
                outcomes = list(pool.map(searched, queries))
        else:
            outcomes = [searched(queries[0])]

        for index, (candidate, result) in enumerate(zip(queries, outcomes), start=1):
            if isinstance(result, Exception):
                errors.append(f"{type(result).__name__}: {result}")
                continue
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

    @staticmethod
    def _in_another_script(first: str, second: str) -> bool:
        """Whether one query is Korean and the other is not."""
        def korean(text: str) -> bool:
            return any("가" <= character <= "힣" for character in text)

        return korean(first) != korean(second)

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
