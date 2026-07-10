import ollama

from memory.memory_manager import MemoryManager
from memory.extractor import MemoryExtractor
from memory.consolidator import MemoryConsolidator
from memory.router import MemoryRouter
from memory.context_builder import ContextBuilder
from brain.prompt_builder import PromptBuilder
from brain.conversation_manager import ConversationManager
from brain.memory_ranker import MemoryRanker
from brain.attention import Attention
from voice.manager import VoiceManager

class ChatEngine:

    def __init__(self):
        self.prompt_builder = PromptBuilder()
        self.client = ollama.Client()
        self.memory_manager = MemoryManager()
        self.extractor = MemoryExtractor()
        self.consolidator = MemoryConsolidator()
        self.router = MemoryRouter()
        self.context_builder = ContextBuilder()
        self.conversation = ConversationManager()
        self.memory_ranker = MemoryRanker()
        self.attention = Attention()
        self.voice = VoiceManager()

        self.system_prompt = """
You are Elaina.

You are a warm, friendly and intelligent AI.

Speak naturally.

Most replies should be one or two sentences.

Don't mention memories unless they're useful.

Don't over-explain.

Only give detailed answers if the user asks.

Sound like a real person.

Never use emojis.

Reply using plain text only.
"""

    def chat(self, user_input):
        
        self.attention.update(user_input)

        ####################################################
        # Retrieve Memories
        ####################################################

        memory_text = ""

        attention_text = self.attention.build_context()

        use_memory = self.router.should_use_memory(user_input)

        if use_memory:

            memories = self.memory_manager.search(
                user_input,
                k=20
            )
            
            memories = self.memory_ranker.rank(
                memories
            )

            memory_text = self.context_builder.build(memories)

        ####################################################
        # Build Prompt
        ####################################################

        prompt = self.prompt_builder.build(
            self.system_prompt,
            memory_text,
            attention_text,
            user_input
        )

        self.conversation.add(
            "user",
            prompt
        )

        ####################################################
        # Ask Qwen
        ####################################################

        stream = self.client.chat(
            model="qwen3:8b",
            messages=self.conversation.get_history(),
            stream=True
        )

        reply = ""


        print("\nElaina: ", end="", flush=True)

        for chunk in stream:

            if "message" not in chunk:
                continue

            content = chunk["message"]["content"]

            print(content, end="", flush=True)

            reply += content

        print()

        self.conversation.add(
            "assistant",
            reply
        )

        self.voice.speak(reply)

        ####################################################
        # Store Memory
        ####################################################

        memory = self.extractor.extract(user_input)

        if memory["save"]:

            similar = self.memory_manager.search_memory_objects(
                memory["content"]
            )

            result = self.consolidator.consolidate(
                similar,
                memory["content"]
            )

            action = result["action"]

            if action == "ADD":

                self.memory_manager.store_memory(
                    content=memory["content"],
                    category=memory["category"],
                    importance=5
                )

            elif action == "UPDATE":

                self.memory_manager.update_memory(
                    result["memory_id"],
                    result["content"]
                )

        return reply