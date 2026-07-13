from collections import deque


class ConversationManager:

    def __init__(self, max_messages: int = 20):
        self.history = deque(maxlen=max_messages)

    def add(self, role: str, content: str) -> None:
        self.history.append({
            "role": role,
            "content": content,
        })

    def get_history(self) -> list[dict[str, str]]:
        return list(self.history)

    def build_messages(
        self,
        system_prompt: str,
        context_prompt: str,
    ) -> list[dict[str, str]]:
        messages = [
            {
                "role": "system",
                "content": system_prompt.strip(),
            }
        ]

        messages.extend(self.get_history())

        messages.append({
            "role": "user",
            "content": context_prompt.strip(),
        })

        return messages

    def clear(self) -> None:
        self.history.clear()