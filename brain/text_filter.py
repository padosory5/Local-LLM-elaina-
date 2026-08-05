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

    # Korean Windows installations expose native app, window, and control
    # names through UI Automation. Those labels are valuable for grounding
    # and should remain untouched in logs and the on-screen response, but an
    # English Piper voice should never be asked to pronounce them.
    HANGUL_PATTERN = re.compile(
        "["
        "\u1100-\u11FF"  # Hangul Jamo
        "\u3130-\u318F"  # Hangul Compatibility Jamo
        "\uA960-\uA97F"  # Hangul Jamo Extended-A
        "\uAC00-\uD7AF"  # Hangul syllables
        "\uD7B0-\uD7FF"  # Hangul Jamo Extended-B
        "]+",
        flags=re.UNICODE,
    )

    _FAILED_ACTION_PATTERN = re.compile(
        r"(?i)\b(?:could\s+not|couldn['’]?t|did\s+not|"
        r"didn['’]?t|failed|unable|not\s+found)\b"
    )

    _ENGLISH_ACTION_FALLBACKS = (
        (
            re.compile(r"(?i)\btyped\b.*?into|\bentered\b.*?into"),
            "Entered the text in the requested field.",
        ),
        (
            re.compile(r"(?i)\bclicked\b"),
            "Clicked the requested control.",
        ),
        (
            re.compile(r"(?i)\bfocused\b|\bswitched\s+to\b"),
            "Focused the requested window.",
        ),
        (
            re.compile(r"(?i)\bopened\b|\bis\s+open\b"),
            "Opened the requested item.",
        ),
        (
            re.compile(r"(?i)\bclosed\b|\bforce[- ]?quit\b"),
            "Closed the requested window.",
        ),
        (
            re.compile(r"(?i)\bselected\b"),
            "Selected the requested option.",
        ),
        (
            re.compile(r"(?i)\bscrolled\b"),
            "Scrolled the requested view.",
        ),
    )

    @classmethod
    def clean(cls, text: str) -> str:
        text = cls.EMOJI_PATTERN.sub("", text)

        # Elaina is presented as a speaking companion. Stray Markdown emphasis
        # characters look unnatural in chat and may be pronounced by TTS.
        # Insert a separator when malformed emphasis would otherwise fuse two
        # words, such as "models**offer" becoming "modelsoffer".
        text = re.sub(r"(?<=\w)\*+(?=\w)", " ", text)
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
    def for_configured_speech(
        cls,
        text: str,
        *,
        response_language: str = "en",
    ) -> str:
        """Create TTS input that respects the configured response language.

        This filter is intentionally applied at the audio boundary. It does
        not modify the assistant response shown in Electron, planner state,
        accessibility labels, or audit logs.

        English voices receive concise semantic descriptions for native
        Korean UI action results. Unexpected Korean metadata is removed while
        preserving the surrounding English text; if nothing meaningful is
        left, a short screen-reference fallback is spoken.
        """
        text = cls.for_speech(text)
        language = str(response_language or "").strip().lower()

        if not language.startswith("en"):
            return text

        if not cls.HANGUL_PATTERN.search(text):
            return text

        # Do not turn a reported failure into a success acknowledgement. A
        # negative result instead falls through to the metadata-only scrub so
        # phrases such as "I couldn't click 설정" remain truthful.
        if not cls._FAILED_ACTION_PATTERN.search(text):
            if "?" in text and re.search(
                r"(?i)\bclick(?:ed|ing)?\b", text,
            ):
                return "Click the requested control?"
            for pattern, fallback in cls._ENGLISH_ACTION_FALLBACKS:
                if pattern.search(text):
                    return fallback

        text = cls.HANGUL_PATTERN.sub("", text)
        text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"(?:^|\s)[\-–—]+\s*(?=[.!?]|$)", " ", text)
        text = re.sub(r"[ \t]+", " ", text).strip(" \t\r\n-\u2013\u2014,:;")

        # A native title such as "제목 없음 - 메모장" has no useful English
        # speech left after sanitizing. Avoid silence while keeping Piper from
        # attempting an unsupported pronunciation.
        if not re.search(r"[A-Za-z]", text):
            return "The result is shown on screen."

        return text

    @classmethod
    def for_voice_response(
        cls,
        text: str,
        *,
        max_words: int = 45,
        max_sentences: int = 2,
    ) -> str:
        """Create plain prose for Electron and TTS without deleting content.

        ``max_words`` and ``max_sentences`` remain accepted for compatibility
        with older callers. Response length is now controlled during model
        generation and complete-answer rewriting, never by slicing text here.
        """
        del max_words, max_sentences
        text = cls.for_speech(text)
        if not text:
            return ""

        # These generic endings often create confirmation loops in voice chat
        # without adding useful information. Specific natural follow-ups such
        # as "What kept you up?" are not affected.
        text = re.sub(
            r"\s*(?:Do you )?Want to know (?:anything|more)[^?]*\?\s*$|"
            r"\s*Anything else[^?]*\?\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

        return text.strip()
