"""Stop Elaina quoting a price she never actually looked up.

Found live, in a conversation-routed turn with no tool call in it at all:

    User:   for real? that seems cheap
    Elaina: "Trip.com shows prices starting at around 120,000 KRW for
             Harbour Plaza Hotels."

Nothing was read. No search ran. The number, the currency, and the source
attribution were all generated. That is worse than an unhelpful answer,
because it is indistinguishable from a real one.

The guard is deliberately narrow, because most numbers in conversation are
perfectly fine to state from general knowledge ("a coffee is about 5,000
won in Seoul"). It only fires when all of these hold:

* no capability ran this turn -- so there is no fresh evidence behind it;
* the conversation already has a grounded subject (the user is following
  up on something Elaina really did look up);
* the reply states a **money amount** that appears nowhere in that grounded
  evidence, nor in what the user themselves said.

That combination is specifically "inventing a figure about the thing we
were just discussing", which is the failure this exists for. A number the
user supplied, or one that came back from a real search, always passes.
"""

from __future__ import annotations

import re

# Money only. A plain integer ("three hotels", "2026") is not a claim about
# a live value and must not be second-guessed.
_MONEY = re.compile(
    r"[$₩€£¥]\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s*"
    r"(?:won|krw|usd|eur|gbp|jpy|dollars?|euros?|pounds?|yen|원)\b",
    flags=re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _digits(text: str) -> set[str]:
    """Bare digit strings, so ₩120,000 and "120000 won" compare equal."""
    return {
        re.sub(r"\D", "", match.group(0))
        for match in _MONEY.finditer(str(text or ""))
        if re.sub(r"\D", "", match.group(0))
    }


class GroundedValueGuard:
    """Tell a looked-up figure from an invented one."""

    @classmethod
    def unsupported_amounts(cls, reply: str, evidence: str) -> set[str]:
        """Money in the reply that the evidence does not contain."""
        return _digits(reply) - _digits(evidence)

    @classmethod
    def needs_correction(
        cls,
        reply: str,
        *,
        evidence: str,
        action_performed: bool,
    ) -> bool:
        if action_performed:
            return False
        if not str(evidence or "").strip():
            # No grounded subject means this is ordinary conversation, not
            # a follow-up about something Elaina looked up.
            return False
        return bool(cls.unsupported_amounts(reply, evidence))

    @classmethod
    def correct(cls, reply: str, *, evidence: str, offer: str) -> str:
        """Drop the sentences carrying invented figures, then offer to check.

        Sentences without a money claim are kept exactly as written -- the
        rest of the answer may be perfectly good.
        """
        unsupported = cls.unsupported_amounts(reply, evidence)
        if not unsupported:
            return reply
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(str(reply).strip())
            if sentence.strip() and not (_digits(sentence) & unsupported)
        ]
        offer = str(offer or "").strip()
        rebuilt = " ".join(kept).strip()
        if rebuilt and offer:
            return f"{rebuilt} {offer}"
        return rebuilt or offer or reply
