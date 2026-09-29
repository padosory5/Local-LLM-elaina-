from types import SimpleNamespace
from typing import Any
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import ollama
from collections import deque
from dataclasses import dataclass, field, replace

from memory.memory_manager import MemoryManager
from memory import memory_manager as memory_categories
from memory.extractor import MemoryExtractor
from memory.consolidator import MemoryConsolidator, same_fact
from memory.context_builder import ContextBuilder
from brain.prompt_builder import PromptBuilder
from brain.deliberation import ClarificationGate, Goal
from brain.deliberation import goal_intent, interaction, supersession
from brain.deliberation.goal_intent import SemanticGoal
from brain import capability_selection
from brain import response_stages
from brain import result_state
from brain import response_surface as surfaces
from brain import entity_discovery
from brain import surface_images
from brain import surface_log
from brain import references
from brain import browser_outcome
from brain import browser_navigation
from brain import browser_progress
from brain.browser_interaction import AmbiguousPageAction, BrowserInteraction
from brain import progress_question
from brain import world_clock
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
from core import timing
from core import turn_trace
from core import model_context
from core.event_bus import EventBus
from brain import evidence as turn_evidence
from brain import domain_resolver
from brain import response_budget
from brain.intent_router import NEEDS_CLARIFICATION
from brain.text_filter import TextFilter
from brain import realize
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
from brain import conversation_style
from brain import capability_contract
from brain import existence_claims
from brain import guard_lines
from brain import search_language
from brain import memory_gate
from brain import model_split
from brain import task_progress
from brain import korean_register
from brain import premise_check
from brain import units
from brain import where_we_are
from brain import world_facts
from brain import turn_language
from brain import near_miss
from brain import known_names
from brain import sense_check
from brain import told_not_asked
from brain import activity_commands
from brain import activity_describe
from memory.activity_log import KINDS as ACTIVITY_KINDS, ActivityLog
from memory.routines import RoutineBook
from brain import replay_plan
from brain.replay_plan import PendingReplay, ReplayStep
from brain.replay_runner import ReplayRunner
from tools.screen_control.activity_recorder import ActivityRecorder
from brain.turn_context import TurnContext
from brain.calculation_planner import CalculationPlanner
from tools.calculator import CalculationError, evaluate_expression
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
from brain.action_commitment import (
    OFFER,
    ActionCommitmentGuard,
    ActionLedger,
    offered_action,
    speech_act_of,
)
from brain.recommendation import (
    RecommendationPolicy,
    names_its_own_errand,
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
from brain import social_lines
from brain.social_lines import SocialLineSelector
from brain.answer_condenser import AnswerCondenser
from brain import attribute_values
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
from brain import context_policy
from brain.context_policy import should_include_grounded_context
from brain.brief_response import BriefResponseGenerator
from datetime import datetime
from vision.screen_monitor import ScreenMonitor
from brain.intent_router import IntentDecision, SemanticIntentRouter, reads_as_request
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

# A bare acknowledgement with nothing outstanding. It authorises nothing,
# asks nothing, and names no subject -- there is no classification for the
# router to make, and routing it costs the full prompt (~3,850 tokens,
# measured at ~4.8s) to be told it was conversation. Guarded exactly like
# the greeting path: any pending offer, consent, clarification or active
# recommendation makes these meaningful, and those branches run first.
_BARE_ACKNOWLEDGEMENT = re.compile(
    r"^\s*(?:i see|ok(?:ay)?|got it|gotcha|right|sure|alright|"
    r"mm-?hm+|uh-?huh|yeah|yep|yup|fair enough|makes sense|noted|"
    r"understood|cool|nice|아 그렇구나|알겠어(?:요)?|그렇구나)"
    r"\s*[.!?]*\s*$",
    flags=re.IGNORECASE,
)

# A noise while they think. Not an acknowledgement -- "ok" agrees with
# something, "um" agrees with nothing -- and the difference matters,
# because the reply to an acknowledgement is closure and the reply to a
# hesitation is silence.
#
# Measured live, in the middle of a Korean conversation about the user's
# university:
#
#     said : 엄
#     read : Confirm if computer engineering at Washington University in
#            Seattle is associated with Bill Gates
#     said : 그냥 어이가 없어서 한 표현이야
#
# One syllable of hesitation became a full repeat of the previous
# question, and the user spent the next turn explaining that they had not
# asked anything. The router has nothing to work with here: a filler
# names no subject, so the only thing it can do is reach for the last one.
#
# Guarded exactly like the greeting and acknowledgement paths -- anything
# outstanding makes even a grunt meaningful, and those branches run first.
_HESITATION = re.compile(
    r"^\s*(?:"
    r"u+m+|u+h+|e+r+m?|h+m+|hmm+|ah+|oh+"
    # Korean fillers, in two groups, because one of them is ambiguous.
    # 음/엄/흠 are hesitation and nothing else. A bare "어" or "아" is more
    # often agreement -- "어" is 반말 for "yeah" -- so those count only
    # lengthened, which is how a drawn-out filler actually transcribes.
    # Whisper writes these as it hears them, so the doubled forms are real
    # transcripts rather than defensive padding.
    r"|[음엄흠][음엄어아으]*|[어아으][음엄어아으]+|저기+|그+"
    r")\s*[.!?~,]*\s*$",
    flags=re.IGNORECASE,
)

# Calling it off. A closed class, and the right response is to stop and
# say so -- not to ask a model what kind of request it was.
# A refusal and nothing else, said to an open recommendation. The offer
# gate already handles "no" to a parked offer -- measured live, that path
# answers in 0.18s with the declined bank -- but a recommendation she made
# without parking anything had no such path, and the same word went to the
# model with the film thread still in its history. It came back
# recommending another film, which is the one thing a refusal rules out:
#
#     You:     아니야
#     Elaina:  어제 영화보다는 오늘 영화를 추천드리겠습니다.
#
# Guarded like the bare-acknowledgement path below it: anything pending
# gives the word a different meaning, and those branches run first.
_BARE_REFUSAL = re.compile(
    r"^\s*(?:no|nah|nope|no,?\s*thanks?|not\s+(?:now|really|interested)|"
    r"아니(?:야|요|에요|예요|오)?|아냐|아닙니다|싫어(?:요)?|별로(?:야|요|입니다)?)"
    r"\s*[.!?]*\s*$",
    flags=re.IGNORECASE,
)

_CANCELLATION = re.compile(
    r"^\s*(?:(?:no|nah|actually|wait)[,! ]+)?"
    r"(?:never ?mind|forget\s+(?:about\s+)?(?:it|that)|cancel(?: that| it)?|"
    r"stop(?: that| it)?|drop it|leave it|don't bother|no need|"
    r"취소|됐어(?:요)?|그만)"
    r"\s*[.!?]*\s*$",
    flags=re.IGNORECASE,
)

# A follow-up about a result set that already exists. Measured on a
# dogfooding-shaped workload, these were 3 of 20 turns and every one paid a
# full ~2.2s routing call to be told it was conversation -- which is all
# the router can say about them, because the answer is in the session
# rather than in the sentence.
#
# The closed class is deliberately narrow: comparison, evaluation and
# refinement, none of which authorises anything. "Can you pull it up?" is
# also a follow-up and is deliberately *not* here, because it asks for an
# action and the router has to say so.
#
# Only ever taken while a recommendation is open. With nothing to refer
# back to, "anything cheaper?" is a question about nothing and belongs to
# the router.
_RESULT_FOLLOW_UP = re.compile(
    r"^\s*(?:so\s+|and\s+|ok(?:ay)?[,\s]+|well\s+)?"
    r"(?:"
    # refinement, and nothing after it: "anything cheaper?", "got anything
    # quieter?". The tail is bounded on purpose -- "anything cheaper in
    # Seoul that has parking?" carries a place and a facility, and those
    # have to be read by something that can put them in the problem.
    r"(?:(?:got|is\s+there|are\s+there|do\s+you\s+have)\s+)?"
    r"any(?:thing)?\s+\w+er"
    r"(?:\s+than\s+(?:that|this|it|those|these))?"
    # a choice among what is already there
    r"|which\s+(?:one\s+)?(?:would|do)\s+you\s+"
    r"(?:choose|pick|recommend|go\s+with|prefer)"
    # an opinion on one of them
    r"|is\s+(?:it|that|this)\s+(?:actually\s+|really\s+)?"
    r"(?:any\s+)?(?:good|worth\s+it|better|ok(?:ay)?)"
    # a position in the list
    r"|what\s+about\s+the\s+"
    r"(?:first|second|third|fourth|fifth|last|other)(?:\s+one)?"
    # approval of one of them
    r"|(?:that|this)\s+one\s+(?:sounds|looks|seems)\s+\w+"
    r")"
    r"(?:\s+(?:then|though|really|actually))?"
    r"\s*[.?!]*\s*$",
    flags=re.IGNORECASE,
)

# Asking for one of them to be opened. The position is resolved by
# brain/references.py, which owns the counting vocabulary; this is only the
# other half of the sentence -- the part that says to go there rather than
# to talk about it.
_OPEN_REQUEST = re.compile(
    r"\b(?:open|pull\s+(?:it|that|them|those)?\s*up|bring\s+"
    r"(?:it|that|them|those)?\s*up|show\s+(?:me\s+)?|go\s+to|visit|"
    r"take\s+me\s+to|let'?s\s+see)\b",
    re.IGNORECASE,
)

# A bare greeting needs no model, locale, capability inventory, or service
# pitch. Full-match-only means "hello, can you check Zillow?" still reaches
# the request behind the greeting.
_SIMPLE_GREETING = re.compile(
    r"^\s*(?:hi|hey|hello|hiya|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+elaina)?\s*[!.?]*\s*$"
    # Korean greetings took the model path instead, because this pattern
    # only knew English ones -- so the one turn with a curated,
    # guaranteed-register answer was the one turn Korean never reached.
    # Measured: "안녕" was answered "오늘은 어떻게 지내?", 반말, on the
    # opening line of the conversation. The register converter could not
    # save it either, because it skips questions by design.
    #
    # The bank in brain/social_lines.py has 습니다체 greetings for every
    # part of the day. This is the whole fix: let Korean reach it.
    r"|^\s*(?:안녕|안녕하세요|안녕하십니까|하이|헬로우?|"
    r"좋은\s*(?:아침|오후|저녁))"
    r"(?:\s*엘라이나)?\s*[!.?~]*\s*$",
    flags=re.IGNORECASE,
)

# An imperative or explicit request, as opposed to a remark that merely
# mentions a browser or an app ("I like using Chrome"). Paired with
# CapabilityRegistry.match() so a conversational turn is only ever
# escalated into a real action when the user actually asked for one.
# Her saying she did something, as opposed to talking about it. Past tense
# or a completion word, in the first person.
_CLAIMS_TO_HAVE_ACTED = re.compile(
    r"\bi(?:'ve| have| just)?\s+(?:already\s+)?"
    r"(?:opened|started|launched|turned\s+on|enabled|used|ran|run|"
    r"activated|switched\s+to)\b"
    r"|\b(?:opened|started|launched|enabled|activated)\s+the\b"
    r"|\bis\s+(?:now\s+)?(?:open|running|on|active)\b",
    re.IGNORECASE,
)

# A progressive verb phrase says something is going on right now. After a
# launch, the only ones with evidence behind them are the ones that mean
# "open" -- everything else is a claim about what the application is doing,
# and nothing looked. The exclusion list is the closed set of ways English
# says "it is open", not a list of activities to catch.
_CLAIMS_AN_ACTIVITY = re.compile(
    r"\b(?:is|are|'s|'re|was|were)\s+(?:now\s+|already\s+|currently\s+)?"
    r"(?!open|opened|opening|running|up|on|active|ready|available|"
    r"launched|started|starting|loaded\b)"
    r"[a-z]+ing\b",
    re.IGNORECASE,
)

# Doubting that an ability works. A question about the ability, and never
# a task the ability can be pointed at.
_DOUBTS_AN_ABILITY = re.compile(
    r"\b(?:do\s?n[o']t|does\s?n[o']t|is\s?n[o']t|are\s?n[o']t)\s+"
    r"(?:think\s+|seem\s+)?"
    r"[^.?!]{0,40}\b(?:work|working|works|functioning|available)\b"
    r"|\bis\s+(?:your|the)\s+\w+\s+control\s+(?:working|broken|down|ok)\b"
    r"|\b(?:seems|looks)\s+(?:like\s+)?(?:it'?s\s+)?broken\b",
    re.IGNORECASE,
)

# Something to do once the page is open, as opposed to opening it. The
# navigation operation owns the address; the planner owns the page.
_PAGE_INTERACTION = re.compile(
    r"\b(?:check|read|find|search|look\s+(?:at|for|up)|click|press|fill|"
    r"type|scroll|compare|book|buy|order|summari[sz]e|tell\s+me|show\s+me|"
    r"see\s+what|what\s+it\s+says)\b",
    re.IGNORECASE,
)

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
from brain.resolved_turn import ResolvedTurn, command_was_fused
from brain import standing_orders
from brain.standing_orders import StandingOrders
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
    resolved: ResolvedTurn | None = None

    def __post_init__(self):
        if self.resolved is None:
            self.resolved = ResolvedTurn(
                raw_transcript=self.user_input,
                normalized_transcript=self.route.normalized_request,
                intent=self.route.intent, subject=self.goal_intent.subject,
                machine_target=self.route.computer_url or self.route.action_target,
                search_query=self.route.search_query, task=self.problem,
                confidence=self.route.confidence,
            )


class ChatEngine:

    # Class-level so a partially-constructed engine still has a language.
    # Several tests build one without running __init__, and the language
    # is now read from deep inside guards that those tests do exercise --
    # a missing attribute there would be an AttributeError in a grounding
    # guard, which is the worst place to discover one.
    _configured_language = "en"
    _turn_language = "en"
    _pinned_language = ""


    def __init__(self, config: Config | None = None):
        # The one seam a test needs: a turn suite builds the real engine
        # with heavy, side-effectful features switched off in a copy of the
        # configuration, rather than reconstructing half of it by hand.
        self.config = config if config is not None else Config()

        # ELAINA_MODEL overrides config.yaml for the same reason as
        # ELAINA_CONVERSATION_MODEL below: an evaluation arm can run another
        # model without touching the file the person's Elaina reads.
        self.model = os.environ.get("ELAINA_MODEL") or self.config.get(
            "llm",
            "ollama",
            "model",
        )

        # The model that says things, which need not be the model that
        # decides things.
        #
        # Measured, same session, same matrices: a 27B scored 83% on the
        # Korean dogfood arc where this model scores 58%, and 83% against
        # 75% on the English one -- and produced two dangerous false
        # positives on the router matrix, reading "Disable Smart App
        # Control" as an action to carry out and a remark about email as
        # a calendar entry. It also needed 13 JSON repair retries over 134
        # routes where this model needed none.
        #
        # Language competence and structured reliability came apart
        # cleanly, so they get different models. Routing, consent,
        # planning, tool selection and extraction stay here; only the
        # words the person hears move.
        #
        # Empty is the previous behaviour exactly: one model for both.
        # ELAINA_CONVERSATION_MODEL overrides config.yaml, so an evaluation
        # can put a different model on the words without editing the file
        # the person's own Elaina reads.
        self.conversation_model = str(
            os.environ.get("ELAINA_CONVERSATION_MODEL")
            or self.config.get(
                "llm", "ollama", "conversation_model",
                default="", required=False,
            ) or ""
        ).strip() or self.model

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

        # What happened in each turn, from the draft to what was said, is
        # written to runtime/turn_trace/ (core/turn_trace.py). The client is
        # wrapped so every model call a turn makes lands in its record; the
        # wrapper is a pass-through, and every component below receives it.
        turn_trace.configure(
            enabled=self.config.get(
                "debug", "turn_trace", default=True, required=False,
            ),
            retention_days=self.config.get(
                "debug", "turn_trace_retention_days",
                default=30, required=False,
            ),
        )
        # Every call asks for one context window large enough for the
        # router's prompt and answer (core/model_context.py).
        self.client = turn_trace.TracingClient(model_context.ContextSizedClient(
            ollama.Client(
                host=self.config.get(
                    "llm",
                    "ollama",
                    "base_url",
                )
            ),
            model_context.configured(self.config),
        ))

        # Say out loud what is configured, and whether it can work. A
        # split whose two models do not fit together is not a little
        # slower -- Ollama evicts and reloads one every turn, which was
        # measured at about 24s. Silence would leave that to be found as
        # mysterious slowness weeks later.
        try:
            note = model_split.report(
                self.client, self.model, self.conversation_model,
            )
            if note:
                print(note)
        except Exception as error:
            print(f"[Models] Could not check the model split: {error}")
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
        # A word that is almost what the conversation is about, asked about
        # and waiting for its answer (brain/near_miss.py); the pairs the
        # person has said are two different things; and what she took a
        # misheard name to be this turn, to be said with the answer.
        self._pending_slip: near_miss.PendingSlip | None = None
        self._distinct_terms: set[frozenset] = set()
        self._slip_assumed = ""
        # Slips the person confirmed ("CBT? -- yes, CPT"), so the same one is
        # corrected and said rather than asked about again; and the one she
        # assumed on the turn before, which a "no, CBT" can still undo.
        self._known_slips: dict[str, str] = {}
        self._last_assumed_slip: near_miss.Slip | None = None
        # Set for the one turn a corrected question runs on, so it is looked
        # up rather than answered from memory.
        self._search_the_correction = False
        # Place names and acronyms from what the person has told her about
        # themselves, read from memory at most every ten minutes.
        self._memory_names: tuple[str, ...] | None = None
        self._memory_names_at = 0.0
        # A clip heard too unclearly to answer, and whether she asked to hear
        # the last one again -- so she never asks twice in a row.
        self._heard_unclearly = False
        self._asked_to_repeat = False
        self._asked_last_turn = False
        self._spoken_word_average = 0.0
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

        # The configured language is now a *starting* language, not the
        # language. Which one a turn is answered in is decided per turn by
        # brain/turn_language.py, from the script the person actually wrote
        # in -- so someone who speaks two languages is answered in the one
        # they just used.
        self._configured_language = str(self.config.get(
            "language",
            "response",
        )).strip().lower()
        self._turn_language = self._configured_language
        # A language the user explicitly asked for ("영어로 말해줘", or the
        # switch in the window). It outranks detection until it is changed.
        self._pinned_language = ""

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
        # The person's own standing rules, and the facts they have asked
        # her to keep. Two hand-editable files that outlive a restart --
        # see brain/standing_orders.py for why a directive is executed
        # rather than read out to the model.
        self.standing_orders = StandingOrders.load()
        print(self.standing_orders.log_block())
        self.input_watcher = InputWatcher()
        watching = self.input_watcher.start()
        # What the person does on the machine: an activity log they can ask
        # about, and recordings they start by voice. Built on the same hooks
        # that tell their input from hers -- see
        # tools/screen_control/activity_recorder.py.
        self.routines = RoutineBook.load()
        self._pending_replay = None
        self._last_listed_steps = []
        self.activity_log = None
        self.activity_recorder = None
        if self.config.get("activity", "enabled", default=True, required=False):
            try:
                self.activity_log = ActivityLog(retention_days=float(self.config.get(
                    "activity", "retention_days", default=14, required=False,
                )))
                self.activity_recorder = ActivityRecorder(
                    watcher=self.input_watcher,
                    log=self.activity_log,
                    typed_text=str(self.config.get(
                        "activity", "typed_text", default="recordings_only",
                        required=False,
                    )),
                    skip_apps=tuple(self.config.get(
                        "activity", "skip_apps", default=[], required=False,
                    ) or ()),
                )
                recording = self.activity_recorder.start()
            except Exception as error:
                print(f"[Activity] could not start: {type(error).__name__}: {error}")
                recording = False
            print(f"[Activity] {'on' if recording else 'off'}"
                  + (f" ({self.activity_recorder.error})"
                     if self.activity_recorder is not None
                     and self.activity_recorder.error else ""))
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
        # Pictures for the cards. Held here rather than called
        # directly so the network boundary is one a test can tie,
        # exactly like the structured search above it.
        self.illustrate_surface = surface_images.illustrate
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
            # The same token generation and speech already honour. Without
            # it a cancelled multi-step task kept dispatching browser and UI
            # actions to the end.
            is_cancelled=self._turn_is_cancelled,
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
        # What is actually true about this turn's action, which is what
        # every "I'll check" and "want me to?" is now checked against. It
        # reads the offer gate above rather than copying it: two records of
        # the same fact is two things that can disagree.
        self.action_ledger = ActionLedger(
            pending_offer=self.capability_offer.peek,
        )
        # Whether this turn's reply carries an invitation she may keep
        # without parking anything. Reset with the ledger, for the same
        # reason: it describes this turn and nothing beyond it.
        self._invitation_stands = False
        # What this turn does to anything outstanding. Read once per turn
        # by the routing phase, and reported by the interaction decision.
        self._supersedes = supersession.Supersession()
        self._last_interaction = interaction.InteractionDecision()
        # Speaks. "Anytime." and "Got it." are words the person hears.
        self.brief_responses = BriefResponseGenerator(
            self.client,
            self.conversation_model,
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
        # Set for one turn when a structured browser result is the answer.
        self._browser_result_is_final = False
        # What she last actually did on the machine, so a complaint about
        # it ("you're showing me nothing") reaches that surface again
        # instead of being read as a fresh, unsupported request.
        self._last_computer_action = ""
        self._last_computer_goal = ""
        # The page interaction now standing: what was asked for, on which
        # page, and what became of it. A retry repeats this, never the
        # transcript of whatever was said in between.
        self._browser_interaction: BrowserInteraction | None = None
        # A page action that matched several elements and is waiting on a
        # choice. Choosing one is not a new command -- it fills the slot
        # this action is missing, and the action then runs.
        self._page_choice: AmbiguousPageAction | None = None
        # Whether that action ended in a failure the person can ask to have
        # tried again. "Try again" and a bare "yeah" after she asks "try
        # again?" both need one recorded action to operate on rather than
        # a target rebuilt out of the conversation each time.
        self._last_action_failed = False
        # Whether the machine target has already survived one drifting
        # turn. A second one retires it.
        self._machine_target_reprieved = False
        self._turn_points_at_the_last_action = False
        # The last navigation and the addresses it has been through. The
        # history is what makes an honest recovery possible: a candidate
        # the conversation supplied rather than one nobody mentioned.
        self._navigation = None
        self._navigation_history: tuple[str, ...] = ()
        # What the browser was showing when a navigation was dispatched.
        self._navigation_before: tuple[tuple[str, str], ...] = ()
        # Set when this turn opened a different recommendation, so the
        # previous one's turns do not stay in the answering prompt.
        self._recommendation_restarted = False
        # Named by the person for this turn only. Never written to the
        # profile: "use Google Maps for this one" must not erase a
        # standing preference for Naver Maps.
        self._source_override = ""
        self._tool_override = ""
        # Speaks: it rewrites a long answer into something listenable,
        # so its output is read aloud verbatim.
        self.answer_condenser = AnswerCondenser(
            self.client,
            self.conversation_model,
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

    def _retire_pending_interpretations(self):
        for gate in (self.agent_consent, self.computer_consent, self.task_consent,
                     self.task_strategy_consent, self.capability_offer, self.clarification):
            gate.clear()
        self._pending_slip = None

    def _recent_messages(
        self, *, exclude: str = "", limit: int = 12,
        roles: tuple[str, ...] = ("user", "assistant"),
    ) -> list[str]:
        """The last turns of the conversation, this one excluded."""
        conversation = getattr(self, "conversation", None)
        try:
            history = list(conversation.get_history()) if conversation else []
        except Exception:
            history = []
        if (
            history and exclude
            and history[-1].get("role") == "user"
            and str(history[-1].get("content", "") or "").strip() == exclude.strip()
        ):
            history = history[:-1]
        return [
            str(item.get("content", "") or "")
            for item in history[-limit:]
            if item.get("role") in roles
        ]

    def listening_terms(self, *, exclude: str = "") -> tuple[str, ...]:
        """What the conversation is about, as the names and acronyms in it.

        Read by the near-miss guard, and handed to the transcriber before
        each listen, so the words it is most likely to mishear are the ones
        it is listening for.
        """
        extra = list(dict.fromkeys(getattr(self, "_entity_aliases", {}).values()))
        try:
            problem = self.task_sessions.active_recommendation()
        except Exception:
            problem = None
        if problem is not None and getattr(problem, "subject", ""):
            extra.append(problem.subject)
        extra.extend(self._names_from_memory())
        return near_miss.held_terms(
            self._recent_messages(exclude=exclude),
            extra=extra,
            said_by_them=self._recent_messages(exclude=exclude, roles=("user",)),
        )

    def _asks_to_hear_it_again(self, user_input: str) -> "TurnRouting | None":
        """A clip the transcriber could barely decode is asked about, once.

        Measured with the harvested mishearings: "인산부한테 인산비날 가는
        것이 있죠" -- noise that decoded into words nobody said -- was
        answered "인산비날은 인산부한테 가는 것이 있습니다", as if it made
        sense. The transcriber knows when it is guessing
        (voice/transcription_policy.heard_unclearly), and a person would say
        they did not catch it.

        Never twice in a row: asked once and still unclear, the best reading
        is answered rather than asking again. A question she was waiting on
        is left in place, so the repeated answer still reaches it.
        """
        # Read once per turn, here at the top of routing: whether she asked
        # on the turn before. This check and the sense check further down
        # (_does_not_make_sense) share it, so between them she never asks
        # twice in a row.
        self._asked_last_turn = bool(getattr(self, "_asked_to_repeat", False))
        self._asked_to_repeat = False
        if not getattr(self, "_heard_unclearly", False):
            return None
        if self._reads_as_her_own_command(user_input):
            print("[STT] Word confidence was low, but it reads as one of her "
                  "own commands or answers; answering it.")
            return None
        return self._ask_to_hear_it_again(
            user_input,
            why="heard too unclearly to answer",
            line=guard_lines.say("didnt_catch", self._turn_language),
        )

    def _does_not_make_sense(self, user_input: str) -> "TurnRouting | None":
        """A doubtful spoken transcript whose words do not fit together is
        asked about, once.

        The transcriber can be sure of words nobody said: "해수에서 슬프
        당선까지 버스있어" scored no lower than a correct "간단한 걸로" in the
        same noise, so the confidence check above cannot see it. Reading
        the sentence can (brain/sense_check.py). Run after the near-miss
        repair, so a slip it can settle ("빈천공항" -> 인천공항, "CLT" ->
        시애틀?) is settled rather than asked about from scratch, and never
        on a typed turn or a clearly heard one.
        """
        if self._reads_as_her_own_command(user_input):
            return None
        average = float(getattr(self, "_spoken_word_average", 0.0) or 0.0)
        below = float(self.config.get(
            "stt", "faster_whisper", "sense_check_below",
            default=sense_check.READ_BELOW, required=False,
        ))
        if not sense_check.worth_reading(user_input, average, below=below):
            return None
        # The repair already recognised what they meant, or is waiting on
        # their answer to its question.
        if self._slip_assumed or self._pending_slip is not None:
            return None
        her_lines = self._recent_messages(
            exclude=user_input, limit=2, roles=("assistant",),
        )
        started = time.perf_counter()
        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": sense_check.PROMPT},
                    {"role": "user", "content": sense_check.message(
                        user_input,
                        her_last_line=her_lines[-1] if her_lines else "",
                    )},
                ],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 16},
                keep_alive=self.keep_alive,
                think=False,
            )
            content = self._value(self._value(response, "message", {}), "content", "")
        except Exception as error:
            # Never loses the turn: it is answered as heard.
            print(f"[Sense Check] Failed; answering as heard: "
                  f"{type(error).__name__}: {error}")
            return None
        garbled = sense_check.reads_as_garbled(content)
        print(f"[Sense Check] {'garbled' if garbled else 'sense'} "
              f"({time.perf_counter() - started:.2f}s, word average "
              f"{average:.2f}): {user_input!r}")
        if not garbled:
            return None
        return self._ask_to_hear_it_again(
            user_input,
            why="its words do not fit together",
            line=guard_lines.say("heard_as_nonsense", self._turn_language).format(
                heard=sense_check.quoted(user_input),
            ),
        )

    def _reads_as_her_own_command(self, user_input: str) -> bool:
        """A transcript that parses as one of her deterministic commands --
        or as a yes/no to the question she just asked -- was understood,
        whatever the transcriber's word confidence. Live, "Record this" and
        "Stop Recording" were heard exactly right at an average of 0.59 and
        0.40, and she asked to hear them again, twice."""
        if activity_commands.read(user_input) is not None:
            return True
        if getattr(self, "_pending_replay", None) is not None:
            return bool(activity_commands.read_consent(user_input))
        return False

    def _ask_to_hear_it_again(
        self, user_input: str, *, why: str, line: str,
    ) -> "TurnRouting | None":
        if self._asked_last_turn:
            print(f"[STT] {why.capitalize()}, but she asked once already; "
                  "answering the best reading.")
            return None
        self._asked_to_repeat = True
        print(f"[STT] Asking to hear it again ({why}): {user_input!r}")
        return TurnRouting(
            route=IntentDecision(
                intent="conversation",
                confidence=1.0,
                normalized_request=user_input,
                reason=f"The transcript was not usable: {why}.",
            ),
            user_input=user_input,
            locked_response=line,
        )

    def _activity_turn(self, said: str) -> "TurnRouting | None":
        """What the person did on the computer: asked about, repeated, or
        made the start-up routine.

        A repeat is never done on the turn that asks for it: the steps are
        listed and she asks, and only a yes on the next turn does anything
        (``_pending_replay``). Any other answer lets the offer lapse --
        the conversation simply goes on.
        """
        pending = getattr(self, "_pending_replay", None)
        if pending is not None:
            self._pending_replay = None
            if not pending.expired:
                answer = activity_commands.read_consent(said)
                if answer == "yes":
                    return self._activity_routing(said, "replay", self._do_replay(pending))
                if answer == "no":
                    return self._activity_routing(
                        said, "declined",
                        guard_lines.say("replay_declined", self._turn_language),
                    )
        command = activity_commands.read(said)
        if command is None:
            return None
        line = self._activity_reply(command)
        if not line:
            return None
        return self._activity_routing(said, command.kind, line)

    def _activity_routing(self, said: str, kind: str, line: str) -> "TurnRouting":
        print(f"[Activity] {kind}: {line[:160]!r}")
        return TurnRouting(
            route=IntentDecision(
                intent="conversation",
                confidence=1.0,
                normalized_request=said,
                reason=f"Activity: {kind}.",
            ),
            user_input=said,
            locked_response=line,
        )

    def _activity_reply(self, command) -> str:
        language = self._turn_language

        def say(key: str, **values) -> str:
            return guard_lines.say(key, language).format(**values)

        recorder = getattr(self, "activity_recorder", None)
        log = getattr(self, "activity_log", None)
        kind = command.kind

        if kind == "replay_last":
            plan = replay_plan.build(self._recent_for_replay())
            if not plan.steps:
                return say("replay_nothing")
            count = command.count or self._last_burst(plan)
            chosen = replay_plan.last(plan, count)
            n = len(chosen.steps)
            if language == "ko":
                what = "방금 하신 동작" if n == 1 else f"최근 동작 {n}개"
            else:
                what = "Your last action" if n == 1 else f"Your last {n} actions"
            return self._offer_replay(chosen, "replay_offer", what=what)

        if kind in ("recall_boot", "replay_boot"):
            found = self._boot_plan()
            if found is None:
                return say("boot_actions_none")
            plan, key, at = found
            return self._offer_replay(
                plan, key, time=activity_describe.clock(at),
            )

        if kind == "save_startup":
            steps = list(getattr(self, "_last_listed_steps", None) or ())
            if not steps:
                found = self._boot_plan()
                steps = list(found[0].steps) if found is not None else []
            if not steps:
                return say("startup_nothing")
            self.routines.save_plan(
                "startup", [step.as_dict() for step in steps], said=command.kind,
            )
            return say("startup_saved",
                       steps=replay_plan.steps_text(steps, language))

        if kind == "forget_startup":
            self.routines.forget("startup")
            return say("startup_forgotten")

        if kind in ("pause", "resume"):
            if recorder is None:
                return say("activity_unavailable")
            recorder.paused = kind == "pause"
            return say("activity_paused" if kind == "pause" else "activity_resumed")

        if kind == "recall":
            if log is None:
                return say("activity_unavailable")
            kinds = command.kinds or tuple(k for k in ACTIVITY_KINDS if k != "start")
            rows = [
                row for row in log.recent(
                    since=command.since, until=command.until, kinds=kinds,
                    containing=command.about, limit=300,
                )
                if not activity_describe.is_shell(row)
            ]
            when = activity_describe.when_phrase(command.when, language)
            if not rows:
                return say("activity_recall_empty", when=when)
            items, more = activity_describe.timeline_text(rows, language, limit=10)
            line = say("activity_recall", when=when, items=items)
            if more:
                line += say("activity_recall_more", more=more)
            return line

        if kind == "forget_activity":
            if log is None:
                return say("activity_unavailable")
            if recorder is not None:
                recorder.recent.clear()
            if command.everything:
                return say("activity_forgotten_all", count=log.forget(everything=True))
            count = log.forget(since=command.since, until=command.until)
            return say(
                "activity_forgotten", count=count,
                when=activity_describe.when_phrase(command.when, language),
            )
        return ""

    # ----------------------------------------------------------- repeating

    def _can_replay(self) -> bool:
        return bool(getattr(self.computer_control, "enabled", False))

    def _offer_replay(self, plan, key: str, **values) -> str:
        language = self._turn_language
        if not self._can_replay():
            return guard_lines.say("replay_unavailable", language)
        self._pending_replay = PendingReplay(list(plan.steps))
        self._last_listed_steps = list(plan.steps)
        line = guard_lines.say(key, language).format(
            steps=replay_plan.steps_text(plan.steps, language), **values,
        )
        if plan.skipped:
            line += guard_lines.say("replay_skipped", language).format(
                skipped=replay_plan.skipped_text(plan.skipped, language),
            )
        return line

    def _recent_for_replay(self) -> list:
        recorder = getattr(self, "activity_recorder", None)
        recent = recorder.recent_activities() if recorder is not None else []
        if recent:
            return recent
        log = getattr(self, "activity_log", None)
        if log is None:
            return []
        return log.recent(since=time.time() - 3 * 3600, limit=300)

    @staticmethod
    def _last_burst(plan) -> int:
        """"What I just did": the steps since the last pause of two minutes,
        at most eight."""
        steps = plan.steps
        count = 1
        for later, earlier in zip(reversed(steps), list(reversed(steps))[1:]):
            if later.at and earlier.at and later.at - earlier.at > 120:
                break
            count += 1
        return max(1, min(count, 8, len(steps)))

    def _boot_plan(self):
        """What they did first after the PC started: (plan, line key, time)."""
        log = getattr(self, "activity_log", None)
        if log is None:
            return None
        try:
            import psutil

            booted = float(psutil.boot_time())
        except Exception:
            return None
        rows = log.recent(since=booted, limit=2000)
        if not rows:
            return None
        first = rows[0].at
        plan = replay_plan.build([row for row in rows if row.at <= first + 600])
        plan.steps = plan.steps[:8]
        if not plan.steps:
            return None
        if first - booted > 300:
            return plan, "boot_actions_late", first
        return plan, "boot_actions", booted

    def _replay_runner(self) -> ReplayRunner:
        return ReplayRunner(
            computer_control=self.computer_control,
            desktop_control=self.desktop_action_planner.control,
            browser_service=self.browser_service,
            observer=self.computer_control.ui_observer,
            input_watcher=self.input_watcher,
        )

    def _do_replay(self, pending) -> str:
        """Do the steps they said yes to. Desktop Control Mode is switched on
        for this run only if it was off -- the yes to a listed plan is the
        consent -- and put back afterwards."""
        language = self._turn_language

        def say(key: str, **values) -> str:
            return guard_lines.say(key, language).format(**values)

        if not self._can_replay():
            return say("replay_unavailable")
        was_on = bool(self.computer_control_mode.enabled)
        if not was_on and not self.set_computer_control_mode(True):
            return say("replay_unavailable")
        try:
            outcome = self._replay_runner().run(pending.steps)
        finally:
            if not was_on:
                self.set_computer_control_mode(False)
        total = len(pending.steps)
        print(f"[Replay] {len(outcome.done)}/{total} done"
              + (f"; stopped at {outcome.failed.action} {outcome.failed.target!r}: "
                 f"{outcome.reason}" if outcome.failed is not None else ""))
        if outcome.finished:
            return say("replay_done",
                       steps=replay_plan.steps_text(outcome.done, language))
        if outcome.interrupted:
            return say("replay_interrupted", done=len(outcome.done), total=total)
        return say(
            "replay_failed", done=len(outcome.done), total=total,
            step=replay_plan.step_text(outcome.failed, language),
            reason=outcome.reason if language != "ko" else "",
        ).strip()

    def notice_where_we_are(self) -> str:
        """At start-up: say so if the machine moved since she was last on.

        Called once by main.py, beside the routine offer, and a question in
        exactly the same way -- where someone shops is not settled by which
        zone their laptop is in. Measured on this machine: the clock was
        Pacific, Windows still said 한국, and she went on pricing things in
        won with nothing to tell the person why (brain/where_we_are.py).
        """
        from datetime import datetime

        from core.paths import DATA_DIRECTORY

        path = DATA_DIRECTORY / "where_we_are.json"
        clock = datetime.now().astimezone()
        offset = clock.utcoffset()
        context = getattr(self.user_locale, "context", None)
        here = where_we_are.Place(
            country=str(getattr(context, "home", "") or ""),
            currency=str(getattr(context, "currency", "") or ""),
            timezone=str(clock.tzname() or ""),
            offset_hours=(offset.total_seconds() / 3600.0) if offset else 0.0,
        )
        before = where_we_are.read(path)
        where_we_are.write(path, here)
        if not where_we_are.moved(before, here):
            return ""
        language = getattr(self, "_turn_language", "") or self.response_language
        line = where_we_are.sentence(before, here, language)
        print(f"[Locale] The clock moved: {before.timezone!r} -> "
              f"{here.timezone!r}; asking about it.")
        self.events.emit("assistant_started")
        self.events.emit("assistant_finished", text=line)
        try:
            self.audio.speak(line)
        except Exception as error:
            print(f"[Locale] could not say it: {error}")
        return line

    def offer_startup_routine(self) -> str:
        """At start-up: offer the routine they asked for, and wait for a yes.

        Called once by main.py when she is ready. Never runs anything by
        itself -- the answer arrives as an ordinary turn.
        """
        steps = [
            ReplayStep.from_dict(step) for step in self.routines.plan("startup")
        ]
        if not steps or not self._can_replay():
            return ""
        language = getattr(self, "_turn_language", "") or self.response_language
        self._pending_replay = PendingReplay(steps, seconds=600.0)
        self._last_listed_steps = steps
        line = guard_lines.say("startup_offer", language).format(
            steps=replay_plan.steps_text(steps, language),
        )
        print(f"[Activity] start-up offer: {line[:160]!r}")
        self.events.emit("assistant_started")
        self.events.emit("assistant_finished", text=line)
        try:
            self.audio.speak(line)
        except Exception as error:
            print(f"[Activity] could not say the start-up offer: {error}")
        return line

    def _names_from_memory(self) -> tuple[str, ...]:
        """Place names and acronyms from what they have told her before.

        Measured live: "인천공항에서 미국 시애틀까지" was heard as "... 미국
        CLT까지" in a conversation that had not mentioned Seattle -- but the
        person had told her long before that they study at UW in Seattle.
        What she already knows about them is part of what she should be
        listening for. Only names: the place list's own names (an English
        memory's "Seattle" becomes 시애틀) and acronyms, never an ordinary
        word from a remembered sentence.
        """
        now = time.monotonic()
        if self._memory_names is not None and now - self._memory_names_at < 600:
            return self._memory_names
        names: list[str] = []
        if getattr(self, "memory_enabled", False):
            try:
                found = self.memory_manager.search(
                    "where the person lives, studies, works and travels",
                    k=20,
                    exclude_categories={memory_categories.RESEARCH_CATEGORY},
                )
            except Exception as error:
                print(f"[Near Miss] Could not read names from memory: "
                      f"{type(error).__name__}")
                found = []
            for memory in found or ():
                text = str(getattr(memory, "content", "") or "")
                names.extend(known_names.korean_for(text))
                names.extend(
                    korean for korean in known_names.korean_names()
                    if len(korean) >= 3 and korean in text
                )
                names.extend(near_miss.acronyms_in(text))
        self._memory_names = tuple(dict.fromkeys(names))
        self._memory_names_at = now
        return self._memory_names

    def _near_miss_turn(self, user_input: str) -> "TurnRouting | str":
        """Settle a word that is almost what the conversation is about.

        The person has been asking about CPT and says "CBT"; the transcriber
        hears "배인브리지" for the "베인브리지" the conversation has been
        using. An acronym is asked about -- one letter is the whole
        difference, and only they know which they meant -- and the answer
        to that question is read here on the next turn. A longer name is
        taken as the held one, and she says so with the answer.
        """
        pending, self._pending_slip = self._pending_slip, None
        assumed, self._last_assumed_slip = self._last_assumed_slip, None
        if pending is not None and not pending.expired:
            slip = pending.slip
            meaning = near_miss.read_answer(user_input, slip)
            if meaning == near_miss.MEANT:
                print(f"[Near Miss] Confirmed {slip.meant!r}, not {slip.heard!r}.")
                self._known_slips[near_miss.slip_key(slip)] = slip.meant
                return self._corrected_turn(slip.corrected)
            if meaning == near_miss.HEARD:
                print(f"[Near Miss] {slip.heard!r} was meant; "
                      "not asking about it again.")
                self._distinct_terms.add(near_miss.settled_pair(slip))
                return slip.said
            print("[Near Miss] The reply is a new turn; the question is dropped.")
        if (
            assumed is not None
            and near_miss.names_the_heard(user_input, assumed)
            and near_miss.read_answer(user_input, assumed) == near_miss.HEARD
        ):
            # "아니 CBT 맞아" right after she took it as CPT: undone, and not
            # taken that way again this session.
            print(f"[Near Miss] {assumed.heard!r} was meant after all; not "
                  f"taking it as {assumed.meant!r} again.")
            self._known_slips.pop(near_miss.slip_key(assumed), None)
            self._distinct_terms.add(near_miss.settled_pair(assumed))
            return user_input
        known = near_miss.known_slip(user_input, self._known_slips)
        if known is not None:
            print(known.log_line())
            self._last_assumed_slip = known
            self._slip_assumed = guard_lines.say(
                "slip_assumed", self._turn_language,
            ).format(meant=known.meant)
            return known.corrected
        terms = self.listening_terms(exclude=user_input)
        seen = " ".join(self._recent_messages(exclude=user_input))
        places = known_names.korean_names()
        # Every name taken first -- "빈천공항에서 미국 CLT까지" has two, and
        # the question about one should not leave the other uncorrected --
        # then at most one question about what is left.
        text, assumed_lines, last = user_input, [], None
        for _ in range(3):
            slip = near_miss.find(
                text, terms, seen=seen, distinct=self._distinct_terms,
                known=places, only="assume",
            )
            if slip is None:
                break
            print(slip.log_line())
            assumed_lines.append(guard_lines.say(
                "slip_assumed", self._turn_language,
            ).format(meant=slip.meant))
            last, text = slip, slip.corrected
        slip = near_miss.find(
            text, terms, seen=seen, distinct=self._distinct_terms,
            known=places, only="ask",
        )
        if slip is not None:
            print(slip.log_line())
            # One outstanding question at a time, across every kind.
            self._retire_pending_interpretations()
            self._pending_slip = near_miss.PendingSlip(slip)
            question = guard_lines.say(
                "slip_question", self._turn_language,
            ).format(
                heard=slip.heard, meant=slip.meant,
                quote=near_miss.quoted_particle(slip.heard),
            )
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=text,
                    reason=(
                        "The turn names a near-miss of a term the "
                        "conversation holds."
                    ),
                ),
                user_input=text,
                locked_response=" ".join([*assumed_lines, question]),
            )
        if not assumed_lines:
            return user_input
        self._slip_assumed = " ".join(assumed_lines)
        self._last_assumed_slip = last
        return text

    def _corrected_turn(self, corrected: str) -> "TurnRouting | str":
        """The request as they meant it -- and looked up, when it asks.

        The person asked for this outright: once the right term is settled,
        what they wanted to know is searched, not answered from what she
        half-remembers. Measured in the English demo: "yes" after "did you
        mean CPT?" was answered from memory, and said CPT goes through "your
        immigration office" -- it goes through the school.
        """
        self._search_the_correction = reads_as_request(corrected)
        return corrected

    def _retire_machine_target(self):
        self._last_computer_action = ""
        self._last_computer_goal = ""
        self._browser_interaction = None
        self._page_choice = None
        self._last_action_failed = False
        self._machine_target_reprieved = False
        self._navigation = None
        self._navigation_history = ()

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
        if not text:
            return ""
        # Doubting that an ability works is a question about it, even
        # though it is shaped like a statement. Measured live, the
        # session-11 rerun: "I don't think your browser control is working
        # right now" became a browser goal, the planner failed on it
        # because a complaint is not a goal, and she concluded from that
        # failure that she has no browser at all.
        if not CapabilityRegistry.is_ability_question(text) and not (
            _DOUBTS_AN_ABILITY.search(text)
        ):
            return ""

        if _ABILITY_INVENTORY_QUESTION.search(text):
            return CapabilityRegistry.inventory_sentence(state)

        match = CapabilityRegistry.match(text)
        # The set used to be browser_control and ui_control only. Measured
        # live once the three new surfaces were registered:
        #
        #     You said: can you commit changes to git for me?
        #     Elaina:   I can't commit changes to Git right now.
        #
        # She had not answered the question -- she had *run* the git
        # action, which correctly reported nothing staged, and rendered
        # that as an inability. A question about an ability is answered
        # from the registry for every ability in it.
        if not match.matched or match.capability.id not in {
            "browser_control", "ui_control",
            "git", "project_edit", "agent_building",
        }:
            return ""

        capability = match.capability
        language = self._turn_language
        # Every sentence below used to be an English f-string. The path is
        # deterministic on purpose -- it answers from the registry so the
        # model cannot deny an ability she has -- and a deterministic
        # answer in the wrong language is exactly as reliable, in English.
        blocked = CapabilityRegistry.blocked_reason(capability, state)
        if blocked:
            fix = CapabilityRegistry.fix_for(capability, state)
            answer = guard_lines.say("ability_blocked", language).format(
                name=capability.name_in(language), reason=blocked,
            )
            if not fix:
                return answer
            return answer + " " + guard_lines.say(
                "ability_blocked_fix", language,
            ).format(fix=_sentence_case(fix))

        # An offer needs something to do. Measured live, the session-11
        # rerun:
        #
        #     You said: I don't think your browser control is working.
        #     Elaina: I can use browser control for this -- want me to?
        #     You said: Yep.
        #     [browser_action target=I don't think your browser control
        #      is working right now.]  -> failed
        #     Elaina: I'm unable to access or control a browser.
        #
        # The complaint was handed to the planner as the goal, it failed
        # because it is not a goal, and she concluded from that that she
        # has no browser. A doubt about an ability is a question about it,
        # not a task to run.
        if _DOUBTS_AN_ABILITY.search(text):
            print(f"[Ability] Answered a doubt about {capability.id}.")
            return guard_lines.say("ability_doubted", language).format(
                name=capability.name_in(language),
                ability=capability.spoken_summary_in(language),
            )
        offer = guard_lines.say("ability_offer", language)
        self.capability_offer.offer(
            capability_id=capability.id, goal=text, offer_text=offer,
        )
        print(f"[Ability] Answered from the registry for {capability.id}.")
        return guard_lines.say("ability_yes", language).format(
            ability=capability.spoken_summary_in(language), offer=offer,
        )

    # ------------------------------------------------------------ language
    #
    # Both of these were plain attributes set once in __init__, read at a
    # dozen call sites. Making them properties over ``_turn_language`` is
    # what let the language become a per-turn decision without touching any
    # of those call sites: they ask the same question and now get an answer
    # about this turn instead of about the config file.

    @property
    def response_language(self) -> str:
        """The language this turn is being answered in."""
        return self._turn_language

    @property
    def system_prompt(self) -> str:
        """Who she is, written in the language of this turn."""
        return self.personality_loader.load(self._turn_language)

    def _use_language(self, language: str) -> None:
        """Answer in this language from now until it changes.

        Most of the system reads ``response_language`` when it needs it, so
        it needs no telling. These four hold their own copy because they
        were built once with language-specific line banks or prompts, and a
        bank chosen at construction cannot follow a conversation.
        """
        language = str(language or "").strip().lower()
        if not language or language == self._turn_language:
            return
        self._turn_language = language
        for component in (
            self.action_status,
            self.social_lines,
            self.recommendations,
        ):
            speak_in = getattr(component, "speak_in", None)
            if callable(speak_in):
                speak_in(language)
        planner = getattr(self, "desktop_action_planner", None)
        if planner is not None:
            planner.response_language = language
        # And the audio boundary, which is the one that matters most: it
        # decides whether the reply is spoken at all.
        audio = getattr(self, "audio", None)
        if audio is not None and hasattr(audio, "speak_in"):
            audio.speak_in(language)

    def _decide_turn_language(
        self, said: str, *, spoken_language: str = "",
        spoken_confidence: float = 0.0,
    ) -> turn_language.LanguageDecision:
        """Which language to answer this turn in, and remember the answer."""
        decision = turn_language.decide(
            said,
            current=self._turn_language,
            pinned=self._pinned_language,
            detected=spoken_language,
            probability=spoken_confidence,
        )
        if decision.pinned:
            self._pinned_language = decision.language
        if decision.switched or decision.pinned:
            print(decision.log_line())
        self._use_language(decision.language)
        return decision

    def _things_she_has_said(self) -> tuple[str, tuple[str, ...]]:
        """The last thing she said, and everything before it this session.

        Both, because they catch different faults. The adjacent reply
        catches a draft that echoes the turn before; the rest of the
        session catches a bank line coming back around, which is invisible
        turn-to-turn and obvious across a conversation.
        """
        # Read defensively. Guards call this on every reply now -- a
        # disclaimer is said once, a question is not asked twice in a row --
        # and an engine without a conversation (a test's bare instance, or
        # one still being built) must mean "nothing said yet", never an
        # exception in the middle of producing a reply.
        conversation = getattr(self, "conversation", None)
        try:
            history = conversation.get_history() if conversation else []
        except Exception:
            history = []
        spoken = [
            str(item.get("content", "") or "")
            for item in history
            if item.get("role") == "assistant"
            and str(item.get("content", "") or "").strip()
        ]
        if not spoken:
            return "", ()
        return spoken[-1], tuple(spoken[:-1])

    @staticmethod
    def _keeps_the_facts(candidate: str, original: str) -> bool:
        """Whether a re-said reply still says everything the draft did.

        The whole safety argument for letting a model reword a tool result
        rests here, so it is deliberately two-sided and deliberately dumb.
        Every number in the draft must survive, and no number may appear
        that was not there -- an invented value and a dropped one are the
        same defect wearing different clothes, and the condenser's own
        one-sided subset test would have caught only half of it.

        Capitalised words are checked the same way, because that is where
        the names live: a report that quietly stops mentioning Notepad, or
        starts mentioning Chrome, is not a rewording.
        """
        if not candidate.strip():
            return False

        def numbers(text: str) -> set[str]:
            return {
                token.strip(".,")
                for token in re.findall(r"\d[\d,.:]*", str(text))
                if token.strip(".,")
            }

        def names(text: str, *, skip_sentence_openers: bool) -> set[str]:
            found: set[str] = set()
            for sentence in re.split(r"(?<=[.!?])\s+", str(text).strip()):
                words = re.findall(r"[^\W\d_][\w'-]*", sentence)
                start = 1 if skip_sentence_openers else 0
                for word in words[start:]:
                    if len(word) > 2 and word[0].isupper():
                        found.add(word)
            return found

        if numbers(candidate) != numbers(original):
            return False
        # Asymmetric on purpose. What the draft capitalised mid-sentence is
        # a name; what the rewrite capitalised anywhere might be, because a
        # rewrite is allowed to move a name to the front of a sentence. The
        # comparison errs towards keeping the original.
        return names(original, skip_sentence_openers=True).issubset(
            names(candidate, skip_sentence_openers=False)
        )

    def _holds_a_parked_offer(self, text: str) -> bool:
        """Whether this draft carries the offer the gate is waiting on.

        The style pass runs before the guards that park one, so in practice
        it is looking at the model's own offers. In practice is not a rule:
        removing a parked offer would leave the gate holding a question
        nobody was asked, which is the failure ``_one_offer_per_reply``
        exists to prevent, so the same check is made here.
        """
        try:
            pending = self.capability_offer.peek()
        except Exception:
            return False
        if pending is None:
            return False
        parked = " ".join(str(pending.offer_text or "").split())
        return bool(parked) and parked in " ".join(str(text or "").split())

    def _say_it_in_her_voice(
        self,
        reply: str,
        *,
        act: str,
        user_input: str,
        model: str,
        keep_alive,
        max_words: int,
    ) -> str:
        """Repair what is damaged, and say again what sounds like a machine.

        The one place a finished reply is judged on *how it sounds*, and the
        reason every path now sounds like the same person. Before it, a
        greeting came from a variety selector, a tool outcome came from a
        planner and a plain answer came from personality.txt -- three
        authors, and the seams were audible the moment a conversation
        crossed between them.

        Two mechanisms, and the split between them is the point:

        **Structural damage is repaired, silently and always.** A leaked
        ``[id=6e663719-e0]``, an unpaired quote, a snake_case identifier
        that reached speech because it was appended after the speech filter
        had already run. No rewording of those is the correct version.

        **Register is re-said, never edited.** A customer-service sentence
        cut out of a reply leaves a shorter customer-service reply, so the
        model is asked for the same content in her own voice instead. That
        costs one call and only happens when the review actually failed,
        which on measured conversation is a minority of turns.

        The locked acts are exempt from the second mechanism entirely. A
        clarification question and a consent question are classified
        against their exact words by ``SemanticConsentClassifier``, and a
        reworded question is a different question -- so those are repaired,
        reported, and left alone.
        """
        draft = str(reply or "")
        if not draft.strip():
            return draft
        # A guard's own line is what the guard decided to say, word for
        # word. Said twice it is still true; re-said by the model it was
        # not ("Sorry, I didn't catch that" came back "Sure. Stop
        # recording.").
        if guard_lines.is_fixed_line(draft):
            return draft

        # Fix the register mechanically before judging it. Measured over
        # three live runs, roughly half her Korean sentences came back
        # 해요체, the prompt did not move it, and asking for the line again
        # mostly produced 해요체 again -- once 반말, which is further from
        # the register than what it replaced. What is left after this pass
        # is what genuinely needs saying differently.
        # Keyed to what she actually wrote, not to what the turn decided.
        # "안녕" is two syllables and stays under the switch threshold, so
        # the turn stayed English -- and the model answered in Korean 반말
        # anyway, with no Korean guard running over it. The register of a
        # Korean sentence is a fact about the sentence.
        spoken_language = (
            "ko" if any("\uac00" <= ch <= "\ud7a3" for ch in draft)
            else self._turn_language
        )
        if spoken_language.startswith("ko"):
            formal = korean_register.to_formal(draft)
            if formal != draft:
                print("[Style] Converted to 습니다체.")
                draft = formal

        contract = conversation_style.contract_for(act)
        previous, earlier = self._things_she_has_said()
        verdict = conversation_style.review(
            draft,
            act=act,
            user_input=user_input,
            previous_reply=previous,
            earlier_replies=earlier,
            language=spoken_language,
        )
        repaired = verdict.repaired
        if repaired != draft:
            print(f"[Style] repaired structure: {verdict.report()[:120]}")
        if verdict.clean:
            return repaired
        if not response_stages.soft_stages_on():
            # This stage is MIXED (brain/response_stages.py). With the soft
            # stages off, its soft half -- cutting to the act's length,
            # taking offers out, saying it again -- is skipped; the register
            # conversion and structural repair above are the hard half and
            # have already run.
            return repaired
        print(f"[Style] {act}: {verdict.report()}")

        if not verdict.needs_rewrite:
            return repaired
        if not contract.may_reword:
            # Said out loud rather than swallowed: a locked act that keeps
            # failing the review is a defect in whoever writes that
            # question, and it should be visible in the log.
            print(f"[Style] {act} is locked; left as written.")
            return repaired

        # Too long and nothing else, on an act that carries no facts: cut
        # rather than re-said. The re-say mostly came back just as long (see
        # conversation_style.cut_to_length), and a cut costs no call.
        if all(
            finding.failure == conversation_style.TOO_VERBOSE
            for finding in verdict.findings
        ):
            cut = self._cut_to_the_acts_length(
                repaired, act=act, user_input=user_input,
                previous=previous, earlier=earlier, language=spoken_language,
            )
            if cut != repaired:
                return cut

        # An act that may not offer, offering anyway. Deterministic and
        # first: this is the one failure whose repair needs no new
        # sentence, only the removal of one, and the re-say measured live
        # came back offering again on a plain refusal.
        if all(
            finding.failure == conversation_style.DUPLICATE_OFFER
            for finding in verdict.findings
        ) and not self._holds_a_parked_offer(repaired):
            without = conversation_style.without_offers(repaired, act)
            if without != repaired:
                print(f"[Style] An offer where a {act} allows none; "
                      f"took it out.")
                return without

        said_again = self._resay(
            repaired,
            act=act,
            user_input=user_input,
            model=model,
            keep_alive=keep_alive,
            max_words=max_words,
            faults=verdict.findings,
            already_said=(previous, *earlier),
        )
        if spoken_language.startswith("ko"):
            said_again = korean_register.to_formal(said_again)
        if not said_again:
            return repaired
        # A draft that repeats the previous answer has no facts of its own
        # to keep: its numbers belong to the question before this one.
        # Measured live -- "콜드브루 만드는 법 알아?" was answered with the
        # London time from two turns earlier, the style layer caught the
        # repetition, and the faithfulness check then vetoed the rewrite
        # for dropping "16:35". It was protecting the wrong answer.
        stale = any(
            finding.failure == conversation_style.SELF_REPETITION
            for finding in verdict.findings
        )
        if not stale and not self._keeps_the_facts(said_again, repaired):
            print("[Style] The re-said version changed a value or dropped a "
                  "name; kept the original.")
            return repaired

        second = conversation_style.review(
            said_again, act=act, user_input=user_input,
            previous_reply=previous, earlier_replies=earlier,
            language=spoken_language,
        )
        if second.needs_rewrite:
            # One attempt only. A second call would usually return the same
            # register, and a reply that is merely late is worse than one
            # that is merely stiff.
            #
            # But "not perfect" is not "not better", and treating them as
            # the same had a cost. Measured live: four consecutive
            # acknowledgements all came out "i'm here...", because each
            # rewrite still tripped the opener check and each was therefore
            # discarded in favour of the exact repeat it was offered to
            # replace. The saturated history then swallowed the next real
            # question -- "what's 2+2" was answered "You had a rough night,
            # but I'm here." So take whichever reads better, and only fall
            # back when the rewrite is genuinely no improvement.
            if len(second.findings) < len(verdict.findings):
                print(f"[Style] The re-said version still reads as "
                      f"{', '.join(second.classes)}, but less so; taking it.")
                shorter = self._cut_to_the_acts_length(
                    second.repaired, act=act, user_input=user_input,
                    previous=previous, earlier=earlier,
                    language=spoken_language,
                )
                if self._holds_a_parked_offer(shorter):
                    return shorter
                return conversation_style.without_offers(shorter, act)
            print(f"[Style] The re-said version still reads as "
                  f"{', '.join(second.classes)}; kept the original.")
            shorter = self._cut_to_the_acts_length(
                repaired, act=act, user_input=user_input,
                previous=previous, earlier=earlier,
                language=spoken_language,
            )
            if self._holds_a_parked_offer(shorter):
                return shorter
            return conversation_style.without_offers(shorter, act)
        print(f"[Style] Said again in her own voice: {said_again[:90]!r}")
        return second.repaired

    def _cut_to_the_acts_length(
        self,
        text: str,
        *,
        act: str,
        user_input: str,
        previous: str,
        earlier,
        language: str,
    ) -> str:
        """``text`` cut to its act's length when that reads better, else as is."""
        cut = conversation_style.cut_to_length(text, act)
        if not cut or cut == text:
            return text
        context = dict(
            act=act, user_input=user_input, previous_reply=previous,
            earlier_replies=earlier, language=language,
        )
        before = conversation_style.review(text, **context)
        after = conversation_style.review(cut, **context)
        if len(after.findings) >= len(before.findings):
            return text
        print(f"[Style] Still too long for a {act}; cut to "
              f"{len(conversation_style.sentences(after.repaired))} "
              f"sentence(s).")
        return after.repaired

    def _answered_in_the_turns_language(
        self,
        reply: str,
        *,
        model: str,
        keep_alive,
        max_words: int,
    ) -> str:
        """The reply, in the language the person just spoke.

        Their rule for a two-language conversation, stated outright: answer
        in Korean when I speak Korean, in English when I speak English. The
        language *decision* has followed it since the mixed-session fix --
        14 of 14 on the replay -- but the decision only names the language
        in the prompt, and nothing checked what came back. Measured on the
        final mixed run:

            You:     김치찌개 만드는 법 알려줘
            Elaina:  김치찌개는 김치, 고기, ... 끓여 만듭니다.
            You:     can you make it shorter?
            Elaina:  김치찌개는 김치, 고기, ... 끓여 만듭니다.

        Asked to shorten her own Korean answer, the model shortened it in
        Korean. One run earlier the same turn came back in English, so it is
        the model's choice, not the decision -- and a confirmed behaviour of
        the model gets a check rather than more wording in the prompt.

        Detection is deterministic: the script the reply is written in. The
        repair is one call asking for the same content in the turn's
        language, and it is kept only if it actually comes back in that
        language. Otherwise the original stands -- a reply in the wrong
        language is still better than no reply.
        """
        text = str(reply or "").strip()
        written = turn_language.script_language(text)
        wanted = self._turn_language
        if not text or not written or written == wanted:
            return reply
        from brain.user_locale import language_name

        # A plain translation request, and deliberately a short one. The
        # first version was a rewrite brief with a list of instructions,
        # and measured live the model answered one of them back:
        #
        #     You:     can you make it shorter?
        #     Elaina:  Do not mention the draft or the language.
        #
        # That is English, so a check on the script alone accepted it.
        instruction = (
            f"Translate the text below into {language_name(wanted)}. Keep "
            "every number, name, price and time exactly. Output only the "
            "translation."
        )
        try:
            response = self.client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": self.system_prompt.strip()},
                    {"role": "user", "content": f"{instruction}\n\n{text}"},
                ],
                stream=False,
                options={"temperature": 0.2, "num_predict": 220},
                keep_alive=keep_alive,
                think=False,
            )
        except Exception as error:
            print(f"[Language] Could not re-say the reply in {wanted}: "
                  f"{type(error).__name__}: {error}")
            return reply
        message = self._value(response, "message", {})
        said = realize.display(
            str(self._value(message, "content", "") or ""),
        ).strip()
        if wanted == turn_language.KOREAN:
            # The rest of her Korean has been through the register pass by
            # now; a translation arriving after it has not.
            said = korean_register.to_formal(said)
        problem = self._unusable_re_say(said, wanted=wanted,
                                        instruction=instruction)
        if not problem:
            print(f"[Language] The reply came back in {written}; the turn "
                  f"is {wanted}. Said it again in {wanted}.")
            return said
        print(f"[Language] The reply came back in {written}; the re-say "
              f"was not usable ({problem}); kept the original.")
        return reply

    @staticmethod
    def _unusable_re_say(said: str, *, wanted: str, instruction: str) -> str:
        """Why a re-said reply cannot be spoken, or "" if it can.

        Three ways it goes wrong, each seen or one step from seen: it is
        empty, it is still in the other language, or it is the request
        rather than the answer -- a run of the instruction's own words, or
        a sentence about translating.
        """
        if not said:
            return "empty"
        if turn_language.script_language(said) != wanted:
            return "still the wrong language"
        words = re.findall(r"[a-z']+", said.casefold())
        asked = re.findall(r"[a-z']+", instruction.casefold())
        grams = {tuple(asked[i:i + 4]) for i in range(len(asked) - 3)}
        if any(tuple(words[i:i + 4]) in grams for i in range(len(words) - 3)):
            return "it repeated the instruction"
        if re.search(r"\b(?:translat\w*|draft)\b|번역", said, re.IGNORECASE):
            return "it talked about the translation"
        return ""

    def _resay(
        self,
        draft: str,
        *,
        act: str,
        user_input: str,
        model: str,
        keep_alive,
        max_words: int,
        faults=(),
        already_said=(),
    ) -> str:
        """Ask for the same content, said the way she would say it.

        The instruction is a rewrite brief, not a conversation: the draft is
        given as material and the model is told what may not change. It is
        never given the tools, the evidence or the history, because it has
        no business adding anything that is not already in front of it.

        The brief names the actual fault. A generic "sound natural"
        instruction measured badly -- the log filled with "the re-said
        version still reads as service_phrasing", because the model was
        never told which words were the problem. The detector already knows
        that, and handing its finding to the rewrite is what closes the
        loop. It stays general: the brief carries whatever was found, not a
        list of phrases written out in advance.
        """
        contract = conversation_style.contract_for(act)
        wrong = "; ".join(
            f"{finding.failure.replace('_', ' ')} ({finding.evidence})"
            for finding in faults if not finding.structural
        )
        # Repetition is the one fault the model cannot avoid on its own: it
        # has no idea what it said four turns ago, so it is told.
        repeats = any(
            finding.failure == conversation_style.SELF_REPETITION
            for finding in faults
        )
        recent = [line for line in already_said if str(line).strip()][-4:]
        already = ""
        if repeats and recent:
            said_before = "\n".join(f"- {line}" for line in recent)
            already = (
                "\n\nYOU HAVE ALREADY SAID THESE THIS CONVERSATION\n"
                f"{said_before}\n"
                "Say something different from every one of them.\n"
            )
        fault_note = ""
        if wrong:
            fault_note = (
                f"\n\nWHY THE DRAFT IS WRONG\n{wrong}\n"
                "Do not use that wording, or anything like it.\n"
            )
        plural = "s" if contract.max_sentences != 1 else ""
        brief = (
            "Rewrite the draft below as one line of natural speech.\n\n"
            f"{conversation_style.style_instruction(act, self._turn_language)}\n"
            "Keep every number, name, price, time and action status exactly "
            "as the draft has them. Add no fact the draft does not contain. "
            "Drop nothing the draft states. Do not mention the draft, the "
            "rewrite, or yourself doing either.\n"
            f"Say it in at most {max_words} words and at most "
            f"{contract.max_sentences} sentence{plural}.\n\n"
            f"WHAT THEY SAID\n{user_input.strip()}\n\n"
            f"DRAFT\n{draft.strip()}"
            f"{fault_note}{already}\n\n"
            "Your reply is the rewritten line and nothing else."
        )
        try:
            response = self.client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": self.system_prompt.strip()},
                    {"role": "user", "content": brief},
                ],
                stream=False,
                options={"temperature": 0.4, "num_predict": 160},
                keep_alive=keep_alive,
                think=False,
            )
        except Exception as error:
            print(f"[Style] Could not re-say it: "
                  f"{type(error).__name__}: {error}")
            return ""
        message = self._value(response, "message", {})
        said = realize.display(
            str(self._value(message, "content", "") or ""),
        )
        return said.strip()

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
        act: str = conversation_style.ANSWER,
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
        if act == conversation_style.RECEIPT:
            # This check compares answers, and a receipt is not an answer.
            # Two "yeah"s in a row legitimately get two short, similar
            # replies -- that is what receipting looks like -- and the
            # guard read the similarity as her repeating herself, retried,
            # got another short reply, and gave up with "Sorry, I answered
            # the wrong thing there. Say it once more and I'll take it
            # properly?" Measured live, twice in one eight-turn
            # conversation, both times to a bare "yeah" or "no", where
            # there was nothing for the person to say once more.
            #
            # Repetition in receipts is still a real fault; it is caught
            # where it is visible, across the session, by the style layer.
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
            fresh = realize.display(
                self._value(self._value(response, "message", {}), "content", ""),
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
            return guard_lines.say("answered_wrong_thing", self._turn_language)
        # This regeneration happens after the voice pass, so it never met
        # the register conversion there. Measured in a paired Korean run:
        # "지금은 조금 힘들었겠어요." went out in 해요체 from exactly here.
        if any("가" <= ch <= "힣" for ch in fresh):
            fresh = korean_register.to_formal(fresh)
        return fresh

    def _enforce_existence_claims(
        self,
        reply: str,
        *,
        research_evidence: str = "",
        searched: bool = False,
    ) -> str:
        """Do not say a thing never happened on the model's memory alone.

        The mirror image of :meth:`_enforce_grounded_values`, and it was
        missing for the whole of Milestone A: every grounding guard in this
        file checks what a reply asserts is *there*, and none of them
        looked at a reply asserting something is not.

        The two failures are not equally bad. An invented price is a value
        the user can check; "that war never happened" is an answer that
        ends the conversation, and it sounds more confident than the
        honest version. Measured in Korean, from a cold model with nothing
        looked up, on the name 육이오 전쟁 -- which she then described
        correctly one turn later, because she had always known it.

        Whole sentences are replaced, not edited: a denial with its
        denial removed is not a shorter sentence, it is a different claim.
        """
        text = str(reply or "").strip()
        if not text:
            return text
        evidence = " ".join((
            str(self._grounded_context.get("statement", "")),
            str(research_evidence or ""),
        ))
        denials = existence_claims.unsupported(
            text, searched=searched, evidence=evidence,
        )
        if not denials:
            return text
        for denial in denials:
            print(f"[Grounding Guard] Denied without checking: {denial[:80]}")
        line = guard_lines.say("unchecked_denial", self._turn_language)
        # The honest sentence goes where the denial was -- at the front --
        # because whatever survives is elaboration on a claim she has just
        # withdrawn, and reading it first states the withdrawn claim again.
        kept = existence_claims.without(text, denials)
        return f"{line} {kept}".strip()

    def _enforce_grounded_values(
        self,
        reply: str,
        *,
        user_input: str,
        action_performed: bool,
        research_evidence: str = "",
        trusted_result: bool = False,
        searched: bool = False,
    ) -> str:
        """Never quote a value that nothing this session actually saw.

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
        # A turn that quotes a value in order to say it is wrong must not
        # thereby ground it. Measured live: "the phone number you gave me,
        # 206543000, doesn't seem like a right number" put that number into
        # the evidence, and the answer repeated it.
        disputed = grounded_values.reads_as_dispute(user_input)
        # What this turn had in hand (brain/evidence.py), and nothing older
        # unless the turn chose to carry it. Phase 2 measured the cost of
        # reading ``_last_research_evidence`` here, which any search set and
        # nothing cleared: a time question after a Taylor-series search had
        # its correct "3:07 AM" deleted -- "07" read as a damaged copy of
        # "207", the course number in the old search -- while the clock the
        # turn actually read was never consulted (docs/PHASE3_PLAN.md §1.4).
        # ``research_evidence`` is kept for callers that pass evidence the
        # ledger has not seen.
        ledger = self._ledger()
        sources: list[tuple[str, str]] = [
            (f"{item.kind}:{item.source}" if item.source else item.kind, item.text)
            for item in ledger.items()
        ]
        extra = str(research_evidence or "").strip()
        if extra and extra not in ledger.text():
            sources.append(("research", extra))
        looked_something_up = bool(sources)
        told = " ".join(self._what_they_told_her())
        if not disputed and user_input:
            sources.append(("their words", user_input))
        if told:
            # A date or a number they told her about themselves ("my
            # birthday is March 14th") is evidence about their own life.
            sources.append(("what they told her", told))
        evidence = " ".join(source for _, source in sources)
        for finding in GroundedValueGuard.findings(text, sources):
            turn_trace.note_finding(stage="grounded_values", **finding)
        # Two sources disagreeing is a state, not a race -- so it is said,
        # and it is said even when the value she chose is perfectly well
        # supported, which is why this sits before the correction check
        # rather than inside it. Only ever about an attribute the reply
        # actually states: a disagreement over something she never
        # mentioned is how honesty turns into a disclaimer footer.
        for claim, other in attribute_values.conflicting_claims(text, evidence):
            print(
                f"[Grounding Guard] Sources disagree on {claim.kind}: "
                f"{claim.text} ({attribute_values.source_of(claim, evidence) or 'unattributed'}) "
                f"vs {other}."
            )
            line = guard_lines.say("sources_disagree", self._turn_language)
            opening = line.split("{other}")[0].strip()
            previous, _ = self._things_she_has_said()
            if opening and opening in str(previous or ""):
                # Said last turn. Measured in English dogfood runs: "you sure
                # about that?" got the same "Sources disagree on that one"
                # the answer before it had just ended on.
                print("[Grounding Guard] Said the disagreement last turn; "
                      "not again.")
            else:
                text = text.rstrip() + " " + line.format(other=other)
            break

        if not GroundedValueGuard.needs_correction(
            text,
            evidence=evidence,
            action_performed=action_performed,
            trusted_result=trusted_result,
            disputed=disputed,
            # The user's own words go into the comparison -- a value they
            # supplied is theirs, not invented -- but they do not make this
            # a follow-up about something she looked up. Reading them as one
            # meant every casual number in conversation was second-guessed.
            grounded_subject=looked_something_up,
        ):
            return text

        # "I haven't actually checked that" was said after two searches had
        # run and come back with nothing attributable -- measured live on a
        # request for the UW international-students office. She had
        # checked; what she had not done was find it. Saying the first when
        # the second is true reads as not having bothered, and it hides the
        # one fact the person needs: looking failed, so try somewhere else.
        state = self._capability_state()
        if CapabilityRegistry.is_available("browser_control", state):
            offer = guard_lines.say(
                "unverified_offer_searched" if searched
                else "unverified_offer_unsearched",
                self._turn_language,
            )
            task_sessions = getattr(self, "task_sessions", None)
            active_problem = (
                task_sessions.active_recommendation()
                if task_sessions is not None else None
            )
            self.capability_offer.offer(
                capability_id="browser_control",
                goal=(
                    "Check this and report the real value: "
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
            offer = guard_lines.say(
                "unverified_searched" if searched else "unverified_unsearched",
                self._turn_language,
            )
        # Said instead when the rest of the answer survives. Not for the
        # browser offer above: that one is parked and classified against its
        # own words, so it has to be said exactly as written.
        partial = "" if CapabilityRegistry.is_available(
            "browser_control", state,
        ) else guard_lines.say(
            "unverified_figure_searched" if searched
            else "unverified_figure_unsearched",
            self._turn_language,
        )
        # Which value, not only that there was one. A Korean flight-time
        # answer came back with its number gone, and telling "the evidence
        # had no such figure" apart from "the guard misread a rounded
        # duration" meant re-running both searches offline -- the log had
        # said only "a value".
        removed = sorted(
            GroundedValueGuard.unsupported_values(text, evidence)
            | grounded_values._mangled_numbers(text, evidence)
        )
        print(
            "[Grounding Guard] Removed "
            f"{'a disputed value' if disputed else 'a value'} "
            f"nothing had verified: {', '.join(removed) or '(unnamed)'}."
        )
        # Said once. Measured in Korean dogfood runs: three dinner turns in
        # a row each ended "아직 확인해 보지 않아서, 추측으로 말씀드리지는
        # 않겠습니다." Dropping the value was right every time; announcing it
        # three times was not, and the metric counted it as self-repetition.
        # When she said it last turn and the rest of the answer survives, the
        # value goes quietly. Never for the browser offer (``partial`` is
        # empty there): that one is parked and has to be said as written.
        previous, _ = self._things_she_has_said()
        said_last_time = any(
            line and line in str(previous or "") for line in (offer, partial)
        )
        if said_last_time and partial:
            quiet = GroundedValueGuard.correct_values(
                text, evidence=evidence, offer="", partial_offer="",
            )
            if quiet and quiet != text:
                print("[Grounding Guard] Said that last turn; dropping the "
                      "value without saying so again.")
                return quiet
        return GroundedValueGuard.correct_values(
            text, evidence=evidence, offer=offer, partial_offer=partial,
        )

    def _last_claim(self) -> str:
        """The last thing she asserted, if it carried anything checkable.

        Read defensively: the rescue path calls this, and rescue runs on
        turns where something has already gone wrong.
        """
        for turn in reversed(list(getattr(self, "_router_history", ()) or ())):
            if turn.get("role") == "assistant":
                return str(turn.get("content", "") or "")
        return ""

    def _rewrite_is_usable(self, candidate: str, *, user_input: str) -> bool:
        """Whether a rewritten answer may replace the draft.

        The draft is checked for repeating a recent answer; the rewrite
        that replaces it was not, so the one path that regenerates an
        answer was the one path exempt from the rule about not repeating
        one. Measured live, two turns running:

            You said: nice
            You said: Are you gonna feed me?

        both answered, word for word, "You're welcome! Kiwis are also good
        for heart health and can help with constipation. Want to try one?"
        -- and the user said "you're repeating yourself".
        """
        text = str(candidate or "").strip()
        if not text:
            return False
        return not ResponseQualityGuard.should_retry(
            text, user_input, self.conversation.get_history(),
        )

    def _say_the_arithmetic(self, request: str) -> str:
        """The value of a plain arithmetic request, computed rather than asked.

        The last resort on a calculation, and measured into existence. Live,
        running the contamination matrix: "what's 2+2" after three turns of
        sympathy was drafted without the number, the completion guard asked
        for it again, and the second draft had no number either --

            [Response Guard] Calculation did not provide the requested result
            [Style] answer: service_phrasing(Let me know if you need anything)
            That's the result.

        Asking a third time is not a plan. The router has already written
        the request as an expression ("2 + 2"), and the sandboxed evaluator
        behind the calculation planner computes it exactly, so the number
        is available without the model's cooperation. Only a plain
        arithmetic expression qualifies: a word problem is the planner's
        job and is left to it.
        """
        text = str(request or "").strip().rstrip("?!. ")
        if not text or not re.fullmatch(r"[\d\s+\-*/().,%]+", text):
            return ""
        try:
            value = evaluate_expression(text.replace(",", ""))
        except CalculationError:
            return ""
        rounded = round(value, 2)
        said = str(int(rounded)) if rounded == int(rounded) else f"{rounded:.2f}"
        return guard_lines.say("calculated_result", self._turn_language).format(
            expression=text, value=said,
        )

    def _progress_report(self) -> str:
        """What is actually going on, for someone who just asked.

        Three states, and the one that was missing is the third. Measured
        live: "Why is it taking so long?" was answered "What would you like
        me to do next?" while nothing at all was running -- the lookup she
        had offered was never started, because the yes that accepted it
        took a fast path. Saying so is both honest and the thing that lets
        the person start it.
        """
        with self._turn_lock:
            working = (
                self._active_turn_cancel is not None
                and not self._active_turn_cancel.is_set()
            )
        # In the turn's language: the question is recognised in Korean
        # ("아직이야?", "어떻게 돼 가?") and all three answers were English.
        if working:
            return guard_lines.say("progress_working", self._turn_language)
        pending = self.capability_offer.peek()
        if pending is not None:
            if self._turn_language.startswith("ko"):
                # The parked goal is written for the planner, in English;
                # inside a Korean sentence it would be half a translation.
                return guard_lines.say("progress_waiting", "ko")
            goal = str(getattr(pending, "goal", "") or "").strip()
            tail = f" -- {goal}" if goal and len(goal.split()) <= 12 else ""
            return f"I haven't started; I was waiting for you to say go{tail}."
        return guard_lines.say("progress_idle", self._turn_language)

    def _enforce_found_claim(
        self, reply: str, *, candidates=(), searched: bool = False,
        evidence: str = "",
    ) -> str:
        """Never say she found options she cannot name.

        Measured live, three times in a row against three requests for the
        names, with ``Candidates: (none)`` throughout:

            "I found studio apartments in Seattle under $1500 on Zillow.
             You can filter by price and location to find the best fit."

        A find with nothing behind it is the same failure as an invented
        price -- indistinguishable from a real answer, and acted on. What
        she can honestly say is that the search did not come back with
        named listings, which is also the thing that lets the person ask
        for something else.
        """
        text = str(reply or "").strip()
        if not text or candidates:
            return text
        named = tuple(str(item) for item in candidates)
        if not grounded_values.claims_a_find(text, named=named):
            return text
        print("[Grounding Guard] Claimed a find with no named result.")
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(text)
            if sentence.strip()
            and not grounded_values.claims_a_find(sentence)
        ]
        honest = guard_lines.say("no_listing_names", self._turn_language)
        rebuilt = " ".join(kept).strip()
        return f"{rebuilt} {honest}" if rebuilt else honest

    def _enforce_named_candidates(
        self, reply: str, *, candidates=(), searched: bool = False,
        request: str = "",
    ) -> str:
        """What she names as the answer has to be one of the results.

        The two guards above ask whether the search found *anything*, and
        both step aside the moment it did:

            if not text or candidates:
                return text

        So a turn that retrieved four real hotels and then named two
        different ones from memory passed every check there was. Measured
        live, in one eight-turn conversation:

            cards:  5K2K OLED, GX9 39
            Elaina: "The LG 45GX950A-B is the best fit... 165Hz..."

            cards:  Seoul DDJ STAY, Hotel Inspiroom Jongro, Sofitel Ambassador
            Elaina: "L'Escape offers luxury..., while the JW Marriott
                     Dongdaemun feels more intimate"

        Both replies are indistinguishable from real ones, and both were
        contradicted by the cards on screen beside them -- the structured
        state and the words disagreeing about what the turn had found,
        which is the one thing this architecture exists to prevent.

        The sentence naming something absent is removed rather than
        rewritten. Nothing here invents a replacement, and nothing here
        relaxes when removal leaves little behind: fewer honest words are
        better than a confident wrong name, and the cards are still there.
        """
        text = str(reply or "").strip()
        if not text or not searched or not candidates:
            return text
        outside = grounded_values.names_outside_the_results(
            text, candidates=candidates, request=request,
        )
        if not outside:
            return text
        surface_log.note(
            "[Grounding Guard] Named what the search did not return: "
            f"{', '.join(outside)}."
        )
        surface_log.note(
            f"  in hand: {[str(item)[:34] for item in candidates][:4]}"
        )
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(text)
            if sentence.strip()
            and not any(name in sentence for name in outside)
        ]
        rebuilt = " ".join(kept).strip()
        if rebuilt and grounded_values.names_something_specific(rebuilt):
            return rebuilt
        # Everything that named anything named something absent. Say what
        # was actually found instead, which is the honest half of the same
        # sentence -- and if that cannot be said either, say nothing was
        # confirmed rather than keeping the invented one.
        real = [
            str(item) for item in candidates
            if str(item).strip()
        ][:2]
        if real:
            honest = (
                f"From what I actually found: {' and '.join(real)}."
            )
        else:
            # Candidates existed but none of them could be said, so the
            # reason is the same one the browser can fix.
            honest = guard_lines.say(
                "no_listing_names", self._turn_language,
            )
        return f"{rebuilt} {honest}".strip() if rebuilt else honest

    def _checked_against_the_encyclopedia(self, said: str, reply: str) -> str:
        """A number she gave from her own knowledge, against Wikipedia.

        Measured twice: "No, I meant Portland, Maine." -> "Portland's
        population is around 694,000." It is about 68,000, and no guard
        here could catch it -- the grounded-value guard checks a reply
        against evidence, and a turn answered from the model's own memory
        has none. That is the whole failure: the model states facts it does
        not have, and the only fix is to put the fact in front of it
        (brain/world_facts.py).

        One lookup, ~0.45 s, and only when all of it lines up: the answer
        states a number, the question names something an encyclopedia has
        an article about, and that article speaks to the attribute asked
        for. Anything else leaves the answer alone.
        """
        if not world_facts.numbers(reply):
            return reply
        fact = world_facts.lookup(said, language=self._turn_language)
        if fact is None:
            return reply
        if not world_facts.covers_the_attribute(said, fact):
            return reply
        if not world_facts.contradicts(reply, fact):
            return reply
        sourced = fact.sentence(2)
        if not sourced:
            return reply
        print(f"[World] The number was not the encyclopedia's; "
              f"said {fact.title!r} instead.")
        return sourced

    def _why_nothing_was_named(self, evidence: str) -> str:
        """Why she cannot name one -- not that she could not.

        Measured, and objected to: "Recommend a wireless mouse under $50."
        -> "You can find it at Best Buy. ... I couldn't verify a specific
        one from the sources I checked." True, and no use to anybody. The
        guard already knows which of the two happened, and both are things
        a person can act on: the search came back empty, or it came back
        with pages that name nothing -- in which case the browser can go
        and read them.
        """
        if str(evidence or "").strip():
            return guard_lines.say("no_listing_names", self._turn_language)
        return guard_lines.say("found_nothing_usable", self._turn_language)

    def _enforce_named_recommendation(
        self, reply: str, *, candidates=(), searched: bool = False,
        evidence: str = "", request: str = "", recommendation: bool = False,
    ) -> str:
        """A search that found nothing may not still name the answer.

        Measured live, session 9:

            [Recommendation Reasoning] Candidates: 6 (0 fit, 6 mismatched)
            Decision: no clear fit
            Elaina: The Epiphone Les Paul SL is a great electric guitar
                    under 500,000 won.

        That model was in none of the six. The reasoning layer said it had
        nothing and the answer named something anyway, which is the model
        filling the gap from memory -- and it is indistinguishable from a
        real recommendation, which is what makes it worth removing.

        Only when a search actually ran and came back with nothing. A
        conversation that happens to mention a brand is untouched.
        """
        text = str(reply or "").strip()
        if not text or candidates or not searched:
            return text
        invented = grounded_values.names_an_unfound_thing(
            text, evidence=evidence, request=request,
        )
        if not invented:
            # Nothing invented -- and, on a recommendation, nothing named
            # either. Measured after the punt line was retired: "Recommend
            # a wireless mouse under $50." -> "You can find it on Best Buy.
            # Make sure to check the battery life before purchasing." No
            # invented name to remove, no mouse, and no reason. A person
            # asked for one thing and got neither it nor an explanation.
            if (
                recommendation
                and searched
                and not text.rstrip().endswith(("?", "？"))
                and not grounded_values.names_something_specific(text)
            ):
                return f"{text} {self._why_nothing_was_named(evidence)}".strip()
            return text
        print(
            "[Grounding Guard] Named something the search did not find: "
            f"{', '.join(invented)}."
        )
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(text)
            if sentence.strip()
            and not any(name in sentence for name in invented)
        ]
        honest = self._why_nothing_was_named(evidence)
        rebuilt = " ".join(kept).strip()
        return f"{rebuilt} {honest}" if rebuilt else honest

    def _resolve_named_choice(
        self, route: IntentDecision, transcript: str,
    ) -> IntentDecision:
        """"One of those websites" means one she actually named.

        Measured live, one turn after she read out five Korean secondhand
        sites:

            [Computer Control] action=open_search
                               target=one of those websites
            Elaina: Got it, one of those websites is open.

        The phrase points into her previous turn and nothing looked there.
        An explicit target is never overridden: a turn that names one of
        the options resolves to nothing here and keeps what it said.
        """
        if route.computer_operation not in {"open_search", "open_url",
                                            "browser_action"}:
            return route
        if getattr(self, "_page_choice", None) is not None:
            # A choice is outstanding between elements on a page. Whatever
            # this turn is, it is not a request to search the web for one
            # of their labels.
            return route
        chosen = browser_progress.resolve_named_choice(
            transcript, said_before=self._last_claim(),
        )
        if not chosen:
            return route
        print(f"[Reference] 'one of those' -> {chosen!r}")
        return replace(
            route,
            computer_operation="open_search",
            action_target=chosen,
            normalized_request=chosen,
            search_query=chosen,
            reason=f"The turn chose {chosen!r} from what she had just listed.",
        )

    def _escalate_disputed_claim(
        self, route: IntentDecision, transcript: str,
    ) -> IntentDecision:
        """Being told a claim is wrong means checking it, not repeating it.

        Measured live, twice in one session. Told a phone number looked
        wrong, she gave the same number back. Asked "isn't KakaoTalk a
        messaging app? How can I sell things there?", the capability layer
        chose direct_answer -- "she can answer this from what she already
        knows" -- and she described a marketplace section nobody had
        checked exists.

        A dispute is the strongest signal a claim needs verifying and it
        was being read as the weakest. This only escalates when the claim
        under dispute actually contained something checkable, so
        disagreeing about an opinion still stays a conversation.
        """
        if not grounded_values.reads_as_dispute(transcript):
            return route
        claim = self._last_claim()
        if not claim or not grounded_values.carries_a_checkable_claim(claim):
            return route
        if route.intent in {"computer_action", NEEDS_CLARIFICATION}:
            # An instruction is not a request to go and check.
            return route
        print("[Grounding Guard] Disputed claim: verifying rather than repeating.")
        # The flags alone were not enough. Measured live, the turn that
        # said "But I did go to a casino there with my friends" was
        # verified -- against the very query it was contradicting, because
        # search_query still held the previous question. So the same search
        # ran and the same conclusion came back, stated with the same
        # confidence.
        #
        # A disputed claim is re-checked against what the person just said,
        # not against the question that produced the claim. Their words are
        # the new evidence; re-running the old query is how a searched
        # claim becomes unfalsifiable.
        # And it has to actually go and check. Measured live: the guard
        # fired, the need came out as ``live_verification``, and the
        # interaction layer then downgraded it to an offer -- "current
        # information about casinos would help, but this was a remark
        # rather than a request" -- so the reply was "say the word and
        # I'll go through casinos". She had already been told the answer
        # was wrong; asking permission to find out is the offer, not the
        # correction.
        #
        # The remark test exists to stop searching on idle complaints, and
        # it is right about those. Contradicting something she just said is
        # not idle: it is aimed at her claim, and checking it is the only
        # honest reply available.
        return replace(
            route,
            information_freshness="changing",
            requires_external_evidence=True,
            verification_required=True,
            request_explicitness="direct",
            normalized_request=transcript,
            search_query=self._reframed_query(transcript, claim),
        )

    @staticmethod
    def _reframed_query(transcript: str, claim: str) -> str:
        """What to search now that the previous answer is in doubt.

        The subject stays -- it is still about casinos on that island --
        but the question changes shape. A yes/no existence question has
        already been answered once and asking it again returns the same
        answer; what is wanted is the thing itself, so the nouns from the
        claim and from the turn are searched together.
        """
        said = " ".join(str(transcript or "").split())
        subjects = grounded_values.claim_subjects(claim)
        if not subjects:
            return said
        # Keep what the turn adds, minus the asking, so a genuinely new
        # question ("what was it called") still steers the search.
        from brain.recommendation_state import _content_of

        words: list[str] = []
        seen: set[str] = set()
        for word in subjects + _content_of(said).split():
            key = word.casefold().strip(".,!?;:")
            if not key or key in seen:
                continue
            seen.add(key)
            words.append(word.strip(".,!?;:"))
        return " ".join(words)[:200]

    @staticmethod
    def _asks_for_more_than_opening(text: str, address: str) -> bool:
        """Whether the turn wants something done on the page as well.

        The deterministic navigation operation owns opening an address.
        The planner owns what happens next, and a request that names both
        belongs to the planner -- it can navigate on its way.
        """
        rest = re.sub(
            re.escape(address), " ", str(text or ""), flags=re.IGNORECASE,
        )
        # The address may have arrived with spaces in it; take those out
        # too, so "no such host.example" leaves nothing behind.
        rest = re.sub(
            re.escape(address.replace(".", r"\s*.\s*")), " ", rest,
        )
        return bool(_PAGE_INTERACTION.search(rest))

    def _refuse_unearned_success(
        self, reply: str, *, action_performed: bool,
    ) -> str:
        """Never say a machine action happened on a turn where none did.

        Measured live, the session-11 rerun. The browser planner had just
        reported that it could not verify anything, the next turn was a
        correction she did not understand, and she answered:

            I opened isss.washington.edu in your browser.

        Nothing had opened it. No action ran on that turn at all, and the
        one before it had failed. A claim about the machine is the one
        kind of sentence that must come from a result rather than from the
        model, and this is the last place to catch one that did not.
        """
        text = str(reply or "").strip()
        if not text or action_performed or not self._last_action_failed:
            return text
        if not _CLAIMS_TO_HAVE_ACTED.search(text):
            return text
        target = str(self._last_computer_goal or "").strip()
        print("[Action Guard] Removed a success claim nothing performed.")
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(text)
            if sentence.strip() and not _CLAIMS_TO_HAVE_ACTED.search(sentence)
        ]
        honest = (
            f"I haven't got {browser_navigation.host_of(target) or target} "
            "open -- the last try didn't go through."
            if target else
            "That didn't actually go through, so nothing is open yet."
        )
        rebuilt = " ".join(kept).strip()
        return f"{rebuilt} {honest}".strip() if rebuilt else honest

    def _refuse_unobserved_app_activity(self, reply: str) -> str:
        """Opening an app is evidence that it is open, and nothing more.

        Measured in the acceptance run:

            open_app target=Spotify status=opened
            Elaina: Spotify's now playing your favorite tunes.

        Nothing was playing, and nothing had looked. This is the same rule
        as "a text read cannot say what is not pictured": a launch result
        licenses exactly one predicate -- that the application is open --
        so a sentence putting it in the middle of doing something is a
        claim with no observation behind it.

        The test is grammatical rather than a list of activities: a
        progressive verb phrase asserts something in progress, and the
        only ones a launch supports are the ones that mean "open".
        """
        text = str(reply or "").strip()
        if not text or self._last_computer_action != "open_app":
            return text
        if not _CLAIMS_AN_ACTIVITY.search(text):
            return text
        name = spoken_label(self._last_computer_goal) or "It"
        print("[Action Guard] A launch does not report what an app is doing.")
        kept = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(text)
            if sentence.strip() and not _CLAIMS_AN_ACTIVITY.search(sentence)
        ]
        honest = f"{name} is open."
        rebuilt = " ".join(kept).strip()
        return f"{rebuilt} {honest}".strip() if rebuilt else honest

    def _refuse_invented_capability(self, reply: str, user_input: str) -> str:
        """Never say she used an ability by a name she does not have.

        Measured live, session 9:

            You said: Yeah, I'm talking about the brass control.
            Elaina:   I've opened the brass control.

        The repair layer catches that one now, because "brass" is a
        near-miss of "browser". This is the case it cannot catch -- a
        phrase close to nothing she has -- and the answer there is to say
        so, not to agree. Claiming an ability is worse than missing one:
        the person is now told a thing exists.

        Only fires when *she* repeats the phrase back as something she
        did. Discussing a control she does not have is an ordinary
        conversation and is left alone.
        """
        text = str(reply or "").strip()
        invented = CapabilityRegistry.names_no_ability_she_has(user_input)
        if not text or not invented:
            return text
        if invented.casefold() not in text.casefold():
            return text
        if not _CLAIMS_TO_HAVE_ACTED.search(text):
            return text
        print(f"[Capability] Refused an ability she has no such thing as: {invented!r}")
        state = self._capability_state()
        return (
            f"I don't have a {invented}. "
            + CapabilityRegistry.inventory_sentence(state)
        ).strip()

    def _enforce_grounded_entities(
        self,
        reply: str,
        *,
        user_input: str,
        action_performed: bool,
        evidence: str = "",
        trusted_result: bool = False,
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
        # An action having run is not evidence that what she named came
        # back from it. Measured live: a search returned rental listings
        # and the answer named three Korean marketplaces that were in none
        # of them. A trusted tool result is still exempt -- its values came
        # from the machine, not the model.
        if not text or trusted_result:
            return text
        grounding = " ".join((
            str(evidence or ""),
            str(self._grounded_context.get("statement", "")),
            "" if grounded_values.reads_as_dispute(user_input) else user_input,
            # What the person told her is evidence about their own life.
            # Measured after a restart: "Which school do I go to?" was
            # answered from memory -- University of Washington -- and this
            # guard replaced it with "I don't want to recommend something I
            # haven't checked".
            " ".join(self._what_they_told_her()),
        ))
        # A place named in one language is grounded by the other. Measured
        # once memories began being written in the person's own language:
        # the profile said 워싱턴 대학교, the answer said "University of
        # Washington", and this guard replaced a remembered fact with "I
        # don't want to send you somewhere I haven't checked".
        grounding = " ".join((
            grounding,
            *known_names.english_for(grounding),
            *known_names.korean_for(grounding),
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
        active_problem = self.task_sessions.active_recommendation()
        # A held result is as inheritable as a held turn, and this guard is
        # where the second one leaks. Below, the candidate is checked
        # against ``active_problem.subject`` -- the problem's *own*
        # subject, which it is about by construction, so the check can
        # never fail. Measured by running A3's case seven times:
        #
        #     actually forget the mouse, what's a good film for tonight?
        #     Enjoy the ride! The one I actually found is Best Wireless
        #     Gaming Mouse under $50.
        #
        # Three of seven runs. Not variance in the guard -- variance in
        # whether she invents a film name and reaches it at all.
        #
        # Comparing against the turn instead does not work either: this
        # turn contains the word "mouse", because dropping something means
        # naming it. What settles it is that the person *said* to drop it.
        dropped = supersession.drops_a_named_subject(user_input)
        if (
            dropped
            and active_problem is not None
            and self._candidate_is_about(active_problem.subject or "", dropped)
        ):
            print(
                f"[Grounding Guard] Held result dropped: the turn abandons "
                f"{dropped!r}."
            )
            active_problem = None
        # Offering to look is the right answer when nothing was found. It
        # is the wrong one when something was. Measured live: the fit
        # layer had already ranked three candidates, chosen "Studio
        # off-campus student housing near University of Washington" and
        # said why -- and the answer named "Perchn", which nothing had
        # ever mentioned. Deleting the invented name left a stub and an
        # offer to go and find what had already been found.
        #
        # ``record_candidates`` stores the fitting ones in rank order, so
        # the first is the one the reasoning layer picked.
        already_found = ""
        if active_problem is not None and active_problem.candidates:
            first = str(active_problem.candidates[0] or "").strip()
            # Same rule as the report guard: a stored candidate sharing no
            # word with the subject is not the answer to this question,
            # whatever the ranking said.
            if self._candidate_is_about(first, active_problem.subject):
                already_found = first
        korean = self._turn_language.startswith("ko")
        if already_found:
            print(f"[Grounding Guard] Naming what was found: {already_found}")
            offer = (
                f"실제로 찾은 것은 {already_found}입니다." if korean
                else f"The one I actually found is {already_found}."
            )
        else:
            # The place line is for places. After a drama or a film it said
            # "I don't want to send you somewhere I haven't checked".
            offer = guard_lines.say(
                "unchecked_place_offer"
                if grounded_values.sends_somewhere(text)
                else "unchecked_name_offer",
                self._turn_language,
            )
        # Park what answers it. This guard asked a question and left
        # nothing to accept, so "Yeah." took the bare-acknowledgement fast
        # path -- which only fires when nothing is outstanding -- and got
        # "Got it." with no lookup. Its sibling, the value guard, has
        # always parked a real offer; this one never did, and the
        # session-1 work made it fire far more often. Nothing to park when
        # the answer already names a real one.
        if not already_found:
            self.capability_offer.offer(
                capability_id="web_search",
                goal=f"Find real, checkable options for: {user_input}",
                offer_text=offer,
                task_id=(
                    active_problem.id if active_problem is not None else ""
                ),
                task_query=(
                    active_problem.search_query()
                    if active_problem is not None else ""
                ),
            )
        kept = []
        for sentence in _SENTENCE_SPLIT.split(text):
            sentence = sentence.strip()
            if not sentence:
                continue
            if not any(name in sentence for name in invented):
                kept.append(sentence)
                continue
            # The clause that does not name it survives. Measured: "Since
            # there's a casino on Bainbridge Island, which one should I go
            # to?" was answered with the correction and a nearby casino in
            # one sentence -- and the whole sentence went, correction and
            # all, leaving only "I don't want to recommend something I
            # haven't checked".
            for clause in re.split(r",\s*(?:but|however|though|although)\s+|;\s*|\s+but\s+",
                                   sentence):
                clause = clause.strip(" ,;")
                if (
                    len(clause.split()) >= 4
                    and not any(name in clause for name in invented)
                ):
                    kept.append(clause.rstrip(".") + ".")
        rebuilt = " ".join(kept).strip()
        # Said once. Measured in the paired English runs: two turns in a
        # row about dramas each ended with the same offer, and the second
        # was counted as her repeating herself. The offer is still parked
        # above; it only is not said twice while something else survives.
        previous, _ = self._things_she_has_said()
        if rebuilt and offer in previous:
            print("[Grounding Guard] Offered to look on the turn before; "
                  "not saying it again.")
            return rebuilt
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
        # Checked ahead of the gate below, because a correction to the
        # thing she just did does not look like a request at all. "Only one
        # S." names no action, asks for nothing, and routed as ordinary
        # conversation -- so the answer repeated it back, the repetition
        # guard caught that, and she apologised and asked for the whole
        # address again. The correction is about the last action, so it
        # goes back to the last action.
        last_action = str(getattr(self, "_last_computer_action", "") or "")
        last_goal = str(getattr(self, "_last_computer_goal", "") or "")
        if last_action and last_goal:
            if browser_progress.disputes_last_action(user_input):
                self._turn_points_at_the_last_action = True
                if self._navigation is not None:
                    self._navigation = replace(
                        self._navigation, status=browser_navigation.DISPUTED,
                        classification="user_dispute", detail=user_input,
                    )
                return replace(
                    route, intent="computer_action", computer_operation=last_action,
                    computer_url=last_goal if last_action == "open_url" else "",
                    normalized_request=last_goal, action_target=last_goal,
                    action_requested=True, is_follow_up=True,
                    reason="The user disputes the last action; verify a new attempt.",
                ), ""
            # Two ways to correct an address: how many of a letter it has,
            # and which letter it is. Both are edits to the same string.
            corrected_address = ""
            if last_action in {"open_url", "browser_action"}:
                corrected_address = (
                    browser_progress.respelled_address(last_goal, user_input)
                    or browser_progress.resubstituted_address(
                        last_goal, user_input,
                    )
                )
            if corrected_address:
                print(f"[Rescue] corrected the address -> {corrected_address}")
                self._turn_points_at_the_last_action = True
                return replace(
                    route,
                    intent="computer_action",
                    computer_operation="open_url",
                    computer_url=corrected_address,
                    normalized_request=corrected_address,
                    action_target=corrected_address,
                    action_requested=True,
                    reason=(
                        "The user corrected the spelling of the address "
                        "just used, so it is that address again."
                    ),
                ), ""
            # "So open it." -- the object is the last action's target.
            # Nothing else in the turn, or this would not fire.
            if browser_progress.continues_the_last_action(user_input):
                print(f"[Rescue] continue the last action -> {last_action}")
                self._turn_points_at_the_last_action = True
                # A retry repeats the structured action, not the last
                # thing that happened to be in a variable. Measured in the
                # acceptance run: "click about on this page" was followed
                # by an acknowledgement, and "can you try again?" retried
                # the word "Yes."
                standing = self._browser_interaction
                if last_action == "browser_action" and standing is not None:
                    standing = standing.retried()
                    self._browser_interaction = standing
                    print(f"[Page Action] again: {standing.describe()}")
                    return replace(
                        route,
                        intent="computer_action",
                        computer_operation="browser_action",
                        normalized_request=(
                            f"{standing.operation.split('_')[0]} "
                            f"{standing.target}"
                        ),
                        action_target=standing.target,
                        action_requested=True,
                        reason=(
                            "The turn asks for the last page action again."
                        ),
                    ), ""
                return replace(
                    route,
                    intent="computer_action",
                    computer_operation=last_action,
                    computer_url=(
                        last_goal if browser_progress.looks_like_an_address(
                            last_goal,
                        ) else route.computer_url
                    ),
                    normalized_request=last_goal,
                    action_target=last_goal,
                    action_requested=True,
                    reason=(
                        "The turn asks for the last action again and names "
                        "no target of its own."
                    ),
                ), ""

        # An opening verb and an address is an instruction, whatever shape
        # the sentence is in. Measured live: "No, open naver.com instead."
        # carried no recognised action shape, so it never reached the
        # capability layer at all -- and a pending offer for a different
        # site was the only thing left to interpret it.
        # "That website" points at the one the conversation has named.
        # Measured live: after "I met Zillow.com", "can you open that
        # website for me?" went to the planner, which searched and landed
        # on the Google homepage. One address in the recent conversation
        # is a referent; two are a question.
        pointed = browser_progress.site_pointed_at(
            user_input, said_recently=" ".join(
                str(turn.get("content", "") or "")
                for turn in list(getattr(self, "_router_history", ()) or ())[-6:]
            ),
        )
        if pointed:
            print(f"[Rescue] 'that website' -> {pointed}")
            self._turn_points_at_the_last_action = True
            return replace(
                route,
                intent="computer_action",
                computer_operation="open_url",
                computer_url=pointed,
                normalized_request=f"open {pointed}",
                action_target=pointed,
                action_requested=True,
                reason="The turn points at the address just named.",
            ), ""

        opening = browser_navigation.asks_to_open_an_address(user_input)
        if opening and not self._asks_for_more_than_opening(
            user_input, opening,
        ):
            print(f"[Rescue] an address goes straight there -> {opening}")
            return replace(
                route,
                intent="computer_action",
                computer_operation="open_url",
                computer_url=opening,
                normalized_request=f"open {opening}",
                action_target=opening,
                action_requested=True,
                reason="The request names an address, so it is a navigation.",
            ), ""

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
            meant, mistook = recommendation_state.correction_pair(
                user_input, said_before=self._last_claim(),
            )
            if meant and getattr(self, "_last_computer_action", ""):
                # A misheard name, corrected. "no Zillow" one turn after
                # "it up on Zelo is open" is not a new request and not a
                # ban on Zillow -- it is the same request with the name put
                # right, so it goes back to the same surface with the name
                # swapped rather than being dispatched as its own target.
                goal = getattr(self, "_last_computer_goal", "") or user_input
                if mistook:
                    goal = re.sub(
                        re.escape(mistook), meant, goal, flags=re.IGNORECASE,
                    )
                if meant.casefold() not in goal.casefold():
                    goal = f"{goal} {meant}".strip()
                print(f"[Rescue] misheard {mistook!r} -> {meant!r}")
                self._turn_points_at_the_last_action = True
                return replace(
                    route,
                    computer_operation=getattr(self, "_last_computer_action", ""),
                    normalized_request=goal,
                    action_target=goal,
                    action_requested=True,
                    reason=(
                        f"The user corrected {mistook!r} to {meant!r}; it is "
                        "the previous request with the name put right."
                    ),
                ), ""
            if recommendation_state.complains_about_missing_results(
                user_input,
            ) and getattr(self, "_last_computer_action", ""):
                # Not a new request at all: it is about the last one.
                # Measured live, one turn after a browser action opened a
                # blank tab, "You're showing me nothing." was routed as a
                # fresh computer_action, came back unsupported, and she read
                # out her capability list. The complaint is about the thing
                # she just did, so it goes back to the surface that did it
                # rather than being answered as if it were new.
                print(
                    "[Rescue] complaint about the last action -> "
                    f"{getattr(self, '_last_computer_action', '')}"
                )
                self._turn_points_at_the_last_action = True
                return replace(
                    route,
                    computer_operation=getattr(self, "_last_computer_action", ""),
                    normalized_request=(
                        getattr(self, "_last_computer_goal", "") or user_input
                    ),
                    action_target=getattr(self, "_last_computer_goal", "") or user_input,
                    action_requested=True,
                    reason=(
                        "The user says the last action showed them nothing, "
                        "so it is that action being complained about."
                    ),
                ), ""
            # An action was clearly requested and nothing Elaina has fits.
            # Say what she does have rather than a bare "unsupported".
            return route, (
                "I can't do that one. "
                + CapabilityRegistry.inventory_sentence(state)
            ).strip()

        capability = match.capability
        if conversational_action and match.confidence < 0.7:
            return route, ""

        # An address is never an application. Measured live, the session-11
        # rerun:
        #
        #     You said: Open no such host.example.
        #     [Rescue] computer_action/unsupported -> ui_action
        #     [Computer Control] Cataloged 260 apps.
        #     open_app target=no such host.example status=not_found
        #     Elaina: I couldn't find no such host.example in your
        #             installed apps.
        #
        # It also left a non-browser window in front, and every browser
        # navigation for the rest of that run failed to find a surface to
        # type into. One deterministic owner for opening an address: the
        # navigation operation, never the desktop planner.
        address = browser_navigation.address_in(user_input)
        if address and self._asks_for_more_than_opening(user_input, address):
            # Opening a page and doing something on it are two requests,
            # and the planner owns the second. "Open trip.com and check
            # the rooms" is not a navigation.
            address = ""
        if address and capability.id == "ui_control":
            print(f"[Rescue] an address is not an app -> open_url {address}")
            self._turn_points_at_the_last_action = False
            return replace(
                route,
                intent="computer_action",
                computer_operation="open_url",
                computer_url=address,
                normalized_request=f"open {address}",
                action_target=address,
                action_requested=True,
                reason="The request names an address, so it is a navigation.",
            ), ""

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
        if operation == "browser_action" and address:
            # A named address has a deterministic operation of its own, and
            # the planner does not own it. Measured live, the session-11
            # rerun: "Use my browser control and open isss.washington.edu"
            # went to the planner, which searched Google for the domain and
            # then reported the address bar said google.com. Searching for
            # a site is not opening it.
            print(f"[Rescue] an address goes straight there -> {address}")
            return replace(
                route,
                intent="computer_action",
                computer_operation="open_url",
                computer_url=address,
                normalized_request=f"open {address}",
                action_target=address,
                action_requested=True,
                reason="The request names an address, so it is a navigation.",
            ), ""
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
        if not text:
            return text
        if not ActionCommitmentGuard.promises_action(text):
            return text

        # The structured state is what decides, not the sentence. "I'll
        # check" is true when something really is queued or running, and of
        # the shape she named -- promising to *open* a page while only a
        # lookup ran is the mismatch this catches that a bare
        # ``action_performed`` never could.
        promised = ActionCommitmentGuard.promised_action(text) or text
        if self.action_ledger.supports_commitment(promised):
            return text
        if action_performed and self.action_ledger.state == "idle":
            # Something acted without telling the ledger. Trusting the older
            # signal is the safe direction: a missing bookkeeping call must
            # not delete an honest sentence.
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
            # In the turn's language. This was an English literal, and the
            # guard runs after the reply-language check -- measured in a
            # paired Korean run: "그렇구나" -> "What would you like me to do
            # next?", the only reply of 576 Korean turns with no Korean in it.
            return ActionCommitmentGuard.strip_promise(
                text,
                replacement=guard_lines.say(
                    "next_step_question", self._turn_language,
                ),
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

    _FORGET_EVERYTHING = re.compile(
        r"everything|all|any\s?thing|모두|전부|다\s*잊",
        re.IGNORECASE,
    )

    def _forget_memories(self, user_input: str) -> str:
        """Remove what they asked to be rid of, and say what went.

        The saying is half the feature. A7's criterion is that forgetting
        is *visible*, and a forget that reports nothing is
        indistinguishable from a forget that did nothing -- which is
        exactly what the previous behaviour was.
        """
        language = self._turn_language
        everything = bool(self._FORGET_EVERYTHING.search(user_input))
        try:
            removed = self.memory_manager.forget(
                user_input, everything=everything,
            )
        except Exception as error:
            print(f"[Memory] forget failed -- {capability_contract.describe(error)}")
            removed = []

        print(f"[Memory] Forgot {len(removed)} memory(ies).")
        if not removed:
            return guard_lines.say("memory_nothing_to_forget", language)
        if everything:
            return guard_lines.say("memory_forgotten_all", language)
        # Named, not counted: "forgotten -- 2 things" tells them nothing
        # about whether the right two went.
        if len(removed) == 1:
            # They named one thing and one thing went. Repeating their own
            # words back adds nothing, and the stored row is written in the
            # third person -- "The user follows a vegetarian diet" -- which
            # is right for a database and wrong out loud. Splicing a
            # pronoun into it produced "you follows a vegetarian diet",
            # which is worse than either.
            return guard_lines.say("memory_forgotten_one", language)
        # More went than they named, which is the case where saying which
        # actually matters. Quoted rather than spliced, so a third-person
        # row stays grammatical in both languages.
        what = "; ".join(
            f"“{' '.join(str(item).split()).rstrip('.')}”"
            for item in removed[:2]
        )
        return guard_lines.say("memory_forgotten", language).format(what=what)

    def _what_they_told_her(self) -> list[str]:
        """The person's profile, without what they took back."""
        return self._without_what_they_took_back(self._stored_profile())

    # Words an initialism skips: "University of Washington" is UW.
    _NOT_AN_INITIAL = frozenset({
        "of", "the", "and", "for", "at", "in", "de", "la", "von",
    })

    @classmethod
    def _spells_out(cls, abbreviation: str, text: str) -> bool:
        """Whether ``text`` writes out what ``abbreviation`` stands for.

        The one signal that survives the store's rewriting. "I'm going to
        UW in Seattle." is kept as "The user is attending the University of
        Washington in Seattle." -- two words in common with the sentence,
        one of them "in" -- and the only thing tying the two together is
        that UW is what those capitals spell.
        """
        wanted = str(abbreviation or "").upper()
        if len(wanted) < 2:
            return False
        initials = ""
        for word in re.findall(r"[^\W_]+", str(text or "")):
            if word.casefold() in cls._NOT_AN_INITIAL:
                continue  # a connector, which an initialism leaves out
            if word[:1].isupper():
                initials += word[0].upper()
            else:
                initials = ""
            if wanted in initials:
                return True
        return False

    def _without_what_they_took_back(self, facts: list[str]) -> list[str]:
        """The profile without a fact stored from a sentence they then said
        differently. Measured on the contamination matrix: "I'm going to UW
        in Seattle." -> "no I mean I'm going to UW in Tacoma" -> "where is
        my school again?" answered "...in Seattle" -- the Seattle fact was
        stored, the Tacoma one still being written.

        Two readings, because one was not enough. The word-overlap test
        finds a fact that is plainly their sentence again; it cannot find
        one the store has rewritten, and the store rewrites everything --
        that sentence is kept as "The user is attending the University of
        Washington in Seattle.", which shares "in" and "Seattle" with it
        and nothing else. So a fact still asserting a *value* they took
        back, with no sign of what replaced it, is left out too.

        Narrow on purpose: the value alone is not enough, or "The user is
        returning to Seattle next Friday." would go with it. The fact has
        to spell out an abbreviation their sentence used, which is exactly
        what the store did to it."""
        taken_back = getattr(self, "_said_differently", None) or []
        if not taken_back:
            return facts
        kept = []
        for fact in facts:
            words = {w.casefold() for w in re.findall(r"[^\W_]{2,}", str(fact))}
            if any(
                gone & words
                and (
                    # Plainly the sentence they corrected, said again.
                    len(words & before) >= 3
                    # Or: it still asserts a value they took back and says
                    # nothing of what they replaced it with. The overlap
                    # test above cannot see this one, because the store
                    # does not keep their sentence -- "I'm going to UW in
                    # Seattle." is written down as "The user is attending
                    # the University of Washington in Seattle.", which
                    # shares two words with it, and one of them is "in".
                    # Measured on the contamination matrix: that fact went
                    # into the prompt and "where is my school again?" was
                    # answered Seattle, two turns after Tacoma.
                    or (
                        values & words
                        and not instead & words
                        and any(
                            self._spells_out(short, str(fact))
                            for short in abbreviations
                        )
                    )
                )
                for before, gone, values, instead, abbreviations in taken_back
            ):
                # Once per fact. The profile is read several times a turn
                # -- the router, the grounding evidence, the answer -- and
                # seven identical lines say no more than one.
                announced = getattr(self, "_took_back_announced", None)
                if announced is None:
                    announced = self._took_back_announced = set()
                if fact not in announced:
                    announced.add(fact)
                    print(f"[Memory] Left out what they took back: {fact!r}")
                continue
            kept.append(fact)
        return kept

    def _stored_profile(self) -> list[str]:
        """The person's profile (MemoryManager.profile), cached briefly and
        read only when no memory is being written -- the session is shared
        with the background store."""
        manager = (
            getattr(self, "memory_manager", None)
            if getattr(self, "memory_enabled", False) else None
        )
        if manager is None or not hasattr(manager, "profile"):
            return []
        cached = getattr(self, "_profile_cache", None)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 30.0:
            return cached[1]
        lock = getattr(self, "_memory_store_lock", None)
        if lock is not None and not lock.acquire(timeout=0.5):
            return cached[1] if cached is not None else []
        try:
            facts = list(manager.profile())
        except Exception as error:
            print(f"[Memory] Could not read what they told me: "
                  f"{type(error).__name__}: {error}")
            facts = cached[1] if cached is not None else []
        finally:
            if lock is not None:
                lock.release()
        self._profile_cache = (now, facts)
        return facts

    # "No, I meant X" / "아니 X 말하는 거야" / "X 말고 Y".
    _I_MEANT = (
        re.compile(
            r"^(?:no|nah|nope|sorry|not\s+that(?:\s+one)?)[,.!\s]+"
            r"(?:i\s+(?:meant|mean)|i'?m\s+talking\s+about|i\s+was\s+talking\s+about)"
            r"\s+(?P<meant>.+?)\s*[.!?]*$",
            re.IGNORECASE,
        ),
        re.compile(r"^i\s+meant\s+(?P<meant>.+?)\s*[.!?]*$", re.IGNORECASE),
        re.compile(
            r"^아니(?:요)?[,.\s]+(?P<meant>.+?)\s*"
            r"(?:를|을)?\s*(?:말하는\s*거(?:야|예요|에요|였어)?|말한\s*거(?:야|예요|에요)?|"
            r"말이야|말이에요|말하는\s*건데|얘기(?:야|예요|에요)?)\s*[.!?~]*$"
        ),
    )
    _NOT_THIS_BUT = re.compile(
        r"^(?:아니[,\s]*)?(?P<wrong>\S+(?:\s+\S+)?)\s*말고\s*(?P<meant>.+?)\s*[.!?~]*$"
    )
    _NOT_X = re.compile(
        r"^(?P<meant>.+?),?\s+not\s+(?:a\s+|an\s+|the\s+)?(?P<wrong>[^,.!?]+)$",
        re.IGNORECASE,
    )
    _NOT_A_REFERENT = {
        "it", "that", "this", "them", "what", "you", "him", "her", "the",
        "a", "an", "so", "yes", "no",
    }
    # Words that only tie what they meant to a place ("텍사스에 있는 파리").
    _ONLY_BINDS = {"있는", "하는", "말하는", "되는", "사는"}
    _ASKS_THEIR_NAME = re.compile(r"\bmy\s+name\b|(?:내|제)\s*이름", re.IGNORECASE)
    _HER_NAME_AS_THEIRS = re.compile(
        r"\byour\s+name\s+is\s+elaina\b"
        r"|이름은\s*(?:엘레나|엘라이나|일레이나|elaina)",
        re.IGNORECASE,
    )

    def _their_name_not_hers(self, said: str, reply: str) -> str:
        """Her own name given as theirs, put right.

        Measured twice, once after every other memory fix was in: "What's
        my name?" -> "Your name is Elaina." Their name is in the profile in
        front of her; the model reached for its own instead.
        """
        if not (self._ASKS_THEIR_NAME.search(str(said or ""))
                and self._HER_NAME_AS_THEIRS.search(str(reply or ""))):
            return reply
        for fact in self._what_they_told_her():
            match = re.search(r"name is ([^.(\"]+)", str(fact), re.IGNORECASE)
            name = match.group(1).strip(" '\"") if match else ""
            if not name or "elaina" in name.casefold():
                continue
            print(f"[Memory] Said her own name as theirs; theirs is {name!r}.")
            return (f"이름은 {name}입니다." if self._turn_language == "ko"
                    else f"Your name is {name}.")
        return reply

    @classmethod
    def _names_what_they_meant(cls, reply: str, term: str) -> bool:
        """Whether an answer is about what they said they meant: every word
        of it, Korean by its first two syllables ("텍사스에" is in
        "텍사스주"), English as a whole word ("OPT" is not in "option")."""
        text = str(reply or "")
        words = [w for w in re.findall(r"[^\W_]{2,}", str(term or ""))
                 if w.casefold() not in cls._NOT_A_REFERENT and w not in cls._ONLY_BINDS]
        for word in words:
            if re.match(r"[가-힣]", word):
                if word[:2] not in text:
                    return False
            elif not re.search(rf"(?<![^\W_]){re.escape(word)}(?![^\W_])", text,
                               re.IGNORECASE):
                return False
        return True

    def _with_correction_applied(self, said: str) -> str:
        """Their previous question, with the one they meant in it.

        Measured: "What's the population of Portland?" -> "No, I meant
        Portland, Maine." was answered "Portland, Maine is a great place for
        seafood" -- the correction heard, the question lost -- and "파리
        날씨 어때?" -> "아니 텍사스에 있는 파리 말하는 거야" with "어떤 정보를
        찾으시려는 건가요?". Unchanged when there is nothing to put right.
        """
        text = " ".join(str(said or "").split())
        if not text or len(text.split()) > 14:
            return said
        meant = wrong = ""
        match = self._NOT_THIS_BUT.match(text)
        if match:
            meant, wrong = match.group("meant"), match.group("wrong")
        else:
            for pattern in self._I_MEANT:
                match = pattern.match(text)
                if match:
                    meant = match.group("meant")
                    break
        if not meant:
            return said
        meant = re.sub(r"^(?:그게\s*아니라|그게\s*아니고|그거\s*말고)\s*", "", meant.strip())
        spelled = self._NOT_X.match(meant)
        if spelled:
            meant, wrong = spelled.group("meant"), spelled.group("wrong")
        meant = meant.strip(" ,.\"'“”")
        # "아니 CBT 맞아, 인지행동치료 말하는 거야" confirms what they said and
        # takes back her assumption -- the near-miss repair's to settle, not
        # a different thing they meant.
        if re.search(r"맞아|맞다|맞는|맞고|맞습니다|\b(?:right|correct)\b", meant, re.IGNORECASE):
            return said
        content = [w for w in re.findall(r"[^\W_]+", meant.casefold())
                   if len(w) >= 2 and w not in self._NOT_A_REFERENT]
        previous = self._recent_messages(exclude=text, limit=6, roles=("user",))
        question = previous[-1] if previous else ""
        if not content or not question or question.strip() == text:
            return said
        core = re.sub(r"^(?:a|an|the)\s+", "", meant, flags=re.IGNORECASE)
        corrected = ""
        if wrong:
            spot = re.search(re.escape(wrong.strip(" ,.")), question, re.IGNORECASE)
            if spot:
                corrected = question[:spot.start()] + core + question[spot.end():]
        if not corrected:
            # "I'm going to UW in Seattle." -> "no I mean I'm going to UW in
            # Tacoma": said again, whole. Measured on the contamination
            # matrix: put in at the first word they share, it became "I'm
            # I'm going to UW in Tacoma to UW in Seattle."
            before = {w.casefold() for w in re.findall(r"[^\W_]{2,}", question)}
            words = re.findall(r"[^\W_]{2,}", meant)
            repeated = [w for w in words if w.casefold() in before]
            if len(repeated) >= 2 and len(repeated) * 2 >= len(words):
                new = [w for w in words if w.casefold() not in before]
                if not new:
                    return said
                # What they took back ("Seattle"): a fact already stored from
                # the sentence they corrected is kept out of her profile
                # while the corrected one is still being written.
                gone = before - {w.casefold() for w in words}
                # Which of those was a *value*, and what replaced it. A
                # value is a fact she may have stored; "going" and "to"
                # are not. The first word is skipped for the reason every
                # other reader here skips it: a capital at the start of a
                # sentence says nothing about the word.
                values = gone & {
                    word.casefold()
                    for word in re.findall(r"[^\W_]{2,}", question)[1:]
                    if word[:1].isupper() or word.isdigit()
                }
                instead = {w.casefold() for w in new}
                # An abbreviation they used ("UW"), because the store will
                # have written it out in full and nothing else ties the
                # two sentences together.
                abbreviations = {
                    word for word in re.findall(r"[^\W_]{2,5}", question)
                    if word.isupper()
                }
                self._said_differently = [
                    *getattr(self, "_said_differently", []),
                    (before, gone, values, instead, abbreviations),
                ]
                ending = question.rstrip()[-1:]
                corrected = meant.rstrip(" .!?") + (ending if ending in ".!?" else "")
                core = " ".join(new)
        if not corrected:
            for word in re.findall(r"[^\W_]{2,}", meant):
                spot = re.search(rf"(?<![^\W_]){re.escape(word)}(?![^\W_])", question,
                                 re.IGNORECASE)
                if spot:
                    corrected = question[:spot.start()] + meant + question[spot.end():]
                    break
        if not corrected:
            # Nothing in their last question to put right: a bolted-on
            # "(…)" made "UW 유학생인데 CPT 신청하려고 해 (CBT 맞아, …)".
            return said
        print(f"[Correction] {question!r} -> {corrected!r}")
        self._corrected_from = question
        # What changed, not the whole phrase: "Portland, Maine" is about
        # Maine; "텍사스에 있는 파리" is about 텍사스.
        asked = {w.casefold() for w in re.findall(r"[^\W_]{2,}", question)}
        changed = [w for w in re.findall(r"[^\W_]{2,}", core) if w.casefold() not in asked]
        self._corrected_to = " ".join(changed) or core.strip()
        return corrected

    def _premise_corrected(self, said: str, reply: str) -> str | None:
        """What to say instead, when they took something false for granted
        and her answer did not tell them (brain/premise_check.py).

        Measured on the misunderstanding check: "한국은 엔화 쓰잖아, 환전할
        때 엔으로 바꾸면 되지?" -> "환전 시 엔화 사용은 가능합니다"; "물은
        100도에서 얼잖아" answered with the freezer's temperature and the 100
        left standing -- with a prompt telling her to check the assumption.
        """
        if not str(reply or "").strip() or not told_not_asked.worth_checking(said):
            return None
        if guard_lines.is_fixed_line(reply):
            return None
        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": premise_check.PROMPT},
                    {"role": "user", "content": premise_check.message(said)},
                ],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 220},
                keep_alive=self.keep_alive,
                think=False,
            )
            content = self._value(self._value(response, "message", {}), "content", "")
        except Exception as error:
            print(f"[Premise] Could not check it: {type(error).__name__}: {error}")
            return None
        correction = premise_check.correction(content)
        if not correction or premise_check.already_said(reply, correction, said):
            return None
        # Measured: an English question got its correction in Korean.
        if bool(re.search(r"[가-힣]", correction)) != (self._turn_language == "ko"):
            return None
        if self._turn_language == "ko":
            correction = korean_register.to_formal(correction)
        # Common knowledge is the judge's; a name is not. "Bainbridge has no
        # casino" stays, a casino named beside it goes unless it was checked.
        correction = self._enforce_grounded_entities(
            correction, user_input=said, action_performed=False,
        )
        if not str(correction or "").strip():
            return None
        print(f"[Premise] They took something false for granted; said instead: {correction!r}")
        return correction

    def _answered_on_its_own(self, question: str) -> str:
        """The question answered with no conversation behind it -- what she
        copied from is the history, so the second attempt does not have it."""
        try:
            messages = self._build_factual_messages(question, "", reset_history=True)
            response = self.client.chat(
                model=self.model,
                messages=messages,
                stream=False,
                options={"temperature": 0, "num_predict": 220},
                keep_alive=self.keep_alive,
                think=False,
            )
            text = str(
                self._value(self._value(response, "message", {}), "content", "") or ""
            ).strip()
        except Exception as error:
            print(f"[Answer] Could not answer it again: "
                  f"{type(error).__name__}: {error}")
            return ""
        if self._turn_language == "ko" and text:
            text = korean_register.to_formal(text)
        return text

    def _answer_the_corrected_question(self, question: str, term: str) -> str:
        """The corrected question answered on its own, without the history.

        Measured: "CPT 신청 서류 뭐가 필요해?" -> "아니 CPT 말고 OPT" was
        rewritten to "OPT 신청 서류 뭐가 필요해?", routed as that -- and
        answered with the previous CPT answer word for word, because the
        strongest thing in the prompt was her own last reply. Kept only when
        it names what they meant.
        """
        text = self._answered_on_its_own(question)
        if text and self._names_what_they_meant(text, term):
            print(f"[Correction] The answer was about the old one; answered "
                  f"{question!r} on its own.")
            return text
        return ""

    def _not_her_last_answer(self, said: str, reply: str) -> str:
        """A new question answered with the answer to the last one.

        Measured on the basics check: asked "물은 몇 도에서 끓어?" one turn
        after a question about Fahrenheit, she said "물의 동결점은 32도
        화씨입니다" -- the previous answer, with the new question's own word
        (끓) nowhere in it. Asked again without the history; the second
        attempt is kept only when it says something else.
        """
        previous, _ = self._things_she_has_said()
        if not str(previous or "").strip():
            return reply
        said_again = (
            told_not_asked.repeats(reply, previous)
            or told_not_asked.answers_the_one_before(reply, said, previous)
        )
        if not said_again:
            return reply
        fresh = self._answered_on_its_own(said)
        if (fresh and not told_not_asked.repeats(fresh, previous)
                and not told_not_asked.answers_the_one_before(fresh, said, previous)):
            print(f"[Answer] That was the answer to the question before it; "
                  f"answered {said[:40]!r} on its own.")
            return fresh
        return reply

    _POINTS_AT_NOTHING = (
        re.compile(
            r"^(?:(?:ok(?:ay)?|hey|please|can\s+you|could\s+you|just)\s+)*"
            r"(?:open|close|play|pause|stop|start|delete|remove|send|show|read|buy|"
            r"book|save|download|install|turn\s+(?:on|off)|do|use|try|fix|check|"
            r"run|launch)\s+(?:it|that|this|them|those|these)"
            r"(?:\s+(?:up|out|now|please|for\s+me|again))?\s*[.!?]*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:how\s+(?:long|much)\s+(?:does|will|is|would)\s+(?:it|that|this)"
            r"(?:\s+(?:take|cost|be))?|what\s+about\s+(?:it|that|this)|"
            r"is\s+(?:it|that)\s+(?:good|open|far|expensive|safe|any\s+good))\s*[?.!]*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:그거|이거|저거|그것|이것|저것|그걸|이걸|저걸)\s*(?:을|를|은|는|이|가|도)?\s*"
            r"(?:좀\s*)?(?:열어|닫아|틀어|꺼|켜|해|보여|지워|보내|사|예약|실행|읽어)\S{0,4}"
            r"\s*[.!?~]*$"
        ),
        re.compile(
            r"^(?:그거|이거|저거|그건|이건|저건)\s*(?:은|는|이|가)?\s*"
            r"(?:얼마나\s*걸려|얼마야|얼마|어때|뭐야|괜찮아)\S{0,3}\s*[?.!~]*$"
        ),
    )

    def _refers_to_nothing(self, said: str) -> "TurnRouting | None":
        """A bare "it/그거" before anything has been said for it to mean.

        Only when she has not spoken yet in this conversation: afterwards
        the continuity layer resolves "open it" against what she said, and
        it is right to. Nothing to resolve against is the one case where
        every answer is a guess.
        """
        text = " ".join(str(said or "").split())
        if not text or len(text.split()) > 7:
            return None
        if not any(pattern.match(text) for pattern in self._POINTS_AT_NOTHING):
            return None
        if self._recent_messages(exclude=text, limit=8, roles=("assistant",)):
            return None
        print("[Context] A reference with nothing said yet to refer to; asking.")
        return TurnRouting(
            route=IntentDecision(
                intent="conversation",
                confidence=1.0,
                normalized_request=text,
                reason="A reference to nothing yet said.",
            ),
            user_input=text,
            locked_response=guard_lines.say("refers_to_nothing", self._turn_language),
        )

    def _acknowledgement_if_missed(self, said: str, reply: str) -> str | None:
        """What to say instead, when they told her something about their
        life and her answer does not show she understood it.

        Measured on the misunderstanding check, the ordinary answer to such
        a turn -- with the whole conversation, the profile and the tools in
        its prompt -- missed what was said as often as not: "내 여동생은
        부산에 살아" -> "제가 잘 지내고 있습니다", "My birthday is March
        14th." -> "That will make for a long day.", "저 다음 달에 이사가요"
        -> "다음 달에 이사하시나요?". Then the model is asked one small thing
        with only their sentence in front of it, and what comes back is
        checked (told_not_asked.takes_it_in); failing that, a fixed line.

        A check on the answer, not a route of its own: the turn is routed
        and answered as usual first. Answering it before routing lost what
        the router notes -- "I am going to UW in Seattle." then "I need
        rent near my school" had no school to be near. An answer that
        already took it in is left alone; so is a turn that answers her
        own question, one inside an open recommendation, and one while she
        waits on a yes.
        """
        if not told_not_asked.only_tells(said):
            return None
        if told_not_asked.takes_it_in(reply, said):
            return None
        # "Remember that I'm vegetarian" is an instruction the standing
        # orders keep in about_me.yaml; answering it here skipped them.
        if (
            memory_gate.asks_to_remember(said)
            or memory_gate.asks_to_forget(said)
            or standing_orders.read_instruction(said)[0]
        ):
            return None
        if getattr(self, "_pending_replay", None) is not None:
            return None
        if getattr(self, "_pending_slip", None) is not None:
            return None
        task_sessions = getattr(self, "task_sessions", None)
        if task_sessions is not None and task_sessions.active_recommendation() is not None:
            return None
        previous, _ = self._things_she_has_said()
        if str(previous or "").strip().endswith(("?", "？")):
            return None
        language = self._turn_language
        line = ""
        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": (
                        "You are Elaina, talking with the person in front of "
                        "you. They just told you something about their own "
                        "life. Reply with ONE short, warm sentence that shows "
                        "you understood exactly what they said and that you "
                        "will remember it. It is THEIR life: say \"your ...\" "
                        "(in Korean use 님/분 or no pronoun -- never 우리/내/제 "
                        "for their things). Do not ask a question. Do not "
                        "congratulate them or wish them well as if it were "
                        "happening today. Do not talk about yourself. "
                        + ("Reply in Korean, in 습니다체." if language == "ko"
                           else "Reply in English.")
                    )},
                    {"role": "user", "content": said},
                ],
                stream=False,
                options={"temperature": 0, "num_predict": 70},
                keep_alive=self.keep_alive,
                think=False,
            )
            line = str(
                self._value(self._value(response, "message", {}), "content", "") or ""
            )
        except Exception as error:
            print(f"[Acknowledge] Failed: {type(error).__name__}: {error}")
        line = " ".join(line.split())
        if language == "ko" and line:
            line = korean_register.to_formal(line)
            # The prompt says 님/분 and the model still reaches for 당신,
            # which reads cold: "당신의 여동생이 부산에 살아 있다는 걸
            # 알았습니다." Dropping it leaves the sentence standing.
            line = " ".join(re.sub(r"당신(?:의|은|이|께서|께)?\s*", "", line).split())
        if len(line) > 180 or not told_not_asked.takes_it_in(line, said):
            print(f"[Acknowledge] Did not show it understood ({line[:80]!r}); "
                  "saying the fixed line.")
            line = guard_lines.say("noted_fact", language)
        print(f"[Acknowledge] The answer missed what they told me "
              f"({reply[:60]!r}); said instead: {line[:80]!r}")
        return line

    def _not_told_yet(self, said: str) -> "TurnRouting | None":
        """A question about a detail of their life that nothing they told
        her answers. Measured after a restart, asked "내가 제일 좋아하는
        색깔이 뭐였지?" with a prompt saying never to guess: "파랑이었습니다".
        Answered by a fixed line instead; the model is not asked."""
        if not self.memory_enabled or self.memory_manager is None:
            return None
        if not memory_gate.asks_for_a_personal_detail(said):
            return None
        told = self._what_they_told_her()
        if told and memory_gate.shares_a_topic(said, told):
            return None
        # "Never told" cannot be said while what they told her is still
        # being written -- measured on the contamination matrix, "where is
        # my school again?" came a second after "I'm going to UW in Tacoma"
        # -- nor about something said in this conversation, which is in
        # front of her anyway.
        if any(thread.is_alive() for thread in getattr(self, "_memory_stores", [])):
            return None
        said_here = [
            line for line in self._recent_messages(exclude=said, limit=12, roles=("user",))
            if memory_gate.carries_something_to_remember(line)
        ]
        if said_here and memory_gate.shares_a_topic(said, said_here):
            return None
        print("[Memory] Asked about something they never told me; saying so.")
        return TurnRouting(
            route=IntentDecision(
                intent="conversation",
                confidence=1.0,
                normalized_request=said,
                reason="A personal detail they have not told her.",
            ),
            user_input=said,
            locked_response=guard_lines.say("not_told_yet", self._turn_language),
        )

    def _replaces_what_they_took_back(self, similar, content: str):
        """The stored memory this new one corrects, if it corrects one.

        The consolidator is a model being asked whether two sentences say
        the same thing, and a correction looks exactly like a duplicate to
        it. Measured on the contamination matrix, 2026-09-23: after "no I
        mean I'm going to UW in Tacoma" it was shown the Seattle memory it
        already had, answered IGNORE, and nothing was written -- so the
        only fact in the database still said Seattle, and "where is my
        school again?" was answered from it two turns later.

        Both halves are required, so this can only ever replace the fact
        it is about: the stored one has to be something they took back,
        and the new one has to say what they replaced it with.
        """
        taken_back = getattr(self, "_said_differently", None) or []
        if not taken_back:
            return None
        said = {w.casefold() for w in re.findall(r"[^\W_]{2,}", str(content))}
        for item in similar or ():
            stored = str(getattr(item, "content", "") or "")
            if not stored or self._without_what_they_took_back([stored]):
                continue
            for _before, _gone, _values, instead, _abbreviations in taken_back:
                if instead & said:
                    return item
        return None

    def _store_memory_candidate(self, user_input: str) -> None:
        """Perform expensive extraction/consolidation outside response latency."""
        if (
            not self.memory_enabled
            or self.memory_manager is None
            or self.extractor is None
            or self.consolidator is None
        ):
            return
        if memory_gate.is_question(user_input):
            # Asking is not telling -- here too, not only where the router
            # calls this. Measured after a restart: the extractor declined
            # "우리 강아지 이름 뭐였지?" and "내 여동생 어디 산다고 했지?",
            # the durable pattern matched "강아지 이름", and both questions
            # were kept as memories of the person. The profile they
            # polluted then made "제일 좋아하는 색깔이 뭐였지?" look like
            # something she had been told, so the fixed "you haven't told
            # me" line never ran and the model guessed 파랑.
            return
        with self._memory_store_lock:
            started = time.perf_counter()
            try:
                memory = self.extractor.extract(user_input)
                # The extractor's "save" is a model call, and it is the
                # last model boolean left in the memory path. A7 replaced
                # the two above it for exactly this reason and stopped one
                # layer short. Measured on an unseen dogfood turn: "i'm
                # vegetarian by the way" opened the gate, started the
                # store, and was dropped here -- the database afterwards
                # held "The user is doing well." and not the diet. She
                # then said "I don't have anything saved about that",
                # which was honest and was the wrong answer.
                #
                # The deterministic gate already decided this sentence
                # states something durable. A model declining to agree
                # does not un-say it.
                worth_keeping = memory_gate.carries_something_to_remember(
                    user_input
                )
                if not memory["save"] and not worth_keeping:
                    return
                if not memory["save"]:
                    print(
                        "[Memory] The extractor declined; the gate did not. "
                        f"Keeping: {user_input[:60]!r}"
                    )
                if not str(memory.get("content", "")).strip():
                    # No phrasing came back with the refusal, so their own
                    # sentence is the memory. Verbatim on purpose: a
                    # paraphrase of something never paraphrased before is
                    # a second chance to get it wrong.
                    memory = {
                        **memory,
                        "content": " ".join(str(user_input).split()),
                        "category": memory.get("category") or "personal",
                    }

                # The extractor writes English, and a Korean name does not
                # survive the trip: measured, "젠레스 존 제로" was kept as
                # "Genres Zero" and a dog called 콩 as "Kongi" -- so "무슨
                # 게임 한다고 했지?" could only ever be answered wrongly. What
                # they said is kept beside what the extractor made of it.
                said = " ".join(str(user_input).split())
                content = str(memory.get("content", ""))
                if (
                    re.search(r"[가-힣]", said)
                    and not re.search(r"[가-힣]", content)
                    and said not in content
                ):
                    memory = {
                        **memory,
                        "content": f'{content} (in their words: "{said[:160]}")',
                    }

                similar = self.memory_manager.search_memory_objects(
                    memory["content"]
                )
                result = self.consolidator.consolidate(
                    similar,
                    memory["content"],
                )
                action = result["action"]
                corrects = self._replaces_what_they_took_back(
                    similar, memory["content"],
                )
                if corrects is not None and action != "UPDATE":
                    print(
                        "[Memory] They took that back; putting the "
                        f"correction in its place: {memory['content'][:60]!r}"
                    )
                    self.memory_manager.update_memory(
                        corrects.id, memory["content"],
                    )
                elif action == "ADD":
                    self.memory_manager.store_memory(
                        content=memory["content"],
                        category=memory["category"],
                        importance=5,
                    )
                elif action == "UPDATE":
                    # An UPDATE *overwrites a row*, so the model proposes and
                    # this decides. Measured: it overwrote the education
                    # memory with an unrelated game habit, and the row kept
                    # the education label while the fact was gone.
                    target = next(
                        (
                            row for row in similar
                            if getattr(row, "id", None) == result.get("memory_id")
                        ),
                        None,
                    )
                    if target is not None and same_fact(
                        getattr(target, "content", ""), memory["content"],
                    ):
                        self.memory_manager.update_memory(
                            result["memory_id"],
                            result["content"],
                        )
                    else:
                        print(
                            "[Memory] The consolidator wanted to overwrite a "
                            "different fact; keeping both: "
                            f"{memory['content'][:60]!r}"
                        )
                        self.memory_manager.store_memory(
                            content=memory["content"],
                            category=memory["category"],
                            importance=5,
                        )
                elif any(
                    same_fact(getattr(row, "content", ""), memory["content"])
                    for row in similar
                ):
                    # Genuinely already known. Visible, because it was not:
                    # a Korean memory among English ones was consolidated
                    # away in silence and the restart could not say which
                    # school.
                    print(
                        "[Memory] Already known; not kept again: "
                        f"{memory['content'][:60]!r}"
                    )
                else:
                    print(
                        "[Memory] The consolidator called it a duplicate of "
                        f"nothing it had; keeping it: {memory['content'][:60]!r}"
                    )
                    self.memory_manager.store_memory(
                        content=memory["content"],
                        category=memory["category"],
                        importance=5,
                    )
                self._profile_cache = None
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

    def _subject_now(self) -> str:
        """What the conversation is about, from whichever layer holds it.

        The focus is only written by the task planner, so on an ordinary
        conversational turn it is empty and the open recommendation is the
        one carrying a subject. Asking both is what makes the answer
        available on a chat turn at all.
        """
        for holder in (
            self.task_sessions.focus(),
            self.task_sessions.active_recommendation(),
        ):
            subject = str(getattr(holder, "subject", "") or "").strip()
            if subject:
                return subject
        return ""

    def _state_for_trace(self) -> dict:
        """The conversation state a turn's record needs, read and not changed.

        Read only while a record is open (core/turn_trace.py). A state
        that cannot be read is written down as unreadable rather than
        allowed to reach the turn.
        """
        state: dict = {}
        readers = (
            ("subject_before_turn", lambda: self._subject_before_turn),
            ("open_problem", lambda: getattr(
                self.task_sessions.active_recommendation(), "subject", None,
            )),
            ("held_results", lambda: len(self.task_sessions.results())),
            ("offer_pending", lambda: self.action_ledger.offer_pending),
            ("awaiting_clarification",
             lambda: self.clarification.peek() is not None),
            ("history_messages", lambda: len(self.conversation.get_history())),
        )
        for name, read in readers:
            try:
                state[name] = read()
            except Exception as error:
                state[name] = f"<unreadable: {type(error).__name__}>"
        return state

    def _context_for_turn(self, route, goal) -> TurnContext:
        """What this turn may carry, decided once for every builder.

        The one place that answers "does the conversation so far belong to
        this turn". Before it, three prompt builders each answered
        separately and disagreed -- one of them by never asking.
        """
        # Whichever authority actually holds a subject. The focus is only
        # written by the task planner (`remember`), so on an ordinary
        # conversational turn it is empty -- which is why the first version
        # of this rule never fired once in twelve measured cases. The open
        # recommendation is the one that carries a subject through chat.
        held_subject = getattr(self, "_subject_before_turn", "")
        current = str(getattr(goal, "subject", "") or "") or route.topic

        # Read before the model's own label, because the person saying so
        # outranks a classifier agreeing. "actually forget the mouse,
        # what's a good film tonight?" names the subject it is dropping,
        # and A3's case for exactly that shape passed three runs in four
        # -- the fourth answered "The one I actually found is Best
        # Wireless Gaming Mouse under $50." A signal that is right two
        # times in three is not a gate.
        dropped = supersession.drops_a_named_subject(
            route.normalized_request or ""
        )
        if route.intent == "clarification":
            # "I still don't get it", "what do you mean?": about the answer
            # just given, so the conversation is the whole of what it
            # means. A router that reads it as a new subject, or a subject
            # comparison that finds none, must not take that away -- that
            # is how the 27B answered "What part is unclear?" to half its
            # follow-ups (docs/PHASE3_PLAN.md R9).
            reason = ""
        elif dropped:
            reason = f"the turn drops {dropped!r} and asks something else"
        elif route.topic_shift:
            reason = "the router says the topic moved"
        elif held_subject and current and not context_policy.subjects_agree(
            held_subject, current,
        ):
            reason = (
                f"the turn is about {current!r}, the conversation was about "
                f"{held_subject!r}"
            )
        else:
            reason = ""

        return TurnContext(
            question=route.normalized_request or "",
            inherit_history=not reason,
            include_grounded=self._grounded_context_is_relevant(route, goal),
            followup_subject=self._followup_subject_for(route, goal),
            reason=reason,
            held_subject=held_subject,
            current_subject=current,
        )

    def _history_belongs_to_this_turn(self, route, goal) -> bool:
        """Whether the conversation so far is about what is being asked now.

        ``route.topic_shift`` is the model's answer to this and it is not
        reliable: measured live, "what's 2+2" after four turns about not
        sleeping came back with topic_shift unset, inherited the sympathy,
        and never produced the number.

        ``context_policy.subjects_agree`` is the deterministic answer, and
        it is already trusted for exactly this decision one layer over --
        it decides whether stored evidence is about the current subject.
        Evidence and history are the same question about the same two
        subjects, so they get the same answer.

        Either subject being unknown is not a disagreement. Nothing is
        reset on a guess; the old behaviour is what happens when the
        comparison cannot be made.
        """
        focus = self.task_sessions.focus()
        held = str(getattr(focus, "subject", "") or "") if focus else ""
        current = str(getattr(goal, "subject", "") or "") or route.topic
        if not held or not current:
            return True
        agrees = context_policy.subjects_agree(held, current)
        if not agrees:
            print(f"[Context] The turn is about {current!r}, the "
                  f"conversation was about {held!r}; not inheriting it.")
        return agrees

    def _should_reset_history(self, route, goal) -> bool:
        """Whether this turn starts clean."""
        return bool(route.topic_shift) or not self._history_belongs_to_this_turn(
            route, goal,
        )

    def _ledger(self) -> turn_evidence.EvidenceLedger:
        """This turn's evidence (brain/evidence.py).

        Created per turn in ``chat``; created here for an engine built
        without ``__init__`` or a handler reached outside ``chat``.
        """
        ledger = getattr(self, "_turn_evidence", None)
        if ledger is None:
            ledger = self._turn_evidence = turn_evidence.EvidenceLedger()
        return ledger

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
            self._ledger().add(
                turn_evidence.GROUNDED, grounded_context,
                source="recent verified context", carried=True,
            )
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
        *,
        inherit_history: bool = True,
        evidence_kind: str = turn_evidence.TOOL_RESULT,
    ) -> list[dict]:
        """Let personality.txt phrase a trusted action result for the user.

        ``inherit_history`` was missing entirely, and this is the path a
        successful calculation plan takes. Measured live: "what's 2+2"
        after four turns about not sleeping was answered "That's
        straightforward." -- the sympathy came in with the history and the
        number never came out. The other two builders could already drop
        history; this one could not, so no rule added to them reached it.
        """
        self._ledger().add(evidence_kind, tool_result,
                           source="trusted tool result")
        return build_personality_messages(
            system_prompt=self.system_prompt,
            history=self.conversation.get_history() if inherit_history else [],
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

    def _note_standing_instruction(self, user_input: str, *, kinds=()) -> str:
        """Write down a rule the person has just stated, and say so.

        The whole point is that they say it once. So the confirmation
        names what was written and where, because a rule you cannot see is
        a rule you cannot correct -- and both files are theirs to open.
        """
        kind, first, second = standing_orders.read_instruction(user_input)
        if not kind or (kinds and kind not in kinds):
            return ""

        if kind == "repair":
            if not self.standing_orders.remember_repair(first, second):
                return ""
            print(f"[Standing Orders] +say {first!r} -> {second!r}")
            return guard_lines.say("standing_repair", self._turn_language).format(
                first=first, second=second,
            )
        if kind == "fact":
            if not self.standing_orders.remember_fact(first):
                return ""
            print(f"[Standing Orders] +fact {first!r}")
            return guard_lines.say("standing_fact", self._turn_language)
        if kind == "note":
            if not self.standing_orders.remember_note(first):
                return ""
            print(f"[Standing Orders] +note {first!r}")
            return guard_lines.say("standing_note", self._turn_language).format(
                first=first,
            )
        if kind == "forget":
            rules, facts = self.standing_orders.forget(first)
            if not rules and not facts:
                return ""
            print(f"[Standing Orders] -{rules} rule(s), -{facts} fact(s)")
            return guard_lines.say("standing_forget", self._turn_language).format(
                first=first,
            )
        return ""

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

    def _research_for_recommendation(
        self, query: str, *, resolution=None, resolved=None, said: str = "",
    ):
        """Find candidates, check them, and only then call any of them good.

        The cascade, in order, stopping as soon as the answer is settled:

            search -> is this even a candidate -> deterministic checks
            -> reject conflicts -> targeted re-search if something
            important is still unevidenced -> semantic check if it still
            is -> rank -> recommend, or say the evidence is not there

        Returns ``None`` whenever this is not a constrained recommendation,
        so every other lookup keeps the ordinary research path untouched.
        """
        if candidate_fit.asks_for_a_method(said):
            # "How do I make it tasty" is answered from what the pages say,
            # not by ranking them as though one were the answer. Ranking is
            # what put a GIF and a blog headline forward as picks; the
            # ordinary research path hands the same pages to the model as
            # evidence, which is what a method question needs.
            print("[Recommendation] The turn asks how to do something; "
                  "researching it rather than ranking candidates.")
            return None
        problem = resolved.task if resolved is not None else self.task_sessions.active_recommendation()
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

        # Pages about several things are sources; the things themselves are
        # what a recommendation is made of. When the search has not returned
        # enough of the latter, read them out of the former rather than
        # searching a third time for more pages.
        if len(candidate_fit.viable(fits)) < 3:
            surface_log.note("[Acquisition]")
            surface_log.note(f"  Query: {first[:70]!r}   shape={shape}")
            surface_log.note("  [Search Result]")
            for fit in fits:
                # SOURCE and CANDIDATE are the distinction that matters
                # here: a page about several things is somewhere to read,
                # never something to recommend.
                if fit.kind == acquisition.SOURCE_SURFACE:
                    reading = "SOURCE   "
                elif fit.kind == acquisition.OFF_TARGET:
                    reading = "DROP     "
                elif self._is_a_page_about_things(fit, problem):
                    reading = "SOURCE   "
                else:
                    reading = "CANDIDATE"
                surface_log.note(f"    {reading} {fit.name[:58]}")
            entities = self._entities_from(fits, problem, shape)
            if entities:
                queries.append("entity discovery")
                fits = candidate_fit.evaluate(
                    [
                        {
                            "title": entity["title"],
                            "url": entity["url"],
                            "summary": entity["summary"],
                        }
                        for entity in entities
                    ] + [
                        {"title": fit.name, "url": fit.url,
                         "summary": fit.summary}
                        for fit in fits
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
        # The whole fit, not its name. The URL, the ranking reason and the
        # verdict were all worked out a line above and used to be dropped
        # here -- which is why a later "open the second one" could count to
        # a position and find nothing to open.
        # Only things, never pages.
        #
        # This used to be ``fitting or viable(fits)``, and the fallback was
        # how every source reached recommendation state: measured live, all
        # three of "find me some good hotels in Seoul", "recommend me some
        # mechanical keyboards" and "find me a good gaming monitor" came
        # back with FITS=0, so ``viable`` was recorded instead -- and
        # ``viable`` only means "the right kind of thing, contradicting
        # nothing", which an aggregator's front page satisfies.
        #
        # A source is still kept as evidence; it simply is not a candidate.
        # Fewer real ones is the better failure: an honest two beats two
        # plus a magazine article.
        # Everything of the right kind that contradicts nothing, whether or
        # not a source happened to state a quality about it.
        #
        # This used to prefer ``fitting`` and drop the rest, which meant one
        # result confirming a constraint discarded every other real thing
        # found alongside it. Measured live on "recommend me some 1440p
        # monitors": eight results, two confirmed, and the ASUS ROG Swift
        # PG27AQWP-W and Alienware AW2524HF -- both read out of round-ups,
        # both verified to their own pages -- were thrown away for being
        # merely unchecked. Not having had its price confirmed is not a
        # reason to pretend a monitor was never found.
        #
        # They are already ranked with the confirmed ones first, and the
        # page filter is what keeps sources out. Verification decides the
        # order and what may be *claimed*, never what exists.
        recordable = [
            fit for fit in candidate_fit.viable(fits)
            if not self._is_a_page_about_things(fit, problem)
        ]
        found_set = result_state.from_fits(
            recordable,
                kind=(
                    result_state.PLACE if shape == candidate_fit.PLACE
                    else result_state.PRODUCT if shape == candidate_fit.PRODUCT
                    else result_state.UNKNOWN
                ),
            source="web_search",
            query=query,
        )
        # Where each one came from, and how far it got. A name read out of a
        # round-up carries that round-up's address as well as its own page,
        # so a later turn can say "these are the ones I found" without
        # implying anything was checked that was not.
        origins = getattr(self, "_entity_origins", {}) or {}
        self.task_sessions.record_candidates(
            tuple(
                replace(
                    item,
                    source_urls=tuple(dict.fromkeys(
                        item.source_urls
                        + ((origins[item.name.casefold()][0],)
                           if origins.get(item.name.casefold(), ("", ""))[0]
                           else ())
                    )),
                    state=origins[item.name.casefold()][1],
                )
                if item.name.casefold() in origins else item
                for item in found_set.items
            ),
            evidence=(candidate_fit.shortlist_text(fits),),
        )
        self._entity_origins = {}

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

    # How many discovered names are worth chasing. Reading them is free --
    # it is text a search already returned -- and verifying each is one more
    # search, run in parallel, so this bounds the only part that costs
    # anything. Four is two more than a shortlist needs.
    ENTITY_LIMIT = 4

    def _discovery_query(self, problem) -> str:
        """A query whose answers *name* things, rather than sell them.

        The other queries in this layer look for the thing itself, which is
        right when the thing has a page of its own -- a hotel does. A
        keyboard usually does not, at least not one a search will rank
        above the shop that sells it, and "mechanical keyboards price buy"
        returns five storefronts and no model. What names models is the
        round-up: the pages the surface layer refuses to put on cards are
        exactly the pages that say "the one we picked is the Keychron Q5
        Max".

        So this asks for those on purpose. It is discovery, not
        verification: what comes back is expected to be writing about
        several things, and what is wanted from it is their names.
        """
        thing = str(getattr(problem, "subject", "") or "").strip()
        try:
            thing = problem.search_query() or thing
        except Exception:
            pass
        thing = " ".join(str(thing).split())
        if not thing:
            return ""
        # Its own qualities stay; the shape words a listing query needs
        # ("price buy", "reviews address") would pull this back towards
        # shops, which is what it exists to avoid.
        for noise in (" price buy", " reviews address"):
            thing = thing.replace(noise, "")
        if re.search(r"\b(?:best|top)\b", thing, re.IGNORECASE):
            return thing
        return f"best {thing}".strip()

    def _is_a_page_about_things(self, fit, problem) -> bool:
        """Whether this result is a way of finding things, not one of them.

        The last check before something becomes a candidate, and it asks
        the acquisition layer's question rather than the surface layer's:
        the surface refuses a *card*, which is a rendering decision, while
        this refuses a *candidate*, which is what Elaina will talk about.
        Both boundaries are wanted; neither replaces the other.
        """
        if fit.kind != acquisition.CANDIDATE:
            return True
        if re.match(r"^\s*(?:https?://|www\.)", str(fit.name or "")):
            # An address is where a thing is, not what it is called.
            # Measured live: a raw Naver Map link arrived as a restaurant's
            # name and would have been read out as one.
            return True
        if candidate_fit.restates_the_search(fit.name, fit.url, problem):
            return True
        if candidate_fit.off_target(
            fit.name, fit.url, fit.summary,
            candidate_fit.expected_shape(problem),
        ):
            return True
        # And the acquisition layer's own question. Measured live: "The
        # Best Hotels in Seoul, From Gangnam to Hongdae" carries no colon
        # and no year, so the round-up title pattern did not match it, and
        # it entered candidate state as a hotel. It is plural where the
        # request was singular, which is the whole of what is wrong with it.
        return entity_discovery.names_the_category(
            fit.name, self._subject_words(problem),
        )

    def _subject_words(self, problem) -> str:
        """What this turn is about, in the words it is actually about it in.

        The stored subject alone is not enough. Measured live: a turn asking
        for restaurants in Gangnam still held "gaming monitor" as its
        subject from the turn before, so "The 10 Best Korean Restaurants in
        Gangnam-gu" did not look like the category to anything checking
        against it, and two round-ups entered candidate state. The query
        that was actually sent is the more current of the two, and using
        both costs nothing.
        """
        parts = [
            str(getattr(problem, "subject", "") or ""),
            str(getattr(problem, "location", "") or ""),
        ]
        try:
            parts.append(str(problem.search_query() or ""))
        except Exception:
            pass
        return " ".join(part for part in parts if part)

    def _entities_from(self, fits, problem, shape):
        """The individual things named inside pages that are about several.

        A source is not a candidate, but it is full of candidates' names: a
        round-up says which keyboard it picked, a listing page says which
        hotel it is showing. This reads those out of evidence already
        retrieved -- never inventing one, only ever taking a literal span of
        text that came back -- and then searches for each so it arrives with
        a page of its own instead of borrowing the article's.

        Discovery and verification are deliberately separate. A name whose
        own page cannot be found is still returned: being named in real
        evidence is enough to exist, and throwing away a real hotel for want
        of a price is the failure this layer was built to stop.
        """
        subject = self._subject_words(problem).strip()
        known = {str(fit.name or "").casefold() for fit in fits}
        # A round-up is a source worth reading -- "The 16 Best Seoul Hotels"
        # is sixteen hotel names -- but a video platform is not. What is on
        # a channel page is the channel: measured live, discovery on YouTube
        # results produced "Gyan Therapy585K" and "LakhVenom's Tech464K",
        # which are subscriber counts wearing a name.
        readable = [
            fit for fit in fits
            if not acquisition.is_publishing(fit.url)
        ]
        found = [
            (name, source) for name, source in entity_discovery.from_results(
                [
                    {"title": fit.name, "url": fit.url, "summary": fit.summary}
                    for fit in readable
                ],
                subject=subject,
            )
            if name.casefold() not in known
        ][:self.ENTITY_LIMIT]
        if len(found) < 2:
            # Nothing named in what came back. One more search, worded to
            # find pages that *name* things rather than pages that sell
            # them: a round-up says which keyboard it picked, where a
            # storefront query returns the storefront. This is the second
            # of the two bounded attempts, and it is a different strategy
            # rather than the same query again.
            discovery = self._discovery_query(problem)
            if discovery:
                surface_log.note(f"  discovery query: {discovery!r}")
                try:
                    more = self.research_agent.research_structured(
                        search_query=discovery, max_results=6,
                        query_is_resolved=True,
                    )
                except Exception:
                    more = ()
                extra = [
                    (name, source)
                    for name, source in entity_discovery.from_results(
                        [
                            result for result in (more or ())
                            if not acquisition.is_publishing(
                                str(result.get("url", "") or ""),
                            )
                        ],
                        subject=subject,
                    )
                    if name.casefold() not in known
                ]
                for pair in extra:
                    if pair[0].casefold() not in {
                        name.casefold() for name, _ in found
                    }:
                        found.append(pair)
                found = found[:self.ENTITY_LIMIT]
        if not found:
            return ()

        for name, source in found:
            surface_log.note(
                f"  read {name!r} out of {acquisition.host_of(source)}"
            )

        def verify(pair):
            """One search, for one name, to find its own page."""
            name, source = pair
            try:
                results = self.research_agent.research_structured(
                    search_query=name, max_results=3, query_is_resolved=True,
                )
            except Exception:
                results = ()
            for result in results or ():
                url = str(result.get("url", "") or "")
                if acquisition.classify(url) != acquisition.CANDIDATE:
                    continue
                if not entity_discovery.is_about(
                    name, f"{result.get('title', '')} {url}",
                ):
                    continue
                # The name stays the one that was discovered. The page is
                # the page; its title is a title, and titles are how a
                # round-up got in here in the first place.
                return {
                    "title": name,
                    "url": url,
                    "summary": str(result.get("summary", "") or ""),
                    "source_url": source,
                    "state": result_state.VERIFIED,
                }
            return {
                "title": name, "url": "", "summary": "",
                "source_url": source, "state": result_state.DISCOVERED,
            }

        try:
            with ThreadPoolExecutor(max_workers=self.ENTITY_LIMIT) as pool:
                entities = list(pool.map(verify, found))
        except Exception:
            entities = [
                {"title": name, "url": "", "summary": "",
                 "source_url": source, "state": result_state.DISCOVERED}
                for name, source in found
            ]
        for entity in entities:
            surface_log.note("  [Candidate]")
            surface_log.note(f"    {entity['title']}")
            origin = acquisition.host_of(entity["source_url"])
            surface_log.note(f"    origin: {origin or 'search result'}")
            surface_log.note(f"    state: {entity['state']}")
            if entity["url"]:
                surface_log.note(f"    page: {entity['url'][:64]}")
        # Kept so provenance survives the trip through the fit layer, which
        # knows about pages and not about where a name was read.
        self._entity_origins = {
            str(entity["title"]).casefold(): (
                entity["source_url"], entity["state"],
            )
            for entity in entities
        }
        return tuple(entities)

    def _candidates_for(
        self, query: str, problem, shape: str, *, preferred_source: str = "",
    ):
        """One structured search, read as candidates, surfaces or neither."""
        try:
            found = self.research_agent.research_structured(
                search_query=query, max_results=6, query_is_resolved=True,
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
                            max_results=6, query_is_resolved=True,
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
            question = problem.question_for(nxt, language=self._turn_language)
            self.clarification.offer(
                goal=Goal(kind="recommendation", utterance=problem.subject),
                slot=nxt,
                question=question,
                task_id=problem.id,
            )
            self.task_sessions.note_dimension_asked(nxt)
            received = (
                "알겠습니다." if self._turn_language.startswith("ko")
                else "Got it."
            )
            spoken = f"{received} {question}"
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
        question = problem.question_for(dimension, language=self._turn_language)
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

    def _reselect_for_options(self, route, goal, *, options: bool = True):
        """Ask the same two layers again, with evidence declared necessary.

        Nothing is decided here that they do not decide -- the route is
        restated with the one fact the router missed (this turn wants real
        options), and interaction/capability run again unchanged.

        ``options=False`` wants evidence and not options: a question to be
        looked up, not a choice to be made. Measured in the English slip
        demo: a corrected "what documents do I need for CPT?" escalated as
        options, the open recommendation took the search results as
        candidates, and she answered "That's done. CPT Application Process
        is the one I'd start with."
        """
        asking = replace(
            route,
            intent="web_search",
            computer_operation="",
            requires_external_evidence=True,
            recommendation_needed=options,
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
            follow_up=bool(getattr(route, "is_follow_up", False)),
            location=(
                self.task_sessions.focus().background.get("location", "")
                if self.task_sessions.focus() is not None else ""
            ),
            anchor=(
                self.task_sessions.focus().background.get("about", "")
                if self.task_sessions.focus() is not None else ""
            ),
            # "no Zillow" right after "Zelo is open" is fixing a misheard
            # name, not banning a site. Only her own previous words can
            # tell the two apart.
            said_before=self._last_claim(),
        )

        self._recommendation_restarted = (
            before is not None and problem.turns <= 1
        )
        if before is not None and problem.id != before.id:
            # The conversation has moved to a different problem, so an
            # offer made about the old one is no longer answerable.
            #
            # "Want me to look up some monitors?" -- then "actually find me
            # restaurants in Gangnam" -- then "yeah". Without this the
            # "yeah" reaches back past the restaurants and runs the monitor
            # lookup, which is consent given for one thing spent on
            # another. The 90-second expiry was the only thing standing
            # between the two, and time is not the boundary that matters.
            pending = self.capability_offer.peek()
            if pending is not None and not self.capability_offer.belongs_to(
                problem.id,
            ):
                surface_log.note("[Task Lifecycle]")
                surface_log.note(
                    f"  dropped offer: {pending.capability_id or pending.goal} "
                    f"(offered for task {pending.task_id[:8] or '?'})"
                )
                self.capability_offer.clear()
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

    def _said_in_the_turns_language(self, query: str, said: str) -> str:
        """The query, in the language the person was actually speaking.

        Last, so it applies to whatever the layers above chose. The words
        put back are the person's own, which is the one string guaranteed
        to be in the right language and to carry the proper nouns in the
        form they used -- 워싱턴 대학교 rather than the router's
        "Washington University in Seattle", which is a different school in
        St. Louis.
        """
        rewritten, why = search_language.in_the_turns_language(
            query, said=said, language=self._turn_language,
        )
        if rewritten != query:
            print("[Query]")
            print(f"  source: the turn's own words ({why})")
            print(f"  text: {rewritten}")
            return rewritten
        if why:
            # Declined, and out loud. The turns this cannot help are the
            # ones worth counting.
            print(f"[Query] kept the router's wording: {why}.")
        return query

    def _resolved_search_query(self, route, goal, *, said: str = "") -> str:
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

        # A held problem builds its query from *its own* subject, and the
        # turn's named entity is appended to that. When the conversation
        # has moved on and nothing retired the problem, the two are glued
        # together and searched as one thing. Measured in a real Korean
        # session, three turns after the subject changed from the Korean
        # War to the user's university:
        #
        #     [Query] source: active_task
        #             text: 6/25 war Washington University Seattle
        #     [Context] Inheriting the conversation
        #               (was Washington University in Seattle, now ...)
        #
        # The conversation layer had the subject right. The query did not,
        # and the contaminated string was then *cached*, so every later
        # question in the same thread reused it -- "6/25 war Washington
        # University Bill Gates Seattle" two turns further on.
        #
        # Same rule the history and the held results already follow: both
        # subjects have to be known before a disagreement counts, so an
        # ordinary subjectless follow-up ("which one?") still inherits.
        #
        # The goal layer's subject, and only that -- ``route.topic`` is a
        # filing label written per turn, and the test below is specified
        # on noun phrases rather than on sentences. Falling back to the
        # task on an unknown subject is the safe direction; falling back
        # to the router's own query for this turn is the safe direction
        # when they disagree.
        builder = problem
        if builder is not None:
            held_subject = str(getattr(builder, "subject", "") or "").strip()
            turn_subject = str(getattr(goal, "subject", "") or "").strip()
            if context_policy.names_a_different_subject(
                held_subject, turn_subject,
            ):
                print(
                    f"[Query] the open task is about '{held_subject}' and this "
                    f"turn is about '{turn_subject}'; not building from it."
                )
                builder = None
        if builder is not None and (
            builder.constraints or builder.lookup_requested
        ):
            resolved = builder.search_query(request or router_query)
            if resolved:
                # Strong task and conversation context comes first. Locale is
                # only a fallback, so it cannot suppress a known destination.
                resolved = self._with_focus(
                    resolved, focus, include_subject=False,
                )
                resolved = self._localised(builder, resolved, focus=focus)
            if resolved and resolved.casefold() != router_query.casefold():
                print("[Query]")
                print("  source: active_task")
                print(f"  text: {resolved}")
                if builder.constraints:
                    # This is the branch that exists to carry words the
                    # turn does not have. Swapping in the turn's own words
                    # would drop the three turns of constraints that are
                    # the entire reason it was built.
                    return resolved
                return self._said_in_the_turns_language(resolved, said)
        return self._said_in_the_turns_language(
            self._localised(
                problem, self._with_focus(
                    router_query or self._search_subject(route, goal), focus,
                ), focus=focus,
            ),
            said,
        )

    def _localised(self, problem, query: str, *, focus=None) -> str:
        """Add the user's market, unless the query already says where.

        The place test used to be re-derived from the query text, which
        cannot see a name it does not already know. Measured live:

            [Query] studio apartments University of Washington $1,500
                    in South Korea

        "University of Washington" is where, and the locale layer had no
        way to tell -- so it appended the user's own country to a search
        about Seattle. Nothing in the text needs interpreting: the caller
        knows whether it just put a place in, because it is holding it.
        """
        placed = bool(
            str(getattr(problem, "location", "") or "").strip()
            or str(getattr(problem, "anchor", "") or "").strip()
            # An area the turn itself supplied is the most direct answer
            # there is to "did they say where". It used to be reached only
            # through the conversation's background location, which meant
            # the guard depended on a fact left over from an older topic --
            # and when that fact was retired, "studio apartments University
            # of Washington" collected "in South Korea" again.
            or bool(getattr(problem, "values", lambda *_: ())(
                recommendation_state.AREA,
            ))
        )
        if not placed and focus is not None:
            placed = bool(focus.background.get("location", ""))
        return self.user_locale.localize_query(
            query,
            category=getattr(problem, "category", ""),
            assume_local=getattr(problem, "real_world", False),
            already_placed=placed,
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

    # Turns that are asking for the set to be weighed rather than listed.
    _WANTS_COMPARISON = re.compile(
        r"\bcompare\b|\bside\s+by\s+side\b|\bwhich\s+is\s+better\b"
        r"|\bdifference\s+between\b|\bversus\b|\bvs\.?\b",
        re.IGNORECASE,
    )

    def _surface_for(self, user_input: str, *, searched: bool) -> "surfaces.Surface":
        """The structured half of this reply, or nothing at all.

        Decided here rather than in Electron, and that is the whole point of
        the protocol: the layer that knows whether a real result set exists
        is the layer that found it. A renderer inferring cards from the word
        "hotel" in a sentence is how a UI starts disagreeing with the
        conversation it is attached to.

        Conservative by construction. A surface appears only when this turn
        genuinely has several checkable things in hand -- so ordinary
        conversation, a single recommendation and a failed search all stay
        exactly as they were, which is what keeps ``none`` the common case.
        """
        def no(reason: str) -> "surfaces.Surface":
            # Every way of staying flat says so. Silence and "the code never
            # ran" look identical from outside, and telling them apart by
            # reading the source cost a whole debugging pass once already.
            surface_log.note(f"[Surface] none: {reason}.")
            return surfaces.NOTHING

        # Recorded before any decision, so that "nothing about this turn in
        # the log" means something definite: the surface layer was never
        # reached, rather than reached and declined.
        surface_log.note(
            f"[Surface] considering: said={str(user_input or '')[:70]!r} "
            f"searched={searched} held={len(self.task_sessions.results())} "
            f"recommendation="
            f"{self.task_sessions.active_recommendation() is not None}"
        )
        held_now = self.task_sessions.results().items
        subject_now = str(getattr(
            self.task_sessions.active_recommendation(), "subject", "",
        ) or "") or str(user_input or "")
        for candidate, (card, refused) in zip(
            held_now, surfaces.refusals(held_now, subject_now),
        ):
            surface_log.note(
                f"[Surface]   {candidate.verdict or '(none)':<10} "
                f"{('DROP: ' + refused) if refused else 'card':<28} "
                f"{card[:64]!r}"
            )

        if self._browser_result_is_final:
            # A live page is already its own surface, and a shortlist drawn
            # over it would describe a different moment.
            return no("a live page is already the surface")
        held = self.task_sessions.results()
        if len(held) < 2:
            return no(f"{len(held)} candidate(s) in hand; a shortlist needs 2")
        wants_comparison = bool(self._WANTS_COMPARISON.search(str(user_input or "")))
        if not searched and not wants_comparison and not (
            self._last_interaction.continues
        ):
            # Results are in hand but this turn was about something else.
            return no(
                f"{len(held)} in hand but this turn neither searched nor "
                f"continued (mode={self._last_interaction.mode})"
            )
        if wants_comparison:
            chosen = references.resolve(user_input, held.names())
            picked = [
                held.at(index) for index in (
                    range(len(held)) if not chosen.resolved
                    else [held.names().index(name) for name in chosen.values]
                )
            ]
            built = surfaces.from_candidates(
                [item for item in picked if item is not None][:3],
                kind=surfaces.COMPARISON,
                title="Side by side",
                subject=subject_now,
            )
            return built or no(
                "nothing chosen for comparison names a thing a card can show"
            )
        built = surfaces.from_candidates(
            held.items, kind=surfaces.SHORTLIST,
            title=str(getattr(
                self.task_sessions.active_recommendation(), "subject", "",
            ) or "").strip()[:60],
            # What was asked for, so a result that only repeats the request
            # back ("Seoul", "Seoul Hotels") can be told from one of the
            # things it was asking about.
            subject=subject_now,
        )
        # Refusing every candidate and never building are different things,
        # and both end in an empty window. Say which one happened.
        return built or no(
            f"{len(held)} in hand, but fewer than 2 of them name a thing a "
            f"card could show"
        )

    def surface_opened(self, candidate_id: str) -> str:
        """Note which floating card the person opened.

        Electron opens the page itself, because following a link is not a
        decision and routing it through a turn would make Elaina narrate a
        click. But the conversation still has to know *which* one, or the
        window and the reasoning end up describing different things.

        Resolving by identity rather than by label is also what catches a
        stale card: one left over from a result set that has since been
        replaced names nothing here, and is ignored.
        """
        held = self.task_sessions.results()
        chosen = held.by_id(str(candidate_id or "").strip())
        if chosen is None:
            print("[Surface] a card was opened for something no longer held.")
            return ""
        print(f"[Surface] opened [{chosen.id}] {chosen.name} -> {chosen.url}")
        return chosen.name

    def surface_action(self, action: str, candidate_id: str) -> str:
        """Run a card press as an ordinary turn.

        The whole point of the protocol's identity rule arrives here. The
        card sends the candidate's id, never its label, so this resolves
        against the result set actually in hand and then says the sentence
        the person would have said -- which goes through the router, the
        interaction decision and every guard, exactly as if they had typed
        it. No decision is taken in Electron, and none is taken here either.

        An id that resolves to nothing is dropped. A stale card from a
        result set that has since been replaced must not open something the
        conversation has moved on from.
        """
        held = self.task_sessions.results()
        chosen = held.by_id(str(candidate_id or "").strip())
        if chosen is None:
            print("[Surface] a card was pressed for something no longer held.")
            return ""
        wanted = str(action or "").strip()
        if wanted == "open":
            if not chosen.openable:
                print(f"[Surface] {chosen.name!r} has nowhere to open.")
                return ""
            # The address on its own. A bare address is already the
            # deterministic navigation path -- the one with the browser
            # recovery and verification behind it -- and wrapping it in a
            # verb sends it to the page planner instead.
            said = chosen.url
        elif wanted == "compare":
            said = f"compare {chosen.name} with the others"
        elif wanted == "tell_me_more":
            said = f"tell me more about {chosen.name}"
        else:
            print(f"[Surface] unknown card action {wanted!r}.")
            return ""
        print(f"[Surface] {wanted} on [{chosen.id}] -> {said!r}")
        return self.chat(said)

    def _offerable_capability(self, capability, goal) -> str:
        """Which ability an unprompted offer would actually use.

        Split out of ``_append_recommendation`` so the grounding path below
        cannot drift from it. When the turn was answered from what she knew,
        the capability layer says ``direct_answer`` -- and the offer is the
        extra effort on top, so the ability has to be named here instead.

        Search first, deliberately. Driving the browser is the heavier, more
        disruptive ability and it needs a real page to go to -- offered as
        the default it produced "Happy to dig into a Dinner if that helps",
        and accepting it ran browser control on nothing.
        """
        capability_id = str(getattr(capability, "capability", "") or "")
        if capability_id not in {"direct_answer", "none", ""}:
            return capability_id
        state = self._capability_state()
        wants_browser = goal_intent.names_a_surface(
            str(getattr(goal, "subject", "") or "")
        )
        preference = (
            ("browser_control", "web_search") if wants_browser
            else ("web_search",)
        )
        return next(
            (
                option
                for option in preference
                if CapabilityRegistry.is_available(option, state)
            ),
            "",
        )

    def _offerable_subject(self, goal) -> tuple[str, str]:
        """What the offer is about, or the reason there is nothing to offer.

        Without a distinct topic the goal's subject falls back to the whole
        utterance, and the offer becomes "Want me to look into i am thinking
        about getting a new monitor?". Better to say nothing than to say
        that -- so the refusal is returned rather than raised, and every
        caller reports why it stayed quiet.
        """
        subject = str(getattr(goal, "subject", "") or "").strip()
        if len(subject.split()) > 6:
            # The router named no topic, so the goal's subject is the whole
            # utterance. Try to name the thing itself before giving up.
            subject = subject_phrase(subject)
        if not subject:
            return "", "no subject to name"
        if not subject_is_offerable(subject):
            return "", f"{subject!r} is about them, not a thing to look up"
        if len(subject.split()) > 6:
            return "", f"the subject is a whole sentence ({subject!r})"
        # The offer template is in the language of the turn, and the subject
        # comes from the router, which writes in English. Dropped into a
        # Korean sentence it produces "a movie 확인해 드릴까요?" -- measured
        # live. An offer she cannot say in one language is not an offer.
        if self._turn_language.startswith("ko") and not any(
            "가" <= character <= "힣" for character in subject
        ):
            return "", f"{subject!r} is English and this turn is Korean"
        return subject, ""

    def _one_offer_per_reply(self, reply: str) -> str:
        """Two offers in one answer is one offer too many.

        Measured live on the monitor turn:

            Elaina: That sounds fun! Monitors can make a big difference.
                    Would you like help finding specific models or prices?
                    I don't want to send you somewhere I haven't checked,
                    want me to look up real ones?

        Two questions, one answer. Only the second one was real: the entity
        guard had retracted two unverified brands and parked an offer to go
        and check. The first was the model offering in its own words, and
        ``keep_offers`` had waved the whole reply through because *an* offer
        was open, without asking whether it was *this* one.

        So the parked offer is the one that survives, and any other
        offer-shaped sentence goes. The gate holds exactly one thing; the
        reply may ask exactly one question about it.

        Deliberately the *wider* reader here. ``speech_act_of`` is narrow
        because everything it matches gets a real action parked behind it;
        this is the opposite question -- "does this read as a second call to
        action?" -- and a false positive costs a redundant sentence rather
        than an honest one. Measured live, the narrow reader missed "Let me
        know if you'd like help finding something specific!" purely on the
        apostrophe in "you'd", and the reply carried it *and* a real offer.
        """
        text = str(reply or "").strip()
        if not text:
            return text
        pending = self.capability_offer.peek()
        if pending is None:
            return text
        protected = " ".join(str(pending.offer_text or "").split())
        if not protected:
            return text
        sentences = [
            sentence.strip()
            for sentence in _SENTENCE_SPLIT.split(text)
            if sentence.strip()
        ]
        if len(sentences) < 2:
            return text
        spoken = TextFilter.natural_dashes
        protected_said = spoken(protected)
        whole = spoken(" ".join(sentences))
        if protected_said not in whole:
            # The reply no longer carries the parked offer -- a rewrite
            # replaced it, or a guard phrased it differently. Dropping the
            # other offers would leave the gate holding a question nobody
            # was asked, which is the failure this whole area exists to
            # prevent.
            return text

        kept: list[str] = []
        dropped: list[str] = []
        for sentence in sentences:
            said = spoken(sentence)
            if said in protected_said or protected_said in said:
                kept.append(sentence)
            elif (
                speech_act_of(sentence) == OFFER
                or ClosingOfferGuard.offers_to_act(sentence)
            ):
                dropped.append(sentence)
            else:
                kept.append(sentence)
        rebuilt = " ".join(kept).strip()
        if not dropped or not rebuilt:
            return text
        print(f"[Offer] Dropped a second offer: {dropped[0][:60]!r}")
        return rebuilt

    @staticmethod
    def _spoken_list(names) -> str:
        """A list said the way a person says one, not comma-separated."""
        items = [name for name in names if name]
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        return f"{', '.join(items[:-1])} and {items[-1]}"

    @staticmethod
    def _short_name(name: str, *, max_words: int = 7) -> str:
        """Enough of a result title to recognise it, and no more.

        Search results arrive with tails a person would never say out loud
        ("... | Best Buy", "- Reviews, Specs and Prices"). The head is the
        thing; the tail is the site.
        """
        cleaned = " ".join(str(name or "").split())
        for separator in (" | ", " - ", " – ", " — ", " : "):
            head = cleaned.split(separator)[0].strip()
            if len(head.split()) >= 2:
                cleaned = head
        words = cleaned.split()
        return " ".join(words[:max_words]) if words else ""

    @staticmethod
    def _candidate_is_about(name: str, about: str) -> bool:
        """Whether this result has anything to do with what was asked for.

        The fit layer keeps a candidate that contradicts nothing, which is
        the right call for ranking and the wrong one for reading a name out
        loud. Measured live, four turns into a conversation about monitors:

            [Recommendation Reasoning] Candidates: 6 (0 fit, 2 unchecked)
            [Grounding Guard] The search found 2 and the answer named
                              nothing; naming '2027 Car Prices In South
                              Korea'.
            Elaina: ... 2027 Car Prices In South Korea is the one I'd
                    start with.

        Nothing had contradicted it, because nothing in a conversation
        about monitors says anything about cars. Silence is the right
        failure here: a name that shares no word with the subject is worse
        than no name at all.
        """
        subject = str(about or "").strip()
        if not subject:
            return True
        from brain import recommendation_state

        word = r"[a-z0-9\uac00-\ud7a3']+"
        return recommendation_state._shares_a_word(
            re.findall(word, str(name or "").casefold()),
            re.findall(word, subject.casefold()),
        )

    @staticmethod
    def _names_the_same_thing(name: str, lowered_reply: str) -> bool:
        """Whether the reply already points at this result.

        A stored result title is longer than the way anyone refers to it, so
        the test is the head of the name rather than all of it: "LG UltraGear
        27GP850-B" identifies the thing whose title continues "27in QHD
        Gaming Monitor". Two words is the floor -- one shared word is a
        coincidence, and "monitor" appears in every title here.
        """
        words = str(name or "").casefold().split()
        if not words:
            return False
        if " ".join(words) in lowered_reply:
            return True
        for width in (4, 3, 2):
            if len(words) >= width and " ".join(words[:width]) in lowered_reply:
                return True
        return False

    def _report_what_was_found(
        self, reply: str, *, candidates=(), searched: bool = False,
        about: str = "", said: str = "",
    ) -> str:
        """A search that found things may not be answered with nothing.

        The mirror of :meth:`_enforce_found_claim`. That one stops her
        claiming a find she cannot name; this one stops her burying a find
        she can. Measured live on the turn this was written for:

            [Recommendation Reasoning] Candidates: 6 (0 fit, 5 unchecked)
            [Recommendation] Removed the model's own offer: 'Let me check
                             some options for you; Would you like me to
                             search for the best ones'
            Elaina: I think you're looking for a monitor that fits your
                    needs.

        Six results were in hand and the evidence block told the model to
        offer them as unverified options. It offered to go and search
        instead -- on the turn whose search had already run -- so the offer
        was stripped, correctly, and what was left said nothing at all.

        Only the candidates the fit layer already called viable reach here:
        real items of the right kind that contradict nothing the person
        said. Articles, round-ups and places-to-search are excluded before
        this sees them, so naming these is safe. Nothing is claimed about
        whether they *fit* -- that judgement stays where it is made.
        """
        text = str(reply or "").strip()
        if not searched:
            return text
        if candidate_fit.asks_for_a_method(said):
            # A method has no candidates. Measured live, on "search how to
            # make it really tasty, not the obvious stuff": the reply was a
            # method and named nothing -- correctly -- and this appended
            # "Realize Cooking Doesn't Really Burn Off is the one I'd start
            # with", a food blog's headline. The search found pages about
            # the question, not things to pick between.
            print("[Grounding Guard] The turn asks how to do something; "
                  "not naming a pick.")
            return text
        # A name they cannot read is not a pick they can act on -- see
        # TextFilter.OTHER_SCRIPT_PATTERN for the one that was said.
        names = [
            short for short in (
                self._short_name(name) for name in candidates
            )
            if short and self._candidate_is_about(short, about)
            and not TextFilter.OTHER_SCRIPT_PATTERN.search(short)
            # The card layer's own test for a page. Measured: it dropped
            # "Sennheiser — Headphones, Microphones, Wireless Systems" as
            # several of them, not one, and this still said it as the pick.
            and surfaces.names_a_specific_thing(short, about)
        ][:3]
        if not names:
            return text
        lowered = text.casefold()
        # Named, not quoted. A reply that says "the LG UltraGear 27GP850-B
        # is a solid pick" has named the result whose stored title runs on
        # into "27in QHD Gaming Monitor", and a whole-string test called
        # that unnamed and listed it back at the person.
        if any(self._names_the_same_thing(name, lowered) for name in names):
            return text
        if grounded_values.names_something_specific(text):
            # She named a particular thing. Whether it is the right one is
            # the grounding guards' question, not this one's -- and adding
            # a second product underneath the first is the "unnecessary
            # last sentence" this guard was reported for. It exists to
            # supply a missing name, never to argue with one.
            return text
        print(
            f"[Grounding Guard] The search found {len(names)} and the "
            f"answer named nothing; naming {names[0][:40]!r}."
        )
        # One name, said as an answer rather than as a receipt. Measured
        # live: three long titles listed after a perfectly good
        # recommendation -- "What came up was Best Gaming Monitors 2026:
        # Budget, Curved... and Gaming monitor." Reading the result set back
        # to someone who asked for a recommendation is a different sentence
        # from answering them, and it is the wrong one.
        # In the turn's language. The English sentence was appended to
        # Korean replies as it was.
        if self._turn_language.startswith("ko"):
            return f"{text} {names[0]}부터 보시는 걸 추천합니다.".strip()
        return f"{text} {names[0]} is the one I'd start with.".strip()

    def _refuse_redundant_permission(
        self,
        reply: str,
        *,
        decision,
        route,
        action_performed: bool,
    ) -> str:
        """Never ask to do the thing that was already asked for, or done.

        Exit criterion 6 of Phase 4F.1, and the mirror image of the broken
        promise: that one claims an action nothing is doing, this one asks
        permission for an action already under way. Both are the reply
        disagreeing with the record, and the record wins.

        ``ClosingOfferGuard`` almost covers it and stops one case short --
        it never touches a single-sentence reply, because deleting the only
        sentence would leave silence. So the whole-reply case is handled
        here, where there is a true thing to say instead.

        A question is left alone whenever something really is waiting on an
        answer: an open offer, an outstanding clarification, or any consent
        gate. Those questions are the honest ones, and this must not eat
        them.
        """
        text = str(reply or "").strip()
        if not text:
            return text
        if not (
            action_performed
            or bool(getattr(decision, "acts", False))
            or bool(getattr(route, "action_requested", False))
        ):
            return text
        if any(
            gate.peek() is not None
            for gate in (
                self.capability_offer,
                self.clarification,
                self.computer_consent,
                self.task_consent,
                self.task_strategy_consent,
                self.agent_consent,
            )
        ):
            return text
        if not offered_action(text):
            return text

        kept = ClosingOfferGuard.strip(text, keep_offers=False).strip()
        if kept and kept != text:
            print(
                "[Permission] Dropped a question about something already "
                "requested."
            )
            return kept
        if kept == text and speech_act_of(text) != OFFER:
            # Multi-sentence, and the offer is not the closing line -- the
            # strip could not reach it and the rest is real content.
            return text
        if conversation_style.word_count(text) > 12:
            # One long sentence that answers and then offers is still an
            # answer. Measured in the slip demo: the CPT document list, with
            # an offer clause on the end, was replaced whole with
            # "완료했습니다." -- the answer the person had just confirmed they
            # wanted, deleted for the offer attached to it. English lost it
            # the same way, as "That's done."
            print("[Permission] The offer is attached to a real answer; "
                  "kept.")
            return text
        # The question was the whole reply. Saying nothing is not an option
        # and repeating the question is not true, so report the work.
        print("[Permission] Replaced a redundant permission question.")
        return self._generic_outcome(succeeded=True)

    def _ground_offer_language(
        self,
        reply: str,
        *,
        decision,
        capability,
        goal,
        route,
        action_performed: bool,
    ) -> str:
        """Make her own offer real, instead of deleting it for being hers.

        Phase 4F.1. ``ClosingOfferGuard`` used to strip every "I can pull up
        a few current options if you want" that had no parked offer behind
        it -- which was all of them, because nothing ever parked one for a
        sentence the model wrote itself. The rule was right and the remedy
        was backwards: an offer is only dishonest when the user's "yeah"
        would land on nothing. So park something for it to land on, and the
        sentence becomes true rather than deleted.

        Refuses in four cases, each of which is a reason the offer should
        not have been made:

        * the user already asked outright -- asking again is friction, and
          exit criterion 6 of this phase names it;
        * a question of her own is already outstanding, so a second one
          makes the next reply ambiguous;
        * no ability is available to do it, so the yes could not be kept;
        * the offer cooldown is still running -- ``RecommendationPolicy``
          stays the single layer deciding how often she may offer at all.

        A refusal changes nothing here; ``ClosingOfferGuard`` still strips
        the sentence a moment later, exactly as before.
        """
        text = str(reply or "").strip()
        if not text:
            return text
        sentence = offered_action(text)
        if not sentence:
            return text
        if self.capability_offer.peek() is not None:
            # A guard already parked one this turn -- the grounded-value or
            # grounded-entity repair does exactly that when it retracts an
            # unchecked claim -- and the reply carries its words. Nothing to
            # ground, and _one_offer_per_reply below makes sure the parked
            # one is the only question that survives.
            #
            # Said out loud because the silence here cost a debugging pass:
            # a live run showed no grounding line and looked broken, when
            # the offer had simply come from somewhere else.
            print(
                "[Offer] Already grounded by another guard this turn; "
                "standing down."
            )
            return text

        def quiet(why: str) -> str:
            print(f"[Offer] Left her own offer ungrounded: {why}.")
            return text

        if (
            action_performed
            or bool(getattr(decision, "acts", False))
            or bool(getattr(route, "action_requested", False))
        ):
            return quiet("the user asked for this outright")
        if self.clarification.peek() is not None:
            return quiet("a question of her own is already outstanding")

        capability_id = self._offerable_capability(capability, goal)
        if not capability_id:
            return quiet("no ability is available behind it")
        state = self._capability_state()
        if not CapabilityRegistry.is_available(capability_id, state):
            return quiet(f"{capability_id} is not available")

        subject, refusal = self._offerable_subject(goal)
        if refusal:
            return quiet(refusal)
        if not self.recommendations.claim_her_own(capability_id):
            # The rationing, not the mode gate. Deciding an offer belongs
            # here was done when the sentence was written; all that is left
            # is whether she has offered too recently.
            #
            # An invitation survives the refusal. "Let me know if you want
            # help narrowing down options" leaves nothing pending -- there
            # is no question waiting on an answer, so there is nothing to
            # be dishonest about, and deleting it took the only useful
            # sentence out of the reply. A *question* still goes: "want me
            # to?" with nothing parked is the failure this phase exists for.
            if not sentence.rstrip().endswith("?"):
                self._invitation_stands = True
                print(
                    "[Offer] Kept her invitation without parking it: "
                    f"{sentence[:60]!r}"
                )
                return text
            return quiet("the offer cooldown is still running")

        active_problem = self.task_sessions.active_recommendation()
        self.capability_offer.offer(
            capability_id=capability_id,
            goal=subject,
            # Her own words, not a generated substitute: the user heard
            # this sentence, so this is the offer they are answering.
            offer_text=sentence,
            # She raised it herself, so anything short of a clear yes drops
            # it rather than being read as one.
            proactive=True,
            task_id=(active_problem.id if active_problem is not None else ""),
            task_query=(
                active_problem.search_query()
                if active_problem is not None else ""
            ),
        )
        print(
            f"[Offer] Grounded her own offer for {capability_id}: "
            f"{sentence[:80]!r}"
        )
        return text

    def _append_recommendation(
        self, reply: str, *, decision, capability, goal, act=conversation_style.ANSWER,
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
        if conversation_style.contract_for(act).offers_allowed < 1:
            # The act says no. A receipt, a greeting and a goodbye have no
            # room for an offer, and this is the layer that was adding one
            # anyway -- it runs after the style pass, so nothing the style
            # pass decided had reached it.
            #
            # Not a cosmetic rule. Measured live: "ok" was answered with "I
            # can help you find a good wireless mouse under $50. Want me to
            # look it up?", which parked an offer; the next turn was "that's
            # fine, thanks", which the consent classifier read as accepting
            # it, and a browser action ran on a goodbye. An offer nobody
            # asked for is not just noise -- it is a question, and the next
            # thing the person says becomes its answer.
            return quiet(f"a {act} has no room for an offer")
        if self.clarification.peek() is not None:
            # She has just asked a question of her own. Adding "want me to
            # search?" underneath it puts two questions on the table and
            # makes the next reply ambiguous -- measured live, the answer
            # to hers was consumed as a "no" to this one.
            return quiet("a question of her own is already outstanding")
        if text.rstrip().endswith("?"):
            # The same rule, for a question she asked in her own words
            # rather than through the gate. Measured live: "Are you looking
            # for a gaming one or something for work? Want me to look into
            # a monitor?" -- and the person answered the first one, which
            # is the right thing to do with two questions and exactly why
            # there should only be one.
            return quiet("she ended on a question of her own")
        capability_id = self._offerable_capability(capability, goal)
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

        subject, refusal = self._offerable_subject(goal)
        if refusal:
            return quiet(refusal)

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

    def _generic_declined(self) -> str:
        """Acknowledge a refusal, and vary it without losing the meaning.

        The status bank's bare acknowledgements ("Sure.", "Yeah.") read as
        agreement rather than as dropping something, which is the opposite
        of what a refusal deserves -- so a refusal has a bank of its own.

        It used to be a private tuple here with a two-deep memory that
        always took the first survivor, which is a weaker rotation than the
        one every other line already gets. Same words, one selector: the
        anti-repetition is now shared with everything else she says.
        """
        return self.action_status.select(StatusContext(
            phase="declined", force=True,
        )) or "Okay, I'll leave it."

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

    # How long to let the browser settle before looking. A navigation that
    # has not finished is not a navigation that failed, and calling it one
    # would trade a false success for a false failure.
    NAVIGATION_SETTLE_SECONDS = 0.8
    NAVIGATION_LOOKS = 3

    def _observed_tabs(self):
        """What the browser is showing, or nothing if it cannot be read.

        Two shapes, because the two drivers report differently. The CDP
        driver's ``list_tabs`` carries a URL per tab. The screen driver
        reads the window the person already has open, and its tab list is
        window titles only -- the address lives in the omnibox, which
        comes back with a page observation. So: the cheap call first, and
        the fuller one only when the cheap one said nothing about where
        the browser actually is.

        Nothing here raises. A browser that cannot be inspected produces
        an honest "I have not checked", which is the whole point of the
        lifecycle -- an unreadable browser must never become a success
        claim, and it must never become a crash either.
        """
        rows: list[Any] = []
        try:
            tabs = self.browser_observer.list_tabs()
        except Exception as error:  # noqa: BLE001 - observation is best-effort
            print(f"[Navigation] could not read the browser: {error}")
            tabs = ()
        if isinstance(tabs, (list, tuple)):
            rows = [tab for tab in tabs if str(getattr(tab, "url", "") or "")]
        if rows:
            return tuple(rows)

        try:
            page = self.browser_observer.describe_page(None)
        except Exception as error:  # noqa: BLE001
            print(f"[Navigation] could not read the page: {error}")
            return ()
        url = str(getattr(page, "url", "") or "")
        if not url:
            print("[Navigation] the browser did not report an address.")
            return ()
        # The page's own words as well as its name. A title that is just
        # the address, with nothing behind it, is a browser saying it had
        # nothing to show -- and that is only visible if the text comes
        # too.
        body = " ".join((
            " ".join(getattr(page, "headings", ()) or ()),
            str(getattr(page, "text_excerpt", "") or ""),
        )).strip()
        return (SimpleNamespace(
            index=0, url=url,
            title=str(getattr(page, "title", "") or ""),
            text=body,
            is_active=True,
        ),)

    def _browser_fingerprint(self):
        """What the browser was showing, cheaply, before anything moved.

        Only ``list_tabs`` -- the fast call. It is enough to tell a stale
        reading from a real one, which is the question it exists to
        answer: the address bar said zillow.com and the page said
        openzillow.com, and knowing whether that pair was already there a
        moment ago is what separates "wrong page" from "I looked too
        early".
        """
        try:
            tabs = self.browser_observer.list_tabs()
        except Exception:  # noqa: BLE001 - a snapshot is best-effort
            return ()
        if not isinstance(tabs, (list, tuple)):
            return ()
        rows = [
            (str(getattr(tab, "url", "") or ""),
             str(getattr(tab, "title", "") or ""))
            for tab in tabs
        ]
        # The page the last navigation actually landed on, with its real
        # address. The screen driver's tab list reports window titles and
        # no URLs, so without this there is nothing to compare a title
        # against -- and telling a stale reading from a real one is
        # exactly a comparison against where we were.
        previous = getattr(self, "_navigation", None)
        if previous is not None and previous.title:
            rows.append(
                (previous.actual_url or previous.url, previous.title),
            )
        return tuple(rows)

    def _look_at(self, navigation, *, before=()):
        """Verify one navigation, giving the page a moment to arrive."""
        for attempt in range(self.NAVIGATION_LOOKS):
            if attempt:
                time.sleep(self.NAVIGATION_SETTLE_SECONDS)
            looked = browser_navigation.verify(
                navigation, self._observed_tabs(), before=before,
            )
            if looked.arrived or looked.status == browser_navigation.ERROR_PAGE:
                return looked
        return looked

    def _verify_navigation(self, result, route):
        """Say what actually happened, and recover when it is recoverable.

        Session 7, three turns running:

            [Computer Control] open_url openZillow.com status=url_opened
            Elaina: All set, openZillow.com is open.
            You said: didn't open it.
            Elaina: Zillow.com is open.

        Nothing had loaded at any point -- ``openZillow.com`` is not a host
        anybody owns -- and the second claim was made after being told the
        first was wrong. The status meant "the command was accepted"; every
        layer above read it as "the page is on screen".

        Returns the result, and a spoken line to use in place of the
        ordinary success acknowledgement. An empty line means the page is
        genuinely there and the normal path may speak.
        """
        # One resolved target, or say so. Measured live, the session-11
        # rerun:
        #
        #     You said: openZillow.com
        #     [Router] Interpreted transcript as: open isss.washington.edu
        #     [Computer Control] open_url target=openZillow.com
        #
        # The router's own record of the turn and the address the machine
        # received had come apart, which is the failure this project has
        # chased through six sessions under different names. It cannot be
        # fixed by guessing which one is right -- but it can stop being
        # silent.
        interpreted = browser_navigation.address_in(route.normalized_request)
        going_to = browser_navigation.host_of(
            route.computer_url or route.action_target,
        )
        if interpreted and going_to and not browser_navigation.same_destination(
            interpreted, going_to,
        ):
            print(
                "[Navigation] MISMATCH: the turn was interpreted as "
                f"{interpreted!r} and the browser was sent to {going_to!r}."
            )

        # A dispatch that never ran is a different failure from one that
        # ran and could not be observed, and only the browser layer knows
        # which happened.
        dispatch_failed = result.status == "failed"
        dispatch_detail = " ".join(str(result.message or "").split())
        requested = str(route.action_target or result.target or "").strip()
        # A dispatch that failed outright carries no url of its own, so the
        # address has to come from what was asked for. Without this the
        # recovery had nothing to work from and the run ended at "action
        # failed for is.washington.edu".
        opened = str(
            result.url or route.computer_url or requested or "",
        ).strip()
        history = tuple(self._navigation_history)
        before = tuple(self._navigation_before)
        navigation = browser_navigation.start(
            requested, opened, history=history,
            command_fused=getattr(route, "command_fused", False),
        )
        receipt = getattr(result, "navigation", None)
        if isinstance(receipt, browser_navigation.Navigation):
            navigation = replace(
                receipt, requested=requested, history=navigation.history,
                command_fused=navigation.command_fused,
            )
            # A frame that contradicts itself is worth a second look
            # before it becomes a verdict. The dispatcher reads the page
            # once, and the address bar commits before the title follows,
            # so a recovery step can arrive at the right host wearing the
            # name of the one it just left.
            if navigation.classification == "stale_observation":
                print("[Navigation] contradictory frame; looking again.")
                navigation = self._look_at(navigation, before=before)
        else:
            navigation = self._look_at(navigation, before=before)
        print(navigation.log_block())

        if navigation.arrived:
            self._navigation = navigation
            self._navigation_history = (navigation.url,)
            self._last_action_failed = False
            return replace(result, status="url_opened", navigation=navigation), ""

        # "I could not look" and "I looked and could not judge" are two
        # different things, and treating them as one made the whole
        # lifecycle inert. Measured live, session 10, twice:
        #
        #     actual: https://opennaver.com
        #     title: opennaver.com
        #     observation: hwnd:525894:50cfa6ff
        #     status: page_loaded_unverified
        #     classification: ambiguous
        #
        # The observer read the right window, the right address and the
        # right title. What it could not do was tell whether a page was
        # behind them -- and that went to the same dead end as a browser
        # nobody could read, so the naver.com candidate sitting in the
        # conversation was never tried.
        #
        # Recovery is for anything that was *observed* and did not arrive.
        # Nothing here becomes a success that was not one.
        if not navigation.observation_id:
            self._navigation = navigation
            self._navigation_history = navigation.history
            # "I sent the browser there" is itself a claim, and it is false
            # when the dispatch never ran. Measured live, the session-11
            # rerun, four turns in a row:
            #
            #     [Computer Control] open_url isss.washington.edu status=failed
            #     Elaina: I sent the browser to isss.washington.edu, but I
            #             couldn't check whether it loaded.
            #
            # The browser layer had already reported that it could not find
            # a window to navigate, and that reason was thrown away in
            # favour of a sentence about not having checked. Not checking
            # and not going are different failures and the person can act
            # on only one of them.
            if dispatch_failed:
                self._offer_a_retry(navigation.url or opened)
                return replace(
                    result, status="navigation_failed", navigation=navigation,
                ), (
                    f"I couldn't get to {navigation.expected_host}: "
                    f"{dispatch_detail}"
                    if dispatch_detail else
                    f"I couldn't open {navigation.expected_host} -- the "
                    "browser didn't take the request."
                )
            return replace(
                result, status="url_dispatched",
                navigation=navigation,
                message=f"Sent the browser to {opened}, not yet verified.",
            ), (
                f"I sent the browser to {navigation.expected_host}, but I "
                "couldn't check whether it loaded."
            )

        recovered, line = self._recover_navigation(navigation, before=before)
        self._navigation = recovered
        if recovered.arrived:
            self._navigation_history = (recovered.url,)
            self._last_computer_goal = recovered.url
            return replace(
                result, status="url_opened", navigation=recovered,
                url=recovered.url, target=recovered.url,
                display_name=recovered.url,
                message=f"Opened {recovered.url}.",
            ), line
        self._navigation_history = recovered.history
        # She is about to say it did not load, and every one of those lines
        # ends by asking. Park what answers it. Measured live, session 9:
        #
        #     Elaina: Zillow.com didn't open, try again?
        #     You said: Yeah.
        #     Elaina: I got it. Let me know what you need next.
        #
        # She offered a retry and left nothing outstanding for the yes to
        # accept, so the person had to say "you didn't open it" before
        # anything happened.
        self._offer_a_retry(navigation.url or opened)
        if recovered.status == browser_navigation.UNVERIFIED:
            return replace(result, status="url_dispatched", navigation=recovered), line
        return replace(
            result, status="navigation_failed",
            navigation=recovered,
            message=f"{navigation.expected_host} did not load.",
        ), line

    def _offer_a_retry(self, target: str) -> None:
        """Make the retry she just offered something a "yeah" can accept."""
        address = str(target or "").strip()
        if not address:
            return
        self._last_action_failed = True
        self.capability_offer.offer(
            capability_id="browser_control",
            goal=address,
            offer_text=f"Want me to try {browser_navigation.host_of(address)} again?",
        )

    def _recover_navigation(self, navigation, *, before=()):
        """Try the addresses the conversation itself supplied, or ask.

        Never a domain nobody mentioned. There are exactly two sources --
        a command verb the transcriber ran into the host, and the
        spellings between the one first asked for and the one just tried --
        and when neither yields anything the honest move is to say what
        happened rather than to guess.
        """
        host = navigation.expected_host or navigation.url
        candidates = browser_navigation.recovery_candidates(navigation)
        if not candidates:
            # Say what she saw, not just that it failed. "The browser is
            # showing openzillow.com" is actionable; "it didn't load" is a
            # shrug -- and, when all that was wrong is that the page had no
            # name of its own, it is also a claim the evidence does not
            # support. Three states, three sentences.
            if navigation.status == browser_navigation.UNVERIFIED:
                # "I opened X" is a claim about arrival, and this branch
                # exists precisely because arrival was not established.
                # Say what she did do: she sent the browser.
                return navigation, (
                    f"I sent the browser to {host}, but I couldn't confirm "
                    "that the site loaded correctly."
                )
            seen = (
                f" The browser is showing {navigation.title}."
                if navigation.status == browser_navigation.WRONG_DESTINATION
                and navigation.title else ""
            )
            return navigation, (
                f"{host} didn't load as requested."
                f"{seen} Give me the address again and I'll go straight there."
            )
        if len(candidates) > 1:
            options = " or ".join(candidates[:2])
            return navigation, (
                f"{host} didn't load. Did you mean {options}?"
            )

        candidate = candidates[0]
        print(f"[Navigation] recovering: {host} -> {candidate}")
        attempt = browser_navigation.start(
            navigation.requested, candidate,
            history=(*navigation.history, candidate),
        )
        attempt = replace(attempt, recovered_from=host)
        try:
            before = self._browser_fingerprint()
            prepared = self.computer_control.prepare(
                ComputerActionRequest(
                    operation="open_url", target=candidate, url=candidate,
                )
            )
            if prepared.prepared is None:
                return navigation, f"{host} didn't load, and I couldn't try {candidate}."
            dispatched = self.computer_control.execute(prepared.prepared)
            if dispatched.status not in {"url_opened", "url_dispatched"}:
                return navigation, f"{host} didn't load, and I couldn't try {candidate}."
        except Exception as error:  # noqa: BLE001
            print(f"[Navigation] recovery could not run: {error}")
            return navigation, (
                f"{host} didn't load, and I couldn't try {candidate} either."
            )

        receipt = getattr(dispatched, "navigation", None)
        if isinstance(receipt, browser_navigation.Navigation):
            attempt = replace(receipt, requested=attempt.requested,
                              history=attempt.history, recovered_from=host)
            # The same second look the first navigation gets. A recovery
            # step lands on the right host while the title still says the
            # one it came from, and concluding from that first frame is
            # how a working candidate was called a wrong destination.
            if attempt.classification == "stale_observation":
                print("[Navigation] contradictory frame; looking again.")
                attempt = self._look_at(attempt, before=before)
            if attempt.arrived:
                attempt = replace(attempt, status=browser_navigation.RECOVERED)
        else:
            attempt = self._look_at(attempt, before=before)
        print(attempt.log_block())
        if attempt.arrived:
            return attempt, (
                # What she says happened has to match what she saw. An
                # address that came up with no page behind it is not the
                # same claim as one that failed to load, and she has just
                # been strict about that distinction two branches above.
                (
                    f"{host} didn't come up as itself, so I opened "
                    f"{candidate} instead -- that one's up."
                    if navigation.status == browser_navigation.UNVERIFIED
                    else
                    f"{host} didn't load, so I opened {candidate} instead -- "
                    "that one's up."
                )
            )
        if attempt.status == browser_navigation.UNVERIFIED:
            return attempt, (
                f"I sent the browser to {candidate}, but I couldn't check whether it loaded."
            )
        return attempt, (
            f"Neither {host} nor {candidate} loaded. "
            "Give me the address again and I'll go straight there."
        )

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

        # Before anything moves, so a reading taken afterwards can be told
        # from one taken too early.
        if route.computer_operation in {"open_url", "open_search"}:
            self._navigation_before = self._browser_fingerprint()
            # A different page has different elements, so anything still
            # waiting to be chosen between belongs to a page that is on
            # its way out.
            if hasattr(self, "_page_choice"):
                self._page_choice = None

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
            # A screen-driver navigation types into a real address bar
            # with the real cursor, so it is an actuation like any other
            # and needs the same interruption window. It never had one --
            # measured in the session-14 run:
            #
            #     [Input Watch] takeover reason=pointer_drift
            #                   parked_at=(2222,1171) now=(1585,342)
            #     Elaina: I couldn't get to google.com: Something moved
            #             the pointer, so I stopped ...
            #
            # With no run open, `_input_mark` was None, the real-input
            # test was skipped entirely, and drift was the only signal
            # left -- which is exactly the branch that cannot identify a
            # person.
            screen_navigation = (
                getattr(self, "browser_driver", "") == "screen"
                and route.computer_operation in {"open_url", "open_search"}
            )
            if screen_navigation:
                self.cursor_driver.begin_run(
                    f"{route.computer_operation}:{route.action_target}"
                )
            try:
                result = (
                    self.computer_control.execute(prepared_result.prepared)
                    if prepared_result.prepared is not None
                    else prepared_result
                )
            finally:
                if screen_navigation:
                    self.cursor_driver.end_run(restore=False)

        # What was just done, so a turn that points at it has something to
        # point at. Only the two planner paths recorded this, which meant
        # that after three structured ``open_url`` turns the last action on
        # record was still a browser_action from four turns earlier -- and
        # "So open it." would have retried the wrong address.
        if route.computer_operation not in {"none", "unsupported", ""}:
            target = (
                route.computer_url
                or getattr(result, "target", "")
                or route.action_target
                or ""
            )
            if target:
                self._last_computer_action = route.computer_operation
                self._last_computer_goal = target
                self._last_action_failed = not getattr(
                    result, "succeeded", False,
                )

        # Dispatch is not arrival. ``url_opened`` means Windows accepted
        # the navigation command; whether the page the person asked for is
        # on their screen is a different question, and until session 7 it
        # was never asked. Ask it here, before anything speaks.
        #
        # ``failed`` comes here too. Measured live, session 9:
        #
        #     open_url isss.washington.edu status=failed
        #     open_url is.washington.edu   status=failed
        #     Elaina: Moved mouse, action failed for is.washington.edu.
        #
        # and the recovery that would have found iss.washington.edu never
        # ran, because it hung off the verification path and a dispatch
        # that failed outright never reached it. A failure to navigate is
        # the case recovery exists for.
        if result is not None and result.status in {
            "url_opened", "url_dispatched", "failed",
        } and route.computer_operation in {"open_url", "open_search"}:
            result, navigation_line = self._verify_navigation(result, route)
            if navigation_line:
                return navigation_line, result

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
                # Only the steps a person could actually hear. A step
                # record is written for the log, and the last one before an
                # interruption is often a raw observation -- the whole
                # accessibility tree went out this way once, introduced by
                # the word "Completed:".
                done = conversation_style.speakable_list(
                    plan_result.steps_taken
                ).rstrip(" .")
                return (
                    f"You took control, so I stopped. I'd got as far as "
                    f"{done}."
                    if done else
                    "You took control, so I stopped partway through."
                ), None

        self._last_computer_action = "ui_action"
        self._last_computer_goal = route.action_target or ""
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

        plan_result = self._bind_result_to_request(
            plan_result,
            requested_goal=(
                clarified_goal if clarified_goal is not None
                else str(route.normalized_request or original_request or "")
            ),
        )
        self._last_computer_action = "browser_action"
        self._remember_browser_interaction(
            route.action_target or "",
            plan_result=plan_result,
            requested_goal=(
                clarified_goal if clarified_goal is not None
                else str(route.normalized_request or original_request or "")
            ),
        )
        print(
            "[Computer Control] action=browser_action target="
            f"{route.action_target or '(none)'} status={plan_result.status} "
            f"rounds={plan_result.model_rounds} "
            f"failure={plan_result.failure_code or '(none)'}"
        )
        if self._browser_interaction is not None:
            print(
                f"[Page Action] {self._browser_interaction.describe()}"
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
        ambiguity = getattr(plan_result, "ambiguity", None)
        if ambiguity is None or not ambiguity.candidates:
            # A run about something else retires the old question. A run
            # about the same thing does not -- that is the one whose
            # candidates a follow-up choice still refers to.
            standing = getattr(self, "_page_choice", None)
            if standing is not None and not standing.still_answerable_for(
                BrowserActionPlanner._direct_click_target(
                    str(route.normalized_request or original_request or "")
                ),
            ):
                self._page_choice = None
        if ambiguity is not None and ambiguity.candidates:
            # Not a failure to report and forget. The action is standing,
            # missing one thing, and the next turn can supply it.
            self._page_choice = ambiguity
            print(
                f"[Page Action] waiting on a choice between "
                f"{len(ambiguity.candidates)} candidates for "
                f"{ambiguity.requested_label!r}."
            )
        if not succeeded:
            message = self._spoken_browser_failure(
                plan_result.failure_code, message,
                interaction=getattr(plan_result, "interaction", None),
                ambiguity=ambiguity,
            )
            # A machine result is the last word on what the machine did.
            # The search-grounding guards exist to stop an unbacked claim
            # about listings; a page-click failure is not one, and letting
            # them rewrite it replaced the answer with search language.
            self._browser_result_is_final = True

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
        # A text read is evidence about text. Measured live: she searched,
        # clicked into image results, read the page's text -- which on
        # Google Images is navigation chrome and little else -- and reported
        # "the page is empty ... no image results are visible. Please try
        # refreshing the page or checking your internet connection." Every
        # step had worked. She cannot see pictures, so she must not report
        # on their absence; what she can report is what she did.
        message = browser_outcome.without_leaked_instruction(message)
        corrected = browser_outcome.correct_visual_claim(
            message, goal=goal_text, steps_succeeded=succeeded,
        )
        if corrected != message:
            print("[Browser Result] a text read cannot say what is not pictured.")
            message = corrected

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
    def _bind_result_to_request(plan_result, *, requested_goal: str):
        """A run that finished is not the same as the request being met.

        Measured in the acceptance run, retrying an interrupted "click
        About": two clicks, several page descriptions, ``status=done``,
        and a summary of the ISS page -- with no evidence anywhere that
        About had been clicked. "Interact with the page until something
        happens, then describe it" is not what was asked for.

        So when the request named an element, finishing means that element
        was clicked. Anything else is honest uncertainty, which is a thing
        the person can act on; a false success is not.
        """
        if plan_result is None or plan_result.status != "done":
            return plan_result
        element = BrowserActionPlanner._direct_click_target(requested_goal)
        if not element:
            return plan_result
        interaction = getattr(plan_result, "interaction", None)
        if interaction is not None and interaction.satisfied:
            return plan_result
        # The model-driven path leaves no structured record, so what it
        # said it did is the evidence -- its own summary and its step log
        # both count. Something naming the element is enough; nothing
        # naming it anywhere is not.
        wanted = element.casefold()
        reported = [str(plan_result.summary), *map(str, plan_result.steps_taken)]
        if any(wanted in line.casefold() for line in reported):
            return plan_result
        print(
            "[Page Action] the run finished without doing what was asked: "
            f"{element!r} was never clicked."
        )
        return replace(
            plan_result,
            status="failed",
            summary=(
                f"I worked through the page but couldn't confirm I clicked "
                f"{spoken_label(element)}."
            ),
            failure_code="request_unsatisfied",
        )

    def _remember_browser_interaction(
        self, target: str, *, plan_result, requested_goal: str,
    ) -> None:
        """Keep what was asked for, not what was last said.

        Measured in the acceptance run. "Click about on this page" was
        followed by an offer, the person said "Yes.", and the planner's
        target became the word "Yes." -- so "can you try again?" retried
        an acknowledgement, clicked several unrelated things, and read the
        page back as though that had been the request.

        An acknowledgement carries no target. It cannot become one, and it
        cannot overwrite the one already standing.
        """
        proposed = " ".join(str(target or "").split())
        if proposed and recommendation_state.is_acknowledgement(proposed):
            # Do not let "Yes." become the thing to do again.
            print(
                "[Page Action] an acknowledgement is not a target; "
                f"keeping {self._last_computer_goal!r}."
            )
            return
        finished = getattr(plan_result, "interaction", None)
        if finished is not None:
            self._browser_interaction = finished
            self._last_computer_goal = finished.target
            return
        element = BrowserActionPlanner._direct_click_target(requested_goal)
        if element:
            # A click that worked has to record *what* it clicked, or the
            # record says it succeeded at nothing. Measured in the
            # session-14 run: "click_element target='about' status=clicked
            # resolved=''" -- which `satisfied` reads as unsatisfied.
            done = plan_result.status == "done"
            self._browser_interaction = BrowserInteraction(
                operation="click_element", target=element,
                source=requested_goal,
                status="clicked" if done else "failed",
                resolved=element if done else "",
                evidence=str(getattr(plan_result, "summary", "") or ""),
            )
        elif proposed:
            # Not a page interaction -- a navigation or a whole-goal run.
            # Nothing structured to keep, so the record is retired rather
            # than left pointing at the previous page's element.
            self._browser_interaction = None
        self._last_computer_goal = proposed

    def _what_the_watcher_actually_saw(self) -> str:
        """Answer a dispute with the observation, not the conclusion.

        They are telling her the reason was wrong, so the reply has to be
        about the evidence rather than a restated verdict.
        """
        reason = str(getattr(self.cursor_driver, "last_takeover", "") or "")
        if reason == "pointer_drift":
            return (
                "Got it -- I only saw the pointer move, not a click or a "
                "keypress. Say the word and I'll try again."
            )
        if reason == "real_input":
            return (
                "Understood. Something produced a mouse event that didn't "
                "look like mine, and I stopped on it. Say the word and "
                "I'll try again."
            )
        return (
            "Understood -- then something else moved the pointer, not you. "
            "Say the word and I'll try again."
        )

    def _resume_page_choice(self, chosen) -> "TurnRouting":
        """Run the standing page action on the element they picked."""
        standing = self._page_choice
        # Deliberately kept. Answering "which one?" does not use up the
        # answer: measured in the session-14 run, "the first one" was
        # clicked and the very next turn -- "Can you click the second
        # one?" -- had nothing left to choose from, so a bare ordinal
        # reached the router and became a Google search for ABOUT.
        self._retire_pending_interpretations()
        print(
            f"[Page Action] chose {chosen.named()}: "
            f"{chosen.label!r} ({chosen.element_id})"
        )
        self._browser_interaction = BrowserInteraction(
            operation=standing.operation,
            target=standing.requested_label,
            source=standing.goal,
            tab_identity=standing.tab_identity,
            page_url=standing.page_url,
        )
        prepared = PreparedComputerAction(
            operation="browser_action",
            target=chosen.element_id,
            display_name=chosen.label or standing.requested_label,
            tab_index=standing.tab_index,
            url=standing.page_url,
            browser_action="click",
            browser_scan_id=standing.scan_id,
            browser_href=chosen.href,
            browser_goal=standing.goal,
        )
        return TurnRouting(
            route=IntentDecision(
                intent="computer_action", confidence=1.0,
                normalized_request=(
                    f"click {standing.requested_label}"
                ),
                reason="The user chose one of the elements she found.",
                is_follow_up=True,
                speech_act="action_request",
                action_requested=True,
                action_target=standing.requested_label,
                computer_operation="browser_action",
            ),
            user_input=f"click {standing.requested_label}",
            approved_computer_action=prepared,
        )

    @staticmethod
    def _spoken_browser_failure(
        failure_code: str, summary: str, *,
        interaction: BrowserInteraction | None = None,
        ambiguity: AmbiguousPageAction | None = None,
    ) -> str:
        """Say what went wrong, not what the planner said to itself.

        Found live: a failed browser step spoke its own internal
        instruction aloud -- "That element was not in the latest live page
        scan. Call describe page before acting." That sentence is addressed
        to the model, not the user, and means nothing to them.

        When the failure has a structured interaction behind it, the
        sentence is built from that -- the element the person named, and
        the choices when there were several. Measured in the acceptance
        run: "click calendar" came back ``direct_target_ambiguous`` and was
        spoken as "I couldn't get actual listing names out of that search
        -- want me to open it in the browser and read them off?", which
        belongs to a different layer entirely and answers nothing.
        """
        if interaction is not None:
            named = spoken_label(interaction.target) or "that"
            if interaction.status == "not_found":
                return f"I couldn't find a {named} element on this page."
            if interaction.status == "ambiguous":
                if ambiguity is not None and ambiguity.candidates:
                    # "I found more than one about item -- ABOUT, ABOUT"
                    # gives a person nothing to choose with. Where each
                    # one sits does.
                    return ambiguity.question()
                choices = [c for c in interaction.candidates if c]
                if len(choices) > 1:
                    listed = ", ".join(choices[:4])
                    return (
                        f"I found more than one {named} item -- {listed}. "
                        "Which one do you mean?"
                    )
                return (
                    f"I found more than one {named} item. "
                    "Which one do you mean?"
                )
            if interaction.status == "user_took_over":
                return "You took control, so I stopped."
            if interaction.status and interaction.status != "clicked":
                return f"I couldn't click {named}."
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
            "request_unsatisfied": (
                "I couldn't confirm I did the thing you asked for."
            ),
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

        # What the person hears, built from what the run actually did.
        #
        # This used to be `task_result.summary or "That task is done."` --
        # the model's own final planning summary, spoken verbatim. Measured
        # across the execution matrix: four scenarios out of twenty-two
        # reported "Done." on a run whose last step had failed
        # verification, because the model wrote {"done": true, "summary":
        # "Done."} after "Pressed play; nothing started."
        #
        # TaskRunResult.outcome() had said retryable_failure the whole
        # time. Nothing outside the tests ever called it.
        outcome = task_result.outcome()
        print(f"[Task] {outcome.log_line()}")
        print(task_progress.read(task_result.task_state).log_line())
        spoken = task_progress.report(
            outcome,
            task_result.task_state,
            language=self._turn_language,
            model_summary=task_result.summary,
        )
        return self._prefix_with_preview(
            preview, spoken or task_result.summary or guard_lines.say(
                "task_incomplete", self._turn_language,
            ),
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
        resolved=None,
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

        # What this turn is allowed to carry, decided once. Three builders
        # follow, and until this existed each answered the question its own
        # way -- the trusted-result one by never asking it. See
        # brain/turn_context.py for the turn that cost.
        turn_context = self._context_for_turn(route, goal_intent_result)
        print(turn_context.log_line())
        # The last search's results, carried into a follow-up about them
        # ("which of those is cheapest?"): evidence for this turn because
        # this turn continues that one -- never merely because they are the
        # most recent search (Phase 3C).
        if (
            str(getattr(self, "_last_research_evidence", "") or "").strip()
            and getattr(route, "is_follow_up", False)
            and turn_context.inherit_history
        ):
            self._ledger().add(
                turn_evidence.RECALL, self._last_research_evidence,
                source="the previous search, carried into a follow-up",
                carried=True,
            )
        if turn_trace.current() is not None:
            turn_trace.note_context(
                language=self._turn_language,
                user_input_resolved=user_input,
                route=route,
                decision=decision,
                capability=capability,
                goal=goal_intent_result,
                resolved=resolved,
                turn_context=turn_context.log_line(),
                action_performed=action_performed,
                screen_vision=bool(use_screen_vision),
                tool_result=locked_response or forced_response,
                recalled_evidence=recalled_evidence,
                state=self._state_for_trace(),
            )
        if not turn_context.inherit_history:
            # Move the line, rather than skipping one turn. Emptying only
            # this prompt left the old subject in the manager, and the next
            # turn -- which legitimately inherits -- brought it straight
            # back. Measured: the dinner turn started clean and "which one
            # would you choose?" answered about graphics cards anyway.
            self.conversation.start_new_subject()

        messages = self.conversation.build_messages(
            system_prompt=self.system_prompt,
            context_prompt=context_prompt,
            history=turn_context.history_for_builder,
        )

        calculation_plan = None
        if route.intent == "knowledge_question":
            messages = self._build_factual_messages(
                route.normalized_request,
                include_grounded=self._grounded_context_is_relevant(
                    route, goal_intent_result,
                ),
                reset_history=not turn_context.inherit_history,
            )
        elif route.intent == "calculation":
            # A small local model doing multi-step arithmetic in its head is
            # exactly where it goes wrong (measured: three different wrong
            # totals across three temperatures on one proration question).
            # The planner only asks it to translate the problem into plain
            # arithmetic expressions -- a sandboxed evaluator computes the
            # actual numbers, so they can't be a language-model math mistake.
            calculation_started = time.perf_counter()
            claimed = getattr(self, "_turn_domain_claim", None)
            if claimed is not None and claimed.result:
                # Already computed, exactly, by the domain's own grammar:
                # no planning call to the model.
                calculation_plan = claimed
            else:
                calculation_plan = self.calculation_planner.plan(
                    route.normalized_request
                )
            timings["calculation_plan"] = (
                time.perf_counter() - calculation_started
            )
            if calculation_plan is not None:
                turn_trace.note_evidence(
                    calculation=calculation_plan.as_trusted_result_text(),
                )
                messages = self._build_tool_result_messages(
                    user_input=route.normalized_request,
                    tool_result=calculation_plan.as_trusted_result_text(),
                    inherit_history=turn_context.inherit_history,
                    evidence_kind=(
                        turn_evidence.CONVERSION
                        if getattr(calculation_plan, "domain", "") == domain_resolver.CONVERSION
                        else turn_evidence.CALCULATION
                    ),
                )
            else:
                # Use the router's self-contained interpretation so a short
                # follow-up such as "How much did I make?" retains the
                # values from the immediately preceding turns without
                # loading personal memory. This is the fallback when the
                # planner's own request fails or produces untrusted output.
                messages = self._build_factual_messages(
                    route.normalized_request,
                    reset_history=not turn_context.inherit_history,
                    followup_subject=self._followup_subject_for(
                        route, goal_intent_result,
                    ),
                )
        elif route.intent == "time_question":
            clock = self.build_time_context(
                route.normalized_request, said=user_input,
                language=self._turn_language,
            )
            turn_trace.note_evidence(clock=clock)
            self._ledger().add(turn_evidence.CLOCK, clock, source="clock")
            messages = self._build_factual_messages(
                route.normalized_request,
                clock,
                reset_history=not turn_context.inherit_history,
            )

        turn_grounding_source = ""
        turn_grounding_subject = ""

        # Recall reached far enough: answer from it rather than looking again.
        # The evidence travels the same section a live search would have
        # filled, so the answer is grounded in it identically -- which is what
        # makes the reply actually name the hotels from the previous turn
        # instead of merely declining to search.
        if recalled_evidence and decision.reuses_existing_results:
            self._ledger().add(
                turn_evidence.RECALL, recalled_evidence,
                source="earlier in this conversation", carried=True,
            )
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
                resolved_query = (
                    resolved.search_query if resolved is not None
                    else self._resolved_search_query(
                        route, goal_intent_result, said=user_input,
                    )
                )
                research_result = self._research_for_recommendation(
                    resolved_query,
                    resolution=getattr(capability, "execution_preference", None),
                    resolved=resolved,
                    said=user_input,
                ) or self.research_agent.research(
                    request=route.normalized_request,
                    search_query=resolved_query,
                    max_results=5,
                    verify=route.verification_required,
                    query_is_resolved=resolved is not None,
                    # The router's own wording, when the query above is the
                    # person's words in the other language. See
                    # ResearchAgent.research for the turn that needed it.
                    alternate_query=(
                        route.search_query
                        if str(route.search_query or "").strip()
                        != str(resolved_query or "").strip()
                        else ""
                    ),
                )
                self._last_search_query = research_result.queries[0]
                self._last_research_evidence = research_result.evidence
                self._ledger().add(
                    turn_evidence.SEARCH, research_result.evidence,
                    source=research_result.queries[0],
                )
                turn_trace.note_evidence(
                    search_queries=list(research_result.queries),
                    research=research_result.evidence,
                )
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
                self.action_ledger.settled(succeeded=True)
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
                forced_response = capability_contract.failed(
                    "web_search",
                    "unreachable",
                    detail=capability_contract.describe(error),
                    language=self._turn_language,
                ).spoken(self._turn_language)
                # The commitment stands -- she did go and look -- but the
                # record must not say the lookup returned anything. Phase
                # 4E's distinction between dispatch, execution and goal
                # completion is only worth having if a failure reaches it.
                self.action_ledger.failed(
                    f"the search raised {type(error).__name__}",
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
                    turn_trace.note_evidence(
                        search_queries=[route.search_query],
                        research=str(search_result),
                    )
                    self._ledger().add(
                        turn_evidence.SEARCH, str(search_result),
                        source=route.search_query,
                    )
                    # The correction framing only when there is a correction.
                    # fact_check is also where "is there a casino on that
                    # island?" lands -- a question, not a dispute -- and told
                    # to reconcile a correction that did not exist, the model
                    # apologised for one: "이전에 말씀드린 내용이 정확하지
                    # 않았습니다", about a claim she had never made.
                    if grounded_values.reads_as_dispute(user_input):
                        instruction = (
                            f"Reconcile the user's correction with the recent "
                            f"grounded context: {route.normalized_request}. "
                            "If Elaina's earlier statement was wrong, say so "
                            "directly and acknowledge that the user was right."
                        )
                    else:
                        # The person's own words go in as well as the
                        # router's paraphrase. The paraphrase of "was it
                        # Bainbridge Island?" is a question about which
                        # island it was, and answering *that* from the
                        # results produced "The island near Seattle is
                        # Whidbey Island" -- overruling a guess that was
                        # right, because the guess was no longer in the
                        # question.
                        instruction = (
                            f"Answer this from the search results: "
                            f"{route.normalized_request} (the user's own "
                            f"words: {user_input}). If the user offered a "
                            "guess, say whether the results support it. The "
                            "user is asking, not correcting: do not say "
                            "anything earlier was wrong unless the results "
                            "show that it was."
                        )
                    messages = self._build_factual_messages(
                        instruction,
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
                    forced_response = capability_contract.failed(
                        "web_search",
                        "unreachable",
                        detail=capability_contract.describe(error),
                        language=self._turn_language,
                    ).spoken(self._turn_language)
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
                turn_trace.note_evidence(
                    search_queries=[corrected_query],
                    research=str(search_result),
                )
                self._ledger().add(
                    turn_evidence.SEARCH, str(search_result),
                    source=corrected_query,
                )
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
                    f"Got it, the name is {corrected_entity}. "
                    + capability_contract.failed(
                        "web_search",
                        "unreachable",
                        detail=capability_contract.describe(error),
                        language=self._turn_language,
                    ).spoken(self._turn_language)
                )
            finally:
                timings["web_search"] = (
                    time.perf_counter() - search_started
                )

        if route.intent == "pending_approval":
            forced_response = (
                f"The {self._pending_action or 'action'} proposal is still "
                "waiting on screen. Review it and use the approval or "
                "rejection button there."
            )

        if route.intent == "agent_create":
            if self._pending_action:
                forced_response = (
                    f"A {self._pending_action} proposal is already waiting on "
                    "screen. Review it before creating another capability."
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
                    f"A {self._pending_action} proposal is already waiting on "
                    "screen. Review it before preparing another event."
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
                    # Asked from the contract, in the language of the
                    # turn. The agent knows *which* inputs are missing;
                    # it cannot know what language to ask in, and its own
                    # sentence was English whatever the person had said.
                    forced_response = (
                        capability_contract.ask_for(
                            "calendar_action",
                            calendar_result.missing,
                            language=self._turn_language,
                        )
                        or calendar_result.message
                    )

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
                    f"A {self._pending_action} proposal is already waiting on "
                    "screen. Review it before creating another action."
                )
            else:
                project_started = time.perf_counter()
                self._prepare_git_action()
                timings["project_tools"] = (
                    time.perf_counter() - project_started
                )
                if self._pending_action == "Git":
                    forced_response = (
                        "The Git proposal is ready on screen. Nothing has "
                        "been committed or pushed; review it and choose Commit "
                        "& Push, Commit Only, or Reject."
                    )
                else:
                    # It used to end "; check the console error." There is
                    # no console in front of the person -- they are talking
                    # to her, and that clause is the software asking to be
                    # debugged. The detail is already printed by
                    # _prepare_git_action.
                    forced_response = capability_contract.failed(
                        "git",
                        "proposal_failed",
                        detail="the git proposal came back unusable",
                        language=self._turn_language,
                    ).spoken(self._turn_language)

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
                    f"A {self._pending_action} proposal is already waiting on "
                    "screen. Review it before creating another change."
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
                        "The project change proposal is ready on screen. No "
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
        # Every site that produces words for the person reads this one
        # variable, which is what made the split a one-line change here
        # rather than a hunt: realization, retries, rewrites, the
        # finaliser and the streaming answer itself all take active_model.
        active_model = (
            self.vision_model if uses_vision_model
            else self.conversation_model
        )
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
                inherit_history=turn_context.inherit_history,
            )
            forced_response = ""

        detailed_response = route.detailed_response
        calculation_response = route.intent == "calculation"
        # The same completeness question for a value she was asked for but
        # did not have to calculate. Measured: "What is the freezing point
        # of water in Fahrenheit?" -> "This is the temperature at which
        # water turns into ice under standard atmospheric conditions." Only
        # the checks below change; how she is told to answer does not.
        value_response = (
            calculation_response
            or AnswerCompletionGuard.asks_for_a_value(user_input)
        )
        # How much room this reply gets, from what it is for (Phase 3D,
        # brain/response_budget.py). The single 45-word, two-sentence
        # ceiling squeezed every explanation to a median of 32 words; a
        # value keeps that ceiling, an explanation gets room for what the
        # thing is for and one concrete case, and a re-explanation or an
        # explicit request for depth gets room to take another route.
        budget = response_budget.budget_for(
            intent=route.intent,
            detailed=detailed_response,
            value_response=value_response,
            recommendation=bool(
                route.recommendation_needed or route.speech_act == "advice"
            ),
            speech_act=route.speech_act,
            answer_shape=getattr(route, "answer_shape", ""),
            config=self.config,
        )
        max_words, max_sentences = budget.max_words, budget.max_sentences
        # What this reply is *doing*, which is not what the user asked for.
        # One name, decided once, and every style decision below reads it:
        # the length ceiling, the prompt's own instructions, and the review
        # that decides whether a draft is worth saying again. Before this
        # existed each of those three answered the question separately, and
        # they did not always agree -- which is how the same session could
        # answer "ok" with a paragraph and a tool result with a status line.
        turn_act = conversation_style.act_for_turn(
            intent=route.intent,
            speech_act=route.speech_act,
            user_input=user_input,
            action_performed=action_performed,
            has_tool_result=bool(locked_response or forced_response),
            awaiting_consent=self.action_ledger.offer_pending,
            awaiting_clarification=self.clarification.peek() is not None,
            is_greeting=bool(_SIMPLE_GREETING.fullmatch(user_input)),
            is_farewell=social_lines.reads_as_farewell(user_input),
        )
        style = conversation_style.contract_for(turn_act)
        print(f"[Style] act={turn_act} sentences<={style.max_sentences} "
              f"offers<={style.offers_allowed} "
              f"reword={'yes' if style.may_reword else 'no'}")
        # The act tightens the configured length and never loosens it. A
        # receipt is one clause whatever config.yaml permits.
        #
        # Except for an answer whose budget is an explanation. The answer
        # contract's sentence count is a general figure about how long an
        # answer usually runs; the budget is this turn's own reading of what
        # the answer is for, and an explanation or a request for depth is
        # exactly the case where length is the point.
        if budget.kind == response_budget.VALUE or turn_act != conversation_style.ANSWER:
            max_sentences = (
                min(max_sentences, style.max_sentences) if max_sentences > 0
                else style.max_sentences
            )
            # The same, in words, for the acts whose whole job is to be
            # short. Sentences turned out to be the wrong unit for those --
            # two clauses is what a person says, and the length that makes
            # a receipt sound like an assistant is measured in words.
            if style.max_words > 0:
                max_words = (
                    min(max_words, style.max_words) if max_words > 0
                    else style.max_words
                )
        response_limits = ResponseLimits(
            max_words=max_words,
            max_sentences=max_sentences,
            goal=(
                budget.goal(self._turn_language)
                if turn_act == conversation_style.ANSWER else ""
            ),
        )
        # What the person already gave her, for the completeness checks
        # below: a calculation answer made only of the question's own
        # numbers has not answered it. Both spellings count as given --
        # the router rewrites "what's 2+2" to "2 + 2", and either may be
        # the one the model echoed back.
        asked_with_values = f"{user_input} {route.normalized_request or ''}"
        recommendation_response = (
            (route.recommendation_needed or route.speech_act == "advice")
            # Except when they are declining one. Measured live on the
            # contamination matrix: "아니야", after two film turns, was
            # answered with another film -- the rewrite ran the advice
            # finalizer, whose prompt is "give the direct recommendation
            # first", on a turn whose whole content was no. A receipt asks
            # for nothing, so there is nothing to recommend; whether she
            # *had* a recommendation in hand is the previous turn's
            # question, not this one's.
            and turn_act != conversation_style.RECEIPT
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
            language=self._turn_language,
        )
        # The first unverified calculation draft is generated without a
        # length target so it can show brief working before the result;
        # response_instruction (with the real voice-length limits) is used
        # later only to condense a complete draft that ran long, never to
        # constrain this first pass.
        generation_instruction = (
            ResponseLimits().instruction(
                calculation=True, language=self._turn_language,
            )
            if calculation_needs_own_math
            else response_instruction
        )
        turn_trace.note_context(
            act=turn_act,
            limits={
                "max_words": max_words,
                "max_sentences": max_sentences,
                "detailed": detailed_response,
                "budget": budget.kind,
                "shape": budget.shape,
            },
            value_response=value_response,
            recommendation_response=recommendation_response,
            calculation_verified=calculation_verified,
            model=active_model,
            temperature=active_temperature,
        )
        if calculation_needs_own_math:
            messages[-1]["content"] += (
                "\n\nRESOLVED CALCULATION REQUEST\n"
                f"{route.normalized_request}"
            )
        messages[-1]["content"] += (
            "\n\nVOICE RESPONSE REQUIREMENTS\n"
            f"{generation_instruction}"
            # Last, and act-specific, so the rules nearest the message being
            # answered are the ones about how a person performing *this*
            # act would say it. personality.txt says who she is once, at the
            # top of a long prompt; this says what this particular reply is
            # for, which is the part the model was losing.
            f"\n\n{conversation_style.style_instruction(turn_act, self._turn_language)}"
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
            first_token_at = None
            for chunk in response_stream:
                if turn_cancel.is_set():
                    break
                message = chunk.get("message")
                if message:
                    content = str(message.get("content", ""))
                    if content and first_token_at is None:
                        # Time to first token, not total generation. They are
                        # different numbers and only this one describes how
                        # long the person waits before anything appears.
                        first_token_at = time.perf_counter()
                        timing.mark(
                            "ttft", first_token_at - generation_started,
                        )
                    parts.append(content)
                if chunk.get("done") and chunk.get("done_reason") == "length":
                    ran_out_of_budget = True
            return "".join(parts)

        generation_started = time.perf_counter()
        try:
            if effective_forced_response:
                # Verification failures are enforced here instead of asking
                # the vision model to voluntarily avoid a confident guess.
                raw_reply = effective_forced_response
                turn_trace.draft(raw_reply, source="locked")
            else:
                raw_reply = collect_answer()
                turn_trace.draft(raw_reply, source="model", model=active_model)
                if ran_out_of_budget:
                    finished = _drop_unfinished_sentence(raw_reply)
                    if finished != raw_reply.rstrip():
                        print(
                            "\n[Response Guard] Generation hit the length "
                            "budget; dropped the unfinished sentence."
                        )
                        raw_reply = turn_trace.step(
                            "budget_cut", raw_reply, finished,
                        )

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
                raw_reply = turn_trace.step(
                    "vision_retry", raw_reply,
                    self._value(
                        direct_message,
                        "content",
                        "",
                    ),
                )

            # The reply every stage below works on is display text: the
            # HARD invariants and damage repair, nothing said-for-the-ear.
            # Speech is realized separately, at the audio boundary
            # (brain/realize.py, docs/PHASE3_PLAN.md 3A).
            reply = turn_trace.step(
                "final_form", raw_reply, realize.display(raw_reply),
            )
            if (
                not effective_forced_response
                and AnswerCompletionGuard.needs_retry(
                    reply,
                    calculation=value_response,
                    question=asked_with_values,
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
                completion_reply = realize.display(completion_raw)
                if (
                    completion_reply
                    and not AnswerCompletionGuard.needs_retry(
                        completion_reply,
                        calculation=value_response,
                        question=asked_with_values,
                    )
                ):
                    raw_reply = completion_raw
                    reply = turn_trace.step(
                        "completion_retry", reply, completion_reply,
                    )
                else:
                    # Asked twice, and no number either time. Measured live
                    # on the contamination matrix: the answer went out as
                    # "That's the result." A third request would be the
                    # same request; the value is computed instead.
                    computed = self._say_the_arithmetic(
                        route.normalized_request
                    )
                    if computed:
                        print(
                            "[Response Guard] Still no result after the "
                            f"retry; computed it: {computed!r}"
                        )
                        raw_reply = computed
                        reply = turn_trace.step(
                            "computed_result", reply, computed,
                        )
            # The answer as it stood while it was still known to state a
            # result. Everything below may replace it -- a repetition
            # retry, the advice rewrite, the voice pass, a dozen guards --
            # and none of them asks the completeness question again.
            answered_calculation = (
                reply
                if value_response and not effective_forced_response
                and not AnswerCompletionGuard.needs_retry(
                    reply,
                    calculation=True,
                    question=asked_with_values,
                )
                else ""
            )
            if (
                route.intent in {
                    "conversation",
                    "calculation",
                    "agent_offer",
                }
                and not effective_forced_response
                and response_stages.active("repetition_retry")
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
                retry_reply = realize.display(retry_raw)
                if retry_reply:
                    # The retry is checked the way the draft was. Measured
                    # live: "I like strawberries." was answered "You're
                    # welcome -- strawberries are tasty. Want to try
                    # some?" -- the guard caught the stale courtesy
                    # opener, regenerated, and the regeneration opened
                    # with it again, unexamined. Nobody had said thank
                    # you, so the clause is simply removed; the rest of
                    # the answer is about strawberries and is fine.
                    trimmed = ResponseQualityGuard.without_stale_courtesy(
                        retry_reply, user_input,
                    )
                    if trimmed != retry_reply:
                        print(
                            "[Response Guard] The retry opened with a "
                            "courtesy nobody had earned; removed it."
                        )
                    raw_reply = retry_raw
                    reply = turn_trace.step(
                        "repetition_retry", reply, trimmed or retry_reply,
                    )

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
                and response_stages.active("length_rewrite")
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
                rewrite_reply = realize.display(
                    self._value(rewrite_message, "content", ""),
                )
                rewrite_complete = not AnswerCompletionGuard.needs_retry(
                    rewrite_reply,
                    calculation=value_response,
                    question=asked_with_values,
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
                    and self._rewrite_is_usable(
                        rewrite_reply, user_input=user_input,
                    )
                ):
                    reply = turn_trace.step(
                        "length_rewrite", reply, rewrite_reply,
                    )
                else:
                    if (recommendation_response and not route.urgent_safety
                            and response_stages.active("advice_finalizer")):
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
                        finalizer_reply = realize.display(
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
                            and self._rewrite_is_usable(
                                finalizer_reply, user_input=user_input,
                            )
                        )
                        if finalizer_valid:
                            reply = turn_trace.step(
                                "advice_finalizer", reply, finalizer_reply,
                            )
                    print(
                        "\n[Response Rewrite] The first rewrite was not "
                        "complete; applied the advice fallback when valid: "
                        f"{str(rewrite_reply)[:120]!r}"
                    )
            if (recommendation_response
                    and response_stages.active("merge_extra_sentences")):
                reply = turn_trace.step(
                    "merge_extra_sentences", reply,
                    response_limits.merge_extra_sentences(reply),
                )
            if effective_forced_response and response_stages.active("condense"):
                # A verified tool or planner result never went through the
                # generation-length path above, so this is its only chance
                # to become listenable. The condenser refuses any rewrite
                # that changes a value, so a long result survives intact
                # rather than being trimmed into something wrong.
                reply = turn_trace.step(
                    "condense", reply,
                    self.answer_condenser.condense(
                        reply,
                        max_words=max_words,
                        max_sentences=max_sentences,
                        goal=route.normalized_request or user_input,
                    ),
                )
            # Say it like a person, then let every truth guard below judge
            # what came back. Deliberately placed *before* them and not
            # after: a realized sentence is still a claim, and it has to
            # pass the same action-commitment, grounded-value and
            # named-candidate checks as a generated one. Nothing here is
            # trusted more than the draft it replaced.
            reply = turn_trace.step(
                "her_voice", reply,
                self._say_it_in_her_voice(
                    reply,
                    act=turn_act,
                    user_input=user_input,
                    model=active_model,
                    keep_alive=active_keep_alive,
                    max_words=max_words,
                ),
            )
            # Told something, and the answer did not show she understood it
            # ("내 여동생은 부산에 살아" -> "제가 잘 지내고 있습니다").
            acknowledgement = (
                self._acknowledgement_if_missed(user_input, reply)
                if response_stages.active("acknowledgement") else None
            )
            if acknowledgement:
                reply = turn_trace.step("acknowledgement", reply, acknowledgement)
            # They said which one they meant, and the answer is not about it.
            # Only an answer from what she knows: a searched answer asked
            # again without its evidence came back "파리의 실시간 날씨를
            # 검색해 보겠습니다" -- a promise in place of the weather.
            # And only her last answer again: an answer about the right one
            # that does not name it ("…approximately 68,000") is kept --
            # asked again without the history, it came back 685,000.
            corrected_to = str(getattr(self, "_corrected_to", "") or "").strip()
            if (corrected_to and "web_search" not in timings
                    and not effective_forced_response
                    and not self._names_what_they_meant(reply, corrected_to)
                    and told_not_asked.repeats(reply, self._things_she_has_said()[0])):
                retried = self._answer_the_corrected_question(user_input, corrected_to)
                if retried:
                    reply = turn_trace.step("corrected_question", reply, retried)
            # And a value question answered with the previous answer.
            if value_response and not effective_forced_response:
                reply = turn_trace.step(
                    "not_her_last_answer", reply,
                    self._not_her_last_answer(user_input, reply),
                )
                if "web_search" not in timings:
                    reply = turn_trace.step(
                        "encyclopedia", reply,
                        self._checked_against_the_encyclopedia(user_input, reply),
                    )
            # A fact they told her is not asked back to them. The prompt
            # already says so, and "저 다음 달에 이사가요" still came back
            # "다음 달에 이사하시나요?" (brain/told_not_asked.py).
            asked_back = (
                told_not_asked.without_asking_back(reply, user_input)
                if response_stages.active("told_not_asked") else reply
            )
            if asked_back != reply:
                print("[Style] Took out a question that only repeated what "
                      "they told me.")
                reply = turn_trace.step(
                    "told_not_asked", reply,
                    asked_back or guard_lines.say("noted_fact", self._turn_language),
                )
            # Checked again after the voice pass, not only in the speech
            # filter before it. The reply that leaked kana live had been
            # regenerated once and rewritten twice already, and ``_resay``
            # asks the model for a fresh sentence -- so the last string
            # anything produced is the one that has to be clean.
            without_kana = TextFilter.without_foreign_script(reply)
            if without_kana != reply:
                reply = turn_trace.step(
                    "foreign_script", reply,
                    without_kana or guard_lines.say(
                        "no_response", self._turn_language,
                    ),
                )
            reply = turn_trace.step(
                "turn_language", reply,
                self._answered_in_the_turns_language(
                    reply,
                    model=active_model,
                    keep_alive=active_keep_alive,
                    max_words=max_words,
                ),
            )
            reply = turn_trace.step(
                "action_commitment", reply,
                self._enforce_action_commitment(
                    reply,
                    user_input=user_input,
                    action_performed=action_performed,
                ),
            )
            reply = turn_trace.step(
                "grounded_values", reply,
                self._enforce_grounded_values(
                    reply,
                    user_input=user_input,
                    action_performed=action_performed,
                    trusted_result=bool(effective_forced_response),
                    searched="web_search" in timings,
                ),
            )
            reply = turn_trace.step(
                "existence_claims", reply,
                self._enforce_existence_claims(
                    reply,
                    research_evidence=self._last_research_evidence,
                    searched="web_search" in timings,
                ),
            )
            active_problem = self.task_sessions.active_recommendation()
            if not turn_context.inherit_history and active_problem is not None:
                # The candidates belong to the subject we just left. Moving
                # the history line without moving this one produced, live:
                #
                #   User:   actually forget the mouse, what's a good film
                #           for tonight?
                #   Elaina: A perfect pick for tonight? The one I actually
                #           found is AmazonBasics Wireless Mouse.
                #
                # The reply was assembled by the guards that name what a
                # search found, reading a result set from the old subject.
                # Held results are as inheritable as held turns.
                print("[Context] The held results are about the old subject; "
                      "not naming them.")
                active_problem = None
            # A structured browser result is the last word on what the
            # machine did. These two guards read a reply as an answer about
            # listings; a page-click failure is not one, and rewriting it
            # as though it were replaced the answer with search language.
            if not self._browser_result_is_final:
                reply = turn_trace.step(
                    "named_recommendation", reply,
                    self._enforce_named_recommendation(
                        reply,
                        candidates=(
                            active_problem.candidates if active_problem else ()
                        ),
                        searched="web_search" in timings,
                        evidence=self._last_research_evidence,
                        request=user_input,
                        recommendation=recommendation_response,
                    ),
                )
            if not self._browser_result_is_final:
                # After the "did it find anything" guards, and asking the
                # question they do not: is what she named one of them.
                reply = turn_trace.step(
                    "named_candidates", reply,
                    self._enforce_named_candidates(
                        reply,
                        candidates=(
                            active_problem.candidates if active_problem else ()
                        ),
                        searched="web_search" in timings,
                        request=user_input,
                    ),
                )
            if not self._browser_result_is_final:
                reply = turn_trace.step(
                    "found_claim", reply,
                    self._enforce_found_claim(
                        reply,
                        candidates=(
                            active_problem.candidates if active_problem else ()
                        ),
                    ),
                )
            reply = turn_trace.step(
                "grounded_entities", reply,
                self._enforce_grounded_entities(
                    reply,
                    user_input=user_input,
                    action_performed=action_performed,
                    evidence=self._last_research_evidence,
                    trusted_result=bool(effective_forced_response),
                ),
            )
            reply = turn_trace.step(
                "invented_capability", reply,
                self._refuse_invented_capability(reply, user_input),
            )
            reply = turn_trace.step(
                "unearned_success", reply,
                self._refuse_unearned_success(
                    reply, action_performed=action_performed,
                ),
            )
            reply = turn_trace.step(
                "unobserved_app_activity", reply,
                self._refuse_unobserved_app_activity(reply),
            )
            # Before the strip, deliberately. An offer she made in her own
            # words is not filler -- it is only dishonest when nothing is
            # waiting for the answer. Park something, and it survives the
            # strip below on its own merits.
            reply = turn_trace.step(
                "ground_offer_language", reply,
                self._ground_offer_language(
                    reply,
                    decision=decision,
                    capability=capability,
                    goal=goal_intent_result,
                    route=route,
                    action_performed=action_performed,
                ),
            )
            # And the mirror image: a question about something the user
            # already asked for is the same disagreement with the record.
            reply = turn_trace.step(
                "redundant_permission", reply,
                self._refuse_redundant_permission(
                    reply,
                    decision=decision,
                    route=route,
                    action_performed=action_performed,
                ),
            )
            # Last, so it also catches a footer a rewrite reintroduced. Her
            # personality file bans these outright and the model adds them
            # anyway, so the removal is code rather than more prompt wording.
            before_strip = reply
            if response_stages.active("closing_offer"):
                reply = turn_trace.step(
                    "closing_offer", reply,
                    ClosingOfferGuard.strip(
                        reply,
                        # A repair guard, the ability answer or the grounding
                        # pass above may have parked an offer whose text is in
                        # this reply. Removing it would leave the gate holding
                        # an offer the user never saw. Read from the ledger,
                        # which is the one authority on whether an offer is
                        # genuinely open.
                        keep_offers=(
                            self.action_ledger.offer_pending
                            or self._invitation_stands
                        ),
                    ),
                )
            if reply != before_strip:
                removed = before_strip[len(reply):].strip()
                if ClosingOfferGuard.offers_to_act(removed):
                    # Worth counting separately: this is the model making an
                    # offer outside the policy, which is what the policy is
                    # there to bound.
                    print(f"[Recommendation] Removed the model's own offer: "
                          f"{removed[:80]!r}")
            # After the strip: it removes trailing filler, and what is
            # left may still hold a second question about the same offer.
            if response_stages.active("one_offer"):
                reply = turn_trace.step(
                    "one_offer", reply, self._one_offer_per_reply(reply),
                )
            if not self._browser_result_is_final:
                reply = turn_trace.step(
                    "report_found", reply,
                    self._report_what_was_found(
                        reply,
                        candidates=(
                            active_problem.candidates if active_problem else ()
                        ),
                        searched="web_search" in timings,
                        about=(
                            active_problem.subject if active_problem else ""
                        ),
                        said=user_input,
                    ),
                )
            # After the guard, deliberately: a real offer names a capability
            # and a subject, and stripping it as filler would remove the one
            # useful thing this phase adds.
            if response_stages.active("append_recommendation"):
                reply = turn_trace.step(
                    "append_recommendation", reply,
                    self._append_recommendation(
                        reply, decision=decision, capability=capability,
                        goal=goal_intent_result, act=turn_act,
                    ),
                )
            checked_draft = reply
            if response_stages.active("final_check"):
                reply = turn_trace.step(
                    "final_check", reply,
                    self._final_response_check(
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
                        act=turn_act,
                    ),
                )
            if reply != checked_draft:
                # The repetition retry is the final model pass. Its output
                # must pass the same factual/action boundaries as the draft;
                # there are no model rewrites after these checks.
                reply = turn_trace.step(
                    "action_commitment", reply,
                    self._enforce_action_commitment(
                        reply, user_input=user_input,
                        action_performed=action_performed,
                    ),
                )
                reply = turn_trace.step(
                    "grounded_values", reply,
                    self._enforce_grounded_values(
                        reply, user_input=user_input,
                        action_performed=action_performed,
                        trusted_result=False, searched="web_search" in timings,
                    ),
                )
                if not self._browser_result_is_final:
                    reply = turn_trace.step(
                        "found_claim", reply,
                        self._enforce_found_claim(
                            reply,
                            candidates=(
                                active_problem.candidates
                                if active_problem else ()
                            ),
                        ),
                    )
                reply = turn_trace.step(
                    "grounded_entities", reply,
                    self._enforce_grounded_entities(
                        reply, user_input=user_input,
                        action_performed=action_performed,
                        evidence=self._last_research_evidence,
                        trusted_result=False,
                    ),
                )
                if response_stages.active("closing_offer"):
                    reply = turn_trace.step(
                        "closing_offer", reply,
                        ClosingOfferGuard.strip(
                            reply,
                            keep_offers=(
                                self.action_ledger.offer_pending
                                or self._invitation_stands
                            ),
                        ),
                    )
                if response_stages.active("one_offer"):
                    reply = turn_trace.step(
                        "one_offer", reply, self._one_offer_per_reply(reply),
                    )
                if not self._browser_result_is_final:
                    reply = turn_trace.step(
                        "report_found", reply,
                        self._report_what_was_found(
                            reply,
                            candidates=(
                                active_problem.candidates
                                if active_problem else ()
                            ),
                            searched="web_search" in timings,
                            about=(
                                active_problem.subject
                                if active_problem else ""
                            ),
                            said=user_input,
                        ),
                    )
            # The true end of the line, after every guard and every
            # appender. It has to be here and not earlier: the offer this
            # turn may append is added *after* the first pass has run,
            # which is how "Happy to dig into a product_recommendation if
            # that helps" reached a user with the underscore still in it.
            # Anything added past the first pass needs a pass past it. The
            # same display realization as the first -- no speech shaping:
            # dashes, notation and line breaks are the screen's to keep.
            reply = turn_trace.step("final_form", reply, realize.display(reply))
            # Judged again on what they will actually hear. A guard after
            # the first check can take out what showed she understood --
            # measured: "다음 주 금요일에 시애틀로 돌아가시는군요" removed as
            # "a restatement of the current message", leaving "일정이
            # 궁금하시다면 알려주시면 도와드리겠습니다" -- and what they took
            # for granted is checked against the finished answer.
            corrected_premise = self._premise_corrected(user_input, reply)
            if corrected_premise:
                reply = turn_trace.step(
                    "premise_correction", reply,
                    realize.display(corrected_premise),
                )
            else:
                acknowledgement = (
                    self._acknowledgement_if_missed(user_input, reply)
                    if response_stages.active("acknowledgement") else None
                )
                if acknowledgement:
                    reply = turn_trace.step(
                        "acknowledgement", reply,
                        realize.display(acknowledgement),
                    )
            theirs = (
                told_not_asked.as_theirs(reply, user_input)
                if response_stages.active("as_theirs") else reply
            )
            if theirs != reply:
                print("[Style] Their \"my\" was said back as hers; put right.")
                reply = turn_trace.step("as_theirs", reply, theirs)
            if response_stages.active("their_name"):
                reply = turn_trace.step(
                    "their_name", reply,
                    self._their_name_not_hers(user_input, reply),
                )
            # The number she worked out, still in the answer. Measured
            # live on "what's 2+2" after three turns of sympathy: the reply
            # came back "That's the result of 2 plus 2." and, on another
            # run, "That's the result." -- the operands survived and the
            # answer did not. Which stage dropped it varies; that none of
            # them looks does not, so the last word on a calculation is
            # here, where there is nothing left to undo it.
            if answered_calculation and AnswerCompletionGuard.dropped_the_result(
                reply, draft=answered_calculation, question=asked_with_values,
            ):
                said_it = AnswerCompletionGuard.the_result_sentence(
                    answered_calculation, question=asked_with_values,
                )
                # Only within one language. The draft and the finished
                # reply are the same answer in different words; if a later
                # stage answered in the other language, its sentence is the
                # one the person can read.
                korean = any("가" <= ch <= "힣" for ch in said_it)
                if said_it and korean == any(
                    "가" <= ch <= "힣" for ch in reply
                ):
                    print(
                        "\n[Response Guard] The answer lost the value it "
                        f"worked out; saying it: {said_it[:60]!r}"
                    )
                    reply = turn_trace.step("result_restored", reply, said_it)
            # 몇 도 from a Korean speaker means Celsius. The market is the
            # United States and English answers stay in Fahrenheit; the
            # unit follows the language of the question, not the market
            # (brain/units.py).
            #
            # Last, and after the guard just above, because that guard
            # undid it: it compares the finished reply against the draft,
            # saw 212 replaced by 100 and restored the Fahrenheit draft.
            # Measured twice, one line after the other: "[Units] ... said
            # in Celsius" then "[Response Guard] The answer lost the value
            # it worked out".
            if self._turn_language == "ko":
                in_celsius = units.in_celsius(reply, user_input)
                if in_celsius != reply:
                    print("[Units] Fahrenheit in a Korean answer; "
                          "said in Celsius.")
                    reply = turn_trace.step("celsius", reply, in_celsius)
            # What she took a misheard name to be, said with the answer so
            # the person can still correct it (brain/near_miss.py). After
            # the filter, so the answer's own sentences are not the ones
            # trimmed to make room for it.
            if self._slip_assumed and reply.strip():
                reply = turn_trace.step(
                    "near_miss_prefix", reply, f"{self._slip_assumed} {reply}",
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
            turn_trace.note_outcome("error", f"{type(error).__name__}: {error}")
        timings["generation"] = time.perf_counter() - generation_started

        if turn_cancel.is_set():
            print("\n[ChatEngine] Response interrupted.")
            turn_trace.note_outcome("interrupted")
            turn_trace.note_timings(timings)
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
                reply = turn_trace.step(
                    "empty_fallback", reply,
                    realize.display(tool_result_fallback)
                    or guard_lines.say("no_response", self._turn_language),
                )
            elif uses_vision_model:
                # Nothing rephrases this one -- it is the reply. It used to
                # name Ollama and a model tag, in English, to someone who
                # had asked what was on their screen.
                reply = turn_trace.step(
                    "empty_fallback", reply,
                    capability_contract.failed(
                        "screen_analysis",
                        "capture_failed",
                        detail=(
                            f"vision model {self.vision_model!r} returned nothing"
                        ),
                        language=self._turn_language,
                    ).spoken(self._turn_language),
                )
            else:
                reply = turn_trace.step(
                    "empty_fallback", reply,
                    guard_lines.say("no_response", self._turn_language),
                )

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
        turn_trace.display(reply)
        self.events.emit(
            "assistant_finished",
            text=reply,
        )

        # And the optional structured half of it. A separate event, so a
        # client that has never heard of surfaces -- and every reply that
        # does not have one -- behaves exactly as it did before.
        surface = self._surface_for(user_input, searched="web_search" in timings)
        if surface:
            # Pictures are added here rather than inside the decision, which
            # stays pure: whether there is a surface is reasoning, what it
            # looks like is not. This runs after the reply has been sent, so
            # a slow image index delays nothing anybody is waiting on.
            surface = self.illustrate_surface(surface)
            payload = surface.payload()
            turn_trace.note_context(surface={
                "type": payload["type"],
                "items": [item["name"] for item in payload["items"]],
            })
            surface_log.note(surface.log_line())
            surface_log.note(
                f"[Surface] emitting assistant_surface: type={payload['type']} "
                f"items={len(payload['items'])} "
                f"names={[item['name'][:34] for item in payload['items']]} "
                f"images={sum(1 for item in payload['items'] if item['image'])}"
            )
            self.events.emit("assistant_surface", **payload)

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

        # Memory is a property of the sentence, not of the route.
        #
        # This used to require route.intent == "conversation", and
        # "conversation" is one of twenty-four intents. The turns most
        # likely to contain a durable fact about someone are the turns
        # where they are asking for something -- "I'm allergic to
        # shellfish, find me somewhere for dinner" is a web_search, and
        # the allergy was dropped on the floor. Measured across the recall
        # matrix: the intent gate could see 3 of the 16 turns that needed
        # memory, before the model's boolean narrowed it further.
        #
        # Either gate saying yes is enough. A wrong yes costs a little
        # latency; a wrong no loses something the person told you once.
        if self.memory_enabled and (
            (
                route.intent == "conversation" and route.memory_candidate
                # A question states nothing; the extractor filled one in
                # anyway ("내가 무슨 전공인지 기억해?" -> "The user studies
                # Electrical Engineering").
                and not memory_gate.is_question(user_input)
            )
            or memory_gate.carries_something_to_remember(user_input)
        ) and not getattr(self, "_asked_to_repeat", False):
            store = threading.Thread(
                target=self._store_memory_candidate,
                args=(user_input,),
                name="elaina-memory-store",
                daemon=True,
            )
            store.start()
            # Kept so close() can wait for it: something said right before
            # she is turned off must still be there when she is turned on.
            self._memory_stores = [
                thread for thread in getattr(self, "_memory_stores", [])
                if thread.is_alive()
            ] + [store]
            timings["memory_queue"] = 0.0

        timings["total"] = time.perf_counter() - turn_started
        # The voice loop opened this timeline before the microphone even
        # returned, so VAD and STT are already on it; the engine's own dict
        # is folded in here rather than printed separately. One turn, one
        # record, one line.
        timeline = timing.current()
        if timeline is None:
            timeline = timing.begin(label="text")
        timeline.merge(timings)
        timing.finish()
        turn_trace.note_timings(timeline.as_dict())
        if self._print_timings:
            print(timeline.summary())

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
        use_memory = self.memory_enabled and (
            (route.intent == "conversation" and route.memory_relevant)
            # "what's a good restaurant near my school?" routes to
            # web_search, so nothing was ever recalled for it and the gap
            # was filled from the model -- which is where an invented
            # university comes from. The subject filter below is what
            # keeps widening this from reintroducing contamination.
            or memory_gate.needs_what_we_know(user_input)
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
        if action_performed:
            self.action_ledger.dispatching(
                routing.capability.capability or route.intent,
                goal=route.normalized_request or user_input,
                reason="an agent capability was dispatched",
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
            self.action_ledger.dispatching(
                capability_selection.BROWSER_CONTROL,
                goal=route.normalized_request or user_input,
                reason="the capability layer chose browser control",
            )
            locked_response, computer_result = self._run_browser_capability(
                route, routing, user_input,
            )
            action_performed = bool(locked_response)
            self.action_ledger.settled(succeeded=action_performed)

        if route.intent == "computer_action" and not locked_response:
            action_performed = True
            self.action_ledger.dispatching(
                "computer_action",
                goal=route.action_target or route.normalized_request,
                reason=f"computer operation {route.computer_operation or '(none)'}",
            )
            locked_response, computer_result = self._handle_computer_action(
                route,
                approved_action=approved_computer_action,
                original_request=user_input,
                clarified_goal=clarified_goal,
                assumption=assumed_aloud,
            )

            self.action_ledger.settled(
                succeeded=(
                    computer_result.succeeded
                    if computer_result is not None else True
                ),
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
            self.action_ledger.dispatching(
                capability_selection.TASK_PLANNING,
                goal=route.normalized_request or user_input,
                reason="the task planner was chosen for this request",
            )
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
            NEEDS_CLARIFICATION,
            "fact_check",
            }
        ):
            # Carried into this turn because it is about the same subject,
            # so it is this turn's evidence too (brain/evidence.py).
            self._ledger().add(
                turn_evidence.GROUNDED, grounded_context,
                source="recent verified context", carried=True,
            )
            context_prompt += (
                "\n\nRECENT VERIFIED CONTEXT\n"
                f"{grounded_context}"
            )
        if route.intent == "time_question":
            clock = self.build_time_context(
                route.normalized_request, said=user_input,
                language=getattr(self, "_turn_language", "en"),
            )
            self._ledger().add(turn_evidence.CLOCK, clock, source="clock")
            context_prompt += (
                "\n\nCURRENT LOCAL TIME CONTEXT\n"
                f"{clock}"
            )
        if route.intent == "clarification":
            # The person asking about her previous answer (R9). Said as what
            # the turn is, so the answer is about that, from the
            # conversation above -- which this turn keeps.
            context_prompt += (
                "\n\nWHAT THIS TURN IS\n"
                "They are asking about your previous answer: it did not land "
                "for them. Answer about the same thing, from the conversation "
                "above, and put it differently from last time."
            )
        if route.intent == NEEDS_CLARIFICATION and route.reason:
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
        # The few things she must never be wrong about, always present
        # rather than retrieved. Deliberately small: this is the weak
        # mechanism, and the rules that actually bind are executed
        # elsewhere.
        standing = self.standing_orders.context_text()
        if standing:
            context_prompt += f"\n\nABOUT THIS PERSON\n{standing}"
        # What they have told her about themselves, in every turn -- the way
        # a person knows who they are talking to. Measured before this: ten
        # facts told, seven stored, and after a restart 0 of 11 recalled;
        # the store worked, nothing ever put it in front of her.
        told = self._what_they_told_her()
        if told:
            context_prompt += (
                "\n\nWHAT THIS PERSON HAS TOLD YOU ABOUT THEMSELVES "
                "(from earlier conversations -- you keep this when you are "
                "turned off)\n"
                + "\n".join(f"- {fact}" for fact in told)
                + "\nThese are facts about THEM -- the person talking to you, "
                "not about you, Elaina. Say them back as \"you/your\" (in "
                "Korean 님 or no pronoun), never as \"I/my/our\" (내/제/우리). "
                "When they ask about themselves -- their name, school, "
                "family, pets, plans, habits, likes -- answer from this list, "
                "in your own words and in their language. If what they ask is "
                "not on it, say they haven't told you yet; never guess a "
                "personal fact. Otherwise use a fact only when it changes your "
                "answer (a peanut allergy when suggesting a snack), and never "
                "recite the list."
            )
            # The fact that answers it, beside the question. Measured with
            # the whole list in the prompt: "What's my name?" came back
            # "Your name is Elaina." -- her own name, from the system
            # prompt, beat theirs from a list of twenty.
            if memory_gate.asks_about_themselves(user_input):
                answering = memory_gate.facts_on_topic(user_input, told)
                if answering:
                    context_prompt += (
                        "\n\nWHAT THEY TOLD YOU THAT ANSWERS THIS QUESTION "
                        "(about them, not you):\n"
                        + "\n".join(f"- {fact}" for fact in answering)
                    )
        elif memory_gate.asks_about_themselves(user_input):
            context_prompt += (
                "\n\nThey are asking about themselves, and they have not told "
                "you this. Say so plainly; never guess a personal fact."
            )
        if told_not_asked.states_an_assumption(user_input):
            context_prompt += (
                "\n\nThey are assuming something in how they ask. Check that "
                "assumption first; if it is wrong, say so plainly in your "
                "first sentence (e.g. water freezes at 0°C, not 100°C), then "
                "answer what they asked."
            )
        if getattr(self, "_corrected_from", ""):
            context_prompt += (
                "\n\nThey just corrected which one they meant. Answer the "
                "question as it now reads, and acknowledge the correction in "
                "a few words -- do not ask again what they want to know."
            )
        # Being told something is not being asked about it. Measured: "My
        # birthday is March 14th." -> "You're celebrating on March 14th.
        # Take some time for yourself."; "나 수업 끝나고 보통 젠레스 존 제로
        # 해" -> "하셨나요? 좀 힘들었겠습니다"; "다음 주 금요일에 시애틀로
        # 돌아가" echoed back as "돌아가시나요?".
        if (
            memory_gate.carries_something_to_remember(user_input)
            and not memory_gate.is_question(user_input)
        ):
            context_prompt += (
                "\n\nThey are telling you something about their own life. Take "
                "it as a fact about them -- not news that is happening today, "
                "and not a question. Acknowledge it briefly in your own words "
                "(you will remember it); do not congratulate them or wish them "
                "well for it as if it were happening now, and do not repeat it "
                "back as a question. It is theirs, not yours: never repeat "
                "their 우리/내/제/my as if it were your own (say 강아지 or "
                "여동생분, \"your dog\", \"your sister\"). If they also asked "
                "for something, answer that too."
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

        # The dispatch phase is over. This says the capability returned --
        # deliberately not that the person got what they wanted, which is a
        # different question the outcome guards below answer.
        self.action_ledger.settled(succeeded=True)
        if self.action_ledger.state != "idle":
            # One line, not a block: this fires on every acting turn.
            print(
                f"[Act] {self.action_ledger.action or '(none)'} "
                f"-> {self.action_ledger.state}"
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
        # Authority over this turn's wording starts unclaimed. Only a
        # structured machine result takes it, and only for the turn it
        # belongs to.
        self._browser_result_is_final = False
        raw_transcript = user_input
        # Set by tier0 when this turn is a clock, arithmetic or conversion
        # request it routed without the model (brain/domain_resolver.py).
        self._turn_domain_claim = None
        # Before anything reads the turn -- including the near-miss repair,
        # which would otherwise "correct" noise into something plausible.
        unclear = self._asks_to_hear_it_again(user_input)
        if unclear is not None:
            timings["route"] = time.perf_counter() - route_started
            return unclear
        # Her own abilities are a closed vocabulary, so a transcriber that
        # mishears one produces something that is not in it. Repaired here,
        # before anything reads the turn, so every layer downstream sees one
        # version of it. Measured live, session 9, mid-way through a run of
        # browser actions: "the brass control" -> "I've opened the brass
        # control."
        misheard, meant = CapabilityRegistry.repair_spoken_name(user_input)
        if misheard and meant:
            user_input = re.sub(
                re.escape(misheard), meant, user_input, count=1,
                flags=re.IGNORECASE,
            )
            print(f"[Speech Repair] {misheard!r} -> {meant!r}")
        # And the person's own repairs, which outrank everything here:
        # they wrote them down precisely because this keeps happening to
        # them, and nothing this module infers is better evidence than
        # that.
        user_input, applied = self.standing_orders.heard_as(user_input)
        if applied:
            print(f"[Standing Orders] {applied}")
        # A word that is almost what the conversation is about -- a name
        # the transcriber misheard, or "CBT" from someone who has been
        # asking about CPT. Settled before anything reads the turn, so
        # every layer below sees the version the person meant.
        self._slip_assumed = ""
        self._search_the_correction = False
        settled = self._near_miss_turn(user_input)
        if isinstance(settled, TurnRouting):
            timings["route"] = time.perf_counter() - route_started
            return settled
        user_input = settled
        # Words the transcriber was sure of that do not fit together -- the
        # garbled clip the confidence check above cannot see.
        nonsense = self._does_not_make_sense(user_input)
        if nonsense is not None:
            timings["route"] = time.perf_counter() - route_started
            return nonsense
        # "No, I meant Portland, Maine" is the previous question again, with
        # the right one in it -- not a new, empty request.
        self._corrected_from = ""
        self._corrected_to = ""
        user_input = self._with_correction_applied(user_input)
        # Recording what the person does, and what they did. Read before
        # the standing orders, which would take "stop recording" as a rule
        # to forget and "remember what I do" as a fact.
        activity = self._activity_turn(raw_transcript)
        if activity is not None:
            timings["route"] = time.perf_counter() - route_started
            return activity
        # A detail of their own life they never told her is said to be
        # unknown, in so many words -- never generated.
        not_told = self._not_told_yet(raw_transcript)
        if not_told is not None:
            timings["route"] = time.perf_counter() - route_started
            return not_told
        # "Open it." with nothing yet said to point at is a question back,
        # not a guess. Measured as the first turn of a conversation: she
        # listed her abilities and said Desktop Control Mode was off.
        nothing = self._refers_to_nothing(raw_transcript)
        if nothing is not None:
            timings["route"] = time.perf_counter() - route_started
            return nothing
        previous_focus = self.task_sessions.focus()
        # One answer per turn to "is this turn about the thing she just
        # did?". Set by the repair layer, read by the focus layer, which
        # runs after it -- because a correction to a machine target is not
        # a change of subject, and reading it as one is how "I meant only
        # one S" became a topic called "only one S" with the browser
        # action retired behind it.
        self._turn_points_at_the_last_action = False
        continuing_agent_flow = bool(
            self.agent_builder.active or self.calendar_agent.active
        )
        has_explicit_attachment = bool(
            screen_region is not None or screen_snapshot is not None
        )
        # A rule about her rules is read from the raw words, and read
        # first. Measured while building this: a `say` rule rewrote the
        # very sentence asking for it to be removed -- "forget the rule
        # about opennaver.com" became "forget the rule about naver.com"
        # and nothing was dropped. A directive may not edit the
        # instruction that manages directives.
        managed = self._note_standing_instruction(
            raw_transcript, kinds=("repair", "fact", "forget"),
        )
        if managed:
            self.clarification.clear()
            self.capability_offer.clear()
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=raw_transcript,
                    reason="The user gave a standing instruction.",
                ),
                user_input=raw_transcript,
                locked_response=managed,
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
        # After the preference reader, not before it. A preference has a
        # typed home with its own standing and confidence, which is
        # strictly better than free prose -- and "From now on use Spotify
        # whenever I ask you to play music" is one. What is left over is
        # the kind of rule nothing else models: a repair, a fact, or a
        # note in the person's own words.
        standing_reply = self._note_standing_instruction(
            raw_transcript, kinds=("note",),
        )
        if standing_reply:
            self.clarification.clear()
            self.capability_offer.clear()
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user gave a standing instruction.",
                ),
                user_input=user_input,
                locked_response=standing_reply,
            )
        # What the turn is about, decided before any pending gate gets to
        # reinterpret it. The order is the one the user set out: an
        # explicit correction, then an explicit instruction, then a report
        # about the last action, then a retry -- and a pending offer only
        # after all of those have declined it.
        #
        # Measured live, the acceptance run, twice in one session:
        #
        #     Elaina: I couldn't confirm that zillow.com loaded.
        #     You said: It's opened. Thanks.
        #     [Router] computer_action: The user accepted the offered
        #              ability.  -> open_url zillow.com again
        #
        #     You said: I meant only two S's.
        #     [Router] computer_action (0.00): The user accepted the
        #              offered ability.  -> the browser planner
        #
        # Neither was an acceptance. There was no offer worth accepting in
        # the first one, and the second was a correction to an address.
        # A turn that is nothing but a web address carries its own
        # destination, so there is nothing for a pending question to
        # decide. Measured in the acceptance run: "opennaver.com" went
        # straight to deterministic navigation and recovered, and
        # "openiss.washington.edu" one turn later became "the user
        # accepted the offered ability" and went to the planner. The only
        # difference between them was that an offer happened to be open.
        speaks_an_address = browser_navigation.looks_like_an_address(
            user_input,
        )
        has_machine_target = bool(
            self._last_computer_action and self._last_computer_goal
        )
        disputed_machine = bool(
            has_machine_target
            and browser_progress.disputes_last_action(user_input)
        )
        confirmed_machine = bool(
            has_machine_target
            and self._navigation is not None
            and not self._navigation.arrived
            and browser_progress.confirms_last_action(user_input)
        )
        corrects_machine = bool(
            has_machine_target
            and self._last_computer_action in {"open_url", "browser_action"}
            and (
                browser_progress.respelled_address(
                    self._last_computer_goal, user_input,
                )
                or browser_progress.resubstituted_address(
                    self._last_computer_goal, user_input,
                )
            )
        )
        retries_machine = bool(
            has_machine_target
            and browser_progress.continues_the_last_action(user_input)
        )
        # One reading of "does this turn beat what is pending", made here
        # and carried into the interaction decision below, so the answer is
        # stated once rather than re-derived by every layer that cares.
        self._supersedes = supersession.read(user_input)
        if self._supersedes:
            print(self._supersedes.log_line())
        if not continuing_agent_flow and (
            names_its_own_errand(user_input)
            or speaks_an_address
            or disputed_machine
            or confirmed_machine
            or corrects_machine
            or retries_machine
        ):
            # Resolve authority before any pending gate gets to reinterpret
            # the turn. An unrelated later 'yeah' cannot revive these offers.
            self._retire_pending_interpretations()
        if not continuing_agent_flow and browser_progress.disputes_the_reason(
            user_input,
        ):
            # Telling her the explanation was wrong is not asking for the
            # thing again. Measured live: browser control kept cancelling
            # itself and blaming the person, and "I'm not moving the
            # mouse" was read as accepting an offer -- so it ran again,
            # and blamed them again.
            self._retire_pending_interpretations()
            print("[Input Watch] the user disputes the interruption reason.")
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation", confidence=1.0,
                    normalized_request=user_input,
                    reason="The user disputes why the last action stopped.",
                ),
                user_input=user_input,
                locked_response=self._what_the_watcher_actually_saw(),
            )
        # Choosing between things she found is not a new command. It
        # fills the slot the standing action is missing, and that action
        # then runs -- same operation, same page, one named element.
        # Measured in the session-13 run:
        #
        #     You said: click about on this page
        #     Elaina:   I found more than one about item ... which one?
        #     You said: the first one
        #     [Reference] 'one of those' -> 'ABOUT'
        #     open_search ABOUT -> google.com/search?q=ABOUT
        #
        # A label is not an identity, and a search is not a click.
        if not continuing_agent_flow and getattr(
            self, "_page_choice", None,
        ) is not None:
            chosen = self._page_choice.choose(user_input)
            if chosen is not None:
                resumed = self._resume_page_choice(chosen)
                timings["route"] = time.perf_counter() - route_started
                return resumed
            if names_its_own_errand(user_input) or speaks_an_address:
                # They asked for something else instead. The question
                # lapses rather than lingering to catch a later "first".
                print("[Page Action] the choice lapsed; a new errand came.")
                self._page_choice = None

        if confirmed_machine:
            # She said she could not confirm it; the person can. That
            # resolves the doubt rather than starting the work again.
            self._navigation = replace(
                self._navigation, status=browser_navigation.VERIFIED,
                classification="user_confirmed", detail=user_input,
            )
            self._last_action_failed = False
            print("[Navigation] the user confirms the page is up.")
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation", confidence=1.0,
                    normalized_request=user_input,
                    reason="The user says the last action worked.",
                ),
                user_input=user_input,
                locked_response="Good -- glad that one landed.",
            )
        if disputed_machine and self._navigation is not None:
            self._navigation = replace(self._navigation, status=browser_navigation.DISPUTED,
                                       classification="user_dispute", detail=user_input)
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
                # One hard-coded string meant every "thanks" in a session
                # got the identical reply back.
                locked_response=self.action_status.select(StatusContext(
                    phase="closing", force=True,
                )) or "You're welcome.",
            )
        if (
            self.memory_enabled
            and self.memory_manager is not None
            and memory_gate.asks_to_forget(user_input)
        ):
            # Forgetting is an operation, not a mood. Before A7 there was
            # no way to delete a memory at all -- store, search, update,
            # and nothing else -- so "forget what I told you about my
            # school" changed nothing and she went on knowing it.
            #
            # Handled here, deterministically, for the same reason the
            # cancellation above is: there is nothing for a model to
            # classify, and a request to be forgotten should not depend on
            # one agreeing.
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user asked to be forgotten.",
                ),
                user_input=user_input,
                locked_response=self._forget_memories(user_input),
            )
        if (
            _CANCELLATION.fullmatch(user_input)
            and not has_explicit_attachment
            and not continuing_agent_flow
        ):
            # Calling it off. Everything outstanding is dropped and the turn
            # ends -- there is nothing here for a model to classify, and the
            # cancellation should not wait ~4.8s to take effect.
            self.cancel_active_turn()
            self._retire_pending_interpretations()
            self._retire_machine_target()
            self.clarification.clear()
            self.computer_consent.clear()
            self.task_consent.clear()
            self.task_strategy_consent.clear()
            self.capability_offer.clear()
            self.task_sessions.clear()
            self.recommendations.note_declined()
            # Nothing may now claim this action is coming. The gates are
            # empty; the ledger says why.
            self.action_ledger.cancelled("the user called it off")
            self._grounded_context = {}
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user called it off.",
                ),
                user_input=user_input,
                locked_response=self.action_status.select(StatusContext(
                    action="checking", phase="acknowledgement", force=True,
                )) or "Okay, dropped it.",
            )
        if progress_question.asks_about_progress(user_input) and not any((
            pending_computer,
            pending_task,
            pending_clarification,
        )):
            # Asking how it is going is not asking for something new, and
            # the answer is state she already holds. Measured live, this
            # got "What would you like me to do next?" while nothing was
            # running at all.
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user asked how the work in hand is going.",
                    speech_act="information_request",
                ),
                user_input=user_input,
                locked_response=self._progress_report(),
            )
        if social_lines.reads_as_frustration(user_input) and not any((
            pending_computer,
            pending_task,
            pending_clarification,
        )):
            # She just got something wrong and is being told so, and the
            # turn asks for nothing else. Answering from a bank rather than
            # from the model is the same call the greeting path makes, and
            # for a stronger reason: measured live, the model argued back.
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user is fed up, and asked for nothing else.",
                    speech_act="social",
                ),
                user_input=user_input,
                locked_response=self.social_lines.frustration(),
            )
        if (
            _HESITATION.fullmatch(user_input)
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
            # A filler asks nothing, so there is nothing to route. Sent to
            # the router it comes back as the previous question asked
            # again, because the previous subject is the only thing in the
            # prompt that a subjectless turn can attach to -- measured, on
            # "엄", which returned as a full request about Bill Gates.
            #
            # Said rather than swallowed: she is on a microphone, and a
            # turn that produces nothing at all is indistinguishable from
            # her having stopped working.
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="A filler with nothing outstanding.",
                    speech_act="social",
                ),
                user_input=user_input,
                locked_response=guard_lines.say(
                    "still_listening", self._turn_language,
                ),
            )
        if (
            _BARE_REFUSAL.fullmatch(user_input)
            and active_problem is not None
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
            # They said no to the thing she is working on. Nothing is
            # parked, so there is no consent to resolve and nothing for the
            # router to classify -- the only question is whether the
            # recommendation stays open, and it does not.
            #
            # Deterministic for the same reason the cancellation above is:
            # sent to the model, with the subject still in its history, a
            # refusal is answered with another recommendation about 4 times
            # in 8 (measured on tests/contamination_matrix.json,
            # refusal_is_not_a_request).
            self.recommendations.note_declined()
            self.task_sessions.clear_recommendation()
            self._grounded_context = {}
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="The user declined the open recommendation.",
                    speech_act="social",
                    is_follow_up=True,
                ),
                user_input=user_input,
                locked_response=self._generic_declined(),
            )
        if (
            _BARE_ACKNOWLEDGEMENT.fullmatch(user_input)
            and not any((
                pending_offer,
                pending_computer,
                pending_task,
                pending_strategy,
                pending_capability,
                pending_clarification,
            ))
            and not (active_problem is not None and active_problem.evidence)
            and not has_explicit_attachment
            and not continuing_agent_flow
        ):
            # Nothing is outstanding, so this authorises nothing and asks
            # nothing. Every branch that would give it meaning -- a pending
            # offer, a consent question, delivered results to react to -- is
            # checked above and returns before this point.
            #
            # An open problem with *no evidence yet* used to block this, so
            # "ok" two turns into a conversation about monitors paid a full
            # routing call. It cannot mean "yes, go ahead": every gate that
            # could have asked something is empty, and there are no results
            # to be reacting to either.
            timings["route"] = time.perf_counter() - route_started
            return TurnRouting(
                route=IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=user_input,
                    reason="A bare acknowledgement with nothing outstanding.",
                    speech_act="social",
                ),
                user_input=user_input,
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
            None if has_explicit_attachment or continuing_agent_flow or disputed_machine
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

        def tier0(transcript: str) -> IntentDecision | None:
            """The answer, when the sentence already contains it.

            A closed grammatical class with nothing outstanding leaves the
            router nothing to classify. This returns the decision the model
            would have produced, so everything after it -- recall, the
            interaction decision, capability selection -- runs exactly as
            it does on a routed turn. Returning a whole ``TurnRouting``
            instead would skip all of that, and measured, it did: the
            follow-up stopped reaching 4F.2's ``continue`` and answered
            from general knowledge instead of the session's own results.
            """
            if (
                has_explicit_attachment
                or continuing_agent_flow
                or any((
                    pending_offer, pending_computer, pending_task,
                    pending_strategy, pending_capability,
                    pending_clarification,
                ))
            ):
                return None
            # A request whose answer is computed -- the clock, arithmetic,
            # a unit conversion -- is decided by its own grammar, not by a
            # model's reading of how fresh it is (Phase 3B). In shadow mode
            # the claim is recorded beside the router's decision and changes
            # nothing.
            claimed = domain_resolver.claim(transcript)
            if claimed is not None:
                domains = domain_resolver.mode(self.config)
                turn_trace.note_context(
                    domain_claim={**claimed.as_dict(), "mode": domains},
                )
                if domains == "act":
                    self._turn_domain_claim = claimed
                    print(f"[Domains] {claimed.domain}: routed without the "
                          f"model. {claimed.reason}")
                    return claimed.decision()
                print(f"[Domains] shadow: {claimed.domain} would be routed "
                      "without the model; asking the router.")
            # "Open the second tab" counts against the browser's tabs, not
            # against her shortlist, and the page layers own that. The
            # counting vocabulary is identical for both, so the noun beside
            # it is the only thing that tells them apart.
            names_a_surface = re.search(
                r"\b(?:tab|tabs|page|window|windows|screen|browser|app|"
                r"apps|folder|file|files|result|results|link|links)\b",
                transcript, re.IGNORECASE,
            )
            if (
                active_problem is not None
                and _OPEN_REQUEST.search(transcript)
                and not names_a_surface
            ):
                # "Open the second one." The position counts against what
                # she actually listed, and what gets opened is that result's
                # own address -- never its label, because Phase 4E is a long
                # record of labels and identities coming apart.
                held = self.task_sessions.results()
                reference = references.resolve(transcript, held.names())
                if reference.resolved:
                    chosen = held.at(reference.index)
                    if chosen is not None and chosen.openable:
                        print(
                            f"[Reference] {reference.log_line()} -> "
                            f"[{chosen.id}] {chosen.url}"
                        )
                        return IntentDecision(
                            intent="computer_action",
                            confidence=1.0,
                            normalized_request=f"open {chosen.url}",
                            reason="A position in the results she listed.",
                            speech_act="action_request",
                            action_requested=True,
                            action_target=chosen.url,
                            computer_operation="open_url",
                            computer_url=chosen.url,
                            is_follow_up=True,
                        )
                    # Resolved to something with nowhere to go, or to a
                    # position that is not in hand. Neither is a guess to
                    # make: the router sees the turn as it is.
                    print(
                        "[Reference] a position was named but nothing there "
                        "can be opened."
                    )
                elif reference.reason:
                    print(f"[Reference] {reference.reason}.")
            if (
                active_problem is not None
                and _RESULT_FOLLOW_UP.fullmatch(transcript)
            ):
                print("[Router] Tier 0: a follow-up about what was found.")
                return IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=transcript,
                    reason="A follow-up about results this session found.",
                    is_follow_up=True,
                )
            return None

        def route_current(
            transcript: str,
        ) -> IntentDecision:
            settled = tier0(transcript)
            if settled is not None:
                return settled
            # A question about themselves that something they told her
            # answers is answered from that, not from a tool. Measured
            # after a restart: "When's my birthday?" went to a date lookup
            # and came back as today's date; "Do I have any food
            # allergies?" went to a web search, which could only say it
            # did not know. The router never saw what she knew.
            told = self._what_they_told_her()
            if told and memory_gate.asks_about_themselves(transcript) and (
                memory_gate.shares_a_topic(transcript, told)
            ):
                print("[Memory] A question about themselves that what they "
                      "told me answers; answering from memory.")
                return IntentDecision(
                    intent="conversation",
                    confidence=1.0,
                    normalized_request=transcript,
                    reason="A question about themselves, answered from what "
                           "they told her.",
                )
            return self._resolve_named_choice(self._escalate_disputed_claim(
                self.intent_router.route(
                    transcript,
                    recent_turns=list(self._router_history),
                    has_screen_selection=has_explicit_attachment,
                    project_tools_available=self.project_mcp is not None,
                    conversation_state=self._build_conversation_state(),
                    pending_action=self._pending_action,
                    computer_control_enabled=self.computer_control_mode.enabled,
                ),
                transcript,
            ), transcript)

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
            if disputed_machine:
                return IntentDecision(intent="conversation", confidence=1.0,
                                      normalized_request=transcript)
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

        active_problem = self.task_sessions.active_recommendation()
        if (
            pending_clarification is not None
            and pending_clarification.goal.kind == "recommendation"
            and not has_explicit_attachment
            and not continuing_agent_flow
            and not reads_as_new_request(user_input)
            and len(user_input.split()) <= 12
            and recommendation_state.answer_for_dimension(
                pending_clarification.slot, user_input,
                options=(
                    active_problem.type_options()
                    if active_problem is not None else ()
                ),
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
            and str(pending_clarification.question or "").strip()
            and str(pending_clarification.question).strip()
            in self._things_she_has_said()[0]
        ):
            # She asked this on the turn before and the reply did not answer
            # it. The branch below would say it again, word for word -- and
            # measured in three of three English dogfood runs, it did:
            #
            #     You: decent cheap headphone brand?   Elaina: Over-ear or in-ear?
            #     You: under 100                        Elaina: Over-ear or in-ear?
            #     You: you sure about that?             Elaina: Are you looking
            #                                           for over-ear or in-ear...
            #
            # "under 100" was an answer, just not to that question. Asked
            # once and not answered, the question is dropped and the turn is
            # read on its own: a budget folds into the open recommendation,
            # and the type was already asked, so "once each" keeps it from
            # coming back.
            print("[Clarification] asked last turn and not answered; "
                  "not asking it again.")
            self.clarification.clear()
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
                    intent=NEEDS_CLARIFICATION,
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
                intent=NEEDS_CLARIFICATION,
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
            # The local test is strict by construction -- it rejects approval
            # of the subject ("sounds good", "yeah they're expensive") and
            # only passes a bare affirmative or a direct instruction. When it
            # says yes, asking the model adds a round-trip and can disagree:
            # measured, "why not" came back "unclear" from the classifier and
            # the offer was silently dropped. It used to be consulted only for
            # an offer carrying a task_id, which is why every other offer paid
            # for a model call to be told what "yes" means.
            consent = (
                SemanticConsentDecision(
                    decision="accept",
                    confidence=1.0,
                    reason="Clear, unambiguous acceptance of the offer.",
                )
                if reads_as_clear_acceptance(user_input)
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
                # An offer whose goal is an address is a navigation, and
                # accepting it means going there -- not handing the planner
                # a sentence to work out. This is what makes "yeah" answer
                # "Zillow.com didn't open, try again?".
                retry_address = (
                    goal if browser_navigation.looks_like_an_address(goal)
                    else ""
                )
                if retry_address and not reuses_task:
                    operation = "open_url"
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
                    computer_url=retry_address,
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
                    # A parked offer normally carries the words the person
                    # actually heard. When it does not, this used to be one
                    # fixed question -- so an unclear reply got the same
                    # sentence back however many times it happened.
                    or self.action_status.select(StatusContext(
                        phase="offer", force=True,
                    ))
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
        elif (
            not locked_response
            and active_problem is not None
            # The same test the query builder uses to decide the open task
            # has the last word. ``lookup_requested`` is narrower than it
            # sounds -- it means the person asked to be shown options in
            # so many words -- and an ordinary "where can I buy X?" never
            # sets it, though it runs a search all the same.
            and (active_problem.constraints or active_problem.lookup_requested)
            and not has_explicit_attachment
            and not continuing_agent_flow
        ):
            corrected_place = recommendation_state.supplies_only_a_place(
                user_input,
            )
            if corrected_place and corrected_place.casefold() not in {
                value.casefold()
                for value in active_problem.values(recommendation_state.AREA)
            }:
                # Saying where, while a lookup is open, is that lookup
                # again somewhere else. It is the correction the system is
                # least able to notice on its own, because a place it was
                # never told is filled in silently from the user's market --
                # so a search that went out in the wrong place looks
                # exactly like one that went out in the right one.
                # Folded into the stored problem, not into a copy: the
                # resume path below returns the stored one untouched, so a
                # correction applied only here would be reported and then
                # thrown away -- which is the failure this repairs.
                revised = self.task_sessions.note_recommendation_turn(
                    user_input,
                    subject=active_problem.subject,
                    said_before=self._last_claim(),
                )
                query = revised.search_query(user_input)
                route = replace(
                    route,
                    intent="web_search",
                    computer_operation="",
                    normalized_request=query,
                    topic=revised.subject,
                    reason=(
                        "The user corrected where the open lookup should "
                        "be looking."
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
                print(f"  reason: the place was corrected to {corrected_place}")
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
            pointer=self._turn_points_at_the_last_action,
        )
        if focus.corrected_to and focus.subject:
            goal = replace(goal, subject=focus.subject)
        if not self._turn_points_at_the_last_action:
            # A turn that asks for something else replaces the machine
            # target. A turn that merely drifts does not -- not the first
            # time.
            #
            # Measured live, session 11: an address was open, the next turn
            # came back from the transcriber as "I met only one S", the
            # subject became "meeting", and the target was retired. The
            # correction that followed it -- correctly heard this time --
            # had nothing left to correct, and the person had to reopen the
            # address by hand before the same words worked.
            #
            # One reprieve, not a standing exemption: a second drifting
            # turn retires it, so an unrelated conversation cannot keep an
            # old address alive indefinitely.
            drifted = bool(
                previous_focus is not None
                and focus.subject != previous_focus.subject
            )
            asks_for_something_else = (
                names_its_own_errand(user_input)
                or reads_as_new_request(user_input)
            )
            if route.intent != "computer_action" and (
                asks_for_something_else
                or (drifted and self._machine_target_reprieved)
            ):
                self._retire_machine_target()
            elif route.intent != "computer_action" and drifted:
                self._machine_target_reprieved = True
                print(
                    "[Reference] keeping the last address available for one "
                    "more turn."
                )
            elif route.computer_operation == "open_url":
                # A new address starts a new spelling history, and a fresh
                # reprieve. Corrections above explicitly mark themselves as
                # pointing back.
                self._navigation_history = ()
                self._machine_target_reprieved = False
        has_context, recalled_evidence, recall_origin = self._recall_context(
            route, goal, locked_response=locked_response,
        )
        if has_context:
            print(f"[Recall] Answering from {recall_origin}; no search needed.")
        decision = interaction.decide(
            route,
            goal=goal,
            has_usable_context=has_context,
            problem=self.task_sessions.active_recommendation(),
            supersedes_pending=bool(getattr(self, "_supersedes", None)),
        )
        # Kept so the turn's conclusion can be read back -- by a test, and
        # by any later phase that needs what this turn decided rather than
        # deciding it again.
        self._last_interaction = decision
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
        if self._search_the_correction and capability.capability in {
            capability_selection.DIRECT_ANSWER,
            capability_selection.UI_CONTROL,
        }:
            # "CBT?" -- "yes, CPT": the question is asked again about the
            # right thing, and this time evidence is wanted, the same way
            # "show me some" is escalated above.
            decision, capability = self._reselect_for_options(
                route, goal, options=False,
            )
            print("[Near Miss] The corrected question is looked up, not "
                  "answered from memory.")
        if self.intent_router.print_confidence_log:
            print(focus.log_block())
            print(goal.log_block())
            print(decision.log_block())
            print(capability.log_block())
            if problem is not None:
                print(problem.log_block())

        timings["route"] = time.perf_counter() - route_started
        route = replace(route, command_fused=command_was_fused(user_input, route))
        search_may_run = capability_selection.WEB_SEARCH in (
            capability.capability, *capability.fallbacks,
        )
        # A corrected question is searched as corrected. Measured: after
        # "아니 텍사스에 있는 파리 말하는 거야" the first search was that
        # sentence, word for word.
        said_for_search = (
            user_input if getattr(self, "_corrected_from", "")
            else raw_transcript or user_input
        )
        query = (
            self._resolved_search_query(route, goal, said=said_for_search)
            if search_may_run else route.search_query
        )
        resolved = ResolvedTurn(
            raw_transcript=raw_transcript, normalized_transcript=route.normalized_request,
            intent=route.intent, subject=problem.subject if problem else goal.subject,
            machine_target=route.computer_url or route.action_target,
            search_query=query, task=problem, confidence=route.confidence,
            correction_target=(self._last_computer_goal if self._turn_points_at_the_last_action else ""),
            provenance="active_task" if resumed_problem_id else "current_turn",
        )
        return TurnRouting(
            resolved=resolved,
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
        spoken_language="",
        spoken_confidence=0.0,
        heard_unclearly=False,
        spoken_word_average=0.0,
    ):
        """Take one turn, and keep a record of what happened in it.

        The record (core/turn_trace.py) is opened here and closed in a
        ``finally``, so a turn that returns early, is interrupted, or
        raises still leaves one behind. The turn itself is ``_take_turn``,
        unchanged.
        """
        trace = turn_trace.begin(
            str(user_input or ""),
            spoken_language=spoken_language,
            spoken_confidence=spoken_confidence,
            heard_unclearly=heard_unclearly,
            spoken_word_average=spoken_word_average,
            screen_region=bool(screen_region),
            screen_snapshot=screen_snapshot is not None,
        )
        # What this turn has in hand starts empty every turn. Evidence from
        # an earlier turn gets in only when this turn chooses to reuse it.
        self._turn_evidence = turn_evidence.EvidenceLedger()
        try:
            return self._take_turn(
                user_input,
                screen_region=screen_region,
                screen_snapshot=screen_snapshot,
                spoken_language=spoken_language,
                spoken_confidence=spoken_confidence,
                heard_unclearly=heard_unclearly,
                spoken_word_average=spoken_word_average,
            )
        except BaseException as error:
            if trace is not None:
                trace.record_outcome("raised", f"{type(error).__name__}: {error}")
            raise
        finally:
            if trace is not None:
                turn_trace.finish(trace)

    def _take_turn(
        self,
        user_input,
        screen_region=None,
        screen_snapshot=None,
        spoken_language="",
        spoken_confidence=0.0,
        heard_unclearly=False,
        spoken_word_average=0.0,
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

        # First, before anything reads response_language: the router's own
        # prompt, the personality, the style contract and the status banks
        # all ask which language this is, and they must all get one answer.
        self._decide_turn_language(
            user_input,
            spoken_language=spoken_language,
            spoken_confidence=spoken_confidence,
        )
        # Set by the voice loop when the transcriber itself could barely
        # decode the clip; read once, at the top of routing.
        self._heard_unclearly = bool(heard_unclearly)
        # How sure the transcriber was of its words, on average; 0.0 for a
        # typed turn, which is never read for sense.
        self._spoken_word_average = float(spoken_word_average or 0.0)

        self.events.emit(
            "user_message",
            text=user_input,
        )
        # Cooldowns are counted in turns, not seconds: a conversation that
        # pauses for lunch should not become a licence to start offering
        # again.
        self.recommendations.begin_turn()
        # A parked offer deliberately outlives the turn -- the user's "yeah"
        # arrives on the next one. Only the action state is cleared.
        self.action_ledger.begin_turn()
        self._invitation_stands = False
        # Replaced by the routing phase; reset here so a turn that returns
        # early cannot leave the last turn's reading behind it.
        self._supersedes = supersession.Supersession()
        # What the conversation was about *before* this turn touched it.
        #
        # Read here and nowhere else, because by the time the answer is
        # assembled it is already gone: `note_recommendation_turn` folds the
        # current turn into the open problem during routing, so a subject
        # comparison made later reads "mathematics" against "mathematics"
        # and can never disagree. Measured exactly that way -- the
        # inheritance rule was correct and structurally unable to fire.
        self._subject_before_turn = self._subject_now()

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
            resolved=routing.resolved,
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
                    "opening tag. The proposal does not edit anything; the app asks "
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
                        # The model reads this as tool output and will
                        # happily repeat it, so the exception goes to the
                        # log and the model is told what happened instead
                        # of being handed a class name to read out.
                        detail = capability_contract.describe(error)
                        print(f"[Project Tool] {name} failed -- {detail}")
                        result = (
                            f"Tool error: {name} did not complete. "
                            "Say so plainly; do not guess what it would "
                            "have returned."
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
                        # The model reads this as tool output and will
                        # happily repeat it, so the exception goes to the
                        # log and the model is told what happened instead
                        # of being handed a class name to read out.
                        detail = capability_contract.describe(error)
                        print(f"[Project Tool] {name} failed -- {detail}")
                        result = (
                            f"Tool error: {name} did not complete. "
                            "Say so plainly; do not guess what it would "
                            "have returned."
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
                "\n\nA file-change proposal is now visible on screen. "
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
            # The detail goes to the interface event, which is where a
            # developer looks. It used to go into the sentence as well --
            # this block instructed the model, in as many words, to read a
            # Python exception class out loud.
            detail = capability_contract.describe(error)
            self.events.emit(
                "git_action_error",
                status="error",
                message=detail,
            )
            print(f"[Git] proposal failed -- {detail}")
            return (
                "The Git proposal failed. Respond with one factual sentence "
                "only: \"I couldn't prepare the Git proposal, so nothing has "
                "been staged or committed.\" "
                "Do not discuss the time, personality, memories, "
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
            "A Git proposal is visible on screen. No files have been staged, "
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
        # What the person told her last is still being written down on a
        # thread of its own (extraction and consolidation are model calls).
        # Waited for, bounded: a memory lost at shutdown is a fact she
        # "forgets" overnight.
        deadline = time.monotonic() + 45.0
        for thread in getattr(self, "_memory_stores", []):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("[Memory] Shutdown did not wait for every memory to be saved.")
                break
            if thread.is_alive():
                print("[Memory] Finishing what you just told me before closing...")
                thread.join(timeout=remaining)
        recorder = getattr(self, "activity_recorder", None)
        if recorder is not None:
            recorder.stop()
        if getattr(self, "activity_log", None) is not None:
            self.activity_log.close()
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
    
    def build_time_context(self, question: str = "", *, said: str = "",
                           language: str = "") -> str:
        """The clock, and -- when the question names somewhere else -- theirs.

        Measured live: "Tell me the time in Seattle right now" was answered
        with the time in Korea, and the correction was answered with an
        invented one. The only clock in the prompt was an unlabelled local
        time, so there was nothing to convert from and nothing to convert
        with. The conversion happens in code now; the model reads out a
        line it did not have to compute.

        Which clock counts as local is ``time.timezone`` in config.yaml,
        not unconditionally the machine's own. The section has offered an
        IANA name since it was written and nothing honoured it; the tests
        for the two clocks needed one, because run on a machine already in
        the zone being asked about they compared Seattle against Seattle.

        Phase 3B (docs/PHASE3_PLAN.md §1.3): every place named is read, from
        what the person ``said`` as well as the router's paraphrase, and two
        named places are compared with each other. The zone is named from
        the tz database, never from Windows' localised %Z, and set apart as
        something to mention only if asked. A Korean reply gets the time as
        it is said in Korean, 오전/오후 included, computed here.
        """
        now = world_clock.local_now(
            self.config.get(
                "time", "timezone", default="local", required=False,
            ),
        )
        language = str(language or getattr(self, "_turn_language", "") or "en")
        label = world_clock.zone_label(now)
        zone = f"{label} " if label else ""

        lines = [
            f"Today is {now.strftime('%A, %B %d, %Y')}.",
            f"The current local time is {now.strftime('%I:%M %p')}.",
            f"The local time zone is {zone}(UTC{now.strftime('%z')}). Mention "
            "the zone or the offset only if the question asks about them.",
            f"The current year is {now.year}.",
        ]
        if language.startswith("ko"):
            lines.append(
                "In Korean the local time is said: "
                f"{world_clock.spoken_time(now, 'ko')}."
            )

        places = world_clock.read_places(f"{said} {question}".strip())[:3]
        # The same moment the local line above states, so the gap between
        # the two clocks is measured against the clock that was printed.
        described = []
        for place in places:
            elsewhere = world_clock.describe(place, here=now)
            if not elsewhere:
                continue
            described.append(place)
            lines.append("")
            lines.append(elsewhere)
            if language.startswith("ko"):
                found = world_clock.clock_in(place)
                if found is not None:
                    lines.append(
                        f"In Korean, {place} is said: "
                        f"{world_clock.spoken_time(found[1], 'ko')}."
                    )
        if len(described) >= 2:
            lines.append(world_clock.compare(described[0], described[1]))
        if described:
            lines.append(
                "Those lines are already correct for the places the question "
                "names. State them as they stand -- do not convert them "
                "again, and do not substitute the local time above."
            )
        return "\n".join(lines)
