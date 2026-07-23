import re
import threading
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
from vision.screen_monitor import ScreenMonitor

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

        self.keep_alive = self.config.get(
            "llm",
            "ollama",
            "keep_alive",
            default=-1,
            required=False,
        )

        self.vision_model = self.config.get(
            "vision",
            "model",
            default="qwen3-vl:8b",
            required=False,
        )

        self.vision_keep_alive = self.config.get(
            "vision",
            "keep_alive",
            default=0,
            required=False,
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
        self.extractor = MemoryExtractor(config=self.config)
        self.consolidator = MemoryConsolidator(config=self.config)
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

        self.screen_monitor = ScreenMonitor(self.config)
        self.screen_monitor.start()

        # A selection made in Electron is held only until the user's next
        # spoken message. The image remains in memory and is never saved.
        self._pending_screen_lock = threading.Lock()
        self._pending_screen_snapshot = None
        
    def _print_event(self, event: Event) -> None:
        print(
            f"\n[Event] {event.name}: "
            f"{event.data}"
        )

    def on_speech_start(self) -> None:
        self.events.emit("speech_started")

        if self.audio.is_speaking():
            self.audio.stop()
    
    def chat(
        self,
        user_input,
        screen_region=None,
        screen_snapshot=None,
    ):
        user_input = str(user_input).strip()

        if not user_input:
            return ""

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

        use_screen_vision = (
            screen_region is not None
            or screen_snapshot is not None
            or self._should_use_screen_vision(user_input)
        )
        screen_target = self._select_screen_target(user_input)
        if screen_snapshot is not None:
            pass
        elif screen_region is not None:
            screen_snapshot = self.screen_monitor.capture_region(
                screen_region
            )
        elif use_screen_vision:
            screen_snapshot = self.screen_monitor.capture_now(
                screen_target
            )
        else:
            screen_snapshot = None
        screen_context = (
            self._build_screen_context(screen_snapshot)
            if use_screen_vision
            else ""
        )

        context_prompt = self.prompt_builder.build(
            memory_text=memory_text,
            attention_text=attention_text,
            screen_text=screen_context,
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

        # Old screenshots never enter conversation history. Only this turn's
        # latest in-memory frame is sent to Ollama.
        if screen_snapshot is not None:
            messages[-1]["images"] = [screen_snapshot.image_bytes]

        messages.append({
            "role": "system",
            "content": self.build_time_context(),
        })
        
        # A simple relevance check avoids a complete extra LLM generation on
        # every turn. Only clearly time-sensitive questions perform web search.
        if not use_screen_vision and self._should_search_web(user_input):
            search_result = self.search_web(
                query=user_input,
                max_results=3,
            )
            messages.append({
                "role": "system",
                "content": (
                    "Use this current web-search information when answering:\n"
                    f"{search_result}\n\n"
                    "Answer directly in no more than two short sentences. "
                    "Do not provide raw URLs."
                ),
            })

        active_model = self.vision_model if use_screen_vision else self.model
        active_keep_alive = (
            self.vision_keep_alive if use_screen_vision else self.keep_alive
        )

        # Notify the UI before waiting for Ollama's first token.
        print("[ChatEngine] Emitting assistant_started")
        self.events.emit("assistant_started")

        print(
            "\nElaina: ",
            end="",
            flush=True,
        )

        reply = ""
        speech_buffer = ""
        tts_buffer = ""
        tts_sentence_count = 0

        def stream_answer(*, allow_thinking: bool) -> None:
            """Stream one Ollama response into this turn's output buffers."""
            nonlocal reply, speech_buffer, tts_buffer, tts_sentence_count

            response_stream = self.client.chat(
                model=active_model,
                messages=messages,
                stream=True,
                options={
                    "temperature": self.temperature,
                },
                keep_alive=active_keep_alive,
                think=allow_thinking,
            )

            for chunk in response_stream:
                message = chunk.get("message")
                if not message:
                    continue

                content = message.get("content", "")
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

        try:
            # Fast path: ask Ollama to suppress the model's reasoning tokens.
            stream_answer(allow_thinking=False)

            # Some Ollama/Qwen3-VL combinations return an empty content stream
            # when thinking is disabled. Retry only that failed vision request
            # with thinking enabled; reasoning stays hidden because we read only
            # the final `content` field.
            if use_screen_vision and not reply.strip():
                print(
                    "\n[Vision] The first response was empty; retrying in "
                    "compatibility mode..."
                )
                print("Elaina: ", end="", flush=True)
                stream_answer(allow_thinking=True)

        except Exception as error:
            print(f"\n[Vision/LLM Error] {type(error).__name__}: {error}")

        # Never silently return to microphone listening after a failed request.
        if not reply.strip():
            if use_screen_vision:
                reply = (
                    "I couldn't analyze the screen. Please check that the "
                    f"Ollama model '{self.vision_model}' is installed and "
                    "supports images."
                )
            else:
                reply = "I couldn't generate a response. Please try again."

            print(
                reply,
                end="",
                flush=True,
            )
            self.events.emit(
                "assistant_stream",
                text=reply,
            )
            speech_buffer = reply

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

    def prepare_screen_region(self, region: dict) -> bool:
        """Capture a selected region and hold it for the next spoken message."""
        snapshot = self.screen_monitor.capture_region(region)

        if snapshot is None:
            self.events.emit(
                "screen_region_error",
                text="Could not capture the selected area.",
            )
            return False

        with self._pending_screen_lock:
            self._pending_screen_snapshot = snapshot

        self.events.emit("screen_region_ready")
        return True

    def consume_pending_screen_snapshot(self):
        """Return and clear the image waiting for the next user question."""
        with self._pending_screen_lock:
            snapshot = self._pending_screen_snapshot
            self._pending_screen_snapshot = None

        return snapshot

    def _build_screen_context(self, snapshot) -> str:
        if snapshot is None:
            return "Screen capture is enabled, but no frame is available yet."

        title = snapshot.active_window_title or "Unknown"

        return (
            f"A current screenshot of the user's {snapshot.capture_target} is "
            "attached to this "
            "message. Use it naturally when the question refers to what the "
            "user is viewing, watching, reading, playing, or doing. Do not "
            "mention the screenshot unless it is relevant.\n"
            f"Active window title: {title}"
        )

    def _should_use_screen_vision(self, user_input: str) -> bool:
        """Give explicit references to visible desktop content top priority."""
        normalized = " ".join(user_input.lower().split())

        # Screen nouns are the strongest signal. This catches wording such as
        # "on my left screen" without requiring every possible full sentence.
        if re.search(r"\b(screens?|monitors?|desktop|windows?)\b", normalized):
            return True

        visual_phrases = (
            "what am i watching",
            "what am i looking at",
            "what is on my screen",
            "what's on my screen",
            "look at my screen",
            "look at this",
            "can you see my screen",
            "can you see this",
            "read my screen",
            "read this screen",
            "read this page",
            "explain this screen",
            "explain this error",
            "what does this error",
            "what video am i watching",
            "what game am i playing",
            "what page am i on",
            "what do you think about this",
            "tell me about what you see",
        )

        return any(phrase in normalized for phrase in visual_phrases)

    def _select_screen_target(self, user_input: str) -> str:
        """Translate natural monitor wording into a ScreenMonitor target."""
        normalized = " ".join(user_input.lower().split())

        if re.search(
            r"\b(all|both|entire|whole)\b.*"
            r"\b(screens?|monitors?|desktop)\b",
            normalized,
        ):
            return "all"
        if re.search(r"\bleft(?:most)?\b.*\b(screen|monitor)\b", normalized):
            return "left"
        if re.search(r"\bright(?:most)?\b.*\b(screen|monitor)\b", normalized):
            return "right"
        if re.search(r"\b(main|primary)\b.*\b(screen|monitor)\b", normalized):
            return "main"

        return "configured"

    def _should_search_web(self, user_input: str) -> bool:
        """Detect questions that clearly require recently changing information."""
        if not self.config.get(
            "search", "enabled", default=True, required=False
        ):
            return False

        normalized = " ".join(user_input.lower().split())
        current_information_phrases = (
            "search the web",
            "search online",
            "look this up",
            "look it up",
            "latest",
            "current price",
            "stock price",
            "weather",
            "news",
            "score",
            "schedule",
            "who is the current",
            "who won",
            "release date",
            "exchange rate",
        )

        return any(
            phrase in normalized
            for phrase in current_information_phrases
        )

    def close(self) -> None:
        """Stop background services and active speech."""
        self.screen_monitor.stop()
        self.audio.stop()
    
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
