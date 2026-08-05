import json
import re
import threading
import time
import ollama
from collections import deque

from memory.memory_manager import MemoryManager
from memory.extractor import MemoryExtractor
from memory.consolidator import MemoryConsolidator
from memory.context_builder import ContextBuilder
from brain.prompt_builder import PromptBuilder
from brain.conversation_manager import ConversationManager
from brain.memory_ranker import MemoryRanker
from voice.audio_manager import AudioManager
from brain.emotion_engine import EmotionEngine
from core.event_bus import EventBus
from brain.text_filter import TextFilter
from tools.web_search import WebSearchTool
from tools.visual_search import VisualSearchTool
from tools.project_mcp_client import ProjectMCPManager
from config.loader import Config
from brain.personality_loader import PersonalityLoader
from brain.response_messages import build_personality_messages
from brain.response_quality import ResponseQualityGuard
from brain.response_policy import (
    AdviceResponseGuard,
    AnswerCompletionGuard,
    ResponseLimits,
)
from brain.calculation_planner import CalculationPlanner
from brain.desktop_action_planner import (
    DesktopActionPlanner,
    DesktopSurfaceContext,
)
from brain.context_policy import should_include_grounded_context
from brain.brief_response import BriefResponseGenerator
from datetime import datetime
from vision.screen_monitor import ScreenMonitor
from brain.intent_router import IntentDecision, SemanticIntentRouter
from agents.builder import AgentBuilder
from agents.calendar_agent import GoogleCalendarAgent
from agents.coordinator import AgentCoordinator
from agents.research_agent import ResearchAgent
from agents.consent import (
    AGENT_EXECUTION_INTENTS,
    AgentConsentGate,
    SemanticConsentClassifier,
    apply_agent_permission,
)
from agents.registry import AgentRegistry
from agents.task_manager import AgentTaskManager
from security.approval_manager import ApprovalManager
from security.policy import PolicyEngine
from security.computer_consent import ComputerConsentGate
from security.computer_control_mode import ComputerControlMode
from tools.google_calendar import GoogleCalendarTool
from tools.computer_control import (
    ComputerActionRequest,
    ComputerActionResult,
    ComputerControl,
    PreparedComputerAction,
)


