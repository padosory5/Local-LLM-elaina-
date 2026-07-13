import re
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
from voice.audio_manager import AudioManager
from brain.emotion_engine import EmotionEngine


def extract_complete_sentences(
    buffer: str,
) -> tuple[list[str], str]:
    sentences: list[str] = []

    pattern = re.compile(
        r'(.+?[.!?]+(?:["\')\]]+)?)(?=\s|$)',
        re.DOTALL,
    )

    while True:
        match = pattern.match(buffer)

        if match is None:
            break

        sentence = match.group(1).strip()

        if sentence:
            sentences.append(sentence)

        buffer = buffer[match.end():].lstrip()

    return sentences, buffer


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
        self.audio = AudioManager()
        self.emotion = EmotionEngine()

        self.system_prompt = """
You are Elaina.

You are a warm, friendly and intelligent AI.

Speak naturally.

Most replies should be one or two sentences.

Don't mention memories unless they're useful.

Don't over-explain.

Only give detailed answers if the user asks.

Sound like a real person.

Never use emojis, emoticons, decorative symbols, or reaction icons.

Use plain text only.

Do not include emoji characters even if the user uses them.
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

        context_prompt = self.prompt_builder.build(
            memory_text=memory_text,
            attention_text=attention_text,
            user_input=user_input,
        )

        ####################################################
        # Ask Qwen
        ####################################################

        messages = self.conversation.build_messages(
            system_prompt=self.system_prompt,
            context_prompt=context_prompt,
        )

        stream = self.client.chat(
            model="qwen3:8b",
            messages=messages,
            stream=True,
        )

        reply = ""
        speech_buffer = ""

        print("\nElaina: ", end="", flush=True)

        for chunk in stream:
            if "message" not in chunk:
                continue

            content = chunk["message"].get("content", "")

            if not content:
                continue

            print(content, end="", flush=True)

            reply += content
            speech_buffer += content

            complete_sentences, speech_buffer = (
                extract_complete_sentences(speech_buffer)
            )

            for sentence in complete_sentences:
                self.audio.speak(sentence)

        print()

        # Speak any remaining text that did not end in punctuation.
        remaining_text = speech_buffer.strip()

        if remaining_text:
            self.audio.speak(remaining_text)


        self.conversation.add(
            "user",
            user_input,
        )

        self.conversation.add(
            "assistant",
            reply
        )

        emotion_state = self.emotion.analyze(
            user_input=user_input,
            reply=reply,
        )

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