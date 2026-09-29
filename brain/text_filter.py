import re

from brain import capability_contract


# Both. It is the only guard that was bilingual from the start, because
# it had to be: it decides what a voice is asked to pronounce.
LANGUAGES = ("en", "ko")


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
        # What is left over once the picture itself is gone. A live greeting
        # came out as "️ How's your day shaping up?" -- the sun was
        # removed and its variation selector was not, because U+FE0F sits in
        # none of the ranges above. The same is true of the skin-tone
        # modifiers, the zero-width joiner that binds a compound emoji, and
        # the keycap mark. None of them are ever part of a word, and each
        # one that survives is a character a TTS voice has to guess at.
        "\U0001F3FB-\U0001F3FF"  # skin tone modifiers
        "\U0000FE0F\U0000FE0E"   # variation selectors
        "\U0000200D"             # zero-width joiner
        "\U000020E3"             # combining enclosing keycap
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

    # Japanese kana, in a reply that is meant to be Korean or English.
    #
    # Measured, once, in a real Korean session:
    #
    #     안녕하세요. 도움이 되었ようで 다행입니다. 궁금한 점이 또 있으시면...
    #
    # "ようで" is Japanese glued onto a Korean verb stem. The draft had
    # already been regenerated once and rewritten twice by the time it was
    # said, so no amount of asking the model again was going to fix it --
    # this is the CJK bleed a small multilingual model does, and per this
    # project's standing rule a confirmed behaviour gets a deterministic
    # guard rather than more prompt wording.
    #
    # Kana only, deliberately. Han ideographs are *not* included: 한자 is
    # real Korean, and "한자로 어떻게 써?" is a question she should be able
    # to answer. Kana has no such reading -- there is no sentence of hers
    # in either language where it belongs.
    KANA_PATTERN = re.compile("[\u3040-\u30ff]", flags=re.UNICODE)

    # A *name* in a script neither of her languages is written in: Greek,
    # Cyrillic, Hebrew, Arabic, Devanagari, Thai, and kana. Wider than the
    # sentence guard above because a name is not a sentence she wrote -- it
    # is a title lifted off a search result, and a Russian storefront
    # listing was said out loud as the pick in the paired Korean baseline.
    # Latin with accents and Hangul are hers; Han is left out for the reason
    # given above.
    OTHER_SCRIPT_PATTERN = re.compile(
        "[\u0370-\u03ff\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff"
        "\u0900-\u097f\u0e00-\u0e7f\u3040-\u30ff]",
        flags=re.UNICODE,
    )

    # Chinese written *into* a Korean word, which is not how Korean uses
    # 한자. Measured: "원하시면详细介绍해드리겠습니다" in a paired run, and
    # "청양椒" in the person's own session. Two shapes only -- a character
    # glued after a Hangul syllable, inside the word ("청양椒"), or glued in
    # front of the syllable that makes it a verb ("详细介绍해"). Everything
    # a real answer about 한자 needs is untouched: "한자(漢字)", "愛라고
    # 씁니다", "漢字로 쓰면".
    GLUED_HAN_PATTERN = re.compile(
        "(?<=[가-힣])[一-鿿]"
        "|[一-鿿](?=[하해했합드되돼된])",
        flags=re.UNICODE,
    )

    @classmethod
    def _carries_another_script(cls, text: str) -> bool:
        return bool(
            cls.KANA_PATTERN.search(text) or cls.GLUED_HAN_PATTERN.search(text)
        )

    @classmethod
    def without_foreign_script(cls, text: str) -> str:
        """Drop any sentence carrying a script that does not belong.

        The sentence, not the characters. Deleting the run leaves
        "도움이 되었 다행입니다" -- a broken verb and a sentence she cannot
        stand behind, which is worse out loud than not saying it, and
        guessing the connective the model meant is generation rather than
        repair. Whole sentences are what a reply is made of, so removing
        one leaves something grammatical.

        Returns "" when nothing is left, so the caller can decide whether
        an empty reply or a guard line is the right answer there.
        """
        said = str(text or "")
        if not cls._carries_another_script(said):
            return said
        try:
            from brain.conversation_style import sentences
        except Exception:
            parts = [part for part in re.split(r"(?<=[.!?])\s+", said.strip()) if part]
        else:
            parts = sentences(said)
        kept = [part for part in parts if not cls._carries_another_script(part)]
        dropped = len(parts) - len(kept)
        if dropped:
            print(
                f"[Language] Dropped {dropped} sentence(s) carrying Japanese "
                f"kana or Chinese written into a Korean word."
            )
        return " ".join(kept).strip()

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

    # Dashes as punctuation. People do not speak them, and a voice reads
    # them as a stumble or as the word itself: "I haven't checked -- want me
    # to look up real ones?" came out of a live turn sounding like two
    # sentences jammed together with a gap in the middle.
    #
    # Only the *parenthetical* dash is touched. A dash inside a word is part
    # of the word ("27-inch", "force-quit", "2025-01-02") and a dash between
    # two numbers is a range, which is said "to". Both are left alone, which
    # is why this cannot simply delete the character.
    _NUMBER_RANGE = re.compile(
        # A currency mark may sit on the far side of the dash: "$100 - $200"
        # is one range, and reading it as a pause loses the "to".
        r"(?<=\d)\s*(?:--+|[–—]|-)\s*(?=[$₩€£¥]?\s?\d)",
    )
    _PARENTHETICAL_DASH = re.compile(
        r"\s*(?:--+|[–—])\s*|\s+-\s+",
    )
    # What the substitution leaves behind: a comma next to punctuation that
    # already does the same job, or one at the very start of the line.
    _COMMA_TIDY = (
        (re.compile(r"\s+,"), ","),
        (re.compile(r",\s*,+"), ","),
        (re.compile(r"(?<=[.!?;:])\s*,\s*"), " "),
        (re.compile(r",\s*(?=[.!?;:])"), ""),
        (re.compile(r"^\s*,\s*"), ""),
    )

    @classmethod
    def natural_dashes(cls, text: str) -> str:
        """Say a dash the way a person would: as a pause, or as "to".

        Deterministic rather than a prompt instruction, per this project's
        standing rule -- and because the dashes are not all the model's.
        Plenty of them are written into her own lines, so telling the model
        to stop would not have fixed the sentence that prompted this.
        """
        said = str(text or "")
        if not said:
            return said
        # The comment above says a date keeps its dashes; the range rule did
        # not know that. Measured in an English session, spoken aloud: "As
        # of 2026 to 09 to 10". A phone number (206-221-7857, 010-1234-5678)
        # is the same shape and would be read as two ranges. Both are
        # masked with a non-breaking hyphen, which neither rule below
        # touches, and restored straight after.
        said = re.sub(
            r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{2,4}-\d{3,4}-\d{4}\b",
            lambda match: match.group(0).replace("-", "\u2011"),
            said,
        )
        said = cls._NUMBER_RANGE.sub(" to ", said)
        said = cls._PARENTHETICAL_DASH.sub(", ", said)
        said = said.replace("\u2011", "-")
        for pattern, replacement in cls._COMMA_TIDY:
            said = pattern.sub(replacement, said)
        return said.strip(" ,\t")

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

        # After the bullet strip above, deliberately: a leading "- " is a
        # list marker, and turning that into a comma would be wrong.
        text = cls.natural_dashes(text)

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
        """Speech-shaped prose, as the reply pipeline used to make it.

        Not used by the reply pipeline since Phase 3A: applied to the text
        on the screen it changed mathematics ("(x - a)" became "(x, a)").
        The screen now gets ``brain.realize.display`` and the voice
        ``brain.realize.speech`` (docs/PHASE3_PLAN.md 3A). Kept for callers
        outside the pipeline that want the old single-string shape.

        ``max_words`` and ``max_sentences`` remain accepted for compatibility
        with older callers. Response length is now controlled during model
        generation and complete-answer rewriting, never by slicing text here.
        """
        del max_words, max_sentences
        text = cls.for_speech(text)
        if not text:
            return ""

        # Nothing describing how Elaina is built gets pronounced. The
        # static scan in scripts/capability_contract_report.py fixed the
        # sentences the code chooses; this covers the ones it does not --
        # a model echoing an exception back out of a tool-result prompt,
        # a library message arriving through an MCP tool, a call site
        # written next month. Placed here because every reply path in
        # chat_engine already ends up in this method.
        text = capability_contract.redact_internals(text)
        if not text:
            return ""

        # Same reasoning, one script over: a reply carrying Japanese kana
        # is a reply she cannot pronounce in either of her languages.
        # Placed beside the redaction because it covers the same ground --
        # every path that produces a draft ends up here -- and the styling
        # pass that runs *after* this one checks it again, since a resaid
        # sentence is a new sentence and can leak the same way.
        text = cls.without_foreign_script(text)
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
