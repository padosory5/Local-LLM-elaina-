"""Run a whole turn the way the app runs it, without touching the machine.

Every live check written before this called a planner directly. That proved
the planners while the product stayed broken: three of four headline
requests never reached the code those checks exercised. This harness exists
so a test can say the only thing that actually matters -- "when I say this,
she does that" -- through `ChatEngine.chat()`, with the router, the guards,
the front door and the handlers all real.

Two things are replaced, and only two:

* **The model.** A scripted client answers the router, the planners and the
  final sentence, so a turn is deterministic. It never invents: an
  unexpected call is a loud failure rather than a plausible reply.
* **The machine.** Every surface that could move a mouse, launch an app or
  open a page records what it was asked to do and does nothing. Assertions
  are then about the instruction she issued, which is the behaviour under
  test.
"""

from __future__ import annotations

import copy
import json
import re
import tempfile
from types import SimpleNamespace
from pathlib import Path
from dataclasses import dataclass, field, replace

from brain.chat_engine import ChatEngine
from brain.deliberation.profile import UserProfile
from agents.coordinator import AgentCoordinator
from agents.task_manager import AgentTaskManager
from config.loader import Config
from tools.computer_control.computer_control import (
    ComputerActionResult,
    PreparedComputerAction,
)
from tools.computer_control.windows_ui_control import UIActionResult
from tools.computer_control.windows_ui_observer import (
    ControlInfo,
    WindowInfo,
    WindowObservation,
)


# ----------------------------------------------------------------- model


@dataclass
class ScriptedClient:
    """A model that answers from a script, and never improvises.

    Routing answers are keyed by the phrase being routed, so a test reads
    as "when the router is asked about this, it says that". Anything else
    gets the plain conversational reply, which is what most turns need.
    """

    routes: dict[str, dict] = field(default_factory=dict)
    reply: str = "Sure."
    calls: list[str] = field(default_factory=list)

    def chat(self, **kwargs):
        messages = kwargs.get("messages") or []
        # Only the newest user message decides which scripted answer applies.
        # Matching the whole prompt matched the *previous* turn's wording out
        # of the conversation history, and answered this turn with it.
        current = ""
        for message in reversed(messages):
            # Callers pass dicts; some pass bare strings.
            if isinstance(message, dict):
                if str(message.get("role", "")) != "user":
                    continue
                current = str(message.get("content", ""))
            else:
                current = str(message)
            break
        self.calls.append(current[:200])
        if kwargs.get("stream"):
            # The spoken reply is streamed and collected chunk by chunk.
            # Checked first: a routing answer is never streamed, and the
            # reply call quotes the same utterance a route is keyed on --
            # so matching routes first answered the reply with JSON.
            return iter([{"message": {"content": self.reply}}])
        for phrase, decision in self.routes.items():
            if phrase.casefold() in current.casefold() and "intent" in decision:
                return {"message": {"content": json.dumps(decision)}}
        return {"message": {"content": self.reply}}

    def generate(self, **kwargs):  # some callers use generate()
        return {"response": self.reply}


def _starts_playback(label: str) -> bool:
    text = str(label or "").casefold()
    return "재생하기" in text or "play" in text.split()


# ------------------------------------------------------------- the machine


class RecordingComputerControl:
    """Accepts every structured operation and performs none of them."""

    def __init__(self) -> None:
        self.enabled = True
        self.operations: list[tuple[str, str]] = []
        self.ui_observer = RecordingObserver()

    def prepare(self, request, **kwargs):
        operation = getattr(request, "operation", str(request))
        target = getattr(request, "target", "")
        # Preparing resolves a target and changes nothing. Recorded
        # separately so a confirmation question does not read as an action.
        self.operations.append((f"prepare:{operation}", str(target)))
        # The real payload type: guessing its fields one at a time is how a
        # harness ends up testing itself instead of the engine.
        url = str(getattr(request, "url", "") or "")
        if operation in {"open_url", "open_search"} and not url:
            url = str(target)
        prepared = PreparedComputerAction(
            operation=operation,
            target=str(target),
            display_name=str(target),
            url=url,
        )
        return ComputerActionResult(
            "prepared", str(target), str(target),
            f"Ready to {operation} {target}.", operation=operation,
            prepared=prepared,
        )

    def execute(self, prepared, **kwargs):
        operation = getattr(prepared, "operation", "")
        target = getattr(prepared, "display_name", "")
        url = str(getattr(prepared, "url", "") or "")
        self.operations.append((f"{operation}:executed", str(target)))
        if operation in {"open_url", "open_search"}:
            # The real status, which is the point: it says the navigation
            # was dispatched, not that the page arrived. A harness that
            # returned "done" here hid the whole distinction.
            return ComputerActionResult(
                "url_opened", str(target), str(target),
                f"Opened {url} in a new tab.", operation=operation, url=url,
            )
        return ComputerActionResult(
            "done", str(target), str(target), f"Did {operation}.",
            operation=operation,
        )

    def requires_extra_confirmation(self, operation, *args, **kwargs):
        # High-risk operations still pause for a separate yes.
        return operation in {
            "force_quit_app", "delete_file", "delete_folder",
        }

    def open_app(self, target):
        self.operations.append(("open_app", str(target)))
        return ComputerActionResult(
            "opened", str(target), str(target), f"Opened {target}.",
            operation="open_app",
        )

    def __getattr__(self, name):
        # Anything else the engine reaches for is a no-op that records.
        def recorded(*args, **kwargs):
            self.operations.append((name, str(args[0]) if args else ""))
            return ComputerActionResult(
                "done", "", "", f"{name} recorded.", operation=name,
            )
        return recorded


