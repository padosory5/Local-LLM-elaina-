from __future__ import annotations

from collections.abc import Iterable


def build_personality_messages(
    *,
    system_prompt: str,
    history: list[dict[str, str]],
    user_input: str,
    context_sections: Iterable[tuple[str, str]] = (),
) -> list[dict[str, str]]:
    """Build a final-answer prompt with personality as its only system text."""
    sections = [
        f"{label.strip()}\n{content.strip()}"
        for label, content in context_sections
        if content.strip()
    ]
    sections.append(
        "CURRENT USER MESSAGE\n"
        f"{user_input.strip()}"
    )

    return [
        {
            "role": "system",
            "content": system_prompt.strip(),
        },
        *history,
        {
            "role": "user",
            "content": "\n\n".join(sections),
        },
    ]
