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
"""

from __future__ import annotations

import re

# Verbs that describe Elaina doing real work with a capability, as opposed
# to conversational verbs ("tell", "say", "explain") which are satisfied by
# the reply itself and are therefore not broken promises.
_ACTION_VERB = (
    r"(?:open|check|search|look(?:\s+up|\s+into|\s+at)?|find|browse|pull\s+up|"
    r"bring\s+up|go\s+to|visit|navigate|compare|verify|confirm|fetch|grab|"
    r"get\s+you|see\s+what|take\s+a\s+look|dig\s+into|run|start|launch)"
)

_PROMISE = re.compile(
    r"\b(?:"
    r"let\s+me\s+" + _ACTION_VERB + r"|"
    r"i(?:'ll|\s+will|\s+am\s+going\s+to|'m\s+going\s+to|\s+can\s+go)\s+"
    + _ACTION_VERB + r"|"
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
_OFFER = re.compile(
    r"\bwant\s+me\s+to\b|\bwould\s+you\s+like\s+me\s+to\b|\bshould\s+i\b"
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
