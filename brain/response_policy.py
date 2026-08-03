from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ResponseLimits:
    """User-configured targets used while generating a complete answer."""

    max_words: int = 0
    max_sentences: int = 0

    def instruction(self, *, calculation: bool = False) -> str:
        limits: list[str] = []
        if self.max_words > 0:
            limits.append(f"at most {self.max_words} spoken words")
        if self.max_sentences > 0:
            limits.append(f"at most {self.max_sentences} complete sentences")

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