class RecordingObserver:
    """A desktop with one window in it, so lookups resolve deterministically."""

    available = True

    def __init__(self) -> None:
        self.window = WindowInfo("Spotify Premium", is_active=True, handle=1)
        # What was last typed into search, which is what the app would be
        # showing results for, and what it has to show.
        self.searched = ""
        self.catalogue = (("Bang Bang", "IVE"), ("After LIKE", "IVE"))

    def list_windows(self):
        return (self.window,)

    def get_active_window(self):
        return self.window

    def find_window(self, target):
        return self.window

    def describe_window(self, target):
        controls = []
        if self.window.title != "Spotify Premium":
            # Something is playing, so the app offers to stop it -- which is
            # what proves playback when the title alone cannot.
            controls.append(
                ControlInfo(
                    "Button", "일시 정지하기",
                    is_actionable=True, element_id="h-p0",
                )
            )
        controls += [
            ControlInfo(
                "Button", "검색하기", is_actionable=True, element_id="h-e0",
            ),
            ControlInfo(
                "Group", "좋아요 표시한 곡",
                is_actionable=True, element_id="h-e1",
            ),
            ControlInfo(
                "Button", "재생하기", is_actionable=True, element_id="h-e2",
            ),
        ]
        # A real app answers a search with the tracks that match it, each
        # beside its artist and its own play control -- and beside the
        # decoys that share its name. Without those a track request can
        # only ever hand back, which tests the fake rather than her.
        words = set(re.findall(r"[^\W_]+", self.searched.casefold()))
        for index, (title, artist) in enumerate(self.catalogue):
            if not set(re.findall(r"[^\W_]+", title.casefold())) <= words:
                continue
            controls.extend([
                ControlInfo(
                    "Hyperlink", f"{title} Radio", element_id=f"r{index}-e0",
                ),
                ControlInfo("Hyperlink", title, element_id=f"r{index}-e1"),
                ControlInfo("Hyperlink", artist, element_id=f"r{index}-e2"),
                ControlInfo(
                    "Button", f"{title} 재생하기",
                    is_actionable=True, element_id=f"r{index}-e3",
                ),
            ])
        return WindowObservation(
            "observed", title=self.window.title, controls=tuple(controls),
        )

    @staticmethod
    def _safe_text(window):
        return getattr(window, "title", "")


