import json
import re

import ollama
from config.loader import Config

SYSTEM_PROMPT = """
You are a memory consolidation system. You are given the memories already
kept about one person, and one new memory about them. Decide what to do
with the new one.

ADD when it says something the existing memories do not. This is the
default. A fact kept twice costs almost nothing -- the profile shows a
near-duplicate once -- and a fact dropped here is gone for good.

UPDATE when the new memory is the same fact with a different value: they
moved, changed course, changed their mind. Name the id it replaces.

IGNORE only when an existing memory already states the same fact.

A memory written in another language is NOT a duplicate. The person speaks
Korean and English, and their memories are written in whichever language
they said them in: "사용자는 워싱턴 대학교에서 컴퓨터공학을 전공합니다" is a
new fact next to "The user's name is Minjun Park", not a repetition of it.

Return ONLY one of these:

{"action":"ADD"}

{"action":"IGNORE"}

{"action":"UPDATE","memory_id":5,"content":"updated memory"}
"""


# How every memory starts, in both languages, plus the words that carry no
# fact. Two memories about one person share these and nothing else.
_BOILERPLATE = {
    "the", "user", "users", "user's", "is", "are", "was", "were", "has",
    "have", "had", "does", "and", "for", "with", "that", "this", "their",
    "them", "they", "사용자", "사용자는", "사용자의", "사용자가",
}
_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z']{1,}|\d[\d,.]*")


def _facts(text: str) -> set[str]:
    """What a memory actually says, as comparable words: Korean by its first
    two syllables so particles do not matter, English lowercased."""
    found = set()
    for word in _WORD.findall(str(text or "")):
        if "가" <= word[0] <= "힣":
            if word not in _BOILERPLATE:
                found.add(word[:2])
        elif word.casefold() not in _BOILERPLATE:
            found.add(word.casefold())
    return found


def same_fact(existing: str, new: str) -> bool:
    """Whether these two memories are about the same thing.

    The consolidator is a model call in the *write* path, and its UPDATE
    overwrites a row. Measured: it answered UPDATE for "사용자는 수업이
    끝나면 보통 젠레스 존 제로를 합니다" against "사용자는 워싱턴 대학교에서
    컴퓨터공학을 전공하고 있습니다" -- two unrelated facts that share only
    the word for "the user" -- and the education memory was gone, its row
    now holding the game and still labelled education.

    So the model may propose and this decides: an overwrite needs the two
    to share most of what they say, not merely who they are about.
    """
    before, after = _facts(existing), _facts(new)
    if not before or not after:
        return False
    shared = before & after
    return len(shared) * 2 >= min(len(before), len(after))


class MemoryConsolidator:

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self.model = self.config.get("llm", "ollama", "model")
        self.client = ollama.Client(
            host=self.config.get("llm", "ollama", "base_url")
        )

    def consolidate(self, similar_memories, new_memory):

        memories = []

        for memory in similar_memories:

            memories.append({
                "id": memory.id,
                "content": memory.content
            })

        prompt = {
            "existing_memories": memories,
            "new_memory": new_memory
        }

        response = self.client.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, indent=4)
                }
            ],
            format="json",
            options={"temperature": 0},
        )

        return self.read(response)

    @staticmethod
    def read(response) -> dict:
        """The verdict, and ADD for anything that cannot be read.

        Measured: a Korean memory among English ones came back IGNORE and
        the fact was dropped in silence -- "나 워싱턴 대학교에서 컴퓨터공학
        전공하고 있어" was extracted correctly, consolidated away, and the
        restart afterwards could not say which school. Losing a fact is the
        expensive mistake here; keeping one twice is not.
        """
        try:
            payload = json.loads(response["message"]["content"])
        except (TypeError, ValueError, KeyError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        action = str(payload.get("action") or "").strip().upper()
        if action == "UPDATE":
            memory_id = payload.get("memory_id")
            content = str(payload.get("content") or "").strip()
            if memory_id is not None and content:
                return {
                    "action": "UPDATE",
                    "memory_id": memory_id,
                    "content": content,
                }
            return {"action": "ADD"}
        if action == "IGNORE":
            return {"action": "IGNORE"}
        return {"action": "ADD"}
