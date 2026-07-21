import re
from elevenlabs import stream
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
from core.event_bus import Event, EventBus
from brain.text_filter import TextFilter
from tools.web_search import WebSearchTool
from config.loader import Config
from brain.personality_loader import PersonalityLoader
from datetime import datetime

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
        self.config = Config()

        self.model = self.config.get(
            "llm",
            "ollama",
            "model",
        )

        self.temperature = self.config.get(
            "llm",
            "ollama",
            "temperature",
        )

        self.client = ollama.Client(
            host=self.config.get(
                "llm",
                "ollama",
                "base_url",
            )
        )

        self.prompt_builder = PromptBuilder()
        self.personality_loader = PersonalityLoader()

        language = self.config.get(
            "language",
            "response",
        )

        self.system_prompt = self.personality_loader.load(
            language
        )

        self.memory_manager = MemoryManager()
        self.extractor = MemoryExtractor()
        self.consolidator = MemoryConsolidator()
        self.router = MemoryRouter()
        self.context_builder = ContextBuilder()
        self.conversation = ConversationManager()
        self.memory_ranker = MemoryRanker()
        self.attention = Attention()
        self.events = EventBus()

        self.web_search_tool = WebSearchTool()

        self.audio = AudioManager(
            config=self.config,
            event_bus=self.events,
        )

        self.emotion = EmotionEngine()
        
    def _print_event(self, event: Event) -> None:
        print(
            f"\n[Event] {event.name}: "
            f"{event.data}"
        )

    def on_speech_start(self) -> None:
        self.events.emit("speech_started")

        if self.audio.is_speaking():
            self.audio.stop()
    
    def chat(self, user_input):
        user_input = str(user_input).strip()

        if not user_input:
            return ""

        print(
            f"[ChatEngine] Emitting user_message: {user_input}"
        )

        self.events.emit(
            "user_message",
            text=user_input,
        )

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
        time_context = self.build_time_context()

        context_prompt = self.prompt_builder.build(
            memory_text=memory_text,
            attention_text=attention_text,
            user_input=(
                f"{time_context}\n\n"
                f"{user_input}"
            ),
        )
        ####################################################
        # Ask Qwen
        ####################################################

        messages = self.conversation.build_messages(
            system_prompt=self.system_prompt,
            context_prompt=context_prompt,
        )

        messages.append({
            "role": "system",
            "content": self.build_time_context(),
        })
        
        tool_response = self.client.chat(
            model=self.model,
            messages=messages,
            tools=[self.search_web],
            stream=False,
            options={
                "temperature": self.temperature,
            },
        )
        
        tool_calls = (
            tool_response.message.tool_calls
            if tool_response.message
            else None
        )

        if tool_calls:
            messages.append(tool_response.message)

            for tool_call in tool_calls:
                function_name = tool_call.function.name
                arguments = tool_call.function.arguments

                if function_name == "search_web":
                    query = str(
                        arguments.get("query", "")
                    ).strip()

                    try:
                        max_results = int(
                            arguments.get("max_results", 3)
                        )
                    except (TypeError, ValueError):
                        max_results = 3

                    tool_result = self.search_web(
                        query=query,
                        max_results=max_results,
                    )
                else:
                    tool_result = (
                        f"Unknown tool requested: {function_name}"
                    )

                messages.append({
                    "role": "tool",
                    "tool_name": function_name,
                    "content": tool_result,
                })

            messages.append({
                "role": "system",
                "content": (
                    "Answer directly using the supplied search information. "
                    "Use no more than two short sentences. Do not provide URLs, "
                    "tell the user to visit a website, or list unrelated meanings."
                ),
            })

        stream = self.client.chat(
            model=self.model,
            messages=messages,
            stream=True,
            options={
                "temperature": self.temperature,
            },
        )

        reply = ""
        speech_buffer = ""
        tts_buffer = ""
        tts_sentence_count = 0

        # Tell Electron to create a new empty assistant bubble.
        print("[ChatEngine] Emitting assistant_started")

        self.events.emit(
            "assistant_started"
        )

        print(
            "\nElaina: ",
            end="",
            flush=True,
        )

        for chunk in stream:
            if "message" not in chunk:
                continue

            content = chunk["message"].get(
                "content",
                "",
            )

            content = TextFilter.clean(content)

            if not content:
                continue

            print(
                content,
                end="",
                flush=True,
            )

            reply += content
            speech_buffer += content

            print("[ChatEngine] Emitting assistant_finished")

            # Send this exact streamed chunk to Electron.
            self.events.emit(
                "assistant_stream",
                text=content,
            )

            complete_sentences, speech_buffer = (
                extract_complete_sentences(
                    speech_buffer
                )
            )

            for sentence in complete_sentences:
                tts_buffer += " " + sentence
                tts_sentence_count += 1

                if (
                    tts_sentence_count >= 2
                    or len(tts_buffer) >= 180
                ):
                    self.audio.speak(
                        tts_buffer.strip()
                    )

                    tts_buffer = ""
                    tts_sentence_count = 0

        print()

        # The LLM has finished generating its response.
        self.events.emit(
            "assistant_finished",
            text=reply,
        )

        # Speak any remaining text that did not end in punctuation.
        remaining_text = speech_buffer.strip()

        if remaining_text:
            tts_buffer += " " + remaining_text

        final_tts_text = tts_buffer.strip()

        if final_tts_text:
            self.audio.speak(
                final_tts_text
            )

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

        self.events.emit(
            "emotion_changed",
            emotion=emotion_state.name,
            intensity=emotion_state.intensity,
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
    
    def search_web(
        self,
        query: str,
        max_results: int = 5,
    ) -> str:
        """
        Search the web for current or recently changing information.

        Use this tool for news, current events, prices, recent software
        versions, schedules, sports results, current company leaders,
        or any information that may have changed recently.

        Args:
            query: A focused web-search query.
            max_results: Number of results to retrieve.

        Returns:
            Current web-search results.
        """
        print(f"\n[Tool] Searching web for: {query}")

        if hasattr(self, "events"):
            self.events.emit(
                "tool_started",
                tool="web_search",
                query=query,
            )

        result = self.web_search_tool.search_web(
            query=query,
            max_results=max_results,
        )

        if hasattr(self, "events"):
            self.events.emit(
                "tool_finished",
                tool="web_search",
                query=query,
            )

        return result
    
    def build_time_context(self) -> str:
        now = datetime.now()

        return (
            f"Today is {now.strftime('%A, %B %d, %Y')}.\n"
            f"The current local time is {now.strftime('%I:%M %p')}.\n"
            f"The current year is {now.year}."
        )