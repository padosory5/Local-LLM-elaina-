"""Reading the names of things out of pages that are about things.

A search for *good hotels in Seoul* returns writing about hotels. The fit
layer is right to call those sources, and the surface layer is right to
refuse them cards -- but between the two, a turn that found six useful
pages ends with nothing to recommend. The names are usually right there:

    "The best full-size mechanical keyboard we've tested is the
     Keychron Q5 Max."
    "Our three top picks: the Keychron V3 Max, the Keychron V5 Max, and
     the Keychron V6 Max."
    "The best gaming monitor we've tested is the ASUS ROG Swift OLED
     PG32UCDM Gen3."

This module takes those out of evidence that was actually retrieved.

Three rules hold it together.

**Never invent.** Every name returned is a literal span of the text it was
read from. Nothing is completed, corrected, expanded or guessed -- a name
that is truncated in a snippet stays truncated or is dropped, because a
plausible-looking hotel that does not exist is far worse than one fewer
card.

**Precision over recall.** A proper-noun run alone is not enough; "Michael
Hession/NYT Wirecutter" is a photo credit and reads exactly like a product.
A run is taken only when something corroborates it: the sentence points at
it ("...is the X"), it carries a model code, or it carries the noun the
person actually asked for.

**Deterministic.** No model call. This runs on text a search already
returned, inside a turn somebody is waiting through.

One known limit, deliberate: capitalisation is what marks where a name
starts and stops, so Korean prose yields nothing here rather than guesses.
Korean candidates still arrive by the older path -- a result whose own URL
identifies one record -- which extraction adds to and does not replace.
"""

from __future__ import annotations

import re


# How many names are worth taking from one page. A round-up of fifty is
# still a round-up; the first few are the ones it is actually arguing for.
PER_SOURCE = 4
MAX_WORDS = 6
MIN_WORDS = 2
# Real names are short. Longer than this and the run has swallowed a
# listing page's worth of text that arrived without spaces --
# measured: 'Parnas Seoul Myeongdong IIGLAD MapoHotel28 MyeongdongNine'.
MAX_CHARS = 48

# Capitalised, but never the name of anything. Sentence openers, editorial
# furniture, and the words a round-up is built out of. A stop word ends a
# run rather than joining it, which is what separates "Best Mechanical
# Keyboard: Corsair K70 Max" into a verdict and a thing.
_NOT_A_NAME = frozenset("""
the a an and or of for with in on at to from by is are was were be been
we our us i my you your they their it its this that these those there here
best top good great better greatest worst cheapest premium budget
review reviews reviewed rating ratings price prices deal deals
pick picks picked choice choices recommend recommended recommendation
tested testing test overall most more less least also but if when while
new latest updated guide guides list lists ranked ranking
buy buying shop shopping sale sales offer offers save
what which who why how where when whether
january february march april may june july august september october
november december monday tuesday wednesday thursday friday saturday sunday
read see view book find compare check learn discover explore
regarding get only save shop featuring including plus versus
photos photo video videos article articles blog news home page
""".split())

# Words that describe a category without ever naming one of its members.
# "Custom Mechanical Keyboards", "Curved Gaming Monitor", "Luxury Collection
# Hotel" -- each reads like a name and each is an aisle. Measured live: all
# three were discovered as candidates before this existed.
_DESCRIPTOR = frozenset("""
custom mechanic mechanical curved flat ultrawide portable wireless wired
gaming office home professional pro premium luxury budget cheap affordable
compact mini micro slim smart digital classic modern vintage
collection selection range series edition version model models
info information detail details overview summary
low high full half size sized profile low-profile
display displays screen screens panel panels
qhd uhd fhd hd oled led lcd ips hdr freesync gsync
inch inches hz khz ghz
""".split())

