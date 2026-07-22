class PromptBuilder:

    def build(
        self,
        memory_text: str,
        attention_text: str,
        user_input: str,
        screen_text: str = "",
    ) -> str:
        sections = []

        if attention_text.strip():
            sections.append(
                f"Relevant conversation context:\n"
                f"{attention_text.strip()}"
            )

        if memory_text.strip():
            sections.append(
                f"Relevant memories:\n"
                f"{memory_text.strip()}"
            )

        if screen_text.strip():
            sections.append(
                f"Current visual context:\n"
                f"{screen_text.strip()}"
            )

        sections.append(
            f"Current user message:\n"
            f"{user_input.strip()}"
        )

        return "\n\n".join(sections)
