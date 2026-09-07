"""Where candidates come from, as distinct from the candidates themselves.

A directory, a map, a marketplace or a job board is often the best place in
a given market to find what someone is looking for -- and is never the
thing they were looking for. Naver Maps is how a great many people in Korea
actually find somewhere to eat; it is not a restaurant. Booking.com is not
a hotel, Coupang is not a guitar, Indeed is not a job.

The first version of this collapsed those two ideas and rejected the
surfaces along with the articles, which threw away the most useful result
on the page. So results are read as one of three things:

    CANDIDATE       an actual entity -- this restaurant, this listing
    SOURCE_SURFACE  somewhere entities can be found
    OFF_TARGET      writing or video *about* them

Only a CANDIDATE is ever ranked as a recommendation. A SOURCE_SURFACE is
kept, because it tells us where to look next and is worth offering when
nothing concrete was found.

What separates an entity from a surface is the shape of its URL, not its
host: the same site serves both.

    diningcode.com/profile.php?rid=qk74g6MEO1Vf   one restaurant
    diningcode.com/list.dc?query=서울+죽            a search over restaurants

That rule is market-independent, which is what keeps this generic. Which
*surfaces* are worth reaching for is market-dependent, and that judgement
already exists in brain/user_locale.py, keyed by category -- so it is asked
for rather than restated here. Nothing in this module names a site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CANDIDATE = "candidate"
SOURCE_SURFACE = "source_surface"
OFF_TARGET = "off_target"


@dataclass(frozen=True)
class SurfaceAcquisition:
    """Results proven to come from the selected source surface."""

    preferred: str = ""
    selected: str = ""
    results: tuple[dict[str, str], ...] = ()
    hosts: tuple[str, ...] = ()
    applied: bool = False
    fallback: str = ""
    why: str = ""

    def log_block(self, *, capability: str = "web_search") -> str:
        return "\n".join([
            "[Execution Selection]",
            f"  Required capability: {capability}",
            f"  Preferred provider/source: {self.preferred or '(none)'}",
            f"  Selected: {self.selected or '(none)'}",
            f"  Fallback: {self.fallback or '(none)'}",
            f"  Why: {self.why or '(none)'}",
        ])

# Publishing platforms. Checked before anything else, because the big
# surfaces host blogs on their own domains -- m.blog.naver.com is Naver and
# is not a map.
_PUBLISHING = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
    "pinterest.com", "pinterest.co.kr", "reddit.com", "quora.com",
    "medium.com", "wikipedia.org", "facebook.com", "x.com", "twitter.com",
    "brunch.co.kr", "tistory.com", "blogspot.com", "wordpress.com",
    "vimeo.com", "dailymotion.com", "substack.com",
    # Measured live during the entity-acquisition work: four separate
    # verification searches landed on grokipedia pages, which are the same
    # kind of thing as the wikipedia entry two lines up.
    "grokipedia.com", "linkedin.com", "threads.net",
)
_PUBLISHING_SUBDOMAIN = re.compile(
    r"^(?:m\.)?(?:blog|blogs|post|posts|news|magazine|cafe)\.", re.IGNORECASE,
)

# Reference works. They publish writing *about* words, which is the same
# thing a blog does about a product, and never something anyone can buy or
# visit. Wikipedia was already above; the dictionaries were not, and one of
# them was ranked as a real candidate. Measured live, on a turn asking for a
# monitor:
#
#     [Recommendation Reasoning] Candidates: 4 (0 fit, 3 unchecked)
#     [Grounding Guard] Naming what was found: GOOD Definition & Meaning -
#                       Merriam-Webster
#     Elaina: The one I actually found is GOOD Definition & Meaning,
#             Merriam-Webster.
#
# The degenerate query behind it is fixed in brain/recommendation_state.py.
# This is the second line of defence: whatever the query, a dictionary entry
# is never the thing.
# Deliberately no market-specific sites: a local dictionary is still market
# knowledge, and that lives in brain/user_locale.py. The host list is the
# international reference works; the URL shape below is what catches the
# rest of them in any market, which is the same trade this module already
# makes for surfaces and entities.
_REFERENCE = (
    "merriam-webster.com", "dictionary.com", "thesaurus.com",
    "britannica.com", "wiktionary.org", "wordreference.com",
    "collinsdictionary.com", "oxfordlearnersdictionaries.com",
    "vocabulary.com",
)
_REFERENCE_URL = re.compile(
    r"/(?:dictionary|thesaurus|define|definition|meaning)s?[/._?]"
    r"|\bdict\.",
    re.IGNORECASE,
)

# One thing, identified. A path segment naming a single record, or an id in
# the query string.
_ENTITY_URL = re.compile(
    r"/(?:profile|place|places|restaurant|store|shop|product|products|"
    r"item|items|listing|listings|detail|details|goods|dp|biz|"
    r"entry|venue|hotel|job|jobs|course|event)s?[/._?]"
    r"|[?&](?:rid|pid|id|pcode|prodno|code|no|seq|placeid|entry|itemid)="
    r"|/[A-Za-z]{1,3}/\d{3,}"
    r"|/\d{5,}(?:[/?#]|$)",
    re.IGNORECASE,
)

# A way of looking, rather than a thing looked at.
_SURFACE_URL = re.compile(
    r"/(?:list|lists|search|searches|category|categories|browse|results|"
    r"find|explore|directory|ranking|top)\b"
    r"|[?&](?:q|query|keyword|kw|search|searchword)=",
    re.IGNORECASE,
)


def host_of(url: str) -> str:
    match = re.search(r"https?://([^/]+)", str(url or ""), re.IGNORECASE)
    host = (match.group(1) if match else "").casefold()
    return host[4:] if host.startswith("www.") else host


def same_site_host(host: str, allowed: str) -> bool:
    """Whether two hosts belong to the same registrable site family.

    Used only after navigation began from a verified selected-source URL, so
    a legitimate first-party redirect can be read without broadening search
    result attribution. Common country-code second-level suffixes retain one
    extra label (``example.co.kr`` rather than the meaningless ``co.kr``).
    """
    host = str(host or "").casefold().strip(".")
    allowed = str(allowed or "").casefold().strip(".")
    if not host or not allowed:
        return False
    if host == allowed or host.endswith(f".{allowed}"):
        return True

    def root(value: str) -> str:
        parts = value.split(".")
        width = 3 if (
            len(parts) >= 3
            and len(parts[-1]) == 2
            and parts[-2] in {"co", "com", "net", "org", "ac"}
        ) else 2
        return ".".join(parts[-width:]) if len(parts) >= width else value

    return root(host) == root(allowed)


def is_publishing(url: str) -> bool:
    """Whether this host publishes writing about things."""
    host = host_of(url)
    if _PUBLISHING_SUBDOMAIN.search(host):
        return True
    if any(host.endswith(platform) for platform in _REFERENCE):
        return True
    if _REFERENCE_URL.search(str(url or "")):
        return True
    return any(host.endswith(platform) for platform in _PUBLISHING)


def _is_front_page(url: str) -> bool:
    """Whether this address is a site's door rather than a room in it."""
    without_scheme = re.sub(r"^https?://", "", str(url or ""), flags=re.I)
    path = without_scheme.split("?")[0].split("#")[0]
    _, _, tail = path.partition("/")
    return not tail.strip("/")


