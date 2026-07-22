import json
import ollama
from config.loader import Config

SYSTEM_PROMPT = """
You are a memory consolidation AI.

You must ALWAYS return valid JSON.

Return ONLY one of these formats.

ADD

{
    "action":"ADD"
}

IGNORE

{
    "action":"IGNORE"
}

UPDATE

{
    "action":"UPDATE",
    "memory_id":5,
    "content":"updated memory"
}

Return JSON only.
"""


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
            ]
        )

        return json.loads(
            response["message"]["content"]
        )
