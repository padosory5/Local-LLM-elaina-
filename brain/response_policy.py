from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ResponseLimits:
    """User-configured targets used while generating a complete answer."""

    max_words: int = 0
    max_sentences: int = 0

    def instruction(
        self,
        *,
        calculation: bool = False,
        recommendation: bool = False,
        language: str = "en",
    ) -> str:
        """The generation rules, written in the language of the reply.

        Korean gets Korean. The personality file already switches; the
        rules under it did not, so the model was told in English how to
        write Korean. Measured directly against qwen3:8b on the turns that
        failed live, Korean rules gave shorter and more natural answers.
        """
        if str(language or "").strip().lower().startswith("ko"):
            return self._korean_instruction(
                calculation=calculation, recommendation=recommendation,
            )
        return self._english_instruction(
            calculation=calculation, recommendation=recommendation,
        )

    def _korean_instruction(
        self, *, calculation: bool, recommendation: bool,
    ) -> str:
        limits: list[str] = []
        if self.max_words > 0:
            limits.append(f"{self.max_words}단어 이내")
        if self.max_sentences > 0:
            limits.append(f"{self.max_sentences}문장 이내")
        length_rule = (
            "완성된 응답은 " + ", ".join(limits) + "로 유지합니다."
            if limits else "필요한 만큼만 자세히 말합니다."
        )
        rules = [
            "지금 들어온 요청에 이 응답에서 답합니다.",
            "요청하신 결과를 먼저 말하고, 반응이나 배경 설명은 그다음입니다.",
            "지금 할 수 있는 계산이나 설명을 나중에 하겠다고 미루지 않습니다.",
            "이미 요청하신 것을 다시 원하시는지 묻지 않습니다.",
            length_rule,
            "길이를 맞추려고 문장을 중간에 끊거나 요청하신 결과를 빼지 "
            "않습니다. 자연스럽게 짧게 씁니다.",
            "반드시 습니다체로 씁니다. '~요'로 끝나는 해요체는 쓰지 않습니다.",
            "확실하지 않은 것은 지어내지 않습니다. 영화 제목, 상품명, 가격은 "
            "확인된 것만 말합니다.",
        ]
        if calculation:
            rules.extend([
                "지금 계산해서 최종 숫자를 먼저 말합니다.",
                "합리적인 가정이 가능하면 짧게 밝히고 답합니다. 되묻지 "
                "않습니다.",
            ])
        if recommendation:
            rules.extend([
                "배경 설명보다 추천을 먼저 말합니다.",
                "구체적인 제품, 모델, 장소를 말합니다. 분류나 브랜드 전체, "
                "판매처가 아닙니다.",
                "이유는 요청하신 목적에 맞는 구체적인 기능이나 수치 하나로 "
                "말합니다.",
                "어디서 찾았는지, 검색 결과가 몇 개였는지는 말하지 않습니다.",
                "지금 하실 수 있는 다음 단계와 꼭 필요한 주의사항 하나만 "
                "덧붙입니다.",
                "전문가나 병원에 문의하라는 말을 일상적인 조언에 붙이지 "
                "않습니다.",
                "보고서가 아니라 실무를 아는 사람처럼 말합니다.",
            ])
        return " ".join(rules)

    def _english_instruction(
        self,
        *,
        calculation: bool = False,
        recommendation: bool = False,
    ) -> str:
        limits: list[str] = []
        if self.max_words > 0:
            limits.append(f"at most {self.max_words} spoken words")
        if self.max_sentences > 0:
            # "at most 1 complete sentences" used to be unreachable and is
            # now the ordinary case: a receipt's contract is one sentence.
            plural = "s" if self.max_sentences != 1 else ""
            limits.append(
                f"at most {self.max_sentences} complete sentence{plural}"
            )

        if limits:
            length_rule = "Keep the finished response to " + " and ".join(limits) + "."
        else:
            length_rule = "Use only as much detail as the complete answer needs."

        rules = [
            "Answer the current request in this response.",
            "Give the requested result before reactions, background, or offers of more help.",
            "Do not say that you will calculate, explain, check, or break something down later when you can do it now.",
            "Do not ask whether the user wants the answer after they already requested it.",
            length_rule,
            "Compose a naturally shorter complete answer; never stop mid-sentence or omit the requested result to satisfy a length target.",
        ]
        if calculation:
            rules.extend([
                "Do the arithmetic now and state the final numerical result first.",
                "If the wording permits a reasonable assumption, state it briefly and answer instead of delaying with a clarification question.",
            ])
            if self.max_words <= 0 and self.max_sentences <= 0:
                # This unlimited-length variant is used for the first
                # calculation draft, before any voice-length rewrite. Showing
                # the work directly in the visible answer converges reliably;
                # a hidden reasoning phase measured on this model did not (see
                # ResponseLimits.generation_budget).
                rules.append(
                    "Show brief step-by-step working directly in your "
                    "answer, with no hidden reasoning, then clearly state "
                    "the final result at the end."
                )
        if recommendation:
            rules.extend([
                "Give a direct, friendly recommendation before background.",
                # Asked live for "any specific model you recommend?", the
                # answer named a category and then narrated the search. Both
                # halves are the same mistake: the person asked what to get,
                # not what the shelf is called or where you looked.
                "Name the specific product, model, or place; never a category, a brand's whole range, or a shop.",
                "Say why in terms of one concrete feature or number that matters for what they asked for.",
                "Never describe where the information came from, what the search returned, or how many results there were.",
                "Include an action the user can take now and only one essential caution.",
                "Never make a referral the whole answer; give useful guidance first.",
                "Do not append a doctor, expert, or professional referral to routine advice; reserve outside help for a concrete immediate danger.",
                "When a medicine or condition could change the advice, ask the user for that one detail instead of sending them elsewhere.",
                "For health advice, never invent a numeric dose; use label directions instead.",
                "Sound like a practical friend, not a formal report or disclaimer.",
            ])
        return " ".join(rules)

    def exceeds(self, text: str) -> bool:
        if self.max_words > 0 and self.word_count(text) > self.max_words:
            return True
        if (
            self.max_sentences > 0
            and self.sentence_count(text) > self.max_sentences
        ):
            return True
        return False

    def merge_extra_sentences(self, text: str) -> str:
        """Meet a sentence target by joining clauses without deleting content."""
        if self.max_sentences <= 0:
            return text

        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", text.strip())
            if sentence.strip()
        ]
        if len(sentences) <= self.max_sentences:
            return text

        fixed = sentences[: self.max_sentences - 1]
        remainder = sentences[self.max_sentences - 1 :]
        # Two clauses joined by a semicolon is punctuation. Three or more is
        # a list wearing a disguise, and it is unspeakable: measured live,
        # "How about The Royal Tenenbaums; It's quirky, heartfelt, and just
        # the right mix of humor and warmth; Perfect for a relaxed night
        # in." The sentence target is a target, not a licence to produce
        # something no one would say, so past two clauses it is not met.
        if len(remainder) > 2:
            return text
        merged = "; ".join(
            sentence.rstrip(".!?").strip()
            for sentence in remainder
        ) + "."
        candidate = " ".join([*fixed, merged])
        return candidate if not self.exceeds(candidate) else text

    @staticmethod
    def word_count(text: str) -> int:
        return len(re.findall(r"\b[\w'$%-]+\b", text, flags=re.UNICODE))

    @staticmethod
    def sentence_count(text: str) -> int:
        return len([
            sentence
            for sentence in re.split(r"(?<=[.!?])\s+", text.strip())
            if sentence.strip()
        ]) if text.strip() else 0

    def generation_budget(
        self,
        *,
        detailed: bool = False,
        calculation: bool = False,
    ) -> int:
        """Leave enough room to finish before any optional rewrite."""
        if calculation:
            # Measured against qwen3:8b: a hidden thinking-mode call did not
            # reliably converge on multi-step problems (still empty content
            # with done_reason "length" at 4000+ tokens on a multi-segment
            # proration question). Showing brief working directly in the
            # visible answer instead converged correctly well under this
            # budget on both a simple and a multi-step calculation.
            return 640
        if self.max_words > 0:
            requested = self.max_words * 3 + 64
        else:
            requested = 480 if detailed else 320
        return max(256, min(requested, 768))


