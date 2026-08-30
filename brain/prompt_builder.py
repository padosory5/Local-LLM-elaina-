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
            "only when it is relevant.\n"
            # The factual path has carried this clause all along; the
            # conversation path did not, and that is the path a short reply
            # like "that sounds good" or "no thanks" takes. With little
            # content of its own, the previous assistant turn was the
            # strongest thing in the prompt and came back verbatim.
            "Do not repeat your previous answer merely because it appears "
            "above. A short reply such as \"that sounds good\", \"no "
            "thanks\" or \"okay\" is a reaction to what you just said: "
            "acknowledge it briefly and move the conversation on, rather "
            "than saying the same thing again."
        )

        return "\n\n".join(sections)
