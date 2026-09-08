from collections import deque


class ConversationManager:
    """The turns so far, and where the current subject started.

    The boundary is the whole reason this is not just a deque. A turn that
    changes the subject used to drop the history from its own prompt and
    leave it in place for the next one, so the conversation it had just
    walked away from came straight back:

        [Context] Starting clean: the router says the topic moved
        [Context] Inheriting the conversation (was dinner, now dinner)
        reply: I'd go with the RTX 4080 ...

    Nothing there is wrong except the scope. A topic change moves a line;
    it does not excuse one turn.
    """

    def __init__(self, max_messages: int = 20):
        self.history = deque(maxlen=max_messages)
        # How many messages at the front of the deque belong to a subject
        # the conversation has moved on from. Counted rather than deleted:
        # the transcript is still the transcript, and other layers read it.
        self._retired = 0

    def add(self, role: str, content: str) -> None:
        # The deque drops from the front when it is already full, so a
        # boundary counted in messages has to move with it or it drifts
        # backwards into turns it was never meant to cover. Measured
        # before the append, because that is when the eviction is decided:
        # testing afterwards also fires on the append that merely *fills*
        # the deque, which evicts nothing.
        evicting = len(self.history) == self.history.maxlen
        self.history.append({
            "role": role,
            "content": content,
        })
        if evicting and self._retired > 0:
            self._retired -= 1

    def start_new_subject(self) -> None:
        """Everything said so far belongs to a subject we have left."""
        self._retired = len(self.history)

    def get_history(self) -> list[dict[str, str]]:
        """The turns that belong to what is being talked about now."""
        return list(self.history)[self._retired:]

    def full_history(self) -> list[dict[str, str]]:
        """Everything, boundary ignored. For layers that audit the session."""
        return list(self.history)

    def build_messages(
        self,
        system_prompt: str,
        context_prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        messages = [
            {
                "role": "system",
                "content": system_prompt.strip(),
            }
        ]

        messages.extend(
            self.get_history() if history is None else history
        )

        messages.append({
            "role": "user",
            "content": context_prompt.strip(),
        })

        return messages

    def clear(self) -> None:
        self.history.clear()
        self._retired = 0