_BROWSER_SURFACE_HINTS = (
    "google chrome",
    "microsoft edge",
    "mozilla firefox",
    "brave browser",
    "opera",
    "vivaldi",
)
from tools.safe_browser import SafeBrowserControl
from tools.safe_filesystem import SafeFilesystemControl
from tools.windows_app_catalog import WindowsAppCatalog

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
        self.response_max_words = int(self.config.get(
            "responses",
            "max_words",
            default=45,
            required=False,
        ))
        self.response_max_sentences = int(self.config.get(
            "responses",
            "max_sentences",
            default=2,
            required=False,
        ))
        self.detailed_response_max_words = int(self.config.get(
            "responses",
            "detailed_max_words",
            default=220,
            required=False,
        ))
        self.detailed_response_max_sentences = int(self.config.get(
            "responses",
            "detailed_max_sentences",
            default=8,
            required=False,
        ))
        self.status_max_words = int(self.config.get(
            "responses",
            "status_max_words",
            default=10,
            required=False,
        ))
        self._recent_work_statuses: deque[str] = deque(maxlen=5)

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
            safety_mode=str(self.config.get(
                "routing",
                "project_edit_safety",
                default="enforce",
                required=False,
            )),
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

        self.response_language = str(self.config.get(
            "language",
            "response",
        )).strip().lower()

        self.system_prompt = self.personality_loader.load(
            self.response_language
        )

        self.memory_manager = MemoryManager()
        self.extractor = MemoryExtractor(config=self.config)
        self.consolidator = MemoryConsolidator(config=self.config)
        self.context_builder = ContextBuilder()
        self.conversation = ConversationManager()
        self.memory_ranker = MemoryRanker()
        self.events = EventBus()

        # Agent orchestration is intentionally layered above the proven
        # feature implementations below. Agents decide which constrained
        # capability owns a turn; existing tools still perform the actual work.
        self.agent_registry = AgentRegistry()
        self.agent_tasks = AgentTaskManager()
        self.agent_coordinator = AgentCoordinator(
            registry=self.agent_registry,
            tasks=self.agent_tasks,
        )
        self.agent_consent = AgentConsentGate(
            expiry_seconds=int(self.config.get(
                "routing",
                "agent_offer_expiry_seconds",
                default=300,
                required=False,
            ))
        )
        self.consent_classifier = SemanticConsentClassifier(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
        )
        self.calculation_planner = CalculationPlanner(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
        )
        self.policy = PolicyEngine()
        self.approvals = ApprovalManager(self.policy)
        configured_aliases = self.config.get(
            "computer_control",
            "aliases",
            default={},
            required=False,
        )
        if not isinstance(configured_aliases, dict):
            configured_aliases = {}
        self.computer_control = ComputerControl(
            self.policy,
            enabled=bool(self.config.get(
                "computer_control",
                "enabled",
                default=False,
                required=False,
            )),
            catalog=WindowsAppCatalog(user_aliases=configured_aliases),
            browser=SafeBrowserControl(
                allow_local_urls=bool(self.config.get(
                    "computer_control",
                    "allow_local_urls",
                    default=False,
                    required=False,
                ))
            ),
            filesystem=SafeFilesystemControl(self.config.get(
                "computer_control",
                "allowed_file_roots",
                default=["Desktop", "Documents", "Downloads"],
                required=False,
            )),
        )
        self.computer_consent = ComputerConsentGate(
            expiry_seconds=int(self.config.get(
                "computer_control",
                "consent_expiry_seconds",
                default=90,
                required=False,
            ))
        )
        # Shares computer_control's own live UI observer rather than
        # standing up a second one, so both see the same real window state.
        self.desktop_action_planner = DesktopActionPlanner(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
            observer=self.computer_control.ui_observer,
            computer_control=self.computer_control,
            response_language=self.response_language,
        )
        # Desktop Control is deliberately session-only and starts off after
        # every launch. The config flag remains the master kill switch.
        self.computer_control_mode = ComputerControlMode(enabled=False)
        self.brief_responses = BriefResponseGenerator(
            self.client,
            self.model,
            keep_alive=self.keep_alive,
        )
        self.agent_builder = AgentBuilder(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
        )
        self.calendar_agent = GoogleCalendarAgent(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
        )
        self.calendar_tool = GoogleCalendarTool(self.config)

        self.web_search_tool = WebSearchTool()
        self.research_agent = ResearchAgent(self.search_web)
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
        self._desktop_surface_lock = threading.Lock()
        self._captured_desktop_surface: dict[str, object] = {}
        self._turn_desktop_surface: dict[str, object] = {}
        self._last_desktop_surface: dict[str, object] = {}
        self._memory_store_lock = threading.Lock()
        self._vision_warm_lock = threading.Lock()
        self._vision_warming = False
        self._vision_last_warm = 0.0
        self._turn_lock = threading.Lock()
        self._active_turn_cancel: threading.Event | None = None

    def on_speech_start(self) -> None:
        # Freeze the foreground surface before Electron status events or an
        # interrupted response can move focus. Deictic requests such as
        # "click Settings on this page" must stay bound to what was active
        # when the user began speaking, not whatever is active several model
        # round-trips later.
        surface = self._capture_active_desktop_surface()
        with self._desktop_surface_lock:
            self._captured_desktop_surface = surface

        self.events.emit("speech_started")

        was_speaking = self.audio.is_speaking()
        if was_speaking:
            self.audio.stop()
            with self._turn_lock:
                if self._active_turn_cancel is not None:
                    self._active_turn_cancel.set()

    def _capture_active_desktop_surface(self) -> dict[str, object]:
        """Return a small, stable snapshot of the foreground UI surface."""
        try:
            window = self.computer_control.ui_observer.get_active_window()
        except Exception:
            window = None
        if window is None:
            return {}

        title = str(getattr(window, "title", "") or "").strip()
        application = str(
            getattr(window, "app_name", "")
            or getattr(window, "class_name", "")
            or ""
        ).strip()
        # Text entry and status animations can briefly focus Elaina's own
        # Electron window. That UI is not what "this page" normally refers
        # to; preserve the most recent externally controlled surface instead
        # of rebinding the task to the assistant overlay.
        if "elaina" in title.casefold() and hasattr(
            self, "_desktop_surface_lock"
        ):
            with self._desktop_surface_lock:
                previous = dict(self._last_desktop_surface)
            if previous:
                return previous
        title_key = title.casefold()
        application_key = application.casefold()
        kind = (
            "browser"
            if any(hint in title_key for hint in _BROWSER_SURFACE_HINTS)
            or application_key in {
                "chrome", "google chrome", "msedge", "microsoft edge",
                "firefox", "mozilla firefox", "brave", "brave browser",
                "opera", "vivaldi",
            }
            else "native"
        )
        return {
            "title": title,
            "application": application,
            "kind": kind,
            "identity": str(getattr(window, "identity", "") or ""),
            "handle": getattr(window, "handle", None),
            "process_id": getattr(window, "process_id", None),
        }

    def _desktop_surface_for_turn(self) -> dict[str, object]:
        """Use the utterance-time surface, with a live fallback for API calls."""
        if not hasattr(self, "_desktop_surface_lock"):
            return {}
        with self._desktop_surface_lock:
            current = dict(self._turn_desktop_surface)
            captured = dict(self._captured_desktop_surface)
            previous = dict(self._last_desktop_surface)
        if current:
            return current
        if captured:
            return captured

        captured = self._capture_active_desktop_surface()
        if captured:
            with self._desktop_surface_lock:
                self._captured_desktop_surface = dict(captured)
            return captured
        return previous

    def _begin_desktop_turn(self) -> dict[str, object]:
        """Consume the utterance snapshot so it cannot leak into a later turn."""
        with self._desktop_surface_lock:
            captured = dict(self._captured_desktop_surface)
            self._captured_desktop_surface = {}
        if not captured:
            captured = self._capture_active_desktop_surface()
        with self._desktop_surface_lock:
            self._turn_desktop_surface = dict(captured)
        return captured

    def _remember_desktop_surface(self, surface: dict[str, object]) -> None:
        if not surface:
            return
        if not hasattr(self, "_desktop_surface_lock"):
            return
        with self._desktop_surface_lock:
            self._last_desktop_surface = dict(surface)

    def cancel_active_turn(self) -> None:
        """Unconditionally stop active generation and queued speech."""
        self.audio.stop()
        with self._turn_lock:
            if self._active_turn_cancel is not None:
                self._active_turn_cancel.set()

    def set_computer_control_mode(self, enabled: bool) -> bool:
        """Set the UI-owned session mode and publish the authoritative state."""
        available = bool(self.computer_control.enabled)
        active = self.computer_control_mode.set_enabled(
            bool(enabled) and available
        )
        if not active:
            # A later "yes" must never revive an action prepared while control
            # was enabled. High-risk operations can be requested again.
            self.computer_consent.clear()
        self.publish_computer_control_mode()
        print(
            "[Computer Control Mode] "
            f"{'ON' if active else 'OFF'}"
        )
        return active

    def publish_computer_control_mode(self) -> None:
        """Synchronize Electron with backend state after toggles/reconnects."""
        self.events.emit(
            "computer_control_mode_changed",
            enabled=self.computer_control_mode.enabled,
            available=bool(self.computer_control.enabled),
        )

    def _turn_is_cancelled(self) -> bool:
        with self._turn_lock:
            return bool(
                self._active_turn_cancel is not None
                and self._active_turn_cancel.is_set()
            )

    def _build_conversation_state(self) -> dict:
        pending_offer = self.agent_consent.peek()
        pending_computer = self.computer_consent.peek()
        return {
            "active_topic": self._active_topic,
            "active_entity": self._active_entity,
            "entity_aliases": self._entity_aliases,
            "grounded_context": dict(self._grounded_context),
            "computer_control_enabled": self.computer_control_mode.enabled,
            "active_desktop_surface": self._desktop_surface_for_turn(),
            "pending_agent_offer": (
                pending_offer.public_context()
                if pending_offer is not None
                else None
            ),
            "pending_computer_action": (
                pending_computer.public_context()
                if pending_computer is not None
                else None
            ),
            "available_agents": [
                {
                    "name": agent.name,
                    "description": agent.description,
                    "tools": list(agent.tools),
                }
                for agent in self.agent_registry.all()
                if agent.enabled
            ],
        }

    def _capability_context(self) -> str:
        mode = "ON" if self.computer_control_mode.enabled else "OFF"
        desktop = (
            "Desktop Control Mode: " + mode + ". Elaina can open, close, and "
            "force-quit discovered Windows applications; open validated public "
            "websites; and create or recycle exact files and folders inside "
            "allowed roots. She can inspect the active native Windows UI and "
            "use verified common controls to focus, click, type, select, and "
            "scroll. A request about 'this page' stays locked to the captured "
            "foreground surface and never falls back to an unrelated app. "
            "Reliable DOM-level browser-page control is not available until "
            "Phase 4C. When the mode is OFF, these actions cannot run. "
            "If one of these supported controls would make the user's task "
            "easier, recommend the visible Computer Control toggle without "
            "claiming the mode is active. Force-quit and deletion always need "
            "a separate confirmation. Unsupported controls must not be "
            "promised merely because the mode can be enabled."
        )
        return self.agent_registry.capability_context() + "\n" + desktop

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
        if route.topic_shift and route.intent != "fact_check":
            # Verified evidence belongs to its original subject. Carrying it
            # into an unrelated turn caused later questions to stay anchored
            # to the last identified image.
            self._grounded_context = {
                "subject": "",
                "statement": "",
                "source": "",
            }
            if not route.entity:
                self._active_entity = ""
        if route.topic:
            self._active_topic = route.topic
        elif route.intent in {
            "knowledge_question",
            "calculation",
            "web_search",
        }:
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

    def _grounded_context_is_relevant(self, route) -> bool:
        """Use retrieved evidence only for an explicit semantic follow-up."""
        return should_include_grounded_context(
            has_statement=bool(
                self._grounded_context.get("statement", "").strip()
            ),
            intent=route.intent,
            is_follow_up=route.is_follow_up,
            topic_shift=route.topic_shift,
        )

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
        *,
        include_grounded: bool = False,
        reset_history: bool = False,
    ) -> list[dict]:
        """Build a grounded answer without replacing Elaina's personality."""
        grounded_context = self._grounded_context_text()
        context_sections: list[tuple[str, str]] = []
        if include_grounded and grounded_context:
            context_sections.append((
                "RECENT VERIFIED CONTEXT",
                grounded_context,
            ))
        if evidence:
            context_sections.append((
                "CURRENT RETRIEVED EVIDENCE",
                evidence,
            ))
        context_sections.append((
            "CURRENTLY AVAILABLE AI AGENTS",
            self._capability_context(),
        ))

        return build_personality_messages(
            system_prompt=self.system_prompt,
            history=(
                [] if reset_history else self.conversation.get_history()
            ),
            user_input=question,
            context_sections=context_sections,
        )

    def _build_tool_result_messages(
        self,
        user_input: str,
        tool_result: str,
    ) -> list[dict]:
        """Let personality.txt phrase a trusted action result for the user."""
        return build_personality_messages(
            system_prompt=self.system_prompt,
            history=self.conversation.get_history(),
            user_input=user_input,
            context_sections=(
                ("TRUSTED TOOL RESULT", tool_result),
                (
                    "CURRENTLY AVAILABLE AI AGENTS",
                    self._capability_context(),
                ),
            ),
        )

    def _announce_work_status(
        self,
        intent: str,
        user_input: str,
    ) -> None:
        """Use Ollama for one brief acknowledgement before slower work."""
        work_by_intent = {
            "web_search": "A one-time web search is starting.",
            "entity_correction": (
                "The web search is restarting with the corrected entity."
            ),
            "screen_analysis": (
                "The selected screen region is being analyzed."
            ),
            "project_question": (
                "The Coding Agent is inspecting relevant project files."
            ),
            "project_edit": (
                "The Coding Agent is inspecting files and preparing a change "
                "proposal. No file has changed yet."
            ),
            "git_commit": (
                "The Git Agent is preparing a commit proposal. Nothing has "
                "been committed yet."
            ),
            "git_publish": (
                "The Git Agent is preparing a commit and push proposal. "
                "Nothing has been committed or pushed yet."
            ),
            "agent_create": (
                "The Agent Builder is checking requirements and permissions."
            ),
            "calendar_action": (
                "The Calendar Agent is preparing event details. Nothing has "
                "been added yet."
            ),
        }
        work = work_by_intent.get(intent, "")
        if not work:
            return

        text = self.brief_responses.generate(
            "work_started",
            subject=intent.replace("_", " "),
            detail=f"User request: {user_input.strip()} Work: {work}",
        )

        self._recent_work_statuses.append(text)
        print(f"[Status] Elaina: {text}")
        self.events.emit(
            "assistant_status",
            text=text,
            intent=intent,
        )
        self.audio.speak(text)

    @staticmethod
    def _speak_window_list(windows) -> str:
        if not windows:
            return "I don't see any windows open right now."
        titles = [window.title for window in windows]
        active = next((window.title for window in windows if window.is_active), "")
        if len(titles) == 1:
            return f"You have one window open: {titles[0]}."
        preview = titles[:6]
        summary = ", ".join(preview)
        remaining = len(titles) - len(preview)
        if remaining > 0:
            summary += f", and {remaining} more"
        front = f" {active} is currently in front." if active else ""
        return f"You have {len(titles)} windows open: {summary}.{front}"

    @staticmethod
    def _speak_window_description(observation) -> str:
        if observation.status != "observed":
            return observation.message
        names = [control.name for control in observation.controls]
        preview = names[:6]
        summary = ", ".join(preview)
        remaining = len(names) - len(preview)
        if remaining > 0:
            summary += f", and {remaining} more"
        return f"{observation.title} has {len(names)} controls: {summary}."

    def _handle_computer_action(
        self,
        route: IntentDecision,
        *,
        approved_action: PreparedComputerAction | None = None,
        original_request: str = "",
    ) -> tuple[str, ComputerActionResult | None]:
        """Return one outcome-locked line and one trusted action result."""
        if route.computer_operation in {"none", "unsupported"}:
            return self.brief_responses.generate(
                "blocked",
                subject=route.action_target,
            ), None

        if not self.computer_control_mode.enabled:
            return self.brief_responses.generate(
                "control_mode_off",
                subject=route.action_target,
                detail=(
                    "Desktop Control Mode is off. Recommend turning on the "
                    "visible Computer Control toggle for this supported action."
                ),
                operation=route.computer_operation,
            ), None

        # ui_action is goal-driven and multi-step, not a single resolved
        # target like open_app/delete_file -- brain.desktop_action_planner
        # owns the whole loop, deciding per-click whether confirmation is
        # needed, so it never goes through prepare()/execute() below.
        if route.computer_operation == "ui_action" or (
            approved_action is not None and approved_action.operation == "ui_action"
        ):
            return self._handle_ui_action(
                route,
                approved_action=approved_action,
                original_request=original_request,
            )

        if approved_action is not None and not (
            self.computer_control.requires_extra_confirmation(
                approved_action.operation
            )
        ):
            return self.brief_responses.generate(
                "blocked",
                subject=approved_action.display_name,
            ), None

        if approved_action is not None:
            result = self.computer_control.execute(
                approved_action,
                confirmed=True,
            )
        else:
            prepared_result = self.computer_control.prepare(
                ComputerActionRequest(
                    operation=route.computer_operation,
                    target=route.action_target,
                    location=route.computer_location,
                    url=route.computer_url,
                )
            )
            if prepared_result.prepared is not None and (
                self.computer_control.requires_extra_confirmation(
                    route.computer_operation
                )
            ):
                self.agent_consent.clear()
                self.computer_consent.offer(
                    prepared=prepared_result.prepared,
                    reason=route.reason,
                )
                return self.brief_responses.generate(
                    (
                        "force_quit_offer"
                        if route.computer_operation == "force_quit_app"
                        else "delete_offer"
                    ),
                    subject=prepared_result.display_name,
                    detail=prepared_result.prepared.request,
                    operation=route.computer_operation,
                ), prepared_result
            result = (
                self.computer_control.execute(prepared_result.prepared)
                if prepared_result.prepared is not None
                else prepared_result
            )

        # Observation results carry real information (which windows exist,
        # what a window contains), not just a pass/fail outcome, so they
        # can't go through brief_responses' generic short acknowledgements
        # (built for "Got it, X is open," capped near 7 words) without
        # losing the actual content the user asked for. The spoken summary
        # here is built directly from the same real data, never an LLM
        # paraphrase, so it carries no hallucination risk -- but the full
        # detail (every control, every window) still reaches Electron
        # through computer_result.message on the completed event below,
        # unabridged, for "what Elaina currently sees."
        if result.status == "windows_listed":
            return (
                self._speak_window_list(self.computer_control.ui_observer.list_windows()),
                result,
            )
        if result.status == "window_described":
            observation = self.computer_control.ui_observer.describe_window(
                result.target or result.display_name
            )
            return self._speak_window_description(observation), result

        response_kind = {
            "opened": "opened",
            "closed": "closed",
            "close_requested": "close_requested",
            "force_quit": "force_quit",
            "url_opened": "url_opened",
            "file_created": "file_created",
            "folder_created": "folder_created",
            "file_deleted": "file_deleted",
            "folder_deleted": "folder_deleted",
            "not_found": "not_found",
            "not_running": "not_running",
            "ambiguous": "ambiguous",
            "already_exists": "already_exists",
            "item_not_found": "item_not_found",
            "wrong_type": "wrong_type",
            "invalid_target": "invalid_target",
            "outside_allowed": "outside_allowed",
            "parent_not_found": "invalid_target",
            "needs_location": "needs_location",
            "failed": "failed",
            "disabled": "blocked",
            "blocked": "blocked",
        }.get(result.status, "blocked")
        return self.brief_responses.generate(
            response_kind,
            subject=(
                result.display_name
                or result.target
                or route.action_target
            ),
            detail=result.message,
            operation=result.operation,
        ), result

    def _handle_ui_action(
        self,
        route: IntentDecision,
        *,
        approved_action: PreparedComputerAction | None,
        original_request: str = "",
    ) -> tuple[str, ComputerActionResult | None]:
        """Phase 4B.2: goal-driven UI actions (click/type/focus/select/scroll).

        Every step is a real, verified tools.windows_ui_control call, not an
        LLM claim -- so the spoken result here is the planner's own
        tool-grounded summary, never re-paraphrased by brief_responses.
        The one exception is the confirmation *question* itself, which goes
        through brief_responses' "ui_action_offer" kind for the same varied,
        natural phrasing already used for force-quit/delete offers.
        """
        if approved_action is not None:
            plan_result = self.desktop_action_planner.resume_confirmed_click(
                window_title=approved_action.window_title,
                control_name=approved_action.display_name,
                window_snapshot=approved_action.window_snapshot,
            )
        else:
            # The router may improve the semantic goal while accidentally
            # dropping deictic scope such as "on this page". Keep both forms:
            # the normalized request tells the planner what to do, while the
            # original wording preserves which foreground surface is allowed.
            normalized_goal = str(route.normalized_request or "").strip()
            original_goal = str(original_request or "").strip()
            planner_goal = normalized_goal or original_goal
            if (
                original_goal
                and original_goal.casefold() != planner_goal.casefold()
            ):
                planner_goal = (
                    f"{planner_goal}\n"
                    f"Original user request: {original_goal}"
                )
            plan_result = self.desktop_action_planner.act(
                planner_goal,
                surface_context=DesktopSurfaceContext.from_public_snapshot(
                    self._desktop_surface_for_turn()
                ),
            )

        print(
            "[Computer Control] action=ui_action target="
            f"{route.action_target or '(none)'} status={plan_result.status} "
            f"rounds={plan_result.model_rounds} "
            f"action_steps={plan_result.action_steps} "
            f"recovery={plan_result.recovery_used} "
            f"failure={plan_result.failure_code or '(none)'}"
        )

        resolved_surface = plan_result.surface_context.to_public_snapshot()
        if resolved_surface:
            self._remember_desktop_surface(resolved_surface)

        if plan_result.status == "needs_confirmation":
            pending = plan_result.pending
            prepared = PreparedComputerAction(
                operation="ui_action",
                target=pending.control_name,
                display_name=pending.control_name,
                window_title=pending.window_title,
                window_snapshot=pending.window_snapshot,
            )
            self.agent_consent.clear()
            self.computer_consent.offer(prepared=prepared, reason=route.reason)
            return self.brief_responses.generate(
                "ui_action_offer",
                subject=pending.control_name,
                detail=plan_result.summary,
                operation="ui_action",
            ), ComputerActionResult(
                status="prepared",
                target=pending.control_name,
                display_name=pending.control_name,
                message=plan_result.summary,
                operation="ui_action",
                prepared=prepared,
            )

        succeeded = plan_result.status == "done"
        message = plan_result.summary.strip() or (
            "That's done." if succeeded else "I couldn't complete that."
        )
        return message, ComputerActionResult(
            status="ui_action_done" if succeeded else "ui_action_failed",
            target=route.action_target,
            display_name=route.action_target,
            message=message,
            operation="ui_action",
        )

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

        self._begin_desktop_turn()

        turn_cancel = threading.Event()
        with self._turn_lock:
            self._active_turn_cancel = turn_cancel
        self._turn_visual_subject = ""

        self.events.emit(
            "user_message",
            text=user_input,
        )

        ####################################################
        # Retrieve Memories
        ####################################################

        memory_text = ""

        ####################################################
        # Build Prompt
        ####################################################
        route_started = time.perf_counter()
        continuing_agent_flow = bool(
            self.agent_builder.active or self.calendar_agent.active
        )
        has_explicit_attachment = bool(
            screen_region is not None or screen_snapshot is not None
        )
        pending_offer = self.agent_consent.peek()
        pending_computer = self.computer_consent.peek()
        locked_response = ""
        approved_computer_action: PreparedComputerAction | None = None

        def route_current(
            transcript: str,
        ) -> IntentDecision:
            return self.intent_router.route(
                transcript,
                recent_turns=list(self._router_history),
                has_screen_selection=has_explicit_attachment,
                project_tools_available=self.project_mcp is not None,
                conversation_state=self._build_conversation_state(),
                pending_action=self._pending_action,
                computer_control_enabled=self.computer_control_mode.enabled,
            )

        if self.agent_builder.active:
            route = IntentDecision(
                intent="agent_create",
                confidence=1.0,
                normalized_request=user_input,
                reason="Continuing the active agent setup.",
                speech_act="information_request",
                action_requested=True,
                action_target="agent setup",
            )
        elif self.calendar_agent.active:
            route = IntentDecision(
                intent="calendar_action",
                confidence=1.0,
                normalized_request=user_input,
                reason="Continuing the active calendar event draft.",
                speech_act="information_request",
                action_requested=True,
                action_target="calendar event",
            )
        elif pending_computer is not None and not has_explicit_attachment:
            consent = self.consent_classifier.classify(
                user_input,
                pending_computer,
                recent_turns=list(self._router_history),
            )
            if consent.decision == "accept":
                self.computer_consent.clear()
                approved_computer_action = pending_computer.prepared
                route = IntentDecision(
                    intent="computer_action",
                    confidence=consent.confidence,
                    normalized_request=pending_computer.request,
                    reason="The user accepted the exact high-risk confirmation.",
                    is_follow_up=True,
                    speech_act="action_request",
                    action_requested=True,
                    action_target=pending_computer.target_name,
                    computer_operation=pending_computer.operation,
                )
            elif consent.decision == "modify":
                self.computer_consent.clear()
                revised_request = consent.modified_request.strip()
                route = route_current(
                    revised_request or user_input,
                )
            elif consent.decision == "reject":
                self.computer_consent.clear()
                route = IntentDecision(
                    intent="conversation",
                    confidence=consent.confidence,
                    normalized_request=user_input,
                    reason="The user declined the pending computer action.",
                    is_follow_up=True,
                )
                locked_response = self.brief_responses.generate(
                    "declined",
                    subject=pending_computer.target_name,
                    operation=pending_computer.operation,
                )
            elif consent.decision == "unrelated":
                self.computer_consent.clear()
                route = route_current(user_input)
            else:
                route = IntentDecision(
                    intent="conversation",
                    confidence=consent.confidence,
                    normalized_request=user_input,
                    reason="The high-risk confirmation reply was unclear.",
                    is_follow_up=True,
                )
                locked_response = self.brief_responses.generate(
                    (
                        "force_quit_offer"
                        if pending_computer.operation == "force_quit_app"
                        else "delete_offer"
                        if pending_computer.operation in {
                            "delete_file", "delete_folder"
                        }
                        else "ui_action_offer"
                        if pending_computer.operation == "ui_action"
                        else "blocked"
                    ),
                    subject=pending_computer.target_name,
                    detail=pending_computer.request,
                    operation=pending_computer.operation,
                )
        elif pending_offer is not None and not has_explicit_attachment:
            consent = self.consent_classifier.classify(
                user_input,
                pending_offer,
                recent_turns=list(self._router_history),
            )
            if consent.decision == "unrelated":
                # The dedicated consent classifier has established that this
                # is a new topic. Clear the stale offer before normal routing.
                self.agent_consent.clear()
                route = route_current(user_input)
            else:
                route = IntentDecision(
                    intent="agent_consent",
                    confidence=consent.confidence,
                    normalized_request=user_input,
                    reason=consent.reason,
                    is_follow_up=True,
                    speech_act="approval_response",
                    consent_decision=consent.decision,
                    offered_request=consent.modified_request,
                )
        else:
            if has_explicit_attachment and pending_computer is not None:
                self.computer_consent.clear()
            route = route_current(user_input)
        route, agent_permission_context = apply_agent_permission(
            self.agent_consent,
            route,
            user_input=user_input,
            has_explicit_attachment=has_explicit_attachment,
            continuing_agent_flow=continuing_agent_flow,
            available_intents={
                intent
                for agent in self.agent_registry.all()
                if agent.enabled
                for intent in agent.intents
            },
        )
        timings["route"] = time.perf_counter() - route_started
        self._update_conversation_state(route)
        print(
            f"[Router] {route.intent} ({route.confidence:.2f}): "
            f"{route.reason or route.normalized_request}"
        )
        if route.intent in {"knowledge_question", "web_search"}:
            print(
                "[Router Source] "
                f"freshness={route.information_freshness} "
                f"external={route.requires_external_evidence} "
                f"verify={route.verification_required}"
            )
        if route.normalized_request != user_input:
            print(
                f"[Router] Interpreted transcript as: "
                f"{route.normalized_request}"
            )

        agent_task_id = None
        if route.intent in AGENT_EXECUTION_INTENTS:
            assignment_intent = route.intent
            if (
                route.intent == "calendar_action"
                and not self.agent_registry.has_agent(
                    "google_calendar_agent"
                )
            ):
                assignment_intent = "agent_create"
            assignment = self.agent_coordinator.assign(
                assignment_intent,
                route.normalized_request,
            )
            agent_task_id = assignment.task.id
            self.events.emit(
                "agent_task_started",
                task_id=agent_task_id,
                agent_id=assignment.definition.id,
                agent_name=assignment.definition.name,
                intent=route.intent,
            )
            if route.intent == "fact_check" and route.search_query:
                self._announce_work_status(
                    "web_search",
                    route.normalized_request,
                )
            else:
                self._announce_work_status(
                    route.intent,
                    route.normalized_request,
                )

        memory_started = time.perf_counter()
        use_memory = (
            route.intent == "conversation"
            and route.memory_relevant
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

        # The router's local safety policy must explicitly authorize a write
        # proposal. A model label alone is never enough to invoke MCP edits.
        project_edit_requested = (
            route.intent == "project_edit"
            and route.action_requested
        )
        use_screen_vision = route.intent == "screen_analysis"
        forced_response = ""
        if route.intent == "computer_action" and not locked_response:
            locked_response, computer_result = self._handle_computer_action(
                route,
                approved_action=approved_computer_action,
                original_request=user_input,
            )

            if computer_result is not None:
                self.events.emit(
                    "computer_action_completed",
                    status=computer_result.status,
                    operation=computer_result.operation,
                    target=computer_result.target,
                    message=computer_result.message,
                )
        screen_target = route.screen_target or "configured"
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
            screen_text=screen_context,
            user_input=user_input,
        )
        grounded_context = self._grounded_context_text()
        if (
            grounded_context
            and self._grounded_context_is_relevant(route)
            and route.intent in {
            "conversation",
            "clarification",
            "fact_check",
            }
        ):
            context_prompt += (
                "\n\nRECENT VERIFIED CONTEXT\n"
                f"{grounded_context}"
            )
        if route.intent == "time_question":
            context_prompt += (
                "\n\nCURRENT LOCAL TIME CONTEXT\n"
                f"{self.build_time_context()}"
            )
        context_prompt += (
            "\n\nCURRENTLY AVAILABLE AI AGENTS\n"
            f"{self._capability_context()}"
        )
        if agent_permission_context:
            context_prompt += (
                "\n\nAGENT PERMISSION STATE\n"
                f"{agent_permission_context}"
            )

        ####################################################
        # Ask Qwen
        ####################################################

        messages = self.conversation.build_messages(
            system_prompt=self.system_prompt,
            context_prompt=context_prompt,
            history=[] if route.topic_shift else None,
        )

        calculation_plan = None
        if route.intent == "knowledge_question":
            messages = self._build_factual_messages(
                route.normalized_request,
                include_grounded=self._grounded_context_is_relevant(route),
                reset_history=route.topic_shift,
            )
        elif route.intent == "calculation":
            # A small local model doing multi-step arithmetic in its head is
            # exactly where it goes wrong (measured: three different wrong
            # totals across three temperatures on one proration question).
            # The planner only asks it to translate the problem into plain
            # arithmetic expressions -- a sandboxed evaluator computes the
            # actual numbers, so they can't be a language-model math mistake.
            calculation_started = time.perf_counter()
            calculation_plan = self.calculation_planner.plan(
                route.normalized_request
            )
            timings["calculation_plan"] = (
                time.perf_counter() - calculation_started
            )
            if calculation_plan is not None:
                messages = self._build_tool_result_messages(
                    user_input=route.normalized_request,
                    tool_result=calculation_plan.as_trusted_result_text(),
                )
            else:
                # Use the router's self-contained interpretation so a short
                # follow-up such as "How much did I make?" retains the
                # values from the immediately preceding turns without
                # loading personal memory. This is the fallback when the
                # planner's own request fails or produces untrusted output.
                messages = self._build_factual_messages(
                    route.normalized_request,
                    reset_history=route.topic_shift,
                )
        elif route.intent == "time_question":
            messages = self._build_factual_messages(
                route.normalized_request,
                self.build_time_context(),
                reset_history=route.topic_shift,
            )

        turn_grounding_source = ""
        turn_grounding_subject = ""
        
        if not use_screen_vision and route.intent == "web_search":
            search_started = time.perf_counter()
            try:
                research_result = self.research_agent.research(
                    request=route.normalized_request,
                    search_query=(
                        route.search_query or route.normalized_request
                    ),
                    max_results=5,
                    verify=route.verification_required,
                )
                self._last_search_query = research_result.queries[0]
                messages = self._build_factual_messages(
                    route.normalized_request,
                    (
                        f"AS-OF DATE: {datetime.now().strftime('%Y-%m-%d')}\n"
                        f"{research_result.evidence}"
                    ),
                    include_grounded=self._grounded_context_is_relevant(
                        route
                    ),
                    reset_history=route.topic_shift,
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
                        include_grounded=True,
                        reset_history=False,
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
                    include_grounded=True,
                    reset_history=False,
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
                    include_grounded=True,
                    reset_history=False,
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

        if route.intent == "agent_create":
            if self._pending_action:
                forced_response = (
                    f"A {self._pending_action} proposal is already waiting in "
                    "Electron. Review it before creating another capability."
                )
            else:
                build_result = self.agent_builder.handle(
                    route.normalized_request
                )
                forced_response = build_result.message

                if build_result.status == "input_required":
                    self.agent_tasks.update(
                        agent_task_id,
                        "input_required",
                        build_result.message,
                    )
                elif build_result.status in {"unsupported", "cancelled"}:
                    self.agent_tasks.update(
                        agent_task_id,
                        "completed",
                        build_result.message,
                    )

                if (
                    build_result.status == "ready"
                    and build_result.definition is not None
                ):
                    definition = build_result.definition
                    settings = dict(definition.get("settings", {}))
                    tools = list(definition.get("tools", []))
                    credentials_ready, credential_message = (
                        self.calendar_tool.readiness()
                    )
                    proposal = self.approvals.create(
                        action="agent.install",
                        title="Install Google Calendar Agent",
                        summary=(
                            "Activate a constrained agent that can prepare "
                            "Google Calendar events. Every event creation will "
                            "still require a separate approval."
                        ),
                        details=[
                            {
                                "label": "Agent",
                                "value": str(definition.get("name", "")),
                            },
                            {
                                "label": "Allowed tools",
                                "value": ", ".join(tools),
                            },
                            {
                                "label": "Time zone",
                                "value": str(settings.get("timezone", "")),
                            },
                            {
                                "label": "Calendar",
                                "value": str(settings.get("calendar_id", "")),
                            },
                            {
                                "label": "Default duration",
                                "value": (
                                    f"{settings.get('default_duration_minutes')} "
                                    "minutes"
                                ),
                            },
                            {
                                "label": "Credentials",
                                "value": credential_message,
                            },
                        ],
                        payload={
                            "definition": definition,
                            "task_id": agent_task_id,
                            "credentials_ready": credentials_ready,
                        },
                    )
                    self._pending_action = "Agent installation"
                    self.agent_tasks.update(
                        agent_task_id,
                        "waiting_approval",
                        "Waiting for agent installation approval.",
                    )
                    self.events.emit(
                        "action_approval_requested",
                        **proposal.public_payload(),
                    )

        if route.intent == "calendar_action":
            calendar_definition = self.agent_registry.get(
                "google_calendar_agent"
            )
            if calendar_definition is None:
                build_result = self.agent_builder.handle(
                    route.normalized_request
                )
                forced_response = (
                    "I don't have a Google Calendar Agent yet. My "
                    "recommendation is to add one with permission to create "
                    "events only after approval. "
                    + build_result.message
                )
                self.agent_tasks.update(
                    agent_task_id,
                    "input_required",
                    "Calendar Agent setup information is required.",
                )
            elif self._pending_action:
                forced_response = (
                    f"A {self._pending_action} proposal is already waiting in "
                    "Electron. Review it before preparing another event."
                )
            else:
                credentials_ready, credential_message = (
                    self.calendar_tool.readiness()
                )
                if not credentials_ready:
                    calendar_result = None
                    forced_response = (
                        "The Google Calendar Agent is installed, but it cannot "
                        "write events yet. "
                        + credential_message
                        + " Add the OAuth Desktop credential path to .env as "
                        "GOOGLE_CALENDAR_CREDENTIALS, then restart Elaina."
                    )
                    self.agent_tasks.update(
                        agent_task_id,
                        "failed",
                        forced_response,
                    )
                else:
                    calendar_result = self.calendar_agent.handle(
                        route.normalized_request,
                        calendar_definition,
                    )
                    forced_response = calendar_result.message

                if (
                    calendar_result is not None
                    and calendar_result.status == "ready"
                    and calendar_result.event is not None
                ):
                    event = calendar_result.event
                    proposal = self.approvals.create(
                        action="calendar.create_event",
                        title="Create Google Calendar event",
                        summary=(
                            "Create this exact event in Google Calendar. "
                            "Nothing has been written yet."
                        ),
                        details=[
                            {
                                "label": "Title",
                                "value": str(event["summary"]),
                            },
                            {
                                "label": "Starts",
                                "value": str(
                                    event["start"]["dateTime"]
                                ),
                            },
                            {
                                "label": "Ends",
                                "value": str(event["end"]["dateTime"]),
                            },
                            {
                                "label": "Time zone",
                                "value": str(
                                    event["start"]["timeZone"]
                                ),
                            },
                            {
                                "label": "Calendar",
                                "value": calendar_result.calendar_id,
                            },
                            {
                                "label": "Location",
                                "value": str(
                                    event.get("location") or "(none)"
                                ),
                            },
                        ],
                        payload={
                            "calendar_id": calendar_result.calendar_id,
                            "event": event,
                            "task_id": agent_task_id,
                        },
                    )
                    self._pending_action = "Calendar event"
                    self.agent_tasks.update(
                        agent_task_id,
                        "waiting_approval",
                        "Waiting for calendar event approval.",
                    )
                    self.events.emit(
                        "action_approval_requested",
                        **proposal.public_payload(),
                    )
                elif (
                    calendar_result is not None
                    and calendar_result.status == "input_required"
                ):
                    self.agent_tasks.update(
                        agent_task_id,
                        "input_required",
                        calendar_result.message,
                    )

        # Git writes use a deterministic snapshot and approval flow rather than
        # asking the language model to choose commands or files.
        if not use_screen_vision and route.intent in {
            "git_commit",
            "git_publish",
        }:
            self.policy.get(
                "git.push"
                if route.intent == "git_publish"
                else "git.commit"
            )
            if self._pending_action:
                forced_response = (
                    f"A {self._pending_action} proposal is already waiting in "
                    "Electron. Review it before creating another action."
                )
            else:
                project_started = time.perf_counter()
                self._prepare_git_action()
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
            if project_edit_requested:
                self.policy.get("project.write")
            else:
                self.policy.get("project.read")
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
                    messages[-1]["content"] += (
                        "\n\nTRUSTED PROJECT TOOL RESULT\n"
                        f"{project_context}"
                    )

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
                messages = self.conversation.build_messages(
                    system_prompt=self.system_prompt,
                    context_prompt=(
                        "CURRENT USER MESSAGE\n"
                        f"{user_input}\n\n"
                        "VERIFIED REVERSE-IMAGE EVIDENCE\n"
                        f"{verification_context}\n\n"
                        "SPOKEN ANSWER REQUIREMENTS\n"
                        "Give the identification or answer directly in one or "
                        "two natural sentences. Use the evidence silently. Do "
                        "not mention matching pages, URLs, evidence lists, "
                        "confidence calculations, or retrieval mechanics.\n\n"
                        "CURRENTLY AVAILABLE AI AGENTS\n"
                        f"{self._capability_context()}"
                    ),
                )
            else:
                messages = [
                    {
                        "role": "system",
                        "content": self.system_prompt,
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{user_input}\n\n"
                            f"{screen_context}\n\n"
                            "Answer the exact visual question directly in one "
                            "or two natural spoken sentences. Do not write a "
                            "report, evidence list, heading, or confidence "
                            "label.\n\n"
                            "CURRENTLY AVAILABLE AI AGENTS\n"
                            f"{self._capability_context()}"
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
            if route.intent in {"conversation", "agent_offer"}
            else 0.1
        )

        # Action handlers produce exact, trusted state such as "waiting for
        # approval" or "nothing changed." The same language model now phrases
        # that state through personality.txt instead of exposing a canned agent
        # response. Keep the original result as a no-hallucination fallback.
        tool_result_fallback = locked_response or forced_response
        if forced_response:
            messages = self._build_tool_result_messages(
                user_input=user_input,
                tool_result=forced_response,
            )
            forced_response = ""

        detailed_response = route.detailed_response
        max_words = (
            self.detailed_response_max_words
            if detailed_response
            else self.response_max_words
        )
        max_sentences = (
            self.detailed_response_max_sentences
            if detailed_response
            else self.response_max_sentences
        )
        response_limits = ResponseLimits(
            max_words=max_words,
            max_sentences=max_sentences,
        )
        calculation_response = route.intent == "calculation"
        recommendation_response = (
            route.recommendation_needed or route.speech_act == "advice"
        )
        # A verified plan already has exact, tool-computed numbers baked into
        # messages as a trusted result -- Elaina only has to phrase it
        # naturally, the same as any other tool result. Only the fallback
        # path (the planner failed) still asks the model to do the
        # arithmetic itself, so only that path needs the unlimited-length
        # "show your work" instruction below.
        calculation_verified = (
            calculation_response and calculation_plan is not None
        )
        calculation_needs_own_math = (
            calculation_response and not calculation_verified
        )
        response_instruction = response_limits.instruction(
            calculation=calculation_needs_own_math,
            recommendation=recommendation_response,
        )
        # The first unverified calculation draft is generated without a
        # length target so it can show brief working before the result;
        # response_instruction (with the real voice-length limits) is used
        # later only to condense a complete draft that ran long, never to
        # constrain this first pass.
        generation_instruction = (
            ResponseLimits().instruction(calculation=True)
            if calculation_needs_own_math
            else response_instruction
        )
        if calculation_needs_own_math:
            messages[-1]["content"] += (
                "\n\nRESOLVED CALCULATION REQUEST\n"
                f"{route.normalized_request}"
            )
        messages[-1]["content"] += (
            "\n\nVOICE RESPONSE REQUIREMENTS\n"
            f"{generation_instruction}"
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
        effective_forced_response = (
            locked_response or forced_response or blocked_identification_reply
        )
        num_predict = response_limits.generation_budget(
            detailed=detailed_response,
            calculation=calculation_needs_own_math,
        )

        def collect_answer() -> str:
            """Collect locally streamed tokens before publishing clean speech."""
            parts: list[str] = []
            response_stream = self.client.chat(
                model=active_model,
                messages=messages,
                stream=True,
                options={
                    "temperature": active_temperature,
                    "num_predict": num_predict,
                },
                keep_alive=active_keep_alive,
                think=False,
            )
            for chunk in response_stream:
                if turn_cancel.is_set():
                    break
                message = chunk.get("message")
                if message:
                    parts.append(str(message.get("content", "")))
            return "".join(parts)

        generation_started = time.perf_counter()
        try:
            if effective_forced_response:
                # Verification failures are enforced here instead of asking
                # the vision model to voluntarily avoid a confident guess.
                raw_reply = effective_forced_response
            else:
                raw_reply = collect_answer()

            # Some Ollama/Qwen3-VL combinations return an empty streamed
            # content field. Retry once with Ollama's documented non-streaming
            # vision request instead of running a long reasoning stream.
            if (
                uses_vision_model
                and not effective_forced_response
                and not str(raw_reply).strip()
            ):
                print(
                    "\n[Vision] The streamed response was empty; retrying "
                    "with the direct vision request..."
                )
                direct_response = self.client.chat(
                    model=active_model,
                    messages=messages,
                    stream=False,
                    options={
                        "temperature": active_temperature,
                        "num_predict": num_predict,
                    },
                    keep_alive=active_keep_alive,
                    think=False,
                )
                direct_message = self._value(
                    direct_response,
                    "message",
                    {},
                )
                raw_reply = self._value(
                    direct_message,
                    "content",
                    "",
                )

            reply = TextFilter.for_voice_response(
                raw_reply,
                max_words=max_words,
                max_sentences=max_sentences,
            )
            if (
                not effective_forced_response
                and AnswerCompletionGuard.needs_retry(
                    reply,
                    calculation=calculation_response,
                )
            ):
                print(
                    "\n[Response Guard] Calculation did not provide the "
                    "requested result; regenerating once."
                )
                completion_messages = [
                    *messages,
                    {"role": "assistant", "content": str(raw_reply)},
                    {
                        "role": "user",
                        "content": (
                            "That draft deferred or stopped before giving the "
                            "numerical answer. Perform the calculation now. "
                            "State every requested final amount first, then "
                            "give only the brief explanation needed. Do not "
                            "ask permission or promise to calculate later."
                        ),
                    },
                ]
                completion_response = self.client.chat(
                    model=active_model,
                    messages=completion_messages,
                    stream=False,
                    options={
                        "temperature": 0.1,
                        "num_predict": num_predict,
                    },
                    keep_alive=active_keep_alive,
                    think=False,
                )
                completion_message = self._value(
                    completion_response,
                    "message",
                    {},
                )
                completion_raw = self._value(
                    completion_message,
                    "content",
                    "",
                )
                completion_reply = TextFilter.for_voice_response(
                    completion_raw,
                )
                if (
                    completion_reply
                    and not AnswerCompletionGuard.needs_retry(
                        completion_reply,
                        calculation=calculation_response,
                    )
                ):
                    raw_reply = completion_raw
                    reply = completion_reply
            if (
                route.intent in {
                    "conversation",
                    "calculation",
                    "agent_offer",
                }
                and not effective_forced_response
                and ResponseQualityGuard.should_retry(
                    reply,
                    user_input,
                    self.conversation.get_history(),
                )
            ):
                print(
                    "\n[Response Guard] Repeated an unrelated prior answer; "
                    "regenerating once."
                )
                retry_messages = [
                    *messages,
                    {"role": "assistant", "content": str(raw_reply)},
                    {
                        "role": "user",
                        "content": (
                            "That draft repeated an older answer and did not "
                            "respond to my current message. Answer this current "
                            f"message directly instead: {user_input}"
                        ),
                    },
                ]
                retry_response = self.client.chat(
                    model=active_model,
                    messages=retry_messages,
                    stream=False,
                    options={
                        "temperature": active_temperature,
                        "num_predict": num_predict,
                    },
                    keep_alive=active_keep_alive,
                    think=False,
                )
                retry_message = self._value(
                    retry_response,
                    "message",
                    {},
                )
                retry_raw = self._value(
                    retry_message,
                    "content",
                    "",
                )
                retry_reply = TextFilter.for_voice_response(
                    retry_raw,
                    max_words=max_words,
                    max_sentences=max_sentences,
                )
                if retry_reply:
                    raw_reply = retry_raw
                    reply = retry_reply

            # Length limits guide both the first generation and this optional
            # rewrite. The sanitizer never slices the final answer. If the
            # model cannot produce a valid shorter version, preserve the
            # complete draft rather than cutting off its result.
            advice_needs_rewrite = AdviceResponseGuard.needs_rewrite(
                reply,
                recommendation=recommendation_response,
                urgent_safety=route.urgent_safety,
                advice_domain=route.advice_domain,
            )
            if (
                not effective_forced_response
                and (
                    response_limits.exceeds(reply)
                    or advice_needs_rewrite
                )
            ):
                print(
                    "\n[Response Rewrite] Rewriting the complete answer to "
                    "the voice advice and length requirements."
                )
                preservation_rule = (
                    " Preserve the direct recommendation, immediate action, "
                    "and essential caution; remove background first. Keep at "
                    "most one question only when a missing safety detail "
                    "changes the recommendation."
                    if recommendation_response
                    else " Preserve every requested result."
                )
                advice_footer_rule = (
                    " Do not add a generic offer or routine referral."
                    if recommendation_response and not route.urgent_safety
                    else (
                        " Preserve the urgent action without softening or delay."
                        if route.urgent_safety
                        else " Do not add a follow-up question."
                    )
                )
                rewrite_messages = build_personality_messages(
                    system_prompt=self.system_prompt,
                    history=[],
                    user_input=(
                        route.normalized_request or user_input
                    ),
                    context_sections=(
                        ("DRAFT ANSWER", reply),
                        (
                            "VOICE RESPONSE REQUIREMENTS",
                            response_instruction
                            + " Rewrite the draft."
                            + preservation_rule
                            + advice_footer_rule,
                        ),
                    ),
                )
                rewrite_response = self.client.chat(
                    model=active_model,
                    messages=rewrite_messages,
                    stream=False,
                    options={
                        "temperature": 0.1,
                        "num_predict": num_predict,
                    },
                    keep_alive=active_keep_alive,
                    think=False,
                )
                rewrite_message = self._value(
                    rewrite_response,
                    "message",
                    {},
                )
                rewrite_reply = TextFilter.for_voice_response(
                    self._value(rewrite_message, "content", ""),
                )
                rewrite_complete = not AnswerCompletionGuard.needs_retry(
                    rewrite_reply,
                    calculation=calculation_response,
                )
                rewrite_advice_valid = not AdviceResponseGuard.needs_rewrite(
                    rewrite_reply,
                    recommendation=recommendation_response,
                    urgent_safety=route.urgent_safety,
                    advice_domain=route.advice_domain,
                )
                if (
                    rewrite_reply
                    and rewrite_complete
                    and rewrite_advice_valid
                    and not response_limits.exceeds(rewrite_reply)
                ):
                    reply = rewrite_reply
                else:
                    if recommendation_response and not route.urgent_safety:
                        finalizer_response = self.client.chat(
                            model=active_model,
                            messages=[
                                {
                                    "role": "system",
                                    "content": self.system_prompt,
                                },
                                {
                                    "role": "user",
                                    "content": (
                                        "Return only a short final voice reply. "
                                        "Give the direct recommendation first, "
                                        "then the immediate action and at most "
                                        "one essential caution. This is routine "
                                        "advice: do not mention a doctor, expert, "
                                        "or professional. For health advice, do "
                                        "not invent a numeric dose; use label "
                                        "directions. Ask for one missing "
                                        "safety detail instead when necessary.\n\n"
                                        "CURRENT USER MESSAGE\n"
                                        f"{route.normalized_request or user_input}\n\n"
                                        f"DRAFT ANSWER\n{reply}\n\n"
                                        "VOICE LIMITS\n"
                                        f"{response_instruction}"
                                    ),
                                },
                            ],
                            stream=False,
                            options={
                                "temperature": 0,
                                "num_predict": num_predict,
                            },
                            keep_alive=active_keep_alive,
                            think=False,
                        )
                        finalizer_message = self._value(
                            finalizer_response,
                            "message",
                            {},
                        )
                        finalizer_reply = TextFilter.for_voice_response(
                            self._value(finalizer_message, "content", ""),
                        )
                        finalizer_valid = (
                            bool(finalizer_reply)
                            and not response_limits.exceeds(finalizer_reply)
                            and not AdviceResponseGuard.needs_rewrite(
                                finalizer_reply,
                                recommendation=True,
                                urgent_safety=False,
                                advice_domain=route.advice_domain,
                            )
                        )
                        if finalizer_valid:
                            reply = finalizer_reply
                    print(
                        "\n[Response Rewrite] The first rewrite was not "
                        "complete; applied the advice fallback when valid."
                    )
            if recommendation_response:
                reply = response_limits.merge_extra_sentences(reply)
            speech_buffer = reply
            if reply:
                print(
                    reply,
                    end="",
                    flush=True,
                )
                self.events.emit(
                    "assistant_stream",
                    text=reply,
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
            current_task = self.agent_tasks.get(agent_task_id)
            if (
                current_task is not None
                and current_task.status not in {
                    "waiting_approval",
                    "completed",
                    "failed",
                    "cancelled",
                }
            ):
                self.agent_tasks.update(
                    agent_task_id,
                    "cancelled",
                    "The user interrupted the active response.",
                )
            with self._turn_lock:
                if self._active_turn_cancel is turn_cancel:
                    self._active_turn_cancel = None
            return reply

        # Never silently return to microphone listening after a failed request.
        if not reply.strip():
            if tool_result_fallback:
                reply = TextFilter.for_voice_response(
                    tool_result_fallback,
                    max_words=max_words,
                    max_sentences=max_sentences,
                )
            elif uses_vision_model:
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
            and route.memory_candidate
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

        current_task = self.agent_tasks.get(agent_task_id)
        if (
            current_task is not None
            and current_task.status == "working"
        ):
            self.agent_tasks.update(
                agent_task_id,
                "completed",
                "Agent returned its response.",
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

    def _start_project_mcp(self) -> None:
        """Start project access without preventing Elaina from launching."""
        enabled = self.config.get(
            "project_access",
            "enabled",
            default=False,
            required=False,
        )
        if not enabled:
            return

        configured_root = self.config.get(
            "project_access",
            "project_root",
            default="",
            required=False,
        )
        if not str(configured_root).strip():
            print(
                "[Project MCP] Disabled because project_root is empty in "
                "config.yaml."
            )
            return

        try:
            project_root = self.config.resolve_path(
                "project_access",
                "project_root",
                must_exist=True,
            )
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
        edit_requested: bool,
    ) -> str:
        """
        Let Qwen gather read-only project evidence before writing its answer.

        A local finish tool gives the planner a clean way to say it has enough
        information. The final answer is generated by the normal streaming path,
        so TTS and Electron events continue to work exactly as before.
        """
        if self.project_mcp is None:
            return ""

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
                    "proposing an HTML change. All tool paths and list_files "
                    "directories must be relative to the configured project "
                    "root; use '.' for the root and never send an absolute "
                    "Windows path. If JavaScript behavior or styling "
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
            if self._turn_is_cancelled():
                print("[Project MCP] Research cancelled.")
                break
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
                if self._turn_is_cancelled():
                    should_finish = True
                    break
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
            and not self._turn_is_cancelled()
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
                    if self._turn_is_cancelled():
                        print("[Project MCP] Proposal generation cancelled.")
                        break
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

    def resolve_agent_action(
        self,
        proposal_id: str,
        approved: bool,
    ) -> dict:
        """Resolve an agent-install or calendar-write proposal."""
        try:
            proposal = self.approvals.resolve(
                proposal_id,
                approved,
            )
        except Exception as error:
            result = {
                "status": "error",
                "message": f"{type(error).__name__}: {error}",
            }
            self.events.emit("action_approval_error", **result)
            return result

        task_id = str(proposal.payload.get("task_id", ""))
        if not approved:
            result = {
                "status": "rejected",
                "proposal_id": proposal.proposal_id,
                "action": proposal.action,
                "message": "The action was rejected. Nothing was changed.",
            }
            if self.agent_tasks.get(task_id) is not None:
                self.agent_tasks.update(
                    task_id,
                    "cancelled",
                    "The user rejected the action.",
                )
            self._pending_action = ""
            self.events.emit("action_approval_rejected", **result)
            return result

        try:
            if proposal.action == "agent.install":
                installed = self.agent_registry.install_user_agent(
                    proposal.payload["definition"]
                )
                credentials_ready, credential_message = (
                    self.calendar_tool.readiness()
                )
                result = {
                    "status": "completed",
                    "proposal_id": proposal.proposal_id,
                    "action": proposal.action,
                    "message": (
                        f"{installed.name} was installed. "
                        + (
                            "It is ready to prepare calendar events."
                            if credentials_ready
                            else (
                                "Before its first event can be created, "
                                + credential_message
                            )
                        )
                    ),
                    "agent_id": installed.id,
                }
            elif proposal.action == "calendar.create_event":
                created = self.calendar_tool.create_event(
                    calendar_id=str(
                        proposal.payload["calendar_id"]
                    ),
                    event=dict(proposal.payload["event"]),
                )
                result = {
                    "status": "completed",
                    "proposal_id": proposal.proposal_id,
                    "action": proposal.action,
                    "message": (
                        f"Created the calendar event "
                        f"'{created['summary']}'."
                    ),
                    **created,
                }
            else:
                raise PermissionError(
                    f"Unsupported approved action: {proposal.action}"
                )
        except Exception as error:
            result = {
                "status": "error",
                "proposal_id": proposal.proposal_id,
                "action": proposal.action,
                "message": f"{type(error).__name__}: {error}",
            }
            if self.agent_tasks.get(task_id) is not None:
                self.agent_tasks.update(
                    task_id,
                    "failed",
                    result["message"],
                )
            self._pending_action = ""
            self.events.emit("action_approval_error", **result)
            return result

        if self.agent_tasks.get(task_id) is not None:
            self.agent_tasks.update(
                task_id,
                "completed",
                result["message"],
            )
        self._pending_action = ""
        self.events.emit("action_approval_completed", **result)
        self.audio.speak(result["message"])
        return result

    def close(self) -> None:
        """Stop background services and active speech."""
        self.cancel_active_turn()
        self.screen_monitor.stop()
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
