"""What the conversation is currently about, decided once.

Every layer downstream used to work this out for itself. The router
paraphrased the turn, the goal layer read a subject off that paraphrase,
the recommendation problem kept a third answer, and the search query
builder reached past all of them for the raw words. Most of the time they
agreed. When they did not, the result was this, measured live:

    "I'm moving to Seattle on September 18."
    "Do you know which university is there?"   -> finds UW
    "Yep, I'm going there."                    -> "Seattle's a great place"
    "No, I mean I'm going to UW."              -> the same words, again

The router logged the correction correctly. The goal layer still logged
"moving to Seattle", because its subject came from a field the correction
never touched. So the answer was about Seattle twice.

This holds one answer to "what are we talking about", and two rules decide
it:

* an explicit correction wins outright, over everything, immediately --
  "no, I mean X" is the least ambiguous thing a person can say about what
  they meant, and it is the one signal that was being ignored;
* what a correction replaces becomes background rather than disappearing.
  Moving to Seattle is still true after "I mean UW"; it has simply stopped
  being the thing under discussion.

Nothing here calls a model, and nothing here decides what to *do* -- only
what the turn is about, for the layers whose job that is.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace

DEFAULT_TTL_SECONDS = 30 * 60

# "No, I mean X." The correction is the clause after the marker, and it is
# taken whole: whatever they just said is a better description of what they
# meant than anything already held.
_CORRECTIONS = (
    re.compile(
        r"\bno[,!.]?\s+(?:i\s+)?mean(?:t)?\s+(?:that\s+)?(.+)$",
        re.IGNORECASE,
    ),
    re.compile(r"\bi\s+mean(?:t)?\s+(?:that\s+)?(.+)$", re.IGNORECASE),
    re.compile(
        r"\bi(?:'m| am|\s+was)\s+talking\s+about\s+(.+)$", re.IGNORECASE,
    ),
    re.compile(
        r"\bnot\s+[\w' -]{2,30},\s*(?:i\s+mean\s+)?(.+)$", re.IGNORECASE,
    ),
    # Bare "No, X" is the one form here with no correction marker in it, so
    # it has to earn the reading from X's own shape. A correction names a
    # thing -- "No, the blue one", "No, Zillow" -- and what follows it is a
    # noun phrase. A clause with its own subject is a contradiction of what
    # she just said, not a new topic.
    #
    # Measured live: "No, I can see the images. Thank you." was read as a
    # correction, so the subject became that sentence, packing peanuts was
    # retired, and two turns later the search ran on "I can see the images.
    # Thank you University in South Korea".
    re.compile(
        r"^\s*(?:no|nope|nah)[,!.]\s+"
        r"(?!(?:i|you|we|they|he|she|it|that|this|there)\b"
        r"(?:'|’|\s+(?:am|is|are|was|were|do|does|did|don|doesn|didn|"
        r"can|could|will|would|have|has|had|isn|aren|wasn|weren|"
        r"[a-z]+\s+(?:the|a|an|it|that|you|me)\b)))"
        r"(.+)$",
        re.IGNORECASE,
    ),
    re.compile(r"\bactually[,]?\s+(.+)$", re.IGNORECASE),
)

# Background facts worth carrying: where, and when. Deliberately two, and
# deliberately narrow -- a wrong background fact is worse than none,
# because it silently steers every query after it.
# Only verbs that actually mean "this is where I will be". "Going to" was
# in here and matched "I'm going to UW", so a correction about which school
# replaced the city -- and every query after it lost Seattle.
# A proper name does not end at its first lowercase word, and the place it
# sits in is often the comma after it. Measured live: "I'm moving to
# University of Washington, Seattle" stopped at "University", because every
# word had to be capitalised -- so "of" ended the name and Seattle, the
# part that was actually a place, was dropped. Every rental query for the
# rest of that session read "University in South Korea".
#
# The internal lowercase words are a closed set of name particles, and the
# comma tail is taken only when a capitalised word follows it, so a name
# cannot run on into the rest of the sentence.
_NAME = (
    r"[A-Z][\w.'-]*"
    r"(?:\s+(?:of|the|and|de|del|da|von|van|der|di|du|la|le)\s+[A-Z][\w.'-]*"
    r"|\s+[A-Z][\w.'-]*){0,4}"
    r"(?:,\s+[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,2})?"
)
_LOCATION = re.compile(
    r"\b(?:moving|move|relocating|relocate)\s+to\s+"
    rf"({_NAME})"
    r"|\bi(?:'m| am)\s+(?:in|at|based\s+in|living\s+in)\s+"
    rf"({_NAME})"
    r"|\b(?:in|near|around)\s+([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,2})"
    r"\s*(?:[,.]|$)",
)

# A correction is a whole clause, and the clause carries scaffolding the
# subject does not need: "I'm going to UW" is a sentence about UW.
_SUBJECT_SCAFFOLD = re.compile(
    r"^(?:i(?:'m| am|\s+will\s+be)?\s+)?"
    r"(?:going|go|heading|moving|attending|studying|talking)\s+"
    r"(?:to|at|about)\s+",
    re.IGNORECASE,
)

_WHEN = re.compile(
    r"\b(?:on\s+)?((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?)"
    r"|\b(\d{4}-\d{1,2}-\d{1,2})\b"
    r"|\b(\d{1,2}/\d{1,2})\b",
    re.IGNORECASE,
)

# Said instead of the thing itself. A turn that is only this cannot be a
# new subject -- it is a pointer at the one already held.
_DEICTIC_ONLY = re.compile(
    r"^(?:yep|yeah|yes|no|nope|ok|okay|sure|right)?[,. ]*"
    r"(?:i(?:'m| am)\s+)?(?:going|go)?\s*(?:there|here|that|it|those)"
    r"[.! ]*$",
    re.IGNORECASE,
)

_RELATIONAL_REFERENCE = re.compile(
    r"\b(?:near|close\s+to|around)\s+(?:my|the)\s+"
    r"(?:school|university|campus|work|office|home)\b",
    re.IGNORECASE,
)

_EDUCATION_ANCHOR = re.compile(
    r"\b(?:going|go|attending|attend|studying|study|heading)\s+"
    r"(?:to|at)\s+([A-Z]{2,8}|[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,4})"
    r"(?=\s+(?:in|at|near)\b|[,.!?]|$)",
)

_KNOWN_EDUCATION_ALIASES = {
    "uw": "University of Washington",
}

_FILLER = re.compile(
    r"^(?:the|a|an|that|it|this|so|well|and|but|um|uh)\b\s*", re.IGNORECASE,
)


def _clean(value: str) -> str:
    value = " ".join(str(value or "").split()).strip(" ,.;:!?-")
    while True:
        stripped = _FILLER.sub("", value).strip()
        if stripped == value:
            return value
        value = stripped


def read_correction(text: str) -> str:
    """What the person just said they actually meant, or nothing."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    for pattern in _CORRECTIONS:
        match = pattern.search(text)
        if match:
            corrected = _clean(
                _SUBJECT_SCAFFOLD.sub("", _clean(match.group(1))),
            )
            if corrected and len(corrected.split()) <= 14:
                return corrected
    return ""


