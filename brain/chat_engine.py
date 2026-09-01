from typing import Any
import json
import re
import threading
import time
import ollama
from collections import deque
from dataclasses import dataclass, field, replace

from memory.memory_manager import MemoryManager
from memory import memory_manager as memory_categories
from memory.extractor import MemoryExtractor
from memory.consolidator import MemoryConsolidator
from memory.context_builder import ContextBuilder
from brain.prompt_builder import PromptBuilder
from brain.deliberation import ClarificationGate, Goal
from brain.deliberation import goal_intent, interaction
from brain.deliberation.goal_intent import SemanticGoal
from brain import capability_selection
from brain import browser_outcome
from brain.capability_selection import CapabilityChoice
from brain.deliberation.interaction import InteractionDecision
from brain.deliberation import front_door
from brain.deliberation.pending import (
    asks_something_else,
    reads_as_new_request,
)
from brain.deliberation.profile import UserProfile
from brain.deliberation import profile as profile_module
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
    ClosingOfferGuard,
    ResponseLimits,
)
from brain.calculation_planner import CalculationPlanner
from brain.desktop_action_planner import (
    DesktopActionPlanner,
    DesktopSurfaceContext,
)
from brain.browser_action_planner import (
    BrowserActionPlanner,
    wants_information,
)
from brain.task_planner import TaskPlanner, TaskState
from brain.task_intent_gate import TaskIntentGate
from brain.task_extractor import TaskExtractor
from brain.task_discovery_policy import TaskDiscoveryPolicy
from brain.task_session import DEICTIC_REFERENCE, TaskSessionStore
from brain.recommendation_state import RecommendationProblem
from brain import recommendation_state
from brain import acquisition
from brain import conversation_focus
from brain import preferences
from brain import candidate_fit
from brain import semantic_fit
from brain.media_target import classify_media_request
from brain.user_locale import UserLocale
from brain.capabilities import CapabilityRegistry
from brain.action_commitment import ActionCommitmentGuard
from brain.recommendation import (
    RecommendationPolicy,
    reads_as_clear_acceptance,
    subject_is_offerable,
    subject_phrase,
)
from brain.action_status import (
    ActionStatusSelector,
    StatusContext,
    action_for_intent,
    is_continuation,
)
from brain.social_lines import SocialLineSelector
from brain.answer_condenser import AnswerCondenser
from brain.grounded_values import GroundedValueGuard
from brain import grounded_values
from brain.grounded_values import _SENTENCE_SPLIT
from brain.web_search_planner import WebSearchActionPlanner
from brain.decision_log import log_information_need
from tools.browser_control.browser_connection import BrowserConnection
from tools.browser_control.browser_observer import spoken_label
from tools.browser_control.browser_service import BrowserService
from tools.screen_browser.screen_browser_service import ScreenBrowserService
from tools.screen_control.cursor_driver import CursorDriver
from tools.screen_control.input_watcher import InputWatcher
from tools.screen_control.screen_ui_control import ScreenUIControl
from brain.context_policy import should_include_grounded_context
from brain.brief_response import BriefResponseGenerator
from datetime import datetime
from vision.screen_monitor import ScreenMonitor
from brain.intent_router import IntentDecision, SemanticIntentRouter
from agents.builder import AgentBuilder
from agents.calendar_agent import GoogleCalendarAgent
from agents.coordinator import AgentCoordinator
from agents.research_agent import ResearchAgent, ResearchResult
from agents.consent import (
    AgentConsentGate,
    SemanticConsentDecision,
    SemanticConsentClassifier,
    apply_agent_permission,
)
from agents.registry import AgentRegistry
from agents.task_manager import AgentTaskManager
from security.approval_manager import ApprovalManager
from security.policy import PolicyEngine
from security.computer_consent import ComputerConsentGate
from security.computer_control_mode import ComputerControlMode
from security.task_consent import PendingTaskAction, TaskConsentGate
from security.task_strategy_consent import TaskStrategyConsentGate
from security.capability_offer import CapabilityOfferGate
from tools.google_calendar import GoogleCalendarTool
from tools.computer_control.computer_control import (
    ComputerActionRequest,
    ComputerActionResult,
    ComputerControl,
    PreparedComputerAction,
)
from tools.computer_control.session_action_memory import SessionActionMemory
from tools.computer_control.windows_ui_control import WindowsUIControl
from tools.computer_control.session_item_memory import SessionItemMemory
from agents.preconditions import check_precondition


def _sentence_case(text: str) -> str:
    """Capitalise the first letter only.

    ``str.capitalize()`` lowercases everything after it, which turned the
    UI's own "Computer Control toggle" into "computer control toggle" --
    and the user has to find that exact control on screen.
    """
    text = str(text).strip()
    return text[:1].upper() + text[1:] if text else text


def _drop_unfinished_sentence(text: str) -> str:
    """Cut back to the last finished sentence after a budget cut-off.

    Only ever called when Ollama reported ``done_reason == "length"`` --
    generation stopped because it ran out of tokens, not because the answer
    was over. Measured live, a reply went out as:

        "Seattle's a cool place to live -- just don't forget the rain. Need"

    The dangling fragment is spoken aloud by TTS, so it is worse in voice
    than on screen. A truncated *first* sentence is kept rather than
    returning nothing: half an answer still beats silence.
    """
    stripped = str(text or "").rstrip()
    if not stripped or stripped[-1] in ".!?\"')]}…":
        return stripped
    finished = re.search(
        r"^.*[.!?][\"')\]]*(?=\s)", stripped, flags=re.DOTALL,
    )
    return finished.group(0).rstrip() if finished else stripped


# A request that names what it is about ("open youtube.com", "check the
# Peninsula Hong Kong") is self-contained and must never be redirected by
# an unrelated earlier topic, so it is excluded first.
_NAMES_ITS_OWN_SUBJECT = re.compile(
    r"\b[\w-]+\.(?:com|net|org|io|kr|co\.kr|jp)\b"
    r"|\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b"
    r"|\"[^\"]{3,}\"|'[^']{3,}'",
)
# What's left is a request with no subject of its own -- either pointing
# back at something ("open the first one", "is it available") or asking
# about a bare attribute ("check the price on the browser"). Only these
# borrow the previous turn's subject.
_DEICTIC_REQUEST = re.compile(
    r"\b(?:it|its|that|those|these|them|there|the\s+"
    r"(?:first|second|third|last|best|cheapest|other)\s+one)\b"
    r"|그거|저거|첫\s*번째",
    flags=re.IGNORECASE,
)
_BARE_ATTRIBUTE_REQUEST = re.compile(
    r"\b(?:price|prices|cost|rate|rates|fee|availability|available|stock|"
    r"rating|ratings|review|reviews|hours|address|number|menu)\b"
    r"|가격|요금|평점|영업시간",
    flags=re.IGNORECASE,
)

# "What can you do?" -- a question about the whole inventory rather than
# one ability, answered from the registry instead of from the model's
# generic idea of what an assistant is.
_ABILITY_INVENTORY_QUESTION = re.compile(
    r"\bwhat\s+(?:can|could|are)\s+you\s+(?:do|able\s+to\s+do)\b"
    r"|\byour\s+(?:abilities|capabilities|features)\b"
    r"|\bwhat\s+are\s+you\s+capable\s+of\b"
    r"|뭐\s*(?:를)?\s*할\s*수\s*있|무엇을\s*할\s*수\s*있|기능이\s*뭐",
    flags=re.IGNORECASE,
)

# A short thank-you closes an unfinished offer/task context.  Without this,
# normal conversational history can cause the next model reply to resume a
# hotel search the person has plainly finished discussing.
_CLOSING_ACKNOWLEDGEMENT = re.compile(
    r"^\s*(?:ok(?:ay)?\s*,?\s*)?(?:thanks?|thank you|thx|고마워(?:요)?|감사(?:합니다|해요)?)\s*[.!?]*\s*$",
    flags=re.IGNORECASE,
)

# A bare greeting needs no model, locale, capability inventory, or service
# pitch. Full-match-only means "hello, can you check Zillow?" still reaches
# the request behind the greeting.
_SIMPLE_GREETING = re.compile(
    r"^\s*(?:hi|hey|hello|hiya|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+elaina)?\s*[!.?]*\s*$",
    flags=re.IGNORECASE,
)

