"""Being told something is not being asked about it.

Measured on the misunderstanding check: told "저 다음 달에 이사가요", she
answered "다음 달에 이사하시나요?"; told "다음 주 금요일에 시애틀로 돌아가",
"다음 주 금요일에 시애틀로 돌아가시나요? 편안하게 가시길 바랍니다". The prompt
already said not to repeat a fact back as a question, and on the next run
the model did it again -- so a question in her reply that only repeats what
they just told her is taken out, here, in code (the project's rule for a
behaviour confirmed live).

What stays: a real follow-up. "어떤 이유로 이사하세요?" asks something new
and shares one word with what they said, not most of it.
"""

from __future__ import annotations

import re

from brain import memory_gate

LANGUAGES = ("en", "ko")

_SENTENCES = re.compile(r"(?<=[.!?？。！])\s+")
_KOREAN_WORD = re.compile(r"[가-힣]{2,}")
_ENGLISH_WORD = re.compile(r"[a-z]{3,}", re.IGNORECASE)
_ENGLISH_STOP = {
    "the", "you", "your", "are", "and", "that", "this", "with", "for", "was",
    "were", "have", "has", "had", "did", "does", "will", "would", "going",
    "really", "just", "then", "there", "what", "when", "how",
}


def _stems(text: str) -> set[str]:
    """Content words, as their first two syllables in Korean (so 이사가요
    and 이사하시나요 are the same word) and whole in English."""
    korean = {word[:2] for word in _KOREAN_WORD.findall(str(text or ""))}
    english = {
        word.casefold() for word in _ENGLISH_WORD.findall(str(text or ""))
        if word.casefold() not in _ENGLISH_STOP
    }
    return korean | english


# Asking for anything at all -- a question, a request, a suggestion.
_ASKS_FOR_SOMETHING = re.compile(
    r"\?|？"
    r"|\b(?:please|can\s+you|could\s+you|would\s+you|will\s+you|help\s+me|recommend|"
    r"find|look\s+up|search|tell\s+me|show\s+me|what|how|why|when|where|which|who)\b"
    r"|해\s*줘|알려\s*줘|찾아\s*줘|보여\s*줘|추천|어때|할까|뭐|어떻게|왜|언제|어디|누구",
    re.IGNORECASE,
)


def only_tells(said: str) -> bool:
    """They told her something about their own life, and asked for nothing.

    Measured, the main answer to such a turn -- with the whole conversation,
    the profile and the tools in its prompt -- missed what was said as often
    as not: "내 여동생은 부산에 살아" -> "제가 잘 지내고 있습니다", "My
    birthday is March 14th." -> "That will make for a long day."
    """
    text = " ".join(str(said or "").split())
    return (
        bool(text)
        and memory_gate.carries_something_to_remember(text)
        and not _ASKS_FOR_SOMETHING.search(text)
    )


# Something assumed while asking: "…잖아", "…right?", "since…". Measured:
# "물은 100도에서 얼잖아, 그럼 냉동실은 몇 도야?" was answered with the
# freezer's temperature and the 100 left standing.
_ASSUMES = re.compile(
    r"잖아|잖아요|잖습니까|맞지\s*[?？]|맞죠\s*[?？]|거지\s*[?？]|거죠\s*[?？]"
    r"|\bright\s*\?|\bisn'?t\s+it\b|\baren'?t\s+they\b|\bdon'?t\s+they\b"
    r"|^\s*since\b|\bgiven\s+that\b|\bas\s+you\s+know\b",
    re.IGNORECASE,
)


def states_an_assumption(said: str) -> bool:
    return bool(_ASSUMES.search(" ".join(str(said or "").split())))


# Assuming without the words above: "Why is Seattle the capital of
# Washington state?" takes it that Seattle is. Measured twice, her own
# correction of it was lost from the reply and nothing checked again. For
# the premise judge only; the answer's prompt is unchanged by these.
_ALSO_ASSUMES = re.compile(
    r"^\s*why\s+(?:is|are|was|were|does|do|did)\b|^\s*왜\s"
    r"|\b(?:isn'?t|aren'?t|wasn'?t|weren'?t|don'?t|doesn'?t|didn'?t|won'?t|can'?t"
    r"|haven'?t|hasn'?t)\s+(?:it|he|she|they|you|we|there|that)\s*[?？]",
    re.IGNORECASE,
)


def worth_checking(said: str) -> bool:
    """Whether what they said takes something for granted that the premise
    judge (brain/premise_check.py) should look at."""
    text = " ".join(str(said or "").split())
    return bool(_ASSUMES.search(text) or _ALSO_ASSUMES.search(text))


