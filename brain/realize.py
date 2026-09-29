"""One reply, two realizations: the text that is read, and the text that is heard.

Phase 3A (docs/PHASE3_PLAN.md §2, 3A). Until this, one string was both: the
speech rules ran on the reply before anyone saw it, so ``(x - a)`` was shown
as ``(x, a)`` and ``b² - 4ac`` as ``b² to 4ac``. Now:

    final response --invariants()--> the reply every stage works on
        --display()--> screen, conversation history, memory
        --speech()---> the voice (voice/audio_manager.py, the only way in)

``invariants`` is what must hold whatever the modality: nothing internal
said or shown, no sentence in a script neither of her languages is written
in. ``display`` repairs damage and nothing else -- notation, line breaks and
list structure survive. ``speech`` may reword for the ear: notation said in
words (brain/spoken_notation.py), visual-only formatting dropped, prose
dashes as pauses. It must not drop substance; that is measured as content
coverage (``coverage``), not string equality.
"""

from __future__ import annotations

import re

from brain import capability_contract, conversation_style, spoken_notation
from brain.text_filter import TextFilter

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_REPORT_LABEL = re.compile(r"(?im)^\s*answer\s*:\s*")
_CONFIDENCE_LABEL = re.compile(
    r"(?i)\bconfidence\s*:\s*(?:high|moderate|medium|low)\b[\s,.;:—-]*"
)
_FENCE = re.compile(r"(?m)^[ \t]*```[\w+-]*[ \t]*\n?")
_PROSE_DOUBLE_DASH = re.compile(r"(?<=\S) -- (?=\S)")
# A web address is shown, never said: bare, in brackets after a label as
# display() writes it, or as a Markdown link target.
_SHOWN_ADDRESS = re.compile(r"\s*\(\s*https?://[^)\s]+\s*\)|https?://\S+")


def invariants(text) -> str:
    """HARD, in every modality: no internals, no script she cannot speak."""
    said = capability_contract.redact_internals(str(text or ""))
    if not said:
        return ""
    return TextFilter.without_foreign_script(said)


_IDENTIFIER = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_URL = re.compile(r"https?://\S+")


def _humanise_identifiers(text: str) -> str:
    """An internal identifier is never shown as one: "product_recommendation"
    is "product recommendation". The style layer counts any lowercase
    snake_case word as internal language (conversation_style._INTERNAL_MARKER);
    this is the display half of that rule, which the old speech filter
    enforced only by turning every underscore into a space. Web addresses
    are left exactly as written."""
    parts = []
    last = 0
    for url in _URL.finditer(text):
        parts.append(_IDENTIFIER.sub(lambda m: m.group(0).replace("_", " "), text[last:url.start()]))
        parts.append(url.group(0))
        last = url.end()
    parts.append(_IDENTIFIER.sub(lambda m: m.group(0).replace("_", " "), text[last:]))
    return "".join(parts)


def display(text) -> str:
    """The reply as the screen shows it: invariants, then damage repair only."""
    said = invariants(text)
    if not said:
        return ""
    said = _humanise_identifiers(said)
    said = TextFilter.EMOJI_PATTERN.sub("", said)
    said = _FENCE.sub("", said)
    said = said.replace("`", "")
    said = _LINK.sub(r"\1 (\2)", said)
    said = _REPORT_LABEL.sub("", said)
    said = _CONFIDENCE_LABEL.sub("", said)
    # An authored "--" is a dash; typeset it as one.
    said = _PROSE_DOUBLE_DASH.sub(" — ", said)
    return conversation_style.repair_display(said)


def speech(text, language: str = "en") -> str:
    """The reply as the voice says it: same substance, said for the ear."""
    said = invariants(text)
    if not said:
        return ""
    # Links first: the label is said, the address is not, and the notation
    # reader must not read a URL's slashes and brackets as mathematics.
    said = _LINK.sub(r"\1", said)
    said = _SHOWN_ADDRESS.sub("", said)
    said = spoken_notation.verbalize(said, language)
    # A bullet is a line on the screen and a pause in speech; a line that
    # ends without a mark ends a sentence when said.
    said = re.sub(r"(?m)^[ \t]*•[ \t]*", "", said)
    said = re.sub(r"(?<=[^\s.!?:;,])[ \t]*\n+[ \t]*(?=\S)", ". ", said)
    said = TextFilter.for_configured_speech(said, response_language=language)
    if not said:
        return ""
    return conversation_style.repair_structure(said)


# ------------------------------------------------------------ measuring

_CONTENT_WORD = re.compile(r"[A-Za-z]{3,}|[가-힣]{2,}|\d+(?:[.,:]\d+)*")
# Words that carry no substance, and markup-only words speech drops.
_FUNCTION_WORDS = frozenset(
    "the and for but not you your are was were has have had with this that "
    "from its it's they them their there here what when where which who "
    "how why can could would should will just also into onto than then "
    "http https www com".split()
)


# Markup, not substance: LaTeX commands and delimiters, and an exponent,
# which is said as "squared" or "to the fourth" rather than as its digit.
_NOT_CONTENT = re.compile(r"\\[A-Za-z]+|\$|\^\{?\(?[\d.]+\)?\}?|[⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ]+")


def content_tokens(text: str) -> list[str]:
    text = _NOT_CONTENT.sub(" ", str(text or ""))
    return [
        token.casefold() for token in _CONTENT_WORD.findall(str(text or ""))
        if token.casefold() not in _FUNCTION_WORDS
    ]


def coverage(shown: str, spoken: str) -> float:
    """How much of the display's substance the speech carries, 0..1.

    Every number and every content word on the screen should be heard. The
    speech may add words ("minus", "squared") and reorder a little; what it
    may not do is drop a value or a named thing. A URL is shown, not said,
    so its parts do not count.
    """
    shown_tokens = content_tokens(re.sub(r"https?://\S+", "", str(shown or "")))
    if not shown_tokens:
        return 1.0
    heard = set(content_tokens(spoken))
    return sum(token in heard for token in shown_tokens) / len(shown_tokens)
