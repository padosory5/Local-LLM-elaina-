class PromptBuilder:

    def build(
        self,
        system_prompt,
        memory_text,
        attention_text,
        user_input
    ):

        prompt = f"""
System

{system_prompt}

----------------------------------------

Attention

{attention_text}

----------------------------------------

Memory

{memory_text}

----------------------------------------

User

{user_input}
"""

        return prompt.strip()