import json
import re

import ollama
from config.loader import Config


SYSTEM_PROMPT = """
You are a memory extraction system.

Return ONLY valid JSON.

If nothing should be remembered, return:

{
  "save": false,
  "content": "",
  "category": "general"
}

If the message contains useful long-term information, return:

{
  "save": true,
  "content": "A concise third-person memory about the user.",
  "category": "personal"
}

Allowed categories:

personal
education
project
goal
preference
relationship
general

Examples:

User:
Hello

Output:
{
  "save": false,
  "content": "",
  "category": "general"
}

User:
My name is Aiden.

Output:
{
  "save": true,
  "content": "The user's name is Aiden.",
  "category": "personal"
}

User:
I study Electrical Engineering.

Output:
{
  "save": true,
  "content": "The user studies Electrical Engineering.",
  "category": "education"
}

User:
I am building a local AI assistant.

Output:
{
  "save": true,
  "content": "The user is building a local AI assistant.",
  "category": "project"
}

Return JSON only. Do not use Markdown code blocks.
"""


class MemoryExtractor:

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.model = self.config.get("llm", "ollama", "model")
        self.client = ollama.Client(
            host=self.config.get("llm", "ollama", "base_url")
        )

    def extract(self, user_message: str) -> dict:

        default_result = {
            "save": False,
            "content": "",
            "category": "general"
        }

        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": user_message
                    }
                ],
                format="json"
            )

            raw_output = response["message"]["content"].strip()

            if not raw_output:
                return default_result

            # Remove Markdown fences if the model adds them anyway.
            raw_output = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                raw_output,
                flags=re.IGNORECASE
            ).strip()

            result = json.loads(raw_output)

            save = bool(result.get("save", False))
            content = str(result.get("content", "")).strip()
            category = str(
                result.get("category", "general")
            ).strip().lower()

            allowed_categories = {
                "personal",
                "education",
                "project",
                "goal",
                "preference",
                "relationship",
                "general"
            }

            if category not in allowed_categories:
                category = "general"

            if not save or not content:
                return default_result

            return {
                "save": True,
                "content": content,
                "category": category
            }

        except (
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ollama.ResponseError
        ) as error:

            print(f"\n[Memory Extractor Warning] {error}")

            return default_result

        except Exception as error:

            print(f"\n[Memory Extractor Error] {error}")

            return default_result
