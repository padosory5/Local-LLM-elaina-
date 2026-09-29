"""Stop Elaina quoting a price she never actually looked up.

Found live, in a conversation-routed turn with no tool call in it at all:

    User:   for real? that seems cheap
    Elaina: "Trip.com shows prices starting at around 120,000 KRW for
             Harbour Plaza Hotels."

Nothing was read. No search ran. The number, the currency, and the source
attribution were all generated. That is worse than an unhelpful answer,
because it is indistinguishable from a real one.

The guard is deliberately narrow, because most numbers in conversation are
perfectly fine to state from general knowledge ("a coffee is about 5,000
won in Seoul"). It only fires when all of these hold:

* no capability ran this turn -- so there is no fresh evidence behind it;
* the conversation already has a grounded subject (the user is following
  up on something Elaina really did look up);
* the reply states a **checkable value** that appears nowhere in that
  grounded evidence, nor in what the user themselves said.

That combination is specifically "inventing a figure about the thing we
were just discussing", which is the failure this exists for. A number the
user supplied, or one that came back from a real search, always passes.

A6 widened what counts as a checkable value. It used to be money, phone
numbers and email addresses; measured against a real monitor listing with
six invented replies, it caught one of the six. A fabricated 240Hz refresh
rate, 4.8-star rating or 14-hour battery life read exactly like retrieved
ones, and they are the numbers that decide a purchase. See
:mod:`brain.attribute_values` -- which produces tokens in the shape
``_digits`` already did, so none of the machinery below had to change.
"""

from __future__ import annotations

import re

from brain import attribute_values

