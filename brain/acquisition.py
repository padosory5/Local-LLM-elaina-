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

CANDIDATE = "candidate"
SOURCE_SURFACE = "source_surface"
OFF_TARGET = "off_target"

# Publishing platforms. Checked before anything else, because the big
# surfaces host blogs on their own domains -- m.blog.naver.com is Naver and
# is not a map.
_PUBLISHING = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
    "pinterest.com", "pinterest.co.kr", "reddit.com", "quora.com",
    "medium.com", "wikipedia.org", "facebook.com", "x.com", "twitter.com",
    "brunch.co.kr", "tistory.com", "blogspot.com", "wordpress.com",
    "vimeo.com", "dailymotion.com", "substack.com",
)
_PUBLISHING_SUBDOMAIN = re.compile(
    r"^(?:m\.)?(?:blog|blogs|post|posts|news|magazine|cafe)\.", re.IGNORECASE,
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


def is_publishing(url: str) -> bool:
    """Whether this host publishes writing about things."""
    host = host_of(url)
    if _PUBLISHING_SUBDOMAIN.search(host):
        return True
    return any(host.endswith(platform) for platform in _PUBLISHING)


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
