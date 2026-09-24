"""A search runs in the language the person was speaking.

The router writes in English -- the whole prompt is English, and nothing
ever asked it not to. Measured over a real Korean session, 4 of 5 searches
went out in a language the turn was not
(``scripts/query_language_check.py``). That is wrong twice over.

**The answer is on the wrong web.** The person's locale is South Korea and
the question is in Korean. "서울에서 제일 유명한 냉면집 알려줘" became
"seoul most famous cold noodles restaurant", and the pages that actually
answer that are Korean ones. The one query in that session that *was* in
Korean is also the only search whose answer went uncomplained-about.

**A translated proper noun is a different thing.** 워싱턴 대학교 came back
as "Washington University in Seattle". "University of Washington" is in
Seattle; "Washington University" is in St. Louis. The query named a real
university, and not the one the person attends.

Asking the router nicely was tried first and measured, because it is the
cheap fix and it would have been the right one if it worked. It did not
move (see ``docs/KOREAN_SESSION_FINDINGS.md``), which is this project's
standing result for prompt wording against a confirmed behaviour. So the
turn's own words are used instead, and they are the one thing guaranteed
to be in the right language: the person wrote them.

**What this deliberately will not do.** A turn that leans on the one
before it -- "그런 뻔한거 말고 뭐가 유명한지 알려줘" -- does not carry its
subject, and searching those words alone would be worse than the English
query, not better. The subject the conversation is holding is stored in
English by the router, so there is nothing in Korean to put back. Those
turns are left exactly as they are, and the log says which ones they were.
That is the honest boundary of a fix that adds no translation step.

LANGUAGES: en, ko. The mechanism is symmetric and only the Korean half is
exercised today, because a query in the wrong language is currently
something that only happens to Korean turns.
"""

from __future__ import annotations

import re


LANGUAGES = ("en", "ko")

ENGLISH = "en"
KOREAN = "ko"

_HANGUL = re.compile(r"[가-힣]")


def language_of(text: str) -> str:
    """Which language a query is written in.

    Presence, not density -- unlike :func:`brain.turn_language.script_language`,
    which reads sentences. A query is a handful of terms, so "which script
    is the grammatical spine" is not a question it can answer, and a query
    carrying any Hangul at all will reach Korean pages.
    """
    return KOREAN if _HANGUL.search(str(text or "")) else ENGLISH


# --------------------------------------------------------------------------
# Does this turn carry its own subject?
# --------------------------------------------------------------------------
#
# A closed grammatical class, the same technique as the Korean request
# endings in ``brain.intent_router`` and the Korean deictics in
# ``brain.task_session``: discourse anaphora, the words a sentence opens
# with when it is continuing one that came before.
#
# Used only in the conservative direction. A match means "leave this turn
# alone", so a word missing from the list costs nothing worse than the
# behaviour that was already there.

#
# The first version only looked at how the turn *opened*, and a second
# Korean session showed why that cannot work. Korean points back from the
# middle of a sentence as easily as from the start --
#
#     그 섬에 카지노가 있는지 알려줘                -> "그 섬에 카지노가 있는지"
#     맛있게 만드는법을 좀 검색해서 알려줘 그런 뻔한거 말고
#
# -- and, more often, does not point back at all: it simply leaves the
# subject out. "얼마나 많은 사람들이 봤는데" is a complete Korean question
# about a drama the sentence never names. So a turn now has to pass two
# tests to be searched on its own words: nothing in it points back, and
# something in it names a thing.

_KOREAN_POINTS_BACK = re.compile(
    # Anywhere in the turn. 그런/그렇-/그거/그중 and friends; 이런/저런;
    # 거기/여기; and a bare 그 or 이 standing in front of a noun, which is
    # "that island" / "this drama". A bare 저 is left out on purpose: it
    # is also the humble "I".
    r"(?:^|\s)(?:"
    r"그(?:런|러한|렇|거|것|게|건|걸|중|쪽|때|곳|분)"
    r"|[이저](?:런|러한|렇|거|것|게|건|걸|쪽)"
    r"|거기|여기|아까|방금\s*그"
    r"|[그이]\s+(?=[가-힣])"
    r")"
    # Connectives that continue a thread, at the start.
    r"|^\s*(?:다시|또|말고|대신|그리고|근데|그런데|그럼|그러면|그래서)",
)

_ENGLISH_POINTS_BACK = re.compile(
    r"^\s*(?:so|then|and|but|also|okay|ok|right|well|anyway|"
    r"what\s+about|how\s+about|instead|again)\b"
    r"|\b(?:that|those|these|it|they|them|there)\b",
    re.IGNORECASE,
)

# A Korean noun announces itself with the particle after it, and that is
# the whole noun detector: no morphological analyser, just the closed class
# of case and topic particles. 워싱턴 대학교"는", 김치찌개"를", 시애틀"에서".
_PARTICLE = (
    r"(?:은|는|이|가|을|를|에서|에게|한테|까지|부터|으로|로|와|과|랑|이랑|"
    r"의|도|만|에)"
)
_MARKED_NOUN = re.compile(
    rf"(?:^|\s)([가-힣A-Za-z0-9]{{2,}}?){_PARTICLE}(?=\s|$|[?.!,~])",
)

