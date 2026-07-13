from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class EmotionState:
    name: str
    intensity: float


class EmotionEngine:
    def __init__(self) -> None:
        self.current = EmotionState(
            name="neutral",
            intensity=0.3,
        )

    def analyze(
        self,
        user_input: str,
        reply: str,
    ) -> EmotionState:
        combined = f"{user_input} {reply}".lower()

        emotion_scores = {
            "happy": self._count_matches(
                combined,
                [
                    r"\bhappy\b",
                    r"\bglad\b",
                    r"\bgreat\b",
                    r"\bwonderful\b",
                    r"\bcongratulations\b",
                    r"\bnice\b",
                    r"\bawesome\b",
                ],
            ),
            "excited": self._count_matches(
                combined,
                [
                    r"\bexcited\b",
                    r"\bamazing\b",
                    r"\bincredible\b",
                    r"\bhuge milestone\b",
                    r"\bexcellent\b",
                ],
            ),
            "curious": self._count_matches(
                combined,
                [
                    r"\binteresting\b",
                    r"\bcurious\b",
                    r"\bwonder\b",
                    r"\bwhy\b",
                    r"\bhow\b",
                    r"\bwhat if\b",
                ],
            ),
            "concerned": self._count_matches(
                combined,
                [
                    r"\bworried\b",
                    r"\bconcerned\b",
                    r"\bcareful\b",
                    r"\bproblem\b",
                    r"\bissue\b",
                    r"\bdanger\b",
                    r"\bwarning\b",
                ],
            ),
            "sad": self._count_matches(
                combined,
                [
                    r"\bsad\b",
                    r"\bsorry\b",
                    r"\bupset\b",
                    r"\blonely\b",
                    r"\bhurt\b",
                    r"\bunfortunately\b",
                ],
            ),
        }

        emotion = max(
            emotion_scores,
            key=emotion_scores.get,
        )

        highest_score = emotion_scores[emotion]

        if highest_score == 0:
            state = EmotionState(
                name="neutral",
                intensity=0.3,
            )
        else:
            intensity = min(
                1.0,
                0.45 + highest_score * 0.15,
            )

            state = EmotionState(
                name=emotion,
                intensity=intensity,
            )

        self.current = state
        return state

    def reset(self) -> None:
        self.current = EmotionState(
            name="neutral",
            intensity=0.3,
        )

    @staticmethod
    def _count_matches(
        text: str,
        patterns: list[str],
    ) -> int:
        return sum(
            len(re.findall(pattern, text))
            for pattern in patterns
        )