"""What the world says, for the things a model should not be trusted on.

qwen3:8b states facts it does not have. Measured on the everyday-answers
check: Portland, Maine's population came back as **694,000** (it is about
68,000), Bainbridge Island was given a casino it does not have, and OPT
paperwork was invented twice. No guard can fix what the model believes --
the only fix is to put the fact in front of it, which is what grounding
means everywhere else in this project.

Wikipedia's REST API is the source: keyless, ~0.2 s, and its one-paragraph
summary is already the sentence a person would want ("Its population was
68,408 at the 2020 census"). Wikidata was measured too and rejected for
this job: the same lookup needs an entity search, a property id, and then
rank and point-in-time qualifiers to choose between +66937, +66194 and
+68408 -- three correct answers for three different years -- and getting
that wrong is the failure this module exists to prevent.

Not for everything. A price, a timetable, the weather and "what is open
now" are what web search is for; this is for the standing facts an
encyclopedia settles.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

LANGUAGES = ("en", "ko")

# A period ends a sentence only after a letter that is not a capital: "U.S."
# and "Mr." are not sentence ends, "Maine." and "census." are.
_SENTENCE_END = re.compile(r"(?<=[a-z가-힣0-9)\]])[.!?]+(?:\s+|$)")

# Wikipedia asks for a descriptive agent and rate-limits anonymous bulk use.
_AGENT = "ElainaAI/1.0 (local voice assistant; contact via project owner)"
_TIMEOUT = 3.0
_CACHE_SECONDS = 1800.0
_cache: dict[tuple[str, str], tuple[float, "WorldFact | None"]] = {}


@dataclass(frozen=True)
class WorldFact:
    """One encyclopedia summary, with where it came from."""

    subject: str
    title: str
    summary: str
    url: str
    language: str

    def sentence(self, limit: int = 2) -> str:
        """The first sentences of it, which is what an answer needs.

        A period after a capital letter does not end a sentence: splitting
        on every one of them cut "the most populous city in the U.S. state
        of Maine" down to "the most populous city in the U.S." -- caught by
        this module's own test before it could be said out loud.
        """
        text = " ".join(self.summary.split())
        parts = _SENTENCE_END.split(text)
        kept = [part.strip() for part in parts if part.strip()][:limit]
        if not kept:
            return text
        said = ". ".join(kept)
        return said if said.endswith((".", "!", "?")) else said + "."


# Question words and the framing around them, so what is left is the thing
# being asked about. "What's the population of Portland, Maine?" -> the
# search wants "Portland, Maine", not the sentence.
_FRAMING = re.compile(
    r"^(?:so|and|also|hey|ok(?:ay)?)[,\s]+"
    r"|^(?:what|who|when|where|which|how)(?:'s|s|\s+is|\s+are|\s+was|\s+were|\s+did|\s+does|\s+do)?\s+"
    r"|^the\s+"
    r"|^(?:tell\s+me|do\s+you\s+know|can\s+you\s+tell\s+me)\s+(?:about\s+)?"
    r"|\b(?:right|really)\s*$",
    re.IGNORECASE,
)
# "the population of X", "the capital of X" -- the attribute is not the
# subject, and Wikipedia's title search does badly when it is included.
_ATTRIBUTE_OF = re.compile(
    r"^(?:.{0,40}?)\b(?:of|in)\s+(?P<subject>.+)$",
    re.IGNORECASE,
)
_WROTE = re.compile(
    r"^(?:wrote|invented|discovered|founded|directed|painted|composed|built)\s+(?P<subject>.+)$",
    re.IGNORECASE,
)
_KOREAN_TAIL = re.compile(
    r"\s*(?:수도|인구|면적|높이|길이|위치|뜻|의미)?\s*"
    r"(?:가|이|은|는|을|를)?\s*"
    r"(?:어디|뭐|무엇|언제|누구|얼마|몇)[가-힣]*\s*[?？]?\s*$"
)


# "물은 몇 도에서 끓어?" keeps 몇 in the middle of the sentence, so the tail
# above never matches it. Everything from the quantity word on is the
# question; what precedes it is the thing.
_KOREAN_VALUE_TAIL = re.compile(r"\s*(?:몇|얼마)[가-힣\s]*[?？]?\s*$")
_KOREAN_PARTICLE = re.compile(r"(?:은|는|이|가|의|에|에서|을|를)$")


def subject_of(question: str) -> str:
    """The thing being asked about, as a search would name it."""
    said = " ".join(str(question or "").split()).strip()
    if not said:
        return ""
    said = _KOREAN_TAIL.sub("", said).strip()
    said = _KOREAN_VALUE_TAIL.sub("", said).strip()
    words = said.split()
    if words and re.search(r"[가-힣]$", words[-1]):
        words[-1] = _KOREAN_PARTICLE.sub("", words[-1]) or words[-1]
        said = " ".join(words)
    previous = None
    while previous != said:
        previous = said
        said = _FRAMING.sub("", said).strip()
    for pattern in (_ATTRIBUTE_OF, _WROTE):
        found = pattern.match(said)
        if found:
            said = found.group("subject").strip()
            break
    return said.strip(" ,.?!\"'").strip()


# Only a named thing. "How many ounces are in a pound?" leaves "a pound",
# whose article search finds *Pound sterling* -- and a summary full of
# unrelated numbers would then "correct" the right answer, 16. A unit
# conversion is arithmetic the model does well; an encyclopedia is for
# Portland, Maine.
_A_NAME = re.compile(r"[A-Z][a-z]|[가-힣]{2,}")


def names_an_entity(subject: str) -> bool:
    """Whether the subject reads as something an encyclopedia has an
    article about, rather than a common noun or a unit."""
    said = str(subject or "").strip()
    if len(said) < 2:
        return False
    return bool(_A_NAME.search(said))


def _fetch(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": _AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def lookup(
    question: str,
    *,
    language: str = "en",
    fetch=None,
    timeout: float = _TIMEOUT,
) -> WorldFact | None:
    """The encyclopedia's summary for what this question is about.

    ``None`` whenever anything at all goes wrong -- no subject, no article,
    a slow network, a changed payload. A fact she cannot fetch leaves the
    answer exactly as it was; this may only ever add grounding.
    """
    subject = subject_of(question)
    if not subject or not names_an_entity(subject):
        return None
    language = language if language in LANGUAGES else "en"
    key = (language, subject.casefold())
    cached = _cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CACHE_SECONDS:
        return cached[1]
    fetch = fetch or _fetch
    fact = None
    try:
        host = f"https://{language}.wikipedia.org"
        found = fetch(
            f"{host}/w/rest.php/v1/search/title"
            f"?q={urllib.parse.quote(subject)}&limit=1",
            timeout,
        )
        pages = found.get("pages") or []
        if pages:
            page = pages[0].get("key") or pages[0].get("title") or ""
            summary = fetch(
                f"{host}/api/rest_v1/page/summary/{urllib.parse.quote(page)}",
                timeout,
            )
            extract = " ".join(str(summary.get("extract") or "").split())
            if extract:
                fact = WorldFact(
                    subject=subject,
                    title=str(summary.get("title") or page),
                    summary=extract,
                    url=str(
                        ((summary.get("content_urls") or {}).get("desktop") or {})
                        .get("page") or f"{host}/wiki/{page}"
                    ),
                    language=language,
                )
    except Exception:
        fact = None
    _cache[key] = (now, fact)
    return fact


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    """Numbers as written, commas removed, so 68,408 and 68408 are one."""
    return {
        match.group(0).replace(",", "").rstrip(".")
        for match in _NUMBER.finditer(str(text or ""))
    }


# The attribute being asked about, in both languages. A summary that does
# not mention it cannot settle it: asked for an area and handed a summary
# whose only numbers are a population and a census year, the check below
# would "correct" a right answer with an unrelated fact.
_ATTRIBUTES = (
    ("population", "인구"),
    ("capital", "수도"),
    ("area", "면적"),
    ("height", "높이"),
    ("length", "길이"),
    ("elevation", "고도"),
    ("depth", "깊이"),
    ("founded", "설립"),
    ("built", "건설"),
    ("born", "출생"),
    ("died", "사망"),
)


def covers_the_attribute(question: str, fact: WorldFact) -> bool:
    """Whether the summary speaks to what was actually asked."""
    asked = " ".join(str(question or "").split()).casefold()
    summary = fact.summary.casefold()
    for words in _ATTRIBUTES:
        if any(word in asked for word in words):
            return any(word in summary for word in words)
    return False


def contradicts(reply: str, fact: WorldFact) -> bool:
    """Whether the reply states a number the encyclopedia does not have.

    Measured: "Portland's population is around 694,000" against a summary
    saying 68,408. Only a number *of the same kind* counts -- a reply with
    no numbers, or one whose numbers all appear in the summary, is left
    alone, and so is a year that matches.
    """
    said = numbers(reply)
    if not said:
        return False
    known = numbers(fact.summary)
    if not known:
        return False
    return not (said & known)
