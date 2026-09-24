from __future__ import annotations

from collections.abc import Sequence


def retry_language_for_detection(
    *,
    configured_language: str | None,
    detected_language: str,
    probability: float,
    allowed_languages: Sequence[str],
    minimum_probability: float,
    preferred_language: str | None = None,
) -> str | None:
    """Return a constrained retry language for unreliable auto-detection.

    ``preferred_language`` is the language the conversation is in. When the
    detection is uncertain, or lands outside the allowed set, that is the
    better guess: without it a short Korean clip read as Japanese was
    re-transcribed as *English* (the first allowed language), which turns
    Korean speech into nonsense. A confident detection is never overruled,
    so switching language mid-conversation still works.
    """
    if configured_language is not None or not allowed_languages:
        return None

    normalized_allowed = tuple(
        str(language).strip().lower()
        for language in allowed_languages
        if str(language).strip()
    )
    detected = detected_language.strip().lower()
    if detected in normalized_allowed and probability >= minimum_probability:
        return None
    preferred = str(preferred_language or "").strip().lower()[:2]
    if preferred in normalized_allowed:
        return preferred
    if detected in normalized_allowed:
        return detected
    return normalized_allowed[0] if normalized_allowed else None


import re

# What Whisper says over silence -- subtitle endings it learned from video,
# in the languages it tends to guess for a quiet clip. Measured with
# large-v3-turbo on near-silence: "Obrigado.", "Gracias.", "Thank you.",
# "You", and after a retry in Korean, "감사합니다.".
_SILENCE_PHRASES = frozenset({
    "감사합니다", "고맙습니다", "시청해주셔서감사합니다", "시청해주셔서고맙습니다",
    "구독과좋아요", "구독과좋아요부탁드립니다", "좋아요와구독부탁드립니다",
    "thankyou", "thanks", "thanksforwatching", "thankyouforwatching", "you",
    "obrigado", "obrigada", "gracias", "merci", "ご視聴ありがとうございました",
})


def reads_as_silence(
    text: str,
    *,
    detected_language: str,
    probability: float,
    most_likely_speech: float = 0.35,
) -> bool:
    """Whether a transcript is Whisper's habit on silence, not something said.

    Both halves are needed. The phrase alone is not enough: people really
    do say 감사합니다, and a real one is read as Korean at 0.99 or more. The
    uncertainty alone is not enough either: a real "고마워" was read as
    Korean at only 0.19. What silence does is both at once -- a guess at a
    language (Portuguese at 0.19, English at 0.28) and one of the phrases
    it ends videos with.

    The no-speech probability cannot be used for this with large-v3-turbo:
    it read 0.00 for silence and for speech alike.
    """
    bare = re.sub(r"[\s.,!?~…'\"-]+", "", str(text or "")).casefold()
    if not bare or bare not in _SILENCE_PHRASES:
        return False
    return float(probability or 0.0) < most_likely_speech


def heard_unclearly(
    word_probabilities: Sequence[float],
    *,
    lowest_below: float = 0.30,
    average_below: float = 0.65,
) -> bool:
    """Whether a transcript is too uncertain to act on as it stands.

    Measured with large-v3-turbo on 112 clips (sentences and short replies,
    clean and at 10 / 5 / 3 dB), against the character error of each: the
    lowest word probability separated garbled transcripts from good ones
    best, and the sentence's average log probability hardly at all. Both
    halves are needed. One doubtful word in a sound sentence is a misheard
    name -- "빈천공항에서 미국 CLT까지" -- which brain/near_miss.py repairs
    better than asking would; a whole sentence of them is noise that
    decoded into words nobody said.

    A reply of one word is never judged here: a real "네" scored 0.41, and
    "잘 못 들었습니다" in answer to a yes is worse than any garbled word.
    """
    probabilities = [float(p) for p in word_probabilities if p is not None]
    if len(probabilities) < 2:
        return False
    average = sum(probabilities) / len(probabilities)
    return min(probabilities) < lowest_below and average < average_below


def segment_is_usable(
    *,
    no_speech_probability: float,
    average_log_probability: float,
    no_speech_threshold: float,
    log_probability_threshold: float,
) -> bool:
    """Reject a segment only when both silence and poor decoding agree."""
    return not (
        no_speech_probability > no_speech_threshold
        and average_log_probability < log_probability_threshold
    )
