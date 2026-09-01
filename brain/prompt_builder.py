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
            "above. If your previous message asked a question, treat a short "
            "current reply as its answer: acknowledge what the person said "
            "and progress instead of asking the same question again. A plain "
            "negative answer is not a courtesy or a request to end the "
            "conversation. Do not expand it into a phrase the person did not "
            "say. Do not begin by restating or paraphrasing the current "
            "message; respond with the reaction, answer, or next useful point. "
            "For a simple greeting, use one short casual sentence and do not "
            "advertise services, tools, or locations."
        )

        return "\n\n".join(sections)