# Money only. A plain integer ("three hotels", "2026") is not a claim about
# a live value and must not be second-guessed.
_MONEY = re.compile(
    r"[$₩€£¥]\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s*"
    r"(?:won|krw|usd|eur|gbp|jpy|dollars?|euros?|pounds?|yen)\b"
    # 원 gets its own branch, ending on "not a digit" rather than \b.
    #
    # Korean attaches its particles directly to the noun -- 10,000원에,
    # 8,000원입니다 -- and \b needs a non-word character after 원 to
    # match. It never gets one, so *every price stated in natural Korean
    # was invisible to this guard*. "10,000원 입니다", with a space, did
    # match, which is not how anybody writes it.
    #
    # Found by an unseen Korean dogfood turn: "서울에 있는 '김치찌개
    # 전문점'에서 10,000원에 먹을 수 있습니다" -- an invented restaurant
    # and an invented price, from a turn where nothing was looked up, and
    # the oldest honesty guard in the project saw neither.
    # Three digits or a thousands group, so "3원소" (three elements) is
    # not read as a three-won price. Nothing in Korea costs single-digit
    # won, and 원 is a syllable inside ordinary words.
    r"|(?:\d{1,3}(?:,\d{3})+|\d{3,})(?:\.\d+)?\s*원(?!\d)",
    flags=re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# A phone number or an email address is a looked-up value in exactly the
# way a price is, and the guard did not know about either. Measured live: a
# search that came back with rental listings was followed by "Email:
# international@uw.edu | Phone: +1 (206) 543-0000", stated flat, and both
# were generated.
#
# Seven digits is the floor so a year, a duration or a count is never read
# as a number to call, and anything the money reader already claimed is
# dropped -- "1,000,000 won" is a price, not a phone.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"\+?\d[\d\s().–—-]{5,}\d")


def _digits(text: str) -> set[str]:
    """Bare digit strings, so ₩120,000 and "120000 won" compare equal."""
    return {
        re.sub(r"\D", "", match.group(0))
        for match in _MONEY.finditer(str(text or ""))
        if re.sub(r"\D", "", match.group(0))
    }


def _contacts(text: str) -> set[str]:
    """Email addresses and phone numbers, normalised for comparison."""
    text = str(text or "")
    found = {match.group(0).casefold() for match in _EMAIL.finditer(text)}
    # Whatever the money reader claimed is a price, not a number to ring.
    priced = _digits(text)
    for match in _PHONE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 7 or digits in priced:
            continue
        # A leading country code is not part of the number's identity:
        # "+1 (206) 543-0000" and "206-543-0000" are the same claim.
        found.add(digits.lstrip("0")[-10:] if len(digits) > 10 else digits)
    return found


def _values(text: str, *, spoken: bool = True) -> set[str]:
    """Every checkable value in this text.

    A6 widened this from money-and-contacts to measured attributes as
    well -- a refresh rate, a rating, a battery life, a weight. Measured
    before that change, against a real monitor listing with six invented
    replies, this caught one of the six: the price. A fabricated 240Hz
    read exactly like a retrieved one, and it is the number that decides
    the purchase.

    Nothing else here changed. ``attribute_values`` produces tokens in the
    same shape ``_digits`` already did, so the decision below, the repair,
    the bilingual honesty line and the parked browser offer all applied to
    the wider vocabulary unaltered.

    ``spoken`` marks text Elaina said, where an ordinary unit in ordinary
    speech ("let it steep for fourteen hours") is not a claim. Evidence is
    read permissively: a measurement in a retrieved document is a fact
    whatever prose surrounds it.
    """
    return (
        _digits(text)
        | _contacts(text)
        | attribute_values.tokens(text, spoken=spoken)
    )


_BARE_NUMBER = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?(?![\d,])")


def _mangled_numbers(reply: str, source: str) -> set[str]:
    """Numbers in the reply that are a damaged copy of one in the source.

    A bare number is deliberately not money to the readers above -- a year,
    a count and a duration all look the same, and treating them as amounts
    is how half a phone number became a rental budget. But a number the
    person just said, coming back with a digit missing, is not a different
    number. It is the same one, wrong:

        User:   My budget is 1500. Repeat that back to me.
        Elaina: Your budget is 150.

    Only a near-miss counts. "You could stretch to 2000" is a different
    figure and this says nothing about it.
    """
    def bare(text: str) -> set[str]:
        return {
            match.group(0).replace(",", "")
            for match in _BARE_NUMBER.finditer(str(text or ""))
        }

    said, given = bare(reply), bare(source)
    if not said or not given:
        return set()
    mangled = set()
    for number in said - given:
        for original in given:
            # A dropped digit means one digit, from a number that still has
            # two. Measured: "물은 100도에서 얼잖아" answered with "0도에서
            # 업니다" -- the correction -- read as "100" with two digits
            # dropped, and deleted. A single digit is the end of almost
            # every number.
            if number == original or len(original) - len(number) != 1 or len(number) < 2:
                continue
            # A dropped digit, from either end.
            if original.startswith(number) or original.endswith(number):
                mangled.add(number)
                break
    return mangled


# Both, and by accident rather than design: what it compares are
# numbers and names, which do not change shape between languages. The
# sentence it *puts back* is language-specific and lives in
# brain/guard_lines.py for that reason.
LANGUAGES = ("en", "ko")


# A name in quotes. English has capitals to mark a business; Korean does
# not, and quoting is what it uses instead -- '김치찌개 전문점'. Both
# straight and typographic pairs, because a model emits either.
_QUOTED_NAME = re.compile(
    "[\u2018\u201c\"']"
    "\\s*([^\u2019\u201d\"'\\n]{2,40}?)\\s*"
    "[\u2019\u201d\"']"
)


def _prices_a_named_place(reply: str) -> bool:
    """Whether this reply puts a price on somewhere it has named.

    The one shape that is a claim about the world even when nothing was
    looked up. Stating a price from general knowledge is fine -- "a coffee
    in Seoul is about 5,000 won" -- and this guard has always stood down
    for it. Naming an establishment *and* what it charges is not that:

        서울에 있는 '김치찌개 전문점'에서 10,000원에 먹을 수 있습니다.

    Nothing was searched. The restaurant and the price were both invented,
    and the person is being told where to go and what it costs.

    Deliberately requires both halves. A quoted name alone is often a film
    or a dish, and a price alone is ordinary conversation.
    """
    text = str(reply or "")
    if not _MONEY.search(text):
        return False
    return bool(_QUOTED_NAME.search(text))


class GroundedValueGuard:
    """Tell a looked-up figure from an invented one."""

    @classmethod
    def findings(cls, reply: str, sources) -> list[dict]:
        """Every checkable value in the reply, and what stands behind it.

        ``sources`` is ``(label, text)`` pairs -- the turn's evidence, one
        entry per ledger item (brain/evidence.py), and the person's own
        words. Nothing is changed here: this is the validator half, so that
        what the stage concluded is recorded whether or not it acts on it
        (docs/PHASE3_PLAN.md 3C).

        ``status`` is ``supported`` (a source holds it), ``conflicting`` (a
        damaged copy of a number a source holds: 150 for 1500) or
        ``unsupported``.
        """
        sources = [(str(label), str(text or "")) for label, text in sources]
        evidence = "\n".join(text for _, text in sources)
        found: list[dict] = []
        for value in sorted(_values(reply)):
            where = next(
                (label for label, text in sources
                 if value in _values(text, spoken=False)),
                "",
            )
            found.append({
                "value": value,
                "status": "supported" if where else "unsupported",
                "source": where,
            })
        for value in sorted(_mangled_numbers(reply, evidence)):
            where = next(
                (label for label, text in sources if _mangled_numbers(value, text)),
                "",
            )
            found.append({"value": value, "status": "conflicting", "source": where})
        return found

    @classmethod
    def unsupported_amounts(cls, reply: str, evidence: str) -> set[str]:
        """Money in the reply that the evidence does not contain."""
        return _digits(reply) - _digits(evidence)

    @classmethod
    def unsupported_values(cls, reply: str, evidence: str) -> set[str]:
        """Every checkable value in the reply the evidence does not contain."""
        return _values(reply) - _values(evidence, spoken=False)

    @classmethod
    def needs_correction(
        cls,
        reply: str,
        *,
        evidence: str,
        action_performed: bool,
        trusted_result: bool = False,
        disputed: bool = False,
        grounded_subject: bool | None = None,
    ) -> bool:
        """Whether the reply states a value nothing behind it supports.

        ``action_performed`` used to end this immediately, on the reasoning
        that a capability having run meant the answer was grounded. Measured
        live, that is the case the guard was most needed for: a 47-second
        search came back with rental listings, and the answer to "give me
        the contact information" was a phone number and an email address
        that appeared in none of it. An action that ran and found something
        else grounds nothing.

        What an action does change is *what* to check against -- the caller
        passes what it actually retrieved. Two exemptions remain:

        * ``trusted_result`` -- a verified tool or planner result, whose
          values came from the machine rather than the model. Reading a real
          number off a real page must not be stripped;
        * no evidence at all after an action, which is an ordinary desktop
          action ("Playing Bang Bang by IVE") with no text behind it.
        """
        if trusted_result:
            return False
        if grounded_subject is None:
            # Back-compatible reading for callers that pass only evidence.
            grounded_subject = bool(str(evidence or "").strip())
        # A value the person supplied in this very turn, coming back
        # changed, is wrong whether or not anything was looked up.
        # Measured live:
        #
        #     User:   My budget is 1500. Repeat that back to me.
        #     Elaina: Your budget is 150.
        #
        # The guard stood down because nothing had been researched, which
        # is the right test for "did she invent a figure" and the wrong one
        # for "did she mangle the person's own".
        contradicts_the_user = bool(
            (_values(evidence, spoken=False)
             and cls.unsupported_values(reply, evidence))
            or _mangled_numbers(reply, evidence)
        )
        if not grounded_subject and not contradicts_the_user and not (
            disputed and _values(reply)
        ) and not _prices_a_named_place(reply):
            # No grounded subject means ordinary conversation, and most
            # numbers in it are fine to state from general knowledge -- "a
            # coffee in Seoul is about 5,000 won" is not a claim about a
            # live value. The exception is a turn that has just challenged
            # the value: there, having nothing behind it is the whole
            # problem, and she is about to say it again. Which is what
            # happened -- told a phone number looked wrong, she gave back
            # the same phone number.
            return False
        return bool(
            cls.unsupported_values(reply, evidence)
            or _mangled_numbers(reply, evidence)
        )

    @classmethod
    def correct_values(
        cls, reply: str, *, evidence: str, offer: str, partial_offer: str = "",
    ) -> str:
        """Drop the sentences carrying values nothing checked.

        ``partial_offer`` is what to say when some of the answer survives.
        The whole-answer line ("I looked and couldn't confirm that") after a
        surviving claim reads as the claim being withdrawn, which it was
        not -- only the sentence with the number went.
        """
        mangled = _mangled_numbers(reply, evidence)
        unsupported = cls.unsupported_values(reply, evidence) or _values(reply)
        if not unsupported and not mangled:
            return reply
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(str(reply).strip())
            if sentence.strip()
            and not (_values(sentence) & unsupported)
            and not _mangled_numbers(sentence, evidence)
        ]
        offer = str(offer or "").strip()
        rebuilt = " ".join(kept).strip()
        if rebuilt and offer:
            # One offer per answer. Measured live, session 8: the model had
            # already ended with "Would you like me to find some options?"
            # and the guard appended "I haven't actually checked that --
            # want me to look it up?" behind it. Two questions, and only
            # one of them was parked, so answering the wrong one did
            # nothing.
            try:
                from brain.response_policy import ClosingOfferGuard

                rebuilt = " ".join(
                    sentence for sentence in kept
                    if not ClosingOfferGuard.offers_to_act(sentence)
                ).strip()
            except Exception:
                pass
            return f"{rebuilt} {partial_offer.strip() or offer}".strip()
        return rebuilt or offer or reply

    @classmethod
    def correct(cls, reply: str, *, evidence: str, offer: str) -> str:
        """Drop the sentences carrying invented figures, then offer to check.

        Sentences without a money claim are kept exactly as written -- the
        rest of the answer may be perfectly good.
        """
        unsupported = cls.unsupported_amounts(reply, evidence)
        if not unsupported:
            return reply
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(str(reply).strip())
            if sentence.strip() and not (_digits(sentence) & unsupported)
        ]
        offer = str(offer or "").strip()
        rebuilt = " ".join(kept).strip()
        if rebuilt and offer:
            return f"{rebuilt} {offer}"
        return rebuilt or offer or reply