# Letters and digits in one token: a model number, and the single most
# reliable sign that a run names one manufactured thing. "Q5", "K70",
# "PG32UCDM", "27GR83Q", "V3".
_MODEL_CODE = re.compile(r"^(?=[^\W_]*\d)(?=[^\W_]*[A-Za-z])[\w-]{2,}$")

# A token that can belong to a name: capitalised, all-caps, a model code,
# or non-Latin script (Korean names have no capitalisation to read).
_NAMEY = re.compile(r"^(?:[A-Z][\w''’-]*|[A-Z0-9][A-Z0-9-]+|[^\W\dA-Za-z_][\w-]*)$")

# Where one run ends and the next begins.
_BREAK = re.compile(r"[.,;:!?()\[\]/|–—\"“”]|\s+-\s+")

# "...we've tested is the Keychron Q5 Max", "Best keyboard: Corsair K70".
# The sentence is pointing at the thing, which is the strongest signal a
# snippet offers and the reason cue-anchored runs are trusted on their own.
_CUES = (
    re.compile(r"\b(?:is|are|was|were)\s+(?:the\s+|a\s+|an\s+)?$", re.I),
    re.compile(r"\b(?:picks?|choices?|winner|favou?rites?|recommend(?:ed|s)?|"
               r"love|like|rate)\b[^:.]{0,40}[:]\s*(?:the\s+)?$", re.I),
    re.compile(r"\b(?:best|top)\b[^:.]{0,44}[:]\s*(?:the\s+)?$", re.I),
    # The article is what makes this a list of things rather than a comma.
    # Without it, "...hotels in Seoul, South Korea" read the country as the
    # next item. Measured on a real Tripadvisor snippet.
    re.compile(r",\s*(?:and\s+)?the\s+$"),
)


def _tokens(fragment: str) -> list[str]:
    return [token for token in fragment.split() if token]


def _base(token: str) -> str:
    """The word without its apostrophe tail: "We've" is "we"."""
    return re.split(r"['’]", token.strip("'’"), 1)[0]


def _is_namey(token: str) -> bool:
    stripped = token.strip("'’")
    if not stripped:
        return False
    if stripped.casefold() in _NOT_A_NAME or _base(stripped).casefold() in _NOT_A_NAME:
        return False
    return bool(_NAMEY.match(stripped))


def _runs(text: str) -> list[tuple[str, int]]:
    """Every proper-noun run, with where in ``text`` it started."""
    found: list[tuple[str, int]] = []
    for fragment in _split_with_offsets(text):
        piece, base = fragment
        run: list[str] = []
        start = 0
        cursor = 0
        for token in _tokens(piece):
            index = piece.index(token, cursor)
            cursor = index + len(token)
            if _is_namey(token):
                if not run:
                    start = index
                run.append(token.strip("'’"))
                continue
            if len(run) >= MIN_WORDS:
                found.append((_collapse(run), base + start))
            run = []
        if len(run) >= MIN_WORDS:
            found.append((_collapse(run), base + start))
    return found


def _collapse(run: list[str]) -> str:
    """One word, once. Listing markup repeats a brand either side of a
    break, and "Alienware Alienware AW2524HF" is not what anything is
    called."""
    kept: list[str] = []
    for token in run[:MAX_WORDS]:
        if kept and kept[-1].casefold() == token.casefold():
            continue
        kept.append(token)
    return " ".join(kept)


def _split_with_offsets(text: str) -> list[tuple[str, int]]:
    pieces: list[tuple[str, int]] = []
    at = 0
    for match in _BREAK.finditer(text):
        pieces.append((text[at:match.start()], at))
        at = match.end()
    pieces.append((text[at:], at))
    return pieces


# "1440p", "2K", "240Hz", "27". Shaped exactly like a model number and
# owned by nobody: measured live, "2K QHD 1440P" was discovered as though
# it were a monitor.
_SPEC = re.compile(r"^\d+(?:k|p|hz|khz|ghz|mhz|w|mm|cm|in|inch|inches)?$", re.I)


