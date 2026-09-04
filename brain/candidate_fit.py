"""Checking what came back against what was actually asked for.

Getting the constraints into the search query is not the same as getting
them into the answer. Measured live, with an open recommendation holding
``electric`` and ``~500,000 won``:

    requested:  electric guitar, about 500,000 won
    returned:   "Yamaha APX500 Acoustic-Electric Guitar"
    said:       "You could try the Yamaha APX500..."

The query was right and the top recommendation was an acoustic guitar.
Nothing had compared the candidate to the constraint, so a result that
contained the word "electric" inside "acoustic-electric" read as a match.

Every check here is deterministic. Scoring "is this electric" or "is this
under 500,000 won" does not need a model, and asking one would add a
round-trip to a comparison that string and number handling settle exactly.
A hard conflict never wins: a candidate that contradicts a stated
constraint is ranked below every candidate that does not, and if it is
mentioned at all it is mentioned as the mismatch it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from brain import acquisition
from brain import recommendation_state as rs

# How far over a stated budget still counts as "around" it. A person who
# says 500,000 will look at 560,000 and will not thank you for 1,200,000.
_BUDGET_TOLERANCE = 1.25

_AMOUNT = re.compile(
    r"(?:(?P<currency>[$₩€£¥])\s?)?"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>won|krw|usd|eur|gbp|jpy|dollars?|euros?|pounds?|yen|원|만원)?",
    re.IGNORECASE,
)

# Words too common to mean anything as a match. "New" appears in half of
# all product titles.
_UNINFORMATIVE = frozenset({
    "new", "best", "good", "great", "top", "cheap", "quality", "the", "a",
})


def _amounts(text: str) -> tuple[float, ...]:
    """Every number in the text that could be a price, largest first."""
    found: list[float] = []
    for match in _AMOUNT.finditer(str(text or "")):
        # A bare four-digit number is commonly a street address, year,
        # square footage or model number. It is not price evidence without
        # a currency marker; measured live, "4733 21st Ave" was read as
        # $4,733 rent and rejected an otherwise valid UW-area property.
        if not match.group("currency") and not match.group("unit"):
            continue
        raw = match.group("number").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        # A "10,000원" is a price; a "2026" or a "500" model number is
        # usually not, but there is no way to tell them apart reliably, so
        # only clearly price-shaped magnitudes are considered.
        if value >= 1000:
            found.append(value)
    return tuple(sorted(found, reverse=True))


def _opposite_of(value: str) -> tuple[str, ...]:
    """The other kinds of the same thing, from the variants already known."""
    wanted = value.strip().casefold()
    others: list[str] = []
    for variants in rs._VARIANTS.values():
        lowered = [variant.casefold() for variant in variants]
        if wanted in lowered:
            others.extend(other for other in lowered if other != wanted)
    return tuple(dict.fromkeys(others))


@dataclass(frozen=True)
class Fit:
    """One candidate, read against the open problem."""

    name: str
    url: str = ""
    summary: str = ""
    matches: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    score: float = 0.0
    # Why this is not a candidate of the expected kind at all -- an article
    # about guitars rather than a guitar. Empty when it is one.
    shape_problem: str = ""
    # candidate / source_surface / off_target. A surface is somewhere
    # candidates can be found -- a map, a directory, a marketplace. It is
    # kept and never recommended: Naver Maps is how a great many people in
    # Korea find somewhere to eat, and is not a restaurant.
    kind: str = acquisition.CANDIDATE

    @property
    def rejected(self) -> bool:
        """Whether this contradicts something the person actually said."""
        return bool(self.conflicts)

    @property
    def verdict(self) -> str:
        """FITS, UNCHECKED or MISMATCH.

        The middle one matters. A restaurant listing rarely says "soft" in
        its title, so a search for soft food comes back with candidates
        that neither match nor contradict -- and calling those a fit is how
        a sore-throat dinner came back recommending Korean BBQ. Not knowing
        is its own answer and gets said out loud.
        """
        if self.kind == acquisition.SOURCE_SURFACE:
            return "SOURCE"
        if self.shape_problem or self.kind == acquisition.OFF_TARGET:
            # Both mean "writing about the thing". The reason may come from
            # the title (a round-up) or from the host (a blog platform), and
            # a blog carries no shape_problem of its own.
            return "OFF-TARGET"
        if self.conflicts:
            return "MISMATCH"
        return "FITS" if self.matches else "UNCHECKED"

    @property
    def viable(self) -> bool:
        """An actual candidate, of the right kind, contradicting nothing."""
        return (
            self.kind == acquisition.CANDIDATE
            and not self.shape_problem
            and not self.conflicts
        )

    @property
    def is_surface(self) -> bool:
        """Somewhere to find candidates, rather than one of them."""
        return self.kind == acquisition.SOURCE_SURFACE

    def because(self) -> str:
        """Why it fits, or why it does not -- in one short clause."""
        if self.is_surface:
            return "a place to search, not something to recommend"
        if self.kind == acquisition.OFF_TARGET and not self.shape_problem:
            return "writing about these, not one of them"
        if self.shape_problem:
            return self.shape_problem
        if self.conflicts:
            return f"conflicts with {', '.join(self.conflicts)}"
        if self.matches:
            return f"fits {', '.join(self.matches)}"
        return "nothing to check it against"


def _names(word: str, lowered: str) -> bool:
    """Whether the candidate says this word, plural or singular.

    A title says "Packing Peanuts" and another says "Peanut". Comparing
    them literally makes the second one an unchecked candidate rather
    than the wrong object, so the singular and the plural are the same
    word here.
    """
    if word in lowered:
        return True
    return bool(word.endswith("s") and len(word) > 3 and word[:-1] in lowered)


def _check(text: str, problem) -> tuple[list[str], list[str], list[str]]:
    matches: list[str] = []
    conflicts: list[str] = []
    unknown: list[str] = []
    lowered = f" {text.casefold()} "

    for value in problem.values(rs.ATTRIBUTE):
        wanted = value.casefold().strip()
        if not wanted or wanted in _UNINFORMATIVE:
            continue
        opposites = _opposite_of(wanted)
        # Checked before the positive match, and deliberately: an
        # "Acoustic-Electric" guitar contains "electric" and is not an
        # electric guitar. Naming the other kind is decisive whether or not
        # the requested word also appears.
        if any(other in lowered for other in opposites):
            conflicts.append(value)
        elif wanted in lowered:
            matches.append(value)
        else:
            unknown.append(value)

    for value in problem.values(rs.HOUSING_TYPE):
        wanted = value.casefold().strip()
        if not wanted:
            continue
        if wanted in lowered:
            matches.append(value)
            continue
        # A room or a multi-bedroom home is not an unchecked studio. These
        # are the concrete false positives exposed by the live Zillow result
        # page; treating them as unknown let them suppress the fallback.
        if wanted == "studio" and re.search(
            r"\b(?:room\s+(?:for\s+rent|in\b)|"
            r"[1-9]\d*[- ]?(?:bed|bedroom)s?\b)",
            lowered,
            re.IGNORECASE,
        ):
            conflicts.append(value)
        else:
            unknown.append(value)

    # What the thing actually is. This was the one dimension nothing
    # checked, and constraints were doing the whole job of ranking --
    # measured live, a search for packing peanuts in Korea recommended
    #
    #     Coffee Flavor Peanut, Korea price supplier - 21food
    #
    # chosen over "Biodegradable Packing Peanuts for sale", with the
    # reason "fits Korea". It did fit Korea. Constraints narrow a set;
    # they do not decide what is in it.
    #
    # A partial match on a compound name is a mismatch and not a weak
    # match, which is the whole point: "peanut" without "packing" is a
    # different object, and calling it unchecked is how it won.
    for value in problem.values(rs.PREFERENCE):
        wanted = [
            word for word in re.findall(r"[\w']+", value.casefold())
            if len(word) > 2 and word not in _UNINFORMATIVE
        ]
        if not wanted:
            continue
        present = [word for word in wanted if _names(word, lowered)]
        if len(present) == len(wanted):
            # No credit for being the right kind of thing. A page titled
            # "Guitars" names the subject and says nothing about electric
            # or about the budget, and counting the subject as a match
            # would make it a fit on that alone.
            continue
        if present:
            conflicts.append(f"not {value}")
        else:
            unknown.append(value)

    for value in problem.values(rs.EXCLUSION):
        if value.casefold().strip() in lowered:
            conflicts.append(f"excluded {value}")

    for value in problem.values(rs.BUDGET):
        limits = _amounts(value)
        prices = _amounts(text)
        if not limits:
            continue
        if not prices:
            unknown.append(value)
            continue
        # A range's upper endpoint is the ceiling. The old lower-endpoint
        # comparison rejected a $1,295 listing for a $1,000-$1,300 request.
        ceiling = (
            max(limits) if len(limits) > 1
            else limits[0] * _BUDGET_TOLERANCE
        )
        if min(prices) > ceiling:
            conflicts.append(f"over {value}")
        else:
            matches.append(value)

    for value in problem.values(rs.AREA):
        if value.casefold().strip() in lowered:
            matches.append(value)

    location = str(getattr(problem, "location", "") or "").strip()
    if location:
        if location.casefold() in lowered:
            matches.append(location)
        else:
            named_cities = tuple(
                match.group(1).strip() for match in _CITY_STATE.finditer(text)
            )
            if named_cities:
                conflicts.append(f"location {location}")
            else:
                unknown.append(location)

    return matches, conflicts, unknown


def evaluate(
    candidates, problem, *, shape: str = "", surface_hosts=(),
) -> tuple[Fit, ...]:
    """Rank what was found against what was asked for, best first.

    Candidates are dicts of title/url/summary as ``research_structured``
    returns them. A candidate that conflicts is kept rather than dropped --
    saying "not that one, because it is acoustic" is more useful than
    silently returning fewer results -- but it can never rank first.
    """
    wanted = shape or expected_shape(problem)
    fits: list[Fit] = []
    for candidate in candidates or ():
        if isinstance(candidate, dict):
            name = str(candidate.get("title", "")).strip()
            url = str(candidate.get("url", "")).strip()
            summary = str(candidate.get("summary", "")).strip()
        else:
            name, url, summary = str(candidate).strip(), "", ""
        if not name:
            continue
        matches, conflicts, unknown = _check(f"{name} {summary}", problem)
        score = len(matches) - 2.0 * len(conflicts) - 0.25 * len(unknown)
        kind = acquisition.classify(url, surface_hosts=surface_hosts)
        if kind == acquisition.CANDIDATE and _COLLECTION_TITLE.search(name):
            kind = acquisition.SOURCE_SURFACE
        problem_shape = (
            "" if kind != acquisition.CANDIDATE
            else off_target(name, url, summary, wanted)
        )
        if problem_shape:
            # A round-up on somebody's blog: writing about candidates, and
            # not a surface that can be searched for them either.
            kind = acquisition.OFF_TARGET
        if kind == acquisition.CANDIDATE and not looks_like(
            name, url, summary, wanted,
        ):
            # Nothing that a real one of these carries -- no price on a
            # product, no hours or rating on a place. Kept, ranked last.
            score -= 1.0
        fits.append(Fit(
            name=name, url=url, summary=summary,
            matches=tuple(matches), conflicts=tuple(conflicts),
            unknown=tuple(unknown), score=score,
            shape_problem=problem_shape, kind=kind,
        ))
    return tuple(sorted(fits, key=_rank))


def _rank(fit: "Fit"):
    """Candidates first, then surfaces, then writing about them.

    Within each, a conflict sinks below anything that contradicts nothing.
    A surface can never outrank a real candidate and can never be the
    recommendation, but it outranks an article, because it is somewhere to
    actually look.
    """
    tier = 0
    if fit.kind == acquisition.SOURCE_SURFACE:
        tier = 1
    elif fit.kind == acquisition.OFF_TARGET or fit.shape_problem:
        tier = 2
    return (tier, fit.rejected, -fit.score, fit.name)


def surfaces(fits) -> tuple["Fit", ...]:
    """The places candidates could be found, when none were."""
    return tuple(fit for fit in fits if fit.is_surface)


def shortlist_text(fits, *, limit: int = 5) -> str:
    """The ranked candidates, as evidence a reply can be written from."""
    lines: list[str] = []
    for index, fit in enumerate(fits[:limit], start=1):
        verdict = fit.verdict
        lines.append(
            f"{index}. [{verdict}] {fit.name} -- {fit.because()}"
            + (f"\n   {fit.summary}" if fit.summary else "")
            + (f"\n   {fit.url}" if fit.url else "")
        )
    return "\n".join(lines)


def log_block(fits, *, chosen: str = "", why: str = "") -> str:
    """Console only -- a decision summary, never the reasoning itself."""
    fitting = [fit for fit in fits if fit.verdict == "FITS"]
    unchecked = [fit for fit in fits if fit.verdict == "UNCHECKED"]
    rejected = [fit for fit in fits if fit.rejected and not fit.is_surface]
    found = surfaces(fits)
    lines = [
        "[Recommendation Reasoning]",
        f"  Candidates: {len(fits) - len(found)} ({len(fitting)} fit, "
        f"{len(unchecked)} unchecked, {len(rejected)} mismatched)",
    ]
    if found:
        lines.append(
            "  Surfaces: "
            + ", ".join(fit.name[:32] for fit in found[:3])
            + " (searchable, never recommended)"
        )
    if rejected:
        lines.append(
            "  Rejected: "
            + "; ".join(f"{fit.name[:40]} ({fit.because()})" for fit in rejected[:3])
        )
    lines.append(f"  Decision: {'recommend' if fitting else 'no clear fit'}")
    if chosen:
        lines.append(f"  Selected: {chosen}")
    if why:
        lines.append(f"  Why: {why}")
    return "\n".join(lines)


# ------------------------------------------------------------------- shape
#
# A search for "electric guitar around 500,000 won" comes back with "25 Best
# Electric Guitars in 2026", and a search for a soft dinner comes back with
# recipe collections and a YouTube travel vlog. Those are articles *about*
# candidates, not candidates, and ranking them is how the recommendation
# became a link to a listicle.
#
# What counts as a candidate depends on what is being recommended, so this
# is keyed on the expected type rather than on a list of sites.

PRODUCT = "product"
PLACE = "place"
ANY = "any"

# Somewhere that publishes writing and video about things, rather than the
# things themselves. Not a judgement about the sites -- a wikipedia article
# on guitars is a fine page and a poor candidate.
_ARTICLE_HOSTS = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com",
    "pinterest.com", "pinterest.co.kr", "reddit.com", "quora.com",
    "medium.com", "wikipedia.org", "facebook.com", "x.com", "twitter.com",
    "brunch.co.kr", "tistory.com", "blogspot.com", "wordpress.com",
    "vimeo.com", "dailymotion.com",
)

# Titles that announce themselves as writing about a set of things.
_ARTICLE_TITLE = re.compile(
    # A cardinal number at the front is the listicle's signature, whatever
    # adjective follows it. The old rule listed the adjectives, and
    # measured live it missed "85 Easy Electric Guitar Songs for
    # Beginners" -- recommended as an electric guitar -- and "12 Things You
    # Never Thought to Do With Packing Peanuts", recommended as somewhere
    # to buy them.
    #
    # A leading number on a real product is a quantity, and a quantity is
    # followed by its unit. That is the whole distinction: "50 Pack
    # Packing Peanuts" is a thing, "12 Things" is an article about things.
    r"^\s*\d{1,3}\s*\+?\s+"
    r"(?!(?:pack|packs|pc|pcs|piece|pieces|count|ct|set|sets|"
    r"inch|inches|in|cm|mm|m|ft|foot|feet|kg|g|lb|lbs|oz|ml|l|"
    r"litre|liter|liters|litres|gal|gallon|gallons|cu|cubic|"
    r"string|strings|key|keys|watt|watts|w|v|volt|volts|"
    r"bit|core|gb|tb|mb|hz|khz|mhz|ghz|mp|x)\b)"
    # And what it counts is plural. "401 Restaurant Korean BBQ" is a
    # restaurant whose name begins with a number; "12 Things You Never
    # Thought..." is twelve things.
    r"(?=[^,;]{0,44}\b[A-Za-z]{3,}s\b)"
    r"[A-Za-z]"
    r"|\b(?:top|best)\s+\d+\b"
    r"|\b(?:recipes?|ideas|guide|guides|tips|how\s+to|why\s+you|"
    r"everything\s+you|explained|review\s+round[- ]?up|listicle|"
    r"vs\.?\b|versus)\b"
    r"|\bin\s+20\d\d\s*$"
    r"|\b(?:blog|article|youtube|vlog)\b"
    r"|추천\s*\d+|정리|후기\s*모음",
    re.IGNORECASE,
)

_ARTICLE_PATH = re.compile(
    r"/(?:blog|blogs|article|articles|news|magazine|guide|guides|story|"
    r"stories|watch|shorts|pin|posts?)(?:/|$|\?)",
    re.IGNORECASE,
)

# Search and category pages word their titles as plural inventories. They
# may contain excellent listings in the snippet, but the page itself is not
# one apartment and must never be ranked as though it were.
_COLLECTION_TITLE = re.compile(
    r"\b(?:studio\s+)?apartments\s+(?:for\s+rent|near|under|in)\b"
    r"|\bapartments\s+for\s+rent\b"
    r"|\bfind\s+(?:your\s+next\s+place|apartments?\s+for\s+rent|"
    r"apartments?\s+near\s+you)\b"
    r"|\bsearch\s+for\s+monthly\s+furnished\s+rentals\b"
    r"|\bapartment\s*finder\b",
    re.IGNORECASE,
)

_CITY_STATE = re.compile(
    r"\b(?:in|near)\s+([A-Z][A-Za-z .'-]{1,40}?)\s+[A-Z]{2}\b",
)

# Something a candidate has and an article does not: a price to pay, or an
# address to go to.
_HAS_PRICE = re.compile(
    r"[$₩€£¥]\s?\d|\b\d[\d,]*\s*(?:won|krw|usd|eur|원)\b", re.IGNORECASE,
)
_HAS_PLACE_DETAIL = re.compile(
    r"\b(?:open(?:s|ing)?\s+(?:at|until|daily)|hours?|address|"
    r"reservation|reserve|book\s+a\s+table|menu|reviews?|rating|"
    r"\d+\.\d\s*(?:stars?|/\s*5))\b"
    r"|\b(?:-?gu|-?dong|-?ro)\b|구\s|동\s|로\s|영업시간|예약|메뉴|평점",
    re.IGNORECASE,
)


def expected_shape(problem) -> str:
    """What kind of thing would actually answer this recommendation."""
    category = str(getattr(problem, "category", "") or "")
    if category in {"restaurant", "hotel"}:
        return PLACE
    if category in {"gpu", "car", "shopping", "secondhand", "flight"}:
        return PRODUCT
    if getattr(problem, "purchase", False):
        return PRODUCT
    # "I want a guitar" names no buying verb and is plainly about buying a
    # guitar -- the same reading the clarification layer already uses.
    try:
        thing = problem._thing()
    except Exception:
        thing = ""
    if thing and thing in rs._VARIANTS:
        return PRODUCT
    return ANY


def _host(url: str) -> str:
    match = re.search(r"https?://([^/]+)", str(url or ""), re.IGNORECASE)
    return (match.group(1) if match else "").casefold().lstrip("www.")


def off_target(name: str, url: str, summary: str, shape: str) -> str:
    """Why this is not a candidate at all, or empty if it might be one.

    Only ever used to *exclude*: something that survives this is not
    thereby a match, it is merely the right kind of thing to check.
    """
    if shape == ANY:
        return ""
    host = _host(url)
    if any(host.endswith(article) for article in _ARTICLE_HOSTS):
        return f"{host} publishes articles, not {shape}s"
    if _ARTICLE_PATH.search(str(url or "")):
        return "the page is an article"
    if _ARTICLE_TITLE.search(str(name or "")):
        # A round-up can still be a page worth reading; it is not a thing
        # that can be bought or visited, which is what was asked for.
        return "a round-up article, not a single candidate"
    return ""


def looks_like(name: str, url: str, summary: str, shape: str) -> bool:
    """Whether the result carries what a real candidate of this kind has."""
    text = f"{name} {summary}"
    if shape == PRODUCT:
        return bool(_HAS_PRICE.search(text))
    if shape == PLACE:
        return bool(_HAS_PLACE_DETAIL.search(text))
    return True


# Constraints worth holding out for. A situation explains the request and a
# budget is settled arithmetically; what needs evidence is the qualities the
# person actually asked for, and the things they ruled out.
_IMPORTANT = (
    rs.ATTRIBUTE, rs.HOUSING_TYPE, rs.BUDGET, rs.EXCLUSION,
)


def viable(fits) -> tuple[Fit, ...]:
    """The candidates still in the running: right kind, no contradiction."""
    return tuple(fit for fit in fits if fit.viable)


def unresolved_constraints(fits, problem) -> tuple[str, ...]:
    """Constraints no surviving candidate has been shown to satisfy.

    This is the difference between "none of these fit" and "nothing here
    tells me whether any of these fit". The second is not permission to
    recommend one anyway -- measured live, three UNCHECKED restaurants for
    a sore throat produced a confident recommendation of Korean BBQ.
    """
    survivors = viable(fits)
    if not survivors:
        return ()
    unresolved: list[str] = []
    for name in _IMPORTANT:
        for value in problem.values(name):
            if not any(value in fit.matches for fit in survivors):
                unresolved.append(value)
    location = str(getattr(problem, "location", "") or "").strip()
    if location and not any(location in fit.matches for fit in survivors):
        unresolved.append(location)
    return tuple(dict.fromkeys(unresolved))


def confident(fits, problem) -> bool:
    """Whether anything here can be recommended without pretending."""
    survivors = viable(fits)
    if not survivors:
        return False
    if not unresolved_constraints(fits, problem):
        return True
    # Some important quality is still unevidenced for every survivor.
    return False


def with_semantic(fits, constraint: str, verdicts: dict) -> tuple[Fit, ...]:
    """Fold a semantic judgement back in as ordinary matches and conflicts.

    The judgement arrives as yes/no/unknown per candidate name. Everything
    downstream -- ranking, the invariant that a conflict never wins, the
    shortlist -- then works exactly as it does for a deterministic check.
    """
    updated: list[Fit] = []
    for fit in fits:
        answer = str(verdicts.get(fit.name, "unknown")).strip().casefold()
        if answer == "yes":
            updated.append(replace(
                fit,
                matches=fit.matches + (constraint,),
                unknown=tuple(v for v in fit.unknown if v != constraint),
                score=fit.score + 1.0,
            ))
        elif answer == "no":
            updated.append(replace(
                fit,
                conflicts=fit.conflicts + (constraint,),
                unknown=tuple(v for v in fit.unknown if v != constraint),
                score=fit.score - 2.0,
            ))
        else:
            updated.append(fit)
    return tuple(sorted(
        updated,
        key=lambda fit: (
            bool(fit.shape_problem), fit.rejected, -fit.score, fit.name,
        ),
    ))