# ---------------------------------------------------------------- entities
#
# The same failure as an invented price, in a different shape. Measured
# live, with no search behind any of them:
#
#     "check out local music stores in Seoul like Melody House or
#      Guitar Center Korea"
#     "you might want to check out local music stores like GS25 or Hanaro"
#
# GS25 is a convenience store. Melody House and Music Zone are not places
# that exist. A named business is a factual claim about the world, and an
# unchecked one is worse than saying nothing, because it sends someone out
# of the house.
#
# Deliberately narrow, on the same principle as the money guard: naming a
# dish, a city or a genre is fine, and most capitalised words in ordinary
# conversation are none of Elaina's business to second-guess.

# Only a reply that is *sending the person somewhere* is checked. "Have
# bibimbap tonight" names no business and needs no evidence.
_NAMES_A_PLACE_TO_GO = re.compile(
    # "The best places to sell secondhand items in Korea are Coupang
    # Auction, Noon, and KakaoTalk marketplace" is sending someone
    # somewhere as surely as naming a shop is, and "place" was not here.
    r"\b(?:place|places|somewhere|platform|platforms|site|sites|app|apps|"
    r"store|stores|shop|shops|shopping|retailer|retailers|market|"
    r"markets|restaurant|restaurants|cafe|cafes|café|bar|bars|hotel|hotels|"
    r"branch|branches|outlet|outlets|dealer|dealers|"
    r"check(?:ing|ed)?\s+out|head\s+(?:to|over)|visit|go\s+to|"
    r"recommend|buy\s+(?:it|one|them)?\s*"
    r"(?:at|from)|available\s+at|sold\s+at|try)\b"
    r"|매장|가게|지점"
    # The Korean counterparts of "recommend", "visit", "try" and "check
    # out". Only 매장/가게/지점 were here, so a Korean reply recommending a
    # drama or a restaurant never reached the name check at all.
    r"|추천|권해|방문|들러|가\s?보|식당|맛집|카페|전문점|시청|보시면|보십시오"
    r"|보세요|드셔\s?보",
    re.IGNORECASE,
)