def read_background(text: str) -> dict[str, str]:
    """Durable facts the turn establishes: where, and when."""
    found: dict[str, str] = {}
    place = _LOCATION.search(str(text or ""))
    if place:
        value = _clean(
            place.group(1) or place.group(2) or place.group(3) or "",
        )
        if value:
            found["location"] = value
    when = _WHEN.search(str(text or ""))
    if when:
        value = _clean(
            when.group(1) or when.group(2) or when.group(3) or "",
        )
        if value:
            found["when"] = value
    return found


def points_at_something_known(text: str) -> bool:
    """Whether the turn is a pointer rather than a subject of its own."""
    return bool(_DEICTIC_ONLY.match(" ".join(str(text or "").split())))


def read_education_anchor(text: str) -> str:
    """An explicitly named school, ahead of a generic router topic."""
    match = _EDUCATION_ANCHOR.search(str(text or ""))
    if not match:
        return ""
    value = _clean(match.group(1))
    return _KNOWN_EDUCATION_ALIASES.get(value.casefold(), value)


@dataclass(frozen=True)
class Focus:
    """The one answer to "what are we talking about"."""

    subject: str = ""
    background: dict[str, str] = field(default_factory=dict)
    corrected_to: str = ""
    superseded: tuple[str, ...] = ()
    expires_at: float = 0.0

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) >= self.expires_at

    def query_context(self) -> tuple[str, ...]:
        """The parts of this worth putting into a search box.

        The subject and where it is. Dates are held for the answer to use
        and kept out of the query, for the same reason a sore throat is:
        they explain the request rather than narrowing the results.
        """
        parts = [self.subject] if self.subject else []
        for name in ("about", "location"):
            value = self.background.get(name, "")
            if not value:
                continue
            if any(value.casefold() in part.casefold() for part in parts):
                continue
            parts.append(value)
        return tuple(part for part in parts if part)

    def log_block(self) -> str:
        """Console only -- so a disagreement between layers is visible."""
        lines = [
            "[Conversation State]",
            f"  Current subject: {self.subject or '(none)'}",
        ]
        if self.background:
            lines.append("  Background:")
            for name, value in self.background.items():
                lines.append(f"    {name}: {value}")
        if self.corrected_to:
            lines.append(f"  Correction applied: {self.corrected_to}")
        if self.superseded:
            lines.append(f"  No longer the focus: {', '.join(self.superseded)}")
        return "\n".join(lines)


