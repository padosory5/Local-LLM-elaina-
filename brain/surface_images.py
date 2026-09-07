"""One picture per card, and never at the cost of the reply.

A shortlist of three hotels *reads* as three names. It *looks* like three
places. The protocol has carried an ``image`` field since 4F.6 and nothing
has ever filled it, because the search that finds candidates returns titles,
addresses and prose -- no pictures at all.

This fills it, under four rules.

**Never block the words.** Illustration runs after the reply has already
been generated and sent, so a slow image index costs the conversation
nothing. There is a hard deadline, and passing it means fewer pictures, not
a later answer.

**Every failure is silent.** No image search is worth a broken turn. Any
error, timeout or empty result leaves the field empty and the card renders
with its name alone.

**A picture is looked up by name, not by guesswork.** The query is the
candidate's own name -- the thing the search already found and the fit layer
already accepted. Nothing here decides *what* to show; that was decided
upstream, and this only puts a face on it.

**What comes back is untrusted.** An image URL is a value from a third-party
index, handled like every other untrusted field in the payload: http(s)
only, length-bounded, and validated again on the Electron side before it
ever reaches an ``<img>``.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from brain import response_surface as surfaces
from brain import surface_log


# A search index is not a fast dependency and not a reliable one. Both
# numbers are ceilings on damage rather than tuning: past the deadline the
# cards simply go out unillustrated.
DEADLINE = 6.0
WORKERS = 4
MAX_URL = 2048

_HTTP = re.compile(r"^https?://", re.IGNORECASE)

# Names repeat across turns -- "which one would you choose?" rebuilds the
# same shortlist -- and re-searching for a picture already in hand is pure
# latency. Bounded because a session can be long.
_CACHE: dict[str, str] = {}
_CACHE_LIMIT = 240


def _clean_link(value) -> str:
    url = " ".join(str(value or "").split())
    if not url or len(url) > MAX_URL:
        return ""
    return url if _HTTP.match(url) else ""


def _search_one(name: str) -> str:
    """One picture for one name, or an empty string.

    The thumbnail is preferred over the original deliberately: a card is
    about a hundred pixels wide, and the full-size image behind a search
    result is routinely a megabyte of photograph nobody will see at size.
    """
    key = name.casefold()
    if key in _CACHE:
        return _CACHE[key]
    found = ""
    try:
        from ddgs import DDGS

        for result in DDGS(timeout=5).images(name, max_results=2) or ():
            found = (
                _clean_link(result.get("thumbnail"))
                or _clean_link(result.get("image"))
            )
            if found:
                break
    except Exception:
        # Silent by design. See the module docstring: no picture is worth
        # a failed turn, and the caller has no better option than the name.
        found = ""
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = found
    return found


def illustrate(surface: "surfaces.Surface") -> "surfaces.Surface":
    """The same surface, with a picture on every card that could get one.

    Returns the surface unchanged when there is nothing to illustrate, when
    every item already has an image, or when the lookup fails -- so a caller
    can use this unconditionally and never has to ask whether it worked.
    """
    if not surface or not surface.items:
        return surface
    wanted = [
        item for item in surface.items
        if not item.image and item.name
    ]
    if not wanted:
        return surface

    pictures: dict[str, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            running = {
                item.id: pool.submit(_search_one, item.name)
                for item in wanted
            }
            for identity, task in running.items():
                try:
                    pictures[identity] = task.result(timeout=DEADLINE)
                except Exception:
                    pictures[identity] = ""
    except Exception:
        return surface

    items = tuple(
        replace(item, image=pictures[item.id])
        if pictures.get(item.id) else item
        for item in surface.items
    )
    found = sum(1 for item in items if item.image)
    surface_log.note(f"[Surface] illustrated {found}/{len(items)} card(s).")
    return replace(surface, items=items)