# A proper name: either two or more capitalised words in a row, or a single
# capitalised token that is not merely the start of a sentence.
_PROPER_NAME = re.compile(
    r"\b([A-Z][A-Za-z0-9&'’-]*(?:\s+(?:of|de|the|and)\s+[A-Z][A-Za-z0-9&'’-]*"
    r"|\s+[A-Z][A-Za-z0-9&'’-]*){1,3})\b"
    r"|\b([A-Z][A-Za-z]*\d[A-Za-z0-9]*)\b"
    # A lone capitalised word: "... or Hanaro for guitars". Sentence-initial
    # ones are dropped below, where the preceding text can be looked at.
    r"|\b([A-Z][a-z]{2,})\b"
)

_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|--\s*|\n)\s*$")

# Capitalised words that are never a business.
_NOT_A_BUSINESS = frozenset({
    "i", "i'm", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november",
    "december", "korean", "korea", "japanese", "chinese", "italian",
    "french", "thai", "indian", "mexican", "american", "english",
    "krw", "usd", "eur", "gbp", "jpy", "won",
})


def _proper_names(text: str) -> list[str]:
    """Names in the text that look like they belong to a real business."""
    text = str(text or "")
    found: list[str] = []
    for match in _PROPER_NAME.finditer(text):
        name = (
            match.group(1) or match.group(2) or match.group(3) or ""
        ).strip(" .,;:")
        if not name:
            continue
        # A capital that only opens a sentence is grammar, not a name --
        # and it must be dropped from the *front* of a longer match too, or
        # "Try Han River BBQ" is read as a business called "Try Han River".
        if _SENTENCE_START.search(text[:match.start()]):
            words = name.split()
            if len(words) == 1:
                continue
            name = " ".join(words[1:])
        words = name.split()
        if not words or all(
            word.casefold() in _NOT_A_BUSINESS for word in words
        ):
            continue
        if name.casefold() in _NOT_A_BUSINESS:
            continue
        found.append(name)
    return list(dict.fromkeys(found))


# A Korean name has no capital to find it by. What Korean writing uses in
# its place is quotation marks: '오징어 게임', '서울 콩고리'. Measured over
# every Korean turn of the paired measurements: 30 of 432 replies quoted a
# name, and the guard below checked none of them -- '더 블랙 블레이드', a
# Canadian drama, and '서울 콩고리', a restaurant, were recommended with no
# search behind them, while the same kind of English reply was retracted.
_QUOTED = re.compile(r"['‘\"“「『]([^'’\"”」』\n]{1,40})['’\"”」』]")
# Quoted *speech* is not a name: "'이 뜨거워졌어요'라는 표현".
_QUOTED_SPEECH = re.compile(r"(?:어요|아요|해요|니다|네요|군요|죠|까요)\s*[.!?]?$")


def _quoted_korean_names(text: str) -> list[str]:
    """Names in the text written the Korean way, in quotation marks."""
    found: list[str] = []
    for match in _QUOTED.finditer(str(text or "")):
        span = " ".join(match.group(1).split())
        # A Latin name in quotes is the capitalised pass's to find.
        if not re.search(r"[가-힣]", span) or _QUOTED_SPEECH.search(span):
            continue
        found.append(span)
    return list(dict.fromkeys(found))


def names_something_specific(text: str) -> bool:
    """Whether this answer points at a particular thing at all.

    Not whether the thing is real -- that is what the guards below are for.
    Only whether the sentence gets as far as naming one, so a layer whose
    job is to supply a missing name can tell "she named nothing" from "she
    named something".
    """
    return bool(_proper_names(text) or _quoted_korean_names(text))


