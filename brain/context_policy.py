from __future__ import annotations


def should_include_grounded_context(
    *,
    has_statement: bool,
    intent: str,
    is_follow_up: bool,
    topic_shift: bool,
) -> bool:
    """Keep verified evidence scoped to its subject instead of every turn."""
    if not has_statement:
        return False
    if intent == "fact_check":
        return True
    return bool(is_follow_up and not topic_shift)
