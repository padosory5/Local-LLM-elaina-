from __future__ import annotations

import time

from ddgs import DDGS
from ddgs.exceptions import DDGSException, TimeoutException

# The order the search engines are tried in: the first that returns results
# answers.
#
# Left to itself the library tries its general engines in a new random order
# on every call. On this machine two of them return anything, and which one a
# search drew decided what she said. Measured, 2026-10-04 (evals/search_order.py,
# docs/TURN_MOVE_AB.md): result sets led by Yahoo named the newest iPhone in 36
# of 37 searches, and those led by Yandex in 33 of 62 -- for a Korean sentence
# and for the dated official-source query, almost never. A Korean lookup makes
# exactly those two searches.
#
# So the same engines, in one order, Yahoo ahead of Yandex. Wikipedia and
# Grokipedia stay first, where the library's own order always has them. With
# Yahoo first it led 49% of 174 searches against 29%, and ordinary searches
# found the same things. On its own that was not a cure: Yahoo came back empty
# on about four calls in ten, and those ended at Yandex. See YAHOO_TRIES.
#
# DuckDuckGo is last, behind Yandex. It returned nothing in any of 164 calls
# over those runs and took ten seconds to do it; tried before the engine that
# answers, that was four of the five or six seconds an average search took.
ENGINE_ORDER = "wikipedia,grokipedia,yahoo,brave,google,startpage,mojeek,yandex,duckduckgo"

# How many times Yahoo is asked on its own, each time on a new connection,
# before the order above is tried.
#
# Its empty answers are not chance. The library gives every new connection a
# random browser on a random system to pose as, about four in ten of them a
# phone, and it cannot read the page Yahoo sends a phone. Measured, 2026-10-04:
# 0 of 26 connections posing as a phone got results, 34 of 34 posing as a
# desktop did. So the same connection asked again stays empty (0 of 12), and a
# new one, which draws again, usually answers (11 of 12).
#
# With three, Yahoo led 59 of 60 searches against 35, every iPhone search
# named the newest model against 37 of 50, and a search took 0.9 seconds on
# average against 1.75: a phone's page comes back in a fifth of a second. It
# costs requests, 91 to Yahoo for 60 searches.
YAHOO_TRIES = 3

# Seconds the library waits for an engine.
_TIMEOUT = 10

# The engine order with Yahoo left out, for the search in which Yahoo has just
# timed out. The order above asks it again, and waits the timeout out again:
# measured with Yahoo made unreachable, two waits where there had been one
# before Yahoo was asked first.
_ORDER_WITHOUT_YAHOO = ",".join(
    engine for engine in ENGINE_ORDER.split(",") if engine != "yahoo"
)


def _results(query: str, max_results: int) -> list[dict]:
    """What the library returns for one search: Yahoo's, when Yahoo answers."""
    order = ENGINE_ORDER
    for _attempt in range(YAHOO_TRIES):
        started = time.monotonic()
        try:
            # A new DDGS each time: that is what makes the connection, and
            # the browser it poses as, a new one.
            return DDGS(timeout=_TIMEOUT).text(
                query, max_results=max_results, backend="yahoo",
            )
        except DDGSException as failure:
            # A phone's page comes back at once, and is worth asking past.
            # An attempt that used up the timeout is Yahoo not answering at
            # all, and asking again would wait it out again.
            #
            # The clock is asked as well as the exception. The library waits
            # for an engine exactly as long as the engine waits for the
            # network, and when its own wait runs out first it reports "No
            # results found." like any empty answer (measured, 2026-10-07,
            # with Yahoo made unreachable).
            if (
                isinstance(failure, TimeoutException)
                or time.monotonic() - started >= _TIMEOUT - 1
            ):
                order = _ORDER_WITHOUT_YAHOO
                break
    return DDGS(timeout=_TIMEOUT).text(
        query, max_results=max_results, backend=order,
    )


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

        results = _results(query, max_results)

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