def _grounded_names(*texts: str) -> set[str]:
    """Names that appear in something real -- evidence, or the user's words."""
    grounded: set[str] = set()
    for text in texts:
        lowered = str(text or "").casefold()
        for name in _proper_names(str(text or "")):
            grounded.add(name.casefold())
        grounded.update(
            word for word in re.findall(r"[a-z0-9&'’.-]{3,}", lowered)
        )
    return grounded


# The head noun of a geographic feature. Session 2: widening the trigger
# above to catch "the best places to sell" also caught "places to travel",
# and this guard -- which exists to stop her sending someone to a shop that
# does not exist -- rejected Mount Rainier National Park, Olympic National
# Park, the San Juan Islands, the Columbia River Gorge and the Pacific
# Coast Highway as unverified businesses.
#
# A landform is not a business. The distinction is carried by the name's
# own head noun, which is a closed class, so this needs no list of parks.
_LANDFORM = frozenset({
    "park", "parks", "island", "islands", "isle", "mountain", "mountains",
    "mount", "mt", "lake", "lakes", "river", "gorge", "canyon", "valley",
    "beach", "beaches", "bay", "cape", "coast", "highway", "trail",
    "trails", "falls", "peninsula", "forest", "glacier", "volcano",
    "sound", "strait", "desert", "hill", "hills", "ridge", "peak",
    "springs", "harbor", "harbour", "reserve", "wilderness",
})


_KOREAN_LANDFORM = re.compile(
    r"(?:산|섬|강|호수|공원|해변|해수욕장|폭포|계곡|반도|해협)$"
)


def _is_a_place(name: str) -> bool:
    """Whether this is somewhere on a map rather than a business."""
    words = [word.casefold().strip(".,") for word in name.split()]
    if not words:
        return False
    # The same exemption in Korean, where the head noun is the name's last
    # syllables: '한라산', '제주도 협재 해수욕장', '설악산 국립공원'.
    if re.search(r"[가-힣]", name):
        return bool(_KOREAN_LANDFORM.search(name))
    # "Mount Rainier National Park", "San Juan Islands", "Mt Baker".
    if words[-1] in _LANDFORM or words[0] in _LANDFORM:
        return True
    try:
        from brain.user_locale import _PLACE_COUNTRIES
    except Exception:
        return False
    lowered = name.casefold()
    if lowered in _PLACE_COUNTRIES:
        return True
    return all(word in _PLACE_COUNTRIES for word in words)