class RecordingUIControl:
    """Records what she asked the machine to do, and touches nothing."""

    available = True

    def __init__(self, observer=None) -> None:
        self.actions: list[tuple[str, str]] = []
        self.observer = observer

    def _record(self, kind: str, name: str, status: str = "clicked"):
        self.actions.append((kind, name))
        return UIActionResult(
            status, f"{kind} {name}.", window_title="Spotify Premium",
            control_name=name, verified=True,
        )

    def focus_window(self, target):
        return self._record("focus", str(target), status="focused")

    def click_control(self, target, control, *, confirmed=False, element_id=""):
        name = control or element_id
        # A real app responds to being told to play: its window takes the
        # name of what started. Without that the turn can only ever report
        # "I couldn't confirm anything is playing", which is true of a
        # machine that did nothing but says nothing about her behaviour.
        if self.observer is not None and _starts_playback(name):
            self.observer.window = replace(
                self.observer.window, title="IVE - ELEVEN",
            )
        return self._record("click", name)

    def double_click_control(self, target, control, *, confirmed=False, element_id=""):
        name = control or element_id
        if self.observer is not None:
            self.observer.window = replace(
                self.observer.window, title=f"IVE - {name}",
            )
        return self._record("double_click", name)

    def type_text(self, target, control, text, *, confirmed=False, element_id="", **kwargs):
        self.actions.append(("type", str(text)))
        if self.observer is not None:
            self.observer.searched = str(text)
        return UIActionResult(
            "typed", f"Typed into {control}.", control_name=control,
            verified=True,
        )

    def click_then_type(self, target, control, text, *, confirmed=False, element_id=""):
        self.actions.append(("type", str(text)))
        if self.observer is not None:
            self.observer.searched = str(text)
        return UIActionResult(
            "typed", f"Clicked {control} and typed.", control_name=control,
        )

    def press_key(self, target, *keys):
        return self._record("press", "+".join(keys), status="typed")

    def select_option(self, target, control, option, *, element_id="", **kwargs):
        return self._record("select", option, status="selected")

    def scroll_control(self, target, control, direction, *, element_id="", **kwargs):
        return self._record("scroll", direction, status="scrolled")


