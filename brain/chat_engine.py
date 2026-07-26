import json
import re
import threading
import time
import ollama
from collections import deque

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
from tools.visual_search import VisualSearchTool
from tools.project_mcp_client import ProjectMCPManager
from config.loader import Config
from brain.personality_loader import PersonalityLoader
from datetime import datetime
from vision.screen_monitor import ScreenMonitor
from brain.intent_router import SemanticIntentRouter

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
            default="10m",
            required=False,
        )
        # A zero keep-alive unloads Qwen3-VL immediately. If the first request
        # needs a compatibility retry, Ollama then has to load the entire model
        # again, which can add many seconds even on a fast GPU.
        if self.vision_keep_alive in {None, 0, "0", "0s"}:
            self.vision_keep_alive = "10m"

        self.client = ollama.Client(
            host=self.config.get(
                "llm",
                "ollama",
                "base_url",
            )
        )
        self.intent_router = SemanticIntentRouter(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
        )
        self._router_history = deque(maxlen=6)
        self._active_topic = ""
        self._active_entity = ""
        self._entity_aliases: dict[str, str] = {}
        self._grounded_context = {
            "subject": "",
            "statement": "",
            "source": "",
        }
        self._turn_visual_subject = ""
        self._pending_action = ""
        self._search_cache: dict[str, tuple[float, str]] = {}
        self._last_search_query = ""
        self._search_cache_seconds = int(self.config.get(
            "search",
            "cache_seconds",
            default=300,
            required=False,
        ))
        self._search_cache_entries = int(self.config.get(
            "search",
            "cache_entries",
            default=20,
            required=False,
        ))
        self._print_timings = bool(self.config.get(
            "debug",
            "print_timings",
            default=True,
            required=False,
        ))

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
        self.visual_search_tool = VisualSearchTool(config=self.config)
        self.project_mcp = None
        self._start_project_mcp()

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
        self._memory_store_lock = threading.Lock()
        self._vision_warm_lock = threading.Lock()
        self._vision_warming = False
        self._vision_last_warm = 0.0
        self._turn_lock = threading.Lock()
        self._active_turn_cancel: threading.Event | None = None

    def _print_event(self, event: Event) -> None:
        print(
            f"\n[Event] {event.name}: "
            f"{event.data}"
        )

    def on_speech_start(self) -> None:
        self.events.emit("speech_started")

        was_speaking = self.audio.is_speaking()
        if was_speaking:
            self.audio.stop()
            with self._turn_lock:
                if self._active_turn_cancel is not None:
                    self._active_turn_cancel.set()

    def _build_conversation_state(self) -> dict:
        return {
            "active_topic": self._active_topic,
            "active_entity": self._active_entity,
            "entity_aliases": self._entity_aliases,
            "grounded_context": dict(self._grounded_context),
        }

    def _grounded_context_text(self) -> str:
        subject = self._grounded_context.get("subject", "").strip()
        statement = self._grounded_context.get("statement", "").strip()
        source = self._grounded_context.get("source", "").strip()
        if not statement:
            return ""
        return (
            "RECENT GROUNDED CONTEXT\n"
            f"Subject: {subject or 'Current subject'}\n"
            f"Last verified result: {statement}\n"
            f"Evidence source: {source or 'Previous verified tool result'}\n"
            "Use this only when it is relevant to the current follow-up. "
            "Distinguish reboots, remakes, sequels, and older works that share "
            "the same name. If the user points out that this verified result "
            "corrected an earlier answer, acknowledge that directly."
        )

    def _remember_grounded_fact(
        self,
        *,
        subject: str,
        statement: str,
        source: str,
    ) -> None:
        statement = " ".join(statement.split()).strip()
        if not statement:
            return
        self._grounded_context = {
            "subject": subject.strip() or self._active_entity or self._active_topic,
            "statement": statement[:1200],
            "source": source.strip(),
        }

    @staticmethod
    def _should_consider_memory(user_input: str) -> bool:
        """Queue only likely personal statements, not tools or fact questions."""
        normalized = " ".join(user_input.lower().split())
        if normalized.endswith("?"):
            return False
        return bool(re.search(
            r"\b(i am|i'm|i feel|i like|i love|i hate|i prefer|i want|"
            r"i need|i have|i live|i study|i work|my favorite|my project)\b",
            normalized,
        ))

    def _store_memory_candidate(self, user_input: str) -> None:
        """Perform expensive extraction/consolidation outside response latency."""
        with self._memory_store_lock:
            started = time.perf_counter()
            try:
                memory = self.extractor.extract(user_input)
                if not memory["save"]:
                    return

                similar = self.memory_manager.search_memory_objects(
                    memory["content"]
                )
                result = self.consolidator.consolidate(
                    similar,
                    memory["content"],
                )
                action = result["action"]

                if action == "ADD":
                    self.memory_manager.store_memory(
                        content=memory["content"],
                        category=memory["category"],
                        importance=5,
                    )
                elif action == "UPDATE":
                    self.memory_manager.update_memory(
                        result["memory_id"],
                        result["content"],
                    )
            except Exception as error:
                print(
                    f"[Memory Background Warning] "
                    f"{type(error).__name__}: {error}"
                )
            finally:
                if self._print_timings:
                    print(
                        "[Timing] background_memory="
                        f"{time.perf_counter() - started:.2f}s"
                    )

    def _update_conversation_state(self, route) -> None:
        """Retain corrected entities and topics for short follow-up turns."""
        if route.topic:
            self._active_topic = route.topic
        elif route.intent in {"knowledge_question", "web_search"}:
            self._active_topic = route.normalized_request
        if route.entity:
            previous_entity = self._active_entity
            self._active_entity = route.entity
            if route.intent == "entity_correction" and previous_entity:
                self._entity_aliases[previous_entity] = route.entity
            for alias in route.aliases:
                self._entity_aliases[alias] = route.entity
            # Keep the state prompt small during long sessions.
            if len(self._entity_aliases) > 20:
                oldest = next(iter(self._entity_aliases))
                self._entity_aliases.pop(oldest, None)

    def _corrected_search_query(self, entity: str) -> str:
        topic = self._active_topic.strip()
        if "release" in topic.lower():
            return f"latest {entity} model releases official"
        if self._last_search_query:
            words = self._last_search_query.split()
            if words:
                words[-1] = entity
                return " ".join(words)
        return f"latest information about {entity}"

    def _build_factual_messages(
        self,
        question: str,
        evidence: str = "",
    ) -> list[dict]:
        grounded_context = self._grounded_context_text()
        evidence_block = (
            f"\n\nCURRENT RETRIEVED EVIDENCE\n{evidence}"
            if evidence
            else ""
        )
        grounded_block = (
            f"\n\n{grounded_context}"
            if grounded_context
            else ""
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are Elaina speaking naturally to the user. Answer the "
                    "exact factual question directly and accurately, normally "
                    "in one or two conversational sentences. This will be read "
                    "aloud by TTS. Never use Markdown, headings, bullet points, "
                    "bold, italics, stars, tables, or labels such as 'Answer:' "
                    "and 'Confidence:'. Do not sound like a written report. "
                    "Give enough detail for how, why, and example questions. "
                    "Do not add greetings, emotional reassurance, time-of-day "
                    "comments, promises to search later, offers for more help, "
                    "or unrelated follow-up questions. Do not output raw URLs."
                    f"{grounded_block}{evidence_block}"
                ),
            },
            {
                "role": "user",
                "content": question,
            },
        ]

    def _announce_work_status(self, intent: str) -> None:
        """Immediately show and speak progress before a slower tool runs."""
        status_by_intent = {
            "web_search": (
                "Sure, I'll search that now. Give me a second."
            ),
            "entity_correction": (
                "Got it. I'll repeat the search with the corrected name."
            ),
            "screen_analysis": (
                "Got it. I'm analyzing the selected area now."
            ),
            "project_question": (
                "I'll check the relevant project files now."
            ),
            "project_edit": (
                "I'll inspect the relevant files and prepare a change for "
                "your approval."
            ),
            "git_commit": (
                "I'll inspect the Git changes and prepare them for your "
                "approval."
            ),
            "git_publish": (
                "I'll inspect the Git changes and prepare a push for your "
                "approval."
            ),
        }
        text = status_by_intent.get(intent, "")
        if not text:
            return

        print(f"[Status] Elaina: {text}")
        self.events.emit(
            "assistant_status",
            text=text,
            intent=intent,
        )
        self.audio.speak(text)
    
    def chat(
        self,
        user_input,
        screen_region=None,
        screen_snapshot=None,
    ):
        turn_started = time.perf_counter()
        timings: dict[str, float] = {}
        user_input = str(user_input).strip()

        if not user_input:
            return ""

        turn_cancel = threading.Event()
        with self._turn_lock:
            self._active_turn_cancel = turn_cancel
        self._turn_visual_subject = ""

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

        ####################################################
        # Build Prompt
        ####################################################
        route_started = time.perf_counter()
        route = self.intent_router.route(
            user_input,
            recent_turns=list(self._router_history),
            has_screen_selection=(
                screen_region is not None
                or screen_snapshot is not None
            ),
            project_tools_available=self.project_mcp is not None,
            conversation_state=self._build_conversation_state(),
            pending_action=self._pending_action,
        )
        timings["route"] = time.perf_counter() - route_started
        self._update_conversation_state(route)
        print(
            f"[Router] {route.intent} ({route.confidence:.2f}): "
            f"{route.reason or route.normalized_request}"
        )
        if route.normalized_request != user_input:
            print(
                f"[Router] Interpreted transcript as: "
                f"{route.normalized_request}"
            )
        if route.intent == "fact_check" and route.search_query:
            self._announce_work_status("web_search")
        else:
            self._announce_work_status(route.intent)

        memory_started = time.perf_counter()
        use_memory = (
            route.intent == "conversation"
            and self.router.should_use_memory(user_input)
        )
        if use_memory:
            memories = self.memory_manager.search(
                user_input,
                k=20,
            )
            memories = self.memory_ranker.rank(memories)
            memory_text = self.context_builder.build(memories)
        timings["memory_retrieval"] = (
            time.perf_counter() - memory_started
        )

        project_edit_requested = route.intent == "project_edit"
        use_screen_vision = route.intent == "screen_analysis"
        forced_response = ""
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
            user_input=user_input,
        )
        ####################################################
        # Ask Qwen
        ####################################################

        messages = self.conversation.build_messages(
            system_prompt=self.system_prompt,
            context_prompt=context_prompt,
        )
        grounded_context = self._grounded_context_text()
        if grounded_context and route.intent in {
            "conversation",
            "clarification",
            "fact_check",
        }:
            messages.insert(
                -1,
                {
                    "role": "system",
                    "content": grounded_context,
                },
            )

        if route.intent == "time_question":
            messages.append({
                "role": "system",
                "content": self.build_time_context(),
            })

        if route.intent in {
            "knowledge_question",
            "selected_text_question",
            "screen_analysis",
            "project_question",
            "project_edit",
            "git_commit",
            "git_publish",
            "time_question",
            "fact_check",
        }:
            messages.append({
                "role": "system",
                "content": (
                    "This is a factual or tool-focused turn. Answer the exact "
                    "request directly. Do not mention the current hour, how "
                    "early or late it is, or add unrelated conversational "
                    "filler. Never claim a tool action succeeded unless the "
                    "tool result says it succeeded. Keep simple answers short, "
                    "but give enough detail to fully answer how or why "
                    "questions."
                ),
            })

        if route.intent == "knowledge_question":
            messages = self._build_factual_messages(
                route.normalized_request,
            )
        elif route.intent == "time_question":
            messages = self._build_factual_messages(
                route.normalized_request,
                self.build_time_context(),
            )

        turn_grounding_source = ""
        turn_grounding_subject = ""
        
        if not use_screen_vision and route.intent == "web_search":
            search_started = time.perf_counter()
            try:
                search_result = self.search_web(
                    query=route.search_query or route.normalized_request,
                    max_results=3,
                )
                self._last_search_query = (
                    route.search_query or route.normalized_request
                )
                if not str(search_result).strip():
                    raise RuntimeError("The search returned no results.")
                messages = self._build_factual_messages(
                    route.normalized_request,
                    str(search_result),
                )
                turn_grounding_source = "Current web search"
                turn_grounding_subject = (
                    route.entity
                    or self._active_entity
                    or route.topic
                    or route.normalized_request
                )
            except Exception as error:
                forced_response = (
                    "I couldn't complete that web search: "
                    f"{type(error).__name__}: {error}"
                )
            finally:
                timings["web_search"] = (
                    time.perf_counter() - search_started
                )

        if route.intent == "fact_check":
            if route.search_query:
                search_started = time.perf_counter()
                try:
                    search_result = self.search_web(
                        query=route.search_query,
                        max_results=3,
                    )
                    self._last_search_query = route.search_query
                    messages = self._build_factual_messages(
                        (
                            f"Reconcile the user's correction with the recent "
                            f"grounded context: {route.normalized_request}. "
                            "If Elaina's earlier statement was wrong, say so "
                            "directly and acknowledge that the user was right."
                        ),
                        str(search_result),
                    )
                    turn_grounding_source = "Current fact-check web search"
                    turn_grounding_subject = (
                        route.entity
                        or self._grounded_context.get("subject", "")
                        or route.topic
                    )
                except Exception as error:
                    forced_response = (
                        "I couldn't verify that correction: "
                        f"{type(error).__name__}: {error}"
                    )
                finally:
                    timings["web_search"] = (
                        time.perf_counter() - search_started
                    )
            else:
                messages = self._build_factual_messages(
                    (
                        f"Respond to this follow-up using the recent grounded "
                        f"context: {route.normalized_request}. If the user was "
                        "right and Elaina's earlier answer was wrong, clearly "
                        "acknowledge both facts."
                    ),
                )

        if route.intent == "entity_correction":
            corrected_entity = route.entity or route.normalized_request
            corrected_query = self._corrected_search_query(corrected_entity)
            search_started = time.perf_counter()
            try:
                search_result = self.search_web(
                    query=corrected_query,
                    max_results=3,
                )
                self._last_search_query = corrected_query
                messages = self._build_factual_messages(
                    (
                        f"Briefly acknowledge that the corrected entity is "
                        f"{corrected_entity}, then answer the corrected search "
                        f"request: {corrected_query}"
                    ),
                    str(search_result),
                )
                turn_grounding_source = "Corrected-entity web search"
                turn_grounding_subject = corrected_entity
            except Exception as error:
                forced_response = (
                    f"Got it—the name is {corrected_entity}. I couldn't redo "
                    f"the search: {type(error).__name__}: {error}"
                )
            finally:
                timings["web_search"] = (
                    time.perf_counter() - search_started
                )

        if route.intent == "pending_approval":
            forced_response = (
                f"The {self._pending_action or 'action'} proposal is still "
                "waiting in Electron. Review it and use the approval or "
                "rejection button there."
            )

        # Git writes use a deterministic snapshot and approval flow rather than
        # asking the language model to choose commands or files.
        if not use_screen_vision and route.intent in {
            "git_commit",
            "git_publish",
        }:
            if self._pending_action:
                forced_response = (
                    f"A {self._pending_action} proposal is already waiting in "
                    "Electron. Review it before creating another action."
                )
            else:
                project_started = time.perf_counter()
                git_context = self._prepare_git_action()
                timings["project_tools"] = (
                    time.perf_counter() - project_started
                )
                if self._pending_action == "Git":
                    forced_response = (
                        "The Git proposal is ready in Electron. Nothing has "
                        "been committed or pushed; review it and choose Commit "
                        "& Push, Commit Only, or Reject."
                    )
                else:
                    forced_response = (
                        "I couldn't prepare a valid Git proposal. Nothing was "
                        "staged, committed, or pushed; check the console error."
                    )

        # Other project questions use the normal read/proposal tool planner.
        elif not use_screen_vision and route.intent in {
            "project_question",
            "project_edit",
        }:
            if project_edit_requested and self._pending_action:
                forced_response = (
                    f"A {self._pending_action} proposal is already waiting in "
                    "Electron. Review it before creating another change."
                )
            else:
                project_started = time.perf_counter()
                project_context = self._research_project(
                    user_input=route.normalized_request,
                    messages=messages,
                    edit_requested=project_edit_requested,
                )
                timings["project_tools"] = (
                    time.perf_counter() - project_started
                )

                if project_edit_requested and self._pending_action == "project":
                    forced_response = (
                        "The project change proposal is ready in Electron. No "
                        "files have changed; review the editable code and click "
                        "Approve or Reject."
                    )
                elif project_edit_requested:
                    forced_response = (
                        "I couldn't create a valid project-change proposal. "
                        "No files were changed; check the project-tool log."
                    )
                elif project_context:
                    messages.append({
                        "role": "system",
                        "content": project_context,
                    })

        if use_screen_vision and screen_snapshot is not None:
            visual_started = time.perf_counter()
            (
                verification_context,
                blocked_identification_reply,
            ) = self._prepare_visual_verification(
                user_input=user_input,
                screen_snapshot=screen_snapshot,
            )
            timings["visual_pipeline"] = (
                time.perf_counter() - visual_started
            )
        else:
            verification_context = ""
            blocked_identification_reply = ""

        verified_identification = bool(verification_context)

        if use_screen_vision and screen_snapshot is not None:
            # Keep vision requests isolated from memories and old conversation
            # history. This makes OCR faster and prevents Qwen3-VL from
            # returning an empty final answer after processing a large prompt.
            # The image is attached directly to the user message exactly as
            # Ollama's vision API expects.
            if verified_identification:
                # Google has already searched the image itself. Use the faster,
                # more reliable text model to synthesize that retrieved
                # evidence instead of sending a large prompt back through VL.
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "IDENTIFICATION MODE\n"
                            "Answer the current identification question using "
                            "the verified reverse-image evidence below. Prefer "
                            "matching-page titles, full/partial matches, and "
                            "high-scoring web entities. Speak like a person, "
                            "normally in one or two natural sentences. State "
                            "the identity directly, then give only the most "
                            "useful supporting detail. If uncertain, express "
                            "that uncertainty naturally in the sentence. Never "
                            "use Markdown, headings, bullets, stars, bold text, "
                            "or separate Answer and Confidence labels.\n\n"
                            f"{verification_context}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": user_input,
                    },
                ]
            else:
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "VISION MODE\n"
                            "Answer only the user's current question using the "
                            "attached selected screen region. Read visible text "
                            "carefully. If translation is requested, transcribe "
                            "the source text and translate it directly. Do not "
                            "discuss unrelated conversation history. If text is "
                            "genuinely unreadable, say so instead of guessing. "
                            "For translation, output only a concise translation "
                            "and necessary source-text clarification. Do not add "
                            "reactions, jokes, opinions, hype, or follow-up "
                            "questions. Write one or two natural spoken "
                            "sentences. Never use Markdown, headings, bullets, "
                            "stars, bold text, tables, or report-style labels."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{user_input}\n\n"
                            f"{screen_context}"
                        ),
                        "images": [screen_snapshot.image_bytes],
                    },
                ]

        uses_vision_model = (
            use_screen_vision
            and not verified_identification
        )
        active_model = self.vision_model if uses_vision_model else self.model
        active_keep_alive = (
            self.vision_keep_alive if uses_vision_model else self.keep_alive
        )
        active_temperature = (
            self.temperature
            if route.intent == "conversation"
            else 0.1
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
        effective_forced_response = (
            forced_response or blocked_identification_reply
        )

        def stream_answer(*, allow_thinking: bool) -> None:
            """Stream one Ollama response into this turn's output buffers."""
            nonlocal reply, speech_buffer, tts_buffer, tts_sentence_count

            response_stream = self.client.chat(
                model=active_model,
                messages=messages,
                stream=True,
                options={
                    "temperature": active_temperature,
                },
                keep_alive=active_keep_alive,
                think=allow_thinking,
            )

            for chunk in response_stream:
                if turn_cancel.is_set():
                    break

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

        generation_started = time.perf_counter()
        try:
            if effective_forced_response:
                # Verification failures are enforced here instead of asking
                # the vision model to voluntarily avoid a confident guess.
                print(
                    effective_forced_response,
                    end="",
                    flush=True,
                )
                reply = effective_forced_response
                speech_buffer = effective_forced_response
                self.events.emit(
                    "assistant_stream",
                    text=effective_forced_response,
                )
            else:
                # Fast path: suppress the model's reasoning tokens.
                stream_answer(allow_thinking=False)

            # Some Ollama/Qwen3-VL combinations return an empty streamed
            # content field. Retry once with Ollama's documented non-streaming
            # vision request instead of running a long reasoning stream.
            if (
                uses_vision_model
                and not effective_forced_response
                and not reply.strip()
            ):
                print(
                    "\n[Vision] The streamed response was empty; retrying "
                    "with the direct vision request..."
                )
                print("Elaina: ", end="", flush=True)
                direct_response = self.client.chat(
                    model=active_model,
                    messages=messages,
                    stream=False,
                    options={
                        "temperature": active_temperature,
                    },
                    keep_alive=active_keep_alive,
                    think=False,
                )
                direct_message = self._value(
                    direct_response,
                    "message",
                    {},
                )
                direct_content = TextFilter.clean(
                    self._value(
                        direct_message,
                        "content",
                        "",
                    )
                )

                if direct_content:
                    print(
                        direct_content,
                        end="",
                        flush=True,
                    )
                    reply = direct_content
                    speech_buffer = direct_content
                    self.events.emit(
                        "assistant_stream",
                        text=direct_content,
                    )

        except Exception as error:
            print(f"\n[Vision/LLM Error] {type(error).__name__}: {error}")
        timings["generation"] = time.perf_counter() - generation_started

        if turn_cancel.is_set():
            print("\n[ChatEngine] Response interrupted.")
            self.events.emit(
                "assistant_interrupted",
                text=reply,
            )
            with self._turn_lock:
                if self._active_turn_cancel is turn_cancel:
                    self._active_turn_cancel = None
            return reply

        # Never silently return to microphone listening after a failed request.
        if not reply.strip():
            if uses_vision_model:
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

        if verified_identification:
            self._remember_grounded_fact(
                subject=(
                    self._turn_visual_subject
                    or route.entity
                    or route.topic
                    or "Selected image"
                ),
                statement=reply,
                source="Google visual matching and current web verification",
            )
        elif turn_grounding_source:
            self._remember_grounded_fact(
                subject=turn_grounding_subject,
                statement=reply,
                source=turn_grounding_source,
            )

        self.conversation.add(
            "user",
            user_input,
        )

        self.conversation.add(
            "assistant",
            reply
        )
        self._router_history.extend([
            {
                "role": "user",
                "content": user_input,
            },
            {
                "role": "assistant",
                "content": reply,
            },
        ])

        emotion_state = self.emotion.analyze(
            user_input=user_input,
            reply=reply,
        )

        self.events.emit(
            "emotion_changed",
            emotion=emotion_state.name,
            intensity=emotion_state.intensity,
        )

        if (
            route.intent == "conversation"
            and self._should_consider_memory(user_input)
        ):
            threading.Thread(
                target=self._store_memory_candidate,
                args=(user_input,),
                name="elaina-memory-store",
                daemon=True,
            ).start()
            timings["memory_queue"] = 0.0

        timings["total"] = time.perf_counter() - turn_started
        if self._print_timings:
            print(
                "[Timing] "
                + " ".join(
                    f"{name}={duration:.2f}s"
                    for name, duration in timings.items()
                )
            )

        with self._turn_lock:
            if self._active_turn_cancel is turn_cancel:
                self._active_turn_cancel = None

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

        # Begin loading Qwen3-VL while the user is speaking their question.
        # If the model is already resident, Ollama returns quickly. The cooldown
        # avoids creating a preload request for every selection in a short
        # session.
        if time.monotonic() - self._vision_last_warm > 300:
            threading.Thread(
                target=self._prewarm_vision_model,
                name="elaina-vision-prewarm",
                daemon=True,
            ).start()

        return True

    def _prewarm_vision_model(self) -> None:
        """Load the vision model before the next direct screen-analysis turn."""
        with self._vision_warm_lock:
            if self._vision_warming:
                return
            if time.monotonic() - self._vision_last_warm <= 300:
                return
            self._vision_warming = True

        started = time.perf_counter()
        try:
            print(f"[Vision] Preloading {self.vision_model}...")
            self.client.generate(
                model=self.vision_model,
                prompt="",
                stream=False,
                keep_alive=self.vision_keep_alive,
            )
            self._vision_last_warm = time.monotonic()
            if self._print_timings:
                print(
                    "[Timing] vision_preload="
                    f"{time.perf_counter() - started:.2f}s"
                )
        except Exception as error:
            print(
                f"[Vision Preload Warning] "
                f"{type(error).__name__}: {error}"
            )
        finally:
            with self._vision_warm_lock:
                self._vision_warming = False

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

    def _prepare_visual_verification(
        self,
        *,
        user_input: str,
        screen_snapshot,
    ) -> tuple[str, str]:
        """
        Verify visual identification requests with current web evidence.

        Translation, OCR, code explanation, and ordinary description skip this
        path. Identification of games, products, landmarks, vehicles, public
        media, and other specific entities receives a visual evidence pass,
        web search, and final image-to-evidence comparison.
        """
        task_type = self._classify_visual_task(user_input)
        print(f"[Vision Router] {task_type}")

        if task_type != "identify":
            return "", ""

        if not self.config.get(
            "search",
            "enabled",
            default=True,
            required=False,
        ):
            return (
                "",
                "Web verification is disabled, so I can't confirm the exact "
                "identity without guessing.",
            )

        try:
            print(
                "[Visual Search] Searching the selected image with "
                "Google Web Detection..."
            )
            visual_result = self.visual_search_tool.search_image(
                screen_snapshot.image_bytes,
            )
            print(
                "[Visual Search] Received "
                f"{len(visual_result.matching_pages)} matching pages and "
                f"{len(visual_result.web_entities)} web entities."
            )
            if visual_result.matching_pages:
                best_page = visual_result.matching_pages[0]
                self.events.emit(
                    "visual_match_found",
                    title=best_page.get("title", ""),
                    url=best_page.get("url", ""),
                    score=best_page.get("score", 0),
                )
        except Exception as error:
            print(
                f"[Visual Search] Image search failed: "
                f"{type(error).__name__}: {error}"
            )
            return (
                "",
                "I couldn't search the image on the web, so I can't verify its "
                "exact identity without guessing. Check the Google Cloud "
                "Vision setup and try again.",
            )

        if not visual_result.has_useful_evidence:
            return (
                "",
                "I couldn't find a reliable matching image or web entity for "
                "this selection, so I don't want to guess its exact identity.",
            )

        visual_subject_candidates = [
            *visual_result.best_guess_labels,
            *[
                str(item.get("description", ""))
                for item in visual_result.web_entities[:3]
            ],
        ]
        self._turn_visual_subject = next(
            (
                candidate.strip()
                for candidate in visual_subject_candidates
                if candidate and candidate.strip()
            ),
            "",
        )

        search_terms = [
            *visual_result.best_guess_labels,
            *[
                str(item.get("description", ""))
                for item in visual_result.web_entities[:5]
            ],
        ]
        search_query = " ".join(
            term.strip()
            for term in search_terms
            if term and term.strip()
        )[:250]

        text_search_result = ""
        if search_query:
            try:
                text_search_result = self.search_web(
                    query=search_query,
                    max_results=5,
                )
            except Exception as error:
                print(
                    f"[Visual Search] Text confirmation failed: "
                    f"{type(error).__name__}: {error}"
                )
                text_search_result = (
                    "Additional text-search confirmation was unavailable."
                )

        return (
            (
                "VISUAL IDENTIFICATION VERIFICATION\n"
                "Google Web Detection searched using the attached image bytes. "
                "Its matching-image evidence is:\n"
                f"{visual_result.to_prompt_text()}\n\n"
                "Additional text-search confirmation:\n"
                f"{text_search_result}\n\n"
                "Use the attached image and this retrieval evidence together. "
                "Prefer full or partial image matches and matching-page titles "
                "over generic web-entity labels. Give an exact identity only "
                "when the evidence agrees. Otherwise state uncertainty. Include "
                "a short confidence label: high, moderate, or low. Briefly "
                "explain which retrieved evidence supports the answer. Do not "
                "output URLs."
            ),
            "",
        )

    def _classify_visual_task(self, user_input: str) -> str:
        """Semantically distinguish identification from direct visual tasks."""
        prompt = (
            "Classify the user's screen-image question as exactly one of:\n"
            "- identify: asks for the exact identity, name, model, brand, "
            "location, title, species, game, product, building, vehicle, logo, "
            "public media, or other specific entity.\n"
            "- direct: asks to translate, read text, explain code, summarize, "
            "describe visible actions, troubleshoot an error, or answer without "
            "needing the exact identity of an entity.\n\n"
            "Infer meaning semantically rather than matching trigger words. "
            'Return JSON only: {"task":"identify"} or {"task":"direct"}.\n\n'
            f"Question: {user_input}"
        )

        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": prompt,
                    },
                ],
                stream=False,
                format="json",
                options={
                    "temperature": 0,
                    "num_predict": 30,
                },
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            content = self._value(message, "content", "")
            payload = json.loads(content)
            task = str(payload.get("task", "")).strip().lower()
            if task in {"identify", "direct"}:
                return task
        except Exception as error:
            print(
                f"[Vision Router] Classification failed: "
                f"{type(error).__name__}: {error}"
            )

        # Failure must not trigger an unnecessary search or confident guess.
        return "direct"

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

    def _start_project_mcp(self) -> None:
        """Start project access without preventing Elaina from launching."""
        enabled = self.config.get(
            "project_access",
            "enabled",
            default=False,
            required=False,
        )
        project_root = self.config.get(
            "project_access",
            "project_root",
            default="",
            required=False,
        )

        if not enabled:
            return
        if not str(project_root).strip():
            print(
                "[Project MCP] Disabled because project_root is empty in "
                "config.yaml."
            )
            return

        try:
            self.project_mcp = ProjectMCPManager(project_root)
            self.project_mcp.start()
            tool_count = len(self.project_mcp.ollama_tools())
            print(
                f"[Project MCP] Connected to {project_root} "
                f"with {tool_count} read-only tools."
            )
        except Exception as error:
            self.project_mcp = None
            print(f"[Project MCP] Could not connect: {error}")

    def _should_use_project_tools(self, user_input: str) -> bool:
        """Detect requests that require evidence from the selected project."""
        if self.project_mcp is None:
            return False

        normalized = " ".join(user_input.lower().split())
        if self._is_git_action_request(normalized):
            return True
        project_phrases = (
            "my project",
            "this project",
            "the project",
            "my codebase",
            "this codebase",
            "the codebase",
            "my repository",
            "this repository",
            "the repository",
            "my repo",
            "this repo",
            "project files",
            "project structure",
            "git status",
            "working tree",
            "what am i working on",
            "what have i changed",
            "what did i change",
            "read the file",
            "read this file",
            "open the file",
            "open this file",
            "search the code",
            "search my code",
            "find in the project",
            "find in my code",
            "which file",
            "edit the file",
            "edit my code",
            "change the code",
            "change my code",
            "modify the code",
            "modify my code",
            "create a file",
            "make a file",
            "add a button",
            "add this feature",
            "implement this",
            "apply a fix",
            "fix my code",
        )

        if any(phrase in normalized for phrase in project_phrases):
            return True

        if self._is_project_edit_request(normalized):
            return True

        # A concrete source filename or relative path is also a strong signal.
        return bool(re.search(
            r"\b[\w./\\-]+\.(py|js|ts|tsx|jsx|html|css|json|ya?ml|md)\b",
            normalized,
        ))

    @staticmethod
    def _is_git_action_request(user_input: str) -> bool:
        """Detect requests to create a commit or push project changes."""
        normalized = " ".join(user_input.lower().split())
        phrases = (
            "git push",
            "push to git",
            "push this to git",
            "push it to git",
            "push to github",
            "push this to github",
            "push my changes",
            "push these changes",
            "commit and push",
            "commit these changes",
            "commit my changes",
            "commit this project",
        )
        return any(phrase in normalized for phrase in phrases)

    @staticmethod
    def _is_project_edit_request(user_input: str) -> bool:
        """Detect requests whose intended result is a project modification."""
        normalized = " ".join(user_input.lower().split())

        return bool(re.search(
            r"\b(add|create|make|edit|change|modify|remove|delete|implement|fix)"
            r"\b.*\b(button|file|code|feature|function|class|html|css|"
            r"javascript|python)\b",
            normalized,
        ))

    @staticmethod
    def _value(item, key: str, default=None):
        """Read a field from either an Ollama object or a plain dictionary."""
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    def _parse_tool_call(self, tool_call) -> tuple[str, dict]:
        function = self._value(tool_call, "function", {})
        name = self._value(function, "name", "")
        arguments = self._value(function, "arguments", {}) or {}

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        if not isinstance(arguments, dict):
            arguments = {}

        return str(name), arguments

    def _research_project(
        self,
        user_input: str,
        messages: list[dict],
        edit_requested: bool | None = None,
    ) -> str:
        """
        Let Qwen gather read-only project evidence before writing its answer.

        A local finish tool gives the planner a clean way to say it has enough
        information. The final answer is generated by the normal streaming path,
        so TTS and Electron events continue to work exactly as before.
        """
        if self.project_mcp is None:
            return ""

        if edit_requested is None:
            edit_requested = self._is_project_edit_request(user_input)
        tools = self.project_mcp.ollama_tools()
        if not tools:
            return ""

        finish_tool = {
            "type": "function",
            "function": {
                "name": "finish_project_research",
                "description": (
                    "Call this when enough project evidence has been collected "
                    "to answer the user accurately."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        }
        planning_tools = [*tools, finish_tool]
        # Keep tool planning separate from personality and old conversation
        # context. This prevents unrelated memories or casual dialogue from
        # influencing an exact source-code modification.
        research_messages = [
            {
                "role": "system",
                "content": (
                    "You are a precise local project editor gathering evidence "
                    "for this exact request:\n"
                    f"{user_input}\n\n"
                    "Do not solve a different problem. Use project tools to "
                    "locate and read the exact relevant source. For UI controls, "
                    "search identifiers using useful forms such as screen-button "
                    "or chat-toggle-button and inspect index.html before "
                    "proposing an HTML change. If JavaScript behavior or styling "
                    "is requested, inspect those files too. If the user asks to "
                    "create or edit code, call propose_file_changes using this "
                    "shape:\n"
                    '{"summary":"...","changes":[{"action":"replace",'
                    '"path":"relative/file.html","old_text":"exact existing '
                    'text","new_text":"replacement text"}]}\n'
                    "When adding a UI element next to an existing HTML element, "
                    "do NOT copy a large exact block. Use:\n"
                    '{"action":"insert_after_html_id","path":"relative/file.html",'
                    '"element_id":"screen-button","new_text":"<button '
                    'id=\\"random-button\\">Random</button>"}\n'
                    "When removing an HTML element, use:\n"
                    '{"action":"remove_html_id","path":"relative/file.html",'
                    '"element_id":"random-button","new_text":""}\n'
                    "Use action=create only for a genuinely new file. Use "
                    "focused exact replacements instead of rewriting large "
                    "files. To remove an HTML element, read its surrounding "
                    "source and replace the complete opening tag, content, and "
                    "closing tag with an empty new_text. Never remove only an "
                    "opening tag. The proposal does not edit anything; Electron asks "
                    "the user for permission. You MUST call "
                    "propose_file_changes before finish_project_research. "
                    "Identifying a file is not enough. Do not answer in normal "
                    "text and never invent paths or source text."
                ),
            },
            {
                "role": "user",
                "content": user_input,
            },
        ]

        max_rounds = int(self.config.get(
            "project_access",
            "max_tool_rounds",
            default=3,
            required=False,
        ))
        max_rounds = max(1, min(max_rounds, 8))
        if edit_requested:
            max_rounds = 6
        evidence: list[str] = []
        evidence_characters = 0
        maximum_evidence = 24000
        proposal_created = False
        source_file_read = False

        print(f"\n[Project MCP] Researching: {user_input}")

        for _ in range(max_rounds):
            try:
                response = self.client.chat(
                    model=self.model,
                    messages=research_messages,
                    tools=planning_tools,
                    stream=False,
                    options={"temperature": 0.1},
                    keep_alive=self.keep_alive,
                    think=False,
                )
            except Exception as error:
                print(f"[Project MCP] Planning failed: {error}")
                break

            assistant_message = self._value(response, "message", {})
            tool_calls = self._value(
                assistant_message,
                "tool_calls",
                [],
            ) or []

            if not tool_calls:
                if edit_requested and not proposal_created:
                    research_messages.append({
                        "role": "system",
                        "content": (
                            "The requested edit still has no proposal. Continue "
                            "using project tools. Read any missing file content, "
                            "then call propose_file_changes with exact old_text "
                            "and new_text. Do not answer in plain text."
                        ),
                    })
                    continue

                break

            research_messages.append(assistant_message)
            should_finish = False

            for tool_call in tool_calls:
                name, arguments = self._parse_tool_call(tool_call)

                if name == "finish_project_research":
                    if edit_requested and not proposal_created:
                        research_messages.append({
                            "role": "tool",
                            "tool_name": name,
                            "content": (
                                "Cannot finish yet: this edit request requires "
                                "a successful propose_file_changes call."
                            ),
                        })
                    else:
                        should_finish = True
                    continue
                if not name:
                    continue

                # Small Qwen models sometimes request only a few irrelevant
                # lines. Edit mode expands safe reads so the model receives
                # enough exact source text to construct a valid replacement.
                if edit_requested and name == "list_files":
                    arguments["limit"] = max(
                        int(arguments.get("limit", 0) or 0),
                        200,
                    )

                if edit_requested and name == "read_file":
                    arguments["start_line"] = 1
                    arguments["line_count"] = 300

                print(f"[Project Tool] {name}: {arguments}")
                self.events.emit(
                    "tool_started",
                    tool=name,
                    arguments=arguments,
                )

                if (
                    edit_requested
                    and name == "propose_file_changes"
                    and not source_file_read
                ):
                    result = (
                        "Tool error: Read the exact target file with read_file "
                        "before creating a change proposal."
                    )
                else:
                    try:
                        result = self.project_mcp.call_tool(name, arguments)
                    except Exception as error:
                        result = (
                            f"Tool error: {type(error).__name__}: {error}"
                        )

                if (
                    name == "read_file"
                    and not result.startswith("Tool error:")
                    and result.strip()
                ):
                    source_file_read = True

                self.events.emit(
                    "tool_finished",
                    tool=name,
                    arguments=arguments,
                )

                remaining = maximum_evidence - evidence_characters
                stored_result = result[:remaining]
                evidence.append(
                    f"TOOL: {name}\n"
                    f"ARGUMENTS: {json.dumps(arguments, ensure_ascii=False)}\n"
                    f"RESULT:\n{stored_result}"
                )
                evidence_characters += len(stored_result)

                research_messages.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": result,
                })

                if name == "propose_file_changes":
                    try:
                        proposal = json.loads(result)
                    except (json.JSONDecodeError, TypeError):
                        proposal = {}

                    if proposal.get("status") == "awaiting_approval":
                        proposal_created = True
                        self._pending_action = "project"
                        should_finish = True
                        self.events.emit(
                            "project_change_proposed",
                            proposal_id=proposal.get("proposal_id", ""),
                            summary=proposal.get(
                                "summary",
                                "Project file changes",
                            ),
                            files=proposal.get("files", []),
                            editable_changes=proposal.get(
                                "editable_changes",
                                [],
                            ),
                            diff=proposal.get("diff", ""),
                            diff_truncated=proposal.get(
                                "diff_truncated",
                                False,
                            ),
                        )

                if evidence_characters >= maximum_evidence:
                    should_finish = True
                    break

            if should_finish:
                break

        # If the research planner found source code but still failed to create
        # the proposal, perform a final tightly-scoped generation where the
        # only available action is proposing changes. This prevents Qwen from
        # falling back to conversational advice after doing the file research.
        if (
            edit_requested
            and not proposal_created
            and evidence
            and source_file_read
        ):
            proposal_tool = next(
                (
                    tool
                    for tool in tools
                    if self._value(
                        self._value(tool, "function", {}),
                        "name",
                        "",
                    ) == "propose_file_changes"
                ),
                None,
            )

            if proposal_tool is not None:
                forced_messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a precise code editor. The user's exact "
                            f"request is:\n{user_input}\n\n"
                            "You must now create only that requested change. "
                            "Use the evidence below to call "
                            "propose_file_changes. The user will review the diff "
                            "before anything is written. Do not answer in plain "
                            "text. Use exact old_text copied from the evidence.\n\n"
                            + "\n\n---\n\n".join(evidence)
                        ),
                    },
                ]

                for _ in range(2):
                    try:
                        forced_response = self.client.chat(
                            model=self.model,
                            messages=forced_messages,
                            tools=[proposal_tool],
                            stream=False,
                            options={"temperature": 0.1},
                            keep_alive=self.keep_alive,
                            think=False,
                        )
                    except Exception as error:
                        print(
                            f"[Project MCP] Proposal generation failed: {error}"
                        )
                        break

                    forced_message = self._value(
                        forced_response,
                        "message",
                        {},
                    )
                    forced_calls = self._value(
                        forced_message,
                        "tool_calls",
                        [],
                    ) or []

                    if not forced_calls:
                        forced_messages.append({
                            "role": "system",
                            "content": (
                                "Plain text is not allowed here. Call "
                                "propose_file_changes now."
                            ),
                        })
                        continue

                    name, arguments = self._parse_tool_call(forced_calls[0])
                    if name != "propose_file_changes":
                        continue

                    print(f"[Project Tool] {name}: {arguments}")
                    self.events.emit(
                        "tool_started",
                        tool=name,
                        arguments=arguments,
                    )

                    try:
                        result = self.project_mcp.call_tool(name, arguments)
                    except Exception as error:
                        result = (
                            f"Tool error: {type(error).__name__}: {error}"
                        )

                    self.events.emit(
                        "tool_finished",
                        tool=name,
                        arguments=arguments,
                    )
                    evidence.append(
                        f"TOOL: {name}\n"
                        f"ARGUMENTS: "
                        f"{json.dumps(arguments, ensure_ascii=False)}\n"
                        f"RESULT:\n{result}"
                    )

                    try:
                        proposal = json.loads(result)
                    except (json.JSONDecodeError, TypeError):
                        proposal = {}

                    if proposal.get("status") == "awaiting_approval":
                        proposal_created = True
                        self._pending_action = "project"
                        self.events.emit(
                            "project_change_proposed",
                            proposal_id=proposal.get("proposal_id", ""),
                            summary=proposal.get(
                                "summary",
                                "Project file changes",
                            ),
                            files=proposal.get("files", []),
                            editable_changes=proposal.get(
                                "editable_changes",
                                [],
                            ),
                            diff=proposal.get("diff", ""),
                            diff_truncated=proposal.get(
                                "diff_truncated",
                                False,
                            ),
                        )
                        break

                    forced_messages.extend([
                        forced_message,
                        {
                            "role": "tool",
                            "tool_name": name,
                            "content": result,
                        },
                        {
                            "role": "system",
                            "content": (
                                "The proposal was invalid. Correct the exact "
                                "replacement using the evidence and try once "
                                "more. For adding or removing HTML controls, "
                                "prefer insert_after_html_id or remove_html_id "
                                "instead of exact multiline replacement."
                            ),
                        },
                    ])

        if not evidence:
            return ""

        approval_instruction = ""
        if proposal_created:
            approval_instruction = (
                "\n\nA file-change proposal is now visible in Electron. "
                "Tell the user briefly that no files have changed yet and that "
                "they should review and click Approve or Reject. Do not paste "
                "the full diff into the spoken response."
            )
        elif edit_requested:
            approval_instruction = (
                "\n\nNo valid file-change proposal was created. State this "
                "clearly and briefly. Do not pretend the change was made and "
                "do not switch to casual conversation."
            )

        return (
            "The following information came from read-only MCP tools connected "
            "to the user's selected local project. Base your answer on this "
            "evidence. Mention relevant relative file paths and line numbers "
            "when the results provide them. If the evidence is insufficient, "
            "say what could not be verified. Do not claim that you edited or "
            "ran the project.\n\n"
            + "\n\n---\n\n".join(evidence)
            + approval_instruction
        )

    def _prepare_git_action(self) -> str:
        """Create and display an exact read-only Git proposal."""
        if self.project_mcp is None:
            return "Project Git access is unavailable because MCP is offline."

        try:
            proposal = json.loads(
                self.project_mcp.prepare_git_proposal()
            )
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            self.events.emit(
                "git_action_error",
                status="error",
                message=message,
            )
            return (
                "The Git proposal failed. Respond with one factual sentence "
                "only: \"I couldn't prepare the Git proposal: "
                f"{message}\" Do not discuss the time, personality, memories, "
                "or ask an unrelated follow-up question."
            )

        if proposal.get("status") != "awaiting_git_approval":
            return "No valid Git proposal was created."

        self.events.emit(
            "git_action_proposed",
            proposal_id=proposal.get("proposal_id", ""),
            branch=proposal.get("branch", ""),
            remote=proposal.get("remote", ""),
            upstream=proposal.get("upstream", ""),
            push_available=proposal.get("push_available", False),
            commit_message=proposal.get("commit_message", ""),
            files=proposal.get("files", []),
            diff_stat=proposal.get("diff_stat", ""),
            diff=proposal.get("diff", ""),
            diff_truncated=proposal.get("diff_truncated", False),
        )
        self._pending_action = "Git"

        return (
            "A Git proposal is visible in Electron. No files have been staged, "
            "committed, or pushed yet. Tell the user to review the exact files, "
            "branch, diff, and editable commit message, then choose Commit & "
            "Push, Commit Only, or Reject. Keep the response to one sentence."
        )

    def resolve_git_action(
        self,
        proposal_id: str,
        approved: bool,
        commit_message: str = "",
        push: bool = True,
    ) -> dict:
        """Execute one Electron-reviewed Git proposal."""
        if self.project_mcp is None:
            result = {
                "status": "error",
                "message": "Project MCP is not connected.",
            }
            self.events.emit("git_action_error", **result)
            return result

        proposal_id = str(proposal_id).strip()
        if not proposal_id:
            result = {
                "status": "error",
                "message": "The Git proposal ID is missing.",
            }
            self.events.emit("git_action_error", **result)
            return result

        try:
            raw_result = self.project_mcp.resolve_git_proposal(
                proposal_id=proposal_id,
                approved=approved,
                commit_message=str(commit_message),
                push=bool(push),
            )
            result = json.loads(raw_result)
        except Exception as error:
            result = {
                "status": "error",
                "proposal_id": proposal_id,
                "message": f"{type(error).__name__}: {error}",
            }
            self.events.emit("git_action_error", **result)
            return result

        status = result.get("status")
        if status == "rejected":
            event_name = "git_action_rejected"
        elif status == "commit_created_push_failed":
            event_name = "git_action_partial"
        else:
            event_name = "git_action_completed"

        self.events.emit(event_name, **result)
        if status in {
            "rejected",
            "committed",
            "pushed",
            "commit_created_push_failed",
        }:
            self._pending_action = ""
        return result

    def resolve_project_change(
        self,
        proposal_id: str,
        approved: bool,
        revised_texts: list[str] | None = None,
    ) -> dict:
        """Resolve one Electron-reviewed proposal and notify the interface."""
        if self.project_mcp is None:
            result = {
                "status": "error",
                "message": "Project MCP is not connected.",
            }
            self.events.emit("project_change_error", **result)
            return result

        proposal_id = str(proposal_id).strip()
        if not proposal_id:
            result = {
                "status": "error",
                "message": "The proposal ID is missing.",
            }
            self.events.emit("project_change_error", **result)
            return result

        try:
            raw_result = self.project_mcp.resolve_proposal(
                proposal_id,
                approved,
                revised_texts=revised_texts if approved else None,
            )
            result = json.loads(raw_result)
        except Exception as error:
            result = {
                "status": "error",
                "proposal_id": proposal_id,
                "message": f"{type(error).__name__}: {error}",
            }
            self.events.emit("project_change_error", **result)
            return result

        event_name = (
            "project_change_applied"
            if result.get("status") == "applied"
            else "project_change_rejected"
        )
        self.events.emit(event_name, **result)
        if result.get("status") in {"applied", "rejected"}:
            self._pending_action = ""
        return result

    def close(self) -> None:
        """Stop background services and active speech."""
        self.screen_monitor.stop()
        self.audio.stop()
        if self.project_mcp is not None:
            self.project_mcp.close()
    
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
        normalized_query = " ".join(str(query).lower().split())
        cached = self._search_cache.get(normalized_query)
        if cached is not None:
            cached_at, cached_result = cached
            if time.monotonic() - cached_at < self._search_cache_seconds:
                print(f"\n[Tool] Using cached web search for: {query}")
                return cached_result
            self._search_cache.pop(normalized_query, None)

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
        self._search_cache[normalized_query] = (
            time.monotonic(),
            result,
        )
        if len(self._search_cache) > self._search_cache_entries:
            oldest_key = min(
                self._search_cache,
                key=lambda key: self._search_cache[key][0],
            )
            self._search_cache.pop(oldest_key, None)

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
