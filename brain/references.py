"""Which of the things she just listed the person means.

"Open the second one." The word "one" names nothing on its own; what makes
it resolvable is that a list was produced a moment ago and both sides can
count. That list already exists -- ``RecommendationProblem.candidates`` holds
up to eight names a search actually found -- and until now nothing read it
back. It was stored, logged, and never consulted, so an ordinal reference to
a spoken result set resolved against nothing at all.

The browser planner has counted ordinals for a while, but only against a live
results *page* ("click the first result"). That is a different situation with
the same vocabulary, so the vocabulary lives here and both use it rather than
keeping two tables that can drift.

Two rules, and the second matters more than the first:

* a reference resolves only when a real list is in hand and the index is
  inside it;
* anything else returns unresolved **with a reason**, so the caller asks
  instead of picking. An ordinal that points past the end of the list is not
  a near-miss to round off -- "the fifth one" against three results means the
  person is talking about something she is not holding.

Nothing here is specific to hotels, monitors or keyboards. It is the closed
grammatical class English uses for counting, and it must stay that way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The counting words, and the position each names. ``-1`` is the end of the
# list rather than a fixed position, which is what "the last one" means.
ORDINAL_INDEX: dict[str, int] = {
    "first": 0, "1st": 0,
    "second": 1, "2nd": 1,
    "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3,
    "fifth": 4, "5th": 4,
    "sixth": 5, "6th": 5,
    "seventh": 6, "7th": 6,
    "eighth": 7, "8th": 7,
    "last": -1, "final": -1,
}

# The word for a position, for reading a resolved choice back to the person.
ORDINAL_WORD: dict[int, str] = {
    0: "first", 1: "second", 2: "third", 3: "fourth",
    4: "fifth", 5: "sixth", 6: "seventh", 7: "eighth",
}

_COUNT_WORD = {"two": 2, "three": 3, "four": 4, "both": 2}

_ORDINALS = "|".join(sorted(ORDINAL_INDEX, key=len, reverse=True))

# "the second one", "the third", "number two", "#2". The trailing noun is
# deliberately open: "the second one", "the second hotel" and "the second
# option" are the same reference, and constraining it to a list of nouns
# would be the keyword table this module exists to avoid.
_ORDINAL_REFERENCE = re.compile(
    rf"\b(?:the\s+)?(?P<ordinal>{_ORDINALS})\b(?!\s+(?:time|thing)\b)"
    rf"|\bnumber\s+(?P<number>\d{{1,2}})\b"
    rf"|#(?P<hash>\d{{1,2}})\b",
    re.IGNORECASE,
)

# "the first two", "both of them", "the top three".
_RANGE_REFERENCE = re.compile(
    r"\b(?:the\s+)?(?:first|top)\s+(?P<count>two|three|four|\d)\b"
    r"|\bboth\b",
    re.IGNORECASE,
)

# A reference that points at the set without counting into it. These are
# resolvable only when exactly one thing is in hand.
_BARE_REFERENCE = re.compile(
    r"\b(?:that|this)\s+one\b|\bit\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Reference:
    """What the person pointed at, or why nothing was chosen."""

    resolved: bool = False
    index: int = -1
    value: str = ""
    values: tuple[str, ...] = ()
    reason: str = ""

    @property
    def ambiguous(self) -> bool:
        """Whether something was pointed at that could not be pinned down."""
        return not self.resolved and bool(self.reason)

    def log_line(self) -> str:
        if self.resolved:
            where = ORDINAL_WORD.get(self.index, str(self.index + 1))
            return f"resolved to the {where}: {self.value!r}"
        return f"unresolved: {self.reason}"


def names_a_position(text: str) -> bool:
    """Whether the turn counts into a list at all."""
    said = str(text or "")
    return bool(
        _ORDINAL_REFERENCE.search(said) or _RANGE_REFERENCE.search(said)
    )


def resolve(text: str, candidates) -> Reference:
    """Pick the candidate the text points at, or say why none was picked.

    ``candidates`` is whatever list is genuinely in hand -- normally
    ``RecommendationProblem.candidates``. An empty list never resolves: the
    right answer to "open the second one" when nothing was listed is a
    question, not a guess.
    """
    said = " ".join(str(text or "").split())
    names = tuple(
        str(name).strip() for name in (candidates or ()) if str(name).strip()
    )
    if not said:
        return Reference(reason="nothing was said")

    span = _RANGE_REFERENCE.search(said)
    ordinal = _ORDINAL_REFERENCE.search(said)
    if span is None and ordinal is None:
        return Reference(reason="the turn does not point at a position")

    if not names:
        # Pointed at a list that does not exist. This is the case that must
        # never become an action.
        return Reference(
            reason="a position was named but no result set is in hand",
        )

    if span is not None:
        raw = (span.group("count") or "").casefold()
        count = _COUNT_WORD.get(raw) or (int(raw) if raw.isdigit() else 2)
        if count > len(names):
            return Reference(
                reason=(
                    f"asked for {count} of them and only {len(names)} "
                    "are in hand"
                ),
            )
        return Reference(
            resolved=True, index=0, value=names[0], values=names[:count],
            reason="",
        )

    word = (ordinal.group("ordinal") or "").casefold()
    if word:
        index = ORDINAL_INDEX[word]
    else:
        digits = ordinal.group("number") or ordinal.group("hash") or ""
        index = int(digits) - 1
        if index < 0:
            return Reference(reason="that is not a position in the list")

    if index == -1:
        index = len(names) - 1
    if index >= len(names):
        # Deliberately not clamped. "The fifth one" against three results
        # means the person is talking about something she is not holding,
        # and quietly handing back the third is how a wrong thing gets
        # opened.
        return Reference(
            reason=(
                f"the {word or index + 1} was named and only "
                f"{len(names)} are in hand"
            ),
        )
    return Reference(
        resolved=True, index=index, value=names[index], values=(names[index],),
    )


def resolve_bare(text: str, candidates) -> Reference:
    """"That one" -- resolvable only when there is exactly one thing.

    Kept separate from :func:`resolve` because it is a weaker signal: a bare
    pointer with several candidates in hand is genuinely ambiguous, and the
    honest answer is to ask which.
    """
    said = " ".join(str(text or "").split())
    names = tuple(
        str(name).strip() for name in (candidates or ()) if str(name).strip()
    )
    if not _BARE_REFERENCE.search(said):
        return Reference(reason="the turn does not point at anything")
    if len(names) == 1:
        return Reference(resolved=True, index=0, value=names[0], values=names)
    if not names:
        return Reference(reason="nothing is in hand to point at")
    return Reference(
        reason=f"{len(names)} things are in hand and none was singled out",
    )
