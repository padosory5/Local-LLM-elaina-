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
* the reply states a **money amount** that appears nowhere in that grounded
  evidence, nor in what the user themselves said.

That combination is specifically "inventing a figure about the thing we
were just discussing", which is the failure this exists for. A number the
user supplied, or one that came back from a real search, always passes.
"""

from __future__ import annotations

import re

# Money only. A plain integer ("three hotels", "2026") is not a claim about
# a live value and must not be second-guessed.
_MONEY = re.compile(
    r"[$₩€£¥]\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s*"
    r"(?:won|krw|usd|eur|gbp|jpy|dollars?|euros?|pounds?|yen|원)\b",
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


def _values(text: str) -> set[str]:
    return _digits(text) | _contacts(text)


class GroundedValueGuard:
    """Tell a looked-up figure from an invented one."""

    @classmethod
    def unsupported_amounts(cls, reply: str, evidence: str) -> set[str]:
        """Money in the reply that the evidence does not contain."""
        return _digits(reply) - _digits(evidence)

    @classmethod
    def unsupported_values(cls, reply: str, evidence: str) -> set[str]:
        """Every checkable value in the reply the evidence does not contain."""
        return _values(reply) - _values(evidence)

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
        if not grounded_subject and not (disputed and _values(reply)):
            # No grounded subject means ordinary conversation, and most
            # numbers in it are fine to state from general knowledge -- "a
            # coffee in Seoul is about 5,000 won" is not a claim about a
            # live value. The exception is a turn that has just challenged
            # the value: there, having nothing behind it is the whole
            # problem, and she is about to say it again. Which is what
            # happened -- told a phone number looked wrong, she gave back
            # the same phone number.
            return False
        return bool(cls.unsupported_values(reply, evidence))

    @classmethod
    def correct_values(cls, reply: str, *, evidence: str, offer: str) -> str:
        """Drop the sentences carrying values nothing checked."""
        unsupported = cls.unsupported_values(reply, evidence) or _values(reply)
        if not unsupported:
            return reply
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(str(reply).strip())
            if sentence.strip() and not (_values(sentence) & unsupported)
        ]
        offer = str(offer or "").strip()
        rebuilt = " ".join(kept).strip()
        if rebuilt and offer:
            return f"{rebuilt} {offer}"
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
    r"|매장|가게|지점",
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


def _is_a_place(name: str) -> bool:
    """Whether this is a city or country rather than a business."""
    try:
        from brain.user_locale import _PLACE_COUNTRIES
    except Exception:
        return False
    lowered = name.casefold()
    if lowered in _PLACE_COUNTRIES:
        return True
    return all(word.casefold() in _PLACE_COUNTRIES for word in name.split())


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
_DISPUTES = re.compile(
    r"\b(?:that'?s|this\s+is|it'?s)\s+(?:not|n[o']t)\b"
    r"|\b(?:doesn'?t|does\s+not|don'?t)\s+(?:seem|look|sound)\b"
    r"|\byou(?:'?re|\s+are)\s+wrong\b"
    r"|\bthat'?s\s+wrong\b"
    r"|\b(?:i\s+don'?t\s+think|not\s+sure)\s+(?:that|it|this|you)\b"
    r"|\bare\s+you\s+sure\b"
    r"|\bisn'?t\s+\w+\s+(?:a|an|the)\b"
    r"|\bthat'?s\s+not\s+(?:right|correct|true|it)\b"
    r"|\bwrong\s+(?:number|answer|one|time|date)\b"
    r"|틀렸|아닌\s?것\s?같|맞아\?",
    re.IGNORECASE,
)


def reads_as_dispute(text: str) -> bool:
    """Whether this turn says something she just claimed is wrong."""
    return bool(_DISPUTES.search(str(text or "")))


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
    for name in _proper_names(reply):
        lowered = name.casefold()
        if _is_a_place(name):
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
