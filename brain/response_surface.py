"""The optional structured half of a reply, and the schema that bounds it.

A reply is text. Sometimes text is the wrong shape for what was found --
four monitors with prices and pages behind them read badly as a sentence and
well as four cards -- so a reply may carry a *surface* alongside its words.

Three rules decide everything in this module.

**The backend chooses.** Electron renders what it is given and decides
nothing. It must never look at a reply, see "hotel", and produce cards of
its own: the layer that knows whether a real result set exists is the layer
that found it, and a renderer guessing from prose is how a UI starts
disagreeing with the conversation.

**The payload is data, never code.** No HTML, no CSS, no JavaScript, no
template. A model cannot reach this: every field is filled from
:class:`~brain.result_state.Candidate` records that a search actually
returned, and anything that fails validation is dropped rather than
rendered. The blast radius of a bad payload is a plain-text reply.

**Absent means unchanged.** ``none`` is the default and the fallback, and a
reply without a surface has to behave exactly as it did before this module
existed -- which is what most replies are.

Deliberately three surfaces and no more. ``shortlist`` for several things
found, ``comparison`` for two or three of them weighed against each other,
``ambient_images`` for atmosphere (4F.7 fills that one in; the type exists
here so the protocol does not have to change again to carry it).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


NONE = "none"
SHORTLIST = "shortlist"
COMPARISON = "comparison"
AMBIENT_IMAGES = "ambient_images"

SURFACES = (NONE, SHORTLIST, COMPARISON, AMBIENT_IMAGES)

# What a card may offer. A closed list: an action Electron does not know is
# an action Elaina cannot honour, and inventing one in a payload would be a
# button that does nothing.
OPEN = "open"
COMPARE = "compare"
TELL_ME_MORE = "tell_me_more"

ACTIONS = (OPEN, COMPARE, TELL_ME_MORE)

# Bounds. Not arbitrary: a shortlist longer than this stops being a
# shortlist, and a field longer than this stops being a card.
MAX_ITEMS = 6
MAX_TEXT = 240
MAX_METADATA = 6

_HTTP = re.compile(r"^https?://", re.IGNORECASE)
# Anything that could be markup rather than words. A payload is data; if a
# field looks like code it is dropped rather than escaped, because escaping
# implies the value was meant to be there.
_MARKUP = re.compile(r"[<>]|javascript:|data:text/html", re.IGNORECASE)


def _clean(value, limit: int = MAX_TEXT) -> str:
    """One line of plain text, or nothing."""
    text = " ".join(str(value or "").split())
    if not text or _MARKUP.search(text):
        return ""
    return text[:limit]


def _link(value: str) -> str:
    """An http(s) address, or nothing. Never a scheme Electron should not open."""
    url = " ".join(str(value or "").split())
    return url if _HTTP.match(url) and not _MARKUP.search(url) else ""


@dataclass(frozen=True)
class SurfaceItem:
    """One card. Every field optional except the identity and the name."""

    id: str
    name: str
    image: str = ""
    subtitle: str = ""
    description: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    actions: tuple[str, ...] = ()
    url: str = ""

    def payload(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "image": self.image,
            "subtitle": self.subtitle,
            "description": self.description,
            "metadata": [list(pair) for pair in self.metadata],
            "actions": list(self.actions),
            "url": self.url,
        }


@dataclass(frozen=True)
class Surface:
    """The structured half of one reply."""

    type: str = NONE
    title: str = ""
    items: tuple[SurfaceItem, ...] = ()

    def __bool__(self) -> bool:
        return self.type != NONE and bool(self.items)

    def payload(self) -> dict:
        return {
            "type": self.type,
            "title": self.title,
            "items": [item.payload() for item in self.items],
        }

    def log_line(self) -> str:
        if not self:
            return ""
        return (
            f"[Surface] {self.type} with {len(self.items)} item(s)"
            f"{': ' + self.title if self.title else ''}"
        )


NOTHING = Surface()


# What a listing's title carries and a card must not. The thing on a card is
# the name of a hotel; what a search returns is the title of a page about
# one. Measured live: "INDUSTRIE HOTEL - Prices & Reviews (Busan, South
# Korea)", "THE MAY HOTEL - Updated 2026 Prices & Reviews (Seoul, South
# Korea)", "Busan Tourist Hotel in Busan, South Korea from GBP29: Deals".
_LISTING_NOISE = re.compile(
    r"\b(?:prices?|reviews?|deals?|photos?|booking|updated|rates|"
    r"official site|book now|20\d\d)\b",
    re.IGNORECASE,
)
_TRAILING_BRACKET = re.compile(r"\s*[\(\[][^\)\]]{0,60}[\)\]]\s*$")
_TITLE_SPLIT = re.compile(r"\s+[-|\u2013\u2014]\s+")
# "... South Korea from GBP29: Deals" and "Find AU$40 Romantic...".
_PRICE_TAIL = re.compile(
    r"\s+from\s*[A-Z]{0,3}[$\u00a3\u20ac\u20a9]\s?[\d.,]+.*$",
    re.IGNORECASE,
)
# "Aloft Seoul Gangnam Reviews: 229 Verified Reviews" -- a count, not a name.
_COUNT_TAIL = re.compile(r"\s*:\s*\d.*$")
# "Mon Oncle a Seoul, Seoul | HotelsCombined" -- the site that listed it,
# not part of what it is called. One word only, so a real subtitle stays.
_SITE_TAIL = re.compile(r"\s*\|\s*[A-Za-z][\w.-]{1,24}\s*$")
_TRAILING_NOISE_WORD = re.compile(
    r"[\s,:-]+(?:reviews?|prices?|deals?|photos?|rates|discounts?)\s*$",
    re.IGNORECASE,
)
# "Borjomi Seoul Boutique Hotel in Seoul, South Korea from." -- a sentence
# cut mid-phrase by the search index, left dangling on a preposition.
_DANGLING = re.compile(
    r"[\s,]+(?:from|in|at|near|with|for|by|to|of|and|the)\s*[.,]?\s*$",
    re.IGNORECASE,
)


# A trailing " - Something" or " | Something" that names the site rather
# than the thing. Which words those are is not guessable in the abstract,
# so it is not guessed: the address says who published the page, and a tail
# made of the publisher's own name is the publisher's own name.
_PUBLISHER_TAIL = re.compile(r"\s*[-|\u2013\u2014]\s*([^-|\u2013\u2014]{2,30})\s*$")


def _strip_publisher(name: str, url: str) -> str:
    """Drop "- Best Buy" from a bestbuy.com title, and "- KAYAK" from kayak."""
    host = re.sub(r"^https?://(?:www\.)?", "", str(url or "")).split("/")[0]
    letters = re.sub(r"[^a-z0-9]", "", host.split(":")[0].casefold())
    if not letters:
        return name
    for _ in range(2):
        tail = _PUBLISHER_TAIL.search(name)
        if tail is None:
            break
        squashed = re.sub(r"[^a-z0-9]", "", tail.group(1).casefold())
        if not squashed or squashed not in letters:
            break
        trimmed = name[:tail.start()].strip(" ,-|:")
        if len(trimmed.split()) < 2:
            break
        name = trimmed
    return name


# "H27E6 27" -- a listing title cut mid-phrase ("27 Inch Gaming Monitor"),
# leaving a measurement with nothing to measure. A model number keeps its
# letters; a bare trailing integer is the seam where the title was cut.
_CUT_SHORT = re.compile(r"\s\d{1,3}$")


def _card_name(value, url: str = "") -> str:
    """The name of the thing, not the title of a page about it.

    Deliberately conservative in one direction: it only ever *drops* a
    trailing fragment, and never promotes a later one. "Best Hotels in Seoul
    2026 | Top 10" comes back unchanged rather than becoming "Top 10",
    because a title that is boilerplate all the way through is a bad
    candidate -- and :func:`names_a_specific_thing` is what refuses it,
    rather than this rewriting the words until it looks like a name.
    """
    name = _clean(value, 200)
    if not name:
        return ""

    name = _PRICE_TAIL.sub("", name)
    name = _COUNT_TAIL.sub("", name)
    name = _SITE_TAIL.sub("", name)
    name = _strip_publisher(name, url)

    # Titles stack parentheticals, so this runs more than once.
    for _ in range(2):
        bracket = _TRAILING_BRACKET.search(name)
        if bracket is None or not _LISTING_NOISE.search(bracket.group(0)):
            break
        name = name[:bracket.start()].strip()

    parts = _TITLE_SPLIT.split(name)
    if len(parts) > 1:
        head = []
        for part in parts:
            if _LISTING_NOISE.search(part):
                break
            head.append(part)
        if head and len(head) < len(parts):
            name = head[0] if len(head) == 1 else " - ".join(head)

    for _ in range(3):
        trimmed = _TRAILING_NOISE_WORD.sub("", name)
        trimmed = _DANGLING.sub("", trimmed)
        if trimmed == name or not trimmed:
            break
        name = trimmed

    return name.strip(" ,-|:").strip()[:120]


# ----------------------- Is this a thing, or a page? -----------------------
#
# A card carries a photograph, and a photograph of "The 10 best guest houses
# in Busan" is whatever picture that article happens to lead with. The point
# of a card is to show the specific hotel, the specific monitor, the actual
# banana -- so a candidate that names a *page about several things* has no
# business on one, however well it scored.
#
# This cannot be answered by the fit layer's verdict. Measured live, every
# one of these came back FITS:
#
#     FITS  'The 10 best accommodation in Busan, South Korea | Booking.com'
#     FITS  'The 10 best guest houses in Busan, South Korea | Booking.com'
#     FITS  'Romantic Getaways Busan: Find AU$40 Romantic... | lastminute'
#
# alongside four real hotels with the same verdict. The distinction is in
# the words, so it is made in the words -- and only ever to *refuse* a card.
# Nothing here promotes anything, and a name it cannot judge is allowed
# through, because a missing card is a smaller error than a wrong one only
# when we are sure.

_A_ROUNDUP = (
    # "Top 10 ...", "5 Best ...", "The 49 best hotels in Seoul"
    (re.compile(r"\b(?:top|best|cheapest|greatest)\s+\d+\b", re.I),
     "a numbered roundup"),
    (re.compile(r"\b\d+\s+(?:best|top|greatest|cheapest|coolest)\b", re.I),
     "a numbered roundup"),
    (re.compile(r"^\s*(?:the\s+)?\d+\s+\w+", re.I),
     "starts with a count"),
    (re.compile(r"^\s*(?:the\s+)?(?:best|top|cheapest|worst)\b", re.I),
     "starts with a superlative"),
    (re.compile(r"\b(?:best|top)\s+[\w' -]{0,30}?\s(?:in|for|near|of|"
                r"under|around)\b", re.I),
     "a superlative list"),
    # "How to choose...", "Where to stay...", "Honest Advice for..."
    (re.compile(r"\b(?:how|where|what|when|why)\s+to\b", re.I),
     "a guide"),
    (re.compile(r"\b(?:guide|tips|advice|checklist|round-?up|explained|"
                r"everything you need)\b", re.I),
     "a guide"),
    (re.compile(r"\bthings\s+to\s+\w+", re.I), "a guide"),
    # "I asked my most stylish friends where they stay in Seoul" -- a title
    # written in the first person is somebody's article about the thing.
    (re.compile(r"^\s*(?:i|we|you|my|our|they|here(?:'s)?)\b", re.I),
     "reads as an article"),
    (re.compile(r"\bwhere\s+(?:they|you|i|we)\b", re.I), "a guide"),
    # "Aman Seoul to Bring Ultra-Luxury Hospitality to South Korea". A name
    # is a noun phrase; a headline is a sentence, and the verb is what
    # gives it away.
    (re.compile(r"\bto\s+(?:bring|open|launch|debut|arrive|expand|offer|"
                r"add|host|replace|close)\b", re.I),
     "a news headline"),
    (re.compile(r"\b(?:announces?|unveils?|reveals?|will\s+open|"
                r"has\s+opened|is\s+coming)\b", re.I),
     "a news headline"),
    # "11-Day South Korea Itinerary: Seoul, Jeju Island & Busan Tour".
    (re.compile(r"\bitinerar(?:y|ies)\b|\b\d+[- ]days?\b", re.I),
     "an itinerary"),
    (re.compile(r"^\s*\d+[-\u2013]", re.I), "starts with a count"),
    (re.compile(r"\bvs\.?\b|\bcompared\b", re.I), "a comparison article"),
    # Aggregator and storefront front pages.
    (re.compile(r"\bofficial\s+(?:site|store|shop|website)\b", re.I),
     "a site front page"),
    # "Custom Mechanical Keyboards for Mac", "Hotels in Seoul". A plural
    # category followed by who or where it is for is a listing of them; a
    # real thing is singular there -- "Boutique Hotel in Seoul" stays.
    (re.compile(r"\b[^\W_]{3,}s\s+(?:for|in|near|under|around)\b", re.I),
     "a category listing"),
    # "Keychron | Custom Mechanical Keyboards for Mac, Windows and..." --
    # a brand and its tagline. What survives a pipe with several words
    # after it is a page title, never the name of one product.
    (re.compile(r"\|\s*\S+(?:\s+\S+){2,}"), "a page title"),
    (re.compile(r"\bfind\s+(?:the\s+)?(?:best|cheap|cheapest)\b", re.I),
     "a search page"),
    (re.compile(r"\bnear\s+me\b", re.I), "a search page"),
    (re.compile(r"\b(?:compare|search)\s+\w+\s+(?:prices|hotels|deals)\b",
                re.I),
     "a search page"),
    (re.compile(r"\bover\s+a\s+(?:billion|million)\b", re.I),
     "a site front page"),
    (re.compile(r"\bcheap\s+\w+\s+deals?\b", re.I), "a deals page"),
    # "Cheap HOTELS in Jungmun", "Budget Hotels". A qualifier on a plural
    # category is a listing of them; a real place is singular -- "Budget
    # Inn" and "Luxury Collection Hotel" are names and stay.
    (re.compile(r"\b(?:cheap|budget|discount|affordable|luxury|top-rated)"
                r"\s+[a-z]+s\b", re.I),
     "a category listing"),
    (re.compile(r"[$\u00a3\u20ac\u20a9]\s?\d", re.I), "a price listing"),
    # A domain is an address, not a name.
    (re.compile(r"\b[\w-]+\.(?:com|net|org|io|co|kr|jp|uk|de)\b", re.I),
     "a web address"),
    (re.compile(r"^\s*(?:www\.|https?://)", re.I), "a web address"),
)


# Words that are never what makes one thing different from another.
_GENERIC = frozenset("""
a an the and or of in on at for with from to by near around this that
your our my their its is are was were be
best good great top cheap nice new other more most
review reviews price prices rate rates deal deals discount discounts
photo photos map maps address addresses info official site online
book booking com net org list guide
""".split())

# Not [a-z]: this user's results are routinely Korean, and an ASCII-only
# word pattern found no words at all in a Korean name -- so every one of
# them looked like it said nothing the request had not said, and was
# refused a card. Measured live on "네이버지도".
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _singularish(word: str) -> str:
    """Crude enough to make "hotels" and "hotel" the same word."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _adds_nothing_to(name: str, subject: str) -> bool:
    """Whether this name says anything the request did not already say.

    "Seoul" and "Seoul Hotels with Reviews & Address", returned for *find
    good hotels in Seoul*, name the category being searched for rather than
    one of the things in it -- and a photograph of a category is a stock
    photo of a skyline. "THE MAY HOTEL" keeps "may", "IBIS AMBASSADOR SEOUL
    MYEONGDONG" keeps three words, and both stay.

    Without a subject there is nothing to compare against, and the rule
    stands aside rather than guessing.
    """
    if not subject:
        return False
    known = {
        _singularish(word) for word in _WORD.findall(str(subject).casefold())
    }
    for word in _WORD.findall(str(name).casefold()):
        stem = _singularish(word)
        if stem not in known and stem not in _GENERIC:
            return False
    return True