# English only. Every pattern below is an English deferral phrase
# ("let me calculate that"), and Korean deferrals share none of their shape.
# A Korean draft that defers is currently not caught.
LANGUAGES = ("en",)


class AnswerCompletionGuard:
    """Identify calculation drafts that defer or visibly stop before answering."""

    _DEFERRAL = re.compile(
        r"\b(?:want me to|would you like me to|should i)\b|"
        r"\b(?:let me|i(?:'ll| will))\s+"
        r"(?:calculate|do the math|break it down|work it out)\b",
        flags=re.IGNORECASE,
    )

    @classmethod
    def needs_retry(cls, text: str, *, calculation: bool) -> bool:
        if not calculation:
            return False

        cleaned = text.strip()
        if not cleaned:
            return True
        if cleaned.endswith("?"):
            return True
        if cleaned[-1] not in ".!?":
            return True
        if cls._DEFERRAL.search(cleaned):
            return True
        return not bool(re.search(r"\d", cleaned))


class ClosingOfferGuard:
    """Cut the canned "anything else?" footer off the end of an answer.

    ``personality_en.txt`` already forbids these -- "Avoid canned lines...
    Don't tack a generic 'anything else?' onto every answer" -- and qwen3:8b
    adds them anyway: measured live, "Let me know if you need help with
    anything else!" closed a plain exchange-rate answer. Prompt wording alone
    has not held, so the removal is deterministic. It also costs nothing,
    which a rewrite call would not.

    Deliberately narrow. Only a *trailing* sentence is considered, and only
    one carrying a generic marker -- "anything else", "any questions", "feel
    free to ask". An offer with a real referent is content, not filler, and
    has to survive:

        "Let me know which one you'd prefer."      kept
        "Want me to pull up the hotel page?"       kept
        "Let me know if you need anything else."   cut
    """

    # Generic markers. Each says "ask me something, anything" -- none of them
    # points at a thing in the conversation, which is what makes them filler.
    _GENERIC = (
        r"anything\s+else",
        r"any\s+(?:other\s+)?questions?",
        r"anything\s+(?:i|else\s+i)\s+can\s+(?:help|do|assist)",
        r"feel\s+free\s+to\s+(?:ask|reach)",
        r"happy\s+to\s+help",
        # "Let me know if you need help." on its own is filler. "Let me
        # know if you want help narrowing down options or finding local
        # shops" is not -- it names two things she would actually do, and
        # stripping it as filler deleted the most useful sentence in the
        # reply. The generic form ends right after "help", or trails off
        # into "with anything"; anything else has a referent and is content.
        r"let\s+me\s+know\s+if\s+you\s+(?:need|want|would\s+like)\s+help"
        r"(?:\s+with\s+(?:anything|that|this|it))?\s*[.!?]*\s*$",
        r"here\s+(?:to|if\s+you\s+need)\s+help",
        r"here\s+to\s+help\s+with\s+anything",
        r"with\s+anything\s+you\s+need",
        r"if\s+you\s+need\s+anything",
        # "you'd like" and "you would like" were missing, and let
        # "Let me know if you'd like more details or updates." through
        # on a live stock-price answer.
        r"let\s+me\s+know\s+if\s+you[’']?(?:d|\s+would)?\s*"
        r"(?:need|want|have|like)\s+(?:any|anything|more|further)",
    )
    # The model's own trailing capability offers. Stripping these is what
    # makes RecommendationPolicy the single layer deciding whether a
    # proactive offer appears at all: measured over twenty live turns, four
    # of eleven recommend-turns were suppressed *because the model had
    # already offered*, uncooled and unparked, which is the pushiness the
    # policy exists to bound.
    #
    # The line is between offering to *act* and giving advice. "I can
    # suggest some movies" is an offer; "I'd suggest checking their site" is
    # advice and stays. First-person modal plus an action verb, or a
    # question about her doing something -- nothing else.
    _OFFER = (
        r"\bwant me to\b",
        # "Would you like help finding a model?" is the same offer
        # without the "me to" -- it survived, the policy's offer was
        # appended after it, and the reply carried two.
        r"\bwould you like (?:me to|help|assistance|a hand)\b",
        r"\bwant (?:help|a hand)\s+(?:with|finding|looking)\b",
        r"\bshall i\b",
        r"\bshould i\s+(?:look|search|check|find|suggest|pull|show)\b",
        r"\bi (?:can|could|'ll|will)\s+(?:also\s+)?"
        r"(?:suggest|recommend|find|look|search|check|pull|show|dig|help)\b",
        r"\blet me know if you(?:[’']d| would)?\s*(?:like|want)\b",
        r"\bi'?m happy to\s+(?:look|search|check|find|suggest|help)\b",
    )

    _CLOSER = re.compile("|".join(_GENERIC), flags=re.IGNORECASE)
    _OFFER_CLOSER = re.compile("|".join(_OFFER), flags=re.IGNORECASE)

    @staticmethod
    def _sentences(text: str) -> list[str]:
        return [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", str(text).strip())
            if sentence.strip()
        ]

    @classmethod
    def is_closing_offer(cls, sentence: str) -> bool:
        text = str(sentence)
        return bool(cls._CLOSER.search(text) or cls._OFFER_CLOSER.search(text))

    @classmethod
    def offers_to_act(cls, sentence: str) -> bool:
        """Whether this sentence offers to *do* something, as opposed to
        recommending that the user do it."""
        return bool(cls._OFFER_CLOSER.search(str(sentence)))

    @classmethod
    def strip(cls, text: str, *, keep_offers: bool = False) -> str:
        """The answer without its trailing generic offer, if it had one.

        ``keep_offers`` protects an offer that is genuinely waiting for an
        answer. Elaina's own "want me to?" is parked in CapabilityOfferGate
        and the next "ok" resolves to the action -- removing the sentence
        would leave a pending offer the user never saw. Generic filler is
        still removed either way.

        Returns the original whenever removal would leave nothing: an answer
        that is *only* a canned offer is a different failure, and silently
        emptying it would turn a weak reply into no reply at all.

        Generic filler is removed wherever it sits, not only at the end.
        Position was an assumption, and it was wrong: measured live, "Let me
        know if you need help with anything else. Enjoy your cold brew!"
        went out whole, because the filler had a real sentence after it and
        a tail-only walk never reached it. A line that names nothing in the
        conversation is filler in the middle of a reply for exactly the
        reason it is filler at the end of one.

        Offers keep the trailing-only rule. An offer says something, and one
        placed mid-reply is usually load-bearing -- "I can check that if you
        want, but here's what I already know" is a sentence with an argument
        in it, and cutting its first half changes the reply's meaning.
        """
        sentences = cls._sentences(text)
        if len(sentences) < 2:
            return text

        # Generic filler, anywhere.
        kept = [
            sentence for sentence in sentences
            if not cls._CLOSER.search(sentence)
        ]
        if not kept:
            return text

        # Offers, from the end only.
        if not keep_offers:
            while len(kept) > 1 and cls._OFFER_CLOSER.search(kept[-1]):
                kept.pop()

        if len(kept) == len(sentences):
            return text
        return " ".join(kept)


class AdviceResponseGuard:
    """Request a rewrite when routine advice turns into a referral footer."""

    _ROUTINE_REFERRAL = re.compile(
        r"\b(?:ask|consult|contact|call|see|talk|check|speak|visit)\b"
        r".{0,48}\b(?:doctor|physician|clinician|healthcare\s+(?:provider|"
        r"professional)|medical\s+professional|expert)\b",
        flags=re.IGNORECASE,
    )
    _NUMERIC_DOSE = re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:mcg|mg|g|ml|micrograms?|milligrams?|"
        r"grams?|milliliters?)\b",
        flags=re.IGNORECASE,
    )

    @classmethod
    def needs_rewrite(
        cls,
        text: str,
        *,
        recommendation: bool,
        urgent_safety: bool,
        advice_domain: str = "general",
    ) -> bool:
        if not recommendation or urgent_safety:
            return False
        if cls._ROUTINE_REFERRAL.search(text):
            return True
        return bool(
            advice_domain == "health"
            and cls._NUMERIC_DOSE.search(text)
        )
