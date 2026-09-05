"""Validated default-browser navigation for local computer actions."""

from __future__ import annotations

import ipaddress
import re
import webbrowser
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse, urlunparse

_DEFAULT_SEARCH_URL_TEMPLATE = "https://www.google.com/search?q={query}"

# What is left when a request names no subject: pronouns and determiners
# pointing at something said elsewhere, the prepositions and particles
# joining them, and the words for the surface itself. A target made only of
# these has nothing to look up.
#
# Measured live, three requests reached here with targets like these and
# all three reported success: "show it off on my browser" opened a blank
# tab and said "Sure, new tab opened", and "show me that on my browser"
# went hunting for a live element called "me that on my browser". Opening
# something empty is a worse answer than saying the request named nothing.
_NO_SUBJECT = frozenset({
    "it", "that", "this", "them", "those", "these", "one", "ones", "some",
    "me", "us", "him", "her", "there", "here", "thing", "things", "stuff",
    "a", "an", "the", "my", "your", "our", "their", "his", "its",
    "in", "on", "at", "to", "of", "for", "with", "from", "up", "off",
    "out", "over", "and", "or", "new", "another", "please", "now",
    "browser", "tab", "tabs", "window", "windows", "page", "pages",
    "screen", "site", "web", "internet", "google", "chrome", "search",
    "brave", "edge", "firefox",
})


@dataclass(frozen=True)
class BrowserResolution:
    status: str
    requested_target: str
    url: str = ""
    message: str = ""


class SafeBrowserControl:
    """Open only grounded HTTP(S) destinations through the configured opener."""

    def __init__(
        self,
        opener=None,
        *,
        allow_local_urls: bool = False,
        search_url_template: str = _DEFAULT_SEARCH_URL_TEMPLATE,
    ) -> None:
        self._opener = opener or webbrowser.open_new_tab
        self.allow_local_urls = bool(allow_local_urls)
        self.search_url_template = (
            str(search_url_template).strip() or _DEFAULT_SEARCH_URL_TEMPLATE
        )

    def resolve_search(self, query: str) -> BrowserResolution:
        """Build a search-engine URL for a spoken query -- never a model-
        invented address. The domain is fixed by local configuration; only
        the query text, always percent-encoded, comes from the request.
        """
        text = str(query).strip()
        if not text:
            return BrowserResolution(
                "invalid_target", text, message="A search query is required.",
            )
        if not self._names_a_subject(text):
            return BrowserResolution(
                "invalid_target",
                text,
                message=(
                    "That names the browser rather than something to look "
                    "up. What would you like me to search for?"
                ),
            )
        url = self.search_url_template.format(query=quote_plus(text))
        return BrowserResolution(
            "resolved", text, url=url, message=f"Ready to search for {text}.",
        )

    @staticmethod
    def _names_a_subject(text: str) -> bool:
        """Whether anything in this target is a thing to look up."""
        words = re.findall(r"[^\W_]+", str(text or ""), flags=re.UNICODE)
        return any(word.casefold() not in _NO_SUBJECT for word in words)

    def resolve(self, requested_target: str, proposed_url: str = "") -> BrowserResolution:
        target = str(requested_target).strip()
        candidate = str(proposed_url or requested_target).strip()
        if not target or not candidate:
            return BrowserResolution(
                "invalid_target",
                target,
                message="A website or URL is required.",
            )

        if "://" not in candidate:
            if not self._looks_like_host(candidate):
                return BrowserResolution(
                    "invalid_target",
                    target,
                    message=(
                        "Please include the website address, such as "
                        "youtube.com."
                    ),
                )
            candidate = f"https://{candidate}"

        parsed = urlparse(candidate)
        if parsed.scheme.casefold() not in {"http", "https"}:
            return BrowserResolution(
                "invalid_target",
                target,
                message="Only HTTP and HTTPS websites can be opened.",
            )
        if not parsed.hostname or parsed.username or parsed.password:
            return BrowserResolution(
                "invalid_target",
                target,
                message="That website address is not valid.",
            )
        try:
            port = parsed.port
        except ValueError:
            return BrowserResolution(
                "invalid_target",
                target,
                message="That website port is not valid.",
            )

        host = parsed.hostname.rstrip(".").casefold()
        if not self.allow_local_urls and self._is_local_host(host):
            return BrowserResolution(
                "blocked",
                target,
                message="Local and private network pages are disabled.",
            )

        # A model may expand "YouTube" to youtube.com, but it may not silently
        # substitute an unrelated destination. User-spoken hostnames are exact.
        if not self._destination_is_grounded(target, host):
            return BrowserResolution(
                "invalid_target",
                target,
                message="The website address does not match the request.",
            )

        netloc = host if port is None else f"{host}:{port}"
        normalized = urlunparse((
            parsed.scheme.casefold(),
            netloc,
            parsed.path or "",
            "",
            parsed.query or "",
            "",
        ))
        return BrowserResolution(
            "resolved",
            target,
            url=normalized,
            message=f"Ready to open {host}.",
        )

    def open(self, url: str):
        result = self._opener(str(url))
        if not result:
            raise OSError("Windows did not accept the browser tab request.")
        return result

    @staticmethod
    def _looks_like_host(value: str) -> bool:
        return bool(re.fullmatch(
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
            r"[A-Za-z]{2,63}(?::\d{1,5})?(?:/[^\s]*)?",
            value,
        ))

    @staticmethod
    def _is_local_host(host: str) -> bool:
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return True
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return False
        return not address.is_global

    @staticmethod
    def _destination_is_grounded(target: str, host: str) -> bool:
        target_host = urlparse(
            target if "://" in target else f"https://{target}"
        ).hostname
        if target_host and "." in target_host:
            expected = target_host.rstrip(".").casefold().removeprefix("www.")
            actual = host.removeprefix("www.")
            return expected == actual

        spoken = re.sub(r"[^a-z0-9]", "", target.casefold())
        host_labels = [
            re.sub(r"[^a-z0-9]", "", label)
            for label in host.split(".")
            if label.casefold() != "www"
        ]
        return bool(spoken and spoken in host_labels)