# ---------------------------------------------------------------- disputes
#
# Being told a claim is wrong is the strongest signal it needs checking,
# and it was read as the weakest. Measured live, twice in one session:
#
#   "...doesn't seem like a right number to me"  -> the same number again
#   "isn't KakaoTalk a messaging app?"           -> direct_answer,
#       "she can answer this from what she already knows", and a
#       marketplace section that was never checked to exist
#
# Read as a shape rather than a phrase list: the turn either says a prior
# claim is wrong, asks whether it is, or presupposes it is by challenging
# what the thing actually is.
# A dispute says the claim is *wrong*. Session 2 found the first version
# of this too wide: "Okay, that's not that much. Thank you, though." tripped
# it, so she re-ran a full web search and read back the same price. "That's
# not much" is a judgement about the size of a number and agrees with it;
# "that's not right" says the number is incorrect. Only the second is a
# dispute, so what follows the negation has to name correctness.
_DISPUTES = re.compile(
    # What follows the negation decides it. A definite reference points at
    # the claim and contradicts it -- "that's not *the time* in Seattle",
    # "that's not *what I meant*". A bare quantifier or degree word judges
    # the size of what she said and agrees with it -- "that's not *much*",
    # "not *a lot*", "not *that much*".
    r"\b(?:that'?s|this\s+is|it'?s)\s+(?:not|n[o']t)\s+"
    r"(?:right|correct|true|it|accurate|quite\s+right|"
    r"what\s+\w+|the\s+\w+|my\s+\w+)\b"
    r"|\b(?:doesn'?t|does\s+not|don'?t)\s+(?:seem|look|sound)\b"
    r"|\byou(?:'?re|\s+are)\s+wrong\b"
    r"|\bthat'?s\s+wrong\b"
    r"|\b(?:i\s+don'?t\s+think|not\s+sure)\s+(?:that|it|this|you)\b"
    r"|\bare\s+you\s+sure\b"
    # The same question with the verb dropped, as it is usually said. Only
    # as a whole turn: "you sure know a lot" is a compliment. Measured on
    # the paired arcs -- "확실해?" was a dispute and "you sure about that?",
    # its translation, was not, so the one turn took different paths in the
    # two languages.
    r"|^\s*(?:you|u)\s+sure(?:\s+about\s+(?:that|this|it))?\s*\??\s*$"
    r"|\bisn'?t\s+\w+\s+(?:a|an|the)\b"
    r"|\bthat'?s\s+not\s+(?:right|correct|true|it)\b"
    r"|\bwrong\s+(?:number|answer|one|time|date)\b"
    # First-hand experience, which is the strongest thing a person can
    # offer against a claim about the world -- and it was read as nothing
    # at all. Measured live: told there are no casinos on Bainbridge
    # Island, "But I did go to a casino there with my friends" produced
    # the same sentence again. A "but"/"wait"/"actually" opener, or the
    # emphatic "did", marks it as contradicting rather than reminiscing:
    # "I went to Seattle last year" is not an argument about anything.
    r"|^\s*(?:but|wait|actually|no)\b[^.?!]{0,60}?"
    r"\bi\s*(?:'ve|’ve|\s+have)?\s*(?:did\s+)?"
    r"(?:go|went|been|saw|was|stayed|visited)\b"
    r"|\bi\s+did\s+(?:go|see|visit|stay)\b"
    r"|\bi\s+(?:definitely|actually|really)\s+(?:went|saw|was|have)\b"
    r"|\bi\s+saw\s+(?:one|it|them|him|her)\s+myself\b"
    r"|\bi\s+was\s+there\b"
    r"|\bi\s*(?:'ve|’ve|\s+have)\s+been\s+to\s+one\b"
    # Korean. "맞아?" was here bare, and it is two different questions: "그거
    # 맞아?" challenges what she said, while "베인브리지 섬이었던 것 같은데
    # 맞아?" asks her to confirm the person's *own* guess. Measured in a
    # Korean session, the second was read as a dispute and her next answer
    # opened "이전에 말씀드린 내용이 정확하지 않았습니다" about a claim she had
    # never made. Only a 맞아 aimed at her words -- a demonstrative or a
    # "really" in front of it -- is a dispute now.
    r"|틀렸|아닌\s?것\s?같|가봤"
    r"|(?:그거|그게|그건|이거|이게|이건|정말|진짜|확실)\s*(?:맞아|맞는|맞나|확실)"
    r"|확실해\??\s*$"
    # "확실한 거야?" -- are you sure, said the other common way. Whole turn
    # only: "확실한 방법 알려줘" asks for a reliable method.
    r"|^\s*(?:그거\s*)?확실한\s*거(?:야|지|예요|에요|죠)?\s*\??\s*$"
    # Asking for the evidence behind what she said. "데이터로 알려줘" was read
    # as a question about data in general, and she offered to explain data.
    # It is the person saying the last answer needs backing up -- the same
    # thing, in the same direction, as "are you sure?".
    r"|근거(?!리)|출처|데이터로|자료로|통계로|수치로|증거"
    r"|\b(?:what'?s\s+your|any)\s+source\b|\bsource\?|\bprove\s+it\b"
    r"|\bwith\s+(?:data|numbers|sources|evidence)\b"
    r"|\bback\s+(?:it|that)\s+up\b"
    r"|\bhow\s+do\s+you\s+know\b"
    r"|\bwhere\s+did\s+you\s+(?:get|hear|read|see)\s+(?:that|this)\b",
    re.IGNORECASE,
)


def reads_as_dispute(text: str) -> bool:
    """Whether this turn says something she just claimed is wrong."""
    return bool(_DISPUTES.search(str(text or "")))


# "I found studio apartments in Seattle under $1500 on Zillow." Said three
# times, to three requests for the names, with Candidates: (none)
# throughout. A find you cannot name is the same failure as an invented
# price -- indistinguishable from a real answer, and acted on.
#
# Only a claim to have *already* found something counts. "I couldn't find
# anything" and "you could try filtering on Zillow" claim nothing.
_CLAIMS_A_FIND = re.compile(
    r"\bi\s*(?:'ve|’ve|\s+have)?\s*found\b"
    r"|\bi\s+did\s+find\b"
    r"|\bhere\s+are\s+(?:some|a few|the)\b[^.]{0,40}\bi\s+found\b"
    r"|\bthere\s+are\s+(?:several|some|a\s+few|multiple)\s+"
    r"(?:listings?|options?|places?|results?)\b"
    r"|\bfound\s+(?:several|some|a\s+few|multiple|two|three)\b",
    re.IGNORECASE,
)
_FOUND_NOTHING = re.compile(
    r"\b(?:could\s?n[o']t|did\s?n[o']t|was\s?n[o']t\s+able\s+to|"
    r"unable\s+to|no\s+luck)\b[^.]{0,20}\bfind\b"
    r"|\bfound\s+(?:nothing|none|no\b)",
    re.IGNORECASE,
)


def claims_a_find(text: str, *, named: tuple[str, ...] = ()) -> bool:
    """Whether the reply says it found things without naming any."""
    said = str(text or "")
    if _FOUND_NOTHING.search(said) or not _CLAIMS_A_FIND.search(said):
        return False
    if named and any(str(name).casefold() in said.casefold() for name in named):
        return False
    # A place is where she looked and a site is what she looked in --
    # neither is a thing she found. "I found studio apartments in Seattle
    # on Zillow" names Seattle and Zillow and no listing at all, which is
    # exactly the sentence this exists for.
    remainder = _CLAIMS_A_FIND.sub(" ", said)
    for name in _proper_names(remainder):
        if _is_a_place(name):
            continue
        if re.search(
            r"\b(?:on|at|from|via|through|in)\s+" + re.escape(name),
            remainder, re.IGNORECASE,
        ):
            continue
        return False
    return True


