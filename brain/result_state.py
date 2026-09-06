"""What a search found, kept in a shape the next turn can point at.

Until now a result set was eight strings. ``record_candidates`` took the
names off the ranked fits and threw the rest away -- the URL, the reason the
fit layer had just worked out, the ranking, the verdict. Everything a later
turn would need to act on one of them was computed and discarded in the same
breath, so:

* "open the second one" had a position to count to and nothing to open;
* "compare the first and third" had nothing to compare;
* the entity guard could name a result but never link to it;
* and "which one would you choose?" could only be answered by asking the
  model to re-read a prose blob of the shortlist.

The fix is not a bigger blob. It is that a result has an **identity** --
something stable that is not its label. Phase 4E is where that lesson comes
from: a page's visible text and the thing it actually is came apart
repeatedly, and every bug that followed was some version of trusting the
label. So a candidate is identified by its URL where it has one, and only
falls back to its name when it does not.

Deliberately domain-neutral. A candidate here is a monitor, a hotel, a
restaurant, an article, a file or a page; nothing in this module knows which,
because the moment it does, the next domain needs a second copy of it.

``Candidate`` stringifies to its name, so every caller that treated the old
tuple of strings as strings -- the logs, the reference resolver, the
grounding guards -- keeps working against the richer record without knowing
it changed.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field, replace


# How long a result set stays answerable. Long enough to talk it over, short
# enough that "open the second one" an hour later is not resolved against a
# list nobody remembers listing.
DEFAULT_TTL_SECONDS = 900

# What kind of thing the set holds. Named rather than inferred, and open:
# a caller that knows passes it, and one that does not passes nothing.
PRODUCT = "product"
PLACE = "place"
PAGE = "page"
ARTICLE = "article"
UNKNOWN = ""


def identity_of(name: str, url: str = "") -> str:
    """A stable id for this result, from what it *is* rather than what it says.

    The URL when there is one: two searches that return the same page return
    the same thing, however differently the title is written that day. Only
    when there is no URL does the name have to serve, and that is the weaker
    case rather than the normal one.

    Content-derived rather than a counter, so the same result keeps its id
    across a re-search and a later turn can still be talking about it.
    """
    basis = " ".join(str(url or "").split()).casefold()
    if not basis:
        basis = " ".join(str(name or "").split()).casefold()
    if not basis:
        return ""
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


@dataclass(frozen=True)
class Candidate:
    """One thing a search found, and what is known about it."""

    name: str
    url: str = ""
    summary: str = ""
    # Why the fit layer ranked it where it did, in its own words. Kept so a
    # later turn can say "because it is the only one under your budget"
    # without re-deriving the judgement.
    why: str = ""
    # FITS / UNCHECKED / MISMATCH / SOURCE / OFF-TARGET, as the fit layer
    # decided. A later turn must not promote an UNCHECKED into a fit.
    verdict: str = ""
    rank: int = 0
    attributes: tuple[str, ...] = ()
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", identity_of(self.name, self.url))

    def __str__(self) -> str:
        # Every caller that treated candidates as plain strings keeps working.
        return self.name

    @property
    def openable(self) -> bool:
        """Whether there is somewhere to actually go."""
        return bool(re.match(r"^https?://", str(self.url or "").strip()))

    def short_name(self, *, max_words: int = 7) -> str:
        """Enough of the title to recognise it, without the site's own name."""
        cleaned = " ".join(str(self.name or "").split())
        for separator in (" | ", " - ", " – ", " — ", " : "):
            head = cleaned.split(separator)[0].strip()
            if len(head.split()) >= 2:
                cleaned = head
        return " ".join(cleaned.split()[:max_words])


@dataclass(frozen=True)
class ResultSet:
    """The candidates in hand, in the order they were ranked."""

    items: tuple[Candidate, ...] = ()
    kind: str = UNKNOWN
    source: str = ""
    query: str = ""
    created_at: float = field(default_factory=time.monotonic)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            basis = "|".join(item.id for item in self.items)
            object.__setattr__(
                self, "id", identity_of(basis or self.query, ""),
            )

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def expired(self, *, ttl: int = DEFAULT_TTL_SECONDS, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        return moment - self.created_at >= ttl

    def at(self, index: int) -> Candidate | None:
        """The candidate at a position, or None when there is no such one.

        Never clamps. "The fifth one" against three results means the person
        is talking about something not in hand, and quietly handing back the
        third is how a wrong thing gets opened.
        """
        if not self.items:
            return None
        if index < 0:
            index = len(self.items) + index
        if 0 <= index < len(self.items):
            return self.items[index]
        return None

    def by_id(self, candidate_id: str) -> Candidate | None:
        wanted = str(candidate_id or "").strip()
        return next((item for item in self.items if item.id == wanted), None)

    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.items)

    def openable(self) -> tuple[Candidate, ...]:
        return tuple(item for item in self.items if item.openable)

    def reranked(self, order) -> "ResultSet":
        """The same candidates in a new order, keeping every identity.

        A refinement -- "anything cheaper?" -- re-ranks what is in hand. It
        does not produce different things, and giving them new ids would
        break every reference the conversation has already made to them.
        """
        by_id = {item.id: item for item in self.items}
        moved = [by_id[key] for key in order if key in by_id]
        moved += [item for item in self.items if item.id not in set(order)]
        return replace(
            self,
            items=tuple(
                replace(item, rank=index)
                for index, item in enumerate(moved)
            ),
        )

    def log_block(self) -> str:
        """Console only. What is in hand, and what could be opened."""
        if not self.items:
            return "[Results] (none)"
        lines = [f"[Results] {len(self.items)} in hand ({self.kind or 'unknown'})"]
        for item in self.items[:5]:
            mark = "*" if item.openable else " "
            lines.append(
                f"  {item.rank + 1}.{mark} [{item.id}] {item.short_name()[:44]}"
                + (f"  -- {item.why[:32]}" if item.why else "")
            )
        return "\n".join(lines)


def from_fits(fits, *, kind: str = UNKNOWN, source: str = "", query: str = "",
              limit: int = 8) -> ResultSet:
    """Build a result set from what the fit layer already worked out.

    Everything here was computed a moment ago and used to be thrown away:
    the URL that makes a candidate openable, the clause explaining the
    ranking, and the verdict that stops an unchecked result being promoted
    into a recommendation later.
    """
    items: list[Candidate] = []
    for rank, fit in enumerate(list(fits)[:limit]):
        name = str(getattr(fit, "name", "") or "").strip()
        if not name:
            continue
        why = ""
        try:
            why = str(fit.because() or "")
        except Exception:
            why = ""
        items.append(Candidate(
            name=name,
            url=str(getattr(fit, "url", "") or "").strip(),
            summary=str(getattr(fit, "summary", "") or "").strip(),
            why=why,
            verdict=str(getattr(fit, "verdict", "") or ""),
            rank=rank,
            attributes=tuple(getattr(fit, "matches", ()) or ()),
        ))
    return ResultSet(items=tuple(items), kind=kind, source=source, query=query)


def from_names(names, *, kind: str = UNKNOWN, source: str = "",
               query: str = "", limit: int = 8) -> ResultSet:
    """A result set from bare names, for callers that have nothing richer.

    The weaker case, and it says so: no URL means nothing to open, and the
    identity has to fall back to the label.
    """
    items = tuple(
        Candidate(name=cleaned, rank=rank)
        for rank, cleaned in enumerate(
            name for name in (
                " ".join(str(value).split()).strip() for value in (names or ())
            ) if name
        )
        if rank < limit
    )
    return ResultSet(items=items, kind=kind, source=source, query=query)
