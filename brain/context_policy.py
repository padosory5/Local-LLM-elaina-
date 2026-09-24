from __future__ import annotations

import re


def _content_words(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]{4,}", str(text or "").casefold()))


def subjects_agree(grounded_subject: str, current_subject: str) -> bool:
    """Whether stored evidence is about what is being asked about now.

    Either side being unknown is not a disagreement -- there is nothing to
    contradict, and the older behaviour (include it) is preserved.
    """
    grounded = _content_words(grounded_subject)
    current = _content_words(current_subject)
    if not grounded or not current:
        return True
    return bool(grounded & current)


def should_include_grounded_context(
    *,
    has_statement: bool,
    intent: str,
    is_follow_up: bool,
    topic_shift: bool,
    grounded_subject: str = "",
    current_subject: str = "",
) -> bool:
    """Keep verified evidence scoped to its subject instead of every turn.

    Being a follow-up was the whole test, and it is not enough: "which one
    would you choose?" after a dinner answer is a follow-up, and the last
    verified thing in the session was a GPU comparison from two turns
    earlier. She answered about graphics cards. A follow-up now also has to
    be about the same subject the evidence is about.
    """
    if not has_statement:
        return False
    if not subjects_agree(grounded_subject, current_subject):
        return False
    if intent == "fact_check":
        return True
    return bool(is_follow_up and not topic_shift)


# --------------------------------------------------------------------------
# Does this turn name a different thing?
# --------------------------------------------------------------------------
#
# ``subjects_agree`` above answers a *permissive* question -- may stored
# evidence be shown -- and its four-character floor is calibrated for that:
# erring towards "agree" only risks mentioning something already verified.
#
# Building a search query is the strict direction, and the same floor is
# wrong there twice over. "war", "fee" and "job" are three characters, and
# a Korean subject is two to four syllables, so on the session this was
# written for every comparison came back "agree" and the held subject was
# glued onto every later query:
#
#     [Query] source: active_task
#             text: 6/25 war Washington University Seattle
#
# Hence a separate test, in the other direction, measured in each script's
# own units -- Rule 4, and the reason it is not a parameter on the function
# above: the two questions want opposite defaults, and a shared threshold
# would have to be wrong for one of them.

_LATIN_TOKEN = re.compile(r"[a-z][a-z'-]{2,}")
_HANGUL_TOKEN = re.compile(r"[가-힯]{2,}")

# Words that name nothing. A subject made only of these is read as
# *unknown*, not as a disagreement, and that asymmetry is the whole safety
# margin here: "which one would you choose?" must keep inheriting the task
# it is asking about, so every word such a turn is made of has to be in
# here -- pro-forms, ordinals, and the few bare verbs a referring question
# is built from. Erring the other way loses the constraints the task spent
# three turns establishing, which is the exact failure
# ``_resolved_search_query`` exists to prevent.
_SAYS_NOTHING = frozenset({
    # articles, conjunctions, prepositions
    "the", "a", "an", "and", "or", "for", "about", "with", "from", "into",
    "than", "then", "also", "besides", "between", "over",
    # pronouns and determiners
    "this", "that", "these", "those", "it", "you", "your", "my", "our",
    "their", "his", "her", "its", "they", "them", "there", "here",
    # question words
    "what", "which", "who", "whom", "where", "when", "how", "why",
    # reference words: an ordinal points at the set, it does not name one
    "one", "ones", "first", "second", "third", "fourth", "fifth", "last",
    "next", "previous", "former", "latter", "other", "another", "same",
    "both", "each", "either", "neither",
    # quantity and vagueness
    "some", "any", "more", "most", "less", "few", "couple", "many", "much",
    "something", "anything", "everything", "nothing", "stuff", "kind",
    "type", "sort", "thing", "things", "option", "options",
    # the verbs a referring question is made of
    "would", "will", "can", "could", "should", "does", "did", "have", "has",
    "are", "was", "were", "been", "being", "choose", "pick", "prefer",
    "think", "say", "tell", "show", "give", "get", "know", "like", "want",
    "please", "just", "really", "instead", "again",
    # Korean pro-forms, demonstratives and connectives
    "그것", "이것", "저것", "그거", "이거", "저거", "그건", "이건", "저건",
    "그런", "이런", "저런", "그중", "무엇", "뭐가", "어떤", "어느", "얼마",
    "그리고", "그래서", "하지만", "그냥", "정말", "다시", "아까", "다른",
    "첫번째", "두번째", "세번째", "마지막", "번째", "말고", "대신",
})


