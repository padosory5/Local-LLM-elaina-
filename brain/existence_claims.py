"""Saying a thing does not exist is a claim, and it needs evidence too.

Every grounding guard in this project so far checks what a reply *asserts*:
a price, a spec, a name, a completed action. None of them looks at the
opposite shape -- a reply that asserts something is not there.

Measured live, in Korean, from a cold model with no search:

    You said:  육이오 전쟁에 대해 알려줘
    Elaina:    (that war never happened)
    You said:  한국전쟁이 육이오 전쟁이야
    Elaina:    한국전쟁은 1950년 6월 25일 시작된 전쟁으로, 육이오 전쟁이라는
               별명도 있습니다.

She knew the whole time. "육이오" is the Sino-Korean reading of the date
6·25, and the model did not connect the reading to the event -- so the
router chose ``direct_answer`` with ``freshness_required: false``, nothing
looked anything up, and a gap in the model's recall was spoken as a fact
about the world.

That asymmetry is the bug. "I don't know" costs the user one turn; "that
never happened" costs them the truth, and it is the more confident-sounding
of the two. So a denial of existence gets the same treatment a price does:
without evidence behind it, it is replaced by an honest sentence.

**A hedge is not a denial.** "I'm not aware of any such thing", "제가 아는
한 없습니다" -- those are claims about *her*, they are true, and they are
exactly what she should say. Nothing here touches them; the patterns below
match only sentences that make the claim about the world.

**With evidence it stands.** A search that ran and found nothing supports
"there is no Costco in Gangnam". This guard fires only when nothing was
checked.

LANGUAGES: en, ko -- both, per Rule 4. The Korean half is not a
translation of the English half: Korean denies existence with 존재하지
않다 and with a demonstrative plus 없다, and the second one is a shape
English has no equivalent of.
"""

from __future__ import annotations

import re


LANGUAGES = ("en", "ko")


# --------------------------------------------------------------------------
# What a denial looks like
# --------------------------------------------------------------------------
#
# Narrow on purpose, in one specific direction: this matches claims that a
# thing or an event *is not there*, and not ordinary disagreement. "No,
# that's not right" corrects the user and is fine; "that war never
# happened" rewrites history and is not. The line between them is whether
# the sentence is about the existence of something.

_DENIES_ENGLISH = re.compile(
    # "there is no such war", "no such thing as"
    r"\bno\s+such\b"
    # "does not exist", "didn't exist"
    r"|\b(?:does|do|did|will)\s*n[o']?t\s+exist\b"
    r"|\b(?:doesn|didn|don)['’]?t\s+exist\b"
    # "never existed", "never happened", "never took place"
    r"|\bnever\s+(?:existed|happened|occurred|took\s+place)\b"
    # "did not happen", "didn't occur"
    r"|\b(?:did|does|do)\s*n[o']?t\s+(?:happen|occur|take\s+place)\b"
    r"|\b(?:didn|doesn|don)['’]?t\s+(?:happen|occur|take\s+place)\b"
    # "there was no war called ...", "there is no city named ..."
    r"|\bthere\s+(?:is|are|was|were)\s+no\s+\S+\s+(?:called|named)\b"
    # "is not a real place", "isn't a real event"
    r"|\b(?:is|are|was|were)\s*n[o']?t\s+(?:a\s+|an\s+)?real\b"
    r"|\b(?:isn|aren|wasn|weren)['’]?t\s+(?:a\s+|an\s+)?real\b"
    # "is fictional", "is fictitious", "is made up"
    r"|\b(?:is|are|was|were)\s+(?:a\s+|an\s+)?(?:fictional|fictitious)\b"
    r"|\b(?:is|are|was|were)\s+made\s+up\b",
    re.IGNORECASE,
)

# Korean denies existence two ways, and only the first is a verb the way
# English has one.
_DENIES_KOREAN = re.compile(
    # 존재하지 않습니다 / 실존하지 않아요 / 존재하지 않는
    r"존재하지\s*않"
    r"|실존하지\s*않"
    # 일어나지 않았습니다 / 발생하지 않았다 / 벌어지지 않았습니다
    r"|일어나지\s*않았"
    r"|발생하지\s*않았"
    r"|벌어지지\s*않았"
    # 그런 전쟁은 없습니다 -- a demonstrative, the thing, then 없다. The
    # gap is one token wide so that a hedge ("그런 전쟁은 제가 아는 한
    # 없습니다") falls outside it: that sentence is about her and is true.
    r"|(?:그런|그러한|이런|그와\s*같은)\s*\S{1,12}\s*없"
    # 육이오 전쟁이라는 전쟁은 없습니다 -- naming the thing, then denying it
    r"|라는\s*\S{1,12}\s*없"
    # 역사적으로 없었습니다 / 역사에 없는
    r"|역사(?:적으로|에)\s*\S{0,6}\s*없"
    # 가공의 인물입니다 / 허구입니다
    r"|가공의\s*\S{1,12}"
    r"|허구(?:입니다|이다|예요|에요|입니까)",
)


def denies_existence(text: str) -> tuple[str, ...]:
    """The sentences in this reply that say something is not there.

    Returned rather than counted, so a caller can replace exactly the
    sentences that made the claim and a report can quote them.
    """
    said = str(text or "").strip()
    if not said:
        return ()
    found = []
    for sentence in _sentences(said):
        if _DENIES_ENGLISH.search(sentence) or _DENIES_KOREAN.search(sentence):
            found.append(sentence)
    return tuple(found)


def _sentences(text: str) -> list[str]:
    try:
        from brain.conversation_style import sentences
    except Exception:
        return [
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+", str(text or "").strip())
            if part.strip()
        ]
    return sentences(text)


def without(text: str, denials: tuple[str, ...]) -> str:
    """This reply with those sentences taken out.

    Whole sentences, because a denial with the denial edited out of it is
    not a shorter sentence -- it is a different claim, and nobody wrote it.
    """
    kept = [
        sentence for sentence in _sentences(text)
        if sentence not in set(denials)
    ]
    return " ".join(kept).strip()


def unsupported(text: str, *, searched: bool, evidence: str = "") -> tuple[str, ...]:
    """The denials in this reply that nothing checked.

    ``searched`` says a lookup ran for this turn and ``evidence`` is what it
    came back with. Either one present means the denial has something behind
    it and is left alone -- "I looked and there is no Costco in Gangnam" is
    a fair sentence, and the whole point of the search is that she is
    allowed to say it afterwards.
    """
    if searched or str(evidence or "").strip():
        return ()
    return denies_existence(text)
