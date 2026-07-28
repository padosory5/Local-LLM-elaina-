import re


class TextFilter:

    # Matches most emoji and pictographic Unicode ranges.
    EMOJI_PATTERN = re.compile(
        "["
        "\U0001F300-\U0001F5FF"
        "\U0001F600-\U0001F64F"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FAFF"
        "\U00002700-\U000027BF"
        "\U00002600-\U000026FF"
        "]+",
        flags=re.UNICODE,
    )

    @classmethod
    def clean(cls, text: str) -> str:
        text = cls.EMOJI_PATTERN.sub("", text)

        # Elaina is presented as a speaking companion. Stray Markdown emphasis
        # characters look unnatural in chat and may be pronounced by TTS.
        return text.replace("*", "")

    @classmethod
    def for_speech(cls, text: str) -> str:
        """Convert display-oriented text into natural TTS input."""
        text = cls.clean(text)

        # Keep link labels but never read their raw destinations aloud.
        text = re.sub(
            r"\[([^\]]+)\]\((?:[^)]+)\)",
            r"\1",
            text,
        )
        text = re.sub(r"https?://\S+", "", text)

        # Suppress report-style labels that occasionally leak from factual
        # models. Confidence should be expressed naturally, not announced as a
        # form field by a speaking companion.
        text = re.sub(
            r"(?i)\banswer\s*:\s*",
            "",
            text,
        )
        text = re.sub(
            r"(?i)\bconfidence\s*:\s*"
            r"(?:high|moderate|medium|low)\b[\s,.;:—-]*",
            "",
            text,
        )

        # Remove common Markdown structure and code-formatting marks.
        text = re.sub(r"```(?:\w+)?", "", text)
        text = text.replace("`", "")
        text = re.sub(
            r"(?m)^\s{0,3}(?:#{1,6}|>|[-+])\s*",
            "",
            text,
        )
        text = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)

        # Underscores are useful on screen for identifiers, but a speech
        # engine should pause between their words instead of saying "underscore."
        text = text.replace("_", " ")
        text = text.replace("&", " and ")

        # Collapse formatting whitespace while preserving sentence boundaries.
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s*\n+\s*", " ", text)
        return text.strip()

    @classmethod
    def for_voice_response(
        cls,
        text: str,
        *,
        max_words: int = 45,
        max_sentences: int = 2,
    ) -> str:
        """Create plain, bounded prose for both Electron and TTS."""
        text = cls.for_speech(text)
        if not text:
            return ""

        sentences = [
            sentence.strip()
            for sentence in re.split(
                r"(?<=[.!?])\s+",
                text,
            )
            if sentence.strip()
        ]
        if max_sentences > 0:
            text = " ".join(sentences[:max_sentences])

        words = text.split()
        if max_words > 0 and len(words) > max_words:
            text = " ".join(words[:max_words]).rstrip(",:;—-")
            if not text.endswith((".", "!", "?")):
                text += "."

        return text.strip()
