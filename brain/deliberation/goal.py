"""A typed request: what was asked for, and where each part came from.

The point of the type is the boundary it creates. Today a planner receives
a sentence and hands pieces of it to a driver; there is no moment at which
anything decides *which* piece was the value. A Goal makes that moment
explicit -- a slot is a value someone deliberately extracted, with a record
of where it came from -- and everything downstream may only use slots.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

# Where a slot's value came from. Provenance is not decoration: Phase 3's
# gate treats a value taken from the person's habits differently from one
# they actually said, and says so out loud when it acts on the former.
SOURCE_UTTERANCE = "utterance"
SOURCE_WORLD = "world"
SOURCE_PROFILE = "profile"
SOURCE_ASKED = "asked"
# A value that came back from a real search or page read, rather than
# from the person or from an assumption. Recommendation constraints
# need this fourth origin: "the listing said 500,000 won" and "you said
# 500,000 won" carry different authority when they disagree.
SOURCE_RESEARCH = "research"

# Slots whose value is text the person asked to be entered somewhere. Only
# these may be typed; a window name or an app name never is.
_TYPEABLE_SLOTS = frozenset({"text", "query", "title", "artist"})

# The verbs that make a sentence an instruction rather than a value. Typed
# text beginning with one of these is the request itself, not the thing the
# request asked for.
_COMMAND_VERBS = frozenset({
    "play", "pause", "resume", "stop", "skip", "open", "launch", "start",
    "close", "search", "find", "lookup", "look", "type", "write", "enter",
    "click", "press", "tap", "select", "choose", "pick", "scroll", "focus",
    "switch", "put", "listen", "show", "give", "tell", "get", "make",
    # Browser-side instructions. Deliberately excludes noun-shaped words
    # like "send" or "go": the slot check runs first, so a real value such
    # as "book prices" still passes on a goal that named it.
    "book", "reserve", "navigate", "browse", "download", "compare",
    "summarize", "summarise", "translate", "schedule",
    "재생", "실행", "열어", "검색", "찾아", "입력", "눌러", "선택",
})


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.split()).strip()


def _tokens(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", _normalized(value), flags=re.UNICODE)


@dataclass(frozen=True)
class Slot:
    """One value the request named, and how it came to be known."""

    name: str
    value: str
    source: str = SOURCE_UTTERANCE
    confidence: float = 1.0

    @property
    def is_assumed(self) -> bool:
        """True when nobody said this -- it was inferred or looked up."""
        return self.source in {SOURCE_WORLD, SOURCE_PROFILE}


@dataclass(frozen=True)
class Goal:
    """What a request asked for, in a shape a driver can be checked against."""

    kind: str = "generic"
    utterance: str = ""
    slots: Mapping[str, Slot] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", MappingProxyType(dict(self.slots)))

    def value(self, name: str, default: str = "") -> str:
        slot = self.slots.get(name)
        return slot.value if slot is not None else default

    def has(self, name: str) -> bool:
        return bool(self.value(name).strip())

    @property
    def assumptions(self) -> tuple[Slot, ...]:
        """Slots nobody actually said, which the gate must own up to."""
        return tuple(slot for slot in self.slots.values() if slot.is_assumed)

    def typeable_values(self) -> tuple[str, ...]:
        """Every value this request could legitimately have typed."""
        return tuple(
            slot.value
            for name, slot in self.slots.items()
            if name in _TYPEABLE_SLOTS and slot.value.strip()
        )

    def permits_typing(self, text: str) -> bool:
        """Whether this text is something the request actually named.

        A slot value passes outright. Anything else passes only if it is
        not the instruction wearing a disguise -- text that opens with a
        command verb is the request being retyped, which is precisely how
        "Play any songs from my liked list" ended up in a search box.
        """
        candidate = _normalized(text)
        if not candidate:
            return False
        for value in self.typeable_values():
            known = _normalized(value)
            # The value itself, or a part of it, is what was asked for.
            # Deliberately not the other direction: text that *contains* a
            # slot value is usually the value with the request wrapped back
            # around it -- "search for cheap flights to Guam".
            if known and (candidate == known or candidate in known):
                return True
        return not self.reads_as_instruction(text)

    def reads_as_instruction(self, text: str) -> bool:
        """True when this text is the request restated, not a value."""
        tokens = _tokens(text)
        if not tokens:
            return False
        if tokens[0] in _COMMAND_VERBS:
            return True
        spoken = set(_tokens(self.utterance))
        if not spoken or len(tokens) < 4:
            return False
        # A long phrase that is mostly the request's own words, and carries
        # one of its verbs, is the request -- however it was reordered.
        shared = sum(1 for token in tokens if token in spoken) / len(tokens)
        return shared >= 0.8 and any(
            token in _COMMAND_VERBS for token in tokens
        )

    def refusal_hint(self) -> str:
        """What to type instead, said in terms of this request."""
        values = self.typeable_values()
        if values:
            named = ", ".join(repr(value) for value in values)
            return f"This request named {named}; type only that."
        return (
            "This request did not name any text to enter. Ask which value "
            "is wanted rather than typing the request itself."
        )