# An imperative or explicit request, as opposed to a remark that merely
# mentions a browser or an app ("I like using Chrome"). Paired with
# CapabilityRegistry.match() so a conversational turn is only ever
# escalated into a real action when the user actually asked for one.
_ACTION_REQUEST_SHAPE = re.compile(
    r"^\s*(?:please\s+|now\s+|just\s+|then\s+|and\s+)*"
    r"(?:open|check|search|look|find|go|click|fill|type|browse|compare|"
    r"verify|confirm|show|use|visit|pull|bring|navigate|read)\b"
    r"|\b(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"(?:open|check|search|look|find|go|click|browse|compare|verify|"
    r"confirm|show|use|visit|pull|bring|navigate|read)\b"
    r"|\bplease\s+(?:open|check|search|look|find|go|click|browse)\b"
    r"|해\s*줘|확인해|열어\s*줘|찾아\s*줘",
    flags=re.IGNORECASE,
)

_BROWSER_SURFACE_HINTS = (
    "google chrome",
    "microsoft edge",
    "mozilla firefox",
    "brave browser",
    "opera",
    "vivaldi",
    # Naver Whale appends "- Whale" to its window titles the same way
    "whale",
    "웨일",
)
from tools.browser_control.safe_browser import SafeBrowserControl
from tools.computer_control.safe_filesystem import SafeFilesystemControl
from tools.computer_control.windows_app_catalog import WindowsAppCatalog

@dataclass
class TurnRouting:
    """Everything the routing phase decided, and nothing else.

    A small, explicit contract in place of ten locals shared down a
    two-thousand-line method: what the request is, what may already have
    been answered for it, and what a later phase is allowed to assume.
    """

    route: IntentDecision
    user_input: str
    locked_response: str = ""
    clarified_goal: Goal | None = None
    assumed_aloud: str = ""
    approved_computer_action: PreparedComputerAction | None = None
    approved_task_action: PendingTaskAction | None = None
    approved_strategy_task_state: TaskState | None = None
    declined_strategy_task_state: TaskState | None = None
    agent_permission_context: str = ""
    # What should happen about this request, decided once. Consumers ask this
    # instead of re-deriving it from route.intent; see
    # brain/deliberation/interaction.py for why that mattered.
    decision: InteractionDecision = field(default_factory=InteractionDecision)
    # What the person wanted, said without naming a tool, and the ability
    # chosen to meet it. Together with `decision` these are the whole
    # chain: goal -> need -> capability -> agent.
    goal_intent: SemanticGoal = field(default_factory=SemanticGoal)
    capability: CapabilityChoice = field(default_factory=CapabilityChoice)
    # What she already had that answers this, when recall found some.
    recalled_evidence: str = ""
    # The recommendation the conversation is working on, when it is working
    # on one. Carried here so the acting phase can build a query from
    # everything established rather than from this turn's words alone --
    # "pull up some spots" says nothing about the sore throat that decided
    # what to look for.
    problem: RecommendationProblem | None = None


class ChatEngine:

    def __init__(self, config: Config | None = None):
        # The one seam a test needs: a turn suite builds the real engine
        # with heavy, side-effectful features switched off in a copy of the
        # configuration, rather than reconstructing half of it by hand.
        self.config = config if config is not None else Config()

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
            medium_confidence_threshold=float(self.config.get(
                "routing",
                "confidence_medium_threshold",
                default=0.5,
                required=False,
            )),
            clarification_enabled=bool(self.config.get(
                "routing",
                "confidence_clarification_enabled",
                default=True,
                required=False,
            )),
            print_confidence_log=bool(self.config.get(
                "debug",
                "print_router_confidence",
                default=True,
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
        # Read once here so CapabilityRegistry can report web search's real
        # availability without re-reading config on every turn.
        self._web_search_enabled = bool(self.config.get(
            "search",
            "enabled",
            default=True,
            required=False,
        ))
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

        # Memory is optional. Besides being a user-facing privacy and
        # resource setting, honouring this flag lets diagnostics and whole-
        # turn tests start the real orchestration without loading the
        # sentence-transformer model or opening the memory database.
        self.memory_enabled = bool(self.config.get(
            "memory", "enabled", default=True, required=False,
        ))
        self.memory_manager = MemoryManager() if self.memory_enabled else None
        self.extractor = (
            MemoryExtractor(config=self.config) if self.memory_enabled else None
        )
        self.consolidator = (
            MemoryConsolidator(config=self.config)
            if self.memory_enabled else None
        )
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
        self.browser_page_control_enabled = bool(self.config.get(
            "browser_control",
            "enabled",
            default=True,
            required=False,
        ))
        # Phase 4E driver selection. "screen" operates the browser already
        # open through UI Automation and the real cursor; "cdp" is the
        # Phase 4C isolated-profile DevTools driver. Anything unrecognised
        # falls back to the older, more conservative driver rather than
        # silently taking control of the user's own browser window.
        configured_driver = str(self.config.get(
            "browser_control", "driver", default="screen", required=False,
        )).strip().lower()
        self.browser_driver = configured_driver if configured_driver in {
            "screen", "cdp",
        } else "cdp"
        # Phase 4F: the same choice for everything that is not a browser.
        # "screen" moves the real pointer and types real keystrokes; "uia"
        # is the Phase 4B driver that calls Invoke() on a control and cannot
        # type into apps that expose no named field. An unrecognised value
        # falls back to the older, less capable driver rather than silently
        # taking the mouse.
        configured_desktop_driver = str(self.config.get(
            "computer_control", "driver", default="screen", required=False,
        )).strip().lower()
        self.desktop_driver = configured_desktop_driver if (
            configured_desktop_driver in {"screen", "uia"}
        ) else "uia"
        # URL policy is shared by both drivers: the destination rules must
        # not depend on which one is steering.
        self.allow_local_browser_urls = bool(self.config.get(
            "computer_control", "allow_local_urls",
            default=False, required=False,
        ))
        self.default_search_url = str(self.config.get(
            "computer_control", "default_search_url",
            default="https://www.google.com/search?q={query}",
            required=False,
        ))
        browser_profile_directory = str(self.config.get(
            "browser_control",
            "user_data_dir",
            default="",
            required=False,
        )).strip()
        # Browser navigation and browser-page control must share the same
        # session.  The former implementation opened Windows' default browser
        # while the latter attached only to configured Whale, making every
        # follow-up click inherently unreliable.
        browser_catalog = WindowsAppCatalog(user_aliases=configured_aliases)
        self.browser_connection = BrowserConnection(
            browser_name=str(self.config.get(
                "browser_control", "browser_name",
                default="Whale", required=False,
            )),
            debugging_port=int(self.config.get(
                "browser_control", "remote_debugging_port",
                default=9222, required=False,
            )),
            user_data_dir=browser_profile_directory or None,
            catalog=browser_catalog,
            force_accessibility=bool(self.config.get(
                "browser_control", "force_accessibility",
                default=True, required=False,
            )),
        )
        self.computer_control = ComputerControl(
            self.policy,
            enabled=bool(self.config.get(
                "computer_control",
                "enabled",
                default=False,
                required=False,
            )),
            catalog=browser_catalog,
            browser=SafeBrowserControl(
                opener=(
                    # The service is created immediately after
                    # ComputerControl below.  Keep this indirection so every
                    # generic "open website" action shares the actor-owned
                    # CDP session with later DOM observation and clicks.
                    lambda url: self.browser_service.open_url(url)
                    if self.browser_page_control_enabled
                    else None
                ),
                allow_local_urls=self.allow_local_browser_urls,
                search_url_template=self.default_search_url,
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
        # Local, session-only record of items Elaina herself just created --
        # lets a referential delete ("delete the folder we just made")
        # resolve without the model ever inventing a target.
        self._session_items = SessionItemMemory()
        # Local, session-only record of verified desktop actions, so a later
        # "stop it" resolves against what Elaina actually did rather than
        # against the model's recollection of the conversation.
        self._session_actions = SessionActionMemory()
        # One watcher for the whole process separates the user's real input
        # from Elaina's injected input. Explicit desktop requests start
        # immediately; this watcher is retained solely as an emergency stop
        # when the user physically reclaims the mouse or keyboard mid-run.
        # One unanswered question at a time. Unlike the consent gates, this
        # is not asking permission -- it is asking for a value the request
        # never named, so answering it continues that request rather than
        # approving anything.
        self.clarification = ClarificationGate()
        # What she has learned about this person from what they asked for
        # and what actually happened. Local to this machine.
        self.user_profile = UserProfile()
        self.input_watcher = InputWatcher()
        watching = self.input_watcher.start()
        self.cursor_driver = CursorDriver(input_watcher=self.input_watcher)
        print(
            f"[Desktop] driver={self.desktop_driver} "
            f"input_watch={'on' if watching else 'off'}"
        )
        if self.desktop_driver == "screen":
            desktop_control = ScreenUIControl(
                observer=self.computer_control.ui_observer,
                cursor=self.cursor_driver,
            )
        else:
            desktop_control = WindowsUIControl(
                observer=self.computer_control.ui_observer,
            )
        # Shares computer_control's own live UI observer rather than
        # standing up a second one, so both see the same real window state.
        self.desktop_action_planner = DesktopActionPlanner(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
            observer=self.computer_control.ui_observer,
            control=desktop_control,
            computer_control=self.computer_control,
            response_language=self.response_language,
            session_actions=self._session_actions,
            profile=self.user_profile,
        )
        # Shares computer_control's own live UI observer (Phase 4B) so
        # active-tab detection can cross-check the real OS window title --
        # see BrowserObserver._active_tab_index for why that beats trusting
        # any in-page signal once a CDP client is attached.
        # Playwright's synchronous CDP handle is thread-affine, whereas each
        # Elaina response runs on a fresh worker thread.  One service actor
        # owns the live connection for the whole chat lifetime, so opening,
        # observing, and acting keep the exact same controlled page identity
        # rather than disconnecting/reconnecting between turns.
        # Phase 4E: the screen driver operates the browser window the user
        # already has open -- reading its live page through UI Automation and
        # moving the real pointer -- instead of launching an isolated,
        # logged-out profile and speaking CDP to it. Both drivers present the
        # same observer/control surface, so everything downstream (the action
        # planner, the task planner, the confirmation flow) is unchanged.
        if self.browser_driver == "screen":
            def launch_default_browser() -> None:
                resolution = browser_catalog.resolve("Default Browser")
                if resolution.status != "resolved" or resolution.entry is None:
                    raise OSError("No default browser is registered.")
                browser_catalog.launch(resolution.entry)

            self.browser_service = ScreenBrowserService(
                safe_browser=SafeBrowserControl(
                    opener=lambda url: None,
                    allow_local_urls=self.allow_local_browser_urls,
                    search_url_template=self.default_search_url,
                ),
                # Desktop and browser actions are one physical-control
                # session. Sharing this driver makes the same immediate
                # takeover and emergency-stop boundary govern both.
                cursor=self.cursor_driver,
                window_launcher=launch_default_browser,
            )
        else:
            self.browser_service = BrowserService(
                connection=self.browser_connection,
                ui_observer=self.computer_control.ui_observer,
            )
        print(f"[Browser] driver={self.browser_driver}")
        self.browser_observer = self.browser_service.observer
        self.browser_control = self.browser_service.control
        self.browser_action_planner = BrowserActionPlanner(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
            observer=self.browser_observer,
            control=self.browser_control,
        )
        # Desktop Control is deliberately session-only and starts off after
        # every launch. The config flag remains the master kill switch.
        self.computer_control_mode = ComputerControlMode(enabled=False)
        # Phase 4D-1: goal-level planner composing the desktop/browser
        # planners above into multi-step tasks. Never touches their
        # internals -- only calls their existing .act()/resume_confirmed_*
        # entry points, the same way chat_engine itself already does for a
        # single-ability turn.
        # Phase 4D-3: opportunistically parses a step's prose result into
        # named, verbatim-attributed items so a later step or the final
        # answer can compare/filter against them instead of re-reading
        # prose. Opt-in on TaskPlanner's side, but always on in production.
        self.task_extractor = TaskExtractor(
            client=self.client, model=self.model, keep_alive=self.keep_alive,
        )
        # Constructed here (ahead of their previous position below) so
        # TaskPlanner can be handed a web_search capability alongside its
        # existing desktop/browser ones -- same ResearchAgent instance the
        # plain web_search intent path already uses, not a second one.
        self.web_search_tool = WebSearchTool()
        # Which market the user actually buys in. Resolved once, then used
        # by every recommendation path (router prompt, task planner, web
        # search) so a Korean user is not quietly sent to US-only sites.
        self.user_locale = UserLocale.from_config(self.config)
        print(
            "[Locale] user="
            f"{self.user_locale.context.home} language={self.user_locale.language} "
            f"currency={self.user_locale.context.currency}"
        )
        self.research_agent = ResearchAgent(
            self.search_web,
            search_structured=self.web_search_tool.search_web_structured,
            locale=self.user_locale,
        )
        self.web_search_action_planner = WebSearchActionPlanner(
            research_agent=self.research_agent,
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
        )
        self.task_discovery_policy = TaskDiscoveryPolicy()
        self.task_sessions = TaskSessionStore()
        self.task_planner = TaskPlanner(
            client=self.client,
            model=self.model,
            keep_alive=self.keep_alive,
            agent_registry=self.agent_registry,
            desktop_action_planner=self.desktop_action_planner,
            browser_action_planner=self.browser_action_planner,
            web_search_action_planner=self.web_search_action_planner,
            computer_control_mode=self.computer_control_mode,
            browser_control_enabled=self.browser_page_control_enabled,
            task_extractor=self.task_extractor,
            discovery_policy=self.task_discovery_policy,
            user_locale=self.user_locale,
        )
        self.task_intent_gate = TaskIntentGate(
            client=self.client, model=self.model, keep_alive=self.keep_alive,
        )
        self.task_consent = TaskConsentGate(
            expiry_seconds=int(self.config.get(
                "routing",
                "task_confirmation_expiry_seconds",
                default=90,
                required=False,
            ))
        )
        self.task_strategy_consent = TaskStrategyConsentGate(
            expiry_seconds=int(self.config.get(
                "routing",
                "task_strategy_offer_expiry_seconds",
                default=90,
                required=False,
            ))
        )
        self.capability_offer = CapabilityOfferGate(
            expiry_seconds=int(self.config.get(
                "routing",
                "capability_offer_expiry_seconds",
                default=120,
                required=False,
            ))
        )
        self.brief_responses = BriefResponseGenerator(
            self.client,
            self.model,
            keep_alive=self.keep_alive,
        )
        # Status lines cover slow work, so they cannot afford to wait on the
        # model themselves. One selector for the whole session: repetition is
        # only visible across turns, so its memory has to outlive them.
        self.action_status = ActionStatusSelector(
            language=self.response_language,
        )
        # Greetings, for the same reason and with the same lifetime: a
        # greeting must not wait on the model, and "the same words every
        # time" is only visible across turns.
        self.social_lines = SocialLineSelector(
            language=self.response_language,
        )
        # Whether to offer something nobody asked for, and how often not to.
        # 4E.2 worked out that an action would help and was not requested;
        # this is what finally reads that.
        self.recommendations = RecommendationPolicy(
            language=self.response_language,
        )
        # How often each ability has failed this session. Tool selection
        # scores a repeatedly failing capability down, so the next-best is
        # chosen instead of the same one forever. TaskPlanner bounds retries
        # *inside* one task; this is the across-turns case it cannot see.
        self._capability_failures: dict[str, int] = {}
        # Left behind by a live check that ran and reached nothing,
        # and consumed by the fallback search below.
        self._live_check_note = ""
        # What the most recent search actually returned, so a named
        # shop in the reply can be checked against it.
        self._last_research_evidence = ""
        # Set when this turn opened a different recommendation, so the
        # previous one's turns do not stay in the answering prompt.
        self._recommendation_restarted = False
        # Named by the person for this turn only. Never written to the
        # profile: "use Google Maps for this one" must not erase a
        # standing preference for Naver Maps.
        self._source_override = ""
        self._tool_override = ""
        self.answer_condenser = AnswerCondenser(
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
                "opera", "vivaldi", "whale",
            }
            else "native"
        )
        state = {
            "title": title,
            "application": application,
            "kind": kind,
            "identity": str(getattr(window, "identity", "") or ""),
            "handle": getattr(window, "handle", None),
            "process_id": getattr(window, "process_id", None),
        }
        # Recording every real external surface here (not only the ones
        # Elaina opened herself) is what gives the Electron-overlay branch
        # above something to fall back to.
        self._remember_desktop_surface(state)
        return state

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
            # was enabled. This includes a native/browser step paused inside
            # a multi-step task, not only the direct computer-action gate.
            # High-risk operations can be requested again after control is
            # explicitly turned back on.
            self.computer_consent.clear()
            task_consent = getattr(self, "task_consent", None)
            if task_consent is not None:
                task_consent.clear()
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
        state = {
            "active_topic": self._active_topic,
            "active_entity": self._active_entity,
            "entity_aliases": self._entity_aliases,
            "grounded_context": dict(self._grounded_context),
            "computer_control_enabled": self.computer_control_mode.enabled,
            "active_desktop_surface": self._desktop_surface_for_turn(),
            "recently_created_items": self._session_items.recent_context(),
            "recent_desktop_actions": self._session_actions.recent_context(),
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
        state.update(self.task_sessions.public_conversation_state())
        return state

    def _capability_state(self) -> dict[str, bool]:
        """Live switch positions, the single input to CapabilityRegistry."""
        return {
            "computer_control_mode": bool(self.computer_control_mode.enabled),
            "browser_control_enabled": bool(
                getattr(self, "browser_page_control_enabled", True)
            ),
            "web_search_enabled": bool(
                getattr(self, "_web_search_enabled", True)
            ),
            "screen_vision_enabled": bool(
                getattr(self.screen_monitor, "enabled", True)
            ),
            "project_access": self.project_mcp is not None,
        }

    def _answer_ability_question(
        self,
        user_input: str,
        state: dict[str, bool],
    ) -> str:
        """Answer "can you...?" from the registry, never from the model.

        Found live, and this is the worst kind of wrong answer there is:
        asked "can you control my browser?", Elaina said "I cannot control
        your browser. I can only provide guidance and assistance with
        information you share." She had been driving a real browser since
        Phase 4C. The capability context was in her prompt and the model
        answered from its generic assistant priors instead.

        A model cannot be trusted to report its own host application's
        feature set, so it is not asked to. Only the two slow, visible,
        state-changing abilities are intercepted here -- for web search or
        screen vision, just doing the thing beats asking about it.
        """
        text = str(user_input or "").strip()
        if not text or not CapabilityRegistry.is_ability_question(text):
            return ""

        if _ABILITY_INVENTORY_QUESTION.search(text):
            return CapabilityRegistry.inventory_sentence(state)

        match = CapabilityRegistry.match(text)
        if not match.matched or match.capability.id not in {
            "browser_control", "ui_control",
        }:
            return ""

        capability = match.capability
        blocked = CapabilityRegistry.blocked_reason(capability, state)
        if blocked:
            fix = CapabilityRegistry.fix_for(capability, state)
            answer = f"Yes, I have {capability.name} -- but {blocked}."
            return answer + (f" {_sentence_case(fix)} and I'll use it." if fix else "")

        offer = "Want me to use it now?"
        self.capability_offer.offer(
            capability_id=capability.id, goal=text, offer_text=offer,
        )
        print(f"[Ability] Answered from the registry for {capability.id}.")
        return f"Yes. I can {capability.summary}. {offer}"

    def _final_response_check(
        self,
        reply: str,
        *,
        user_input: str,
        messages,
        model: str,
        temperature: float,
        num_predict: int,
        keep_alive,
        max_words: int,
        max_sentences: int,
        forced: bool = False,
    ) -> str:
        """The last thing that happens before anything is said out loud.

        The same check already runs on the draft, and that turned out not to
        be enough: between it and here sit the advice rewrite, the finalizer,
        the condenser and five guards, and any of them can hand back
        something the earlier check would have rejected. Measured live, an
        answer about Seattle came back byte-for-byte after "no I mean I'm
        going to UW", with no guard line in the log at all -- because the
        guard had run, and passed, several transformations earlier.

        It is also no longer limited to conversation-shaped turns. A search
        answer can repeat itself just as easily, and did.
        """
        text = str(reply or "").strip()
        if not text:
            return reply
        if forced:
            # A forced reply is hand-written -- a greeting from the social
            # bank, a consent question, a capability note -- not the model
            # reaching for the nearest words. Both checks below exist to
            # catch the model, and running the echo strip over curated text
            # only damaged it: "hey" was answered "Hey! What's up?" and went
            # out as "What's up?", because the guard read the deliberate
            # mirroring in a greeting as parroting.
            return reply
        without_echo = ResponseQualityGuard.strip_current_turn_echo(
            text, user_input,
        )
        if without_echo != text:
            print(
                "\n[Response Guard] Removed a restatement of the current "
                "message."
            )
            text = without_echo
            reply = without_echo
        try:
            history = self.conversation.get_history()
        except Exception:
            return reply
        echoed = ResponseQualityGuard.is_pure_echo(text, user_input)
        if not echoed and not ResponseQualityGuard.should_retry(
            text, user_input, history
        ):
            return reply

        if echoed:
            # Nothing to strip: the echo is the whole reply, so the only
            # repair is to answer again. Live, "I see" was answered "I see."
            print(
                "\n[Response Guard] The final text only repeated the current "
                "message back; regenerating once."
            )
            complaint = (
                "You just said my own words back to me and added nothing. "
                "Reply to what I said instead, in one or two short "
                f"sentences, without restating it: {user_input}"
            )
        else:
            print(
                "\n[Response Guard] The final text repeated the previous "
                "answer after a new or corrected message; regenerating once."
            )
            complaint = (
                "That is the same answer you just gave, and it does not "
                "address what I actually said. Answer this, and only "
                f"this: {user_input}"
            )
        try:
            response = self.client.chat(
                model=model,
                messages=[
                    *messages,
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": complaint},
                ],
                stream=False,
                options={
                    "temperature": temperature, "num_predict": num_predict,
                },
                keep_alive=keep_alive,
                think=False,
            )
            fresh = TextFilter.for_voice_response(
                self._value(self._value(response, "message", {}), "content", ""),
                max_words=max_words,
                max_sentences=max_sentences,
            )
        except Exception as error:
            print(f"[Response Guard] Could not regenerate: {error}")
            return reply
        if not fresh.strip():
            return reply
        # The regenerated text is model output like any other, and was going
        # straight out unexamined. Live, the retry for "I see" came back "I
        # see. What's on your mind?" -- an echo the draft path would have
        # stripped, released only because it arrived on this path instead.
        fresh = ResponseQualityGuard.strip_current_turn_echo(
            fresh, user_input,
        )
        if ResponseQualityGuard.is_pure_echo(
            fresh, user_input
        ) or ResponseQualityGuard.should_retry(fresh, user_input, history):
            # Twice is enough. Saying so is better than saying the same
            # wrong thing a third time.
            print("[Response Guard] The retry repeated it too; saying so.")
            return (
                "Sorry -- I answered the wrong thing there. Say it once "
                "more and I'll take it properly?"
            )
        return fresh

    def _enforce_grounded_values(
        self,
        reply: str,
        *,
        user_input: str,
        action_performed: bool,
    ) -> str:
        """Never quote a price that nothing this session actually saw.

        Found live on a skeptical follow-up ("for real? that seems
        cheap"), routed as plain conversation with no tool call in it:
        "Trip.com shows prices starting at around 120,000 KRW for Harbour
        Plaza Hotels." Nothing was read; the figure, the currency, and the
        attribution were all generated. Doubting a number and being handed
        an invented one is the worst possible answer to that question.
        """
        text = str(reply or "").strip()
        if not text:
            return text
        evidence = " ".join((
            str(self._grounded_context.get("statement", "")),
            user_input,
        ))
        if not GroundedValueGuard.needs_correction(
            text, evidence=evidence, action_performed=action_performed,
        ):
            return text

        state = self._capability_state()
        if CapabilityRegistry.is_available("browser_control", state):
            offer = "I haven't actually checked that -- want me to look it up?"
            task_sessions = getattr(self, "task_sessions", None)
            active_problem = (
                task_sessions.active_recommendation()
                if task_sessions is not None else None
            )
            self.capability_offer.offer(
                capability_id="browser_control",
                goal=(
                    "Check the current price for: "
                    f"{self._grounded_context.get('subject', '') or user_input}"
                ),
                offer_text=offer,
                task_id=(active_problem.id if active_problem is not None else ""),
                task_query=(
                    active_problem.search_query()
                    if active_problem is not None else ""
                ),
            )
        else:
            offer = "I haven't actually checked that, so I'd rather not guess."
        print("[Grounding Guard] Removed a price nothing had verified.")
        return GroundedValueGuard.correct(text, evidence=evidence, offer=offer)

    def _enforce_grounded_entities(
        self,
        reply: str,
        *,
        user_input: str,
        action_performed: bool,
        evidence: str = "",
    ) -> str:
        """Never send someone to a shop nothing actually found.

        The same failure as an invented price, in a different shape.
        Measured live, with no search behind any of it: "check out local
        music stores in Seoul like Melody House or Guitar Center Korea",
        and "local stores like GS25 or Hanaro" -- GS25 being a convenience
        store. Naming a dish or a city stays fine; naming a business is a
        claim about the world, and this only fires when the reply is
        actually sending the person somewhere.
        """
        text = str(reply or "").strip()
        if not text or action_performed:
            return text
        grounding = " ".join((
            str(evidence or ""),
            str(self._grounded_context.get("statement", "")),
            user_input,
        ))
        invented = grounded_values.unverified_entities(
            text, evidence=grounding, request=user_input,
        )
        if not invented:
            return text
        print(
            "[Grounding Guard] Unverified place(s): "
            f"{', '.join(invented)}."
        )
        offer = (
            "I don't want to send you somewhere I haven't checked -- "
            "want me to look up real ones?"
        )
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(text)
            if sentence.strip()
            and not any(name in sentence for name in invented)
        ]
        rebuilt = " ".join(kept).strip()
        return f"{rebuilt} {offer}".strip() if rebuilt else offer

    def _rescue_capability_route(
        self,
        route: IntentDecision,
        user_input: str,
    ) -> tuple[IntentDecision, str]:
        """Never dead-end a real request against an ability Elaina has.

        The router refuses any ``computer_action`` it cannot ground to one
        narrow Phase-4A operation, and ``_handle_computer_action`` turned
        that refusal into "That PC action isn't supported yet" -- said
        live about browser control, an ability Elaina has had since Phase
        4C. The refusal is right about the *narrow operation*; it is wrong
        as a final answer, because the goal-driven planners exist exactly
        for requests that don't fit one structured operation.

        So an ungrounded computer request is re-aimed at the capability it
        actually names, and an ungrounded one with no capability at all
        gets an honest inventory instead of a canned refusal. Nothing here
        skips a safety check: the planner it hands to still grounds every
        element against the live UI or DOM and still pauses before
        anything committing.
        """
        state = self._capability_state()
        dead_ended = (
            route.intent == "computer_action"
            and route.computer_operation in {"none", "unsupported", ""}
        )
        # "Can you open Spotify?" is a polite request, not a question about
        # abilities -- it names a real target and the router grounds it, so
        # doing it beats describing it. "Can you control my browser?" names
        # no target and answers itself.
        asks_for_an_action = bool(_ACTION_REQUEST_SHAPE.search(user_input))
        # Checked ahead of every route, not just a dead-ended one. Found
        # live: the router sent "can you control my browser?" to the
        # browser planner as a grounded browser_action, the planner had no
        # page action to take, and its own model call answered the
        # question conversationally -- with "I cannot control your
        # browser." A question about an ability must never reach a planner
        # whose job is to act on goals.
        if not asks_for_an_action:
            ability_answer = self._answer_ability_question(user_input, state)
            if ability_answer:
                return replace(
                    route,
                    intent="conversation",
                    normalized_request=user_input,
                    reason="The user asked what Elaina can do.",
                    action_requested=False,
                    computer_operation="none",
                ), ability_answer
        conversational_action = (
            route.intent == "conversation"
            and not route.action_requested
            and asks_for_an_action
        )
        if not dead_ended and not conversational_action:
            return route, ""

        match = CapabilityRegistry.match(user_input)
        if not match.matched:
            if not dead_ended:
                return route, ""
            # An action was clearly requested and nothing Elaina has fits.
            # Say what she does have rather than a bare "unsupported".
            return route, (
                "I can't do that one. "
                + CapabilityRegistry.inventory_sentence(state)
            ).strip()

        capability = match.capability
        if conversational_action and match.confidence < 0.7:
            return route, ""

        blocked = CapabilityRegistry.blocked_reason(capability, state)
        if blocked:
            fix = CapabilityRegistry.fix_for(capability, state)
            self.capability_offer.clear()
            return route, (
                f"I can do that with {capability.name}, but {blocked}."
                + (f" {_sentence_case(fix)} and I'll run it." if fix else "")
            )

        operation = {
            "browser_control": "browser_action",
            "ui_control": "ui_action",
        }.get(capability.id, "")
        if not operation:
            if capability.id == "task_planning":
                print(
                    "[Capability Rescue] "
                    f"{route.intent}/{route.computer_operation or '(none)'} "
                    "-> task_action"
                )
                return replace(
                    route,
                    intent="task_action",
                    normalized_request=user_input,
                    reason=match.reason,
                    speech_act="action_request",
                    action_requested=True,
                    action_target=user_input,
                ), ""
            return route, ""

        print(
            "[Capability Rescue] "
            f"{route.intent}/{route.computer_operation or '(none)'} -> "
            f"{operation} ({match.reason})"
        )
        return replace(
            route,
            intent="computer_action",
            normalized_request=user_input.strip(),
            reason=match.reason,
            speech_act="action_request",
            action_requested=True,
            action_target=user_input.strip(),
            computer_operation=operation,
        ), ""

    def _enforce_action_commitment(
        self,
        reply: str,
        *,
        user_input: str,
        action_performed: bool,
    ) -> str:
        """Make "let me check that for you" mean something, or unsay it.

        Observed live: a conversation-routed turn produced "I can check
        prices directly through the browser. Let me open the website and
        find the current rates for you." -- twice, verbatim, with nothing
        ever opening. The response policy already instructs the model not
        to defer work it can do now; it deferred anyway, which is exactly
        the case this project handles structurally rather than with more
        prompt wording.

        Two outcomes, never a silent broken promise:
        * the ability exists and is on -> the promise becomes a real,
          answerable offer, and a matching consent offer is parked so the
          user's next "ok" actually runs it;
        * it doesn't, or it's switched off -> the promise is removed and
          replaced with the honest reason.
        """
        text = str(reply or "").strip()
        if not text or action_performed:
            return text
        if not ActionCommitmentGuard.promises_action(text):
            return text

        state = self._capability_state()
        # Which text described the action decides what the parked offer is
        # *for*. A vague turn ("for real? that seems cheap") followed by a
        # promise ("I can check Trip.com prices for you") means the promise
        # is the actionable goal -- parking the vague line instead gave the
        # browser planner nothing to work with, and it reported only
        # "That's done."
        goal_text = user_input
        match = CapabilityRegistry.match(user_input)
        if not match.matched:
            match = CapabilityRegistry.match(text)
            if match.matched:
                goal_text = ActionCommitmentGuard.promised_action(text) or text
        capability = match.capability
        if capability is None:
            print("[Commitment Guard] Removed a promise with no ability behind it.")
            # A short question, not the whole ability inventory: reciting
            # everything Elaina can do is a non-sequitur when the user just
            # said "ok" and the model invented something to promise.
            return ActionCommitmentGuard.strip_promise(
                text,
                replacement="What would you like me to do next?",
            )

        blocked = CapabilityRegistry.blocked_reason(capability, state)
        if blocked:
            fix = CapabilityRegistry.fix_for(capability, state)
            honest = f"I'd need {capability.name} for that, but {blocked}."
            if fix:
                honest += f" {_sentence_case(fix)} and I'll do it."
            print(f"[Commitment Guard] Promise dropped: {blocked}.")
            # The whole reply is replaced, not just the promise sentence:
            # "I can check prices through the browser" is itself false
            # while the switch is off, so keeping it would trade one wrong
            # claim for another.
            return honest

        offer_text = CapabilityRegistry.recommendation_for(capability.id, state)
        task_sessions = getattr(self, "task_sessions", None)
        active_problem = (
            task_sessions.active_recommendation()
            if task_sessions is not None else None
        )
        self.capability_offer.offer(
            capability_id=capability.id,
            goal=goal_text,
            offer_text=offer_text,
            task_id=(active_problem.id if active_problem is not None else ""),
            task_query=(
                active_problem.search_query()
                if active_problem is not None else ""
            ),
        )
        print(
            f"[Commitment Guard] Promise turned into an offer for "
            f"{capability.id}; awaiting the user's go-ahead."
        )
        return ActionCommitmentGuard.rewrite_promise_as_offer(text, offer_text)

    def _capability_context(self) -> str:
        """Elaina's own abilities, rendered from the one registry.

        This used to be a hand-written paragraph that drifted out of sync
        with what the code could actually do -- the drift the user heard as
        "That PC action isn't supported yet" about working browser control.
        It is now generated from brain/capabilities.py, so the description
        and the behaviour cannot disagree.
        """
        state = self._capability_state()
        return "\n".join(part for part in (
            self.agent_registry.capability_context(),
            CapabilityRegistry.context_text(state),
            (
                "Desktop Control Mode is "
                + ("ON" if state["computer_control_mode"] else "OFF")
                + ". Force-quit, deletion, and any committing web step "
                "(booking, buying, sending, submitting) always need a "
                "separate confirmation first; passwords and payments stay "
                "the user's own to enter. A request about 'this page' stays "
                "locked to the captured foreground surface."
            ),
        ) if part)

    def _followup_subject(self, request: str) -> str:
        """What a bare follow-up is about, or "" when it says so itself.

        "Check the price on the browser" right after a turn that named
        three Hong Kong hotels is only answerable with those names. They
        were already in the session's grounded context; nothing carried
        them across to the browser planner, which then honestly reported
        that it had no idea what to price.

        Kept narrow on purpose: a request that names its own subject gets
        no context at all, so an unrelated earlier topic can never
        redirect a self-contained goal.
        """
        text = str(request or "").strip()
        if not text or _NAMES_ITS_OWN_SUBJECT.search(text):
            return ""
        if not (
            _DEICTIC_REQUEST.search(text)
            or _BARE_ATTRIBUTE_REQUEST.search(text)
        ):
            return ""
        statement = str(self._grounded_context.get("statement", "")).strip()
        if not statement:
            session = self.task_sessions.current()
            if session is not None:
                statement = "; ".join(
                    str(getattr(item, "name", "")).strip()
                    for item in session.items
                    if str(getattr(item, "name", "")).strip()
                )
        statement = " ".join(statement.split())
        return statement[:400]

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
        if (
            not self.memory_enabled
            or self.memory_manager is None
            or self.extractor is None
            or self.consolidator is None
        ):
            return
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

    def _grounded_context_is_relevant(self, route, goal=None) -> bool:
        """Use retrieved evidence only for a follow-up about the same thing.

        The subject comparison is the part that was missing: a dinner
        follow-up was handed a GPU comparison verified two turns earlier,
        purely because both were follow-ups in the same session.
        """
        return should_include_grounded_context(
            has_statement=bool(
                self._grounded_context.get("statement", "").strip()
            ),
            intent=route.intent,
            is_follow_up=route.is_follow_up,
            topic_shift=route.topic_shift,
            grounded_subject=str(
                self._grounded_context.get("subject", "") or ""
            ),
            current_subject=str(
                getattr(goal, "subject", "") or route.topic or ""
            ),
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

    def _followup_subject_for(self, route, goal) -> str:
        """The subject to state in the prompt, or "" when the words carry it.

        Only for a message that means nothing on its own. A self-contained
        request already says what it is about, and naming it again would
        narrow an answer that did not need narrowing.
        """
        subject = str(getattr(goal, "subject", "") or "").strip()
        request = str(getattr(route, "normalized_request", "") or "").strip()
        if not subject or not request:
            return ""
        if subject.casefold() == request.casefold():
            return ""
        return subject if self._reads_as_followup(request) else ""

    def _build_factual_messages(
        self,
        question: str,
        evidence: str = "",
        *,
        include_grounded: bool = False,
        reset_history: bool = False,
        followup_subject: str = "",
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
            response_language=self.response_language,
            followup_subject=followup_subject,
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
            response_language=self.response_language,
        )

    def _memories_about(self, memories, goal):
        """Drop personal memories that have nothing to do with this subject.

        Reported live: "what should I eat for dinner?" then "which one would
        you choose?" answered about graphics cards, because a GPU
        conversation earlier in the session was the nearest neighbour of a
        sentence that means nothing on its own.

        Embedding similarity is the wrong authority once the goal layer has
        resolved a subject. The conversation is already in the prompt and
        already carries the referent; a stored memory only earns its place
        if it is actually about the same thing. When nothing survives, the
        turn is answered from the conversation, which is the correct
        precedence -- immediate context above long-term recall.
        """
        subject = str(getattr(goal, "subject", "") or "").strip()
        if not subject or not memories:
            return memories

        wanted = {
            word for word in re.findall(r"[^\W_]{4,}", subject.casefold())
        }
        if not wanted:
            return memories

        kept = [
            memory
            for memory in memories
            if wanted & set(
                re.findall(r"[^\W_]{4,}", str(
                    getattr(memory, "content", "")
                ).casefold())
            )
        ]
        dropped = len(memories) - len(kept)
        if dropped:
            print(
                f"[Recall] Set aside {dropped} memory item(s) unrelated to "
                f"{subject!r}."
            )
        return kept

    def _shaped_query(self, query: str, problem) -> str:
        """The same query, pointed at where candidates actually live.

        The sources are the locale layer's own, chosen by category rather
        than named here: a Korean restaurant search belongs on the Korean
        restaurant sites for the same reason a hotel search belongs on the
        hotel ones. Scoping is dropped entirely when the market has no
        table for this category, which leaves the plain query.
        """
        shape = candidate_fit.expected_shape(problem)
        # Not site: operators. Measured live: scoping the first search to
        # the locale's own restaurant hosts returned "No results found."
        # and cost a whole query before the plain one ran. What does work
        # is asking for the shape of thing wanted -- a price or a review is
        # what listings have and articles about listings do not.
        if str(getattr(problem, "category", "") or "") == "realestate":
            # Rental search indexes respond much better to listing-shaped
            # terms than to currency punctuation and conversational order.
            # Keep every established value, just place the concrete market,
            # unit type and rent evidence first.
            location = str(getattr(problem, "location", "") or "").strip()
            housing_type = " ".join(
                problem.values(recommendation_state.HOUSING_TYPE)
            ).strip()
            budget = " ".join(
                problem.values(recommendation_state.BUDGET)
            ).replace("$", "").strip()
            anchor = str(getattr(problem, "anchor", "") or "").strip()
            return " ".join(part for part in (
                location,
                housing_type,
                "apartment",
                budget,
                "monthly rent address available",
                f"near {anchor}" if anchor else "",
            ) if part)
        elif shape == candidate_fit.PRODUCT:
            extra = "price buy"
        elif shape == candidate_fit.PLACE:
            extra = "reviews address"
        else:
            return query
        words = query.split()
        for word in extra.split():
            if word.casefold() not in {w.casefold() for w in words}:
                words.append(word)
        return " ".join(words)

    def _note_preference(self, user_input: str) -> str:
        """Record what this turn says about what she should usually use.

        Nothing is written from a bare choice. "Use X for this one" sets a
        turn-scoped override and touches nothing saved; only language that
        is plainly about the future reaches the profile at all.
        """
        self._source_override = ""
        self._tool_override = ""
        try:
            statement = preferences.read(user_input)
        except Exception as error:
            print("[Preference Resolution]")
            print(f"  Applied: no\n  Why: {error}")
            return ""
        if statement is None:
            return ""
        if statement.action == "override":
            if statement.kind == profile_module.TOOL_FOR:
                self._tool_override = statement.value
            elif statement.kind == profile_module.SOURCE_FOR:
                self._source_override = statement.value
            # Scoped to the open task where there is one, so a clarifying
            # question in the middle of it does not drop back to the saved
            # default. A new task opens a new problem and drops it.
            held = (
                self.task_sessions.note_source_override(statement.value)
                if statement.kind == profile_module.SOURCE_FOR else False
            )
            resolution = preferences.resolve(
                self.user_profile, statement.kind, statement.domain,
                context=statement.context, override=statement.value,
            )
            print(resolution.log_block())
            print(f"  Scope: {'this task' if held else 'this turn'}")
            return ""
        return preferences.apply(self.user_profile, statement)

    def _tool_preference_for(self, request: str, *, goal: Goal | None = None):
        """Resolve TOOL_FOR only for a request that reads as music playback."""
        provider_from_goal = goal.value("provider") if goal is not None else ""
        base = preferences.resolve(
            self.user_profile,
            profile_module.TOOL_FOR,
            "music",
            override=self._tool_override,
            default="Spotify",
        )
        provider = provider_from_goal or base.choice
        if not provider:
            return preferences.Resolution(
                kind=profile_module.TOOL_FOR, domain="music", applied=False,
                why="no provider is saved or named for this playback task",
            )
        media = classify_media_request(
            request,
            application=provider,
            preferred_provider=True,
        )
        if goal is None and media.kind == "none":
            return preferences.Resolution(
                kind=profile_module.TOOL_FOR, domain="music", applied=False,
                why="this turn is not a music playback request",
            )
        if provider_from_goal and provider_from_goal.casefold() != base.choice.casefold():
            return preferences.resolve(
                self.user_profile, profile_module.TOOL_FOR, "music",
                override=provider_from_goal,
            )
        return base

    def _source_resolution(self, problem, query: str):
        domain = str(getattr(problem, "category", "") or "")
        ranked = acquisition.surface_names(self.user_locale, domain, query)
        return preferences.resolve(
            self.user_profile,
            profile_module.SOURCE_FOR,
            domain or str(getattr(problem, "subject", "") or ""),
            context=" ".join(problem.values(recommendation_state.SITUATION)),
            override=(
                getattr(self, "_source_override", "")
                or self.task_sessions.source_override()
            ),
            default=ranked[0] if ranked else "",
        )

    def _sources_for(self, problem, query: str, *, resolution=None) -> tuple[str, ...]:
        """The surfaces this market uses to find this kind of thing.

        Asked for by category, so the judgement stays in the locale layer:
        a Korean restaurant search reaches for the Korean restaurant
        surfaces for the same reason a hotel search reaches for the hotel
        ones, and an unserved market reaches for none.
        """
        domain = str(getattr(problem, "category", "") or "")
        ranked = acquisition.surface_names(self.user_locale, domain, query)
        resolution = resolution or self._source_resolution(problem, query)
        if not resolution.applied or not resolution.choice:
            return ranked
        # Preferred, not mandated: it goes to the front of the market's own
        # ranking rather than replacing it, so a task the surface cannot
        # serve can still be served by the next one down.
        chosen = resolution.choice
        return (chosen,) + tuple(
            site for site in ranked if site.casefold() != chosen.casefold()
        )

    def _surface_hosts_for(self, problem, query: str) -> tuple[str, ...]:
        """The same surfaces as hosts, for reading them out of results."""
        return acquisition.surface_hosts(
            self.user_locale,
            str(getattr(problem, "category", "") or ""),
            query,
        )

    def _research_for_recommendation(self, query: str, *, resolution=None):
        """Find candidates, check them, and only then call any of them good.

        The cascade, in order, stopping as soon as the answer is settled:

            search -> is this even a candidate -> deterministic checks
            -> reject conflicts -> targeted re-search if something
            important is still unevidenced -> semantic check if it still
            is -> rank -> recommend, or say the evidence is not there

        Returns ``None`` whenever this is not a constrained recommendation,
        so every other lookup keeps the ordinary research path untouched.
        """
        problem = self.task_sessions.active_recommendation()
        if problem is None or not problem.constraints or not query:
            return None

        shape = candidate_fit.expected_shape(problem)
        first = self._shaped_query(query, problem)
        resolution = resolution or self._source_resolution(problem, query)
        preferred = self._sources_for(problem, query, resolution=resolution)
        if preferred:
            # The surface they asked for, named in the query itself. This is
            # what makes a saved preference change the result rather than
            # only the log -- a surface named in a search is how the
            # entities behind it get reached.
            first = f"{first} {preferred[0]}"
            print("[Acquisition]")
            print(f"  Candidate shape: {shape}")
            print(f"  Source: {preferred[0]}")
        fits = self._candidates_for(
            first, problem, shape,
            preferred_source=(preferred[0] if preferred else ""),
        )
        queries = [first]
        if preferred and not fits:
            # The preference was consulted and attempted, but no returned page
            # could be attributed to that surface.  Fall back honestly rather
            # than claiming the preferred source supplied generic results.
            first = self._shaped_query(query, problem)
            fits = self._candidates_for(first, problem, shape)
            queries.append(first)

        # A scoped search can come back thin, and an unscoped one can come
        # back full of articles. Either way a second attempt is worth one
        # more query -- and no more than one, because this is a turn the
        # person is waiting through.
        survivors = candidate_fit.viable(fits)
        unresolved = candidate_fit.unresolved_constraints(fits, problem)
        if len(survivors) < 2 or unresolved:
            # A surface in the results is a finding, not a failure: it says
            # this is where this market keeps these. Naming it in the next
            # query is how the entities behind it get reached -- measured,
            # "diningcode <query>" returns restaurant pages where the bare
            # query returns writing about restaurants.
            reached = [fit.name for fit in candidate_fit.surfaces(fits)]
            if reached:
                print("[Recommendation Reasoning]")
                print("  Decision: acquire through a surface")
                print(f"  Why: {reached[0][:48]} indexes these; it is not one")
            retry = self._retry_query(
                query, problem, unresolved, shape,
                preferred,
            )
            if retry and retry.casefold() != first.casefold():
                print("[Recommendation Reasoning]")
                print("  Decision: search again")
                print(
                    "  Why: "
                    + (
                        f"nothing yet shows {', '.join(unresolved)}"
                        if unresolved
                        else "too few candidates of the right kind"
                    )
                )
                more = self._candidates_for(
                    retry, problem, shape,
                    preferred_source=(preferred[0] if preferred else ""),
                )
                if more:
                    queries.append(retry)
                    fits = candidate_fit.evaluate(
                        [
                            {
                                "title": fit.name, "url": fit.url,
                                "summary": fit.summary,
                            }
                            for fit in tuple(fits) + tuple(more)
                        ],
                        problem,
                        shape=shape,
                        surface_hosts=self._surface_hosts_for(
                            problem, query,
                        ),
                    )

        if preferred and not candidate_fit.confident(fits, problem):
            # Reaching a map/search page is proof of the surface, not proof of
            # any restaurant/product/job.  If two source-directed attempts
            # still produced no confirmed fit, use ordinary acquisition.
            # A concrete-looking room or school is not enough to suppress
            # fallback when the task asks specifically for a studio.
            print("[Execution Selection]")
            print("  Required capability: web_search")
            print(f"  Preferred provider/source: {preferred[0]}")
            print("  Selected: (none)")
            print("  Fallback: ordinary acquisition")
            print("  Why: preferred source yielded no confirmed fit")
            generic_query = self._shaped_query(query, problem)
            generic = self._candidates_for(generic_query, problem, shape)
            if generic:
                queries.append(generic_query)
                fits = candidate_fit.evaluate(
                    [
                        {"title": fit.name, "url": fit.url, "summary": fit.summary}
                        for fit in tuple(fits) + tuple(generic)
                    ],
                    problem,
                    shape=shape,
                    surface_hosts=self._surface_hosts_for(problem, query),
                )

        # A listing index often names an individual property but omits its
        # rent from that first snippet. Verify one surviving named lead with
        # one ordinary search before giving up; this is the existing bounded
        # second search, aimed at the missing evidence rather than a new
        # acquisition path.
        if (
            str(getattr(problem, "category", "") or "") == "realestate"
            and not candidate_fit.confident(fits, problem)
        ):
            lead = next((
                fit for fit in candidate_fit.viable(fits)
                if not self._generic_source_label(fit.name)
            ), None)
            if lead is not None:
                housing_type = " ".join(
                    problem.values(recommendation_state.HOUSING_TYPE)
                ).strip()
                location = str(
                    getattr(problem, "location", "") or ""
                ).strip()
                verification_query = " ".join(part for part in (
                    lead.name, location, housing_type, "rent price",
                ) if part)
                print("[Recommendation Reasoning]")
                print("  Decision: verify named rental")
                print(f"  Why: {lead.name[:48]} has no confirmed rent yet")
                verified = self._candidates_for(
                    verification_query, problem, shape,
                )
                if verified:
                    queries.append(verification_query)
                    fits = candidate_fit.evaluate(
                        [
                            {
                                "title": fit.name,
                                "url": fit.url,
                                "summary": fit.summary,
                            }
                            for fit in tuple(verified) + tuple(fits)
                        ],
                        problem,
                        shape=shape,
                        surface_hosts=self._surface_hosts_for(problem, query),
                    )

        if not fits:
            return None

        # Still nothing showing an important quality either way. A search
        # result rarely says "soft" about a restaurant, and silence is not
        # evidence -- so one bounded judgement, on the survivors only.
        for constraint in candidate_fit.unresolved_constraints(
            fits, problem,
        )[:1]:
            verdicts = semantic_fit.check(
                self.client, self.model,
                candidate_fit.viable(fits), constraint,
            )
            if verdicts:
                fits = candidate_fit.with_semantic(fits, constraint, verdicts)
                print("[Recommendation Reasoning]")
                print(f"  Decision: judged '{constraint}' semantically")
                print(
                    "  Why: no source stated it, and it is what they "
                    "asked for"
                )

        fitting = [fit for fit in fits if fit.verdict == "FITS"]
        settled = candidate_fit.confident(fits, problem)
        print(candidate_fit.log_block(
            fits,
            chosen=fitting[0].name if (settled and fitting) else "",
            why=(
                fitting[0].because() if (settled and fitting)
                else "insufficient evidence to rank confidently"
            ),
        ))
        self.task_sessions.record_candidates(
            [fit.name for fit in fitting]
            or [fit.name for fit in candidate_fit.viable(fits)],
            evidence=(candidate_fit.shortlist_text(fits),),
        )

        if settled:
            instruction = (
                "CANDIDATES, already checked against what the user asked "
                "for. Recommend the best of the ones marked FITS and say "
                "in one clause why it suits them better than the others. "
                "A MISMATCH may only be named as a mismatch, never as the "
                "recommendation. OFF-TARGET items are articles and SOURCE "
                "items are sites to search -- neither is a real option, so "
                "do not offer either as one."
            )
        else:
            instruction = (
                "CANDIDATES, checked against what the user asked for -- and "
                "none could be shown to meet it. Say plainly that you could "
                "not confirm which of these actually suits them, then offer "
                "what you did find as unverified options. Do not pick a "
                "winner, and do not present an UNCHECKED or OFF-TARGET item "
                "as though it fits. A SOURCE is a site to search, never a "
                "recommendation -- name one only as somewhere they could "
                "look, and only if there is nothing concrete to give."
            )
        return ResearchResult(
            evidence=f"{instruction}\n{candidate_fit.shortlist_text(fits)}",
            queries=tuple(queries),
        )

    def _candidates_for(
        self, query: str, problem, shape: str, *, preferred_source: str = "",
    ):
        """One structured search, read as candidates, surfaces or neither."""
        try:
            found = self.research_agent.research_structured(
                search_query=query, max_results=6,
            )
        except Exception as error:
            print(
                "[Recommendation Reasoning]\n  Decision: plain search"
                f"\n  Why: structured results unavailable ({error})"
            )
            return ()
        surface_hosts = self._surface_hosts_for(problem, query)
        if preferred_source:
            selection = acquisition.select_surface_results(
                found, preferred_source, known_hosts=surface_hosts,
            )
            print(selection.log_block())
            if not selection.applied:
                preferred_hosts = acquisition.hosts_for_source(
                    preferred_source, surface_hosts,
                )
                if preferred_hosts:
                    try:
                        targeted = self.research_agent.research_structured(
                            search_query=f"{query} site:{preferred_hosts[0]}",
                            max_results=6,
                        )
                    except Exception:
                        targeted = ()
                    selection = acquisition.select_surface_results(
                        targeted, preferred_source, known_hosts=preferred_hosts,
                    )
                    print(selection.log_block())
                if selection.applied:
                    found = selection.results
                    surface_hosts = selection.hosts
                else:
                    observed = self._candidates_from_preferred_surface(
                        query, problem, shape,
                        preferred_source=preferred_source,
                        allowed_hosts=preferred_hosts,
                    )
                    if observed:
                        return observed
                    return ()
            else:
                found = selection.results
                surface_hosts = selection.hosts
        fits = candidate_fit.evaluate(
            found, problem, shape=shape, surface_hosts=surface_hosts,
        )
        if preferred_source and not self._usable_named_candidates(fits, shape):
            # A search index can prove that the requested surface exists yet
            # expose only its list page, or concrete record URLs whose titles
            # are merely the URLs themselves.  In that case use the existing
            # live browser capability against the selected host and extract
            # names from what the rendered source actually shows.  This is a
            # generic source escalation: the locale supplies the hosts and
            # BrowserActionPlanner enforces them; no site-specific workflow
            # lives here.
            observed = self._candidates_from_preferred_surface(
                query, problem, shape,
                preferred_source=preferred_source,
                allowed_hosts=surface_hosts,
                seed_urls=tuple(
                    str(item.get("url", "") or "")
                    for item in sorted(
                        found,
                        key=lambda candidate: (
                            0 if acquisition.classify(
                                str(candidate.get("url", "") or ""),
                                surface_hosts=surface_hosts,
                            ) == acquisition.SOURCE_SURFACE else 1
                        ),
                    )
                    if str(item.get("url", "") or "")
                ),
            )
            if observed:
                return observed
        return fits

    @staticmethod
    def _usable_named_candidates(fits, shape: str) -> tuple:
        """Concrete candidates with a human name and evidence of their shape."""
        return tuple(
            fit for fit in candidate_fit.viable(fits)
            if not re.match(r"^https?://", str(fit.name or ""), re.IGNORECASE)
            and not ChatEngine._generic_source_label(fit.name)
            and not (
                acquisition.host_of(fit.url)
                and acquisition.host_of(fit.url) in fit.name.casefold()
            )
            and candidate_fit.looks_like(
                fit.name, fit.url, fit.summary, shape,
            )
        )

    def _candidates_from_preferred_surface(
        self,
        query: str,
        problem,
        shape: str,
        *,
        preferred_source: str,
        allowed_hosts: tuple[str, ...],
        seed_urls: tuple[str, ...] = (),
    ):
        """Read concrete entities from one selected live source surface.

        The browser planner performs and verifies the navigation/search.  A
        candidate is accepted only when TaskExtractor found its name in the
        text read back from that live page; planner prose alone is never
        enough to manufacture an entity.
        """
        try:
            available = CapabilityRegistry.is_available(
                "browser_control", self._capability_state(),
            )
        except Exception:
            available = False
        if not available or not allowed_hosts:
            print("[Execution Selection]")
            print("  Required capability: browser_control")
            print(f"  Preferred provider/source: {preferred_source}")
            print("  Selected: (none)")
            print("  Fallback: ordinary acquisition")
            print(
                "  Why: selected source needs a live page read, but browser "
                "control is unavailable"
            )
            return ()

        print("[Execution Selection]")
        print("  Required capability: browser_control")
        print(f"  Preferred provider/source: {preferred_source}")
        print(f"  Selected: {preferred_source}")
        print("  Fallback: (none)")
        print("  Why: indexed results exposed a source surface, not named entities")
        goal = (
            f"On {preferred_source}, search for {query}. Read the live result "
            f"list and report at least three concrete {shape} names with any "
            "visible rating, review, address, price, or availability evidence. "
            f"Do not present {preferred_source} itself as an option. This is "
            "a read-only lookup."
        )
        screen_run = getattr(self, "browser_driver", "") == "screen"
        plan_result = None
        page_texts = []
        if screen_run:
            self.cursor_driver.begin_run()
        try:
            if seed_urls:
                unique_urls = tuple(dict.fromkeys(seed_urls))
                surfaces = tuple(
                    url for url in unique_urls
                    if acquisition.classify(
                        url, surface_hosts=allowed_hosts,
                    ) == acquisition.SOURCE_SURFACE
                )
                if surfaces:
                    surface_url = min(surfaces, key=len)
                    self.browser_service.open_url(surface_url)
                    if screen_run:
                        time.sleep(2.0)
                    # Preserve the indexed, already-filtered result page
                    # before trying its location field. Marketplace search
                    # boxes commonly accept a city, not a full semantic
                    # query; rewriting the field can discard the studio and
                    # budget filters encoded by the result URL.
                    initial_page = self.browser_observer.read_text(None)
                    if getattr(initial_page, "status", "") == "observed":
                        page_texts.append(initial_page)
                    direct_search = False
                    observation = self.browser_observer.describe_page(None)
                    field = next((
                        element for element in getattr(observation, "elements", ())
                        if str(getattr(element, "role", "") or "").casefold()
                        in {"textbox", "searchbox", "combobox"}
                    ), None)
                    submit = getattr(self.browser_control, "submit", None)
                    if field is not None and callable(submit):
                        filled = self.browser_control.fill(
                            getattr(observation, "tab_index", None),
                            field.id,
                            query,
                            expected_label=field.label,
                            expected_url=getattr(observation, "url", ""),
                            expected_scan_id=getattr(observation, "scan_id", ""),
                            expected_href=getattr(field, "href", ""),
                        )
                        if getattr(filled, "status", "") == "filled":
                            submitted = submit(
                                getattr(observation, "tab_index", None),
                            )
                            direct_search = getattr(
                                submitted, "status", "",
                            ) == "clicked"
                            if direct_search and screen_run:
                                time.sleep(2.0)
                    if not direct_search:
                        plan_result = self.browser_action_planner.act(
                            (
                                f"Use the search field on the currently open "
                                f"{preferred_source} page to search for {query}. "
                                f"Read the result list and report concrete {shape} "
                                "names with visible rating, review, address, hours, "
                                "or category evidence. This is a read-only lookup."
                            ),
                            allow_direct_navigation=False,
                            allowed_hosts=tuple(allowed_hosts),
                            source_names=(preferred_source,),
                        )
                    observed = self.browser_observer.read_text(None)
                    if getattr(observed, "status", "") == "observed":
                        page_texts.append(observed)
                detail_urls = tuple(
                    url for url in unique_urls if url not in surfaces
                )
                for url in detail_urls[:4]:
                    host = acquisition.host_of(url)
                    if not any(
                        host == allowed or host.endswith(f".{allowed}")
                        for allowed in allowed_hosts
                    ):
                        continue
                    self.browser_service.open_url(url)
                    if screen_run:
                        # Map/directory surfaces populate their result lists
                        # after DOMContentLoaded. Give the accessible tree one
                        # short bounded render window before reading it.
                        time.sleep(2.0)
                    observed = self.browser_observer.read_text(None)
                    if getattr(observed, "status", "") == "observed":
                        page_texts.append(observed)
            else:
                plan_result = self.browser_action_planner.act(
                    goal,
                    allow_direct_navigation=False,
                    allowed_hosts=tuple(allowed_hosts),
                    source_names=(preferred_source,),
                )
                # A bounded planner may stop after reaching and observing the
                # right surface (for example, a later optional click went stale).
                # The live page is still valid evidence, so read and validate it
                # before deciding that the source execution failed.
                page_texts.append(self.browser_observer.read_text(None))
        except Exception as error:
            print("[Execution Selection]")
            print("  Required capability: browser_control")
            print(f"  Preferred provider/source: {preferred_source}")
            print("  Selected: (none)")
            print("  Fallback: ordinary acquisition")
            print(
                "  Why: live source read failed safely "
                f"({type(error).__name__}: {error})"
            )
            return ()
        finally:
            if screen_run:
                reclaimed = (
                    plan_result is not None
                    and getattr(plan_result, "failure_code", "") == "user_took_over"
                )
                self.cursor_driver.end_run(restore=not reclaimed)

        page_texts = [
            page for page in page_texts
            if getattr(page, "status", "") == "observed"
        ]
        if not page_texts:
            print("[Execution Selection]")
            print("  Required capability: browser_control")
            print(f"  Preferred provider/source: {preferred_source}")
            print("  Selected: (none)")
            print("  Fallback: ordinary acquisition")
            print("  Why: the live source page exposed no readable text")
            return ()
        page_texts = [
            page for page in page_texts
            if (
                (page_host := acquisition.host_of(
                    str(getattr(page, "url", "") or "")
                ))
                and any(
                    acquisition.same_site_host(page_host, host)
                    for host in allowed_hosts
                )
            )
        ]
        if not page_texts:
            print("[Execution Selection]")
            print("  Required capability: browser_control")
            print(f"  Preferred provider/source: {preferred_source}")
            print("  Selected: (none)")
            print("  Fallback: ordinary acquisition")
            print("  Why: the rendered page was not on the selected source host")
            return ()
        texts = tuple(
            str(getattr(page, "text", "") or "").strip()
            for page in page_texts
            if str(getattr(page, "text", "") or "").strip()
        )
        text = "\n".join(texts).strip()
        if not text:
            return ()
        candidate_extractor = getattr(
            self.task_extractor, "extract_candidates", None,
        )
        used_candidate_extractor = callable(candidate_extractor)
        extractor_shape = shape
        if str(getattr(problem, "category", "") or "") == "realestate":
            housing_type = " ".join(
                problem.values(recommendation_state.HOUSING_TYPE)
            ).strip()
            extractor_shape = (
                f"{housing_type} apartment listing".strip()
            )
        items = tuple(
            item
            for page_body in texts
            for item in (
                candidate_extractor(
                    page_body,
                    shape=extractor_shape,
                    source_type="browser_observed",
                    source=preferred_source,
                )
                if used_candidate_extractor else self.task_extractor.extract(
                    page_body,
                    source_type="browser_observed",
                    source=preferred_source,
                )
            )
        )
        print("[Acquisition]")
        print(f"  Live source pages read: {len(page_texts)}")
        print(f"  Live source text chars: {len(text)}")
        print(f"  Extracted named items: {len(items)}")
        if items:
            print(
                "  Extracted: "
                + "; ".join(
                    str(getattr(item, "name", "") or "")[:48]
                    for item in items[:5]
                )
            )
        lowered = text.casefold()
        found = []
        for item in items:
            name = str(getattr(item, "name", "") or "").strip()
            if not name or name.casefold() not in lowered:
                continue
            if self._generic_source_label(name):
                continue
            attributes = getattr(item, "attributes", {}) or {}
            if (
                not used_candidate_extractor
                and not self._extracted_item_has_shape(attributes, shape)
            ):
                continue
            detail = "; ".join(
                f"{key}: {value}" for key, value in attributes.items()
            )
            found.append({
                "title": name,
                "url": "",
                "summary": " ".join(
                    part for part in (
                        detail, f"Observed on {preferred_source}."
                    ) if part
                ),
            })
        if not found:
            print("[Execution Selection]")
            print("  Required capability: browser_control")
            print(f"  Preferred provider/source: {preferred_source}")
            print("  Selected: (none)")
            print("  Fallback: ordinary acquisition")
            print(
                "  Why: selected live source yielded no concrete named entities"
                + (
                    f" ({getattr(plan_result, 'failure_code', '')})"
                    if getattr(plan_result, "failure_code", "") else ""
                )
            )
            print(f"  Source text excerpt: {' '.join(text.split())[:240]}")
            return ()
        return candidate_fit.evaluate(
            found, problem, shape=shape, surface_hosts=allowed_hosts,
        )

    @staticmethod
    def _extracted_item_has_shape(attributes, shape: str) -> bool:
        """Require live extraction to carry evidence of the requested kind."""
        keys = " ".join(str(key).casefold() for key in attributes)
        if shape == candidate_fit.PLACE:
            return any(token in keys for token in (
                "rating", "review", "address", "hours", "category",
                "평점", "리뷰", "주소", "영업", "업종",
            ))
        if shape == candidate_fit.PRODUCT:
            return any(token in keys for token in (
                "price", "cost", "model", "seller", "stock", "availability",
                "가격", "모델", "판매", "재고",
            ))
        return bool(attributes)

    @staticmethod
    def _generic_source_label(name: str) -> bool:
        """Whether a short name denotes a source UI, not an entity."""
        text = " ".join(str(name or "").split()).strip()
        if not text or len(text.split()) > 4:
            return False
        return bool(re.search(
            r"(?:\b(?:maps?|places?|booking|search|results?|marketplace)"
            r"|지도|플레이스|예약|검색)\s*$",
            text,
            re.IGNORECASE,
        ))

    @staticmethod
    def _retry_query(query, problem, unresolved, shape, sources=()) -> str:
        """A second query aimed at what the first one left open.

        Puts the unevidenced quality at the front, where a search engine
        weighs it most, and names the kind of thing wanted so the results
        are candidates rather than writing about candidates.
        """
        parts = [" ".join(unresolved)] if unresolved else []
        parts.append(query)
        # The locale's own sources for this category, by name rather than
        # as a site: filter -- a name is a search term the engine can weigh,
        # where the filter returned nothing at all.
        parts.append(" ".join(sources[:2]))
        seen: set[str] = set()
        words: list[str] = []
        for word in " ".join(part for part in parts if part).split():
            if word.casefold() in seen:
                continue
            seen.add(word.casefold())
            words.append(word)
        return " ".join(words)

    def _answered_dimension(self, pending, reply: str):
        """Fold an answer into the open problem, and say what happens next.

        Either the next question worth asking, or an acknowledgement of what
        is now known -- never a search on its own, because answering a
        question is not the same as asking for results.
        """
        problem = self.task_sessions.answer_recommendation_dimension(
            pending.task_id, pending.slot, reply,
        )
        if problem is None:
            return None, pending.question
        print("[Recommendation Reasoning]")
        print(f"  Decision: record {pending.slot}")
        print(f"  Why: the turn answers the question she just asked")
        print(problem.log_block())

        nxt = problem.missing_dimension()
        if nxt:
            question = problem.question_for(nxt)
            self.clarification.offer(
                goal=Goal(kind="recommendation", utterance=problem.subject),
                slot=nxt,
                question=question,
                task_id=problem.id,
            )
            self.task_sessions.note_dimension_asked(nxt)
            spoken = f"Got it. {question}"
        else:
            if problem.lookup_requested:
                query = problem.search_query()
                print("[Clarification]")
                print(f"  task_id: {problem.id}")
                print(f"  dimension: {pending.slot}")
                print(f"  answer accepted: {reply}")
                print("  cleared: yes")
                return IntentDecision(
                    intent="web_search",
                    confidence=1.0,
                    normalized_request=query,
                    reason=(
                        "The original lookup request resumes after its "
                        "last clarification was answered."
                    ),
                    is_follow_up=True,
                    speech_act="action_request",
                    action_requested=True,
                    action_target=query,
                    requires_external_evidence=True,
                    recommendation_needed=True,
                    search_query=query,
                ), ""
            spoken = f"Got it -- {problem.search_query()}."
        return IntentDecision(
            intent="conversation",
            confidence=1.0,
            normalized_request=reply,
            reason="The user answered the question about their preference.",
            is_follow_up=True,
        ), spoken

    def _ask_missing_dimension(self, problem, request: str = "") -> str:
        """Ask the one question that would change which candidates come back.

        One at a time, once each, and only when the answer genuinely splits
        the candidate set -- "electric or acoustic" does, "what colour" does
        not. A low-stakes suggestion beats an interrogation, so nothing is
        asked for advice-shaped requests at all.
        """
        if recommendation_state.asks_where(request):
            # "Where can I buy a guitar in Seoul?" wants places, not a
            # narrowing question. Answering "electric or acoustic?" to it
            # is answering a question they did not ask.
            return ""
        dimension = problem.missing_dimension()
        if not dimension:
            return ""
        question = problem.question_for(dimension)
        if not question:
            return ""
        # One outstanding question at a time, across every kind. Measured
        # live: a proactive "want me to search?" was still pending when the
        # budget answer arrived, so "About 500,000 won" was read as
        # declining the offer and came back "Okay, I'll leave it."
        self.capability_offer.clear()
        # Held by the same gate as every other outstanding question, so
        # only one is ever open and it expires the same way.
        self.clarification.offer(
            goal=Goal(kind="recommendation", utterance=problem.subject),
            slot=dimension,
            question=question,
            task_id=problem.id,
        )
        self.task_sessions.note_dimension_asked(dimension)
        print("[Recommendation Reasoning]")
        print(f"  Decision: clarify")
        print(
            f"  Why: {dimension} is unresolved and changes which "
            "candidates are worth finding"
        )
        return question

    def _reselect_for_options(self, route, goal):
        """Ask the same two layers again, with evidence declared necessary.

        Nothing is decided here that they do not decide -- the route is
        restated with the one fact the router missed (this turn wants real
        options), and interaction/capability run again unchanged.
        """
        asking = replace(
            route,
            intent="web_search",
            computer_operation="",
            requires_external_evidence=True,
            recommendation_needed=True,
        )
        decision = interaction.decide(asking, goal=goal)
        capability = capability_selection.select(
            goal, decision, route=asking, failures=self._capability_failures,
        )
        return decision, capability

    def _track_recommendation(
        self, route, goal, decision, user_input="", *, resume_problem_id="",
    ):
        """Keep the open recommendation current, and say why in the log.

        A recommendation is a problem the conversation works on across
        several turns, not a shape of answer produced independently each
        time. The turn that establishes the constraint ("I have a sore
        throat") is never the turn that needs it ("pull up some spots"),
        so this runs whether or not the current turn acts.
        """
        active = self.task_sessions.active_recommendation()
        if (
            active is not None
            and resume_problem_id
            and active.id == resume_problem_id
        ):
            print("[Recommendation Reasoning]")
            print(f"  Decision: {decision.mode}")
            print("  Why: the existing task payload resumed")
            return active
        wants_one = bool(
            getattr(goal, "recommendation", False)
            or goal.intent in {goal_intent.RECOMMEND, goal_intent.COMPARE}
            # Measured live: "I want a guitar." came back as plain
            # conversation with recommendation_needed false and the topic
            # "personal desire", so no problem was opened at all and the
            # two turns after it had nothing to attach to. The words are a
            # better signal than the flag.
            or recommendation_state.starts_a_recommendation(user_input)
        )
        if not wants_one and active is None:
            return None

        # The person's own words, not the router's paraphrase of them.
        # Measured live: "Actually my throat hurts, something soft" reached
        # here as "Throat hurts, something soft" -- without "actually" the
        # revision was invisible, and without "my" the situation reader had
        # nothing to match, so the Korean BBQ it was meant to retire stayed
        # in the problem and went on into the query.
        request = str(
            user_input or route.normalized_request or "",
        ).strip()
        subject = str(getattr(goal, "subject", "") or "").strip()
        before = active
        problem = self.task_sessions.note_recommendation_turn(
            request,
            subject=subject,
            topic_shift=bool(getattr(route, "topic_shift", False)),
            location=(
                self.task_sessions.focus().background.get("location", "")
                if self.task_sessions.focus() is not None else ""
            ),
            anchor=(
                self.task_sessions.focus().background.get("about", "")
                if self.task_sessions.focus() is not None else ""
            ),
        )

        self._recommendation_restarted = (
            before is not None and problem.turns <= 1
        )
        if before is None:
            why = "first turn of a new recommendation"
        elif problem.subject != before.subject and not problem.constraints:
            why = "the subject changed, so the earlier constraints do not apply"
        elif len(problem.superseded) > len(before.superseded):
            why = (
                f"new information supersedes {', '.join(problem.retired_values)}"
            )
        elif len(problem.constraints) > len(before.constraints):
            why = "the turn added a constraint to the open problem"
        else:
            why = "nothing new to add; the problem stands"
        print("[Recommendation Reasoning]")
        print(f"  Decision: {decision.mode}")
        print(f"  Why: {why}")
        return problem

    def _resolved_search_query(self, route, goal) -> str:
        """What to search for, once everything established is folded in.

        The open recommendation has the last word, ahead of the router's
        own suggested query. Measured live: three turns had established a
        sore throat and "something easy to eat", and "pull up some spots
        for me" still searched on the router's sentence -- which is built
        fresh each turn and had drifted back to plain restaurants.

        Nothing is overridden when there are no constraints to apply, so an
        ordinary lookup keeps the router's query exactly as before.
        """
        router_query = str(getattr(route, "search_query", "") or "").strip()
        request = str(getattr(route, "normalized_request", "") or "").strip()
        problem = self.task_sessions.active_recommendation()
        focus = self.task_sessions.focus()
        if problem is not None and (
            problem.constraints or problem.lookup_requested
        ):
            resolved = problem.search_query(request or router_query)
            if resolved:
                # Strong task and conversation context comes first. Locale is
                # only a fallback, so it cannot suppress a known destination.
                resolved = self._with_focus(
                    resolved, focus, include_subject=False,
                )
                resolved = self.user_locale.localize_query(
                    resolved,
                    category=problem.category,
                    assume_local=problem.real_world,
                )
            if resolved and resolved.casefold() != router_query.casefold():
                print("[Query]")
                print("  source: active_task")
                print(f"  text: {resolved}")
                return resolved
        return self._with_focus(
            router_query or self._search_subject(route, goal), focus,
        )

    def _with_focus(self, query: str, focus, *, include_subject: bool = True) -> str:
        """Add what the conversation has established to the search.

        Measured live: three turns had settled Seattle and UW, and "which
        apps do people use for rentals there" searched "apps for finding
        rental properties" -- which is the question with every answer to it
        removed. The focus is what "there" meant.
        """
        query = " ".join(str(query or "").split())
        if focus is None or not query:
            return query
        seen = {word.casefold() for word in query.split()}
        # A query that already names somewhere keeps that somewhere.
        # Measured live: a Korean BBQ search in Korea carried "in South
        # Korea" from the locale and "Seattle" from a conversation three
        # topics earlier -- two places, and no answer.
        try:
            already_placed = self.user_locale._names_a_place(query)
        except Exception:
            already_placed = False
        location = focus.background.get("location", "")
        context = focus.query_context()
        if not include_subject and context:
            context = context[1:]
        for part in context:
            words = part.split()
            if any(word.casefold() in seen for word in words):
                continue
            if already_placed and location and part == location:
                continue
            query = f"{query} {part}"
            seen.update(word.casefold() for word in words)
        return query

    def _search_subject(self, route, goal) -> str:
        """What to actually search for, when the words are not searchable.

        A follow-up says "which one would you choose?" and means the thing
        the last turn was about. Searching the sentence itself returns
        whatever the web happens to be comparing -- measured live, a question
        about Seoul hotels came back recommending an Audi. The goal layer
        already resolved the subject; this uses it.
        """
        request = str(getattr(route, "normalized_request", "") or "").strip()
        subject = str(getattr(goal, "subject", "") or "").strip()

        if not subject or subject.casefold() == request.casefold():
            return request
        if not (
            getattr(route, "is_follow_up", False)
            or self._reads_as_followup(request)
        ):
            return request
        # Keep the question, but say what it is about.
        return f"{subject} {request}".strip()

    def _append_recommendation(
        self, reply: str, *, decision, capability, goal,
    ) -> str:
        """Offer something that would help, when offering is worth it.

        Only reached when 4E.2 decided the action would help and the user
        had not asked for it. Level 1 never gets here -- looking something
        up has no visible cost, so it is simply done -- and level 3 keeps
        the approval wall it already has in ``security/``.

        The offer is parked in the same gate every other offer uses, so a
        later "ok" resolves to the action rather than starting a fresh,
        contextless turn.

        Every way of staying quiet says so. Silence and "the code never ran"
        look identical from the outside, and telling them apart by reading
        the source cost a whole debugging pass.
        """
        def quiet(why: str) -> str:
            if str(getattr(decision, "mode", "")) == "recommend":
                # Only worth a line when an offer was actually on the table.
                print(f"[Recommendation] Stayed quiet: {why}.")
            return reply

        text = str(reply or "").strip()
        if not text:
            return quiet("the reply was empty")
        if self.clarification.peek() is not None:
            # She has just asked a question of her own. Adding "want me to
            # search?" underneath it puts two questions on the table and
            # makes the next reply ambiguous -- measured live, the answer
            # to hers was consumed as a "no" to this one.
            return quiet("a question of her own is already outstanding")
        capability_id = str(getattr(capability, "capability", "") or "")
        if capability_id in {"direct_answer", "none", ""}:
            # She answered from what she knew, and the offer is the extra
            # effort on top. Name the ability that would actually provide
            # it: the live browser when it is switched on, a search when it
            # is not.
            state = self._capability_state()
            # Search first, deliberately. Driving the browser is the heavier,
            # more disruptive ability and it needs a real page to go to --
            # offered as the default it produced "Happy to dig into a Dinner
            # if that helps", and accepting it ran browser control on nothing.
            wants_browser = goal_intent.names_a_surface(
                str(getattr(goal, "subject", "") or "")
            )
            preference = (
                ("browser_control", "web_search") if wants_browser
                else ("web_search",)
            )
            capability_id = next(
                (
                    option
                    for option in preference
                    if CapabilityRegistry.is_available(option, state)
                ),
                "",
            )
        if not capability_id:
            return quiet("no ability is available to offer")
        # She may have offered in her own words already.
        if RecommendationPolicy.reads_as_offer(text):
            return quiet("she already offered in her own words")
        # Or a repair guard may have parked one this turn -- the grounded
        # value guard does exactly that when it retracts an unchecked claim.
        # Two offers in one reply is the pushiness this phase exists to
        # avoid, and the second would overwrite the first in the gate.
        if self.capability_offer.peek() is not None:
            return quiet("an offer is already waiting for an answer")

        # Without a distinct topic the goal's subject falls back to the whole
        # utterance, and the offer becomes "Want me to look into i am
        # thinking about getting a new monitor?". Better to say nothing than
        # to say that.
        subject = str(getattr(goal, "subject", "") or "").strip()
        if len(subject.split()) > 6:
            # The router named no topic, so the goal's subject is the whole
            # utterance. Try to name the thing itself before giving up.
            subject = subject_phrase(subject)
        if not subject:
            return quiet("no subject to name")
        if not subject_is_offerable(subject):
            return quiet(f"{subject!r} is about them, not a thing to look up")
        if len(subject.split()) > 6:
            return quiet(f"the subject is a whole sentence ({subject!r})")

        state = self._capability_state()
        if not CapabilityRegistry.is_available(capability_id, state):
            return quiet(f"{capability_id} is not available")
        registered = CapabilityRegistry.get(capability_id)

        offer = self.recommendations.offer(
            decision,
            capability_id=capability_id,
            capability_name=registered.name if registered else capability_id,
            subject=str(getattr(goal, "subject", "") or ""),
        )
        if offer is None:
            return quiet("the cooldown is still running")

        active_problem = self.task_sessions.active_recommendation()
        self.capability_offer.offer(
            capability_id=capability_id,
            goal=offer.goal,
            offer_text=offer.text,
            proactive=True,
            task_id=(active_problem.id if active_problem is not None else ""),
            task_query=(
                active_problem.search_query()
                if active_problem is not None else ""
            ),
        )
        print(f"[Recommendation] Offered {capability_id}: {offer.text}")
        separator = " " if text.endswith((".", "!", "?")) else ". "
        return f"{text}{separator}{offer.text}"

    _DECLINED_LINES = (
        "Okay, I'll leave it.",
        "Sure, no problem.",
        "Alright, forget it then.",
        "No worries, leaving it.",
    )

    def _generic_declined(self) -> str:
        """Acknowledge a refusal, and vary it without losing the meaning.

        The status bank's bare acknowledgements ("Sure.", "Yeah.") read as
        agreement rather than as dropping something, which is the opposite
        of what a refusal deserves.
        """
        recent = getattr(self, "_recent_declines", None)
        if recent is None:
            recent = self._recent_declines = deque(maxlen=2)
        options = [line for line in self._DECLINED_LINES if line not in recent]
        chosen = (options or list(self._DECLINED_LINES))[0]
        recent.append(chosen)
        return chosen

    def _run_browser_capability(self, route, routing, user_input: str):
        """Drive the browser because the capability layer chose it.

        Falls back rather than failing silently: if browser control is
        unavailable or the run does not succeed, the choice is swapped for
        its own recorded fallback so the answering phase can still produce a
        partial answer from a search. Doing nothing was the old behaviour
        and it is the worst of the three.
        """
        state = self._capability_state()
        if not CapabilityRegistry.is_available("browser_control", state):
            reason = CapabilityRegistry.blocked_reason(
                CapabilityRegistry.get("browser_control"), state,
            )
            print(f"[Capability] browser_control unavailable: {reason}.")
            self._fall_back_from(routing, "browser_control")
            return "", None

        print(
            "[Capability] Dispatching browser_control for: "
            f"{route.normalized_request or user_input}"
        )
        try:
            message, result = self._handle_browser_action(
                route,
                approved_action=None,
                original_request=user_input,
                clarified_goal=None,
            )
        except Exception as error:
            print(
                f"[Capability] browser_control raised "
                f"{type(error).__name__}: {error}"
            )
            capability_selection.note_failure(
                self._capability_failures, "browser_control",
            )
            self._fall_back_from(routing, "browser_control")
            return "", None

        failed = result is not None and str(
            getattr(result, "status", "")
        ).endswith("failed")
        goal_text = str(route.normalized_request or user_input or "")
        # "The planner finished" and "the question is answered" are two
        # different claims. Reported live: a clean five-round run whose
        # whole spoken result was "Opened." -- true about the run, and no
        # answer at all to "does the Lotte Hotel have a room on the 18th".
        outcome = browser_outcome.read(
            message,
            succeeded=not failed,
            needs_verification=(
                routing.decision.need == interaction.NEED_VERIFIED
            ),
            goal=goal_text,
        )
        if outcome.verified:
            capability_selection.note_success(
                self._capability_failures, "browser_control",
            )
            return outcome.answer, result

        # Either the browser fell over, or it ran and never reached the
        # answer. Both mean browser control did not deliver this turn, so
        # both count against choosing it again.
        capability_selection.note_failure(
            self._capability_failures, "browser_control",
        )
        if browser_outcome.fallback_can_help(goal_text):
            self._live_check_note = browser_outcome.fallback_notice()
            self._fall_back_from(routing, "browser_control")
            return "", None

        # Nothing a search could honestly add -- a snippet does not know
        # whether one room is free on one night. Say what happened rather
        # than dress a guess up as a check.
        print(
            f"[Capability] browser_control came back {outcome.state}; "
            "no fallback can honestly answer this one."
        )
        if outcome.state == browser_outcome.FAILED:
            return outcome.answer or self._generic_outcome(False), result
        return browser_outcome.unverified_line(goal_text), result

    def _take_live_check_note(self) -> str:
        """Consume the caveat left by a live check that came back empty.

        Read once and cleared, so a later turn that searches for
        something unrelated never inherits a warning about a check it
        never ran.
        """
        note = getattr(self, "_live_check_note", "")
        self._live_check_note = ""
        return f"{note}\n" if note else ""

    def _fall_back_from(self, routing, capability_id: str) -> None:
        """Move this turn onto the next ability the choice already listed.

        The fallback chain is worked out at selection time, so nothing is
        re-decided here -- the turn simply moves down it.
        """
        remaining = [
            option for option in routing.capability.fallbacks
            if option != capability_id
            and not capability_selection.exhausted(
                self._capability_failures, option,
            )
        ]
        if not remaining:
            print(f"[Capability] No fallback left after {capability_id}.")
            return
        nxt = remaining[0]
        print(f"[Capability] Falling back from {capability_id} to {nxt}.")
        routing.capability = replace(
            routing.capability,
            capability=nxt,
            reason=f"{capability_id} could not be used; {nxt} is the fallback",
        )

    def _generic_outcome(self, succeeded: bool) -> str:
        """A done/failed line for when the planner gave no summary of its own.

        Contentless by definition -- there is no subject to name, which is
        why this is not BriefResponseGenerator's job -- and it was the same
        fixed sentence at two different call sites. The status banks already
        hold four ways of saying each, with the same anti-repetition every
        other status line gets.
        """
        return self.action_status.select(StatusContext(
            phase="success" if succeeded else "failure",
            force=True,
        )) or ("That's done." if succeeded else "I couldn't complete that.")

    def _announce_work_status(
        self,
        intent: str,
        user_input: str,
        *,
        confidence: float = 1.0,
    ) -> None:
        """Say one line locally before slow work, or say nothing.

        This used to spend an Ollama round-trip on the sentence whose entire
        job was to cover the wait -- and, whenever that call failed or its
        answer was rejected, fell back to one flat list shared by every kind
        of work. The choice is now local and made from what she is actually
        about to do, so a search no longer sounds like a Git commit.
        """
        action = action_for_intent(intent)
        if action is None:
            return

        text = self.action_status.select(StatusContext(
            action=action,
            phase="execution_started",
            subject=user_input.strip(),
            continuing=is_continuation(intent),
            confidence=confidence,
        ))
        if not text:
            return

        print(f"[Status] Elaina: {text}")
        self.events.emit(
            "assistant_status",
            text=text,
            intent=intent,
        )
        # Said out loud, not only shown on the activity pill. Measured on a
        # real search turn: the answer arrived 9.5 seconds after this line,
        # and every one of those seconds was silent. Speaking costs nothing
        # here -- AudioManager.speak queues onto its worker thread and
        # returns, so the work starts immediately and the real answer simply
        # queues behind this sentence rather than talking over it.
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
        clarified_goal: Goal | None = None,
        assumption: str = "",
    ) -> tuple[str, ComputerActionResult | None]:
        """Return one outcome-locked line and one trusted action result."""
        if route.computer_operation in {"none", "unsupported"}:
            return self.brief_responses.generate(
                "blocked",
                subject=route.action_target,
            ), None

        if not self.computer_control_mode.enabled:
            provider = clarified_goal.value("provider") if clarified_goal else ""
            if provider:
                print("[Execution Selection]")
                print("  Required capability: ui_control")
                print(f"  Preferred provider/source: {provider}")
                print("  Selected: (none)")
                print("  Fallback: (none)")
                print("  Why: Desktop Control Mode is off")
            return self.brief_responses.generate(
                "control_mode_off",
                subject=route.action_target,
                detail=(
                    "Desktop Control Mode is off. Recommend turning on the "
                    "visible Computer Control toggle for this supported action."
                ),
                operation=route.computer_operation,
            ), None

        # ui_action/browser_action are goal-driven and multi-step, not a
        # single resolved target like open_app/delete_file -- their own
        # planners own the whole loop, deciding per-step whether
        # confirmation is needed, so neither goes through prepare()/
        # execute() below.
        if route.computer_operation == "ui_action" or (
            approved_action is not None and approved_action.operation == "ui_action"
        ):
            return self._handle_ui_action(
                route,
                approved_action=approved_action,
                original_request=original_request,
                clarified_goal=clarified_goal,
                assumption=assumption,
            )
        if route.computer_operation == "browser_action" or (
            approved_action is not None
            and approved_action.operation == "browser_action"
        ):
            return self._handle_browser_action(
                route,
                approved_action=approved_action,
                original_request=original_request,
                clarified_goal=clarified_goal,
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
        if result.status in {"file_created", "folder_created"}:
            self._session_items.record(
                name=result.display_name or result.target,
                location=route.computer_location,
                kind="folder" if result.status == "folder_created" else "file",
            )
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
        clarified_goal: Goal | None = None,
        assumption: str = "",
    ) -> tuple[str, ComputerActionResult | None]:
        """Phase 4B.2: goal-driven UI actions (click/type/focus/select/scroll).

        Every step is a real, verified tools.windows_ui_control call, not an
        LLM claim -- so the spoken result here is the planner's own
        tool-grounded summary, never re-paraphrased by brief_responses.
        The one exception is the confirmation *question* itself, which goes
        through brief_responses' "ui_action_offer" kind for the same varied,
        natural phrasing already used for force-quit/delete offers.
        """
        selected_provider = (
            clarified_goal.value("provider")
            if clarified_goal is not None else ""
        )
        # _handle_computer_action normally enforces this first. Keep the
        # boundary here as well because task continuations and integration
        # callers can reach this helper directly; mode-off must never become
        # a back door into the native UI planner.
        if not self.computer_control_mode.enabled:
            if selected_provider:
                print("[Execution Selection]")
                print("  Required capability: ui_control")
                print(f"  Preferred provider/source: {selected_provider}")
                print("  Selected: (none)")
                print("  Fallback: (none)")
                print("  Why: Desktop Control Mode is off")
            return self.brief_responses.generate(
                "control_mode_off",
                subject=route.action_target,
                detail=(
                    "Desktop Control Mode is off. Recommend turning on the "
                    "visible Computer Control toggle for this supported action."
                ),
                operation="ui_action",
            ), None
        if approved_action is not None:
            plan_result = self.desktop_action_planner.resume_confirmed_click(
                window_title=approved_action.window_title,
                control_name=approved_action.display_name,
                window_snapshot=approved_action.window_snapshot,
                element_id=approved_action.ui_element_id,
            )
        else:
            # The router may improve the semantic goal while accidentally
            # dropping deictic scope such as "on this page". Keep both forms:
            # the normalized request tells the planner what to do, while the
            # original wording preserves which foreground surface is allowed.
            normalized_goal = str(route.normalized_request or "").strip()
            original_goal = str(original_request or "").strip()
            planner_goal = normalized_goal or original_goal
            malformed = re.match(
                r"^([a-z]+)\s+\1\b", planner_goal, re.IGNORECASE,
            )
            if malformed and original_goal:
                # Router paraphrases are advisory.  A duplicated leading verb
                # ("Play Play some music") is malformed and can change what a
                # downstream parser/types.  The raw request is the safer input;
                # typed Goal slots remain authoritative when available.
                planner_goal = original_goal
            if (
                original_goal
                and original_goal.casefold() != planner_goal.casefold()
            ):
                planner_goal = (
                    f"{planner_goal}\n"
                    f"Original user request: {original_goal}"
                )
            # Open the interruption window here, not earlier. begin_run
            # both remembers where the user left the pointer and marks the
            # instant after which their input counts as taking it back --
            # scoped to this one task, so input from ten minutes ago cannot
            # abort a run that has only just started.
            self.cursor_driver.begin_run()
            if selected_provider:
                print("[Execution Selection]")
                print("  Required capability: ui_control")
                print(f"  Preferred provider/source: {selected_provider}")
                print(f"  Selected: {selected_provider}")
                print("  Fallback: general desktop planner")
                print("  Why: resolved actionable user preference")
            plan_result = None
            try:
                plan_result = self.desktop_action_planner.act(
                    # An answered question arrives already read into slots,
                    # so the run continues the original request rather than
                    # re-reading a sentence the person never said in full.
                    clarified_goal if clarified_goal is not None else planner_goal,
                    assumption=assumption,
                    surface_context=DesktopSurfaceContext.from_public_snapshot(
                        self._desktop_surface_for_turn()
                    ),
                )
            finally:
                # Do not pull the pointer away after the person physically
                # reclaims it. Normal completion still restores its starting
                # position.
                interrupted = (
                    plan_result is not None
                    and plan_result.status == "interrupted"
                )
                self.cursor_driver.end_run(restore=not interrupted)

            if plan_result.status == "interrupted":
                # Physical user input remains the immediate emergency stop.
                # It is not converted into another permission question; a
                # later explicit command starts immediately like any other.
                done = ", ".join(plan_result.steps_taken[-2:]) or "nothing yet"
                return f"You took control, so I stopped. Completed: {done}", None

        print(
            "[Computer Control] action=ui_action target="
            f"{route.action_target or '(none)'} status={plan_result.status} "
            f"rounds={plan_result.model_rounds} "
            f"action_steps={plan_result.action_steps} "
            f"recovery={plan_result.recovery_used} "
            f"failure={plan_result.failure_code or '(none)'}"
        )
        if selected_provider and plan_result.status not in {"done", "needs_clarification"}:
            print("[Execution Selection]")
            print("  Required capability: ui_control")
            print(f"  Preferred provider/source: {selected_provider}")
            print("  Selected: (failed)")
            print("  Fallback: general desktop planner exhausted")
            print(f"  Why: {plan_result.failure_code or plan_result.status}")

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
                ui_element_id=pending.element_id,
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

        if plan_result.status == "needs_clarification":
            # She understood the request; it just does not name what to act
            # on. A question is the right outcome, not a failed action --
            # nothing was done, so nothing is recorded as having been done.
            # Holding it means the answer continues this request.
            decision = plan_result.clarification
            if decision is not None:
                self.clarification.offer(
                    goal=decision.goal,
                    slot=decision.missing,
                    question=decision.question,
                    template=decision.template,
                )
            return plan_result.summary, None

        succeeded = plan_result.status == "done"
        message = plan_result.summary.strip() or self._generic_outcome(
            succeeded,
        )
        return message, ComputerActionResult(
            status="ui_action_done" if succeeded else "ui_action_failed",
            target=route.action_target,
            display_name=route.action_target,
            message=message,
            operation="ui_action",
        )

    def _handle_browser_action(
        self,
        route: IntentDecision,
        *,
        approved_action: PreparedComputerAction | None,
        original_request: str = "",
        clarified_goal: Goal | None = None,
    ) -> tuple[str, ComputerActionResult | None]:
        """Phase 4C.2: goal-driven webpage actions (click/fill/select/scroll/navigate).

        Mirrors _handle_ui_action exactly: every step is a real, verified
        tools.browser_control call against the live page's own DOM, not an
        LLM claim, so the spoken result is the planner's own tool-grounded
        summary. The confirmation question reuses the same "ui_action_offer"
        brief_responses kind -- "Click 'X'?" reads naturally for a webpage
        element too, so no separate kind is needed.
        """
        if not getattr(self, "browser_page_control_enabled", True):
            return self.brief_responses.generate(
                "blocked",
                subject=route.action_target,
                detail="Browser-page control is disabled in Elaina's configuration.",
                operation="browser_action",
            ), None

        screen_run = getattr(self, "browser_driver", "") == "screen"
        if screen_run:
            self.cursor_driver.begin_run()
        plan_result = None
        try:
            if approved_action is not None:
                if hasattr(self.browser_action_planner, "resume_confirmed_action"):
                    plan_result = self.browser_action_planner.resume_confirmed_action(
                        tab_index=approved_action.tab_index,
                        element_id=approved_action.target,
                        element_label=approved_action.display_name,
                        action=approved_action.browser_action or "click",
                        text=approved_action.browser_text,
                        expected_url=approved_action.url,
                        expected_scan_id=approved_action.browser_scan_id,
                        expected_href=approved_action.browser_href,
                        goal=approved_action.browser_goal,
                        context=self._followup_subject(approved_action.browser_goal),
                    )
                else:
                    # Keeps third-party/test planners written for Phase 4C.1
                    # compatible; production uses the frozen metadata path.
                    plan_result = self.browser_action_planner.resume_confirmed_click(
                        tab_index=approved_action.tab_index or 0,
                        element_id=approved_action.target,
                        element_label=approved_action.display_name,
                    )
            else:
                normalized_goal = str(route.normalized_request or "").strip()
                original_goal = str(original_request or "").strip()
                # The original utterance remains the authoritative browser
                # goal; surface identity comes from the bound live session.
                planner_goal = original_goal or normalized_goal
                plan_result = self.browser_action_planner.act(
                    clarified_goal if clarified_goal is not None else planner_goal,
                    context=self._followup_subject(planner_goal),
                )
        finally:
            if screen_run:
                reclaimed = (
                    plan_result is not None
                    and plan_result.failure_code == "user_took_over"
                )
                self.cursor_driver.end_run(restore=not reclaimed)

        print(
            "[Computer Control] action=browser_action target="
            f"{route.action_target or '(none)'} status={plan_result.status} "
            f"rounds={plan_result.model_rounds} "
            f"failure={plan_result.failure_code or '(none)'}"
        )

        if plan_result.status == "needs_clarification":
            # A booking cannot be researched, let alone made, without the
            # inputs it turns on. Nothing was opened, so nothing is recorded
            # as done -- and the answer continues this request.
            decision = getattr(plan_result, "clarification", None)
            if decision is not None:
                self.clarification.offer(
                    goal=decision.goal,
                    slot=decision.missing,
                    question=decision.question,
                    template=decision.template,
                )
            return plan_result.summary, None

        if plan_result.status == "needs_confirmation":
            pending = plan_result.pending
            prepared = PreparedComputerAction(
                operation="browser_action",
                target=pending.element_id,
                display_name=pending.element_label or pending.element_id,
                tab_index=pending.tab_index,
                url=pending.url,
                browser_action=pending.action,
                browser_text=pending.text,
                browser_scan_id=pending.scan_id,
                browser_href=pending.href,
                browser_goal=pending.goal,
            )
            self.agent_consent.clear()
            self.computer_consent.offer(prepared=prepared, reason=route.reason)
            return self.brief_responses.generate(
                "ui_action_offer",
                # The raw label is a whole search-result block, breadcrumb
                # and all; only display_name below keeps it, because that
                # is what re-verifies the element after confirmation.
                subject=spoken_label(prepared.display_name),
                detail=plan_result.summary,
                operation="browser_action",
            ), ComputerActionResult(
                status="prepared",
                target=pending.element_id,
                display_name=prepared.display_name,
                message=plan_result.summary,
                operation="browser_action",
                prepared=prepared,
            )

        succeeded = plan_result.status == "done"
        message = plan_result.summary.strip() or self._generic_outcome(
            succeeded,
        )
        if not succeeded:
            message = self._spoken_browser_failure(
                plan_result.failure_code, message,
            )

        # Both routes into this handler -- the capability layer's and the
        # router's own computer_action label -- end here, so the reading
        # happens once, here, rather than in whichever branch called in.
        #
        # "status=done" says the planner stopped cleanly. It never said the
        # question was answered, and a goal that asked for something the
        # page knows is owed an answer, not a confirmation. Reported live:
        # a clean five-round run whose entire spoken result was "Opened."
        goal_text = str(
            (approved_action.browser_goal if approved_action is not None else "")
            or original_request
            or route.normalized_request
            or ""
        )
        if wants_information(goal_text):
            outcome = browser_outcome.read(
                message,
                succeeded=succeeded,
                needs_verification=True,
                goal=goal_text,
            )
            print(
                f"[Browser Result] state={outcome.state} ({outcome.reason})"
            )
            if outcome.state == browser_outcome.NOT_VERIFIED:
                # An action report is not an answer, and saying it as one
                # is the dishonest half of this bug.
                message = browser_outcome.unverified_line(goal_text)

        return message, ComputerActionResult(
            status="ui_action_done" if succeeded else "ui_action_failed",
            target=route.action_target,
            display_name=route.action_target,
            message=message,
            operation="browser_action",
        )

    @staticmethod
    def _spoken_browser_failure(failure_code: str, summary: str) -> str:
        """Say what went wrong, not what the planner said to itself.

        Found live: a failed browser step spoke its own internal
        instruction aloud -- "That element was not in the latest live page
        scan. Call describe page before acting." That sentence is addressed
        to the model, not the user, and means nothing to them.
        """
        spoken = {
            "unobserved": (
                "I lost track of that element on the page -- want me to "
                "look again?"
            ),
            "repeated_not_found": "I couldn't find that on the page.",
            "not_found": "I couldn't find that on the page.",
            "stale": "That page changed while I was working on it.",
            "verification_failed": "I tried, but couldn't confirm it worked.",
            "unavailable": "I couldn't reach the browser.",
            "user_took_over": "You took control, so I stopped.",
            "planner_unavailable": "I couldn't reach the browser planner.",
            "missing_tab_identity": (
                "I lost track of which page that was, so I stopped."
            ),
        }.get(str(failure_code or ""), "")
        if spoken:
            return spoken
        # An honest, model-authored failure sentence ("there's no book
        # button on this page") is genuinely useful and stays as-is; only
        # the planner's own tool instructions are replaced above.
        return summary

    def _handle_task_action(
        self,
        route: IntentDecision,
        *,
        approved_task: PendingTaskAction | None,
        approved_strategy_task_state: TaskState | None = None,
        declined_strategy_task_state: TaskState | None = None,
        original_request: str = "",
    ) -> str:
        """Phase 4D-1: multi-step goals composed from existing 4A-4C
        abilities. The task planner only decides which capability and
        sub-goal come next -- every actual step is a real call into the
        proven desktop/browser planners, never a low-level tool itself.
        """
        is_fresh_run = (
            approved_task is None
            and approved_strategy_task_state is None
            and declined_strategy_task_state is None
        )
        screen_run = (
            getattr(self, "browser_driver", "") == "screen"
            or getattr(self, "desktop_driver", "") == "screen"
        )
        if screen_run:
            self.cursor_driver.begin_run()
        task_result = None
        try:
            if approved_task is not None:
                task_result = self.task_planner.resume(
                    approved_task.task_state,
                    approved_action=approved_task.prepared,
                    step=approved_task.step,
                )
            elif approved_strategy_task_state is not None:
                task_result = self.task_planner.continue_with_strategy(
                    approved_strategy_task_state, accepted=True,
                )
            elif declined_strategy_task_state is not None:
                task_result = self.task_planner.continue_with_strategy(
                    declined_strategy_task_state, accepted=False,
                )
            else:
                goal = str(route.normalized_request or original_request).strip()
                followup_context = self.task_sessions.context_for_followup(goal)
                if followup_context is not None:
                    task_result = self.task_planner.run(
                        goal,
                        initial_information=followup_context.information,
                        initial_items=followup_context.items,
                    )
                else:
                    task_result = self.task_planner.run(goal)
        finally:
            if screen_run:
                reclaimed = bool(
                    task_result is not None
                    and any(
                        str(error).endswith(": user_took_over")
                        for error in task_result.task_state.errors
                    )
                )
                self.cursor_driver.end_run(restore=not reclaimed)

        print(
            "[Task Planner] status="
            f"{task_result.status} "
            f"steps={task_result.task_state.step_count} "
            "capability="
            f"{task_result.pending_capability or task_result.task_state.current_capability or '(none)'}"
        )

        if task_result.status == "needs_strategy_choice":
            self.agent_consent.clear()
            self.task_consent.clear()
            self.task_strategy_consent.offer(
                task_state=task_result.task_state, offer_text=task_result.summary,
            )
            return task_result.summary

        # 4D foundation: state the plan before the outcome, only once, on
        # the turn that actually started the task -- a resumed turn's
        # summary is a continuation, not a fresh intent to (re-)announce.
        preview = (
            task_result.task_state.plan_preview
            if is_fresh_run and task_result.status != "capability_unavailable"
            else ""
        )

        if task_result.status == "needs_confirmation":
            self.agent_consent.clear()
            self.task_consent.offer(
                task_state=task_result.task_state,
                step=task_result.pending_step,
                capability=task_result.pending_capability,
                prepared=task_result.pending_prepared,
                reason=task_result.summary,
            )
            return self._prefix_with_preview(preview, task_result.summary)

        if task_result.status == "done" and task_result.task_state.collected_items:
            # Lets a later turn's "book the best one" / "which of those"
            # resolve against this task's own results, via the same
            # single-slot grounded-context mechanism the plain web_search
            # and fact_check paths already use -- no new persisted state.
            capabilities_used = (
                ", ".join(task_result.task_state.required_capabilities)
                or "task"
            )
            self._remember_grounded_fact(
                subject=task_result.task_state.goal,
                statement=task_result.summary,
                source=f"Task: {capabilities_used}",
            )
            self.task_sessions.remember(task_result.task_state)
        elif (
            task_result.status == "stopped"
            and task_result.task_state.collected_items
        ):
            # A bounded stop can still leave a useful, grounded partial
            # shortlist.  Preserve it only for a short conversational
            # follow-up, never as long-term memory.
            self.task_sessions.remember(task_result.task_state)

        return self._prefix_with_preview(
            preview, task_result.summary or "That task is done.",
        )

    @staticmethod
    def _prefix_with_preview(preview: str, summary: str) -> str:
        preview = preview.strip()
        if not preview:
            return summary
        return f"{preview} {summary}".strip()

    def _answer_turn(
        self,
        *,
        route,
        decision,
        capability,
        goal_intent_result,
        recalled_evidence,
        user_input,
        context_prompt,
        locked_response,
        action_performed,
        agent_task_id,
        project_edit_requested,
        screen_context,
        screen_snapshot,
        use_screen_vision,
        turn_cancel,
        turn_started,
        timings,
        forced_response,
    ) -> str:
        """Produce the answer, once the turn has decided what it is.

        The tail of a turn: build the prompt, generate or speak the
        locked result, then filter, remember and publish it. It reads
        the decisions above and returns the reply; nothing after it
        depends on anything it computes, which is what let it move in
        one piece. Thirteen parameters is not elegance -- it is the
        honest size of what this phase still needs to know.
        """
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
                include_grounded=self._grounded_context_is_relevant(
                    route, goal_intent_result,
                ),
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
                    followup_subject=self._followup_subject_for(
                        route, goal_intent_result,
                    ),
                )
        elif route.intent == "time_question":
            messages = self._build_factual_messages(
                route.normalized_request,
                self.build_time_context(),
                reset_history=route.topic_shift,
            )

        turn_grounding_source = ""
        turn_grounding_subject = ""

        # Recall reached far enough: answer from it rather than looking again.
        # The evidence travels the same section a live search would have
        # filled, so the answer is grounded in it identically -- which is what
        # makes the reply actually name the hotels from the previous turn
        # instead of merely declining to search.
        if recalled_evidence and decision.reuses_existing_results:
            messages = self._build_factual_messages(
                route.normalized_request,
                (
                    "Answer from this, which you already found for this "
                    "person earlier in the conversation. Do not claim to "
                    "have looked it up again.\n"
                    f"{recalled_evidence}"
                ),
                include_grounded=self._grounded_context_is_relevant(
                    route, goal_intent_result,
                ),
                reset_history=False,
            )
            turn_grounding_source = "Earlier in this conversation"
            turn_grounding_subject = (
                str(getattr(goal_intent_result, "subject", "") or "")
                or route.topic
                or route.normalized_request
            )

        # Migrated. The intent says a search would answer this; the
        # decision says whether one should still run. A follow-up the
        # session can already answer reaches here with mode=answer, and
        # a second search would return a different set of options from
        # the ones the user is actually choosing between.
        if (
            not use_screen_vision
            and not locked_response
            and decision.acts
            and capability.capability == capability_selection.WEB_SEARCH
        ):
            search_started = time.perf_counter()
            try:
                resolved_query = self._resolved_search_query(
                    route, goal_intent_result,
                )
                research_result = self._research_for_recommendation(
                    resolved_query,
                    resolution=getattr(capability, "execution_preference", None),
                ) or self.research_agent.research(
                    request=route.normalized_request,
                    search_query=resolved_query,
                    max_results=5,
                    verify=route.verification_required,
                )
                self._last_search_query = research_result.queries[0]
                self._last_research_evidence = research_result.evidence
                # Against the open recommendation, so a follow-up can rank
                # what was found instead of searching for it again.
                self.task_sessions.record_candidates(
                    (), evidence=(research_result.evidence,),
                )
                messages = self._build_factual_messages(
                    route.normalized_request,
                    (
                        f"AS-OF DATE: {datetime.now().strftime('%Y-%m-%d')}\n"
                        f"{self._take_live_check_note()}"
                        f"{research_result.evidence}"
                    ),
                    include_grounded=self._grounded_context_is_relevant(
                        route, goal_intent_result,
                    ),
                    # A different recommendation is a different subject.
                    # Measured live: a dinner search answered with the
                    # names of two electric guitars, because the guitar
                    # turns were still in the prompt's history.
                    reset_history=(
                        route.topic_shift or self._recommendation_restarted
                    ),
                    followup_subject=self._followup_subject_for(
                        route, goal_intent_result,
                    ),
                )
                # Keep it, so the next turn can answer from it instead of
                # searching the same thing again.
                self._remember_research(
                    subject=(
                        str(getattr(goal_intent_result, "subject", "") or "")
                        or route.topic
                        or route.normalized_request
                    ),
                    query=self._last_search_query,
                    result=research_result,
                )
                turn_grounding_source = "Current web search"
                turn_grounding_subject = (
                    route.entity
                    or self._active_entity
                    or route.topic
                    or route.normalized_request
                )
                capability_selection.note_success(
                    self._capability_failures, capability.capability,
                )
            except Exception as error:
                # Recorded, not just reported: a search that keeps failing
                # should stop being the first choice.
                capability_selection.note_failure(
                    self._capability_failures, capability.capability,
                )
                fallback = ", ".join(capability.fallbacks) or "(none)"
                print(
                    f"[Capability] {capability.capability} failed "
                    f"({type(error).__name__}); fallback would be {fallback}."
                )
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

        # Whether generation stopped because it ran out of budget rather than
        # because the answer was finished. Ollama reports this on the final
        # chunk and it was being dropped on the floor, so a sentence cut in
        # half went out as speech.
        ran_out_of_budget = False

        def collect_answer() -> str:
            """Collect locally streamed tokens before publishing clean speech."""
            nonlocal ran_out_of_budget
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
                if chunk.get("done") and chunk.get("done_reason") == "length":
                    ran_out_of_budget = True
            return "".join(parts)

        generation_started = time.perf_counter()
        try:
            if effective_forced_response:
                # Verification failures are enforced here instead of asking
                # the vision model to voluntarily avoid a confident guess.
                raw_reply = effective_forced_response
            else:
                raw_reply = collect_answer()
                if ran_out_of_budget:
                    finished = _drop_unfinished_sentence(raw_reply)
                    if finished != raw_reply.rstrip():
                        print(
                            "\n[Response Guard] Generation hit the length "
                            "budget; dropped the unfinished sentence."
                        )
                        raw_reply = finished

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
                    response_language=self.response_language,
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
            if effective_forced_response:
                # A verified tool or planner result never went through the
                # generation-length path above, so this is its only chance
                # to become listenable. The condenser refuses any rewrite
                # that changes a value, so a long result survives intact
                # rather than being trimmed into something wrong.
                reply = self.answer_condenser.condense(
                    reply,
                    max_words=max_words,
                    max_sentences=max_sentences,
                    goal=route.normalized_request or user_input,
                )
            reply = self._enforce_action_commitment(
                reply,
                user_input=user_input,
                action_performed=action_performed,
            )
            reply = self._enforce_grounded_values(
                reply,
                user_input=user_input,
                action_performed=action_performed,
            )
            reply = self._enforce_grounded_entities(
                reply,
                user_input=user_input,
                action_performed=action_performed,
                evidence=self._last_research_evidence,
            )
            # Last, so it also catches a footer a rewrite reintroduced. Her
            # personality file bans these outright and the model adds them
            # anyway, so the removal is code rather than more prompt wording.
            before_strip = reply
            reply = ClosingOfferGuard.strip(
                reply,
                # A repair guard or the ability answer may have just parked
                # an offer whose text is in this reply. Removing it would
                # leave the gate holding an offer the user never saw.
                keep_offers=self.capability_offer.peek() is not None,
            )
            if reply != before_strip:
                removed = before_strip[len(reply):].strip()
                if ClosingOfferGuard.offers_to_act(removed):
                    # Worth counting separately: this is the model making an
                    # offer outside the policy, which is what the policy is
                    # there to bound.
                    print(f"[Recommendation] Removed the model's own offer: "
                          f"{removed[:80]!r}")
            # After the guard, deliberately: a real offer names a capability
            # and a subject, and stripping it as filler would remove the one
            # useful thing this phase adds.
            reply = self._append_recommendation(
                reply, decision=decision, capability=capability,
                goal=goal_intent_result,
            )
            reply = self._final_response_check(
                reply,
                user_input=user_input,
                messages=messages,
                model=active_model,
                temperature=active_temperature,
                num_predict=num_predict,
                keep_alive=active_keep_alive,
                max_words=max_words,
                max_sentences=max_sentences,
                forced=bool(effective_forced_response),
            )
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
            self.memory_enabled
            and route.intent == "conversation"
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

    def _dispatch_turn(
        self,
        *,
        assumed_aloud,
        clarified_goal,
        locked_response,
        memory_text,
        route,
        routing,
        screen_region,
        screen_snapshot,
        timings,
        user_input,
    ) -> dict:
        """Carry out whatever the routing phase decided this turn is.

        Deliberately not a dispatch table: these branches are not
        mutually exclusive alternatives but a sequence of effects --
        one may act, another may set a flag the answering phase reads,
        a third may log. A table here would be a tidier shape than the
        truth. What it does give is one place where all of it lives.
        """
        approved_computer_action = routing.approved_computer_action
        approved_task_action = routing.approved_task_action
        approved_strategy_task_state = routing.approved_strategy_task_state
        declined_strategy_task_state = routing.declined_strategy_task_state
        agent_permission_context = routing.agent_permission_context
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
            # Scenario 1's fast path: a plain web_search never touches the
            # task planner, so it needs its own decision-log call rather
            # than TaskPlanner._preview()'s (which only fires on the
            # task_action path).
            log_information_need(
                intent=route.intent,
                freshness=route.information_freshness,
                verification=route.verification_required,
                effort="discover",
                capabilities=(
                    (routing.capability.capability,)
                    if routing.capability.needs_a_tool else ()
                ),
            )
        if route.normalized_request != user_input:
            print(
                f"[Router] Interpreted transcript as: "
                f"{route.normalized_request}"
            )

        agent_task_id = None
        # Dispatch is decided by the capability, not by the router's label.
        # It used to read `route.intent in AGENT_EXECUTION_INTENTS`, which
        # made "web_search" both the intent and the tool: the answer was
        # fixed before the question was asked. Now the goal decides the need,
        # the need decides the capability, and only then does anything run.
        # Which agent owns the capability stays declarative, in the
        # `intents:` list of each agents/definitions/*.yaml.
        if routing.capability.needs_agent and routing.decision.acts:
            # Looked up by a label that agrees with the capability. Using the
            # router's own label here handed a web_search turn labelled
            # "conversation" to the Conversation Agent.
            assignment_intent = routing.capability.dispatch_label(route.intent)
            if (
                assignment_intent == "calendar_action"
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
            # A fact check with a query really is a search, and should sound
            # like one. Without a query nothing is searched, so it stays
            # unannounced rather than promising a look she is not taking.
            status_intent = (
                "web_search"
                if route.intent == "fact_check" and route.search_query
                else route.intent
            )
            self._announce_work_status(
                status_intent,
                route.normalized_request,
                confidence=float(getattr(route, "confidence", 1.0) or 1.0),
            )

        memory_started = time.perf_counter()
        use_memory = (
            self.memory_enabled
            and route.intent == "conversation"
            and route.memory_relevant
        )
        if use_memory:
            # Search memory for what the turn is *about*. A bare follow-up
            # ("which one would you choose?") carries no subject of its own,
            # so it matched personal memories at random -- a plausible second
            # source of the car recommendation in a conversation about hotels.
            memories = self.memory_manager.search(
                self._search_subject(route, routing.goal_intent) or user_input,
                k=20,
                # Research evidence lives in the same index but answers a
                # different question. Without this, "how has my week been"
                # could come back with a hotel price as a fact about them.
                exclude_categories={memory_categories.RESEARCH_CATEGORY},
            )
            memories = self.memory_ranker.rank(memories)
            memories = self._memories_about(memories, routing.goal_intent)
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
        # Whether a real capability ran this turn. Used by the commitment
        # guard below, which treats "let me open that for you" as a broken
        # promise unless something actually happened.
        # What ran, read from what was chosen to run. This was a hand-kept
        # list of eight router labels that had to stay in step with
        # AGENT_EXECUTION_INTENTS by hand, and did not: entity_correction
        # dispatched a real search and was missing from it.
        action_performed = (
            routing.decision.acts and routing.capability.needs_agent
        )
        # Capability-keyed execution.
        #
        # The browser handler was reachable through exactly one door:
        # route.intent == "computer_action" *and* computer_operation ==
        # "browser_action" -- both the router's labels. So when the
        # capability layer concluded browser_control from a request the
        # router had called web_search ("does the Lotte Hotel have a room on
        # the 18th"), the decision was computed, logged, and then dropped:
        # no agent (browser control is not agent-dispatched), no search (the
        # capability was not web_search), and no handler (the label was not
        # computer_action). Nothing ran at all.
        #
        # 4E.2 made the capability authoritative for *whether* something
        # runs. This makes it authoritative for *what* runs.
        if (
            not locked_response
            and routing.decision.acts
            and routing.capability.capability == capability_selection.BROWSER_CONTROL
            and route.intent != "computer_action"
        ):
            locked_response, computer_result = self._run_browser_capability(
                route, routing, user_input,
            )
            action_performed = bool(locked_response)

        if route.intent == "computer_action" and not locked_response:
            action_performed = True
            locked_response, computer_result = self._handle_computer_action(
                route,
                approved_action=approved_computer_action,
                original_request=user_input,
                clarified_goal=clarified_goal,
                assumption=assumed_aloud,
            )

            if computer_result is not None:
                if (
                    computer_result.succeeded
                    and computer_result.operation in {"open_url", "open_search"}
                    and computer_result.url
                ):
                    # Text-mode requests can briefly focus Elaina's own
                    # Electron window before the next utterance.  Preserve
                    # the Elaina-opened page so terse follow-ups such as
                    # "click Images" remain bound to it instead of an
                    # unrelated background tab.
                    controlled_url = str(
                        getattr(self.browser_connection, "last_opened_url", "")
                        or computer_result.url
                    )
                    self.browser_observer.prefer_page(controlled_url)
                    self._remember_desktop_surface({
                        "kind": "browser",
                        "title": "",
                        "url": controlled_url,
                    })
                self.events.emit(
                    "computer_action_completed",
                    status=computer_result.status,
                    operation=computer_result.operation,
                    target=computer_result.target,
                    message=computer_result.message,
                )
        # Migrated. The label says the planner *could* run this; the
        # capability says whether it should. "Find me some good hotels in
        # Seoul" is labelled task_action and is a plain information
        # request -- running the planner for it started a booking-source
        # workflow for a question about which hotels are good.
        if (
            route.intent == "task_action"
            and routing.capability.capability == capability_selection.TASK_PLANNING
            and not locked_response
        ):
            action_performed = True
            locked_response = self._handle_task_action(
                route,
                approved_task=approved_task_action,
                approved_strategy_task_state=approved_strategy_task_state,
                declined_strategy_task_state=declined_strategy_task_state,
                original_request=user_input,
            )
        screen_target = route.screen_target or "configured"
        if screen_snapshot is not None:
            pass
        elif screen_region is not None:
            screen_snapshot = self.screen_monitor.capture_region(
                screen_region
            )
        elif use_screen_vision:
            precondition_ok, precondition_message = check_precondition(
                "screen_capture_enabled",
                screen_monitor=self.screen_monitor,
            )
            if precondition_ok:
                screen_snapshot = self.screen_monitor.capture_now(
                    screen_target
                )
            else:
                screen_snapshot = None
                forced_response = precondition_message
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
        # A follow-up that means nothing on its own has to be *told* what it
        # is about. The goal layer resolves it, retrieval already uses it --
        # but nothing said it in the prompt, so the model chose between the
        # topics in history by itself and reliably picked the one that came
        # as a list. Measured live: GPUs, then dinner, then "which one would
        # you choose?" answered about graphics cards three times running.
        followup_subject = str(
            getattr(routing.goal_intent, "subject", "") or ""
        ).strip()
        if followup_subject and self._reads_as_followup(
            route.normalized_request or user_input
        ):
            context_prompt += (
                "\n\nWHAT THIS FOLLOW-UP IS ABOUT\n"
                f"{followup_subject}\n"
                "This message refers to the most recent exchange about that "
                "subject. Answer about it, using the options already given "
                "for it. Do not answer about an earlier, unrelated subject "
                "even if it is still in the history above."
            )
        grounded_context = self._grounded_context_text()
        if (
            grounded_context
            and self._grounded_context_is_relevant(route, routing.goal_intent)
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
        if route.intent == "clarification" and route.reason:
            context_prompt += (
                "\n\nCLARIFICATION NEEDED\n"
                f"{route.reason}\n"
                "Ask one short clarifying question instead of guessing or "
                "answering as if a decision had already been made."
            )
        context_prompt += (
            "\n\nCURRENTLY AVAILABLE AI AGENTS\n"
            f"{self._capability_context()}"
        )
        if routing.problem is not None and routing.problem.real_world:
            # Market context belongs to concrete acquisition and discovery,
            # not to every conversation. This preserves local fallbacks for
            # real recommendations without priming greetings or life advice
            # to mention the user's home country.
            context_prompt += (
                "\n\n"
                f"{self.user_locale.context_text(self.response_language)}"
            )
        if agent_permission_context:
            context_prompt += (
                "\n\nAGENT PERMISSION STATE\n"
                f"{agent_permission_context}"
            )

        return {
            "action_performed": action_performed,
            "agent_task_id": agent_task_id,
            "context_prompt": context_prompt,
            "forced_response": forced_response,
            "locked_response": locked_response,
            "project_edit_requested": project_edit_requested,
            "screen_context": screen_context,
            "screen_snapshot": screen_snapshot,
            "use_screen_vision": use_screen_vision,
        }

    def _route_turn(
        self,
        user_input: str,
        *,
        timings: dict,
        screen_region=None,
        screen_snapshot=None,
    ) -> "TurnRouting":
        """Decide what this turn is, before anything acts on it.

        One phase of the turn, lifted out whole: pending answers first,
        then what the request reads as, then the model router for
        whatever could not be read, then the repair layer. It reaches
        the rest of the turn through the ten decisions below and
        nothing else -- which is what made it safe to move.
        """
        route_started = time.perf_counter()
        continuing_agent_flow = bool(
            self.agent_builder.active or self.calendar_agent.active
        )
        has_explicit_attachment = bool(
            screen_region is not None or screen_snapshot is not None
        )
        preference_reply = self._note_preference(user_input)
        if preference_reply:
            # Saying how she should work is a statement, not a request for
            # that work. Measured live: "use Spotify whenever I ask you to
            # play music" was answered "which song would you like me to
            # play?" -- the preference had already been saved, and the
            # media path had already claimed the turn.
            self.clarification.clear()
            self.capability_offer.clear()
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user stated a standing preference.",
                ),
                user_input=user_input,
                locked_response=preference_reply,
            )
        pending_offer = self.agent_consent.peek()
        pending_computer = self.computer_consent.peek()
        pending_task = self.task_consent.peek()
        pending_strategy = self.task_strategy_consent.peek()
        pending_capability = self.capability_offer.peek()
        pending_clarification = (
            self.clarification.peek()
            if hasattr(self, "clarification")
            else None
        )
        active_problem = self.task_sessions.active_recommendation()
        if (
            pending_clarification is not None
            and pending_clarification.goal.kind == "recommendation"
            and pending_clarification.task_id
            and (
                active_problem is None
                or pending_clarification.task_id != active_problem.id
            )
        ):
            print(
                "[Clarification] stale question cleared: owning task is "
                "no longer active"
            )
            self.clarification.clear()
            pending_clarification = None
        if (
            _SIMPLE_GREETING.fullmatch(user_input)
            and not any((
                pending_offer,
                pending_computer,
                pending_task,
                pending_strategy,
                pending_capability,
                pending_clarification,
            ))
            and not has_explicit_attachment
            and not continuing_agent_flow
        ):
            # A greeting is social glue, not an opening to advertise tools or
            # the user's home market. Keep it out of the general model prompt
            # -- but not by pinning it to one sentence, which made every
            # greeting of the session identical. SocialLineSelector keeps the
            # model out of it and still varies the words.
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user offered a simple greeting.",
                    speech_act="greeting",
                ),
                user_input=user_input,
                locked_response=self.social_lines.greeting(user_input),
            )
        if (
            active_problem is not None
            and active_problem.evidence
            and recommendation_state.is_acknowledgement(user_input)
            and not any((
                pending_offer,
                pending_computer,
                pending_task,
                pending_strategy,
                pending_capability,
                pending_clarification,
            ))
            and not has_explicit_attachment
            and not continuing_agent_flow
        ):
            # A reaction to delivered results is conversational closure, not
            # a new recommendation value and not a reason to regenerate the
            # same results through the model. Closure still has to vary,
            # though: this was pinned to "Got it." and said it every time.
            # The acknowledgement bank already exists and already tracks what
            # was said recently, so it is reused rather than copied.
            timings["route"] = time.perf_counter() - route_started
            acknowledgement = self.action_status.select(StatusContext(
                action="checking", phase="acknowledgement", force=True,
            ))
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user acknowledged the delivered task results.",
                    is_follow_up=True,
                ),
                user_input=user_input,
                locked_response=acknowledgement or "Got it.",
                problem=active_problem,
            )
        if (
            _CLOSING_ACKNOWLEDGEMENT.fullmatch(user_input)
            and not has_explicit_attachment
            and not continuing_agent_flow
        ):
            # Do not let a completed or paused hotel/discovery task bleed
            # into a simple social acknowledgement.
            self.clarification.clear()
            self.computer_consent.clear()
            self.task_consent.clear()
            self.task_strategy_consent.clear()
            self.capability_offer.clear()
            self.task_sessions.clear()
            self._grounded_context = {}
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user closed the previous task.",
                ),
                user_input=user_input,
                locked_response="You're welcome.",
            )
        clarified_goal: Goal | None = None
        # What she filled in herself for this turn, to be said out loud with
        # the result rather than silently acted on.
        assumed_aloud = ""
        # Read the request before anything else looks at it. A sentence she
        # can type is a fresh instruction, and it outranks every pending
        # offer except an answer to a question she just asked -- found by
        # the turn suite: an unanswered "want me to use it now?" swallowed
        # every following request and re-offered itself instead.
        tool_preference = self._tool_preference_for(user_input)
        understood = (
            None if has_explicit_attachment or continuing_agent_flow
            else front_door.read(
                user_input,
                recent_subject=(
                    self.desktop_action_planner._recent_media_subject()
                    if hasattr(self.desktop_action_planner, "_recent_media_subject")
                    else ""
                ),
                profile=getattr(self, "user_profile", None),
                media_application=(
                    tool_preference.choice if tool_preference.applied else ""
                ),
            )
        )
        locked_response = ""
        approved_computer_action: PreparedComputerAction | None = None
        approved_task_action: PendingTaskAction | None = None
        approved_strategy_task_state: TaskState | None = None
        declined_strategy_task_state: TaskState | None = None
        resumed_problem_id = ""
        answered_recommendation = False

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

        def route_fresh(transcript: str) -> IntentDecision:
            """Route a genuinely new request, task gate included.

            Every "this reply was about something else entirely" branch
            below used to call route_current directly, which skips
            TaskIntentGate -- so a new multi-step goal arriving while any
            offer happened to be pending silently lost the whole task
            planner. Found live: a pending ability offer turned "what are
            the best second-hand websites to buy a used phone" into a
            one-shot web_search that answered with US marketplaces,
            bypassing the discovery policy that would have named the
            user's own.
            """
            if continuing_agent_flow or has_explicit_attachment:
                return route_current(transcript)
            decision = self.task_intent_gate.check(
                transcript,
                conversation_state=self._build_conversation_state(),
            )
            if not decision.is_multistep:
                return route_current(transcript)
            return IntentDecision(
                intent="task_action",
                confidence=decision.confidence,
                normalized_request=transcript,
                reason=(
                    decision.reason
                    or "This goal needs more than one capability."
                ),
                speech_act="action_request",
                action_requested=True,
                action_target=transcript,
            )

        if (
            pending_clarification is not None
            and pending_clarification.goal.kind == "recommendation"
            and not has_explicit_attachment
            and not continuing_agent_flow
            and not reads_as_new_request(user_input)
            and len(user_input.split()) <= 12
            and recommendation_state.answer_for_dimension(
                pending_clarification.slot, user_input,
            ) is not None
        ):
            # She asked which kind, and this is the answer. It belongs to
            # the open recommendation before any general reading of it gets
            # a say -- measured live, "About 500,000 won" three turns into
            # an electric-guitar search was answered as a currency
            # conversion, and the budget was never recorded as a budget.
            self.clarification.clear()
            route, locked_response = self._answered_dimension(
                pending_clarification, user_input,
            )
            if locked_response:
                timings["route"] = time.perf_counter() - route_started
                return TurnRouting(
                    route=route,
                    user_input=user_input,
                    locked_response=locked_response,
                    problem=self.task_sessions.active_recommendation(),
                )
            answered_recommendation = True
            resumed_problem_id = pending_clarification.task_id
            pending_clarification = None

        elif (
            pending_clarification is not None
            and pending_clarification.goal.kind == "recommendation"
            and not has_explicit_attachment
            and not continuing_agent_flow
            and not reads_as_new_request(user_input)
            and len(user_input.split()) <= 12
        ):
            # An acknowledgement contains no value for the asked dimension.
            # Keep the owned question pending; never turn "yeah" into a
            # typed recommendation constraint.
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="clarification",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The reply did not contain a value for the pending dimension.",
                    is_follow_up=True,
                ),
                user_input=user_input,
                locked_response=pending_clarification.question,
                problem=self.task_sessions.active_recommendation(),
            )

        if answered_recommendation:
            pass
        elif (
            pending_clarification is not None
            and not has_explicit_attachment
            and not continuing_agent_flow
            and pending_clarification.reads_as_answer(user_input)
            and (completed := pending_clarification.completed(user_input))
            is not None
        ):
            # The person answered the question. The answer is folded back
            # into the request that prompted it, so what runs now is the
            # whole request -- through every guard on that path -- rather
            # than a bare fragment routed on its own.
            self.clarification.clear()
            clarified_goal = completed
            route = IntentDecision(
                # "general overview" turns a booking clarification into
                # research.  It needs the task planner's evidence gate, not
                # a direct browser action that still reads like a booking.
                intent=(
                    "task_action" if completed.kind == "research"
                    else "computer_action"
                ),
                # An answered question continues the request it belongs to,
                # which decides which planner sees it.
                computer_operation=(
                    "browser_action"
                    if completed.kind == "booking"
                    else "ui_action"
                ),
                confidence=1.0,
                normalized_request=completed.utterance,
                reason="The user answered the outstanding question.",
                is_follow_up=True,
                speech_act="action_request",
                action_requested=True,
                action_target=completed.utterance,
            )
        elif (
            understood is not None
            and understood.asks
            and not self.agent_builder.active
        ):
            # One gate, on every path. Whatever this request was headed for
            # -- an app, a page, a multi-step task, a search -- it cannot
            # proceed, so the question is asked here rather than by whoever
            # would have received it. Nothing is dispatched.
            self.capability_offer.clear()
            self.clarification.offer(
                goal=understood.decision.goal,
                slot=understood.decision.missing,
                question=understood.question,
                template=understood.decision.template,
            )
            locked_response = understood.question
            route = IntentDecision(
                intent="clarification",
                confidence=1.0,
                normalized_request=understood.target,
                reason="The request cannot proceed until this is answered.",
                speech_act="information_request",
            )
            print(f"[Gate] asked before dispatch: {understood.goal.kind}")
        elif (
            understood is not None
            and understood.operation
            and not self.agent_builder.active
        ):
            # Understood outright: it goes to the planner that owns its gate,
            # with its slots intact and no model call at all.
            clarified_goal = understood.goal
            assumed_aloud = understood.decision.assumption
            self.capability_offer.clear()
            route = IntentDecision(
                intent="computer_action",
                computer_operation=understood.operation,
                confidence=1.0,
                normalized_request=understood.target,
                reason=understood.reason,
                speech_act="action_request",
                action_requested=True,
                action_target=understood.target,
            )
            print(
                f"[Front Door] {understood.goal.kind} -> "
                f"{understood.operation} without the router."
            )
        elif self.agent_builder.active:
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
        elif (
            pending_strategy is not None
            and not has_explicit_attachment
            # A question is not an answer to an offer. Measured live: the
            # offer from "find hotels in guam" swallowed "what is the
            # tallest building in seoul" and replied with the hotel
            # question. A plain "no, the overview is fine" still answers.
            and not asks_something_else(user_input)
        ):
            browser_ready = bool(
                self.browser_page_control_enabled
                and self.computer_control_mode.enabled
            )
            strategy_reply = self.task_discovery_policy.interpret_reply(
                user_input, browser_ready=browser_ready,
            )
            # Clear replies are resolved locally.  This makes the central
            # conversational handoff reliable even if the small local model
            # is offline, and it preserves a reply such as "yes, under
            # ₩200k near Hongdae" as task preferences instead of discarding
            # it under a generic "modify" label.
            if strategy_reply.mode in {"specialized", "overview"}:
                self.task_strategy_consent.clear()
                pending_strategy.task_state.preferences.update(
                    strategy_reply.preferences,
                )
                accepted_strategy = strategy_reply.mode == "specialized"
                if accepted_strategy:
                    approved_strategy_task_state = pending_strategy.task_state
                else:
                    declined_strategy_task_state = pending_strategy.task_state
                route = IntentDecision(
                    intent="task_action",
                    confidence=1.0,
                    normalized_request=pending_strategy.task_state.goal,
                    reason=(
                        "The user selected live specialised research."
                        if accepted_strategy
                        else "The user selected the quick-overview path."
                    ),
                    is_follow_up=True,
                    speech_act="action_request",
                    action_requested=True,
                    action_target=pending_strategy.task_state.goal,
                )
            else:
                consent = self.consent_classifier.classify(
                    user_input,
                    pending_strategy,
                    recent_turns=list(self._router_history),
                )
                if consent.decision == "accept":
                    self.task_strategy_consent.clear()
                    approved_strategy_task_state = pending_strategy.task_state
                    route = IntentDecision(
                        intent="task_action",
                        confidence=consent.confidence,
                        normalized_request=pending_strategy.task_state.goal,
                        reason="The user accepted the live-research offer.",
                        is_follow_up=True,
                        speech_act="action_request",
                        action_requested=True,
                        action_target=pending_strategy.task_state.goal,
                    )
                elif consent.decision == "modify":
                    # A modified strategy reply still authorises the same
                    # task; merge its literal user details into the paused
                    # TaskState and keep the live-research branch.  The old
                    # implementation treated this as a decline and silently
                    # threw away filters.
                    self.task_strategy_consent.clear()
                    preference_text = consent.modified_request or user_input
                    pending_strategy.task_state.preferences.update(
                        self.task_discovery_policy.extract_preferences(
                            preference_text,
                        ),
                    )
                    approved_strategy_task_state = pending_strategy.task_state
                    route = IntentDecision(
                        intent="task_action",
                        confidence=consent.confidence,
                        normalized_request=pending_strategy.task_state.goal,
                        reason="The user updated preferences for live research.",
                        is_follow_up=True,
                        speech_act="action_request",
                        action_requested=True,
                        action_target=pending_strategy.task_state.goal,
                    )
                elif consent.decision == "reject":
                    self.task_strategy_consent.clear()
                    declined_strategy_task_state = pending_strategy.task_state
                    route = IntentDecision(
                        intent="task_action",
                        confidence=consent.confidence,
                        normalized_request=pending_strategy.task_state.goal,
                        reason="The user declined the live-research offer.",
                        is_follow_up=True,
                        speech_act="action_request",
                        action_requested=True,
                        action_target=pending_strategy.task_state.goal,
                    )
                elif consent.decision == "unrelated":
                    self.task_strategy_consent.clear()
                    route = route_fresh(user_input)
                else:
                    route = IntentDecision(
                        intent="conversation",
                        confidence=consent.confidence,
                        normalized_request=user_input,
                        reason="The strategy offer reply was unclear.",
                        is_follow_up=True,
                    )
                    locked_response = (
                        pending_strategy.offer_text
                        or "Would you like live research, or a quick overview?"
                    )
        elif pending_task is not None and not has_explicit_attachment:
            consent = self.consent_classifier.classify(
                user_input,
                pending_task,
                recent_turns=list(self._router_history),
            )
            if consent.decision == "accept":
                self.task_consent.clear()
                approved_task_action = pending_task
                route = IntentDecision(
                    intent="task_action",
                    confidence=consent.confidence,
                    normalized_request=pending_task.request,
                    reason="The user accepted the pending task step.",
                    is_follow_up=True,
                    speech_act="action_request",
                    action_requested=True,
                    action_target=pending_task.request,
                )
            elif consent.decision == "modify":
                # A modified multi-step task is treated as a fresh goal
                # rather than grafting a changed instruction onto in-flight
                # task state -- simpler and safer than partial-state surgery.
                self.task_consent.clear()
                revised_request = consent.modified_request.strip()
                route = route_fresh(revised_request or user_input)
            elif consent.decision == "reject":
                self.task_consent.clear()
                route = IntentDecision(
                    intent="conversation",
                    confidence=consent.confidence,
                    normalized_request=user_input,
                    reason="The user declined the pending task step.",
                    is_follow_up=True,
                )
                gathered = "; ".join(
                    pending_task.task_state.collected_information
                )
                locked_response = (
                    f"Okay, I'll stop there. So far: {gathered}"
                    if gathered
                    else "Okay, I'll stop there."
                )
            elif consent.decision == "unrelated":
                self.task_consent.clear()
                route = route_fresh(user_input)
            else:
                route = IntentDecision(
                    intent="conversation",
                    confidence=consent.confidence,
                    normalized_request=user_input,
                    reason="The pending task confirmation reply was unclear.",
                    is_follow_up=True,
                )
                locked_response = (
                    pending_task.reason or "Should I continue with that step?"
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
                route = route_fresh(
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
                route = route_fresh(user_input)
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
                        if pending_computer.operation in {"ui_action", "browser_action"}
                        else "blocked"
                    ),
                    subject=pending_computer.target_name,
                    detail=pending_computer.request,
                    operation=pending_computer.operation,
                )
        elif (
            pending_capability is not None
            and not has_explicit_attachment
            and not asks_something_else(user_input)
        ):
            # Elaina offered an ability in ordinary conversation ("I can
            # check that in the browser -- want me to?"). Without this
            # branch the user's "ok" routed as a brand-new, contextless
            # turn -- observed live re-emitting the identical offer while
            # nothing ever opened.
            consent = (
                SemanticConsentDecision(
                    decision="accept",
                    confidence=1.0,
                    reason="Clear acceptance of the owned active-task action.",
                )
                if (
                    pending_capability.task_id
                    and reads_as_clear_acceptance(user_input)
                )
                else self.consent_classifier.classify(
                    user_input,
                    pending_capability,
                    recent_turns=list(self._router_history),
                )
            )
            accepted_proactively = (
                consent.decision == "accept"
                and reads_as_clear_acceptance(user_input)
            )
            if pending_capability.proactive and not accepted_proactively:
                # She raised this herself; the person did not ask a question
                # and owes no answer. Measured live: "yeah they are getting
                # expensive" was read as declining a monitor-search offer,
                # and a real conversational turn got a content-free
                # acknowledgement instead of a reply. Anything short of a
                # clear yes simply drops the suggestion and routes normally.
                self.capability_offer.clear()
                if consent.decision == "reject":
                    self.recommendations.note_declined()
                route = route_fresh(user_input)
            elif consent.decision in {"accept", "modify"}:
                self.capability_offer.clear()
                # The offer was welcome, so it costs nothing.
                self.recommendations.note_accepted()
                goal = (
                    consent.modified_request.strip()
                    if consent.decision == "modify"
                    else ""
                ) or pending_capability.goal
                active_problem = self.task_sessions.active_recommendation()
                reuses_task = bool(
                    pending_capability.task_id
                    and active_problem is not None
                    and pending_capability.task_id == active_problem.id
                )
                if reuses_task:
                    goal = (
                        active_problem.search_query()
                        or pending_capability.task_query
                        or goal
                    )
                    operation = ""
                    resumed_problem_id = active_problem.id
                    print("[Consent Resume]")
                    print(f"  task_id: {active_problem.id}")
                    print("  capability: web_search")
                    print("  reused payload: yes")
                    print("  rerouted from acknowledgement: no")
                else:
                    operation = {
                        "browser_control": "browser_action",
                        "ui_control": "ui_action",
                    }.get(pending_capability.capability_id, "")
                route = IntentDecision(
                    intent=(
                        "web_search" if reuses_task
                        else "computer_action" if operation else "task_action"
                    ),
                    confidence=consent.confidence,
                    normalized_request=goal,
                    reason="The user accepted the offered ability.",
                    is_follow_up=True,
                    speech_act="action_request",
                    action_requested=True,
                    action_target=goal,
                    computer_operation=operation,
                    requires_external_evidence=reuses_task,
                    recommendation_needed=reuses_task,
                    search_query=goal if reuses_task else "",
                )
                if not reuses_task:
                    user_input = goal or user_input
            elif consent.decision == "reject":
                self.capability_offer.clear()
                # A refusal is information. Backing off further than an
                # ordinary gap is what stops the next offer reading as
                # nagging rather than helping.
                self.recommendations.note_declined()
                route = IntentDecision(
                    intent="conversation",
                    confidence=consent.confidence,
                    normalized_request=user_input,
                    reason="The user declined the offered ability.",
                    is_follow_up=True,
                )
                locked_response = self._generic_declined()
            elif consent.decision == "unrelated":
                self.capability_offer.clear()
                route = route_fresh(user_input)
            else:
                route = IntentDecision(
                    intent="conversation",
                    confidence=consent.confidence,
                    normalized_request=user_input,
                    reason="The ability offer reply was unclear.",
                    is_follow_up=True,
                )
                locked_response = (
                    pending_capability.offer_text
                    or "Want me to go ahead with that?"
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
                route = route_fresh(user_input)
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
            route = route_fresh(user_input)
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
        if not locked_response:
            before = (route.intent, route.computer_operation)
            route, capability_note = self._rescue_capability_route(
                route, user_input,
            )
            if capability_note or (route.intent, route.computer_operation) != before:
                # Visible on purpose. This layer exists to repair the
                # router's own mistakes, so how often it fires is the
                # measure of whether the router still needs repairing --
                # and the evidence for retiring it when it stops firing.
                print(
                    f"[Rescue] {before[0]}/{before[1] or '-'} -> "
                    f"{route.intent}/{route.computer_operation or '-'}"
                )
            if capability_note:
                locked_response = capability_note
        active_problem = self.task_sessions.active_recommendation()
        if (
            not locked_response
            and active_problem is not None
            and active_problem.lookup_requested
            and not active_problem.missing_dimension()
            and recommendation_state.complains_about_missing_results(user_input)
            and not has_explicit_attachment
            and not continuing_agent_flow
        ):
            # A complaint about work not arriving is control flow for the
            # task already in progress, not a new information request.  The
            # model may label it as conversation (observed live), which used
            # to let capability selection escalate to browser control and
            # hand the literal complaint to the browser.  Resume the owned,
            # already-authorised lookup before goal and capability selection
            # so every downstream layer sees the canonical task payload.
            query = active_problem.search_query()
            route = replace(
                route,
                intent="web_search",
                computer_operation="",
                normalized_request=query,
                topic=active_problem.subject,
                reason=(
                    "The user is asking the active lookup to deliver its "
                    "missing results."
                ),
                is_follow_up=True,
                speech_act="action_request",
                action_requested=True,
                action_target=query,
                requires_external_evidence=True,
                recommendation_needed=True,
                search_query=query,
            )
            resumed_problem_id = active_problem.id
            self.capability_offer.clear()
            print("[Task Resume]")
            print(f"  task_id: {active_problem.id}")
            print("  reason: missing results complaint")
            print("  capability: web_search")
            print("  reused payload: yes")
        # One decision for the whole turn, made from signals that already
        # exist. No model call: this is the last thing routing does, and it
        # only reads what routing already worked out.
        # The whole chain, in order, each step reading only what the one
        # before it concluded. The router's label is translated once, at
        # the top, and never consulted as an intent again.
        goal = goal_intent.read(route)
        # One answer to "what are we talking about", decided here and read
        # everywhere else. An explicit correction outranks the router's
        # topic: measured live, "No, I mean I'm going to UW" was routed
        # correctly and the goal layer still said "moving to Seattle",
        # because its subject came from a field the correction never
        # touched -- and the Seattle answer was given twice.
        focus = self.task_sessions.note_turn(
            user_input, subject=str(getattr(goal, "subject", "") or ""),
        )
        if focus.corrected_to and focus.subject:
            goal = replace(goal, subject=focus.subject)
        has_context, recalled_evidence, recall_origin = self._recall_context(
            route, goal, locked_response=locked_response,
        )
        if has_context:
            print(f"[Recall] Answering from {recall_origin}; no search needed.")
        decision = interaction.decide(
            route,
            goal=goal,
            has_usable_context=has_context,
        )
        problem = self._track_recommendation(
            route,
            goal,
            decision,
            user_input,
            resume_problem_id=resumed_problem_id,
        )
        if problem is not None and self._source_override:
            # The override is read before a new recommendation problem exists.
            # Attach it after opening the problem so a clarification answer
            # stays on the selected surface for the rest of this task.
            self.task_sessions.note_source_override(self._source_override)
            problem = self.task_sessions.active_recommendation()
        if clarified_goal is not None and clarified_goal.value("provider"):
            tool_preference = self._tool_preference_for(
                clarified_goal.utterance, goal=clarified_goal,
            )
        source_preference = (
            self._source_resolution(problem, problem.search_query(user_input))
            if problem is not None and problem.category else None
        )
        execution_preference = (
            tool_preference if tool_preference.applied else source_preference
        )
        if execution_preference is not None and execution_preference.applied:
            print(execution_preference.log_block())
        capability = capability_selection.select(
            goal, decision, route=route, failures=self._capability_failures,
            execution_preference=execution_preference,
        )
        if problem is not None and not locked_response:
            question = self._ask_missing_dimension(problem, user_input)
            if question:
                locked_response = question
        if (
            problem is not None
            and not locked_response
            and problem.constraints
            # Two ways the same turn goes wrong. "Show me some" is read as
            # plain conversation and answered from nothing, or -- measured
            # live -- as a machine action, which then reports that desktop
            # control is switched off. Neither is what was asked for, and
            # both leave a problem with a type and a budget unsearched.
            and capability.capability in {
                capability_selection.DIRECT_ANSWER,
                capability_selection.UI_CONTROL,
            }
            # A named target is a real instruction and is left alone.
            and not str(getattr(route, "action_target", "") or "").strip()
            and recommendation_state.wants_to_see_options(user_input)
        ):
            # "Show me some" three turns into an electric-guitar budget was
            # routed as plain conversation and answered from nothing. The
            # request is an explicit ask for real options, and the problem
            # already holds enough to look them up -- so the same two
            # layers are asked again, this time told evidence is wanted.
            decision, capability = self._reselect_for_options(route, goal)
            print("[Recommendation Reasoning]")
            print(f"  Decision: {decision.mode}")
            print(
                "  Why: the turn asked to see real options and the problem "
                "has enough to look them up"
            )
        if self.intent_router.print_confidence_log:
            print(focus.log_block())
            print(goal.log_block())
            print(decision.log_block())
            print(capability.log_block())
            if problem is not None:
                print(problem.log_block())

        timings["route"] = time.perf_counter() - route_started
        return TurnRouting(
            problem=problem,
            route=route,
            user_input=user_input,
            locked_response=locked_response,
            clarified_goal=clarified_goal,
            assumed_aloud=assumed_aloud,
            approved_computer_action=approved_computer_action,
            approved_task_action=approved_task_action,
            approved_strategy_task_state=approved_strategy_task_state,
            declined_strategy_task_state=declined_strategy_task_state,
            agent_permission_context=agent_permission_context,
            decision=decision,
            goal_intent=goal,
            capability=capability,
            recalled_evidence=recalled_evidence,
        )

    def _recall_context(
        self,
        route: IntentDecision,
        goal,
        *,
        locked_response: str = "",
    ) -> tuple[bool, str, str]:
        """What she already has that answers this, and where it came from.

        The ladder, cheapest first:

        1. the active task's own evidence (TaskSessionStore)
        2. recent research evidence in memory, by resolved subject
        3. conversation history, which is already in every prompt

        Only if none of those hold enough does a search become the answer.
        Levels 1 and 2 are the ones that can be *stated*; level 3 needs no
        retrieval because the history is already there, so reaching it means
        the decision falls through to whatever the router's evidence flags
        say -- which is how an incomplete recall escalates rather than
        answering from nothing.

        Returns ``(has_usable_context, evidence, origin)``.
        """
        if locked_response:
            return False, "", ""
        request = str(route.normalized_request or "").strip()
        if not request:
            return False, "", ""

        # Rung 0: the candidates the open recommendation already found and
        # already checked against the constraints. "Which one would you
        # choose?" is a question about those, and searching again would
        # return a different set from the one being chosen between.
        if DEICTIC_REFERENCE.search(request):
            problem = self.task_sessions.active_recommendation()
            if problem is not None and problem.evidence:
                return (
                    True,
                    "\n".join(problem.evidence),
                    "the options already found for this",
                )

        # A back-reference is what makes recall the right answer. Without one
        # ("what is nvidia trading at"), stored hotel evidence must not be
        # dragged in just because it is recent.
        try:
            session = self.task_sessions.context_for_followup(request)
        except Exception as error:
            print(
                "[Recall] Session lookup failed safely: "
                f"{type(error).__name__}: {error}"
            )
            session = None
        if session is not None:
            lines = list(session.information) + [
                str(getattr(item, "name", "")) for item in session.items
            ]
            evidence = "\n".join(line for line in lines if str(line).strip())
            if evidence.strip():
                return True, evidence, "active task"

        if session is None and not self._reads_as_followup(request):
            return False, "", ""

        subject = str(getattr(goal, "subject", "") or route.topic or "").strip()
        if not subject or not self.memory_enabled:
            return False, "", ""
        try:
            remembered = self.memory_manager.recall_research(subject)
        except Exception as error:
            print(
                "[Recall] Research recall failed safely: "
                f"{type(error).__name__}: {error}"
            )
            return False, "", ""
        if not remembered:
            return False, "", ""

        evidence = "\n\n".join(
            str(getattr(memory, "content", "") or "")
            for memory in remembered
        ).strip()
        if not evidence:
            # Rows existed and carried nothing. Measured live: "[Recall]
            # Answering from recent research; no search needed" printed
            # alongside "Candidates: (none), Evidence: 0 record(s)", and the
            # answer that followed was about restaurants and the App Store.
            print("[Recall] Recalled rows carried no evidence; searching.")
            return False, "", ""
        if not self._evidence_is_about(evidence, subject):
            # Memory search is semantic, so it returns the nearest thing it
            # has rather than nothing. Near is not the same as relevant.
            print(
                f"[Recall] Stored research is not about '{subject}'; "
                "searching instead."
            )
            return False, "", ""
        print(
            f"[Recall] {len(remembered)} record(s) about '{subject}' "
            "attached."
        )
        return True, evidence, "recent research"

    @staticmethod
    def _evidence_is_about(evidence: str, subject: str) -> bool:
        """Whether recalled evidence actually concerns the subject asked about.

        One shared content word is a low bar, and deliberately: the point is
        to reject evidence about a different topic entirely, not to judge
        how well it answers the question.
        """
        words = {
            word for word in re.findall(
                r"[a-z0-9가-힣]{3,}", str(subject or "").casefold(),
            )
        } - {
            "the", "and", "for", "with", "about", "what", "which", "one",
            "some", "any", "you", "your", "near", "there", "here",
        }
        if not words:
            return False
        haystack = str(evidence or "").casefold()
        return any(word in haystack for word in words)

    @staticmethod
    def _reads_as_followup(request: str) -> bool:
        """Whether this sentence only makes sense against a previous one.

        The same test the session store applies, deliberately shared: two
        copies would eventually disagree about what a follow-up is, and the
        recall ladder and the session store would reuse different turns.
        """
        return bool(DEICTIC_REFERENCE.search(str(request)))

    def _remember_research(self, subject: str, query: str, result) -> None:
        """Keep what a search found, through the memory system she already has."""
        if not self.memory_enabled or not str(subject or "").strip():
            return
        try:
            self.memory_manager.remember_research(
                subject=subject,
                query=query,
                evidence=getattr(result, "evidence", ""),
                sources=getattr(result, "queries", ()),
            )
        except Exception as error:
            # Storing evidence must never be able to fail a turn that has
            # already produced a good answer.
            print(
                "[Recall] Could not store research evidence: "
                f"{type(error).__name__}: {error}"
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
        # Cooldowns are counted in turns, not seconds: a conversation that
        # pauses for lunch should not become a licence to start offering
        # again.
        self.recommendations.begin_turn()

        ####################################################
        # Retrieve Memories
        ####################################################

        memory_text = ""

        ####################################################
        # Build Prompt
        ####################################################
        routing = self._route_turn(
            user_input,
            timings=timings,
            screen_region=screen_region,
            screen_snapshot=screen_snapshot,
        )
        route = routing.route
        user_input = routing.user_input
        locked_response = routing.locked_response
        clarified_goal = routing.clarified_goal
        assumed_aloud = routing.assumed_aloud
        decided = self._dispatch_turn(
            assumed_aloud=assumed_aloud,
            clarified_goal=clarified_goal,
            locked_response=locked_response,
            memory_text=memory_text,
            route=route,
            routing=routing,
            screen_region=screen_region,
            screen_snapshot=screen_snapshot,
            timings=timings,
            user_input=user_input,
        )
        action_performed = decided["action_performed"]
        agent_task_id = decided["agent_task_id"]
        context_prompt = decided["context_prompt"]
        forced_response = decided["forced_response"]
        locked_response = decided["locked_response"]
        project_edit_requested = decided["project_edit_requested"]
        screen_context = decided["screen_context"]
        screen_snapshot = decided["screen_snapshot"]
        use_screen_vision = decided["use_screen_vision"]
        return self._answer_turn(
            route=route,
            decision=routing.decision,
            capability=routing.capability,
            goal_intent_result=routing.goal_intent,
            recalled_evidence=routing.recalled_evidence,
            user_input=user_input,
            context_prompt=context_prompt,
            locked_response=locked_response,
            action_performed=action_performed,
            agent_task_id=agent_task_id,
            project_edit_requested=project_edit_requested,
            screen_context=screen_context,
            screen_snapshot=screen_snapshot,
            use_screen_vision=use_screen_vision,
            turn_cancel=turn_cancel,
            turn_started=turn_started,
            timings=timings,
            forced_response=forced_response,
        )

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
        browser_service = getattr(self, "browser_service", None)
        if browser_service is not None:
            browser_service.close()
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