# Click trackers and ad redirects. The address belongs to the network that
# sold the placement, not to the thing at the other end -- measured live, a
# verification search for a real hotel returned bing.com/aclick?ld=... and
# would have put that on the card.
_REDIRECT = re.compile(
    r"/aclick\?|/y\.js\?|googleadservices\.|doubleclick\.|"
    r"/url\?q=|utm_medium=cpc",
    re.IGNORECASE,
)


def is_a_redirect(url: str) -> bool:
    """Whether this address only points at the thing, rather than being it."""
    return bool(_REDIRECT.search(str(url or "")))


def classify(url: str, *, surface_hosts=()) -> str:
    """Read a result as a candidate, a place to find candidates, or neither.

    ``surface_hosts`` is what the locale layer says serves this category in
    this market. A host on that list is never dismissed: at worst it is a
    surface, which is a useful thing to have found.
    """
    url = str(url or "")
    if not url:
        return CANDIDATE
    if is_publishing(url):
        return OFF_TARGET
    if is_a_redirect(url):
        return OFF_TARGET
    host = host_of(url)
    known = any(
        host == known_host or host.endswith(f".{known_host}")
        for known_host in (surface_hosts or ())
    )
    # An identified record wins over a search path, because a URL can carry
    # both ("/profile.php?rid=..&query=..").
    if _ENTITY_URL.search(url):
        return CANDIDATE
    if _SURFACE_URL.search(url) or known:
        return SOURCE_SURFACE
    if _is_front_page(url):
        # Nothing is *at* a domain root. A site's front door is a way of
        # finding what it sells, never one of the things it sells, and this
        # is where the aggregators were coming in: measured live,
        # hotelscombined.com and keychron.com were both classified as
        # things to recommend, and both reached the shortlist.
        return SOURCE_SURFACE
    return CANDIDATE


