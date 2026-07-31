from __future__ import annotations

import re
from difflib import SequenceMatcher


class ResponseQualityGuard:
    """Detect a draft that simply repeats an unrelated previous answer."""

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(
            re.sub(r"[^\w\s]", " ", str(text).lower()).split()
        )

    @classmethod
    def should_retry(
        cls,
        reply: str,
        current_user: str,
        history: list[dict[str, str]],
    ) -> bool:
        if not reply.strip() or len(history) < 2:
            return False

        previous_user = next(
            (
                item.get("content", "")
                for item in reversed(history)
                if item.get("role") == "user"
            ),
            "",
        )
        previous_assistant = next(
            (
                item.get("content", "")
                for item in reversed(history)
                if item.get("role") == "assistant"
            ),
            "",
        )
        if not previous_user or not previous_assistant:
            return False

        reply_similarity = SequenceMatcher(
            None,
            cls._normalize(reply),
            cls._normalize(previous_assistant),
        ).ratio()
        user_similarity = SequenceMatcher(
            None,
            cls._normalize(current_user),
            cls._normalize(previous_user),
        ).ratio()

        normalized_reply = cls._normalize(reply)
        generic_social_reply = bool(re.search(
            r"^(?:hey|hi|hello|morning|good morning|good evening)\b.*"
            r"(?:what s up|how are you|what s going on|what s on your mind)",
            normalized_reply,
        ))

        # Repeating an answer is fine when the user actually repeated the same
        # message. It is a failure when the new user turn changed substantially.
        return bool(
            generic_social_reply
            and reply_similarity >= 0.90
            and user_similarity < 0.72
        )