def _is_spec(token: str) -> bool:
    return bool(_SPEC.match(token.strip("\"'’-")))


def _distinctive(name: str, wanted: frozenset) -> tuple[str, ...]:
    """The words that could only belong to one particular thing.

    Not the category asked for, not a word that merely qualifies it, not
    editorial furniture. "Hotel Inspiroom Jongro" keeps two; "Curved Gaming
    Monitor" keeps none, which is the whole difference between a hotel and
    an aisle.
    """
    kept = []
    for token in name.split():
        lowered = token.casefold()
        stem = _singularish(lowered)
        if lowered in wanted or stem in wanted:
            continue
        if lowered in _DESCRIPTOR or stem in _DESCRIPTOR:
            continue
        if lowered in _NOT_A_NAME or _base(lowered) in _NOT_A_NAME:
            continue
        if _is_spec(lowered):
            continue
        kept.append(token)
    return tuple(kept)


def _is_a_plural_category(name: str, wanted: frozenset) -> bool:
    """Whether the name says *several* of the thing that was asked for.

    "Seoul Hotels", "Custom Mechanical Keyboards", "Gaming Monitors 2026".
    A request is for one hotel; a name in the plural is the category.
    """
    for token in name.split():
        lowered = token.casefold()
        if not lowered.endswith("s") or lowered.endswith("ss"):
            continue
        if _singularish(lowered) in wanted:
            # Deliberately not "...and the plural is not what was asked
            # for". A request for "good restaurants in Gangnam" is still a
            # request for one restaurant, and "Gangnam Station Lunch
            # Restaurants" is still a list of them.
            return True
    return False


def _corroborated(name: str, text: str, at: int, wanted: frozenset) -> str:
    """Why this run names a thing, or an empty string if nothing says so.

    A proper-noun run on its own is not evidence: a photographer's byline
    reads exactly like a product name, and so does a shop's aisle. Two
    things have to be true -- something must point at it, and it must carry
    a word that could only be this one thing.
    """
    if _is_a_plural_category(name, wanted):
        return ""
    tokens = name.split()
    special = _distinctive(name, wanted)
    if not special:
        # Nothing here that another one of these would not also say.
        return ""
    if any(
        _MODEL_CODE.match(token) and not _is_spec(token) for token in tokens
    ):
        return "model code"
    lowered = {token.casefold() for token in tokens}
    stems = {_singularish(word) for word in lowered}
    if (lowered | stems) & wanted:
        # It carries the noun that was asked for. On its own that is what
        # "Dell Monitor" carries too, so a bare pair is not enough: a real
        # name has something else in it as well.
        return (
            "names the kind of thing asked for" if len(tokens) >= 3 else ""
        )
    before = text[max(0, at - 80):at]
    for cue in _CUES:
        if cue.search(before):
            return "the sentence points at it"
    return ""


def _wanted_nouns(subject: str) -> frozenset:
    """The words that say what kind of thing this is, from the request."""
    words = re.findall(r"[^\W\d_]{3,}", str(subject or "").casefold())
    return frozenset(
        word for word in words if word not in _NOT_A_NAME
    ) | frozenset(
        # A request for "hotels" should recognise "Hotel" in a name.
        word[:-1] for word in words
        if len(word) > 4 and word.endswith("s") and word not in _NOT_A_NAME
    )


def names_the_category(name: str, subject: str) -> bool:
    """Whether this titles the group rather than one of its members.

    The acquisition layer's own version of the question, asked before
    anything becomes a candidate. "The Best Hotels in Seoul" is plural
    where the request was for one hotel; "Hotel Inspiroom Jongro" carries
    two words that could belong to nothing else.

    Deliberately not the surface layer's filter. That one decides whether
    something may be drawn as a card, which is a rendering decision made at
    the end; this decides whether Elaina may *talk about it as a
    recommendation*, which happens much earlier and matters more.
    """
    wanted = _wanted_nouns(subject)
    if not wanted or not str(name or "").strip():
        return False
    if _is_a_plural_category(name, wanted):
        return True
    return not _distinctive(name, wanted)


