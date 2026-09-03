"""Reading a browser run as an answer, not as an action report.

``ActionPlanResult.status == "done"`` means the planner stopped cleanly:
every tool call it made was grounded, nothing failed outright, and it had
something to say. It does *not* mean the user's question was answered.
Those are two different claims, and collapsing them is what let

    "Does the Lotte Hotel have a room available September 18?"

come back as "Opened." -- a true sentence about the run, and no answer at
all. Reported live after the 4E.4 dispatch fix landed.

So a live-verification turn ends in one of four states, and the reply is
built from the state rather than from the planner's narration:

    VERIFIED_TRUE   the page said yes, and the summary carries it
    VERIFIED_FALSE  the page said no
    NOT_VERIFIED    the browser ran but never reached a readable answer
    FAILED          the browser could not run, or fell over

The default is NOT_VERIFIED. A summary earns VERIFIED_* by containing a
finding -- a verdict or a real value -- and a bare action report contains
neither, so it can never be mistaken for one.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

VERIFIED_TRUE = "verified_true"
VERIFIED_FALSE = "verified_false"
NOT_VERIFIED = "not_verified"
FAILED = "failed"

STATES = (VERIFIED_TRUE, VERIFIED_FALSE, NOT_VERIFIED, FAILED)


# Checked first, and deliberately so: "availability is not shown on this
# page" carries both a negative and a positive token, but it is neither --
# it is the planner saying it could not tell. A hedge outranks everything.
_HEDGE = re.compile(
    r"\b(?:could\s?n[o']t|could\s+not|can\s?n[o']t|cannot|un(?:able|clear)|"
    r"was\s?n[o']t\s+able|not\s+able|no\s+(?:clear\s+)?(?:answer|information|"
    r"details?|results?)|"
    r"does\s?n[o']t\s+(?:show|say|list|indicate|display)|"
    r"did\s?n[o']t\s+(?:show|say|list|load|appear)|"
    r"not\s+(?:shown|listed|displayed|visible|stated)|"
    r"you(?:'ll|\s+will|\s+may|\s+might|\s+can)\s+(?:need|want|have)\s+to|"
    r"i\s+do\s?n[o']t\s+(?:know|have)|"
    r"check\s+(?:the\s+)?(?:site|website|hotel|page)\s+(?:directly|yourself)|"
    r"recommend\s+(?:that\s+you\s+)?(?:contact|call|visit))\b",
    re.IGNORECASE,
)

# The page said no. Reached only once the hedges above are ruled out, so
# "not available" here really is the page's answer and not a complaint
# about the page.
_NEGATIVE = re.compile(
    r"\b(?:sold\s?out|fully\s+booked|no\s+(?:rooms?|vacanc(?:y|ies)|"
    r"availability|openings?)|"
    r"(?:is|are|was|were)\s+(?:not|un)available|"
    r"nothing\s+available|out\s+of\s+stock|"
    r"no\s+longer\s+available)\b"
    r"|매진|만실|품절",
    re.IGNORECASE,
)

# The page said yes. A verdict needs a verb, not just the word
# "availability" -- otherwise "Opened the availability page." reads as a
# confirmed booking, which is exactly the kind of false claim this module
# exists to prevent.
_VERDICT = re.compile(
    r"\b(?:is|are|was|were|has|have|had|shows?|showed|showing|lists?|listed|"
    r"found|remains?|remaining|still)\s+(?:\w+\s+){0,3}"
    r"(?:available|availability|vacan(?:t|cy|cies)|open|free|bookable|"
    r"in\s+stock)\b"
    r"|\b(?:rooms?|suites?|tables?|seats?|slots?)\s+(?:are\s+)?available\b"
    r"|\byou\s+can\s+(?:book|reserve|still\s+get)\b"
    r"|\b(?:available|availability)\s+(?:for|on|from)\s+\w+"
    r"|예약\s?가능|잔실|재고\s?있",
    re.IGNORECASE,
)

# A concrete value read off the page is a finding in its own right: a
# nightly rate answers "how much", and a page that quotes one for a date
# is a page that had something for that date.
_VALUE = re.compile(
    r"[$£€¥₩]\s?\d"
    r"|\b\d[\d,.]*\s?(?:usd|eur|gbp|krw|jpy|won|dollars?|euros?)\b"
    r"|\b\d+\s+(?:rooms?|suites?|options?|results?|nights?)\b"
    r"|\bper\s+night\b|\ba\s+night\b|\b원\b",
    re.IGNORECASE,
)

# What a search can still add once the live check came up empty. A pure
# "is it free on the 18th" has no such component -- a snippet cannot
# honestly answer it, so falling back would only dress up a guess.
_SEARCHABLE_COMPONENT = re.compile(
    r"\b(?:which|what|where|who|find|search|look\s*up|list|options?|"
    r"recommend|suggest|compare|best|cheapest|top\s+\d+|"
    r"how\s+much|price|prices|rate|rates|cost|costs|"
    r"phone|number|contact|address|hours|reviews?|ratings?)\b"
    r"|추천|가격|요금|후기",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BrowserOutcome:
    """What a browser run established, and what may honestly be said."""

    state: str
    answer: str
    evidence: str = ""
    reason: str = ""

    @property
    def verified(self) -> bool:
        """Whether the page actually settled the question, either way."""
        return self.state in {VERIFIED_TRUE, VERIFIED_FALSE}

    @property
    def ran(self) -> bool:
        """Whether the browser itself worked, regardless of the answer."""
        return self.state != FAILED


def read(
    summary: str,
    *,
    succeeded: bool,
    needs_verification: bool,
    goal: str = "",
) -> BrowserOutcome:
    """Classify a finished browser run against the goal it was run for.

    ``needs_verification`` comes from the deliberation layer's own need
    (``live_verification``), so nothing here re-reads the request to
    decide what kind of turn this is -- that decision is already made.
    """
    text = " ".join(str(summary or "").split())
    if not succeeded:
        return BrowserOutcome(
            FAILED, text, evidence=text, reason="the browser run failed",
        )
    if not needs_verification:
        # An action goal ("click Images") has nothing to verify: doing it
        # *is* the outcome, and the planner's confirmation is the answer.
        return BrowserOutcome(
            VERIFIED_TRUE, text, evidence=text,
            reason="an action goal, so the action itself is the result",
        )
    if not text:
        return BrowserOutcome(
            NOT_VERIFIED, "", evidence="",
            reason="the run finished with nothing to report",
        )
    if _HEDGE.search(text):
        return BrowserOutcome(
            NOT_VERIFIED, "", evidence=text,
            reason="the summary says it could not tell",
        )
    if _NEGATIVE.search(text):
        return BrowserOutcome(
            VERIFIED_FALSE, text, evidence=text,
            reason="the page reported none available",
        )
    if _VERDICT.search(text) or _VALUE.search(text):
        return BrowserOutcome(
            VERIFIED_TRUE, text, evidence=text,
            reason="the summary carries a finding read off the page",
        )
    return BrowserOutcome(
        NOT_VERIFIED, "", evidence=text,
        reason="the summary reports what was done, not what was found",
    )


def fallback_can_help(goal: str) -> bool:
    """Whether a search can honestly add anything after a failed live check.

    A live yes/no about one thing at one time is not a question a snippet
    can answer, and reaching for one would turn "I could not confirm it"
    into an answer that sounds verified and is not. A goal that also asks
    something researchable -- a rate, a phone number, which places exist --
    keeps a real partial answer available.
    """
    return bool(_SEARCHABLE_COMPONENT.search(str(goal or "")))


# Said when the browser genuinely ran and still could not settle the
# question. It must report the shape of the failure ("I got to the page,
# it didn't answer") rather than a generic apology, because the user's
# next move depends on which half worked.
_UNVERIFIED_LINES = (
    "I got to the page, but it never gave me a clear answer on that.",
    "The site opened fine -- I just couldn't get that off the page.",
    "I had the page up, but couldn't read a straight answer out of it.",
)

# Not a spoken line -- a constraint that rides into the fallback search's
# own evidence. A snippet cannot establish that a room is free on the 18th,
# so once the live check has already failed, the answer written from those
# snippets must not come back sounding like the check succeeded.
_FALLBACK_NOTICE = (
    "LIVE CHECK FAILED: A direct check of the site could not confirm this. "
    "Do not state or imply that current availability, stock, or a live "
    "price has been confirmed. Say plainly that it could not be confirmed, "
    "then give only what the sources below actually support."
)


def unverified_line(goal: str = "") -> str:
    """An honest report that the check ran and came back empty.

    Rotated by the goal so two different questions do not come back with
    the same sentence, and so the same question is stable under test.
    """
    # crc32, not hash(): str.__hash__ is salted per process, so the same
    # question would have come back with a different sentence on every
    # restart and no test could pin it.
    key = " ".join(str(goal or "").lower().split()).encode("utf-8")
    return _UNVERIFIED_LINES[zlib.crc32(key) % len(_UNVERIFIED_LINES)]


def fallback_notice() -> str:
    """The constraint that keeps a fallback answer from sounding verified."""
    return _FALLBACK_NOTICE


# ------------------------------------------------- what a text read proves
#
# Measured live. She was asked to search for packing peanuts, click images,
# and show them. Every step worked -- navigated, clicked, observed -- and
# the answer was:
#
#     "The page is empty except for the Google search bar and navigation
#      links. No image results are visible. Please try refreshing the page
#      or checking your internet connection."
#
#     User: No, I can see the images. Thank you.
#
# The last observation was read_page_text. Google Images is nearly
# textless, so it came back with navigation chrome and little else, and an
# empty *text* read was reported as an empty *page*. Images are not text.
# Their absence from a text read is not evidence of anything, and the
# advice that followed it -- refresh, check your connection -- was invented
# on top of a false premise.
#
# The steps she took are the part she actually knows about, so that is what
# she reports. This is not a claim that the images are there; it is the
# absence of a claim that they are not.

_ASKS_TO_SEE = re.compile(
    r"\b(?:image|images|picture|pictures|photo|photos|pic|pics|"
    r"screenshot|screenshots|thumbnail|thumbnails|video|videos|"
    r"chart|charts|graph|graphs|map|maps|diagram|logo|"
    r"what\s+it\s+looks\s+like|show\s+me\s+what)\b"
    r"|사진|이미지|그림",
    re.IGNORECASE,
)

_DENIES_VISUAL_CONTENT = re.compile(
    r"\b(?:page|it|there)\s+(?:is|are|was|were|seems?|appears?)\s+"
    r"(?:completely\s+|entirely\s+|mostly\s+|basically\s+)?empty\b"
    r"|\bno\s+(?:image|images|picture|pictures|photo|photos|video|videos|"
    r"result|results|content|thumbnails?)\b"
    r"|\b(?:nothing|no\s+content)\s+(?:is\s+)?(?:visible|shown|displayed|there)\b"
    r"|\b(?:image|images|picture|pictures|photo|photos|result|results)\b[^.]{0,30}"
    r"\b(?:are|is)\s+not\s+(?:visible|shown|displayed|loaded|there)\b"
    r"|\bcould\s?n[o']t\s+(?:see|find)\s+any\s+(?:image|picture|photo)",
    re.IGNORECASE,
)

_DID_THE_STEPS = (
    "The image results are up on the page for you.",
    "That's up on screen now -- the results are showing.",
    "Done, the results are on the page now.",
)


def asks_to_see(goal: str) -> bool:
    """Whether the request was for something a text read cannot report on."""
    return bool(_ASKS_TO_SEE.search(str(goal or "")))


def denies_visual_content(summary: str) -> bool:
    """Whether the summary claims there is nothing on the page."""
    return bool(_DENIES_VISUAL_CONTENT.search(str(summary or "")))


def correct_visual_claim(
    summary: str, *, goal: str, steps_succeeded: bool,
) -> str:
    """Replace a text read's verdict on pictures with what she actually did.

    Only when all three hold: the request was visual, the summary denies
    there is anything there, and the steps themselves worked. A run that
    genuinely failed still says so -- being unable to report a real failure
    would be a worse bug than this one.
    """
    text = str(summary or "").strip()
    if not steps_succeeded or not text:
        return text
    if not asks_to_see(goal) or not denies_visual_content(text):
        return text
    key = " ".join(str(goal or "").lower().split()).encode("utf-8")
    return _DID_THE_STEPS[zlib.crc32(key) % len(_DID_THE_STEPS)]

# The planner is instructed in the same channel it answers in, and it
# sometimes reads the instruction as part of the answer. Measured live,
# after the loop-breaking nudge that ends "say so plainly and stop":
#
#     "The page text does not contain the requested information. Stop."
#
# The wording of the nudge is fixed too, but a prompt is not a guard: a
# bare one-word imperative on the end of a report is an artifact whatever
# the instruction happened to say, and it is never something she means.
_LEAKED_INSTRUCTION = re.compile(
    rf"[.!?]\s+(?:stop|halt|done|end|finish|continue|proceed|"
    rf"report|answer|reply)[.!]?\s*$",
    re.IGNORECASE,
)


def without_leaked_instruction(summary: str) -> str:
    """Drop a trailing bare imperative the model echoed from its prompt."""
    text = " ".join(str(summary or "").split())
    match = _LEAKED_INSTRUCTION.search(text)
    if match is None:
        return text
    return text[:match.start() + 1].strip()
