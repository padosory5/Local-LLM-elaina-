"""Stop Elaina from promising an action she never performs.

Found live, and the single worst thing in the transcript that prompted this
module:

    User:    ...
    Elaina:  "I can check prices directly through the browser. Let me open
              the website and find the current rates for you."
    User:    "ok"
    Elaina:  "I can check prices directly through the browser. Let me open
              the website and find the current rates for you."

Nothing opened. Nothing was checked. The turn was routed as ``conversation``,
so no capability ever ran -- the sentence was pure text generation. Then the
user's "ok" produced the identical sentence again, because there was no
pending offer for that "ok" to resolve against.

Two distinct defects, so two distinct guards here:

``promises_action``
    A reply that says Elaina is *about to* do something is a contract. If
    the turn performed no action, the contract is already broken by the
    time the words are spoken. Detecting it lets ``ChatEngine`` either
    escalate the turn into the real capability (preferred) or strip the
    promise before speaking.

``offers_action``
    A reply that *asks* whether to do something is fine -- but it must
    leave a pending offer behind, or the user's "yes" has nothing to
    attach to. Detecting it lets ChatEngine park a real consent offer.

Both are deterministic regexes rather than prompt instructions, per this
project's standing rule: qwen3:8b was already told, in the response policy,
"Do not say that you will calculate, explain, check, or break something
down later when you can do it now" -- and said it anyway.

Phase 4F.1 added the second half of the module, below the guard: the
*record* those regexes are checked against. Reading a sentence can only ever
say what it claims; whether the claim holds is a question about the world,
and :class:`ActionLedger` is where the world is written down. That is what
finally makes an honest offer sayable -- for a year the only safe answer to
"I can pull up a few options if you want" was to delete it, because nothing
existed that could say whether it was true.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Verbs that describe Elaina doing real work with a capability, as opposed
# to conversational verbs ("tell", "say", "explain") which are satisfied by
# the reply itself and are therefore not broken promises.
_ACTION_VERB = (
    # "pull it up" and "pull that up" are the same promise as "pull up",
    # and slipped past a pattern that required the particle to follow the
    # verb immediately -- so "Let me pull that up" was classified as a plain
    # answer and never checked against anything.
    r"(?:open|check|search|look(?:\s+up|\s+into|\s+at)?|find|browse|"
    r"pull(?:\s+(?:it|that|this|them|those))?\s+up|"
    r"bring(?:\s+(?:it|that|this|them|those))?\s+up|"
    r"go\s+to|visit|navigate|compare|verify|confirm|fetch|grab|"
    r"get\s+you|see\s+what|take\s+a\s+look|dig\s+into|run|start|launch|"
    # Repairing something is an action too, and it was the shape session 9
    # used: "Let me correct that." and "Let me fix that and open Naver.com
    # for you." -- neither followed by anything opening.
    r"fix|correct|retry|redo|try(?:\s+again)?|sort\s+(?:that|it)\s+out)"
)

# One clause may sit between the promise and its verb. "Let me fix that
# and open Naver.com" is a promise to open Naver.com; requiring the verb
# to follow "let me" immediately missed it.
_THEN = r"(?:[\w',]{1,20}(?:\s+[\w',]{1,20}){0,3}\s+(?:and|then)\s+)?"

_PROMISE = re.compile(
    r"\b(?:"
    r"let\s+me\s+" + _THEN + _ACTION_VERB + r"|"
    r"i(?:'ll|\s+will|\s+am\s+going\s+to|'m\s+going\s+to|\s+can\s+go)\s+"
    + _THEN + _ACTION_VERB + r"|"
    r"i'?m\s+(?:now\s+)?(?:going\s+to\s+)?(?:opening|checking|searching|"
    r"looking|browsing|pulling|fetching)|"
    r"(?:give\s+me|just)\s+a\s+(?:moment|second|sec|minute)|"
    r"hold\s+on\s+while\s+i|"
    r"one\s+moment(?:\s+please)?|bear\s+with\s+me|"
    r"stand\s+by\s+while\s+i"
    r")\b"
    r"|제가\s*(?:한번\s*)?(?:확인|검색|찾아|열어)\s*(?:해\s*)?(?:볼게|드릴게|보겠)",
    flags=re.IGNORECASE,
)

# "Want me to?" / "Should I?" -- an offer, not a promise. Legitimate, but
# it has to leave a resolvable pending offer behind.
#
# ``ClosingOfferGuard._OFFER`` in brain/response_policy.py answers the
# neighbouring question -- "is this trailing sentence strippable filler?" --
# and is deliberately wider, because it also covers generic closers that
# offer nothing in particular. This one stays narrow: everything it matches
# becomes an offer that a real action is parked behind.
#
# "Would you like help finding specific models?" is the same offer without
# the "me to", and slipped past a pattern that required it. Measured live:
# a reply carried that *and* a second, real offer from the entity guard,
# and said both.
_OFFER = re.compile(
    r"\bwant\s+me\s+to\b|\bshould\s+i\b"
    r"|\bwould\s+you\s+like\s+(?:me\s+to|help|assistance|a\s+hand)\b"
    r"|\bwant\s+(?:help|a\s+hand)\s+(?:with|finding|looking)\b"
    # "I can help you check out some local shops." Offering to help do a
    # thing is offering to do it, and this pattern did not say so, while
    # ClosingOfferGuard's did -- so the sentence was too weak to ground and
    # strong enough to strip. Measured live, twice in two turns.
    r"|\bi\s+(?:can|could)\s+help\s+(?:you\s+)?[a-z]"
    r"|\blet\s+me\s+know\s+if\s+you(?:[\u2019']d|\s+would)?\s*"
    r"(?:want|need|like)\s+help\s+[a-z]"
    r"|\bshall\s+i\b|\bdo\s+you\s+want\s+me\s+to\b"
    r"|\b(?:i\s+can|i\s+could)\b[^.?!]{0,80}\?"
    r"|해\s*드릴까요|할까요",
    flags=re.IGNORECASE,
)

# Softer than _PROMISE: "I can check Trip.com prices for you" claims no
# action is underway, so it is not a broken promise -- but it does name the
# action, which is what a parked offer needs to carry forward.
_STATED_INTENT = re.compile(
    r"\bi\s+(?:can|could)\s+" + _ACTION_VERB + r"\b",
    flags=re.IGNORECASE,
)

# The promise sentence is removed rather than the whole reply, so whatever
# real content came with it survives.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class ActionCommitmentGuard:
    """Tell a promise, an offer, and a plain answer apart."""

    @classmethod
    def promises_action(cls, text: str) -> bool:
        """Whether this reply claims an action is underway or imminent."""
        return bool(_PROMISE.search(str(text or "")))

    @classmethod
    def offers_action(cls, text: str) -> bool:
        """Whether this reply asks permission to do something."""
        return bool(_OFFER.search(str(text or "")))

    @classmethod
    def broken_promise(cls, text: str, *, action_performed: bool) -> bool:
        """A promise with no action behind it in the same turn."""
        if action_performed:
            return False
        return cls.promises_action(text)

    @classmethod
    def promised_action(cls, text: str) -> str:
        """The sentence that states what Elaina said she would do.

        When the user's own turn is vague ("for real? that seems cheap")
        the promise is the only place the actual goal is written down, so
        it is what a parked offer has to carry forward.

        Falls back to a stated intention ("I can check Trip.com prices for
        you") when no outright promise is present -- that sentence names
        the action just as usefully, even though it is too soft to be a
        broken promise on its own.
        """
        sentences = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(str(text or "").strip())
            if sentence.strip()
        ]
        for sentence in sentences:
            if _PROMISE.search(sentence):
                return sentence
        for sentence in sentences:
            if _STATED_INTENT.search(sentence):
                return sentence
        return ""

    @classmethod
    def strip_promise(cls, text: str, *, replacement: str = "") -> str:
        """Drop promise sentences, keeping every sentence that stands alone.

        When nothing survives, ``replacement`` is used so the user still
        gets a real answer instead of silence.
        """
        original = str(text or "").strip()
        if not original:
            return replacement
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(original)
            if sentence.strip() and not _PROMISE.search(sentence)
        ]
        rebuilt = " ".join(kept).strip()
        return rebuilt or replacement or original

    @classmethod
    def rewrite_promise_as_offer(cls, text: str, offer: str) -> str:
        """Turn "let me check" into a real, answerable question.

        Used when the capability exists but needs the user's go-ahead: the
        promise becomes an offer that ``ChatEngine`` parks as pending
        consent, so the next "ok" resolves to a real action.
        """
        kept = cls.strip_promise(text)
        offer = str(offer or "").strip()
        if not offer:
            return kept
        if not kept or cls.promises_action(kept):
            return offer
        return f"{kept} {offer}".strip()


# --------------------------------------------------------- conversation acts
#
# Phase 4F.1. The regexes above tell a promise from an offer by reading the
# words, which is the wrong way round for the one question that matters:
# *is it true?* A sentence cannot answer that about itself.
#
# So the words are no longer the record. What follows is the record -- what
# Elaina is actually doing, or actually waiting on -- and the sentence is
# checked against it. "I'll check" is allowed when something is queued or
# running, and only then; "want me to check?" is allowed when there is a real
# parked offer for the user's yes to land on, and only then. Neither phrasing
# is banned any more, which is the behaviour this phase restores: the old
# guard could only delete, so every honest offer died with the dishonest ones.

# What a sentence claims about the near future.
ANSWER = "answer"          # it claims nothing; it just answers
OFFER = "offer"            # "want me to?" -- waiting on the user
COMMITMENT = "commitment"  # "I'll check" -- claims the work is under way

SPEECH_ACTS = (ANSWER, OFFER, COMMITMENT)

# What is actually true about the action behind it.
IDLE = "idle"                    # there is no action
AWAITING_USER = "awaiting_user"  # offered, and the user has not answered
QUEUED = "queued"                # decided on, not yet started
DISPATCHING = "dispatching"      # handed to the capability, running now
EXECUTED = "executed"            # the dispatch returned -- see settled()
FAILED = "failed"
CANCELLED = "cancelled"

ACTION_STATES = (
    IDLE, AWAITING_USER, QUEUED, DISPATCHING, EXECUTED, FAILED, CANCELLED,
)

# The states in which saying "I'll do it" is true. EXECUTED is included
# because this pipeline composes the reply *after* the capability returns:
# by the time the sentence exists the work is genuinely done, so the claim is
# at worst clumsy about tense and never false. IDLE, CANCELLED and FAILED are
# not here, and those are the three that produced the live failure this
# module was written for.
_BACKS_A_COMMITMENT = frozenset({QUEUED, DISPATCHING, EXECUTED})


# Two shapes of work, told apart because promising one while doing the other
# is the mismatch that was measured live: a web search ran, and the reply
# said "let me open the website and find the current rates for you". Nothing
# opened. Anything this cannot classify returns "" and the check passes --
# guessing a family and being wrong would delete honest sentences.
OPEN = "open"   # something appears on screen: a page, a window, an app
LOOK = "look"   # something is read: a search, a lookup, a check

_OPENS = re.compile(
    r"\b(?:open|pull\s+up|bring\s+up|go\s+to|visit|navigate|launch)\b",
    flags=re.IGNORECASE,
)
_LOOKS = re.compile(
    r"\b(?:search|look\s+up|look\s+into|find|browse|google|check|"
    r"see\s+what|dig\s+into|read)\b",
    flags=re.IGNORECASE,
)

# The capability or router label that ran, mapped to the shape of work it is.
# Absent means "cannot tell", which is deliberately most of them: the task
# planner, a screen analysis and an agent hand-off can each be either.
_FAMILY_BY_ACTION = {
    "web_search": LOOK,
    "fact_check": LOOK,
    "research": LOOK,
    "entity_correction": LOOK,
    "browser_control": OPEN,
    "browser_action": OPEN,
    "browser_tab": OPEN,
    "computer_action": OPEN,
    "ui_control": OPEN,
    "media_action": OPEN,
}


def action_family(text: str) -> str:
    """Which shape of work a sentence says is happening, or "" if unclear."""
    said = str(text or "")
    if _OPENS.search(said):
        return OPEN
    if _LOOKS.search(said):
        return LOOK
    return ""


def speech_act_of(sentence: str) -> str:
    """What one sentence claims: a commitment, an offer, or neither.

    Order matters. A commitment is the stronger claim and is tested first,
    so "I'll check -- want me to?" is held to the commitment's standard
    rather than the offer's.
    """
    said = str(sentence or "")
    if not said.strip():
        return ANSWER
    if _PROMISE.search(said):
        return COMMITMENT
    if _OFFER.search(said) or _STATED_INTENT.search(said):
        return OFFER
    return ANSWER


def offered_action(text: str) -> str:
    """The sentence that offers to do something, or "".

    The mirror of :meth:`ActionCommitmentGuard.promised_action`, and used for
    the same reason: when the reply carries an offer, that sentence is the
    only place the proposed action is written down, so it is what a real
    parked offer has to carry forward.

    When a reply carries more than one, the *strongest* is the one to
    ground. A direct question is a call to action awaiting an answer; a
    passive invitation is not, and parking the invitation instead left the
    reply keeping the weaker sentence and deleting the real one:

        Yeah, a monitor upgrade could be worth it. Let me know if you'd
        like help finding something specific!

    -- with "Want me to pull up a few current ones?" removed as the
    duplicate. Questions win, and the last one is the call to action that
    closes the reply.
    """
    offers = [
        cleaned
        for cleaned in (
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(str(text or "").strip())
        )
        if cleaned and speech_act_of(cleaned) == OFFER
    ]
    if not offers:
        return ""
    questions = [offer for offer in offers if offer.rstrip().endswith("?")]
    return (questions or offers)[-1]


@dataclass(frozen=True)
class ConversationAct:
    """What Elaina is doing about this turn, in a shape a test can read."""

    speech_act: str = ANSWER
    proposed_action: str = ""
    action_state: str = IDLE
    goal: str = ""
    reason: str = ""

    @property
    def backs_a_commitment(self) -> bool:
        return self.action_state in _BACKS_A_COMMITMENT

    @property
    def backs_an_offer(self) -> bool:
        return self.action_state == AWAITING_USER

    def log_block(self) -> str:
        """The debugging view. Console only -- never the conversation UI."""
        return (
            "[Act]\n"
            f"  Speech act: {self.speech_act}\n"
            f"  Action: {self.proposed_action or '(none)'}\n"
            f"  State: {self.action_state}\n"
            f"  Goal: {self.goal or '(none)'}"
        )


class ActionLedger:
    """The turn's action state, and the only thing allowed to be right.

    One per session, reset at the start of every turn. Two facts live here
    and nowhere else:

    * what action this turn dispatched, and how far it got;
    * whether an offer is outstanding -- read straight from
      :class:`~security.capability_offer.CapabilityOfferGate` rather than
      copied, because a second copy of that fact is a second thing that can
      be wrong. The gate stays the one place a parked offer lives.

    Nothing here reads or writes the reply. The guards ask it questions.
    """

    def __init__(self, *, pending_offer=None) -> None:
        # A zero-argument callable returning the parked offer, or None.
        self._pending_offer = pending_offer
        self._action = ""
        self._goal = ""
        self._state = IDLE
        self._reason = ""

    # ------------------------------------------------------------- turns

    def begin_turn(self) -> None:
        """Forget the last turn's action. A parked offer outlives the turn."""
        self._action = ""
        self._goal = ""
        self._state = IDLE
        self._reason = ""

    # ----------------------------------------------------------- writing

    def queued(self, action: str, *, goal: str = "", reason: str = "") -> None:
        """Decided on, not yet handed over."""
        self._record(QUEUED, action, goal, reason)

    def dispatching(
        self, action: str, *, goal: str = "", reason: str = "",
    ) -> None:
        """Handed to the capability. It is running now."""
        self._record(DISPATCHING, action, goal, reason)

    def settled(self, *, succeeded: bool = True) -> None:
        """The dispatch returned.

        Deliberately not "the user got what they wanted": a search that
        returns nothing useful still settles here. Confusing the two is
        how a failure gets reported as a success.
        """
        if self._state in {QUEUED, DISPATCHING}:
            self._state = EXECUTED if succeeded else FAILED

    def failed(self, reason: str = "") -> None:
        self._state = FAILED
        if reason:
            self._reason = str(reason)

    def cancelled(self, reason: str = "") -> None:
        """Called off. No sentence may claim this action is coming."""
        self._state = CANCELLED
        if reason:
            self._reason = str(reason)

    def _record(self, state: str, action: str, goal: str, reason: str) -> None:
        self._state = state
        self._action = str(action or "").strip()
        self._goal = " ".join(str(goal or "").split())
        self._reason = str(reason or "")

    # ----------------------------------------------------------- reading

    @property
    def action(self) -> str:
        return self._action

    @property
    def state(self) -> str:
        return self._state

    @property
    def goal(self) -> str:
        return self._goal

    @property
    def offer_pending(self) -> bool:
        """Whether a real offer is waiting for the user to answer it."""
        return self._peek() is not None

    def _peek(self):
        if self._pending_offer is None:
            return None
        try:
            return self._pending_offer()
        except Exception:
            return None

    @property
    def act(self) -> ConversationAct:
        """The turn's act, with a running action outranking a parked offer.

        If she is doing it, she is not asking about it -- so a dispatched
        action wins even when an older offer is still sitting in the gate.
        """
        if self._state in _BACKS_A_COMMITMENT:
            return ConversationAct(
                speech_act=COMMITMENT,
                proposed_action=self._action,
                action_state=self._state,
                goal=self._goal,
                reason=self._reason,
            )
        offer = self._peek()
        if offer is not None:
            return ConversationAct(
                speech_act=OFFER,
                proposed_action=str(getattr(offer, "capability_id", "")),
                action_state=AWAITING_USER,
                goal=str(getattr(offer, "goal", "")),
                reason="an offer is waiting for the user to answer it",
            )
        return ConversationAct(
            speech_act=ANSWER,
            proposed_action=self._action,
            action_state=self._state,
            goal=self._goal,
            reason=self._reason,
        )

    def supports_commitment(self, promised: str = "") -> bool:
        """Whether "I'll do it" is true right now.

        ``promised`` is the sentence making the claim. When both it and the
        dispatched action name a shape of work, and they disagree in the one
        direction that was measured live -- promising to *open* something
        while only a lookup ran -- the claim is refused. Every other
        combination passes: an unclassifiable promise is not evidence of a
        lie, and deleting it would cost an honest sentence.
        """
        if self._state not in _BACKS_A_COMMITMENT:
            return False
        wanted = action_family(promised)
        ran = _FAMILY_BY_ACTION.get(self._action, "")
        if not wanted or not ran:
            return True
        return not (wanted == OPEN and ran == LOOK)

    def supports_offer(self) -> bool:
        """Whether "want me to?" has something for a yes to land on."""
        return self.offer_pending

    def log_block(self) -> str:
        return self.act.log_block()
