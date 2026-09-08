"""Which language this turn is in, decided once, deterministically.

``config.yaml``'s ``language.response`` used to answer this, once, at
construction: one language for the life of the process. That is wrong for
someone who speaks two, and it is wrong in a specific way -- it is not that
she picked the wrong language, it is that the question was never asked per
turn.

Three rules shape everything here.

**The script is the signal.** Whisper transcribes speech into the script of
the language it heard, so a Korean utterance arrives as Hangul whether it was
typed or spoken. The detected-language field from
:mod:`voice.stt` is a *tiebreaker*, not the primary evidence -- it exists,
it is free, and it is currently discarded, but the text usually already
says.

**Sticky, not per-utterance.** A Korean speaker says "그 monitor 어때?" and
means Korean; an English speaker says "let's eat 삼겹살 tonight" and means
English. Code-switching is normal and must not flip her. So is "ok" -- the
most frequent turn in real use and the least informative. The language
changes only when the evidence is *decisive*, and stays put otherwise.

**An explicit request wins and keeps winning.** Saying "영어로 말해줘" pins
English until something unpins it, even though the request itself is Korean.
A pin is a decision about the conversation, not about one sentence.

No model call. This runs on every turn, before anything else, and a
language decision that costs three seconds is not a language decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


LANGUAGES = ("en", "ko")


ENGLISH = "en"
KOREAN = "ko"

SUPPORTED = (ENGLISH, KOREAN)

# Hangul syllables. Jamo blocks are deliberately excluded: a lone jamo is
# usually an emoticon ("ㅋㅋ", "ㅠㅠ") and says nothing about which language
# the sentence is built in.
_HANGUL = re.compile(r"[가-힯]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_ANY_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# Below this, a turn never changes the language. "ok", "네", "yeah", "응" are
# the most common things anyone says and the least reliable evidence of
# anything; letting them switch makes her flip constantly mid-conversation.
#
# Counted in the units each language actually uses. "방금 퇴근했어" is a
# whole sentence and two words; a three-word floor calibrated on English
# refused to switch on it, and she answered a Korean conversation in
# English for three turns. Four syllables is the Korean equivalent, chosen
# so the acknowledgements that must never switch her still do not:
# "응" (1), "네" (1), "고마워" (3).
MIN_SWITCH_WORDS = 3
MIN_SWITCH_SYLLABLES = 4

# Whisper's own confidence. Below this its language field is not evidence,
# and the safe reading of unclear audio is "carry on in whatever we were
# already speaking".
MIN_DETECTION_CONFIDENCE = 0.60


@dataclass(frozen=True)
class LanguageDecision:
    """The language of this turn, and why."""

    language: str
    switched: bool = False
    pinned: bool = False
    reason: str = ""

    def log_line(self) -> str:
        state = "pinned" if self.pinned else (
            "switched" if self.switched else "kept"
        )
        return f"[Language] {self.language} ({state}): {self.reason}"


# --------------------------------------------------------------------------
# Asking her to change language
# --------------------------------------------------------------------------
#
# A closed set, and it can be one: there are only so many ways to say "speak
# English", and unlike the failure classes in conversation_style this is a
# request with a fixed shape rather than a register with an open one.
#
# The language of the *request* is irrelevant -- "영어로 말해줘" is a Korean
# sentence that asks for English, and reading it as evidence of Korean would
# be exactly backwards.

_ASKS_FOR_ENGLISH = re.compile(
    r"\b(?:speak|talk|say\s+it|answer|reply|respond)\b[^.?!]{0,20}"
    r"\bin\s+english\b"
    r"|\benglish\s+(?:please|from\s+now)\b"
    r"|\bin\s+english\s+(?:please|from\s+now)\b"
    r"|영어로\s*(?:말|얘기|대답|답|해|바꿔|부탁)"
    r"|영어\s*로\s*(?:말|해|바꿔)"
    r"|영어로만",
    re.IGNORECASE,
)

_ASKS_FOR_KOREAN = re.compile(
    r"\b(?:speak|talk|say\s+it|answer|reply|respond)\b[^.?!]{0,20}"
    r"\bin\s+korean\b"
    r"|\bkorean\s+(?:please|from\s+now)\b"
    r"|\bin\s+korean\s+(?:please|from\s+now)\b"
    r"|한국어로\s*(?:말|얘기|대답|답|해|바꿔|부탁)"
    r"|한국말로\s*(?:말|얘기|대답|답|해|바꿔|부탁)?"
    r"|한국어로만",
    re.IGNORECASE,
)


def reads_as_language_request(text: str) -> str:
    """The language this turn asks her to speak, or an empty string.

    Only a *request about language*. "How do you say this in Korean?" is a
    translation question and must not repin her, which is why every pattern
    needs a speech verb aimed at her rather than the bare word "korean".
    """
    said = str(text or "")
    if not said.strip():
        return ""
    # English first only because the two cannot both match a well-formed
    # request; a sentence naming both is ambiguous and repins nothing.
    wants_english = bool(_ASKS_FOR_ENGLISH.search(said))
    wants_korean = bool(_ASKS_FOR_KOREAN.search(said))
    if wants_english and wants_korean:
        return ""
    if wants_english:
        return ENGLISH
    if wants_korean:
        return KOREAN
    return ""


# --------------------------------------------------------------------------
# What language is this written in?
# --------------------------------------------------------------------------


def script_language(text: str) -> str:
    """Which language the sentence is *built* in, or an empty string.

    Not "does it contain Hangul". English sentences essentially never
    contain Hangul, but Korean sentences contain English constantly -- so
    presence alone would call "그 monitor 어때?" Korean (right) and "let's
    eat 삼겹살 tonight" Korean (wrong).

    The proxy is density against the other script: one Hangul syllable
    carries roughly as much as one English word, so the grammatical spine is
    whichever side has more units.

        "그 monitor 어때?"            3 syllables vs 1 word  -> Korean
        "let's eat 삼겹살 tonight"    3 syllables vs 3 words  -> English
        "이 laptop 사고 싶어"          5 syllables vs 1 word  -> Korean
        "I love 김치"                 2 syllables vs 2 words  -> English

    A heuristic, and honest about it: a long English sentence quoting a long
    Korean phrase reads as Korean, and a Korean sentence made almost
    entirely of loanwords reads as English. Both are rare, both are caught
    by the stickiness rule above -- one odd sentence does not move her --
    and for speech the detected language breaks the tie.
    """
    said = str(text or "")
    hangul = len(_HANGUL.findall(said))
    latin = len(_LATIN_WORD.findall(said))
    if not hangul and not latin:
        return ""
    if hangul > latin:
        return KOREAN
    if latin and not hangul:
        return ENGLISH
    # Mixed, and the English side is at least as big. Korean loanword-heavy
    # sentences land here, which is why this is not decisive on its own --
    # `decide` treats it as English only when the turn is long enough to be
    # worth believing.
    return ENGLISH


def _word_count(text: str) -> int:
    return len(_ANY_WORD.findall(str(text or "")))


def _long_enough_to_mean_something(text: str) -> bool:
    """Whether this turn carries enough to be evidence of a language.

    Each script is measured in its own unit. A Korean sentence of two
    words is a sentence; an English turn of two words is usually "ok
    thanks".
    """
    said = str(text or "")
    syllables = len(_HANGUL.findall(said))
    if syllables:
        return syllables >= MIN_SWITCH_SYLLABLES
    return _word_count(said) >= MIN_SWITCH_WORDS


def decide(
    said: str,
    *,
    current: str = ENGLISH,
    pinned: str = "",
    detected: str = "",
    probability: float = 0.0,
    supported: tuple[str, ...] = SUPPORTED,
) -> LanguageDecision:
    """The language to answer this turn in.

    ``current`` is what she has been speaking, ``pinned`` is a language the
    user explicitly asked for and has not taken back, and ``detected`` /
    ``probability`` are Whisper's own reading when the turn was spoken.
    """
    current = current if current in supported else supported[0]

    asked = reads_as_language_request(said)
    if asked in supported:
        return LanguageDecision(
            language=asked,
            switched=asked != current,
            pinned=True,
            reason="the user asked her to speak it",
        )

    if pinned in supported:
        return LanguageDecision(
            language=pinned,
            switched=pinned != current,
            pinned=True,
            reason="pinned by an earlier request",
        )

    # Too short to mean anything. "ok", "네", "yeah" -- the most frequent
    # turns there are, and evidence of nothing.
    if not _long_enough_to_mean_something(said):
        return LanguageDecision(
            language=current,
            reason="too short to be evidence",
        )

    written = script_language(said)
    heard = str(detected or "").strip().lower()
    confident = heard in supported and probability >= MIN_DETECTION_CONFIDENCE

    # The two agree, or only one of them said anything.
    if written in supported and (not confident or heard == written):
        if written == current:
            return LanguageDecision(language=current, reason="unchanged")
        return LanguageDecision(
            language=written, switched=True,
            reason="the whole turn is in it",
        )

    # They disagree, and the microphone was sure. Speech that transcribed
    # into the other script is usually a transcription fault rather than a
    # language change, so the confident detector wins.
    if confident and heard != written:
        if heard == current:
            return LanguageDecision(
                language=current,
                reason=f"heard as {heard}, written as {written}; kept",
            )
        return LanguageDecision(
            language=heard, switched=True,
            reason=f"heard as {heard} with confidence",
        )

    return LanguageDecision(language=current, reason="no decisive evidence")
