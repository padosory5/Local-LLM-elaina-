"""A word that is almost what the conversation is about.

Two ways the same thing happens, and one guard for both:

* the transcriber mishears a name the conversation has been using. Whisper
  does not know what the conversation is about, so "베인브리지" can come
  back "배인브리지", and an acronym read out in Korean comes back spelled in
  Hangul ("씨비티");
* the person says the wrong one. They have been asking about CPT --
  Curricular Practical Training -- and say "CBT", which is a different
  thing entirely and one letter away.

Either way the turn holds a word that is a near-miss of a term the
conversation holds, and that has not itself been part of the conversation.
A short acronym is asked about: one letter is the whole difference between
two unrelated things, and only the person knows which they meant. A longer
name is taken as the held one and said out loud, so the person can still
correct it.

What counts as a held term is deliberately narrow, because the last attempt
at this ("keep the person's spelling of names", KOREAN_SESSION_FINDINGS)
failed by treating every word they had typed as a name, and rewrote 시청자
into 시청한. Held means: an acronym; a capitalised name the way the
grounding guards read one; or a Korean word of three or more syllables that
has come up in at least two messages. And a Korean near-miss must keep its
syllable count and differ in exactly one syllable -- the shape of a
misheard vowel, not of a different word.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from brain import known_names
from brain.grounded_values import _proper_names

LANGUAGES = ("en", "ko")

# ------------------------------------------------------------ acronyms

_ACRONYM = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{2,4})(?![A-Za-z0-9])")
# As heard: all capitals as written, or a vowelless run typed in lower case
# ("cbt"). "opt" and "the" are words, and a word is not an acronym here.
_HEARD_ACRONYM = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{2,4})(?![A-Za-z0-9])")
_VOWEL = re.compile(r"[aeiou]", re.IGNORECASE)

# Letters by the sound of their names. A transcriber confuses letters that
# rhyme, and so does a person reaching for an acronym: CPT and CBT differ by
# P and B, both "-ee". CPT and CPU differ by T and U, which do not rhyme --
# that is another acronym, not a slip.
_RHYME: dict[str, str] = {}
for _family in ("BCDEGPTVZ", "AHJK", "FLMNSX", "IY", "QUW", "O", "R"):
    for _letter in _family:
        _RHYME[_letter] = _family

# Korean names of the letters, for an acronym Whisper spelled out in
# Hangul: "씨비티" is CBT, "씨피티" is CPT.
_LETTER_NAMES = {
    "에이치": "H", "더블유": "W", "에이": "A", "에프": "F", "아이": "I",
    "제이": "J", "케이": "K", "에스": "S", "브이": "V", "엑스": "X",
    "와이": "Y", "제트": "Z", "비": "B", "씨": "C", "디": "D", "이": "E",
    "지": "G", "엘": "L", "엠": "M", "엔": "N", "오": "O", "피": "P",
    "큐": "Q", "알": "R", "티": "T", "유": "U",
}
_LETTER_NAMES_LONGEST_FIRST = sorted(_LETTER_NAMES, key=len, reverse=True)


def acronyms_in(text: str) -> list[str]:
    """The acronyms written in a text, as its capitals: UW, CPT, OPT."""
    return _ACRONYM.findall(str(text or ""))


def spelled_acronym(word: str) -> str:
    """"씨비티" -> "CBT" when the whole word is letter names, else ""."""
    letters: list[str] = []
    index = 0
    while index < len(word):
        for name in _LETTER_NAMES_LONGEST_FIRST:
            if word.startswith(name, index):
                letters.append(_LETTER_NAMES[name])
                index += len(name)
                break
        else:
            return ""
    return "".join(letters) if len(letters) >= 3 else ""


def _spelled(word: str) -> tuple[str, str]:
    """(the letter-name part as written, its letters), or ("", "").

    The particle comes off first: "씨비티는" is 씨비티 plus 는, and whole it
    is not letter names at all -- which is how "씨비티는 얼마나 걸려?" went
    unread in the live demo.
    """
    for form in (word, _stem(word)):
        letters = spelled_acronym(form)
        if letters:
            return form, letters
    return "", ""


def _one_rhyming_letter_apart(heard: str, held: str) -> bool:
    if len(heard) != len(held) or heard == held:
        return False
    differences = [(a, b) for a, b in zip(heard, held) if a != b]
    if len(differences) != 1:
        return False
    a, b = differences[0]
    return a in _RHYME and _RHYME.get(a) == _RHYME.get(b)


# ------------------------------------------------------------ Korean

_HANGUL_WORD = re.compile(r"[가-힣]{2,}")
_PARTICLE = re.compile(
    r"(?:이랑|에서|에게|한테|까지|부터|으로|처럼|보다|은|는|이|가|을|를|에|로|와|과"
    r"|랑|도|만|의|요)$"
)
_SYLLABLES = 11172
_JUNGSEONG_SPAN = 588
_JONGSEONG_SPAN = 28
# Vowels a transcriber trades for each other: 베/배, 오/어, 우/으, 요/여.
_CONFUSABLE_VOWELS = (
    {"ㅐ", "ㅔ"}, {"ㅒ", "ㅖ"}, {"ㅗ", "ㅓ"}, {"ㅜ", "ㅡ"}, {"ㅛ", "ㅕ"},
    {"ㅙ", "ㅚ", "ㅞ"},
)
_JUNGSEONG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"


def _stem(word: str) -> str:
    """A Korean word without the particle attached to it."""
    stripped = _PARTICLE.sub("", word)
    return stripped if len(stripped) >= 2 else word


def _parts(syllable: str) -> tuple[int, int, int]:
    code = ord(syllable) - 0xAC00
    return (
        code // _JUNGSEONG_SPAN,
        (code % _JUNGSEONG_SPAN) // _JONGSEONG_SPAN,
        code % _JONGSEONG_SPAN,
    )


def _one_syllable_misheard(heard: str, held: str) -> bool:
    """The shape of a misheard name: same length, one syllable off.

    Four syllables or more may differ in up to two of that syllable's
    three parts; three syllables only in a confusable vowel, because a
    three-syllable ordinary word is too easily one consonant from another.
    """
    if len(heard) != len(held) or heard == held or len(held) < 3:
        return False
    if not all(0 <= ord(ch) - 0xAC00 < _SYLLABLES for ch in heard + held):
        return False
    differing = [(a, b) for a, b in zip(heard, held) if a != b]
    if len(differing) != 1:
        return False
    a, b = (_parts(ch) for ch in differing[0])
    changed = [i for i in range(3) if a[i] != b[i]]
    if len(held) >= 4:
        return len(changed) <= 2
    if changed != [1]:
        return False
    vowels = {_JUNGSEONG[a[1]], _JUNGSEONG[b[1]]}
    return any(vowels <= pair for pair in _CONFUSABLE_VOWELS)


# ------------------------------------------------------------ Latin names

def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def _name_words(text: str) -> list[str]:
    """Capitalised name words of four letters or more, read as the
    grounding guards read a name -- a capital that only opens a sentence
    is grammar, not a name."""
    words: list[str] = []
    for name in _proper_names(text):
        for word in name.split():
            word = word.strip(".,;:!?'\"")
            if len(word) >= 4 and word[:1].isupper() and not word.isupper():
                words.append(word)
    return words


def _misspelled_name(heard: str, held: str) -> bool:
    h, k = heard.casefold(), held.casefold()
    if h == k or h[:1] != k[:1] or abs(len(h) - len(k)) > 2:
        return False
    return _edit_distance(h, k) <= (1 if len(k) < 6 else 2)


# ------------------------------------------------------------ known places
#
# Asked cold, "인천공항에서 미국 시애틀까지" was heard "빈천공항에서 미국
# CLT까지" -- nothing in the conversation to compare with. Well-known places
# are a vocabulary of their own (brain/known_names.py). Set from real
# mishearings of the app's own model (Windows Korean voice, clean and noisy):
#
#     시의틀 / 시에틀 / 시리틀 -> 시애틀     뱅코버 -> 밴쿠버
#     지지도 -> 제주도                      로스앤젤리스 -> 로스앤젤레스
#
# and against what must stay: 시리즈 (series) is four sounds from 시애틀,
# and 지지도 is also "approval rating" -- which is why only a word used *as
# a place* is compared: with a locative particle, or before a place word.

_LOCATIVE = ("에서부터", "에서", "까지", "부터", "으로", "로", "에", "의", "행")
_PLACE_NEXT = ("공항", "국제공항", "역", "터미널", "다운타운", "시내", "날씨", "근처")
_CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JONGSEONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
# What a listener cannot tell apart: tense from plain, ㅐ from ㅔ.
_PLAIN = str.maketrans({"ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ",
                        "ㅉ": "ㅈ", "ㅐ": "ㅔ", "ㅒ": "ㅖ"})
_LETTER_READING = {
    "A": "에이", "B": "비", "C": "씨", "D": "디", "E": "이", "F": "에프",
    "G": "지", "H": "에이치", "I": "아이", "J": "제이", "K": "케이",
    "L": "엘", "M": "엠", "N": "엔", "O": "오", "P": "피", "Q": "큐",
    "R": "알", "S": "에스", "T": "티", "U": "유", "V": "브이",
    "W": "더블유", "X": "엑스", "Y": "와이", "Z": "제트",
}
_PLACED_ACRONYM = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{2,4})"
    r"(?=까지|에서|에|으로|로|행|부터|\s+(?:공항|날씨|다운타운|시내))"
)
_KNOWN_KOREAN = frozenset(known_names.korean_names())


def _bare(word: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+$", "", word)


def _as_places(text: str) -> list[tuple[str, str]]:
    """(as written, as one name) for each word the sentence uses as a place."""
    words = str(text or "").split()
    found: list[tuple[str, str]] = []
    for index, raw in enumerate(words):
        word = _bare(raw)
        if not re.fullmatch(r"[가-힣]{2,}", word):
            continue
        following = _bare(words[index + 1]) if index + 1 < len(words) else ""
        joined = False
        # "빈천 공항에서": a place and its head noun heard as two words. Not
        # after a particle -- "로스앤젤리스의 공항에서" is a name and then an
        # airport, not one name.
        heads = () if word.endswith(_LOCATIVE) else known_names.HEAD_NOUNS
        for head in heads:
            if following.startswith(head):
                rest = following[len(head):]
                if not rest or rest in _LOCATIVE:
                    found.append((f"{word} {head}", word + head))
                    joined = True
                break
        if joined:
            continue
        for particle in _LOCATIVE:
            if word.endswith(particle) and len(word) - len(particle) >= 2:
                stem = word[: -len(particle)]
                found.append((stem, stem))
                break
        else:
            if any(following.startswith(noun) for noun in _PLACE_NEXT):
                found.append((word, word))
    return found


def _place_distance(heard: str, name: str) -> int:
    """Sound parts between a heard word and a place name, or -1 if too far.

    Same number of syllables, at most two of them different, and at most two
    parts in all. Every real mishearing harvested was within two; three let
    "다운타운" (downtown) become "한인타운".
    """
    if len(heard) != len(name) or heard == name or len(name) < 3:
        return -1
    if not all(0 <= ord(ch) - 0xAC00 < _SYLLABLES for ch in heard + name):
        return -1
    changes = syllables = 0
    for a, b in zip(heard, name):
        if a == b:
            continue
        syllables += 1
        pa, pb = _parts(a), _parts(b)
        changes += sum(1 for i in range(3) if pa[i] != pb[i])
    return changes if changes <= 2 and syllables <= 2 else -1


def _jamo(word: str) -> str:
    out: list[str] = []
    for ch in word:
        code = ord(ch) - 0xAC00
        if 0 <= code < _SYLLABLES:
            out.append(_CHOSEONG[code // _JUNGSEONG_SPAN])
            out.append(_JUNGSEONG[(code % _JUNGSEONG_SPAN) // _JONGSEONG_SPAN])
            if code % _JONGSEONG_SPAN:
                out.append(_JONGSEONG[code % _JONGSEONG_SPAN])
    return "".join(out).translate(_PLAIN)


def _sounds_alike(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, _jamo(a), _jamo(b)).ratio()


def read_aloud(acronym: str) -> str:
    """An acronym as its letters sound in Korean: CLT -> 씨엘티."""
    return "".join(_LETTER_READING.get(ch, "") for ch in str(acronym or "").upper())


# ------------------------------------------------------------ the guard

ACRONYM = "acronym"
NAME = "name"


@dataclass(frozen=True)
class Slip:
    """A word in this turn that is almost a term the conversation holds."""

    heard: str
    meant: str
    kind: str
    said: str
    corrected: str

    @property
    def asks(self) -> bool:
        """An acronym is asked about; a longer name is assumed and said."""
        return self.kind == ACRONYM

    def log_line(self) -> str:
        how = "asking" if self.asks else "taking it as the held term"
        return (f"[Near Miss] {self.heard!r} is one step from "
                f"{self.meant!r}, which the conversation holds; {how}.")


def held_terms(
    messages: Sequence[str],
    *,
    extra: Iterable[str] = (),
    said_by_them: Sequence[str] = (),
) -> tuple[str, ...]:
    """The names and acronyms the conversation is about.

    ``messages`` are the recent turns, both sides. ``extra`` is what other
    layers have already judged to be the subject or an entity -- counted
    without the repetition a Korean word otherwise needs. ``said_by_them``
    is the person's own recent turns: a Korean word of four syllables or
    more that *they* used is held from its first mention. Measured in the
    live demo: she answered "베인브리지 섬에 카지노 있어?" with "Bainbridge
    Island에 ...", so 베인브리지 appeared once, was not held, and the
    transcriber's 배인브리지 on the next turn went unrepaired.
    """
    acronyms: list[str] = []
    names: list[str] = []
    korean: dict[str, set[int]] = {}

    def read(text: str, index: int, *, trusted: bool) -> None:
        acronyms.extend(_ACRONYM.findall(text))
        names.extend(_name_words(text))
        for word in _HANGUL_WORD.findall(text):
            _, spelled = _spelled(word)
            if spelled:
                acronyms.append(spelled)
                continue
            stem = _stem(word)
            if len(stem) >= 3:
                korean.setdefault(stem, set()).add(-1 if trusted else index)

    for index, message in enumerate(messages):
        read(str(message or ""), index, trusted=False)
    for item in extra:
        read(str(item or ""), -1, trusted=True)
    for message in said_by_them:
        for word in _HANGUL_WORD.findall(str(message or "")):
            stem = _stem(word)
            # A known place they named is held from its first mention,
            # whatever its length: it is a name, not an ordinary word.
            if stem not in _KNOWN_KOREAN:
                for particle in _LOCATIVE:
                    if stem.endswith(particle) and stem[: -len(particle)] in _KNOWN_KOREAN:
                        stem = stem[: -len(particle)]
                        break
            if (len(stem) >= 4 or stem in _KNOWN_KOREAN) and not _spelled(word)[1]:
                korean.setdefault(stem, set()).add(-1)

    held_korean = [
        stem for stem, seen in korean.items() if -1 in seen or len(seen) >= 2
    ]
    return tuple(dict.fromkeys(acronyms + names + held_korean))


def _heard_acronyms(said: str):
    for match in _HEARD_ACRONYM.finditer(said):
        token = match.group(1)
        if token.isupper() or not _VOWEL.search(token):
            yield token, token.upper()
    for word in _HANGUL_WORD.findall(said):
        written, spelled = _spelled(word)
        if spelled:
            yield written, spelled


def find(
    said: str,
    held: Iterable[str],
    *,
    seen: str = "",
    distinct: Iterable[frozenset] = (),
    known: Iterable[str] = (),
    only: str = "",
) -> Slip | None:
    """The one near-miss in this turn, or None.

    ``seen`` is the conversation so far: a word already in it is part of
    the conversation, not a slip. ``distinct`` holds pairs the person has
    already said are two different things, which are never asked twice.
    Ambiguity -- two held terms equally close -- returns None: guessing
    between them is worse than answering the turn as said.
    """
    text = str(said or "")
    if not text.strip():
        return None
    held = tuple(held)
    seen_folded = str(seen or "").casefold()
    settled = {frozenset(pair) for pair in distinct}
    seen_acronyms = {a for a in _ACRONYM.findall(str(seen or ""))}
    seen_acronyms |= {
        _spelled(w)[1] for w in _HANGUL_WORD.findall(str(seen or ""))
    } - {""}

    # ``only``: "assume" finds just the slips taken and said, "ask" just the
    # ones asked about -- so every name can be corrected before deciding
    # whether one question is still needed.
    held_acronyms = [t for t in held if _ACRONYM.fullmatch(t)]
    for written, heard in (_heard_acronyms(text) if only != "assume" else ()):
        if heard in held_acronyms:
            continue
        if heard in seen_acronyms:
            continue
        matches = [
            k for k in held_acronyms
            if _one_rhyming_letter_apart(heard, k)
            and frozenset((heard, k)) not in settled
        ]
        if len(matches) == 1:
            return _slip(text, written, matches[0], ACRONYM)

    held_names = [t for t in held if re.fullmatch(r"[A-Za-z][A-Za-z'-]+", t)
                  and not t.isupper()]
    for heard in (_name_words(text) if only != "ask" else ()):
        if heard.casefold() in seen_folded or heard in held_names:
            continue
        matches = [
            k for k in held_names
            if _misspelled_name(heard, k)
            and frozenset((heard, k)) not in settled
        ]
        if len(matches) == 1:
            return _slip(text, heard, matches[0], NAME)

    held_korean = [t for t in held if re.fullmatch(r"[가-힣]+", t)]
    for word in (_HANGUL_WORD.findall(text) if only != "ask" else ()):
        if _spelled(word)[1]:
            continue
        heard = _stem(word)
        if heard in seen_folded or heard in held_korean:
            continue
        matches = [
            k for k in held_korean
            if _one_syllable_misheard(heard, k)
            and frozenset((heard, k)) not in settled
        ]
        if len(matches) == 1:
            return _slip(text, heard, matches[0], NAME)

    places = tuple(
        name for name in known
        if re.fullmatch(r"[가-힣]{3,}", str(name or ""))
    )
    for written, heard in (_as_places(text) if only != "ask" and places else ()):
        if (
            heard in places or heard in held_korean or heard in seen_folded
            or heard in _PLACE_NEXT or heard in {"한인타운", "다운타운"}
        ):
            continue
        scored = sorted(
            (distance, name) for name in places
            for distance in (_place_distance(heard, name),)
            if distance >= 0 and frozenset((heard, name)) not in settled
        )
        if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
            continue
        return _slip(text, written, scored[0][1], NAME)

    # A real code that sounds like a place this person cares about: CLT is
    # Charlotte, and it is also what "시애틀" becomes. Only asked when the
    # place is one the conversation or their memories hold -- otherwise CLT
    # is simply Charlotte.
    salient = [
        term for term in held
        if term in _KNOWN_KOREAN or term in set(places)
    ]
    if only != "assume" and salient and _HANGUL_WORD.search(text):
        for match in _PLACED_ACRONYM.finditer(text):
            acronym = match.group(1)
            if acronym in held or acronym in seen_acronyms:
                continue
            reading = read_aloud(acronym)
            scored = sorted(
                (
                    (_sounds_alike(reading, name), name) for name in salient
                    if frozenset((acronym, name)) not in settled
                ),
                reverse=True,
            )
            if scored and scored[0][0] >= 0.7 and (
                len(scored) == 1 or scored[1][0] < scored[0][0]
            ):
                return _slip(text, acronym, scored[0][1], ACRONYM)
    return None


KNOWN = "known"


def slip_key(slip: Slip) -> str:
    """How a slip is remembered: an acronym by its letters, so "cbt", "CBT"
    and "씨비티" are the same slip; anything else as it was heard."""
    if slip.kind in {ACRONYM, KNOWN}:
        return spelled_acronym(slip.heard) or slip.heard.upper() if (
            spelled_acronym(slip.heard)
            or re.fullmatch(r"[A-Za-z0-9]+", slip.heard)
        ) else slip.heard
    return slip.heard


def known_slip(said: str, known: dict[str, str]) -> Slip | None:
    """A slip the person already confirmed once, found again.

    Asked once and answered, the same mistake is not asked about again:
    "씨비티는 얼마나 걸려?" after "CBT? -- yes, CPT" is the same slip, and
    the reply says what it was taken as instead.
    """
    text = str(said or "")
    if not known:
        return None
    for written, letters in _heard_acronyms(text):
        meant = known.get(letters)
        if meant and letters != meant:
            return _slip(text, written, meant, KNOWN)
    for heard in _name_words(text):
        if heard in known:
            return _slip(text, heard, known[heard], KNOWN)
    for word in _HANGUL_WORD.findall(text):
        stem = _stem(word)
        if stem in known and not _spelled(word)[1]:
            return _slip(text, stem, known[stem], KNOWN)
    return None


def names_the_heard(said: str, slip: Slip) -> bool:
    """Whether this turn says the word she took to be a slip."""
    text = str(said or "")
    key = slip_key(slip)
    if any(letters == key for _, letters in _heard_acronyms(text)):
        return True
    return slip.heard.casefold() in text.casefold()


def settled_pair(slip: Slip) -> frozenset:
    """The key ``find(distinct=...)`` checks, for a pair the person settled.

    An acronym is keyed by its letters, so "no, I meant cbt" settles CBT
    however it is written or spelled out next time.
    """
    # Keyed exactly as a remembered slip is. With only ACRONYM converted, a
    # "no, CBT" after she had taken "씨비티" as CPT settled the pair
    # ("씨비티", "CPT") -- and a later "CBT" was asked about again.
    return frozenset((slip_key(slip), slip.meant))


def _slip(said: str, heard: str, meant: str, kind: str) -> Slip:
    if re.fullmatch(r"[A-Za-z0-9'-]+", heard):
        corrected = re.sub(
            rf"(?<![A-Za-z0-9]){re.escape(heard)}(?![A-Za-z0-9])",
            meant, said, count=1,
        )
    else:
        corrected = said.replace(heard, meant, 1)
    return Slip(heard=heard, meant=meant, kind=kind, said=said,
                corrected=corrected)


# ------------------------------------------------------------ the answer

MEANT = "meant"
HEARD = "heard"

_YES = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|ya|right|correct|exactly|sure|i\s+did"
    r"|응|어|네|예|맞아|맞아요|맞습니다|맞지|그래|그래요|그렇지|웅|ㅇㅇ|넵)"
    r"(?=$|[\s.,!?~])",
    re.IGNORECASE,
)
_NO = re.compile(
    r"^\s*(?:no|nope|nah|not\s+really|아니|아니요|아니오|아뇨|아냐|노)"
    r"(?=$|[\s.,!?~])",
    re.IGNORECASE,
)


def read_answer(reply: str, slip: Slip) -> str:
    """Which one the person meant, from their answer: MEANT, HEARD or ""."""
    text = " ".join(str(reply or "").split())
    folded = text.casefold()
    names_meant = slip.meant.casefold() in folded
    names_heard = slip.heard.casefold() in folded
    if names_meant and not names_heard:
        return MEANT
    if names_heard and not names_meant:
        return HEARD
    if _NO.search(text):
        return HEARD
    if _YES.search(text):
        return MEANT
    return ""


@dataclass
class PendingSlip:
    """A near-miss asked about, waiting for its answer."""

    slip: Slip
    expires_at: float = field(default_factory=lambda: time.monotonic() + 120)

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.expires_at


def quoted_particle(word: str) -> str:
    """라고 or 이라고, whichever the last sound of the word takes."""
    last = str(word or "")[-1:]
    if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28:
        return "이라고"
    return "라고"
