import os
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

Keep only what stays true.

A request, a question, or what they want right now is not part of the fact.
"By the way I'm vegetarian. What's a quick dinner I can make?" holds one
memory -- that they are vegetarian -- and the dinner belongs to that turn,
not to who they are. Measured: kept as "The user is vegetarian and is
looking for a quick dinner idea", it answered a question about their
university two turns later with a dinner suggestion.

Write the memory in the language the person used.

Keep every name, title, place and brand exactly as they wrote it. Never
romanize it and never translate it: "젠레스 존 제로" is not "Genres Zero",
and 콩 is not "Kongi". They will ask about it again in their own words, and
a name that was translated on the way in cannot be found again or said back
to them. When the sentence is not English you may add a short English gloss
in brackets after a name -- never instead of it.

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

User:
By the way I'm vegetarian. What's a quick dinner I can make?

Output:
{
  "save": true,
  "content": "The user is vegetarian.",
  "category": "preference"
}

User:
우리 집 강아지 이름은 콩이야

Output:
{
  "save": true,
  "content": "사용자의 강아지 이름은 콩입니다.",
  "category": "personal"
}

User:
나 수업 끝나고 보통 젠레스 존 제로 해

Output:
{
  "save": true,
  "content": "사용자는 수업이 끝나면 보통 젠레스 존 제로를 합니다.",
  "category": "personal"
}

Return JSON only. Do not use Markdown code blocks.
"""


# Said once more, when the first answer ignored it.
INSIST = """

The message you were just given is NOT in English. Write the memory in that
same language. Keep every name exactly as it was written -- do not
translate it, do not romanize it, and do not replace it with a name that
looks similar in English.
"""

_HANGUL = re.compile(r"[가-힣]")


def wrong_language(said: str, content: str) -> bool:
    """Whether a Korean sentence came back as an English memory."""
    if not _HANGUL.search(str(said or "")):
        return False
    return not _HANGUL.search(str(content or ""))


class MemoryExtractor:

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        # ELAINA_MODEL: the same override ChatEngine honours, so an
        # evaluation arm running one model never loads a second one here.
        self.model = os.environ.get("ELAINA_MODEL") or self.config.get("llm", "ollama", "model")
        # The same context window as every other caller of this model,
        # or Ollama reloads it between them (core/model_context.py).
        from core import model_context

        self.client = model_context.ContextSizedClient(
            ollama.Client(host=self.config.get("llm", "ollama", "base_url")),
            model_context.configured(self.config),
        )

    def extract(self, user_message: str) -> dict:
        """One extraction, checked for the language it came back in.

        The prompt says to write in the language they used, and one run in
        two it did not: "나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어" came
        back as "The user is majoring in Computer Engineering at Washington
        University" -- the wrong language and, worse, a different
        university. So the rule is checked here rather than hoped for: ask
        once more, and if it still comes back in English keep their own
        sentence, which is at least what they said.
        """
        result = self._extract_once(user_message)
        if not result["save"] or not wrong_language(user_message, result["content"]):
            return result
        print("[Memory] The extraction came back in the wrong language; "
              "asking once more.")
        second = self._extract_once(user_message, insist=True)
        if second["save"] and not wrong_language(user_message, second["content"]):
            return second
        print("[Memory] Still the wrong language; keeping their own words.")
        return {**result, "content": " ".join(str(user_message).split())}

    def _extract_once(self, user_message: str, *, insist: bool = False) -> dict:

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
                        "content": SYSTEM_PROMPT + (INSIST if insist else "")
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
