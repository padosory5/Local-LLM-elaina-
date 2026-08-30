from __future__ import annotations

import re
from difflib import SequenceMatcher


class ResponseQualityGuard:
    """Detect a draft that simply repeats an unrelated previous answer.

    The name was right and the implementation was not: it required the reply
    to be a *greeting* before anything else was checked, so it only ever
    caught "Hey! What's up?" twice in a row. A dinner recommendation
    repeated byte-for-byte after "that sounds good" sailed through, three
    separate times across two phases.

    What matters is the relationship between the two turns, not what the
    reply happens to be about: the same answer to a meaningfully different
    question is a failure, whatever its subject.
    """

    # A courtesy answer to a thank-you, carried forward to turns that were
    # not thank-yous. Measured live: "thanks" -> "You're welcome.", and then
    # the next three turns all opened with it, including "no thanks" and "I
    # watched a good film last night".
    #
    # Similarity cannot see this. "no thanks" is 0.80 similar to "thanks",
    # so the guard read it as the person repeating themselves; and "You're
    # welcome. Enjoy the film!" is only 0.50 similar to "You're welcome.",
    # so it was not a repeat either. The relationship that matters is
    # between the *reply's opening* and *this* turn, not between two turns.
    _COURTESY_OPENER = re.compile(
        r"^(?:you'?re welcome|you are welcome|no problem|no worries|"
        r"happy to help|glad (?:i could help|to help)|anytime|my pleasure|"
        r"천만에|별말씀)",
        re.IGNORECASE,
    )
    _THANKING = re.compile(
        r"\b(?:thanks|thank you|thx|appreciate (?:it|that)|"
        r"고마워|감사)\b",
        re.IGNORECASE,
    )
    _OPENS_NEGATIVE = re.compile(r"^\s*(?:no|nah|nope)\b", re.IGNORECASE)

    # Asking for anything is a reason to answer, and answering the same
    # question twice may legitimately produce the same words: "Yeah, tell me
    # when" after "When was OpenAI founded?" wants that exact date again.
    _REQUESTS_SOMETHING = re.compile(
        r"\b(?:what|when|where|which|who|whom|why|how|whose)\b"
        r"|\b(?:tell|give|show|explain|list|name|remind|describe|send)\b"
        r"|\b(?:can|could|would|will) you\b"
        r"|\?$",
        re.IGNORECASE,
    )

    # Purely a reaction to what she just said. Nothing is being asked, so the
    # same answer a second time is never the right reply -- this is the shape
    # every reported case took: "that sounds good", "no thanks", "okay".
    _IS_REACTION = re.compile(
        r"^(?:(?:yeah|yes|yep|ok|okay|sure|alright|right|nice|cool|"
        r"great|thanks|thank you|no|nah|nope|hmm|oh|wow|i see|got it|"
        r"maybe|perhaps|fine|good)[\s,.!]*)+$"
        r"|\b(?:sounds?|looks?|seems?)\s+(?:good|nice|great|fine|cool|"
        r"tasty|lovely|interesting)"
        r"|\bthat'?s\s+(?:cool|nice|good|great|fine|interesting)"
        r"|\bi (?:like|love) (?:that|it|this)"
        r"|\bno,? thanks\b|\bnot (?:right )?now\b"
        r"|^좋(?:네|다|아)|^괜찮|^고마워|^알았어",
        re.IGNORECASE,
    )

    # Asking for it again is a reason to repeat, not a reason to retry.
    _ASKS_TO_REPEAT = re.compile(
        r"\b(?:say|tell|repeat|read|go over)(?:\s+(?:it|that|them|this|me|us|those))*\s+again\b"
        r"|\bagain,? please\b|\bone more time\b|\bwhat did you say\b"
        r"|\brepeat (?:that|it)\b"
        r"|다시 말해|한번 더",
        re.IGNORECASE,
    )

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(
            re.sub(r"[^\w\s]", " ", str(text).lower()).split()
        )

    @classmethod
    def _is_thanking(cls, text: str) -> bool:
        """Whether this turn actually thanks her.

        "no thanks" contains the word and means the opposite, which is why a
        bare keyword test read a refusal as gratitude.
        """
        said = str(text or "").strip()
        if cls._OPENS_NEGATIVE.match(said):
            return False
        return bool(cls._THANKING.search(said))

    @classmethod
    def should_retry(
        cls,
        reply: str,
        current_user: str,
        history: list[dict[str, str]],
    ) -> bool:
        if not reply.strip() or len(history) < 2:
            return False

        previous_user = next(
            (
                item.get("content", "")
                for item in reversed(history)
                if item.get("role") == "user"
            ),
            "",
        )
        previous_assistant = next(
            (
                item.get("content", "")
                for item in reversed(history)
                if item.get("role") == "assistant"
            ),
            "",
        )
        if not previous_user or not previous_assistant:
            return False

        reply_similarity = SequenceMatcher(
            None,
            cls._normalize(reply),
            cls._normalize(previous_assistant),
        ).ratio()
        user_similarity = SequenceMatcher(
            None,
            cls._normalize(current_user),
            cls._normalize(previous_user),
        ).ratio()

        # Repeating an answer is fine when the user actually repeated the
        # same message, when they asked to hear it again, and when they asked
        # anything at all -- the same question can deserve the same words.
        #
        # It is a failure in one shape: the person only *reacted* to what she
        # said, and she said it again. Deliberately narrower than "the reply
        # is identical and the turn is different", because the wider rule
        # rejected "Yeah, tell me when" being answered with the same date.
        said = str(current_user)

        # Checked first, and without reference to history: a stale courtesy
        # opener is wrong even on a turn where repeating would be fine.
        if cls._COURTESY_OPENER.match(str(reply).strip()) and not cls._is_thanking(said):
            return True

        if cls._ASKS_TO_REPEAT.search(said):
            return False
        if reply_similarity < 0.90 or user_similarity >= 0.72:
            return False

        # Two shapes, not one. The original: a stale greeting handed to a
        # substantive new message. The one that kept getting through: the
        # person only reacted to what she said, and she said it again.
        stale_greeting = bool(re.search(
            r"^(?:hey|hi|hello|morning|good morning|good evening)\b.*"
            r"(?:what s up|how are you|what s going on|what s on your mind)",
            cls._normalize(reply),
        ))
        if stale_greeting:
            return True

        reacted_only = bool(
            cls._IS_REACTION.search(said.strip())
            and not cls._REQUESTS_SOMETHING.search(said)
        )
        return reacted_only