# Nouns that name nothing a search could find: people in general, a thing,
# a way, a time, a place-word, pronouns -- and the evidence words, because
# "데이터로 알려줘" asks for proof of the last answer rather than about data.
_NAMES_NOTHING = frozenset({
    "사람", "사람들", "것", "거", "게", "데", "곳", "때", "방법", "법",
    "만드는법", "정도", "시간", "몇시간", "이거", "그거", "저거", "여기",
    "거기", "오늘", "요즘", "지금", "나", "너", "저", "제", "내", "우리",
    "뭐", "누구", "어디", "언제", "얼마", "아무것", "뭔가", "무엇", "좀",
    "데이터", "근거", "출처", "자료", "통계", "수치", "숫자", "증거",
})

_LATIN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_LATIN_SAYS_NOTHING = frozenset({"ok", "okay", "lol", "the", "a", "an", "and"})


def _names_a_thing(text: str) -> bool:
    """Whether this Korean turn names something a search could look for."""
    for stem in _MARKED_NOUN.findall(text):
        if stem not in _NAMES_NOTHING:
            return True
    # Brands, products and codes usually arrive in Latin letters even in a
    # Korean sentence: "i20", "sevis fee", "gpt모델".
    for word in _LATIN_NAME.findall(text):
        if len(word) >= 2 and word.casefold() not in _LATIN_SAYS_NOTHING:
            return True
    # A request drops the object particle: "삼성전자 주가 알려줘". What sits
    # right in front of the request verb is the thing being asked about.
    request = _TRAILING_REQUEST.search(text)
    if request:
        for word in text[:request.start()].split()[-2:]:
            word = word.strip(" ,.?!~")
            # "데이터로 알려줘" carries its particle and still names nothing.
            bare = re.sub(rf"{_PARTICLE}$", "", word)
            if _HANGUL.search(word) and not (
                word in _NAMES_NOTHING or bare in _NAMES_NOTHING
            ):
                return True
    return False


def stands_alone(said: str) -> bool:
    """Whether this turn can be searched on without the turns before it.

    Deliberately strict: it has to be obvious. Everything this returns
    False for keeps the router's query, which carries the subject the
    conversation resolved -- in English, but about the right thing.
    """
    text = str(said or "").strip()
    if not text:
        return False
    if _HANGUL.search(text):
        if _KOREAN_POINTS_BACK.search(text):
            return False
        return _names_a_thing(text)
    return not _ENGLISH_POINTS_BACK.search(text)


# --------------------------------------------------------------------------
# Turning a turn into a query
# --------------------------------------------------------------------------
#
# The request grammar comes off the end and nothing else is touched. Korean
# is head-final, so "알려줘" and "찾아줘" sit at the end of the sentence and
# carry none of what is being asked about -- the same closed class
# ``brain.intent_router`` already uses to tell a request from a remark.
#
# No attempt is made to pull out a noun phrase. Korean needs a
# morphological analyser for that, there is not one here, and a question
# asked whole is a perfectly good search: "워싱턴 대학교는 어떤 과가
# 유명해?" finds the answer, and it finds it for the right university.

_TRAILING_REQUEST = re.compile(
    r"(?:\s*(?:좀|한번|한 번))?\s*"
    r"(?:알려\s*(?:줘|주세요|줄래|주라|다오)|"
    r"찾아\s*(?:줘|주세요|봐|보자|줄래)|"
    r"검색\s*(?:해\s*줘|해\s*주세요|해\s*봐)|"
    r"보여\s*(?:줘|주세요|줄래)|"
    r"추천\s*(?:해\s*줘|해\s*주세요|좀)|"
    r"말해\s*(?:줘|주세요)|"
    r"설명\s*(?:해\s*줘|해\s*주세요))"
    r"\s*[.!?~]*\s*$",
)

# The trailing politeness a spoken request ends on, once the verb is gone.
_TRAILING_NOISE = re.compile(r"[\s.,!?~]*$")


def as_query(said: str) -> str:
    """This turn, said the way it would be typed into a search box."""
    text = " ".join(str(said or "").split())
    if not text:
        return ""
    text = _TRAILING_REQUEST.sub("", text)
    return _TRAILING_NOISE.sub("", text).strip()


def in_the_turns_language(
    query: str, *, said: str, language: str,
) -> tuple[str, str]:
    """The query to actually run, and why it is that one.

    Returns the query unchanged, with an empty reason, whenever nothing
    here applies -- which is every English turn, every Korean turn the
    router already wrote in Korean, and every turn that leans on the one
    before it.
    """
    wanted = str(language or "").strip().lower()[:2]
    if wanted not in LANGUAGES:
        return query, ""
    if language_of(query) == wanted:
        return query, ""
    if language_of(said) != wanted:
        # The turn is not written in the language it was decided to be --
        # a bare "ok" inside a Korean conversation, say. There is nothing
        # of the person's own to put back, so the router's query stands.
        return query, ""
    if not stands_alone(said):
        return query, (
            "the turn leans on the one before it, so its own words are "
            "not a query"
        )
    rewritten = as_query(said)
    if not rewritten:
        return query, ""
    return rewritten, f"the turn was {wanted} and the query was not"
