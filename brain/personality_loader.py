from pathlib import Path


class PersonalityLoader:
    def __init__(self) -> None:
        self.path = (
            Path(__file__).resolve().parent
            / "personality.txt"
        )

    def load(self) -> str:
        if not self.path.is_file():
            raise FileNotFoundError(
                f"Personality file not found: {self.path}"
            )

        return self.path.read_text(
            encoding="utf-8",
        ).strip()