_NUMBER = re.compile(r"\d[\d,.]*")


def repeats(reply: str, earlier: str) -> bool:
    """Whether a reply says again what an earlier one said: every number in
    it already there, and most of its words.

    Measured: after "아니 CPT 말고 OPT" the answer was her CPT answer word
    for word. And measured the other way: "Portland, Maine" answered with a
    different population and no "Maine" in it is a new answer, not a repeat
    -- asked again without the history, it came back 685,000.
    """
    if set(_NUMBER.findall(str(reply or ""))) - set(_NUMBER.findall(str(earlier or ""))):
        return False
    mine = _stems(reply)
    return bool(mine) and len(mine & _stems(earlier)) >= 0.7 * len(mine)


def answers_the_one_before(reply: str, said: str, earlier: str) -> bool:
    """Whether a reply is the answer to the question before this one.

    Measured on scripts/live_basics_check.py, asking "물은 몇 도에서 끓어?"
    one turn after "What is the freezing point of water in Fahrenheit?":
    "물의 동결점은 32도 화씨입니다." ``repeats`` cannot see it -- her previous
    answer was English and this one Korean, so no word stem crosses the two.
    What does cross is the number, and what is missing is anything the new
    question added: 끓 appears nowhere in the reply.
    """
    mine = set(_NUMBER.findall(str(reply or "")))
    if not mine or not mine <= set(_NUMBER.findall(str(earlier or ""))):
        return False
    asked = _stems(said) - _stems(earlier)
    return bool(asked) and not (asked & _stems(reply))


_MINE_KO = r"(?:내|제|우리|저희)"


def _your(match: re.Match) -> str:
    return "Your " if match.group(0)[0].isupper() else "your "


def as_theirs(reply: str, said: str) -> str:
    """Their "my sister" said back as hers, put right.

    Measured: "내 여동생은 부산에 살아" -> "내 여동생이 부산에 삽니다. 알고
    있습니다." -- with the prompt saying whose facts they are. Only on a turn
    that only tells her something, where she has no "my" of her own to
    speak of; a question ("What's my name?") is left alone.
    """
    if not only_tells(said):
        return reply
    text = str(reply or "")
    for noun in re.findall(rf"(?:^|\s){_MINE_KO}\s+([가-힣]+)", said):
        text = re.sub(rf"(^|\s){_MINE_KO}\s+(?={re.escape(noun[:2])})", r"\1", text)
    for noun in re.findall(r"\bmy\s+([a-z]{2,})", said, re.IGNORECASE):
        text = re.sub(rf"\bmy\s+(?={re.escape(noun)}\b)", _your, text, flags=re.IGNORECASE)
    return text


def takes_it_in(reply: str, said: str) -> bool:
    """Whether a reply shows it understood what they said: a sentence of it
    that is not a question back, about the thing they said -- a word of it
    in it. A question after that is a follow-up, not a miss: measured,
    "여동생분께서 부산에 계시는군요. 잘 계시는가요?" was replaced for ending
    in a question, by "당신의 여동생이 부산에 살아 있다는 걸 알았습니다"."""
    told = _stems(said)
    for sentence in _SENTENCES.split(" ".join(str(reply or "").split())):
        sentence = sentence.strip()
        if sentence and not sentence.endswith(("?", "？")) and _stems(sentence) & told:
            return True
    return False


def without_asking_back(reply: str, said: str) -> str:
    """The reply without any question that only repeats what they told her.

    Returns the reply unchanged when they did not state a fact, or when no
    such question is in it; "" when that question was all there was.
    """
    text = str(reply or "").strip()
    if not text:
        return reply
    if not memory_gate.carries_something_to_remember(said) or memory_gate.is_question(said):
        return reply
    told = _stems(said)
    if len(told) < 2:
        return reply
    kept: list[str] = []
    removed = False
    for sentence in _SENTENCES.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if sentence.endswith(("?", "？")):
            asked = _stems(sentence)
            # max(1, ...): measured, "이사하시나요?" carries one content
            # word and was kept, while "다음 달에 이사하시나요?" was taken
            # out. A real follow-up still survives -- "어떤 이유로
            # 이사하세요?" shares one of its three words with what they said.
            if asked and len(asked & told) >= max(1, round(0.6 * len(asked))):
                removed = True
                continue
        kept.append(sentence)
    if not removed:
        return reply
    return " ".join(kept).strip()
