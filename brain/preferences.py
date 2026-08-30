"""What this person usually wants used, and what they usually want.

``brain/deliberation/profile.py`` already knows how to hold a preference
honestly -- what was said outright versus what was merely noticed, how much
standing a thing needs before it may be acted on unasked, and how a
correction outweighs a habit instead of being averaged into it. What it has
never had is a way in from ordinary speech, or a way out into the choices
that are actually made.

This is those two ends.

The reading half is deliberately timid. Two sentences differ by four words:

    "Use Naver Maps for this search."              -> this turn only
    "Use Naver Maps whenever I ask for restaurants." -> from now on

Getting the first one wrong costs the person one repeated instruction.
Getting the second one wrong changes what she does permanently, invisibly,
and in a way they never asked for. So anything that is not clearly durable
is treated as a one-off, and nothing is written from a bare choice at all
-- a single use is not a preference, which is a rule the profile already
enforces on its own by refusing to act on one observation.

The resolving half never decides anything by itself. It answers "what does
this person usually want here", and hands that to the layer whose job the
decision actually is -- which may have a reason to go elsewhere, and says
so when it does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from brain.deliberation.profile import (
    FAVOURITE_FOR,
    SOURCE_FOR,
    STATED,
    SUGGESTED,
    TOOL_FOR,
    context_key,
)

# "From now on" and "whenever" are promises about the future. Anything less
# is about this turn.
_DURABLE = re.compile(
    r"\b(?:from now on|going forward|in future|in the future|"
    r"whenever|every time|each time|always|by default|as a rule)\b"
    r"|앞으로|항상|늘",
    re.IGNORECASE,
)

# Said with less force. Remembered, but not acted on until it is said or
# seen again -- which is what the profile's standing threshold already does
# for anything below a flat statement.
_SOFT = re.compile(
    r"\b(?:usually|normally|generally|tend to|i prefer|i'd prefer|"
    r"most of the time|i like to)\b"
    r"|보통|주로",
    re.IGNORECASE,
)

# Explicitly about this turn and no other.
_ONE_OFF = re.compile(
    r"\b(?:for this one|this time|just this once|for now|right now|"
    r"for this search|for this|just now|on this occasion)\b"
    r"|이번(?:엔|만|에는)",
    re.IGNORECASE,
)

_STOP = re.compile(
    r"\b(?:stop|don't|do not|no longer|quit)\s+(?:using|use)\s+(.+?)"
    r"(?:\s+(?:by default|any ?more|for .+))?$"
    r"|\bforget\s+(?:my\s+)?(.+)$",
    re.IGNORECASE,
)

# The thing to use. The verb is matched case-insensitively and the name is
# not: capitalisation is most of what marks a product name, and a blanket
# IGNORECASE threw that away -- every sentence opening with "Use ..." then
# failed to match at all.
_USE = re.compile(
    r"(?i:\b(?:use|using|switch\s+to|go\s+with|prefer|prefers)\s+)"
    r"(my\s+[\w'-]+(?:\s+[\w'-]+){0,2}"
    r"|[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*"
    r"|[a-z][\w.'-]*)"
)

# Media carries its own obvious domain: a playlist is music whether or not
# the sentence says the word.
_MEDIA = re.compile(
    r"\b(?:music|playlist|playlists|song|songs|track|tracks|album|albums|"
    r"podcast|podcasts|radio|station)\b"
    r"|음악|플레이리스트",
    re.IGNORECASE,
)

# "instead of X" -- the profile decays the loser on its own once the winner
# is recorded as stated, so only the winner needs reading.
_INSTEAD = re.compile(r"\binstead of\s+([\w.' -]{2,40})", re.IGNORECASE)

# What the preference is about. "whenever I ask for restaurants",
# "for restaurants", "when I want music".
_DOMAIN = re.compile(
    r"\b(?:when(?:ever)?\s+(?:i|you)\s+(?:ask(?:\s+(?:me|you))?\s+)?"
    r"(?:for|to|about)?\s*|for\s+(?:my\s+)?)"
    r"([a-z][\w' -]{2,40}?)"
    r"(?=[,.;!?]|$|\s+(?:and|or|but|instead|from now|by default))",
    re.IGNORECASE,
)

# A situation rather than a request: "when I work out", "when my throat
# hurts". Told apart from a domain trigger by the absence of an asking verb.
_CONTEXT = re.compile(
    r"\bwhen(?:ever)?\s+((?:i|my)\b[^,.;!?]{2,40}?)"
    r"(?=[,.;!?]|$|\s+(?:i|please|use|play|get|order))",
    re.IGNORECASE,
)
_ASKING = re.compile(
    r"\b(?:ask|asks|asking|want|wants|need|needs|say|says|"
    r"look|looking|search|searches|request)\b",
    re.IGNORECASE,
)

# A concrete thing they go back to, rather than a way of looking for one.
_FAVOURITE = re.compile(
    r"\b(?:i\s+(?:usually\s+|normally\s+|always\s+)?"
    r"(?:get|order|go to|buy|grab))\s+"
    r"(?:(?:some|a|an|the)\s+)?"
    r"([\w' -]{2,40}?)"
    r"(?:\s+from\s+([\w' -]{2,40}?))?"
    r"(?=[,.;!?]|$)",
    re.IGNORECASE,
)

# Doing something, as opposed to finding something.
_ACTION_DOMAIN = re.compile(
    r"\b(?:play|listen|watch|open|launch|call|message|navigate|drive)\b",
    re.IGNORECASE,
)

_TRAILING = re.compile(
    r"\s*\b(?:by default|from now on|please|always|whenever|going forward)\b"
    r"\s*$",
    re.IGNORECASE,
)


_POSSESSIVE = re.compile(r"^(?:my|our|the)\s+", re.IGNORECASE)
_SELF = re.compile(r"^(?:i|my)\s+", re.IGNORECASE)


def _tidy(value: str) -> str:
    value = " ".join(str(value or "").split()).strip(" ,.;:!?-")
    while True:
        trimmed = _TRAILING.sub("", value).strip()
        if trimmed == value:
            return value
        value = trimmed


def _domain_of(text: str) -> str:
    """The area a preference is about, in one normalised word or phrase."""
    text = _tidy(text)
    if not text:
        return ""
    if _MEDIA.search(text):
        # "use my workout playlist" and "instead of YouTube Music" are both
        # about music, and neither names music as its subject.
        return "music"
    try:
        from brain.task_discovery_policy import TaskDiscoveryPolicy

        # The category vocabulary already exists and already knows that
        # "restaurants", "맛집" and "places to eat" are one thing.
        category = TaskDiscoveryPolicy.category_for(text)
        if category:
            return category[0]
    except Exception:
        pass
    words = [
        word for word in re.findall(r"[a-z0-9가-힣]+", text.casefold())
        if word not in {
            "a", "an", "the", "some", "any", "me", "my", "for", "to",
            "ask", "asks", "asking", "want", "wants", "you", "i", "it",
        }
    ]
    if not words:
        return ""
    last = words[-1]
    return last[:-1] if len(last) > 3 and last.endswith("s") else last


# Words that follow "use" constantly and name nothing. Measured live:
# "which app does people use to find rents" was read as an instruction to
# use a tool called "to". A source override has to name a source.
_NOT_A_NAME = frozenset({
    "to", "for", "on", "in", "at", "of", "with", "by", "from", "into",
    "it", "this", "that", "those", "these", "them", "us", "me", "you",
    "a", "an", "the", "my", "your", "our", "some", "any", "one", "ones",
    "when", "what", "which", "how", "who", "why", "there", "here",
    "and", "or", "but", "if", "as", "so", "up", "out", "over",
    "something", "anything", "everything", "nothing",
})


def _names_a_source(value: str) -> bool:
    """Whether this is plausibly the name of a tool, site or provider.

    A capitalised word is a name. A lower-case one has to at least be a
    word somebody could mean -- "spotify" counts, "to" does not, and when
    it is not clear the answer is no, because a wrong override silently
    sends the turn somewhere nobody asked for.
    """
    value = " ".join(str(value or "").split()).strip(" .,;:!?")
    if not value:
        return False
    words = value.split()
    if any(word.casefold() in _NOT_A_NAME for word in words):
        return False
    if value[:1].isupper():
        return True
    return len(words) == 1 and len(value) >= 4 and value.isalnum()


@dataclass(frozen=True)
class Statement:
    """A preference the person just expressed, and how firmly."""

    action: str            # "remember" | "forget" | "override"
    kind: str = SOURCE_FOR
    domain: str = ""
    context: str = ""
    value: str = ""
    source: str = STATED   # STATED for "always", SUGGESTED for "usually"

    @property
    def durable(self) -> bool:
        return self.action == "remember"

    @property
    def key(self) -> str:
        return context_key(self.domain, self.context)


def read(text: str) -> Statement | None:
    """What this utterance says about what to use, if anything.

    Returns ``None`` for the overwhelming majority of sentences, which say
    nothing about preferences at all.
    """
    text = " ".join(str(text or "").split())
    if not text:
        return None

    stop = _STOP.search(text)
    if stop:
        target = _tidy(stop.group(1) or stop.group(2) or "")
        if target:
            return Statement(action="forget", value=target)

    used = _USE.search(text)
    favourite = _FAVOURITE.search(text)
    if not used and not favourite:
        return None

    # Situation and subject are both introduced by "when", and are told
    # apart by whether the clause is about asking for something.
    context = ""
    for match in _CONTEXT.finditer(text):
        clause = _tidy(match.group(1))
        if clause and not _ASKING.search(clause):
            context = _tidy(_SELF.sub("", clause))
            break

    domain = ""
    for match in _DOMAIN.finditer(text):
        clause = _tidy(match.group(1))
        if _ONE_OFF.search(f"for {clause}") or clause.casefold().startswith(
            ("this ", "that ", "these ", "those "),
        ):
            # "for this one" and "for this search" say when, not what.
            continue
        candidate = _domain_of(clause)
        if candidate and candidate not in {"one", "this", "search", "thing"}:
            domain = candidate
            break

    if favourite and not used:
        thing = _tidy(favourite.group(1))
        where = _tidy(favourite.group(2) or "")
        value = where or thing
        # A favourite needs either a habit word or a named place it comes
        # from. Without one of those this is just a sentence about today.
        if not value or not (
            where or _SOFT.search(text) or _DURABLE.search(text)
        ):
            return None
        return Statement(
            action="remember" if _DURABLE.search(text) or _SOFT.search(text)
            else "override",
            kind=FAVOURITE_FOR,
            domain=domain or _domain_of(thing),
            context=context,
            value=value,
            source=STATED if _DURABLE.search(text) else SUGGESTED,
        )

    value = _tidy(used.group(1))
    # "my workout playlist" is a thing they named as theirs, which is its
    # own evidence that it is a name; anything else has to look like one.
    possessive = bool(_POSSESSIVE.match(value))
    value = _POSSESSIVE.sub("", value).strip() or value
    if not possessive and not _names_a_source(value):
        return None
    kind = (
        TOOL_FOR if (_ACTION_DOMAIN.search(text) or _MEDIA.search(text))
        else SOURCE_FOR
    )
    if not domain or _MEDIA.search(text):
        domain = _domain_of(text) or domain
    if not domain:
        return None

    # Order matters. "Use X for this one" is about this turn even though it
    # is phrased as an instruction, and "from now on" outranks nothing else
    # in the sentence.
    if _ONE_OFF.search(text):
        return Statement(
            action="override", kind=kind, domain=domain,
            context=context, value=value,
        )
    if _DURABLE.search(text) or _INSTEAD.search(text):
        return Statement(
            action="remember", kind=kind, domain=domain,
            context=context, value=value, source=STATED,
        )
    if context:
        # "When I work out, use my workout playlist" describes a situation
        # that recurs, which is what makes it worth keeping -- but it was
        # said once and softly, so it is kept below the acting threshold
        # until it is said or seen again.
        return Statement(
            action="remember", kind=kind, domain=domain,
            context=context, value=value, source=SUGGESTED,
        )
    if _SOFT.search(text):
        return Statement(
            action="remember", kind=kind, domain=domain,
            context=context, value=value, source=SUGGESTED,
        )
    # A bare "use X" is an instruction for now, not a standing order.
    return Statement(
        action="override", kind=kind, domain=domain,
        context=context, value=value,
    )


@dataclass(frozen=True)
class Resolution:
    """What she settled on, and on what grounds."""

    domain: str = ""
    context: str = ""
    choice: str = ""
    source: str = ""
    confidence: str = ""
    applied: bool = False
    why: str = ""

    def log_block(self) -> str:
        """Console only -- a decision summary, never the reasoning."""
        lines = ["[Preference Resolution]", f"  Domain: {self.domain}"]
        if self.context:
            lines.append(f"  Context: {self.context}")
        lines.append(f"  Choice: {self.choice or '(none)'}")
        if self.source:
            lines.append(f"  Source: {self.source}")
        if self.confidence:
            lines.append(f"  Confidence: {self.confidence}")
        lines.append(f"  Applied: {'yes' if self.applied else 'no'}")
        if self.why:
            lines.append(f"  Why: {self.why}")
        return "\n".join(lines)


def _confidence(preference) -> str:
    if preference.source == STATED:
        return "high"
    return "medium" if preference.standing >= 3.0 else "low"


def resolve(
    profile,
    kind: str,
    domain: str,
    *,
    context: str = "",
    override: str = "",
    default: str = "",
) -> Resolution:
    """What this person usually wants here, by the agreed precedence.

    An override is this turn's instruction and outranks everything without
    touching what is saved -- "use Google Maps for this one" must not erase
    a standing preference for Naver Maps.
    """
    domain = " ".join(str(domain or "").split()).strip().casefold()
    if not domain:
        return Resolution(applied=False, why="nothing to resolve")
    if override:
        return Resolution(
            domain=domain, context=context, choice=override,
            source="current_turn_override", confidence="high", applied=True,
            why="asked for by name this turn; the saved default is untouched",
        )
    preference = None
    if profile is not None:
        try:
            preference = profile.preferred_in(kind, domain, context)
        except Exception:
            preference = None
    if preference is not None:
        return Resolution(
            domain=domain, context=context, choice=preference.value,
            source=(
                "explicit_user_default" if preference.source == STATED
                else "repeated_behaviour"
            ),
            confidence=_confidence(preference),
            applied=True,
            why=preference.because(),
        )
    if default:
        return Resolution(
            domain=domain, context=context, choice=default,
            source="locale_default", confidence="low", applied=True,
            why="no saved preference; this market's usual source",
        )
    return Resolution(
        domain=domain, context=context, applied=False,
        why="nothing saved and no default for this market",
    )


def apply(profile, statement: Statement) -> str:
    """Carry out what the person just said about their preferences.

    Returns a short line to say back, or empty when nothing was durable --
    a one-turn override changes this turn and is never announced as though
    it had changed anything permanent.
    """
    if profile is None or statement is None:
        return ""
    if statement.action == "forget":
        dropped = profile.forget_value(statement.value)
        print("[Preference Resolution]")
        print(f"  Domain: {', '.join(dropped) or '(nothing saved)'}")
        print(f"  Choice: {statement.value}")
        print("  Applied: forgotten" if dropped else "  Applied: no")
        if not dropped:
            return f"I wasn't using {statement.value} by default anyway."
        return f"Alright -- I'll stop defaulting to {statement.value}."
    if statement.action != "remember" or not statement.value:
        return ""
    preference = profile.observe(
        statement.kind,
        statement.value,
        key=statement.key,
        source=statement.source,
    )
    if preference is None:
        return ""
    print("[Preference Resolution]")
    print(f"  Domain: {statement.domain}")
    if statement.context:
        print(f"  Context: {statement.context}")
    print(f"  Choice: {statement.value}")
    print(f"  Source: {statement.source}")
    print(
        "  Applied: saved"
        + ("" if preference.is_actionable else " (not yet acted on unasked)")
    )
    where = f" when {statement.context}" if statement.context else ""
    if preference.is_actionable:
        return f"Got it -- {statement.value} for {statement.domain}{where}."
    return (
        f"Noted -- you usually go with {statement.value} for "
        f"{statement.domain}{where}."
    )
