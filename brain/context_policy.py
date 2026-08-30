from __future__ import annotations

import re


def _content_words(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]{4,}", str(text or "").casefold()))


def subjects_agree(grounded_subject: str, current_subject: str) -> bool:
    """Whether stored evidence is about what is being asked about now.

    Either side being unknown is not a disagreement -- there is nothing to
    contradict, and the older behaviour (include it) is preserved.
    """
    grounded = _content_words(grounded_subject)
    current = _content_words(current_subject)
    if not grounded or not current:
        return True
    return bool(grounded & current)


def should_include_grounded_context(
    *,
    has_statement: bool,
    intent: str,
    is_follow_up: bool,
    topic_shift: bool,
    grounded_subject: str = "",
    current_subject: str = "",
) -> bool:
    """Keep verified evidence scoped to its subject instead of every turn.

    Being a follow-up was the whole test, and it is not enough: "which one
    would you choose?" after a dinner answer is a follow-up, and the last
    verified thing in the session was a GPU comparison from two turns
    earlier. She answered about graphics cards. A follow-up now also has to
    be about the same subject the evidence is about.
    """
    if not has_statement:
        return False
    if not subjects_agree(grounded_subject, current_subject):
        return False
    if intent == "fact_check":
        return True
    return bool(is_follow_up and not topic_shift)