class RecordingBrowser:
    """A browser that reports nothing open, and opens nothing."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    def list_tabs(self):
        return ()

    def __getattr__(self, name):
        def recorded(*args, **kwargs):
            self.actions.append((name, str(args[:2])))
            return None
        return recorded


class RecordingBrowserObserver:
    """A browser that shows exactly what a test says it shows.

    The engine verifies a navigation by looking at the browser, so a test
    about navigation has to be able to say what the browser is showing.
    Empty by default, which is the honest "I could not check" case.
    """

    def __init__(self) -> None:
        self.tabs: tuple = ()
        self.page = None
        self.calls: list[str] = []

    def showing(self, url: str, title: str = "", text: str = "") -> None:
        """Put one page in the browser, as the active tab.

        ``text`` is the page's own words. A title that is only the address
        with nothing behind it is how a browser says it had nothing to
        show, so a test about arrival has to be able to say whether there
        is anything there.
        """
        self.tabs = (SimpleNamespace(
            index=0, title=title, url=url, text=text, is_active=True,
        ),)
        self.page = SimpleNamespace(
            status="observed", url=url, title=title,
            headings=(), text_excerpt=text, message="",
        )

    def unreadable(self) -> None:
        """A browser that cannot be inspected at all."""
        self.tabs = ()
        self.page = None

    def list_tabs(self):
        self.calls.append("list_tabs")
        return self.tabs

    def describe_page(self, tab_index=None, *, query: str = ""):
        self.calls.append("describe_page")
        return self.page

    def read_text(self, tab_index=None):
        self.calls.append("read_text")
        return self.page

    def prefer_page(self, url: str) -> None:
        self.calls.append("prefer_page")


class SilentCursor:
    """The physical driver, disconnected."""

    available = True

    def begin_run(self) -> None:
        pass

    def end_run(self, *, restore: bool = True) -> None:
        pass

    def user_took_over(self) -> bool:
        return False


@dataclass
class RecordingAudio:
    """Everything she said out loud, with nothing reaching the speakers.

    ``build_engine`` sets ``tts.enabled = False``, and nothing reads it:
    ``VoiceManager`` picks its provider from ``tts.provider`` alone. So the
    harness was building a real Piper engine and playing generated speech
    through the machine during the whole-turn suite -- the one surface the
    "tie her hands" block had missed. Recording what she would have said is
    also the only way to assert it.
    """

    spoken: list[str] = field(default_factory=list)

    def speak(self, text: str) -> None:
        line = str(text or "").strip()
        if line:
            self.spoken.append(line)

    def stop(self) -> None:
        pass

    def is_speaking(self) -> bool:
        return False

    def echo_reference_text(self) -> str:
        return self.spoken[-1] if self.spoken else ""


# ------------------------------------------------------------------ engine


def build_engine(routes: dict[str, dict] | None = None) -> ChatEngine:
    """A real ChatEngine whose model is scripted and whose hands are tied."""
    config = Config()
    config.data = copy.deepcopy(config.data)
    # Off: anything that starts a subprocess, grabs a device, or reaches the
    # network. What stays on is everything that decides behaviour.
    for section in (
        "memory", "vision", "visual_search", "project_access", "search",
    ):
        if section in config.data and isinstance(config.data[section], dict):
            config.data[section]["enabled"] = False
    if "tts" in config.data:
        config.data["tts"]["enabled"] = False
    if "stt" in config.data:
        config.data["stt"]["enabled"] = False

    engine = ChatEngine(config)

    client = ScriptedClient(routes=routes or {})
    engine.client = client
    for owner in (
        engine.intent_router,
        engine.desktop_action_planner,
        engine.browser_action_planner,
        getattr(engine, "task_planner", None),
        getattr(engine, "task_intent_gate", None),
        getattr(engine, "brief_responses", None),
        getattr(engine, "consent_classifier", None),
    ):
        if owner is not None and hasattr(owner, "client"):
            owner.client = client

    # Tie her hands: nothing below this line can touch the machine.
    engine.computer_control = RecordingComputerControl()
    engine.desktop_action_planner.computer_control = engine.computer_control
    engine.desktop_action_planner.observer = engine.computer_control.ui_observer
    engine.desktop_action_planner.control = RecordingUIControl(
        observer=engine.computer_control.ui_observer,
    )
    # Media skills wait for a real app to redraw or begin playback. The
    # recording surface updates synchronously, so sleeping here only makes
    # the whole-turn suite slow without testing any product behaviour.
    engine.desktop_action_planner._sleep = lambda _seconds: None
    engine.browser_control = RecordingBrowser()
    engine.browser_action_planner.control = engine.browser_control
    # The engine looks at the browser after opening a URL. Without this it
    # would look at the real one on the machine running the tests.
    engine.browser_observer = RecordingBrowserObserver()
    engine.browser_action_planner.observer = engine.browser_observer
    engine.cursor_driver = SilentCursor()
    # The speakers are part of the machine too.
    engine.audio = RecordingAudio()
    engine.project_mcp = None
    if getattr(engine, "input_watcher", None) is not None:
        try:
            engine.input_watcher.stop()
        except Exception:
            pass
    # Her real profile lives in runtime/data and reflects what this person
    # actually plays. A test must neither read it nor write to it.
    test_state = Path(tempfile.mkdtemp(prefix="elaina-turns-"))
    engine.user_profile = UserProfile(path=test_state / "profile.json")
    engine.desktop_action_planner.profile = engine.user_profile
    # Agent assignment normally appends to runtime/audit. Whole-turn tests
    # must be hermetic: running the deterministic suite may not depend on a
    # writable project checkout or add records to the user's real audit log.
    engine.agent_tasks = AgentTaskManager(
        audit_path=test_state / "agent_tasks.jsonl",
    )
    engine.agent_coordinator = AgentCoordinator(
        registry=engine.agent_registry,
        tasks=engine.agent_tasks,
    )
    engine.computer_control_mode.set_enabled(True)
    return engine


def machine_actions(engine: ChatEngine) -> list[tuple[str, str]]:
    """Everything this turn asked the machine to do."""
    return (
        list(engine.desktop_action_planner.control.actions)
        + list(engine.computer_control.operations)
        + list(engine.browser_control.actions)
    )


def reset(engine: ChatEngine) -> None:
    """Start the next case with nothing left over from the last one."""
    engine.desktop_action_planner.control.actions.clear()
    engine.computer_control.operations.clear()
    engine.browser_control.actions.clear()
    # Every gate that can hold an answer between turns. A case is about one
    # utterance; leaving an offer pending would test the previous one.
    observer = engine.computer_control.ui_observer
    observer.window = replace(observer.window, title="Spotify Premium")
    observer.searched = ""
    # What she played in an earlier case is real state she would rightly
    # act on; inside this suite it would silently answer the next case.
    session = getattr(engine, "_session_actions", None)
    if session is not None and hasattr(session, "clear"):
        session.clear()
    profile = getattr(engine, "user_profile", None)
    if profile is not None:
        profile._entries.clear()
    # She only offers a choice of research source once per sitting. That is
    # correct in a conversation and wrong in a suite: one engine serves every
    # case in a module, so the second case to reach a discovery offer would
    # silently get no question and fail on the previous case's rate limit.
    discovery = getattr(engine, "task_discovery_policy", None)
    if discovery is not None and hasattr(discovery, "forget_offers"):
        discovery.forget_offers()
    for gate in (
        engine.clarification,
        engine.capability_offer,
        engine.computer_consent,
        getattr(engine, "task_consent", None),
        getattr(engine, "task_strategy_consent", None),
        getattr(engine, "agent_consent", None),
    ):
        if gate is not None and hasattr(gate, "clear"):
            gate.clear()