def names_a_specific_thing(name: str, subject: str = "") -> bool:
    """Whether this names one thing you could photograph.

    ``"Lotte Hotel World"`` and ``"LG UltraGear 27GP850-B"`` do. ``"The 10
    best guest houses in Busan"`` and ``"Booking.com | Official site"`` do
    not -- they name a page, and the picture behind a page is whatever it
    happens to lead with.
    """
    return not _refuses_a_card(name, subject)


def _refuses_a_card(name: str, subject: str = "") -> str:
    """The reason this is a page rather than a thing, or an empty string."""
    text = " ".join(str(name or "").split())
    if not text:
        return "empty"
    for pattern, reason in _A_ROUNDUP:
        if pattern.search(text):
            return reason
    if _adds_nothing_to(text, subject):
        return "names the category, not one of them"
    if _pluralises_the_request(text, subject):
        # "High Refresh Gaming Computer Monitors" answering a request for a
        # gaming monitor. It carries distinguishing words, so the rule above
        # lets it through -- but it is still several of them, not one.
        return "several of them, not one"
    if _CUT_SHORT.search(text) and not re.search(r"[A-Za-z]\d|\d[A-Za-z]",
                                                 text.split()[-1]):
        return "a title cut short"
    return ""


def _pluralises_the_request(name: str, subject: str) -> bool:
    """Whether the name says *several* of the thing that was asked for."""
    if not subject:
        return False
    known = {
        _singularish(word) for word in _WORD.findall(str(subject).casefold())
    }
    for token in _WORD.findall(str(name).casefold()):
        if not token.endswith("s") or token.endswith("ss"):
            continue
        stem = _singularish(token)
        if stem != token and stem in known:
            return True
    return False