def surface_names(locale, category: str, goal: str = "") -> tuple[str, ...]:
    """The surfaces worth reaching for, in this market, for this category.

    Ranked and chosen by the locale layer, which is where market knowledge
    belongs. An unlisted category or an unserved market gives nothing back,
    and the caller searches plainly -- which is right, because a wrong
    market's directory is worse than no directory.
    """
    category = str(category or "").strip()
    if not category or locale is None:
        return ()
    try:
        sites, _market = locale.sites_for_goal(category, goal)
    except Exception:
        return ()
    return tuple(sites)


def surface_hosts(locale, category: str, goal: str = "") -> tuple[str, ...]:
    """The same surfaces, as hosts, for recognising them in results."""
    category = str(category or "").strip()
    if not category or locale is None:
        return ()
    try:
        return tuple(locale.source_hosts_for_goal(category, goal))
    except Exception:
        return ()


def hosts_for_source(preferred: str, known_hosts=()) -> tuple[str, ...]:
    """Locale-known hosts whose name identifies ``preferred``.

    This is deliberately lexical and generic: a multi-word source label is
    matched to a locale-ranked host using its distinctive words. If the
    locale does not know a matching host, nothing is guessed.
    """
    words = tuple(
        word for word in re.findall(r"[a-z0-9가-힣]+", str(preferred).casefold())
        if word not in {"app", "apps", "map", "maps", "music", "the"}
    )
    if not words:
        return ()
    return tuple(
        str(host).casefold() for host in known_hosts
        if any(word in str(host).casefold() for word in words)
    )


def select_surface_results(
    results,
    preferred: str,
    *,
    known_hosts=(),
) -> SurfaceAcquisition:
    """Keep only results demonstrably belonging to ``preferred``.

    A source name in a general query is only a hint.  This function turns it
    into an execution boundary: first discover the source's host from actual
    result URLs (constrained by locale hosts when available), then retain only
    pages on that host.  Concrete entity pages remain candidates; search/list
    pages remain source surfaces and can never be ranked as recommendations.
    """
    preferred = " ".join(str(preferred or "").split()).strip()
    raw = tuple(item for item in (results or ()) if isinstance(item, dict))
    if not preferred:
        return SurfaceAcquisition(
            results=raw, applied=False, fallback="ordinary acquisition",
            why="no preferred source was resolved",
        )

    words = tuple(
        word for word in re.findall(r"[a-z0-9가-힣]+", preferred.casefold())
        if word not in {"app", "apps", "map", "maps", "music", "the"}
    )
    allowed = tuple(str(host).casefold() for host in known_hosts if str(host))
    discovered: list[str] = []
    for item in raw:
        host = host_of(item.get("url", ""))
        if not host:
            continue
        if allowed and not any(
            host == candidate or host.endswith(f".{candidate}")
            for candidate in allowed
        ):
            continue
        haystack = " ".join((
            str(item.get("title", "")), host, str(item.get("url", "")),
        )).casefold()
        if not words or any(word in haystack for word in words):
            discovered.append(host)

    hosts = tuple(dict.fromkeys(discovered))
    selected = tuple(
        item for item in raw
        if (host := host_of(item.get("url", "")))
        and any(host == source or host.endswith(f".{source}") for source in hosts)
    )
    if not selected:
        return SurfaceAcquisition(
            preferred=preferred,
            results=(),
            hosts=hosts,
            applied=False,
            fallback="ordinary acquisition",
            why="the preferred source returned no attributable pages",
        )
    return SurfaceAcquisition(
        preferred=preferred,
        selected=preferred,
        results=selected,
        hosts=hosts,
        applied=True,
        why="returned pages are on the selected source surface",
    )