# Two more kinds of word that sit in a subject field without naming one.
#
# The router's filing labels. It files a turn under "personal desire" or
# "Conversation" the way a clerk labels a folder, and that label arrives as
# the subject. Read as a subject, "Electric." -- the answer to "acoustic or
# electric?" -- was "about" personal desire, disagreed with the guitar it was
# answering, and split the task.
#
# And bare request verbs. When the router names no topic the subject is the
# utterance itself, so "compare them" arrived as the subject "compare them",
# "compare" counted as a thing, and asking to compare the monitors in hand
# started a new task holding none of them.
_NAMES_NO_SUBJECT = frozenset({
    "conversation", "chat", "chatting", "personal", "desire", "general",
    "misc", "miscellaneous", "information", "info", "greeting", "farewell",
    "compare", "comparing", "comparison", "check", "checking", "open", "find",
    "look", "search", "see", "try", "rank", "sort", "list", "recommend",
    "suggest", "explain", "help", "need", "use", "buy", "quit", "exit",
})


def _named_tokens(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """The Latin and Hangul words of a subject, separately.

    Kept apart because the two scripts need different rules about what
    counts as the same word: English needs a plural and a stem to match,
    Korean needs a noun to match itself carrying a particle.

    ``recommendation_state``'s own two lists join the stop list rather than
    being copied into it -- they are tuned for the same question on the
    task-boundary side, and two lists that must agree and can drift is how
    "spots" ends up naming a thing in one layer and nothing in the other.
    """
    said = str(text or "").casefold()
    skip = _SAYS_NOTHING | _NAMES_NO_SUBJECT
    try:
        from brain import recommendation_state
        skip = skip | recommendation_state._EMPTY_SUBJECTS
        skip = skip | recommendation_state._WEAK_SUBJECTS
    except Exception:
        pass
    try:
        from brain.recommendation import _FILING_WORDS
        skip = skip | _FILING_WORDS
    except Exception:
        pass
    latin = frozenset(_LATIN_TOKEN.findall(said)) - skip
    hangul = frozenset(_HANGUL_TOKEN.findall(said)) - skip
    return latin, hangul


def _shares_a_word(mine: frozenset[str], theirs: frozenset[str], *, floor: int) -> bool:
    """Whether two token sets name any word in common.

    Containment rather than equality, so "hotels" matches "hotel" and
    "전쟁이" matches "전쟁" without a stemmer or a particle table -- but only
    once the shorter side is long enough for containment to mean something.
    A three-letter floor here would call "war" and "warrant" the same word.
    """
    for word in mine:
        for other in theirs:
            if word == other:
                return True
            short, long = sorted((word, other), key=len)
            if len(short) >= floor and short in long:
                return True
    return False


# Where a subject stops being a subject. Six is the figure
# ``ChatEngine._offerable_subject`` already refuses to say out loud for the
# same reason -- past it, the router named no topic and this is the whole
# utterance wearing the field.
_SUBJECT_WORD_LIMIT = 6


def _reads_as_a_sentence(subject: str) -> bool:
    return len(str(subject or "").split()) > _SUBJECT_WORD_LIMIT


def names_a_different_subject(held_subject: str, current_subject: str) -> bool:
    """Whether this turn is about something other than what is held.

    Both arguments are *subjects* -- short noun phrases of the kind the
    goal layer resolves, not raw sentences. That is the contract, and it
    is what makes the stop list above finite: a sentence is made of verbs
    and connectives that no list of "words that name nothing" ever
    finishes covering, and passing one in can read as naming something.

    It is enforced rather than merely documented, because the callers
    cannot always honour it: when the router names no topic the subject
    falls back to the whole utterance, and "find me a few good hotels in
    Seoul" would then be compared word-for-word against "which one would
    you pick?". Over the limit, the side is read as unknown -- which is
    the safe answer, and the one the code had before this function.

    Conservative by construction, in the direction that keeps working
    conversations working: either side naming nothing is *not* a
    disagreement, so an ordinary follow-up ("pull up some spots for me",
    "which one?") still inherits everything the task established -- those
    turns arrive here with the subject the goal layer resolved for them,
    or with no subject at all.

    Cross-script pairs -- a Korean turn against an English task subject --
    read as different, and that is the honest answer available without
    translating: the two share no evidence a deterministic test can see.
    Callers must be able to fall back to something safe.
    """
    if _reads_as_a_sentence(held_subject) or _reads_as_a_sentence(
        current_subject,
    ):
        return False
    held_latin, held_hangul = _named_tokens(held_subject)
    turn_latin, turn_hangul = _named_tokens(current_subject)
    if not (held_latin or held_hangul) or not (turn_latin or turn_hangul):
        return False
    if _shares_a_word(held_latin, turn_latin, floor=4):
        return False
    if _shares_a_word(held_hangul, turn_hangul, floor=2):
        return False
    return True
