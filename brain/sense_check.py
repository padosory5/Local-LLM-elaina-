"""Does a doubtful transcript say anything at all?

The transcriber can be sure of words nobody said. "벨뷰에서 시애틀 다운타운까지
버스 있어?" at 3 dB came back "해수에서 슬프 당선까지 버스있어" with word
probabilities no lower than a correct "간단한 걸로" in the same noise
(least-sure word 0.46 vs 0.45, average 0.53 vs 0.53), so no confidence
threshold separates them (voice/transcription_policy.heard_unclearly stops
at what the transcriber *knows* it guessed). Reading the sentence does:
its words do not fit together.

So the resident model is asked one narrow question -- could a person
really have said this -- and code decides the rest:

* only a spoken turn, and only one whose words the transcriber was not
  already sure of (``worth_reading``). A clear transcript is never
  second-guessed; a typed one never reaches here;
* the judge sees her last line (a bare fragment only makes sense as a
  reply to it) and never the person's own earlier turns. Measured: shown
  those, it called a plain change of subject ("오늘 날씨 어때?", "그 monitor
  어때? 50달러 이하로 찾아줘") garbled -- 4 of 205 sound turns;
* anything but a clean "garbled" reads as sense. A failed or odd reply
  answers the turn as it always would -- this check can only ever add a
  question, never lose a turn.

Measured on 36 real garbled transcripts and 205 sound ones (the probes'
good transcripts, casual fragments with the line they answer, and every
dogfood arc turn in both languages): 23/36 caught, 1/205 flagged -- "인천
공항에서 미국 시의틀까지 가는 내며 시간 걸려", a slip the near-miss repair
settles first, after which this check does not run. On twenty sentences it
was never shown (both languages, fresh noise): 5 of 14 garbled caught, 0 of
59 sound ones flagged. ~0.22 s a call on the shared GPU. A stricter
"do the words fit together" wording flagged nothing but caught only 16/36,
and nothing the chosen one missed. What both miss mostly still reads as a
sentence ("인천공항지의 인천인회가 있는 곳이 있어") -- a wrong sentence that
reads right is past what any reading can catch.
"""

from __future__ import annotations

import json
from typing import Any

PROMPT = (
    "You check speech-recognition transcripts for a voice assistant. A person "
    "spoke to her; the transcript is what the recognizer wrote down. On a "
    "noisy clip the recognizer can turn speech into real words that sound "
    "alike but do not fit together, so the sentence means nothing a person "
    "would say.\n\n"
    "Decide whether the transcript reads as something a person could really "
    "have said here.\n"
    "- sense: a person could have said it. Casual, short, fragmentary, "
    "slangy or ungrammatical speech, a reply to her last line, and a "
    "sentence with one odd or unknown word (a name, a term, a typo) all "
    "count as sense.\n"
    "- garbled: the words do not fit together into anything a person would "
    "say; most of it is misheard.\n"
    "When unsure, answer sense.\n"
    'Return JSON only: {"reads_as":"sense"} or {"reads_as":"garbled"}.'
)

# A clear transcript is left alone. Measured with large-v3-turbo: every
# garbled transcript in the probes averaged 0.77 or less per word, while
# clearly heard speech averaged 0.85-0.99.
READ_BELOW = 0.80


def worth_reading(text: str, word_average: float, *, below: float = READ_BELOW) -> bool:
    """A spoken transcript the transcriber was unsure of, of two words or more.

    ``word_average`` is 0.0 for a typed turn (and for a transcript that
    carried no word timings), which is never read. A one-word reply is
    never judged: "네" has nothing to fit together.
    """
    if not word_average or word_average <= 0.0:
        return False
    if word_average >= below:
        return False
    return len(str(text or "").split()) >= 2


def message(text: str, *, her_last_line: str = "") -> str:
    """The user message for the judge: her last line (a bare fragment only
    makes sense as a reply to it), then the transcript. The person's own
    earlier turns are deliberately not shown -- see the module docstring."""
    lines = []
    if her_last_line.strip():
        lines.append(f"Her last line: {her_last_line.strip()[:300]}")
    lines.append(f"Transcript: {str(text).strip()}")
    return "\n".join(lines)


def reads_as_garbled(content: Any) -> bool:
    """True only for a clean ``{"reads_as": "garbled"}``; everything else,
    including a reply that cannot be read, is sense."""
    try:
        payload = json.loads(str(content or ""))
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return str(payload.get("reads_as", "")).strip().casefold() == "garbled"


def quoted(text: str, *, limit: int = 40) -> str:
    """The transcript as she repeats it back: short, whole words."""
    text = " ".join(str(text or "").split()).strip(" .?!")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]) + "..."
