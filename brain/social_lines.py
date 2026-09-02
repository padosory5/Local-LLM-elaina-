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


# Being told off is social too, and it had no policy at all. Measured live,
# two insults in one session got two answers from whatever the model
# happened to produce -- "I'm sorry you're feeling that way" once, and once
#
#   "You're being rude. I'm here to help, but I can't keep up with your
#    attitude. Let's talk about something real."
#
# which is her arguing with the person she is meant to be helping. The
# brief for this project is that she should feel like a friend, and a
# friend who has just got something wrong does not open with your tone.
#
# So: take it, do not moralise, and ask for the thing again. Short, because
# a long apology is its own kind of demand.
_FRUSTRATION_BANKS: dict[str, tuple[str, ...]] = {
    "en": (
        "Sorry -- that one's on me.",
        "Fair enough. Let me try that again.",
        "You're right, I got that wrong.",
        "My fault. Say it once more and I'll get it right.",
        "Yeah, that wasn't good enough. What did you actually want?",
        "Sorry about that. Let's start over.",
        "I messed that up. What were you after?",
        "That's fair. Tell me again and I'll do better.",
    ),
    "ko": (
        "미안해, 내가 잘못했어.",
        "그러네. 다시 해볼게.",
        "내 실수야. 다시 말해줄래?",
        "미안. 뭐가 필요했는지 다시 알려줘.",
        "맞는 말이야. 이번엔 제대로 할게.",
    ),
}

# Hostility aimed at her, as a shape rather than as a list of insults: a
# second-person subject with a negative predicate, or a bare profanity with
# nothing else in the turn.
_AIMED_AT_HER = re.compile(
    r"\byou(?:'?re|\s+are|\s+r)?\s+(?:so\s+|such\s+a\s+|really\s+|"
    r"pretty\s+|kind\s+of\s+)?"
    r"(?:stupid|dumb|useless|terrible|awful|garbage|trash|"
    r"worthless|pathetic|broken|wrong|bad|annoying|the\s+worst)\b"
    r"|\bfuck\s+(?:you|off)\b"
    r"|\bshut\s+up\b"
    r"|\byou\s+suck\b"
    r"|짜증|답답해|바보",
    re.IGNORECASE,
)

# Anything that makes the turn a request as well as a complaint. When one
# of these is present the turn has to be answered, not soothed: "just
# answer my fucking question" is asking for the answer, and a sympathetic
# line is the complaint happening once more.
_CARRIES_A_REQUEST = re.compile(
    r"\?|\b(?:answer|tell|show|find|search|look|open|close|play|give|"
    r"send|make|do|try|again|explain|what|when|where|which|who|how|why)\b"
    r"|해줘|알려|보여",
    re.IGNORECASE,
)


# What is left over in a turn that is only being cross: discourse
# particles, the grammar holding them together, and more of the same
# judgement. Anything else -- a noun, a number, a place -- means the turn
# is also telling her something, and "you're wrong, the number is
# 206-221-7857" has to be acted on rather than apologised at.
_JUST_UPSET = frozenset({
    "okay", "ok", "yeah", "yes", "no", "nope", "well", "so", "oh", "ah",
    "um", "uh", "man", "god", "jeez", "wow", "seriously", "honestly",
    "just", "like", "really", "very", "such", "too", "again", "still",
    "i", "you", "your", "it", "its", "this", "that", "these", "those",
    "the", "a", "an", "my", "is", "are", "am", "was", "were", "be",
    "and", "but", "at", "in", "on", "of", "to", "with", "right", "now",
    # More of the same judgement, once the trigger itself is removed.
    "stupid", "dumb", "useless", "terrible", "awful", "bad", "worst",
    "garbage", "trash", "annoying", "wrong", "sucks", "pathetic",
    "worthless", "broken", "rubbish", "crap", "hopeless",
})


def reads_as_frustration(text: str) -> bool:
    """Whether this turn is hostility at her and nothing else."""
    said = " ".join(str(text or "").split())
    if not said or not _AIMED_AT_HER.search(said):
        return False

    # Take the hostility out and look at what is still standing. A turn
    # that only vents has nothing left; one that also asks or corrects
    # does, and that half is the part she owes an answer to.
    remainder = _AIMED_AT_HER.sub(" ", said)
    if _CARRIES_A_REQUEST.search(remainder):
        return False
    tokens = [word.casefold() for word in re.findall(r"[^\W_]+", remainder)]
    if len(tokens) <= 1:
        return True
    return all(token in _JUST_UPSET for token in tokens)


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

    def frustration(self) -> str:
        """A short, non-defensive answer to being told off."""
        chosen = self._choose(_FRUSTRATION_BANKS[self.language])
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
