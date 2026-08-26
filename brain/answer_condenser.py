"""Shorten a real, grounded result without inventing anything new.

Tool and planner results (a task-planner shortlist, a research summary, a
page reading) bypass the ordinary response-length path entirely: they are
"locked responses", passed through ``TextFilter.for_voice_response``, which
deliberately never slices text. That is right -- silently truncating a
verified result would be worse than a long one -- but it left the longest
answers Elaina ever gives with no shortening step at all.

This condenser adds one: a single cheap rewrite pass with a **verifiable
contract** rather than a trusted instruction, since qwen3:8b does not
reliably honour "don't add anything" on its own.

The contract, checked in code after the model answers:

* it must be genuinely shorter than the original;
* it must not contain a number, price, or percentage that the original
  did not contain -- the one failure mode that would turn a shortening
  pass into a fabrication;
* it must be a complete, non-empty sentence.

If any check fails, the original result is spoken unchanged. A slow, long,
correct answer is always preferable to a short, wrong one.
"""

from __future__ import annotations

import re
from typing import Any

# Numbers with their attached unit/symbol, which is what actually carries
# meaning in a result ("$68", "4.5 stars", "20%"). A bare digit inside a
# word ("B2B") is not one.
_NUMERIC_TOKEN = re.compile(
    r"[$₩€£¥]?\s?\d[\d,]*(?:\.\d+)?\s?%?",
)


class AnswerCondenser:
    """Make a long verified result easy to hear, or leave it alone."""

    def __init__(
        self,
        client: Any,
        model: str,
        *,
        keep_alive: int | str = -1,
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive

    @staticmethod
    def _numbers(text: str) -> set[str]:
        """Numeric values, normalised so punctuation can't fake a mismatch.

        "140,000." at the end of a sentence and "140,000 won" mid-sentence
        are the same value; without stripping the trailing stop, a
        perfectly faithful shortening looked like it had invented one.
        """
        return {
            token
            for token in (
                match.group(0).replace(" ", "").strip(".,")
                for match in _NUMERIC_TOKEN.finditer(str(text))
            )
            if token
        }

    @staticmethod
    def word_count(text: str) -> int:
        return len(re.findall(r"\b[\w'$%-]+\b", str(text), flags=re.UNICODE))

    def should_condense(self, text: str, *, max_words: int) -> bool:
        if max_words <= 0:
            return False
        # A little over the target is not worth a whole extra model call;
        # this only fires on answers that are genuinely hard to listen to.
        # At the default 45-word target that means roughly 68 words and up,
        # which is about where a spoken shortlist stops being followable.
        return self.word_count(text) > max_words * 1.5

    def condense(
        self,
        text: str,
        *,
        max_words: int,
        max_sentences: int,
        goal: str = "",
    ) -> str:
        original = " ".join(str(text or "").split()).strip()
        if not original or not self.should_condense(original, max_words=max_words):
            return original

        limits: list[str] = []
        if max_words > 0:
            limits.append(f"at most {max_words} words")
        if max_sentences > 0:
            limits.append(f"at most {max_sentences} sentences")
        limit_text = " and ".join(limits) or "as short as it can be"

        prompt = (
            "Rewrite this finished result so it is easy to understand when "
            f"spoken aloud, in {limit_text}.\n"
            "Rules:\n"
            "- Lead with the answer the user actually wanted.\n"
            "- Keep every name, price, and number exactly as written.\n"
            "- Never add a number, price, or fact that is not already "
            "there. If something does not fit, leave it out entirely "
            "rather than approximating it.\n"
            "- No preamble, no bullet points, no markdown. Plain sentences.\n"
            + (f"\nWhat the user asked: {goal}\n" if goal.strip() else "")
            + f"\nResult to shorten:\n{original}"
        )
        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                stream=False,
                options={"temperature": 0.2, "num_predict": max(96, max_words * 4)},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = response.get("message", {}) if isinstance(response, dict) else (
                getattr(response, "message", {}) or {}
            )
            content = (
                message.get("content", "")
                if isinstance(message, dict)
                else getattr(message, "content", "")
            )
            candidate = " ".join(str(content).split()).strip()
        except Exception as error:
            print(
                "[Condenser] Shortening failed safely: "
                f"{type(error).__name__}: {error}"
            )
            return original

        if not self._is_faithful(candidate, original):
            print("[Condenser] Rejected a shortening that changed the result.")
            return original
        print(
            "[Condenser] "
            f"{self.word_count(original)} -> {self.word_count(candidate)} words."
        )
        return candidate

    def _is_faithful(self, candidate: str, original: str) -> bool:
        if not candidate:
            return False
        if candidate[-1] not in ".!?":
            return False
        if self.word_count(candidate) >= self.word_count(original):
            return False
        # The only hard failure worth rejecting for: a value that was never
        # in the verified result.
        return self._numbers(candidate).issubset(self._numbers(original))