def _same_thing_as_one_of(key: str, shown: set, subject: str) -> bool:
    """Whether this identity is one already drawn, wearing its category.

    "HOTEL THE BOTANIK SEWOON MYEONGDONG" and "BOTANIK SEWOON MYEONGDONG"
    are one hotel: everything that differs between them is the word the
    person searched for. Both reached the window as separate cards.

    Deliberately not containment on its own. "Keychron Q1" is inside
    "Keychron Q1 Max" and they are two products -- what separates the two
    cases is whether the extra words say *which one* or merely say *what
    kind*, and the request is what knows the difference.
    """
    words = set(key.split())
    if not words:
        return False
    category = {
        _singularish(word) for word in _WORD.findall(str(subject or "").casefold())
    }
    if not category:
        return False
    for held in shown:
        other = set(str(held).split())
        if not other or other == words:
            continue
        if not (words <= other or other <= words):
            continue
        difference = words ^ other
        if difference and all(
            _singularish(word) in category or word in category
            for word in difference
        ):
            return True
    return False


def _identity_key(name: str) -> str:
    """What two cards for the same thing have in common.

    "ROYAL HOTEL SEOUL" and "ROYAL HOTEL SEOUL (South Korea)" are one
    hotel; "Hotel Morning Sky" and "Hotel Morning Sky in Seoul, South
    Korea" are one hotel. Both pairs reached the window as two cards.

    Reduced to the words that do the naming -- case folded, punctuation
    dropped, the region qualifier and the joining words removed -- so the
    comparison is between identities and not between labels. Word order is
    kept: "Seoul Station Hotel" and "Hotel Station Seoul" are not obviously
    the same thing, and collapsing them would be guessing.
    """
    words = [
        word for word in re.findall(r"[^\W_]+", str(name or "").casefold())
        if word not in _QUALIFIER
    ]
    return " ".join(words)


