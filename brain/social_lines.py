"""Greetings and other purely social lines, chosen locally and varied.

A greeting carries no information. That is exactly why it was hard-coded:
routing "hi" to the general model dragged the capability inventory, the
user's home market and a service pitch into a turn that wanted none of
them, so the turn was locked to a fixed sentence instead. The lock fixed
the pitch and created a worse problem -- every greeting for the whole life
of the session was the same seven words:

    "hi"           -> "Hey, how are you doing?"
    "hello"        -> "Hey, how are you doing?"
    "good morning" -> "Hey, how are you doing?"

Nobody with a friend experiences that. The lock was right about the model
and wrong about the sentence: what a greeting needs is no model call *and*
no fixed answer, which is what this module is.

Where the boundary sits
-----------------------

:mod:`brain.brief_response` owns lines that report an **outcome** and name a
**subject**; they are validated against the real status, because claiming
success for a failure is a correctness bug. :mod:`brain.action_status` owns
the contentless lines that cover **work in progress**. This module owns the
contentless lines that are purely **social** -- they answer a greeting and
nothing else. Nothing here can misreport anything, because nothing here
reports anything.

Like ``action_status``, this picks from hand-written banks with no model
call: the sentence answering "hey" must not cost a round-trip, and a
greeting is the one turn where latency is most obvious.
"""

from __future__ import annotations

import datetime
import random
import re
from collections import deque

DEFAULT_LANGUAGE = "en"

# Which bank the hour falls in. Deliberately coarse -- the point is that she
# does not say "good morning" at midnight, not that she tracks dusk.
_PARTS = (
    ("morning", range(5, 12)),
    ("afternoon", range(12, 17)),
    ("evening", range(17, 22)),
)

# A greeting that names its own time of day outranks the clock: answering
# "good morning" with "still up?" because the machine clock disagrees is
# worse than being an hour off.
_NAMED_PART = re.compile(
    r"\b(?:good\s+)?(morning|afternoon|evening|night)\b", re.IGNORECASE,
)

# Not every line is a question. "How are you doing?" every single time is
# repetitive twice over -- the same words, and the same demand that the
# other person carry the turn. A friend sometimes just says hello.
_BANKS: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "neutral": (
            "Hey! What's up?",
            "Hey you. How's it going?",
            "Hi! How's your day been?",
            "Hey there. What's on your mind?",
            "Hey! Good to hear from you.",
            "Hi. What are you up to?",
            "Hey, how are you doing?",
            "Hey! How's everything?",
            "Hi there. Good to see you.",
        ),
        "morning": (
            "Morning! How'd you sleep?",
            "Good morning. What's first today?",
            "Morning! How's the day looking?",
            "Hey, good morning. Up early?",
            "Morning. Ready for today?",
        ),
        "afternoon": (
            "Hey! How's the day going?",
            "Afternoon. How's it treating you?",
            "Hi! Getting much done today?",
            "Hey there. How's the afternoon?",
        ),
        "evening": (
            "Evening! How was your day?",
            "Hey, good evening. Long day?",
            "Hi! How'd today go?",
            "Evening. Done for the day?",
        ),
        "night": (
            "Hey, still up?",
            "Evening. Winding down?",
            "Hi! Late one tonight?",
            "Hey there. Burning the midnight oil?",
        ),
    },
    "ko": {
        "neutral": (
            "안녕! 잘 지냈어?",
            "안녕, 무슨 일이야?",
            "어서 와. 요즘 어때?",
            "안녕! 오늘 어땠어?",
            "반가워. 뭐 하고 있었어?",
        ),
        "morning": (
            "좋은 아침! 잘 잤어?",
            "아침이네. 오늘 뭐부터 할까?",
            "좋은 아침. 일찍 일어났네?",
        ),
        "afternoon": (
            "안녕! 오늘 하루 어때?",
            "점심은 먹었어?",
            "안녕. 오후는 좀 어때?",
        ),
        "evening": (
            "좋은 저녁! 오늘 어땠어?",
            "저녁이네. 하루 어땠어?",
            "안녕. 오늘 고생했지?",
        ),
        "night": (
            "아직 안 잤네?",
            "늦었네. 이제 좀 쉬어야지?",
            "안녕. 밤늦게까지 뭐 해?",
        ),
    },
}


class SocialLineSelector:
    """Answer a greeting without a model call and without a fixed sentence.

    One per ChatEngine, like :class:`~brain.action_status.ActionStatusSelector`
    and for the same reason: repetition is only visible *across* turns, so the
    memory of what was just said has to outlive the turn that said it.
    """

    RECENT_LINES = 6
    RECENT_OPENINGS = 3

    def __init__(
        self,
        *,
        language: str = DEFAULT_LANGUAGE,
        rng: random.Random | None = None,
        clock=None,
    ) -> None:
        self.language = self._known_language(language)
        self._rng = rng if rng is not None else random.Random()
        self._clock = clock
        self._recent: deque[str] = deque(maxlen=self.RECENT_LINES)
        self._recent_openings: deque[str] = deque(maxlen=self.RECENT_OPENINGS)

    # ------------------------------------------------------------- public

    def greeting(self, said: str = "") -> str:
        """A greeting answering ``said``, different from the recent ones."""
        part = self._part_of_day(said)
        banks = _BANKS[self.language]
        options = tuple(banks.get(part, ())) + banks["neutral"]
        chosen = self._choose(options)
        self._remember(chosen)
        return chosen

    def reset(self) -> None:
        """Forget what was said recently. For tests and session restarts."""
        self._recent.clear()
        self._recent_openings.clear()

    @property
    def recent(self) -> tuple[str, ...]:
        return tuple(self._recent)

    # ------------------------------------------------------------ choosing

    def _part_of_day(self, said: str) -> str:
        named = _NAMED_PART.search(str(said or ""))
        if named:
            return named.group(1).casefold()
        hour = self._hour()
        for name, hours in _PARTS:
            if hour in hours:
                return name
        return "night"

    def _hour(self) -> int:
        if self._clock is not None:
            return int(self._clock())
        return datetime.datetime.now().hour

    def _choose(self, options: tuple[str, ...]) -> str:
        """Prefer a line she has not just used, then a fresh opening.

        Each filter falls back to the wider pool rather than returning
        nothing, so a small bank still answers instead of going silent.
        """
        fresh = [line for line in options if line not in self._recent]
        pool = fresh or list(options)

        varied = [
            line for line in pool
            if self._opening(line) not in self._recent_openings
        ]
        pool = varied or pool

        return self._rng.choice(pool)

    def _remember(self, line: str) -> None:
        self._recent.append(line)
        opening = self._opening(line)
        if opening:
            self._recent_openings.append(opening)

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _opening(line: str) -> str:
        """The first couple of words, as a repetition signature.

        Korean has no space between a particle and its stem, so words alone
        would be a weak signal; the first two tokens still separate the
        neutral lines from each other well enough to rotate them.
        """
        words = re.findall(r"[\w']+", str(line).casefold(), flags=re.UNICODE)
        if not words:
            return ""
        return " ".join(words[:2])

    @staticmethod
    def _known_language(language: str) -> str:
        key = str(language or "").strip().casefold()
        return key if key in _BANKS else DEFAULT_LANGUAGE
