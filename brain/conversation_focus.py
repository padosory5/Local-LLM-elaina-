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

# A reference to the entity under discussion made by its role rather than
# its name -- "near my school", "close to the office". It has to *not name
# the thing*, or it is not a reference at all.
#
# Measured live: "a studio near the University of Washington" matched on
# "near the university", so the previous subject was preserved as the
# task's anchor -- and the previous subject was ``time``, left over from
# asking what time it was in Seattle. The query went out as
#
#     accommodation University of Washington time Seattle
#
# The lookahead is the whole fix: a role followed by a name is the name,
# and a turn that says which university is not asking anything to
# remember which one.
_NAMES_ITS_OWN_REFERENT = r"(?!\s+(?:of\s+)?[A-Z])"

_RELATIONAL_REFERENCE = re.compile(
    r"\b(?:near|close\s+to|around)\s+(?:my|the)\s+"
    r"(?:school|university|campus|work|office|home)\b"
    + _NAMES_ITS_OWN_REFERENT,
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


# An institution says what kind of thing it is inside its own name, which
# is the one signal available without a model call or a gazetteer.
_INSTITUTION = re.compile(
    r"\b([A-Z][\w.'-]*(?:\s+(?:of|the|and|de|del)\s+[A-Z][\w.'-]*"
    r"|\s+[A-Z][\w.'-]*){0,4})\b"
)
_KIND_WORDS = {
    "university": "school",
    "college": "school",
    "school": "school",
    "institute": "school",
    "academy": "school",
    "polytechnic": "school",
}


def read_named_roles(text: str) -> dict[str, str]:
    """Which role each institution the turn names could later be called by.

    "Near my school" has to reach the school. Measured live, it did not:
    the relational reader preserved whatever the previous subject had
    been, and after two turns of small talk that was the literal word
    "Conversation" -- so a rental search ran on "studio apartments near
    Conversation $1,500". The user had named the University of Washington
    four times in the same conversation, and one turn later she could
    still answer "where's my university again?" correctly.

    The name is the evidence. A thing called a university is what "my
    university" means, and nothing else in the turn has to be understood
    for that to hold.
    """
    found: dict[str, str] = {}
    for match in _INSTITUTION.finditer(str(text or "")):
        name = _clean(match.group(1))
        words = [word.casefold().strip(".,") for word in name.split()]
        if len(words) < 2:
            # A bare "University" names no particular one.
            continue
        for word in words:
            role = _KIND_WORDS.get(word)
            if role and role not in found:
                found[role] = name
    return found


def location_is_about_the_user(text: str) -> bool:
    """Whether the place was said about the person, or just mentioned.

    "I'm moving to Seattle" is a fact about them and outlives whatever
    they were discussing when they said it. "What time is it in Seattle?"
    is a fact about that question and nothing else -- the first two
    branches of ``_LOCATION`` are the first kind, the loose bare-preposition
    branch is the second.

    Measured live, this cost an entire session. "Now in Seattle" -- three
    words inside a question about a clock -- became background, and twenty
    turns later it was still there: a shopping search read "packing
    peanuts Seattle" instead of the user's own market, and a search for
    casinos on an island came back about casinos in Seattle.
    """
    place = _LOCATION.search(str(text or ""))
    return bool(place and (place.group(1) or place.group(2)))


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
    # Whether the held location was mentioned in passing rather than
    # stated about the person. A topical place is context for its own
    # topic and retires with it; a place they said they live in does not.
    topical_location: bool = False
    # Role -> the institution the conversation has named for it, so "my
    # school" resolves to the school rather than to whatever the previous
    # subject happened to be.
    named_roles: dict[str, str] = field(default_factory=dict)

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


# The existential "there" is not a pointer at anything. "Are there
# casinos in Seattle?" says nothing about the previous topic; it is the
# grammar English uses to ask whether something exists.
#
# Measured live: it read as a pointer, so the anchor held -- and the
# anchor was "no, open Zillow.com", left over from a correction three
# turns earlier. The search went out as
#
#     Are there Cousinos in Seattle? no, open Zillow.com
#
# The same is true of "here's" and "there's" introducing a clause.
_EXISTENTIAL = re.compile(
    r"\b(?:is|are|was|were|isn'?t|aren'?t|wasn'?t|weren'?t)\s+(?:there|here)\b"
    r"|\b(?:there|here)(?:'s|'re)\b"
    r"|\b(?:there|here)\s+(?:is|are|was|were|isn'?t|aren'?t|wasn'?t|"
    r"weren'?t|will\s+be|used\s+to\s+be)\b",
    re.IGNORECASE,
)

# A turn that points back at what is already held: a relational reference
# ("near my school"), a deictic ("there", "it"), or a correction.
_POINTS_BACK = re.compile(
    r"\b(?:there|here|that|this|those|these|them|it|its|the\s+same)\b"
    r"|\b(?:my|the)\s+(?:school|university|campus|work|office|home|place)\b"
    + _NAMES_ITS_OWN_REFERENT,
    re.IGNORECASE,
)

# An anchor is what the conversation is about, not the request that
# mentioned it. Measured live, a correction taken whole made the anchor
# "look at Zillow for rental options near University of Washington" --
# thirteen words of task description, appended to every later query.
_ANCHOR_WORDS = 8

# An anchor is a thing, and these are not things.
#
# A placeholder is what the routing layer says when it has nothing to
# say. Measured live: two small-talk turns set the subject to the literal
# word "Conversation", and the next request -- "find me a rent near my
# school" -- preserved it as the anchor, so the search ran on
#
#     studio apartments near Conversation $1,500
#
# An instruction is the other kind of non-thing. "No, no, open Zillow.com"
# is a redirected errand, and holding it as what the conversation is about
# is how it ended up inside a search for casinos two turns later.
_PLACEHOLDER_SUBJECTS = frozenset({
    "conversation", "chat", "chatting", "general", "general conversation",
    "small talk", "smalltalk", "greeting", "unknown", "none", "other",
    "personal preference", "preferences", "casual conversation",
    "statement", "remark", "question", "topic", "discussion",
})

_AN_INSTRUCTION = re.compile(
    r"^(?:(?:no|nope|nah|yeah|yes|ok(?:ay)?|actually|so|well)[,.!]?\s+)*"
    r"(?:please\s+|just\s+|now\s+|then\s+|can\s+you\s+|could\s+you\s+)*"
    r"(?:open|close|quit|launch|start|stop|play|pause|show|find|search|"
    r"look|go|visit|click|type|scroll|read|check|make|create|delete|"
    r"move|send|call|put|set|turn|use|try)\b",
    re.IGNORECASE,
)


def _can_be_an_anchor(value: str) -> bool:
    """Whether this is a thing the conversation can point back at."""
    cleaned = _clean(value)
    if not cleaned:
        return False
    if cleaned.casefold() in _PLACEHOLDER_SUBJECTS:
        return False
    return not _AN_INSTRUCTION.match(cleaned)


def _points_back(text: str) -> bool:
    said = str(text or "")
    # The existential reading is taken out first, so what is left is only
    # the deictic one. "Is there a cheaper one there?" still points back;
    # "Are there casinos in Seattle?" no longer does.
    return bool(_POINTS_BACK.search(_EXISTENTIAL.sub(" ", said)))


def _introduces_a_new_subject(
    text: str, subject: str, anchor: str,
) -> bool:
    """Whether this turn is about something the anchor has no part in."""
    said = f"{text} {subject}".casefold()
    words = set(re.findall(r"[a-z0-9가-힣]{3,}", said))
    held = set(re.findall(r"[a-z0-9가-힣]{3,}", str(anchor or "").casefold()))
    if not words or not held:
        return False
    return not (words & held)


def _shortened(anchor: str) -> str:
    """The anchor as a phrase, cut at the point it stops being one."""
    words = " ".join(str(anchor or "").split()).split()
    return " ".join(words[:_ANCHOR_WORDS])


def update(
    focus: Focus,
    text: str,
    *,
    subject: str = "",
    now: float | None = None,
    ttl: int = DEFAULT_TTL_SECONDS,
    pointer: bool = False,
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
    topical_location = focus.topical_location
    fresh = read_background(text)

    # The same rule the anchor got, applied to the other background fact
    # that steers every query silently. A place mentioned in passing is
    # context for the topic that mentioned it, so when the topic moves to
    # something the place has no part in, the place goes with it. A place
    # stated about the person -- "I'm moving to Seattle" -- is a fact
    # about them and stays.
    if (
        "location" not in fresh
        and topical_location
        and background.get("location")
        and not _points_back(text)
        and _introduces_a_new_subject(text, subject, background["location"])
    ):
        background.pop("location", None)
        topical_location = False

    background.update(fresh)
    if "location" in fresh:
        topical_location = not location_is_about_the_user(text)

    # Named institutions accumulate. The most recent one wins its role,
    # and a role once filled is never emptied by a turn that names none --
    # "my school" a week later still means the school.
    named_roles = dict(focus.named_roles)
    named_roles.update(read_named_roles(text))
    if subject:
        named_roles.update(read_named_roles(subject))

    # The anchor is context for the subject it was set with, and nothing
    # retired it. Measured live: a correction set
    #
    #   about: look at Zillow for rental options near University of
    #          Washington
    #
    # and it was still riding into every query an hour later -- an
    # international driving permit search, an AI internship search, a
    # secondhand-selling search. It deliberately outlives the turn that set
    # it, because "rent near my school" three turns later still means near
    # UW. What it had no way to do was stop.
    #
    # It stops here: a turn that establishes a new subject while pointing
    # at nothing has moved on, and the anchor moves with it.
    if (
        background.get("about")
        and not _points_back(text)
        and _introduces_a_new_subject(text, subject, background["about"])
    ):
        background.pop("about", None)

    if pointer:
        # The layer above has already resolved what this turn is about, so
        # nothing here may read a second answer out of the words. Measured
        # live: "I meant only one S" is a correction by every test in this
        # module, and reading it as one made the subject "only one S" and
        # retired the browser action it was correcting.
        return replace(
            focus, background=background, corrected_to="",
            expires_at=now + ttl, topical_location=topical_location,
            named_roles=named_roles,
        )

    corrected = read_correction(text)
    explicit_anchor = read_education_anchor(text)
    if explicit_anchor and not corrected:
        return replace(
            focus,
            subject=explicit_anchor,
            background=background,
            corrected_to="",
            expires_at=now + ttl,
            topical_location=topical_location,
            named_roles=named_roles,
        )

    # A relational request names a new task while pointing back to a known
    # entity. Resolve which one before the generic subject ("rent near my
    # school") replaces it.
    #
    # The role comes first and the previous subject is only the fallback.
    # It used to be the only source, which meant the anchor was whatever
    # had been talked about last -- and after two turns of small talk that
    # was the word "Conversation".
    relational = _RELATIONAL_REFERENCE.search(str(text or ""))
    if relational and "about" not in background:
        role = _KIND_WORDS.get(relational.group(0).split()[-1].casefold(), "")
        by_name = named_roles.get(role, "") if role else ""
        candidate = by_name or focus.subject
        if _can_be_an_anchor(candidate):
            background["about"] = _shortened(candidate)

    if corrected:
        superseded = focus.superseded
        if focus.subject and focus.subject.casefold() != corrected.casefold():
            superseded = superseded + (focus.subject,)
        # A correction is the most explicit thing a person says about what
        # they meant, so it outlives the turn that made it: "rent near my
        # school" three turns later still means near UW.
        #
        # Unless it is an errand. "No, no, open Zillow.com" corrects what
        # to do, not what this is about, and holding it as the anchor put
        # it inside a search for casinos two turns later.
        if _can_be_an_anchor(corrected):
            background["about"] = _shortened(corrected)
        return replace(
            focus,
            subject=corrected,
            background=background,
            corrected_to=corrected,
            superseded=superseded[-3:],
            expires_at=now + ttl,
            topical_location=topical_location,
            named_roles=named_roles,
        )

    if points_at_something_known(text):
        # "Yep, I'm going there." names nothing; the focus stands.
        return replace(
            focus, background=background, corrected_to="",
            expires_at=now + ttl, topical_location=topical_location,
            named_roles=named_roles,
        )

    offered = _clean(subject)
    return replace(
        focus,
        subject=offered or focus.subject,
        background=background,
        corrected_to="",
        expires_at=now + ttl,
        topical_location=topical_location,
        named_roles=named_roles,
    )