# Words that qualify a name without being part of it: the country a listing
# appends, and the joining words a longer variant uses to say the same thing.
_QUALIFIER = frozenset({
    "the", "a", "an", "in", "at", "of", "on", "and",
    "south", "north", "korea", "korean", "japan", "china", "usa", "us",
    "kr", "jp", "cn", "seoul",
})


def refusals(candidates, subject: str = "") -> tuple[tuple[str, str], ...]:
    """For each candidate: the name a card would carry, and why it cannot.

    An empty reason means it can. Exposed so the decision layer can write
    down what it dropped and why -- "there were four results and no cards"
    is otherwise indistinguishable from "the rule never ran".
    """
    return tuple(
        (name, _refuses_a_card(name, subject))
        for name in (
            _card_name(
                getattr(candidate, "name", ""), getattr(candidate, "url", ""),
            )
            for candidate in (candidates or ())
        )
    )


def from_candidates(
    candidates,
    *,
    kind: str = SHORTLIST,
    title: str = "",
    limit: int = MAX_ITEMS,
    subject: str = "",
) -> Surface:
    """Build a surface from result-state candidates, or return ``NOTHING``.

    Only from :class:`~brain.result_state.Candidate` records -- the identity
    on a card is the candidate's own id, so an action coming back names the
    thing rather than the label sitting on the button. Phase 4E is why:
    labels and identities came apart repeatedly, and a card is one more
    place they could.

    Returns ``NOTHING`` rather than an empty surface when there is not
    enough to show. One card is not a shortlist, and a comparison of one is
    not a comparison.
    """
    if kind not in {SHORTLIST, COMPARISON}:
        return NOTHING
    items: list[SurfaceItem] = []
    # Two candidates whose titles differ but whose *card names* do not are
    # one thing on screen. Measured live: "LOTTE HOTEL SEOUL" and "LOTTE
    # HOTEL SEOUL - Updated 2026 Prices & Reviews" survive de-duplication
    # as candidates, correctly, and then become two identical cards.
    shown: set[str] = set()
    # Filtered before the limit, not after: three roundup articles ahead of
    # a real hotel must not use up the room the hotel was going to take.
    for candidate in (candidates or ()):
        if len(items) >= limit:
            break
        name = _card_name(
            getattr(candidate, "name", ""), getattr(candidate, "url", ""),
        )
        identity = _clean(getattr(candidate, "id", ""), 64)
        if not name or not identity:
            continue
        if _refuses_a_card(name, subject):
            # A card carries a photograph, and the picture behind a page
            # about several things is whatever that page leads with.
            continue
        # An exact repeat, a longer way of writing one already shown, or the
        # same thing with the category word attached. Only the label is
        # collapsed: the candidates keep their own identities, and the one
        # that ranked highest is the one drawn.
        key = _identity_key(name)
        if name.casefold() in shown or (key and key in shown):
            continue
        if key and _same_thing_as_one_of(key, shown, subject):
            continue
        shown.add(name.casefold())
        if key:
            shown.add(key)
        url = _link(getattr(candidate, "url", ""))
        actions = [TELL_ME_MORE]
        if url:
            actions.insert(0, OPEN)
        if kind == SHORTLIST:
            actions.append(COMPARE)
        items.append(SurfaceItem(
            id=identity,
            name=name,
            subtitle=_clean(getattr(candidate, "why", ""), 90),
            description=_clean(getattr(candidate, "summary", "")),
            metadata=tuple(
                (str(value), "")
                for value in tuple(getattr(candidate, "attributes", ()))[:MAX_METADATA]
                if str(value).strip()
            ),
            actions=tuple(actions),
            url=url,
        ))
    if len(items) < 2:
        return NOTHING
    return Surface(type=kind, title=_clean(title, 90), items=tuple(items))


