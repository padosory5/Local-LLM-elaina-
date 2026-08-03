from __future__ import annotations

from collections.abc import Sequence


def retry_language_for_detection(
    *,
    configured_language: str | None,
    detected_language: str,
    probability: float,
    allowed_languages: Sequence[str],
    minimum_probability: float,
) -> str | None:
    """Return a constrained retry language for unreliable auto-detection."""
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
    if detected in normalized_allowed:
        return detected
    return normalized_allowed[0] if normalized_allowed else None


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
