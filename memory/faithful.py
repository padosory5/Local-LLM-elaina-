"""A memory may say less than the person did. It may not say more.

The wording of a memory is a model's: the extractor turns "I'm a junior at
UW" into a third-person sentence, and it does not always stop there. Measured
in this project: "나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어" kept as "...at
Washington University" (a different university), "젠레스 존 제로" as "Genres
Zero", a dog called 콩 as "Kongi", and "I'm going to UW" as "the University
of Washington" -- a guess, when UW is also Wisconsin.

She may get things wrong when she talks. What she keeps about a person has to
be what they said. So the extraction is checked against their sentence before
it is kept, and when it says anything they did not, their own words are kept
instead (``as_said``). Nothing here asks a model.

What counts as "more":
  * a number they did not say;
  * a word that carries meaning and is nowhere in their sentence, compared
    loosely enough that "studies" is "study" and "끝나면" is "끝나고";
  * a "not" they did not say, or theirs left out.

Strict on purpose. A paraphrase wrongly refused costs nothing but phrasing --
their own sentence is kept, which is never wrong. One wrongly allowed is a
false memory.
"""

from __future__ import annotations

import re

# How a memory is framed, and the small words of a sentence. Not what it says.
_FRAME = frozenset({
    "the", "user", "users", "their", "theirs", "they", "them", "has", "have", "had", "having",
    "are", "was", "were", "been", "being", "does", "did", "doing", "and", "but", "with", "that",
    "this", "these", "those", "for", "from", "into", "onto", "who", "whom", "whose", "which",
    "when", "while", "where", "because", "since", "also", "about", "after", "before", "than",
    "then", "very", "there", "here", "its", "his", "her", "she", "him", "you", "your", "our",
    "ours", "not", "per", "via", "said",
})
_LATIN = re.compile(r"[A-Za-z][A-Za-z']*")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_HANGUL = re.compile(r"[가-힣]+")
# Endings and particles a Korean memory is written with, longest first. What
# is left is the part that has to be theirs.
_ENDINGS = (
    "있습니다", "입니다", "합니다", "습니다", "됩니다", "이에요", "에서는", "으로", "에서", "에게",
    "한테", "까지", "부터", "이다", "예요", "은", "는", "이", "가", "을", "를", "의", "에", "로",
    "와", "과", "도", "만", "요", "다",
)
_KOREAN_FRAME = frozenset({"사용자", "사용", "그들", "그"})
# "no" and "아니" are left out: at the front of a sentence they take back the
# one before ("no I mean ... Tacoma"), which is not this fact said in the negative.
_NOT = re.compile(
    r"\b(?:not|never|cannot|can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't)\b|n't\b"
    r"|안\s|못\s|않|없",
    re.IGNORECASE,
)


def _words(text: str) -> list[str]:
    found = []
    for word in _LATIN.findall(str(text or "")):
        word = word.casefold()
        if word.endswith("'s"):
            word = word[:-2]
        found.append(word.strip("'"))
    return [word for word in found if word]


def _same_word(a: str, b: str) -> bool:
    """Loose on endings: study/studies, live/lives, allergy/allergic, go/goes."""
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4:
        return a[:4] == b[:4]
    short, long = sorted((a, b), key=len)
    return len(short) >= 2 and long.startswith(short) and len(long) - len(short) <= 3


def _light(syllable: str) -> str:
    """A Hangul syllable without its final consonant: 살 and 삽 are one verb."""
    code = ord(syllable) - 0xAC00
    return chr(0xAC00 + (code // 28) * 28) if 0 <= code < 11172 else syllable


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) > len(ending):
            return word[: -len(ending)]
        if word == ending:
            return ""
    return word


def added(said: str, content: str) -> set[str]:
    """What the memory says that the person did not. Empty when it is theirs."""
    said, content = str(said or ""), str(content or "")
    extra: set[str] = set()

    theirs = {number.replace(",", "").rstrip(".") for number in _NUMBER.findall(said)}
    for number in _NUMBER.findall(content):
        if number.replace(",", "").rstrip(".") not in theirs:
            extra.add(number)

    said_words = _words(said)
    for word in _words(content):
        if len(word) < 3 or word in _FRAME:
            continue
        if not any(_same_word(word, theirs_) for theirs_ in said_words):
            extra.add(word)

    theirs_korean = _HANGUL.findall(said)
    compact = "".join(theirs_korean)
    starts = {_light(word[0]) for word in theirs_korean}
    for word in _HANGUL.findall(content):
        stem = _stem(word)
        if not stem or stem in _KOREAN_FRAME or _stem(stem) in _KOREAN_FRAME:
            continue
        if stem in compact or (len(stem) >= 2 and stem[:2] in compact):
            continue
        # A verb in the formal ending a memory is written in: 다녀 is kept as
        # 다닙니다 and 살아 as 삽니다, which share no whole syllable.
        if word.endswith("니다") and _light(word[0]) in starts:
            continue
        extra.add(word)

    if bool(_NOT.search(said)) != bool(_NOT.search(content)):
        extra.add("not")
    return extra


def as_said(text: str) -> str:
    """Their own words, marked as theirs: in front of her a bare "I'm a
    junior at UW" reads as hers."""
    said = " ".join(str(text or "").split())
    return f'They said: "{said}"'