def names_an_unfound_thing(
    text: str, *, evidence: str = "", request: str = "",
) -> tuple[str, ...]:
    """Proper names offered as the answer that nothing actually found.

    Narrower than :func:`unverified_entities`, which is about being sent
    somewhere. This is about being handed a *thing* -- a product, a model,
    a title -- after a search that came back with nothing. Measured live:
    six candidates, none of them a fit, and the answer named an Epiphone
    Les Paul SL that appeared in none of them.

    A name the person themselves used is theirs, and a place is where you
    look rather than what you find; both are left alone.
    """
    said = str(text or "")
    if not said.strip():
        return ()
    seen = f"{evidence or ''} {request or ''}".casefold()
    found: list[str] = []
    for name in _proper_names(said):
        if _is_a_place(name) or name.casefold() in seen:
            continue
        # One capitalised word is as often a sentence opening as a name.
        if len(name.split()) < 2:
            continue
        if name not in found:
            found.append(name)
    return tuple(found)


# A brand and the model it sells: "LG 45GX950A-B", "Samsung Odyssey G55C".
#
# _PROPER_NAME cannot see these. Its multi-word branch needs every word to
# start with a capital, and a model number starts with a digit as often as
# not -- so "The LG 45GX950A-B is the best fit" yielded "LG", one word, and
# the guards that skip single words skipped it. That is the shape most
# product recommendations arrive in, which is how a monitor from the model's
# memory was offered beside cards showing two different ones.
#
# Used only by names_outside_the_results: the older guards keep the older
# reading, because widening what counts as a name changes what they remove.
_LEADING = frozenset({"the", "a", "an", "this", "that", "my", "our", "its"})
_BRAND_AND_MODEL = re.compile(
    r"\b((?:[A-Z][\w&'\u2019-]*\s+){1,4}"
    r"(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[A-Za-z0-9][\w-]*)\b"
)


def _brand_models(text: str) -> list[str]:
    """Brand-plus-model names, which _PROPER_NAME is not shaped to catch."""
    found: list[str] = []
    for match in _BRAND_AND_MODEL.finditer(str(text or "")):
        words = match.group(1).split()
        while words and words[0].casefold() in _LEADING:
            words = words[1:]
        while words and words[0].casefold() in _NOT_A_BUSINESS:
            words = words[1:]
        if len(words) < 2:
            continue
        name = " ".join(words)
        if name not in found:
            found.append(name)
    return found


def _distinctive_words(name: str) -> list[str]:
    """The words of a name that could only belong to this one thing."""
    return [
        word for word in re.findall(r"[^\W_]{2,}", str(name or "").casefold())
        if word not in _NOT_A_BUSINESS
    ]


def _matches_a_candidate(name: str, candidates) -> bool:
    """Whether this name is one of the things actually in hand.

    Deliberately generous about *form*: "the Sofitel" is the candidate
    "Sofitel Ambassador Seoul Hotel", and a reply is allowed to shorten a
    name it is naming. It is not generous about *identity* -- something has
    to be shared, and the shared part has to be a word that distinguishes
    the thing rather than the category it belongs to.
    """
    words = set(_distinctive_words(name))
    if not words:
        return False
    # A word the whole set shares is the category, not an identity. Every
    # hotel in a set of hotels contains "hotel", so matching on it made
    # "Lotte Hotel World" -- which nothing found -- indistinguishable from
    # "Hotel Inspiroom Jongro", which something did. Worked out from the
    # candidates themselves rather than from a list of category nouns,
    # because the categories are not knowable in advance.
    shared: dict[str, int] = {}
    for candidate in candidates or ():
        for word in set(_distinctive_words(str(candidate))):
            shared[word] = shared.get(word, 0) + 1
    for candidate in candidates or ():
        held = set(_distinctive_words(str(candidate)))
        if not held:
            continue
        for word in (words & held):
            if shared.get(word, 0) < 2:
                return True
    return False


