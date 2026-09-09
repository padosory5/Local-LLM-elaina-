"""Whether this turn replaces what was already outstanding.

The one question five different layers had each been answering on their
own. Over five rounds of live testing, every one of them turned out to be
answering it slightly differently:

* ``recommendation.names_its_own_errand`` -- a turn that specifies its own
  errand is not a "yes" to an older offer;
* ``recommendation_state.revises`` -- "actually, something else" retires
  what was asked for;
* ``recommendation_state.about_the_same_thing`` -- naming a different thing
  starts a different problem;
* ``conversation_focus.read_correction`` -- "I was talking about X" says
  what the subject really is;
* ``chat_engine``'s cancellation pattern -- "never mind" ends it.

Each is right about its own corner and each is consulted from a different
place, so "does the current turn beat the pending one" had no single
answer to test, log, or reason about. Measured live, the cost was a
conversation that restarted four times in seven turns, and a search that
went out for the word "good".

This does not replace any of them -- they hold the knowledge and they are
what the guards still call. It reads them, once, and states the
conclusion, so the interaction decision can carry it and a test can pin
it. Dev rule 5 of the phase brief, as a value rather than a habit:

    User current-turn intent must always beat stale state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Calling it off. Read before anything else, because "actually never mind"
# satisfies the correction patterns too -- and taking "never mind" as the
# corrected *subject* is how a cancellation became a thing to search for.
_CALLS_IT_OFF = re.compile(
    r"^\s*(?:(?:no|nah|actually|wait)[,! ]+)?"
    r"(?:never ?mind|forget\s+(?:about\s+)?(?:it|that)|cancel(?: that| it)?|"
    r"stop(?: that| it)?|drop it|leave it|don't bother|no need|"
    r"\uc7a1\uc18c|\ub410\uc5b4(?:\uc694)?|\uadf8\ub9cc)"
    r"\s*[.!?]*\s*$",
    flags=re.IGNORECASE,
)


# The same instruction, but naming what is being dropped and then asking
# something else: "actually forget the mouse, what's a good film tonight?"
#
# ``_CALLS_IT_OFF`` requires the whole turn to *be* the cancellation, so a
# named subject followed by a new question matched nothing here, and the
# clean start depended entirely on the model setting ``topic_shift``.
# Measured by re-running A3's own case four times: it passed three and
# failed once, with the abandoned recommendation leaking into the answer --
#
#     actually forget the mouse, what's a good film for tonight?
#     Enjoy the ride! The one I actually found is Best Wireless Gaming
#     Mouse under $50.
#
# A model label that is right two times in three is not a gate. The person
# said which subject to drop; that is deterministic and it is theirs.
_DROPS_A_NAMED_SUBJECT = re.compile(
    r"^\s*(?:(?:ok|okay|no|nah|actually|wait|anyway|right)[,! ]+)*"
    r"(?:never ?mind|forget(?:\s+about)?|drop|leave)\s+"
    r"(?:the|that|this|those|these|my|our)?\s*"
    r"([\w][\w \-']{0,40}?)\s*[,;.]\s*(?=\S)"
    r"|(?:은|는|말고|말구)\s*(?:됐(?:어|다|습니다)|그만)\s*[,.]?\s*(?=\S)",
    flags=re.IGNORECASE,
)


def drops_a_named_subject(text: str) -> str:
    """The subject this turn explicitly abandons, or "".

    Only when something else follows it -- "forget the mouse." on its own
    is a cancellation and ``_CALLS_IT_OFF`` already owns that. What this
    catches is the shape where the person drops one thing and asks about
    another in the same breath, which is how people actually change
    subject and which left the old subject inheritable.
    """
    found = _DROPS_A_NAMED_SUBJECT.search(str(text or ""))
    if not found:
        return ""
    named = (found.group(1) or "").strip()
    # A pronoun is not a named subject; that is the cancellation above.
    if named.casefold() in {
        "it", "that", "this", "them", "those", "these", "one", "thing",
    }:
        return ""
    return named


# Why a turn supersedes what was pending. Named rather than boolean so a
# log says which of the five reasons fired, which is the thing that was
# impossible to see while they were scattered.
ERRAND = "names its own errand"
CORRECTION = "corrects what she was working on"
REVISION = "revises what was asked for"
CANCELLED = "calls it off"
DROPPED = "drops a named subject and asks something else"

# Deliberately absent: "this turn is a question, so it is not an answer to
# the pending one". That is true and it is a different fact -- it decides
# whether a gate may *consume* the turn, which ``deliberation.pending``
# already owns. Reading it as supersession made "Anything cheaper?"
# replace the very result set it was asking about, which is the opposite
# of what the brief asks for.


@dataclass(frozen=True)
class Supersession:
    """What this turn does to whatever was already outstanding."""

    supersedes: bool = False
    reason: str = ""
    # What the person says the subject really is, when they said so. Empty
    # unless the turn carried an explicit correction.
    corrected_subject: str = ""

    def __bool__(self) -> bool:
        return self.supersedes

    def log_line(self) -> str:
        if not self.supersedes:
            return ""
        subject = (
            f" -> {self.corrected_subject!r}" if self.corrected_subject else ""
        )
        return f"[Supersedes] the current turn {self.reason}{subject}."


def read(text: str) -> Supersession:
    """What this turn does to what was pending, and why.

    Ordered by how explicit the signal is. A correction that names the
    subject outright is the strongest thing a person can say about what
    they meant, so it is read first and its subject is carried out.

    Every predicate is imported rather than reimplemented. A second copy
    of "is this a new errand" is a second thing that can be wrong, and the
    existing ones are the ones with live failures behind them.
    """
    said = " ".join(str(text or "").split())
    if not said:
        return Supersession()

    try:
        from brain import conversation_focus
        from brain import recommendation_state
        from brain.recommendation import names_its_own_errand
    except Exception:
        return Supersession()

    if _CALLS_IT_OFF.match(said):
        # Nothing replaces it; it simply stops. Carrying a subject out of
        # here would make "never mind" the thing being worked on.
        return Supersession(True, CANCELLED)

    corrected = ""
    try:
        corrected = str(conversation_focus.read_correction(said) or "").strip()
    except Exception:
        corrected = ""
    if corrected:
        return Supersession(True, CORRECTION, corrected)

    try:
        if names_its_own_errand(said):
            return Supersession(True, ERRAND)
    except Exception:
        pass

    try:
        if recommendation_state.revises(said):
            return Supersession(True, REVISION)
    except Exception:
        pass

    return Supersession()
