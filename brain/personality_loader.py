from pathlib import Path


class PersonalityLoader:

    def __init__(self):
        self.directory = Path(__file__).parent

    def load(
        self,
        language: str,
    ) -> str:

        filename = f"personality_{language}.txt"

        path = self.directory / filename

        if not path.exists():
            raise FileNotFoundError(
                f"Personality file not found: {path}"
            )

        return path.read_text(
            encoding="utf-8",
        ).strip()