def names_outside_the_results(
    text: str, *, candidates=(), request: str = "",
) -> tuple[str, ...]:
    """Things named as the answer that are not among the results in hand.

    The other guards in this module ask whether *anything* was found. This
    asks whether the thing she named is one of them, which is a different
    question and the one that was never being asked.

    Measured live, in a single eight-turn conversation:

        cards:  5K2K OLED, GX9 39
        Elaina: "The LG 45GX950A-B is the best fit, it's a 5K2K OLED
                 curved gaming monitor with 165Hz refresh rate..."

        cards:  Seoul DDJ STAY, Hotel Inspiroom Jongro, Sofitel Ambassador
        Elaina: "L'Escape offers luxury..., while the JW Marriott
                 Dongdaemun feels more intimate"

    Every named thing came out of the model's memory, and every one was
    offered as though the search had returned it -- alongside cards showing
    entirely different things. The refresh rate was invented too, which is
    the same failure wearing a number.

    A name the person used themselves is theirs and is left alone, as is a
    place, which is where you look rather than what you find.
    """
    said = str(text or "")
    if not said.strip() or not candidates:
        return ()
    theirs = str(request or "").casefold()
    outside: list[str] = []
    seen_names = list(dict.fromkeys(_brand_models(said) + _proper_names(said)))
    for name in seen_names:
        if _is_a_place(name) or name.casefold() in theirs:
            continue
        # One capitalised word is as often a sentence opening as a name.
        if len(name.split()) < 2:
            continue
        if _matches_a_candidate(name, candidates):
            continue
        if name not in outside:
            outside.append(name)
    return tuple(outside)


def claim_subjects(text: str) -> list[str]:
    """The nouns a claim is about, for re-checking it a different way.

    A claim that has been searched once must not become unfalsifiable, and
    re-running the query that produced it is how that happens. These are
    what the new search keeps: the thing and the place, without the yes/no
    shape of the question that has already been answered.
    """
    text = str(text or "")
    found: list[str] = []
    for name in _proper_names(text):
        if name not in found:
            found.append(name)
    # Plus the plain nouns the sentence turns on, which a proper-name
    # reader will not see: "casinos", "gambling venues".
    for word in re.findall(r"\b[a-z]{4,}\b", text.casefold()):
        if word in _CLAIM_STOPWORDS or word in {n.casefold() for n in found}:
            continue
        if word not in found:
            found.append(word)
    return found[:6]


# Grammar and the vocabulary of denial, which say nothing about what the
# claim was about.
_CLAIM_STOPWORDS = frozenset({
    "there", "their", "they", "them", "this", "that", "these", "those",
    "with", "from", "have", "has", "had", "been", "being", "were", "was",
    "will", "would", "could", "should", "about", "into", "your", "yours",
    "here", "what", "when", "where", "which", "while", "also", "just",
    "only", "very", "much", "many", "some", "any", "none", "legal",
    "illegal", "area", "residential", "known", "find", "found", "look",
    "know", "think", "sure", "like", "well", "yeah", "okay", "please",
    "actually", "really",
})


def carries_a_checkable_claim(text: str) -> bool:
    """Whether a reply asserted anything that could be checked.

    Disagreeing about an opinion ("that's not a good idea") is a
    conversation. Disagreeing about a number, an address, or a named
    business is a question with an answer, and that is the only kind worth
    going and looking up.
    """
    text = str(text or "")
    return bool(_values(text) or _proper_names(text))


def unverified_entities(
    reply: str, *, evidence: str = "", request: str = "",
) -> tuple[str, ...]:
    """Businesses the reply names that nothing actually checked.

    A name is fine when it came back from a real search, when the person
    said it themselves, or when it is a place rather than a business. What
    is left is Elaina telling someone to go somewhere she made up.
    """
    reply = str(reply or "")
    if not _NAMES_A_PLACE_TO_GO.search(reply):
        return ()
    grounded = _grounded_names(evidence, request)
    haystack = " ".join((str(evidence or ""), str(request or ""))).casefold()
    unverified = []
    for name in _proper_names(reply) + _quoted_korean_names(reply):
        lowered = name.casefold()
        if _is_a_place(name):
            continue
        # A Korean name is checked as the string it is: there is no
        # capitalised form to collect, and '오징어 게임' grounded by the
        # evidence is '오징어 게임' appearing in it.
        if re.search(r"[가-힣]", name):
            if lowered not in haystack:
                unverified.append(name)
            continue
        # A multi-word name has to appear as that name. Checking its words
        # separately let "Guitar Center" pass because the person had said
        # "guitar" -- and Guitar Center has no branch in Seoul.
        if " " in lowered:
            if lowered in haystack:
                continue
        elif lowered in grounded:
            continue
        unverified.append(name)
    return tuple(unverified)


# Whether a reply is sending someone to a *place*, as opposed to naming a
# drama or a film. The guard's offer said "I don't want to send you
# somewhere I haven't checked" after a drama recommendation -- the words
# were right for shops and wrong for everything else.
_A_PLACE_NOUN = re.compile(
    r"\b(?:place|places|somewhere|store|stores|shop|shops|restaurant|"
    r"restaurants|cafe|cafes|café|bar|bars|hotel|hotels|branch|branches|"
    r"outlet|outlets|dealer|dealers|market|markets|marketplace|site|sites|"
    r"app|apps|platform|platforms)\b"
    r"|매장|가게|지점|식당|맛집|카페|전문점|호텔|사이트|앱|장터",
    re.IGNORECASE,
)


def sends_somewhere(text: str) -> bool:
    """Whether this reply points the person at a place to go."""
    return bool(_A_PLACE_NOUN.search(str(text or "")))
