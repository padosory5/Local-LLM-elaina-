"""Is what they took for granted true?

Measured on the misunderstanding check (final code, read by hand): 4 of 8
false premises corrected. The prompt already told her to check an
assumption first, with an example -- and qwen3:8b, with the conversation,
the profile and search results in front of it, answered the question and
left the premise standing: "물은 100도에서 얼잖아, 그럼 냉동실은 몇 도야?"
got the freezer's temperature, "한국은 엔화 쓰잖아, 환전할 때 엔으로 바꾸면
되지?" got "환전 시 엔화 사용은 가능합니다", and "The Great Wall of China is
visible from space with the naked eye, right?" got a yes. Asked the one
narrow question alone, the same model knows all three.

So, like the sense check: only a turn that assumes something
(``told_not_asked.states_an_assumption``), one narrow question to the
resident model -- what did they take for granted, is it a fact, does it
hold -- and code decides the rest.

The judge sees only what they said. Shown her reply too (first version,
scratchpad premise_set.py), it said the reply already corrected them when
it had not ("번개는 같은 곳에 두 번 안 치잖아" -> "네, … 안전하다고"), and
took her view of a film for the truth against theirs ("그 영화 좀
지루했잖아, 그치?" -> "영화는 지루하지 않았습니다"). Whether her reply
already says so is decided here, by the words the correction adds to what
they said -- Olympia, Beijing, 삼성.

Anything but a clean "a fact, and it does not hold" leaves the reply alone:
this check can only ever correct a premise, never lose an answer.
"""

from __future__ import annotations

import json
import re
from typing import Any

PROMPT = (
    "Someone asked a voice assistant something and, in asking, took "
    "something for granted (\"since...\", \"...right?\", \"...잖아\", "
    "\"...맞지?\"). Check what they took for granted.\n\n"
    "Return JSON only, with these keys:\n"
    '- "assumed": what they took for granted, in a few words.\n'
    '- "kind": "fact" for something about the world that is simply true or '
    'false; "opinion" for taste or judgement (boring, better, worth it); '
    '"their_own" for their own life, plans, feelings or situation.\n'
    '- "holds": for a fact, false only when it is clearly and commonly known '
    "to be false; when unsure, true. For an opinion or their own life, "
    "always true.\n"
    '- "correction": only when a fact does not hold: one or two short '
    "sentences in the same language they used -- what is actually true, "
    "then, if that changes it, the right answer to what they asked. Polite; "
    "for Korean, 습니다체. Otherwise empty."
)


def message(said: str) -> str:
    """The user message for the judge: what they said, and nothing else."""
    return f"They said: {' '.join(str(said or '').split())}"


def correction(content: Any) -> str:
    """The correction to say; "" unless the judge cleanly said it is a fact
    and it does not hold."""
    try:
        payload = json.loads(str(content or ""))
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    if str(payload.get("kind") or "").strip().casefold() != "fact":
        return ""
    if payload.get("holds") is not False:
        return ""
    said = " ".join(str(payload.get("correction") or "").split())
    if not said or len(said) > 280 or said.endswith(("?", "？")):
        return ""
    return said


_WORDS = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d+(?:\.\d+)?")
_STOP = {
    "the", "and", "but", "not", "isn", "aren", "wasn", "doesn", "don", "didn",
    "actually", "that", "this", "with", "for", "from", "are", "was", "were",
    "its", "you", "your", "they", "their", "there", "can", "cannot", "will",
    "would", "have", "has", "had", "does", "did", "which", "what", "about",
}
# Saying a thing is not so -- "isn't the capital", "해당되지 않습니다" --
# and not a verb's "don't" ("grades don't define you" went along with the
# Einstein myth).
_DENIES = re.compile(
    r"\b(?:isn'?t|is\s+not|aren'?t|are\s+not|wasn'?t|was\s+not"
    r"|not\s+(?:the|a|an|in|on|from|true))\b"
    r"|아닙|아니라|아니에|아니예|않습|않아|없습",
    re.IGNORECASE,
)
_FIRST_SENTENCE = re.compile(r"(?<=[.!?。])\s+")
_FIRST_CLAUSE = re.compile(r"[,;.!?。]")
# How Korean says "not", as _terms reads it -- never the fact itself.
_DENIAL_TERMS = {"아니", "아닙", "않습", "않아", "없습"}


def _terms(text: str) -> set[str]:
    """Content words: Korean by their first two syllables (원화를/원화로),
    English whole, numbers as written."""
    terms = set()
    for word in _WORDS.findall(str(text or "")):
        if "가" <= word[0] <= "힣":
            terms.add(word[:2])
        elif word[0].isdigit():
            terms.add(word)
        elif word.casefold() not in _STOP:
            terms.add(word.casefold())
    return terms


def already_said(reply: str, correction: str, said: str) -> bool:
    """Whether her reply already tells them what the correction would.

    Either the reply carries everything the correction adds to their own
    words (Beijing, Olympia, 삼성, 원화) -- from its first sentence, or from
    all of it when the first only denies ("Seattle is not the capital… The
    capital is Olympia.") -- or the reply's first clause denies their
    assumption in their own words ("CPT는 PhD 학생만 해당되지 않습니다").
    Everything, not half: measured, "환전 시 엔화 사용 여부는 목적에 따라
    다릅니다. 일본에서 거래할 경우…" shared 사용 and 일본 with "…원화를
    사용합니다. 일본에서…" and passed for a correction without 원화 in it.
    Unsure is "not said": the same fact twice is better than the wrong one
    kept.
    """
    told = _terms(said) | _DENIAL_TERMS
    correction = str(correction or "").strip()
    first = _FIRST_SENTENCE.split(correction, maxsplit=1)[0]
    # A first sentence that only denies ("한국은 엔화를 사용하지 않습니다.")
    # adds nothing but a verb; measured, "사용" in her wrong reply then
    # counted as the correction. The true fact is in what follows.
    source = correction if _DENIES.search(first) else first
    new = _terms(source) - told or _terms(correction) - told
    if new and new <= _terms(reply):
        return True
    first_clause = _FIRST_CLAUSE.split(str(reply or "").strip(), maxsplit=1)[0]
    return bool(_DENIES.search(first_clause)) and len(_terms(first_clause) & told) >= 2