def names_in(text: str, *, subject: str = "", host: str = "") -> tuple[str, ...]:
    """Names of individual things that appear literally in ``text``.

    ``subject`` is what the person asked for, used to recognise the kind of
    noun a name would carry. ``host`` is where the text came from, so the
    site's own name is not read back as one of the things it lists.
    """
    text = " ".join(str(text or "").split())
    if not text:
        return ()
    wanted = _wanted_nouns(subject)
    site = frozenset(re.findall(r"[a-z0-9]{3,}", str(host or "").casefold()))
    seen: dict[str, str] = {}
    for name, at in _runs(text):
        if name.casefold() in seen or len(name) > MAX_CHARS:
            continue
        if _is_the_site(name, site):
            continue
        if _adds_nothing(name, wanted):
            continue
        if not _corroborated(name, text, at, wanted):
            continue
        seen[name.casefold()] = name
    return tuple(seen.values())[:PER_SOURCE]


def _is_the_site(name: str, site: frozenset) -> bool:
    """Whether this is the publisher rather than one of its subjects.

    "NYT Wirecutter" on nytimes.com, "Tripadvisor" on tripadvisor.com. A
    single shared token is enough: no hotel is called after the directory
    that lists it.
    """
    if not site:
        return False
    return any(
        token.casefold() in site
        or any(token.casefold() in word for word in site if len(word) > 4)
        for token in name.split()
    )


def _singularish(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _adds_nothing(name: str, wanted: frozenset) -> bool:
    """Whether the name is only the category words handed back.

    Both sides are singularised: the request says "gaming monitor" and the
    round-up's title says "Gaming Monitors", and those are the same two
    words. Without that, the category itself was read as a product.
    """
    tokens = [token.casefold() for token in name.split()]
    if not tokens:
        return True
    for token in tokens:
        stem = _singularish(token)
        if stem in wanted or token in wanted:
            continue
        if token in _NOT_A_NAME or _base(token) in _NOT_A_NAME:
            continue
        return False
    return True


def is_about(name: str, text: str) -> bool:
    """Whether ``text`` is plausibly about the thing called ``name``.

    Used to decide whether a verification search actually found the
    candidate's own page, or something else that happened to rank. The test
    is deliberately blunt -- most of a short name has to be present -- because
    the alternative is attaching a real name to an unrelated address, which
    is exactly the confusion this whole layer exists to remove.
    """
    words = [
        word for word in re.findall(r"[^\W_]{2,}", str(name or "").casefold())
        if word not in _NOT_A_NAME and word not in _DESCRIPTOR
    ]
    if not words:
        return False
    haystack = str(text or "").casefold()
    hits = sum(1 for word in words if word in haystack)
    if hits < max(1, (len(words) + 1) // 2):
        return False
    # And the rarest word has to be there. Measured live: "Luxury
    # Collection Hotel" matched a Sheraton in Bangkok on "hotel" and
    # "collection" alone, which would have put a real name on an unrelated
    # address -- the exact confusion this layer exists to remove.
    rarest = max(words, key=len)
    return rarest in haystack


def from_results(results, *, subject: str = "") -> tuple[tuple[str, str], ...]:
    """(name, the url it was read from) for every result given.

    Results are title/url/summary dicts as the search tool returns them.
    The title is read as well as the snippet: a round-up's title sometimes
    names its winner, and a listing page's title is often the thing itself.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for result in results or ():
        url = str(result.get("url", "") or "")
        host = re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0]
        text = " ".join((
            str(result.get("title", "") or ""),
            str(result.get("summary", "") or ""),
        ))
        for name in names_in(text, subject=subject, host=host):
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append((name, url))
    return tuple(found)
