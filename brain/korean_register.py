"""Say a Korean sentence in 습니다체 when it came out in 해요체.

``personality_ko.txt`` specifies 습니다체 and qwen3:8b does not hold it.
Measured across three live runs, roughly half of her generated Korean
sentences came back 해요체, the prompt did not move it, and asking for the
line again mostly produced 해요체 a second time -- once it produced 반말,
which is further from the register than what it replaced.

That is the exact shape of problem this project has learned to fix in code
rather than in wording. The fix is narrow on purpose:

**Only exact endings, only form.** Every entry here is a change of
*politeness form*, never of meaning, tense, or content. "인상적이에요" and
"인상적입니다" say the same thing about the same subject; nothing here can
turn one claim into another, which is what makes a deterministic rewrite
safe on text that has already passed the grounding guards.

**Unsure means unchanged.** Endings that need real verb morphology --
"고마워요", "있나요?", "지내세요?", "그렇군요" -- are left alone rather than
guessed at. Half-converted Korean is worse than politely wrong Korean, and
the style layer's re-say still gets its attempt at those.
"""

from __future__ import annotations

import re


LANGUAGES = ("ko",)


# The ㄹ that a future form inserts, and how to take it back off. "도와드릴
# 게요" is 도와드리 + ㄹ게요, and 겠습니다 attaches to 도와드리 -- so the
# syllable has to be decomposed rather than sliced.
_HANGUL_BASE = 0xAC00
_JONGSEONG_COUNT = 28
_RIEUL = 8


def _ends_in_rieul(syllable: str) -> bool:
    if not ("가" <= syllable <= "힣"):
        return False
    return (ord(syllable) - _HANGUL_BASE) % _JONGSEONG_COUNT == _RIEUL


def _without_rieul(syllable: str) -> str:
    return chr(ord(syllable) - _RIEUL)


def _has_final_consonant(syllable: str) -> bool:
    """Whether this Hangul syllable ends in a consonant (받침)."""
    if not ("가" <= syllable <= "힣"):
        return False
    return (ord(syllable) - _HANGUL_BASE) % _JONGSEONG_COUNT != 0


# Exact ending -> exact replacement. Ordered longest-first within each
# family so "이에요" is tried before "예요" and "겠네요" before "네요".
_ENDINGS: tuple[tuple[str, str], ...] = (
    ("이에요", "입니다"),
    ("이네요", "입니다"),
    ("예요", "입니다"),
    ("에요", "입니다"),
    ("있어요", "있습니다"),
    ("없어요", "없습니다"),
    ("있네요", "있습니다"),
    ("없네요", "없습니다"),
    ("겠어요", "겠습니다"),
    ("겠네요", "겠습니다"),
    ("좋아요", "좋습니다"),
    ("싫어요", "싫습니다"),
    ("같아요", "같습니다"),
    ("돼요", "됩니다"),
    ("해요", "합니다"),
    ("드려요", "드립니다"),
    # Regular for a stem ending in 리: 느려요 -> 느립니다, 기다려요 ->
    # 기다립니다, 어울려요 -> 어울립니다. Listed after 드려요 so that one
    # keeps its own entry rather than being caught by this.
    ("려요", "립니다"),
    ("주세요", "주십시오"),
    ("하세요", "하십시오"),
    ("보세요", "보십시오"),
)

# Phrases that stopped being grammar. 안녕하세요 is -세요 by shape, and the
# -세요 rule turned it into "안녕하십시오" -- which is a word, and is not
# what anyone says. A rule about verb endings has to know which endings are
# no longer verb endings.
_LEXICALISED = (
    "안녕하세요",
    "안녕히 계세요",
    "안녕히 가세요",
    "어서 오세요",
    "안녕하십니까",
)

# What separates a sentence from its punctuation, kept so the mark comes
# back exactly as it was.
_TRAILING = re.compile(r"[\s.!?~]*$")
_HANGUL = re.compile(r"[가-힣]")


def to_formal_sentence(sentence: str) -> str:
    """One sentence in 습니다체, or unchanged when the ending is not known."""
    text = str(sentence or "")
    if not _HANGUL.search(text):
        return text

    tail_match = _TRAILING.search(text)
    tail = tail_match.group(0) if tail_match else ""
    body = text[: len(text) - len(tail)] if tail else text
    if not body:
        return text

    if any(body.endswith(fixed) for fixed in _LEXICALISED):
        return text

    # A question keeps its own forms. "할까요?" is how this register asks
    # permission and "있나요?" needs morphology this module does not have,
    # so questions are left to the re-say entirely.
    if "?" in tail:
        return text

    # "도와드릴게요" -> "도와드리겠습니다".
    if body.endswith("게요"):
        stem = body[:-2]
        if stem and _ends_in_rieul(stem[-1]):
            stem = stem[:-1] + _without_rieul(stem[-1])
        if stem:
            return stem + "겠습니다" + tail

    for ending, formal in _ENDINGS:
        if body.endswith(ending):
            return body[: -len(ending)] + formal + tail

    # The regular case, once the irregular ones above have had their turn.
    # A stem ending in a consonant takes 습니다 where 해요체 takes 어요 or
    # 아요: 알겠어요 -> 알겠습니다, 깊어요 -> 깊습니다, 먹어요 -> 먹습니다.
    # A stem ending in a vowel does not -- 와요 becomes 옵니다, which needs
    # more than this module knows -- so the 받침 test is what keeps it from
    # guessing.
    for ending in ("어요", "아요"):
        if body.endswith(ending):
            stem = body[: -len(ending)]
            if stem and _has_final_consonant(stem[-1]):
                return stem + "습니다" + tail

    return text


def to_formal(text: str) -> str:
    """Every sentence of this reply in 습니다체, where the ending is known.

    Sentence by sentence, because a reply mixes them: the measured drift
    was usually one 해요체 clause sitting between two correct ones.
    """
    said = str(text or "")
    if not _HANGUL.search(said):
        return said

    pieces = re.split(r"(?<=[.!?])(\s+)", said)
    rebuilt = [
        piece if index % 2 else to_formal_sentence(piece)
        for index, piece in enumerate(pieces)
    ]
    return "".join(rebuilt)
