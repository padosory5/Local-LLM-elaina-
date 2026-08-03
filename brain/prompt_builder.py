class PromptBuilder:

    def build(
        self,
        memory_text: str,
        user_input: str,
        screen_text: str = "",
    ) -> str:
        sections = []

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
            f"{user_input.strip()}\n"
            "Respond to this message, not an older turn. Use earlier context "
            "only when it is relevant."
        )

        return "\n\n".join(sections)