def validate(payload) -> Surface:
    """Read a payload back, keeping only what is well formed.

    The receiving half of the contract, and the reason a bad payload is a
    plain-text reply rather than a broken window. Anything unrecognised --
    a surface type nobody implements, an item with no identity, a field
    carrying markup -- is dropped, and if too little survives the whole
    surface becomes ``NOTHING``.
    """
    if not isinstance(payload, dict):
        return NOTHING
    kind = str(payload.get("type", "") or "").strip()
    if kind not in SURFACES or kind == NONE:
        return NOTHING
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return NOTHING

    items: list[SurfaceItem] = []
    for raw in raw_items[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        identity = _clean(raw.get("id"), 64)
        name = _clean(raw.get("name"), 120)
        if not identity or not name:
            continue
        metadata = []
        for pair in (raw.get("metadata") or [])[:MAX_METADATA]:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                label, value = _clean(pair[0], 40), _clean(pair[1], 60)
                if label:
                    metadata.append((label, value))
        items.append(SurfaceItem(
            id=identity,
            name=name,
            image=_link(raw.get("image")),
            subtitle=_clean(raw.get("subtitle"), 90),
            description=_clean(raw.get("description")),
            metadata=tuple(metadata),
            actions=tuple(
                action for action in (raw.get("actions") or [])
                if action in ACTIONS
            ),
            url=_link(raw.get("url")),
        ))

    if kind in {SHORTLIST, COMPARISON} and len(items) < 2:
        return NOTHING
    if kind == AMBIENT_IMAGES and not items:
        return NOTHING
    return Surface(type=kind, title=_clean(payload.get("title"), 90),
                   items=tuple(items))
