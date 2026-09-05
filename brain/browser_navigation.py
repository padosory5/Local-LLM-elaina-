"""What happened after the browser was told to go somewhere.

``open_url`` returned ``url_opened`` and every layer above it read that as
"the page the person asked for is on their screen". It never meant that.
It meant the navigation command was accepted by Windows. Measured live,
session 7:

    You said: openZillow.com
    [Computer Control] action=open_url target=openZillow.com status=url_opened
    Elaina: All set, openZillow.com is open.
    You said: didn't open it.
    Elaina: Zillow.com is open.
    You said: the website is not opened on my browser.
    Elaina: I can't do that one.

Three claims, none of them checked, and the last one contradicts the
first two. ``openZillow.com`` is not a host anybody owns; nothing had
loaded at any point.

So the one status becomes a small lifecycle:

    requested -> dispatched -> observed -> verified | failed -> recovered

with the distinction that matters kept explicit: **dispatch is not
arrival**. Nothing here speaks; it reads what the browser reports and
says which of those states the navigation is in. The engine does the
looking, because the browser lives there.

Recovery never invents a domain. It has exactly two sources, and both are
things the conversation already said:

* a command verb fused onto the host by the transcriber -- "openZillow.com"
  is "open zillow.com" said quickly;
* the spellings between the one first asked for and the one just tried.
  "isss" corrected to "is" leaves "iss" untried, and that is a candidate
  the person supplied, not one we made up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

# Where a navigation can be. The names matter less than the line between
# the third and the fourth: everything above it is "we asked", everything
# below is "we looked".
DISPATCHED = "navigation_dispatched"
UNVERIFIED = "page_loaded_unverified"
VERIFIED = "target_verified"
RECOVERED = "recovered_target_verified"
WRONG_DESTINATION = "wrong_destination"
ERROR_PAGE = "error_page"
FAILED = "navigation_failed"
DISPUTED = "navigation_disputed"

# Only these may be spoken as "it is open".
ARRIVED = frozenset({VERIFIED, RECOVERED})

# Command verbs a transcriber runs into the host after them. Closed, and
# only ever used to *propose* an alternative that is then verified like
# any other -- never to rewrite a target before it has been tried.
_FUSED_VERB = re.compile(
    r"^(?:open|go\s*to|goto|visit|launch|start|browse|load)"
    r"(?=[A-Za-z0-9])",
    re.IGNORECASE,
)

# Browser error signatures in the title, URL, and bounded visible text.
_ERROR_TITLE = re.compile(
    r"\b(?:can'?t\s+be\s+reached|cannot\s+be\s+reached|site\s+can'?t|"
    r"not\s+found|no\s+such\s+host|server\s+not\s+found|"
    r"dns_probe|err_name_not_resolved|err_connection|"
    r"this\s+site\s+can|page\s+isn'?t\s+working|problem\s+loading|"
    r"connection\s+failed|찾을\s*수\s*없|연결할\s*수\s*없)\b",
    re.IGNORECASE,
)
_ERROR_URL = re.compile(
    r"^(?:chrome-error|edge-error|about:neterror|neterror)", re.IGNORECASE,
)
_INTERSTITIAL = re.compile(
    r"\b(?:your connection is not private|privacy error|security check|"
    r"checking your browser|verify you are human|access denied|"
    r"just a moment|captcha|domain (?:is )?for sale)\b", re.IGNORECASE,
)
_BLANK_URL = re.compile(
    r"^(?:about:blank|chrome://newtab|edge://newtab|about:newtab)",
    re.IGNORECASE,
)

# A search results page is not the site. Asking for zillow.com and being
# shown a Google search *for* "zillow.com" is the browser's fallback for
# something it could not treat as an address.
_SEARCH_HOSTS = (
    "google.", "bing.", "duckduckgo.", "search.yahoo.", "search.naver.",
    "yandex.",
)

# A whole string that is nothing but an address.
_ADDRESS_SHAPE = re.compile(r"[\w-]+(?:\.[\w-]+)+(?::\d+)?(?:/\S*)?")


def host_of(url: str) -> str:
    """The hostname a URL or bare address points at, normalised."""
    said = " ".join(str(url or "").split())
    if not said:
        return ""
    parsed = urlparse(said if "://" in said else f"https://{said}")
    host = (parsed.hostname or "").strip().rstrip(".").casefold()
    return host.removeprefix("www.")


def _labels(host: str) -> list[str]:
    return [label for label in str(host or "").split(".") if label]


def same_destination(expected: str, actual: str) -> bool:
    """Whether the page that loaded is the site that was asked for.

    A path, a locale prefix and a subdomain are all still the site: asking
    for zillow.com and landing on www.zillow.com/homes/for_rent is
    arriving. A different registrable name is not.
    """
    wanted, got = host_of(expected), host_of(actual)
    if not wanted or not got:
        return False
    if wanted == got:
        return True
    # A root may redirect into its subdomain. Losing a specifically named
    # subdomain does not prove arrival at that service (e.g. ISS -> UW home).
    return got.endswith(f".{wanted}")


def reads_as_error(url: str, title: str, text: str = "") -> bool:
    """Whether what loaded is the browser saying it could not load it."""
    address = " ".join(str(url or "").split())
    if _ERROR_URL.match(address):
        return True
    return bool(
        _ERROR_TITLE.search(str(title or ""))
        or _ERROR_TITLE.search(str(text or "")[:400])
    )


def _is_bare_address(title: str) -> bool:
    """Whether the title is just an address rather than a page's name.

    A page that rendered has a name: "NAVER", "International Student
    Services - ISS", "Zillow: Real Estate...". A browser that had nothing
    to show falls back to the address it was given.

    Measured live, session 8, four times in one run -- and every one of
    them was called ``target_verified``:

        requested: host.example        title: host.example
        requested: opennavier.com      title: opennavier.com
        requested: openzillow.com      title: openzillow.com
        requested: isss.washington.edu title: isss.washington.edu

    against the two that had really arrived:

        requested: naver.com           title: NAVER
        requested: iss.washington.edu  title: International Student
                                              Services - ISS
    """
    said = " ".join(str(title or "").split())
    if not said or " " in said:
        return False
    return bool(_ADDRESS_SHAPE.fullmatch(said.removeprefix("https://")
                                         .removeprefix("http://")
                                         .rstrip("/")))


def title_names_another_host(title: str, expected: str) -> bool:
    """Whether the page's own title says it belongs somewhere else.

    Measured live: ``zillow.com`` was requested, the address bar read
    ``zillow.com``, and the title read ``openzillow.com`` -- the page from
    the turn before. Two sources disagreeing about which page this is means
    neither of them has established it.
    """
    match = re.match(r"(?:https?://)?([\w-]+(?:\.[\w-]+)+)(?=[:/\s|]|$)", str(title).strip())
    return bool(match and not same_destination(expected, match.group(1)))


def reads_as_blank(url: str) -> bool:
    return bool(_BLANK_URL.match(" ".join(str(url or "").split())))


def reads_as_search_results(url: str, expected: str) -> bool:
    """Whether the browser searched for the address instead of going to it."""
    host = host_of(url)
    if not host or same_destination(expected, url):
        return False
    return any(host.startswith(name) or f".{name}" in f".{host}"
               for name in _SEARCH_HOSTS)


@dataclass(frozen=True)
class Navigation:
    """One attempt to go somewhere, and what became of it."""

    requested: str
    url: str
    status: str = DISPATCHED
    actual_url: str = ""
    title: str = ""
    detail: str = ""
    # Every address this navigation has been through, oldest first. It is
    # the only honest source of a recovery candidate: a spelling the
    # person themselves supplied or implied.
    history: tuple[str, ...] = field(default_factory=tuple)
    recovered_from: str = ""
    classification: str = "unobserved"
    observation_id: str = ""
    # Set only when interpretation established a fused command, never from
    # a hostname beginning with a verb by itself.
    command_fused: bool = False

    @property
    def expected_host(self) -> str:
        return host_of(self.url)

    @property
    def arrived(self) -> bool:
        return self.status in ARRIVED

    @property
    def checked(self) -> bool:
        """Whether anything actually looked."""
        return self.status not in {DISPATCHED, UNVERIFIED}

    def log_block(self) -> str:
        lines = [
            "[Navigation]",
            f"  requested: {self.requested or '(none)'}",
            f"  dispatched: {self.url or '(none)'}",
            f"  expected host: {self.expected_host or '(none)'}",
            f"  actual: {self.actual_url or '(not observed)'}",
        ]
        if self.title:
            lines.append(f"  title: {self.title[:60]}")
        lines.append(f"  status: {self.status}")
        lines.append(f"  observation: {self.observation_id or '(uncorrelated)'}")
        lines.append(f"  classification: {self.classification}")
        if self.recovered_from:
            lines.append(f"  recovered from: {self.recovered_from}")
        if self.detail:
            lines.append(f"  detail: {self.detail}")
        return "\n".join(lines)


def start(requested: str, url: str, *, history=(), command_fused=False) -> Navigation:
    """A navigation that has been dispatched and not yet looked at."""
    seen = tuple(dict.fromkeys((*history, url)))
    return Navigation(
        requested=str(requested or "").strip(),
        url=str(url or "").strip(),
        status=DISPATCHED,
        history=seen,
        command_fused=command_fused,
    )


@dataclass(frozen=True)
class PageEvidence:
    """A read of the exact page/window used by the navigation dispatcher."""

    url: str = ""
    title: str = ""
    text: str = ""
    identity: str = ""
    correlated: bool = False
    readable: bool = True
    error_code: str = ""
    http_status: int = 0
    document_url: str = ""


def verify(navigation: Navigation, tabs, *, before=()) -> Navigation:
    """Read the browser and say where this navigation actually got to.

    A matching hostname is necessary and nowhere near sufficient. Browsers
    keep the address you asked for in the bar through DNS failures, parked
    domains and error interstitials, so session 8 called four
    non-existent hosts ``target_verified`` on that evidence alone --
    including the one the recovery path was waiting for, which meant the
    recovery never ran.

    The observation is classified, and only one of the classes is
    arrival:

    * one correlated page with the requested host, its own title, readable
      body and no error signature -- **arrived**;
    * a browser error page, or a search *for* the address -- **error**;
    * the requested host with a title that names a different site --
      **wrong destination**, or a stale reading when the browser was
      already showing exactly that before the navigation;
    * the requested host with no page behind it -- **error** when there
      is nothing there at all, **unverified** when there is something and
      it cannot be judged;
    * a different host -- **wrong destination**;
    * nothing readable -- **unverified**, which is the honest one.

    Observations without dispatch correlation are ignored. ``before``
    additionally distinguishes stale screen readings from a changed page.
    """
    expected = navigation.expected_host
    if not expected:
        return replace(navigation, status=FAILED, detail="no host to check")

    rows = []
    for tab in tabs or ():
        url = str(getattr(tab, "url", "") or "")
        title = str(getattr(tab, "title", "") or "")
        text = str(getattr(tab, "text", "") or "")
        if (url or title) and getattr(tab, "correlated", False):
            rows.append(tab)
    if not rows:
        return replace(
            navigation, status=UNVERIFIED,
            detail="no observation correlated to this navigation",
        )

    if len(rows) != 1:
        return replace(navigation, status=UNVERIFIED, classification="ambiguous",
                       detail="more than one page claims the navigation")
    row = rows[0]
    url = str(getattr(row, "url", "") or "")
    title = str(getattr(row, "title", "") or "")
    text = str(getattr(row, "text", "") or "")
    matching = same_destination(navigation.url, url)

    seen_before = any(
        host_of(url) == host_of(str(row[0] if isinstance(row, tuple) else row.url))
        and title == str(row[1] if isinstance(row, tuple) else row.title)
        for row in (before or ())
        if isinstance(row, tuple) or hasattr(row, "url")
    ) if before else False

    landed = replace(navigation, actual_url=url, title=title,
                     observation_id=str(getattr(row, "identity", "") or ""))
    if not landed.observation_id or not getattr(row, "readable", True):
        return replace(landed, status=UNVERIFIED, classification="unreadable",
                       detail="the dispatched page could not be inspected")

    error_code = str(getattr(row, "error_code", "") or "")
    if reads_as_error(url, title, text) or error_code or getattr(row, "http_status", 0) >= 400:
        return replace(
            landed, status=ERROR_PAGE,
            classification=("dns_error" if re.search(r"dns|name_not_resolved|nxdomain", error_code + text, re.I)
                            else "connection_error"),
            detail="the browser reported it could not load that address",
        )
    if _INTERSTITIAL.search(title + " " + text[:1200]):
        return replace(landed, status=ERROR_PAGE, classification="interstitial",
                       detail="a browser or site interstitial prevents checking the destination")
    if reads_as_search_results(url, navigation.url):
        return replace(
            landed, status=ERROR_PAGE,
            classification="search_results",
            detail="the browser searched for the address instead of opening it",
        )
    if reads_as_blank(url):
        return replace(landed, status=FAILED, classification="blank", detail="the tab is blank")
    if not matching:
        return replace(
            landed, status=WRONG_DESTINATION,
            classification="wrong_destination",
            detail="a different page is showing",
        )

    # The address is right. Whether a page is behind it is a second
    # question, and it is the one session 8 never asked.
    document_url = str(getattr(row, "document_url", "") or "")
    if title_names_another_host(title, navigation.url) or (
        document_url and not same_destination(navigation.url, document_url)
    ):
        if seen_before:
            return replace(
                landed, status=UNVERIFIED,
                classification="stale_tab",
                detail=(
                    "the browser is still showing what it showed before, "
                    f"so this reading of {title} may be stale"
                ),
            )
        return replace(
            landed, status=WRONG_DESTINATION,
            classification="wrong_destination",
            detail=f"the address bar says {host_of(url)} and the page says {title}",
        )
    if _is_bare_address(title):
        if not text.strip():
            return replace(
                landed, status=ERROR_PAGE,
                classification="empty_destination",
                detail="the address is in the bar and no page is behind it",
            )
        return replace(
            landed, status=UNVERIFIED,
            classification="ambiguous",
            detail="the page has no name of its own, so it could not be checked",
        )

    if not title.strip() or not text.strip():
        return replace(landed, status=UNVERIFIED, classification="unreadable",
                       detail="an address and title alone do not establish page content")
    return replace(
        landed,
        status=RECOVERED if navigation.recovered_from else VERIFIED,
        classification="valid_destination",
    )


def unfused(url: str) -> str:
    """The address with a command verb the transcriber ran into it removed.

    "openZillow.com" is "open zillow.com" said quickly. Only proposed --
    a host that genuinely begins with "open" is a real thing, so this is a
    candidate to try after the original failed, never a rewrite before it.
    """
    said = " ".join(str(url or "").split())
    parsed = urlparse(said if "://" in said else f"https://{said}")
    host = (parsed.hostname or "").strip().rstrip(".")
    if not host:
        return ""
    match = _FUSED_VERB.match(host)
    if not match:
        return ""
    stripped = host[match.end():].lstrip(".")
    if len(_labels(stripped)) < 2 or len(stripped.split(".")[0]) < 2:
        return ""
    return stripped.casefold()


def _letter_runs(label: str, letter: str) -> list[re.Match]:
    return list(re.finditer(rf"{re.escape(letter)}+", label, re.IGNORECASE))


def spellings_between(first: str, tried: str) -> tuple[str, ...]:
    """The addresses between the one first asked for and the one just tried.

    Measured live: "isss.washington.edu" was corrected to "only one S",
    which is literally "is.washington.edu" -- and that host does not
    exist. The spelling the person actually wanted is between the two, and
    it is the only candidate here: three S's were said, one S was asked
    for, and two is what neither has tried.

    Nothing is invented. Every address returned differs from the two given
    only in the length of one run of one letter, in the site's own label.
    """
    start_host, tried_host = host_of(first), host_of(tried)
    if not start_host or not tried_host or start_host == tried_host:
        return ()
    start_labels, tried_labels = _labels(start_host), _labels(tried_host)
    if len(start_labels) != len(tried_labels):
        return ()
    # Only the first label -- the site's own name -- may differ.
    if start_labels[1:] != tried_labels[1:]:
        return ()
    was, now = start_labels[0], tried_labels[0]

    letters = {letter for letter in set(was) if letter.isalpha()}
    found: list[str] = []
    for letter in sorted(letters):
        was_runs = _letter_runs(was, letter)
        now_runs = _letter_runs(now, letter)
        if len(was_runs) != len(now_runs):
            continue
        differing = [
            (before, after)
            for before, after in zip(was_runs, now_runs)
            if len(before.group(0)) != len(after.group(0))
        ]
        if len(differing) != 1:
            continue
        before, after = differing[0]
        if was[:before.start()] != now[:after.start()] or was[before.end():] != now[after.end():]:
            continue
        low, high = sorted((len(before.group(0)), len(after.group(0))))
        for count in range(low + 1, high):
            rebuilt = (
                now[:after.start()] + letter * count + now[after.end():]
            )
            candidate = ".".join([rebuilt, *tried_labels[1:]])
            if candidate not in found:
                found.append(candidate)
    return tuple(found)


def recovery_candidates(navigation: Navigation) -> tuple[str, ...]:
    """Addresses worth trying instead, best first, or none.

    Both sources are things the conversation supplied. An empty result is
    the honest answer and means asking rather than guessing.
    """
    found: list[str] = []
    split = unfused(navigation.url) if navigation.command_fused else ""
    if split:
        found.append(split)
    if navigation.history:
        for candidate in spellings_between(
            navigation.history[0], navigation.url,
        ):
            if candidate not in found:
                found.append(candidate)
    seen_hosts = {host_of(url) for url in navigation.history}
    return tuple(
        candidate for candidate in found
        if host_of(candidate) not in seen_hosts
    )