def start(subject: str = "", *, now: float | None = None,
          ttl: int = DEFAULT_TTL_SECONDS) -> Focus:
    now = now if now is not None else time.monotonic()
    return Focus(subject=_clean(subject), expires_at=now + ttl)


def update(
    focus: Focus,
    text: str,
    *,
    subject: str = "",
    now: float | None = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> Focus:
    """Fold this turn in, with a correction outranking everything.

    ``subject`` is what the routing layers made of the turn. It is used
    when the turn genuinely introduces something, and ignored when the turn
    is a correction or a bare pointer -- in the first case because the
    person just said something more authoritative, and in the second
    because there is nothing new in it to read.
    """
    now = now if now is not None else time.monotonic()
    background = dict(focus.background)
    background.update(read_background(text))

    corrected = read_correction(text)
    explicit_anchor = read_education_anchor(text)
    if explicit_anchor and not corrected:
        return replace(
            focus,
            subject=explicit_anchor,
            background=background,
            corrected_to="",
            expires_at=now + ttl,
        )

    # A relational request names a new task while pointing back to the
    # current entity. Preserve that entity as the task's anchor before the
    # generic subject ("rent near my school") replaces it.
    if (
        _RELATIONAL_REFERENCE.search(str(text or ""))
        and focus.subject
        and "about" not in background
    ):
        background["about"] = focus.subject

    if corrected:
        superseded = focus.superseded
        if focus.subject and focus.subject.casefold() != corrected.casefold():
            superseded = superseded + (focus.subject,)
        # A correction is the most explicit thing a person says about what
        # they meant, so it outlives the turn that made it: "rent near my
        # school" three turns later still means near UW.
        background["about"] = corrected
        return replace(
            focus,
            subject=corrected,
            background=background,
            corrected_to=corrected,
            superseded=superseded[-3:],
            expires_at=now + ttl,
        )

    if points_at_something_known(text):
        # "Yep, I'm going there." names nothing; the focus stands.
        return replace(
            focus, background=background, corrected_to="",
            expires_at=now + ttl,
        )

    offered = _clean(subject)
    return replace(
        focus,
        subject=offered or focus.subject,
        background=background,
        corrected_to="",
        expires_at=now + ttl,
    )
