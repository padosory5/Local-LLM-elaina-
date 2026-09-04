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
    _INVENTED_NO_THANKS = re.compile(r"\bno,?\s+thanks\b", re.IGNORECASE)

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
        r"|\bi (?:do not|don't) know\b|\bnot sure\b|\bno idea\b"
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

    _ANSWERS_WITH_UNCERTAINTY = re.compile(
        r"\bi (?:do not|don't) know\b|\bnot sure\b|\bno idea\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(
            re.sub(r"[^\w\s]", " ", str(text).lower()).split()
        )

    # Below this a text is too short to judge by containment: almost any
    # brief line is "contained in" a long previous answer somewhere, and a
    # one-word reply would read as a repeat of everything.
    _CONTAINMENT_MIN_CHARS = 25

    @classmethod
    def _repeats(cls, reply: str, previous: str) -> float:
        """How much of one answer is already the other, 0.0 to 1.0.

        A plain similarity ratio is the wrong measure when one answer is a
        *subset* of the other, because the extra words in the longer one
        count against the score. Measured live:

            "I'm moving to seattle" -> "That's exciting! Seattle's a great
                                        place to start something new."
            "not yet"               -> "Cool, Seattle's a great place to
                                        move!"
            "I see"                 -> "Seattle's a great place to start
                                        something new."

        The third answer is the first one with its opening clause removed,
        and scored 0.85 -- under the 0.90 gate, so it went out. As a share
        of the shorter text it is 1.0: every word of it had been said.
        """
        first = cls._normalize(reply)
        second = cls._normalize(previous)
        if not first or not second:
            return 0.0
        matcher = SequenceMatcher(None, first, second)
        ratio = matcher.ratio()
        shorter = min(len(first), len(second))
        if shorter < cls._CONTAINMENT_MIN_CHARS:
            return ratio
        matched = sum(block.size for block in matcher.get_matching_blocks())
        return max(ratio, matched / shorter)

    # A single ASCII hyphen was missing here, and it is the one the model
    # actually types. Measured live: "I see" -> "I see- Have a wonderful
    # day!" walked straight through, because only em/en dashes and "--"
    # were recognised. A lone hyphen only counts as a break when it has
    # whitespace on at least one side, so "well-known" and "9/13-15" are
    # still ordinary words rather than echo boundaries.
    _ECHO_DASH = re.compile(r"\s*(?:—|–|--+)\s*|\s+-\s*|\s*-\s+")
    _ECHO_SENTENCE = re.compile(r"(?<=[.!?])\s+")
    # The same restatement with a comma instead of a dash: "I see, have a
    # wonderful day." Only ever applied under the whole-message rule below,
    # because a comma is far too common to strip on a partial match.
    _ECHO_COMMA = re.compile(r"\s*,\s*")
    _ECHO_STOPWORDS = frozenset({
        "a", "also", "am", "are", "at", "be", "d", "for", "how", "i", "im",
        "in", "is", "it", "me", "my", "of", "on", "the", "to", "with",
        "ll", "m", "re", "s", "ve", "you", "your", "youre",
    })

    # Words that carry no content of their own in an opener. An echo often
    # hides behind one. Measured live, answering "not yet":
    #
    #   "Ah, got it. Not yet -- that's totally normal! ..."
    #   "Oh, got it. Not yet -- that's totally fine. ..."
    #
    # The restatement is there, one acknowledgement away from the start, so
    # a prefix made only of these plus the person's own words is still a
    # restatement. At least one real echoed word is required before any of
    # it is removed, so a plain "Got it -- here's the plan" keeps its opener.
    _ECHO_FILLER = frozenset({
        "ah", "oh", "okay", "ok", "yeah", "yep", "yes", "sure", "alright",
        "right", "well", "hmm", "got", "gotcha", "understood", "noted",
        "totally", "absolutely", "no", "worries", "problem", "course",
        "makes", "sense", "fair", "enough", "good", "great", "nice",
    })

    @classmethod
    def _content_words(cls, text: str) -> set[str]:
        return {
            word for word in cls._normalize(text).split()
            if word not in cls._ECHO_STOPWORDS
        }

    @classmethod
    def strip_current_turn_echo(cls, reply: str, current_user: str) -> str:
        """Remove a leading restatement while preserving the useful reply.

        Handles the two measured shapes: an exact echoed first sentence
        ("I see. ...") and a paraphrased setup clause before a dash
        ("You're moving to Seattle on 9/13—pack ..."). Ordinary answers are
        untouched because they neither exactly repeat the turn nor place a
        high-overlap setup clause before a dash.
        """
        text = str(reply or "").strip()
        said = str(current_user or "").strip()
        if not text or not said:
            return text

        sentences = cls._ECHO_SENTENCE.split(text, maxsplit=1)
        if (
            len(sentences) == 2
            and cls._normalize(sentences[0]) == cls._normalize(said)
        ):
            return sentences[1].strip()

        for separator in (cls._ECHO_DASH, cls._ECHO_COMMA):
            stripped = cls._strip_before(text, said, separator)
            if stripped != text:
                return stripped
        return text

    # Long enough to be a real answer that happens to reuse the words, as
    # opposed to the message handed straight back.
    _ECHO_MAX_WORDS = 10

    @classmethod
    def is_pure_echo(cls, reply: str, current_user: str) -> bool:
        """Whether the reply says nothing except what the person just said.

        :meth:`strip_current_turn_echo` cannot help here, because there is
        nothing left after the echo is removed -- the echo *is* the reply.
        The only repair is to answer again, so this is reported separately.

        Measured live, from a clean session:

            "I'm moving to seattle" -> "That's exciting! ... where you'll
                                        be staying?"
            "not yet"               -> "Got it -- moving is a big step.
                                        When are you arriving?"
            "I see"                 -> "I see."

        Both existing guards passed it: there was no prefix to strip, and it
        looks nothing like the previous answer.
        """
        text = cls._normalize(reply)
        said = cls._normalize(current_user)
        if not text or not said:
            return False
        if text == said:
            return True
        # The same content in the mirror -- "I'm moving to Seattle" answered
        # "You're moving to Seattle!" -- which adds a pronoun and nothing
        # else. Bounded by length so a real answer that reuses the subject's
        # words ("The capital of France is Paris") is never caught: it has
        # content words of its own, so the sets differ.
        if len(text.split()) > cls._ECHO_MAX_WORDS:
            return False
        # Filler is discounted on both sides, so an acknowledgement in front
        # of the echo does not disguise it: live, "I see" came back as "Got
        # it. I see." Requiring both sides to keep a real word afterwards is
        # what stops a bare "Got it." -- which echoes nothing -- from being
        # read as one.
        reply_words = cls._content_words(reply) - cls._ECHO_FILLER
        said_words = cls._content_words(current_user) - cls._ECHO_FILLER
        return bool(reply_words) and bool(said_words) and (
            reply_words == said_words
        )

    @classmethod
    def _strip_before(cls, text: str, said: str, separator: re.Pattern) -> str:
        """Drop a restatement sitting before ``separator``, or keep the text."""
        break_at = separator.search(text)
        if break_at is None:
            return text
        prefix = text[:break_at.start()].strip(" ,.;:!?")
        remainder = text[break_at.end():].strip()
        if not prefix or not remainder:
            return text
        prefix_words = cls._content_words(prefix)
        user_words = cls._content_words(said)
        if not prefix_words:
            return text

        # Two ways a prefix is an echo, and the second one is why "I see"
        # survived: the old rule needed two content words before it would
        # strip anything, and "I see" has one ("i" is a stopword). Requiring
        # the prefix to account for the person's *whole* message instead of
        # merely a subset of it makes one word safe to strip -- "Python -
        # it has better libraries" answers "python or rust?" and keeps its
        # answer, because "python" is not the whole question.
        restates_everything = (
            prefix_words == user_words
            or cls._normalize(prefix) == cls._normalize(said)
        )
        # The same restatement, with an acknowledgement allowed in front of
        # it: "Ah, got it. Not yet -- ...". What has to be substantial is
        # the *echoed* part, not the prefix, or a one-word answer gets
        # deleted -- "Python - it has the better libraries" answers "python
        # or rust?" with a prefix that is entirely the person's own word.
        echoed = prefix_words - cls._ECHO_FILLER
        partial_echo = (
            separator is cls._ECHO_DASH
            and prefix_words.issubset(user_words | cls._ECHO_FILLER)
            and (
                (bool(echoed) and echoed == user_words)
                or (len(echoed) >= 2 and echoed.issubset(user_words))
            )
        )
        if restates_everything or partial_echo:
            return remainder[:1].upper() + remainder[1:]
        return text

    # An explicit correction. Measured live:
    #
    #   "yep I'm going there"      -> "Seattle's a great place..."
    #   "no I mean I'm going to UW" -> the same words, byte for byte
    #
    # The reacted-only rule below does not catch this, because a
    # correction is not a reaction -- it is the strongest possible signal
    # that the previous answer was about the wrong thing.
    _CORRECTS = re.compile(
        r"^(?:no|nope|nah)[,! ]|\bi\s+me(?:an|ant)\b|"
        r"\bi\s+was\s+talking\s+about\b|\bi(?:'m|\s+am)\s+telling\s+you\b|"
        r"\bas\s+i\s+said\b|\bi\s+said\b|\bnot\s+[\w]+,\s*|"
        r"\bactually[, ]|\bthat's\s+not\s+what\b",
        re.IGNORECASE,
    )

    # Asking why is asking for something the previous answer did not
    # say. Measured live: "Which one would you choose?" was answered,
    # then "Why?" got the same sentence back, word for word.
    _ASKS_FOR_REASONS = re.compile(
        r"^(?:but\s+)?(?:why|how come)\b|^\s*(?:why|how come)[?.\s]*$|\bwhat makes\b|\bhow so\b|\bfor what reason\b",
        re.IGNORECASE,
    )

    @classmethod
    def without_stale_courtesy(cls, reply: str, current_user: str) -> str:
        """The reply with an unearned "you're welcome" taken off the front.

        ``should_retry`` already refuses a draft that opens this way, and
        the regeneration was then accepted without the same check.
        Measured live: "I like strawberries." was answered "You're welcome
        -- strawberries are tasty. Want to try some?", the guard fired,
        and the retry opened with it again.

        Removing the clause is safe in a way that rejecting the answer is
        not: what follows it is a real reply, and asking the model a third
        time costs a turn to no purpose.
        """
        text = str(reply or "").strip()
        if not text or cls._is_thanking(str(current_user or "")):
            return text
        match = cls._COURTESY_OPENER.match(text)
        if not match:
            return text
        rest = text[match.end():].lstrip(" ,.!-–—:;")
        if not rest:
            return text
        return rest[:1].upper() + rest[1:]

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

    # How many recent answers a new one is measured against. Four covers the
    # span a person actually notices; beyond that, a genuinely re-asked
    # question starts looking like a repeat.
    RECENT_ANSWERS = 4

    @classmethod
    def _recent_answers(
        cls, history: list[dict[str, str]],
    ) -> list[tuple[str, str]]:
        """Recent (what was said, what she answered) pairs, oldest first."""
        pairs: list[tuple[str, str]] = []
        asked = ""
        for item in history:
            role = item.get("role")
            content = str(item.get("content", "") or "")
            if role == "user":
                asked = content
            elif role == "assistant" and content.strip():
                pairs.append((asked, content))
        return pairs[-cls.RECENT_ANSWERS:]

    @classmethod
    def should_retry(
        cls,
        reply: str,
        current_user: str,
        history: list[dict[str, str]],
    ) -> bool:
        if not reply.strip() or len(history) < 2:
            return False

        # Against the *recent* answers, not only the last one. Measured live:
        #
        #   "I'm moving to seattle" -> "Seattle's a great place to start
        #                               something new. ..."
        #   "not yet"               -> "Got it. Not yet, that's fine. ..."
        #   "I see"                 -> "Seattle's a great place to start
        #                               something new. ..."
        #
        # Comparing only against the immediately previous answer, the third
        # turn looked fine -- it is nothing like the second one. Repetition
        # a listener notices spans more than one turn, so the window has to
        # as well. Each answer is judged against the message that actually
        # prompted it, or "did the user repeat themselves" would compare
        # against the wrong turn.
        answered = cls._recent_answers(history)
        if not answered:
            return False
        previous_user, previous_assistant, reply_similarity = max(
            (
                (said, answer, cls._repeats(reply, answer))
                for said, answer in answered
            ),
            key=lambda item: item[2],
        )
        if not previous_user or not previous_assistant:
            return False
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

        if (
            cls._OPENS_NEGATIVE.match(said)
            and not cls._is_thanking(said)
            and cls._INVENTED_NO_THANKS.search(str(reply))
        ):
            return True

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
            and (
                cls._ANSWERS_WITH_UNCERTAINTY.search(said)
                or not cls._REQUESTS_SOMETHING.search(said)
            )
        )
        if reacted_only:
            return True

        # They asked for the reasoning behind the answer, and got the answer
        # again. Whatever else is true of the turn, that is not a reply to
        # what was asked.
        if cls._ASKS_FOR_REASONS.search(said.strip()):
            return True

        # Third shape: they said the last answer was about the wrong thing,
        # and the same answer came back. Whatever else is true of the turn,
        # that reply has already been rejected by the person hearing it.
        if cls._CORRECTS.search(said):
            return True

        # Everything above names a *shape of turn* that must not be answered
        # twice, and that list was the bug: it was an allowlist, so every
        # phrasing nobody had thought of yet was allowed through. Measured
        # against twenty-four ordinary replies to "have you figured out where
        # you'll be staying?", eighteen got the identical answer back --
        # "not yet", "nothing yet", "still looking", "haven't decided",
        # "probably an apartment", "in september", "makes sense", "lol".
        # None of them are exotic, and no list of reaction words was ever
        # going to cover them.
        #
        # So the default flips. By this point the reply is at least 90%
        # identical to the previous one and the person did not repeat
        # themselves, which is already the definition of her repeating
        # herself. Saying it again is only defensible for two kinds of turn,
        # and both are now stated positively as exceptions rather than left
        # to an allowlist to imply:
        #
        # * the turn asks for something -- an elliptical "yeah, tell me when"
        #   genuinely wants that same date back;
        # * the turn thanks her again. The right answer to a second thank-you
        #   is the same courtesy as the first; a courtesy answer is only
        #   wrong on a turn that was *not* thanks, which the stale-courtesy
        #   rule above already catches.
        return not (
            cls._REQUESTS_SOMETHING.search(said) or cls._is_thanking(said)
        )
