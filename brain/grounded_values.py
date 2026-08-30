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


def _digits(text: str) -> set[str]:
    """Bare digit strings, so ₩120,000 and "120000 won" compare equal."""
    return {
        re.sub(r"\D", "", match.group(0))
        for match in _MONEY.finditer(str(text or ""))
        if re.sub(r"\D", "", match.group(0))
    }


class GroundedValueGuard:
    """Tell a looked-up figure from an invented one."""

    @classmethod
    def unsupported_amounts(cls, reply: str, evidence: str) -> set[str]:
        """Money in the reply that the evidence does not contain."""
        return _digits(reply) - _digits(evidence)

    @classmethod
    def needs_correction(
        cls,
        reply: str,
        *,
        evidence: str,
        action_performed: bool,
    ) -> bool:
        if action_performed:
            return False
        if not str(evidence or "").strip():
            # No grounded subject means this is ordinary conversation, not
            # a follow-up about something Elaina looked up.
            return False
        return bool(cls.unsupported_amounts(reply, evidence))

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
    r"\b(?:store|stores|shop|shops|shopping|retailer|retailers|market|"
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
