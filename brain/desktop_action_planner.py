"""Scoped, outcome-checked planning for native Windows UI actions.

The model chooses semantic UI operations; local code owns the execution
boundary. Every window/control is resolved against the live UI Automation
tree, deictic requests stay on the foreground surface captured when speech
began, repeated states receive one bounded recovery, and a final success is
accepted only after the last mutating step has observable evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any

from tools.computer_control.computer_control import ComputerControl
from tools.computer_control.windows_ui_control import UIActionResult, WindowsUIControl
from tools.computer_control.windows_ui_observer import WindowInfo, WindowsUIObserver


@dataclass(frozen=True)
class DesktopSurfaceContext:
    """The foreground surface frozen at utterance time."""

    window_title: str = ""
    app_name: str = ""
    is_active: bool = False
    browser_page_cue: bool = False
    page_title: str = ""
    page_url: str = ""
    lock_to_surface: bool = False
    handle: int | None = None
    process_id: int | None = None
    class_name: str = ""
    surface_identity: str = ""

    @classmethod
    def from_window_info(
        cls,
        window: WindowInfo | None,
        *,
        browser_page_cue: bool = False,
        page_title: str = "",
        page_url: str = "",
        lock_to_surface: bool = False,
    ) -> "DesktopSurfaceContext":
        if window is None:
            return cls(
                browser_page_cue=browser_page_cue,
                page_title=page_title,
                page_url=page_url,
                lock_to_surface=lock_to_surface,
            )
        return cls(
            window_title=window.title,
            app_name=window.app_name,
            is_active=window.is_active,
            browser_page_cue=browser_page_cue,
            page_title=page_title or window.title,
            page_url=page_url,
            lock_to_surface=lock_to_surface,
            handle=window.handle,
            process_id=window.process_id,
            class_name=window.class_name,
            surface_identity=window.identity,
        )

    @classmethod
    def from_public_snapshot(
        cls,
        snapshot: dict[str, object] | None,
        *,
        lock_to_surface: bool = False,
    ) -> "DesktopSurfaceContext":
        snapshot = snapshot or {}
        title = str(snapshot.get("title", "") or "").strip()
        kind = str(snapshot.get("kind", "") or "").strip().casefold()
        return cls(
            window_title=title,
            app_name=str(snapshot.get("application", "") or "").strip(),
            is_active=bool(title),
            browser_page_cue=kind == "browser",
            page_title=title if kind == "browser" else "",
            lock_to_surface=lock_to_surface,
            handle=_optional_int(snapshot.get("handle")),
            process_id=_optional_int(snapshot.get("process_id")),
            class_name=str(snapshot.get("application", "") or "").strip(),
            surface_identity=str(snapshot.get("identity", "") or "").strip(),
        )

    @property
    def available(self) -> bool:
        return bool(self.window_title)

    def as_window_info(self) -> WindowInfo | None:
        if not self.window_title:
            return None
        return WindowInfo(
            title=self.window_title,
            app_name=self.app_name,
            is_active=self.is_active,
            handle=self.handle,
            process_id=self.process_id,
            class_name=self.class_name,
        )

    def prompt_text(self) -> str:
        if not self.available:
            return "No foreground surface was captured."
        kind = "browser page" if self.browser_page_cue else "native window"
        lock = "locked" if self.lock_to_surface else "starting context only"
        details = [
            f"kind={kind}",
            f"scope={lock}",
            f"title={self.window_title!r}",
        ]
        if self.app_name:
            details.append(f"application/class={self.app_name!r}")
        if self.page_url:
            details.append(f"url={self.page_url!r}")
        return ", ".join(details)

    def to_public_snapshot(self) -> dict[str, object]:
        if not self.available:
            return {}
        return {
            "title": self.window_title,
            "application": self.app_name or self.class_name,
            "kind": "browser" if self.browser_page_cue else "native",
            "identity": self.surface_identity,
            "handle": self.handle,
            "process_id": self.process_id,
        }


@dataclass(frozen=True)
class PendingConfirmation:
    window_title: str
    control_name: str
    window_snapshot: WindowInfo | None = None


@dataclass(frozen=True)
class ActionPlanResult:
    status: str  # "done", "needs_confirmation", "failed"
    summary: str = ""
    pending: PendingConfirmation | None = None
    steps_taken: tuple[str, ...] = ()
    surface_context: DesktopSurfaceContext = field(
        default_factory=DesktopSurfaceContext
    )
    model_rounds: int = 0
    action_steps: int = 0
    recovery_used: bool = False
    failure_code: str = ""


@dataclass(frozen=True)
class _ToolExecution:
    tool_name: str
    status: str
    message: str
    is_action: bool = False
    verified: bool | None = None
    evidence: str = ""
    pending: PendingConfirmation | None = None
    window_snapshot: WindowInfo | None = None
    fingerprint: str = ""
    # The real resolved control name (UIActionResult.control_name), distinct
    # from whatever the model passed as arguments["control"] -- populated
    # even for an element_id-only call, which carries no name at all. Used
    # by _action_completion_terms so goal-completion matching still works
    # when the model addressed a control by id rather than by name.
    resolved_control_name: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {
            "observed",
            "windows_listed",
            "opened",
            "focused",
            "clicked",
            "typed",
            "selected",
            "scrolled",
        }

    def tool_message(self) -> str:
        payload = {
            "tool": self.tool_name,
            "status": self.status,
            "message": self.message,
            "verified": self.verified,
            "evidence": self.evidence,
        }
        return json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class _CompletedAction:
    family: str
    terms: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _GoalCompletionContract:
    """Small semantic contract between a user goal and tool capabilities.

    The model still decides the concrete UI path. This contract only prevents
    a verified preparatory step (for example, typing a song into Search) from
    being mistaken for completion of an operation such as playback.
    """

    operation: str = "generic"
    completing_families: frozenset[str] = frozenset()
    subject_terms: frozenset[str] = frozenset()
    direct_control_terms: frozenset[str] = frozenset()
    # Compound requests such as "search X and open that song to play" give
    # both a search query and a generic playback control.  Matching only one
    # word of X could select the wrong artist/title, so their typed or clicked
    # result must retain the full extracted subject before a generic Play
    # control can complete the request.
    subject_requires_full_match: bool = False

    def is_satisfied_by(
        self,
        completed_actions: list[_CompletedAction],
    ) -> bool:
        candidates = [
            action
            for action in completed_actions
            if action.family in self.completing_families
        ]
        if not candidates:
            return False
        if not self.subject_terms:
            return True
        if any(
            self.subject_terms <= action.terms
            if (
                self.operation in {"search", "text_input"}
                or self.subject_requires_full_match
            )
            else bool(action.terms & self.subject_terms)
            for action in candidates
        ):
            return True

        # A generic Play/Pause/Next control can complete a media action when
        # the concrete item was first grounded by a matching search/selection.
        # It cannot make an unrelated click such as Home count as playback.
        if self.operation == "activation" and any(
            action.terms & self.direct_control_terms
            for action in candidates
        ):
            return any(
                action.family in {"text_input", "selection"}
                and (
                    self.subject_terms <= action.terms
                    if self.subject_requires_full_match
                    else bool(action.terms & self.subject_terms)
                )
                for action in completed_actions
            ) or self.subject_terms <= _GENERIC_MEDIA_SUBJECT_TERMS
        return False

    def reminder(self) -> str:
        if self.operation == "activation":
            return (
                "The goal requires activating the requested item; opening, "
                "focusing, or typing a search is only preparation."
            )
        if self.operation == "search":
            return "The goal requires entering the requested search query."
        if self.operation == "text_input":
            return "The goal requires entering the requested text."
        if self.operation == "selection":
            return "The goal requires selecting the requested option."
        if self.operation == "click":
            return "The goal requires activating the requested control."
        if self.operation == "scroll":
            return "The goal requires scrolling the requested surface."
        if self.operation == "focus":
            return "The goal requires bringing the requested window forward."
        if self.operation == "launch":
            return "The goal requires opening or focusing the requested app."
        return "The requested operation is not complete yet."


_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": "List currently open windows when the target window is unknown.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_window",
            "description": (
                "Read the real visible, enabled controls in one window. Call "
                "this before acting; never invent a control name. Each line "
                "ends with a short id such as [id=a1b2c3d4-e7]; pass that id "
                "as element_id on your next action instead of retyping the "
                "control's name."
            ),
            "parameters": {
                "type": "object",
                "properties": {"window": {"type": "string"}},
                "required": ["window"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": (
                "Open the application explicitly named by the goal when its "
                "window is not open. Never use this for a button that happens "
                "to share an application's name."
            ),
            "parameters": {
                "type": "object",
                "properties": {"app": {"type": "string"}},
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Bring one verified window to the foreground.",
            "parameters": {
                "type": "object",
                "properties": {"window": {"type": "string"}},
                "required": ["window"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_control",
            "description": (
                "Invoke one real visible control. Committing controls pause "
                "for separate confirmation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string"},
                    "element_id": {
                        "type": "string",
                        "description": (
                            "Preferred: the exact id from the most recent "
                            "describe_window of this window, copied verbatim."
                        ),
                    },
                    "control": {
                        "type": "string",
                        "description": (
                            "Fallback only, when no id is shown: the "
                            "control's exact accessible name, copied "
                            "verbatim from describe_window."
                        ),
                    },
                },
                "required": ["window"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": (
                "Type into a verified text field. Credential fields are "
                "refused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string"},
                    "element_id": {
                        "type": "string",
                        "description": (
                            "Preferred: the exact id from the most recent "
                            "describe_window of this window, copied verbatim."
                        ),
                    },
                    "control": {
                        "type": "string",
                        "description": (
                            "Fallback only, when no id is shown: the "
                            "field's exact accessible name, copied verbatim "
                            "from describe_window."
                        ),
                    },
                    "text": {"type": "string"},
                },
                "required": ["window", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_then_type",
            "description": (
                "For a field with no separately named text control -- only "
                "a button that reveals it (a search icon, for example): "
                "click that button, then type into whatever gains keyboard "
                "focus as a result. Use this instead of type_text only "
                "when describe_window shows no matching Edit/ComboBox for "
                "the field itself. Cannot verify the keystrokes landed in "
                "the right place, so use type_text whenever a real named "
                "text field exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string"},
                    "element_id": {
                        "type": "string",
                        "description": (
                            "Preferred: the exact id of the revealing "
                            "button from the most recent describe_window, "
                            "copied verbatim."
                        ),
                    },
                    "control": {
                        "type": "string",
                        "description": (
                            "Fallback only, when no id is shown: the "
                            "revealing button's exact accessible name, "
                            "copied verbatim from describe_window."
                        ),
                    },
                    "text": {"type": "string"},
                },
                "required": ["window", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_option",
            "description": "Select one option in a verified list or combo box.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string"},
                    "element_id": {
                        "type": "string",
                        "description": (
                            "Preferred: the exact id from the most recent "
                            "describe_window of this window, copied verbatim."
                        ),
                    },
                    "control": {
                        "type": "string",
                        "description": (
                            "Fallback only, when no id is shown: the "
                            "control's exact accessible name, copied "
                            "verbatim from describe_window."
                        ),
                    },
                    "option": {"type": "string"},
                },
                "required": ["window", "option"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_control",
            "description": "Scroll a verified container.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string"},
                    "element_id": {
                        "type": "string",
                        "description": (
                            "Preferred: the exact id from the most recent "
                            "describe_window of this window, copied verbatim."
                        ),
                    },
                    "control": {
                        "type": "string",
                        "description": (
                            "Fallback only, when no id is shown: the "
                            "container's exact accessible name, copied "
                            "verbatim from describe_window."
                        ),
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                    },
                },
                "required": ["window", "direction"],
            },
        },
    },
]


_BASE_SYSTEM_PROMPT = (
    "Carry out the desktop goal with the provided tools. Use one tool at a "
    "time and wait for its structured result. Observe a window before acting "
    "inside it and copy exact accessible control names from that observation. "
    "Never guess a control name or treat a generic role such as Button as its "
    "name. Each control line ends with a short id such as [id=a1b2c3d4-e7]; "
    "always prefer passing that exact id as element_id on your next action "
    "instead of retyping the control's name -- copy it exactly, character "
    "for character. Only use control (the exact accessible name, copied "
    "exactly from the same observation) when no id is shown. Never invent "
    "either one: an id or name from an older observation, a different "
    "window, or one you made up will simply fail -- observe again to get a "
    "current one. If a named application is not open, open it, then use the exact "
    "window returned by the tool. Do not reduce a goal such as playing a song "
    "to merely opening or searching the application. Some apps (Spotify, "
    "Battle.net, Discord, and similar) never expose their real search or "
    "text field as a named Edit/ComboBox at all -- only a button that "
    "reveals it. When describe_window shows a button like that (a search "
    "icon, for example) but no matching text field, use click_then_type on "
    "that button instead of type_text; this is the correct tool for that "
    "situation, not a workaround. For a compound media request such as "
    "searching a title and artist then playing that result, enter the full "
    "title-and-artist query, re-observe the results, and activate a matching "
    "observed result; do not stop after searching. If a tool reports confirmation_required, "
    "refused, ambiguous, verification_failed, or a scope violation, stop; "
    "never work around it. When the complete goal is verified, answer with "
    "one short outcome sentence under 15 words. Do not describe a next step "
    "as completed. Do not offer further help."
)

_DEICTIC_SURFACE_PATTERN = re.compile(
    r"(?:\b(?:this|that|current|active)\s+"
    r"(?:page|window|screen|app|application)\b|"
    r"\b(?:on|in)\s+(?:(?:this|that|the\s+current)\s+"
    r"(?:page|window|screen|app|application)|it)\b|"
    r"\b(?:right\s+)?here\b)",
    flags=re.IGNORECASE,
)
_STILL_WORKING_PATTERN = re.compile(
    r"\b(?:let'?s|we'?ll|i'?ll|now|next)\b.{0,15}\b"
    r"(?:click|type|focus|select|scroll|try|check|look)\b",
    flags=re.IGNORECASE,
)
_FAILURE_SUMMARY_PATTERN = re.compile(
    r"\b(?:can(?:not|'t)|could(?:\s+not|n't)|did(?:\s+not|n't)|failed|"
    r"unable|not\s+found|not\s+complete)\b",
    flags=re.IGNORECASE,
)

_OBSERVATION_TOOLS = frozenset({"list_windows", "describe_window"})
_ACTION_TOOLS = frozenset({
    "open_app", "focus_window", "click_control", "type_text",
    "click_then_type", "select_option", "scroll_control",
})
_ACTION_FAMILY_BY_TOOL = {
    "open_app": "launch",
    "focus_window": "focus",
    "click_control": "activation",
    "type_text": "text_input",
    "click_then_type": "text_input",
    "select_option": "selection",
    "scroll_control": "scroll",
}

# These are operation words, not full utterance triggers. The compact mapping
# defines which tool capability must occur before a model-authored success can
# be accepted. Higher-priority operations intentionally supersede their usual
# preparatory steps: "search and play" must reach activation, while "open and
# search" must reach text input.
_GOAL_OPERATION_BY_WORD = {
    "play": "activation",
    "pause": "activation",
    "resume": "activation",
    "skip": "activation",
    "click": "click",
    "press": "click",
    "tap": "click",
    "select": "selection",
    "choose": "selection",
    "pick": "selection",
    "search": "search",
    "find": "search",
    "lookup": "search",
    "type": "text_input",
    "write": "text_input",
    "enter": "text_input",
    "input": "text_input",
    "scroll": "scroll",
    "focus": "focus",
    "switch": "focus",
    "bring": "focus",
    "open": "launch",
    "launch": "launch",
}
_ACTIVATION_PHRASE_PATTERN = re.compile(
    r"\b(?:put\s+on|listen\s+to)\b",
    flags=re.IGNORECASE,
)
_COMPOUND_SEARCH_TO_MEDIA_PATTERN = re.compile(
    r"\b(?:search(?:\s+for)?|find|look\s+up|lookup)\s+"
    r"(?P<subject>.+?)(?:\s*,?\s*(?:and|then)\s+)"
    r"(?:open|play|put\s+on|start)\s+"
    r"(?:(?:that|it|the)(?:\s+(?:song|track|music|result))?|"
    r"(?:song|track|music|result))\b",
    flags=re.IGNORECASE,
)
_GOAL_OPERATION_PRIORITY = (
    "activation", "click", "selection", "search", "text_input",
    "scroll", "focus", "launch",
)
_COMPLETING_FAMILIES_BY_OPERATION = {
    # Selecting an item is usually preparation; playback/pause/skip needs an
    # actual invocation so a highlighted song cannot be narrated as playing.
    "activation": frozenset({"activation"}),
    "click": frozenset({"activation"}),
    "selection": frozenset({"selection", "activation"}),
    "search": frozenset({"text_input"}),
    "text_input": frozenset({"text_input"}),
    "scroll": frozenset({"scroll"}),
    "focus": frozenset({"focus"}),
    "launch": frozenset({"launch", "focus"}),
    "generic": frozenset({
        "activation", "text_input", "selection", "scroll",
    }),
}
_DIRECT_CONTROL_TERMS_BY_GOAL_WORD = {
    "play": frozenset({"play"}),
    "pause": frozenset({"pause"}),
    "resume": frozenset({"resume", "play"}),
    "skip": frozenset({"skip", "next"}),
}
_DIRECT_CONTROL_LABEL_TERMS = frozenset({
    term
    for values in _DIRECT_CONTROL_TERMS_BY_GOAL_WORD.values()
    for term in values
})
_GENERIC_MEDIA_SUBJECT_TERMS = frozenset({
    "audio", "it", "media", "music", "song", "track", "this",
})

# Grammar and capability words do not identify the requested control/value.
# The remaining terms form a compact local alignment check between the user's
# goal and the exact value passed to the UI tool.
_CONTRACT_STOP_TERMS = frozenset({
    "a", "an", "and", "app", "application", "at", "button", "by", "can",
    "control", "could", "current", "for", "forward", "from", "front", "here",
    "in", "inside", "into", "it", "me", "my", "now", "of", "on",
    "music", "original", "page", "please", "request", "result", "screen", "song", "spotify", "the", "this",
    "that", "to", "track", "up", "use", "user", "using", "window", "with",
    "would", "you",
}) | frozenset(_GOAL_OPERATION_BY_WORD)
_TERMINAL_FAILURES = frozenset({
    "ambiguous",
    "invalid",
    "refused",
    "surface_unavailable",
    "surface_violation",
    "unavailable",
    "unsafe_match",
    "verification_failed",
})

_MAX_MODEL_ROUNDS = 14
_MAX_ACTION_STEPS = 7
_MAX_OBSERVATIONS = 9
_MAX_NUDGES = 2
_WINDOW_APPEAR_ATTEMPTS = 6
_WINDOW_APPEAR_INTERVAL_SECONDS = 0.6


class DesktopActionPlanner:
    """Run one native UI request to a verified result or a safe stop."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        keep_alive: Any,
        observer: WindowsUIObserver | None = None,
        control: WindowsUIControl | None = None,
        computer_control: ComputerControl | None = None,
        response_language: str = "en",
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        self.observer = observer or WindowsUIObserver()
        self.control = control or WindowsUIControl(observer=self.observer)
        self.response_language = str(response_language or "en").strip().lower()
        if computer_control is not None:
            self.computer_control = computer_control
        else:
            from security.policy import PolicyEngine

            self.computer_control = ComputerControl(
                PolicyEngine(), ui_observer=self.observer,
            )

    def act(
        self,
        goal: str,
        *,
        surface_context: DesktopSurfaceContext | None = None,
    ) -> ActionPlanResult:
        goal = str(goal).strip()
        completion_contract = _completion_contract(goal)
        effective_surface = self._effective_surface(goal, surface_context)
        if effective_surface.lock_to_surface and not effective_surface.available:
            return ActionPlanResult(
                "failed",
                "I couldn't identify the page or window you meant.",
                surface_context=effective_surface,
                failure_code="surface_unavailable",
            )

        messages: list[Any] = [
            {
                "role": "system",
                "content": self._system_prompt(effective_surface),
            },
            {"role": "user", "content": goal},
        ]
        steps: list[str] = []
        # Each call remembers its action generation, scoped window-tree state,
        # and whether it already invoked successfully. Only a failed attempt
        # may be retried after the observed UI changes; repeating a successful
        # toggle could silently undo it.
        seen_calls: dict[str, tuple[int, str, bool]] = {}
        nudges_used = 0
        recovery_used = False
        action_steps = 0
        observation_steps = 0
        successful_actions = 0
        last_execution: _ToolExecution | None = None
        last_observation_fingerprint = ""
        last_window_observation_fingerprint = ""
        actions_at_last_observation = -1
        observation_before_last_action = ""
        last_action_verified: bool | None = None
        post_action_state_changed = False
        completed_actions: list[_CompletedAction] = []
        completion_nudge_used = False
        verification_nudge_used = False
        observations_after_last_action = 0
        completion_ready = False
        last_successful_action_message = ""

        def finish(
            status: str,
            summary: str,
            *,
            rounds: int,
            pending: PendingConfirmation | None = None,
            failure_code: str = "",
        ) -> ActionPlanResult:
            return ActionPlanResult(
                status,
                summary,
                pending=pending,
                steps_taken=tuple(steps),
                surface_context=effective_surface,
                model_rounds=rounds,
                action_steps=action_steps,
                recovery_used=recovery_used,
                failure_code=failure_code,
            )

        for round_index in range(1, _MAX_MODEL_ROUNDS + 1):
            # Once local evidence satisfies the goal contract, remove tools
            # from the final model call. This prevents a completed search or
            # toggle from accumulating extra clicks while still allowing one
            # short natural spoken outcome.
            message = self._ask(messages, allow_tools=not completion_ready)
            if message is None:
                if completion_ready:
                    return finish(
                        "done",
                        self._verified_summary(
                            last_successful_action_message
                        ),
                        rounds=round_index,
                    )
                return finish(
                    "failed",
                    "I couldn't reach the desktop planner.",
                    rounds=round_index,
                    failure_code="planner_unavailable",
                )

            tool_calls = list(self._value(message, "tool_calls", None) or ())
            if completion_ready:
                content = str(
                    self._value(message, "content", "") or ""
                ).strip()
                if (
                    not content
                    or tool_calls
                    or _FAILURE_SUMMARY_PATTERN.search(content)
                    or _STILL_WORKING_PATTERN.search(content)
                ):
                    content = self._verified_summary(
                        last_successful_action_message
                    )
                return finish("done", content, rounds=round_index)

            if tool_calls:
                messages.append(message)
                for call in tool_calls:
                    tool_name, arguments = self._call_parts(call)
                    signature = self._call_signature(tool_name, arguments)
                    previous_context = seen_calls.get(signature)
                    repeated_call = False
                    if previous_context is not None:
                        (
                            previous_generation,
                            previous_window_state,
                            previous_succeeded,
                        ) = (
                            previous_context
                        )
                        if tool_name in _OBSERVATION_TOOLS:
                            repeated_call = previous_generation == action_steps
                        elif tool_name in _ACTION_TOOLS:
                            repeated_call = not (
                                not previous_succeeded
                                and last_window_observation_fingerprint
                                and last_window_observation_fingerprint
                                != previous_window_state
                            )
                        else:
                            repeated_call = True
                    if repeated_call:
                        if not recovery_used:
                            recovery_used = True
                            messages.append({
                                "role": "tool",
                                "content": json.dumps({
                                    "tool": tool_name,
                                    "status": "stalled",
                                    "message": (
                                        "That exact step already ran and made "
                                        "no new progress. Re-observe once or "
                                        "choose a different verified route."
                                    ),
                                }),
                            })
                            messages.append({
                                "role": "user",
                                "content": (
                                    "Use the one recovery now. Do not repeat "
                                    "the same call or change to another surface."
                                ),
                            })
                            continue
                        return finish(
                            "failed",
                            self._stalled_message(effective_surface),
                            rounds=round_index,
                            failure_code="repeated_step",
                        )
                    call_window_state = last_window_observation_fingerprint

                    if tool_name in _ACTION_TOOLS:
                        if action_steps >= _MAX_ACTION_STEPS:
                            return finish(
                                "failed",
                                "I stopped before repeating more desktop actions.",
                                rounds=round_index,
                                failure_code="action_budget_exhausted",
                            )
                        action_steps += 1
                        observation_before_last_action = (
                            last_window_observation_fingerprint
                        )
                        post_action_state_changed = False
                        observations_after_last_action = 0
                    elif tool_name in _OBSERVATION_TOOLS:
                        if observation_steps >= _MAX_OBSERVATIONS:
                            return finish(
                                "failed",
                                "I couldn't get a clear view of the requested control.",
                                rounds=round_index,
                                failure_code="observation_budget_exhausted",
                            )
                        observation_steps += 1

                    execution = self._run_tool_call(
                        tool_name,
                        arguments,
                        surface=effective_surface,
                    )
                    last_execution = execution
                    steps.append(execution.message)
                    self._log_execution(
                        round_index, action_steps, execution,
                    )
                    messages.append({
                        "role": "tool",
                        "content": execution.tool_message(),
                    })
                    seen_calls[signature] = (
                        action_steps,
                        call_window_state,
                        execution.succeeded,
                    )

                    if execution.window_snapshot is not None:
                        same_surface = self._same_surface(
                            effective_surface, execution.window_snapshot,
                        )
                        browser_cue = (
                            effective_surface.browser_page_cue
                            if same_surface
                            else self._looks_like_browser_window(
                                execution.window_snapshot
                            )
                        )
                        new_surface = DesktopSurfaceContext.from_window_info(
                            execution.window_snapshot,
                            browser_page_cue=browser_cue,
                            page_title=(
                                effective_surface.page_title
                                if same_surface and browser_cue
                                else execution.window_snapshot.title
                                if browser_cue
                                else ""
                            ),
                            page_url=effective_surface.page_url,
                            lock_to_surface=(
                                effective_surface.lock_to_surface
                                or execution.tool_name == "open_app"
                                or execution.is_action
                            ),
                        )
                        effective_surface = new_surface

                    if execution.pending is not None:
                        return finish(
                            "needs_confirmation",
                            execution.message,
                            rounds=round_index,
                            pending=execution.pending,
                        )

                    if execution.is_action:
                        last_action_verified = execution.verified
                        if execution.succeeded:
                            successful_actions += 1
                            last_successful_action_message = execution.message
                            family = _ACTION_FAMILY_BY_TOOL.get(
                                execution.tool_name
                            )
                            if family and execution.verified is not False:
                                completed_actions.append(_CompletedAction(
                                    family=family,
                                    terms=_action_completion_terms(
                                        execution.tool_name, arguments,
                                        resolved_name=execution.resolved_control_name,
                                    ),
                                ))
                    elif execution.fingerprint:
                        is_window_observation = (
                            execution.tool_name == "describe_window"
                        )
                        if is_window_observation and successful_actions > 0:
                            observations_after_last_action += 1
                        unchanged = (
                            execution.fingerprint == last_observation_fingerprint
                            and actions_at_last_observation == action_steps
                        )
                        if (
                            is_window_observation
                            and observation_before_last_action
                            and action_steps > actions_at_last_observation
                            and execution.fingerprint
                            != observation_before_last_action
                        ):
                            post_action_state_changed = True
                        if unchanged:
                            if not recovery_used:
                                recovery_used = True
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        "The observable UI state did not change. "
                                        "Use one different recovery step within "
                                        "the same surface, then verify again."
                                    ),
                                })
                            else:
                                return finish(
                                    "failed",
                                    self._stalled_message(effective_surface),
                                    rounds=round_index,
                                    failure_code="unchanged_state",
                                )
                        last_observation_fingerprint = execution.fingerprint
                        if is_window_observation:
                            last_window_observation_fingerprint = (
                                execution.fingerprint
                            )
                        actions_at_last_observation = action_steps

                    if execution.status in _TERMINAL_FAILURES or (
                        execution.tool_name == "open_app"
                        and not execution.succeeded
                    ):
                        return finish(
                            "failed",
                            self._spoken_failure(
                                execution, effective_surface,
                            ),
                            rounds=round_index,
                            failure_code=execution.status,
                        )

                    completion_ready = (
                        completion_contract.is_satisfied_by(completed_actions)
                        and successful_actions > 0
                        and last_action_verified is not False
                        and (
                            last_action_verified is True
                            or post_action_state_changed
                        )
                    )
                    if completion_ready:
                        messages.append({
                            "role": "user",
                            "content": (
                                "The requested operation and target are now "
                                "verified. Do not perform another tool action. "
                                "Give one short outcome sentence only."
                            ),
                        })
                        break
                continue

            content = str(self._value(message, "content", "") or "").strip()
            if (
                successful_actions == 0
                or _STILL_WORKING_PATTERN.search(content)
            ):
                if nudges_used >= _MAX_NUDGES:
                    detail = (
                        self._spoken_failure(
                            last_execution, effective_surface,
                        )
                        if last_execution is not None
                        and not last_execution.succeeded
                        else "I couldn't work out a verified next step."
                    )
                    return finish(
                        "failed", detail,
                        rounds=round_index,
                        failure_code="planner_stalled",
                    )
                nudges_used += 1
                messages.append(message)
                messages.append({
                    "role": "user",
                    "content": (
                        "The goal is not verified yet. Call exactly one tool "
                        "for the next step instead of describing it."
                    ),
                })
                continue

            if not completion_contract.is_satisfied_by(
                completed_actions
            ):
                if completion_nudge_used:
                    return finish(
                        "failed",
                        "I stopped because the requested desktop operation wasn't completed.",
                        rounds=round_index,
                        failure_code="goal_operation_incomplete",
                    )
                completion_nudge_used = True
                messages.append(message)
                messages.append({
                    "role": "user",
                    "content": (
                        f"{completion_contract.reminder()} Call exactly one "
                        "verified tool for that operation before reporting "
                        "success. Stay on the same surface."
                    ),
                })
                continue

            if last_execution is not None and not last_execution.succeeded:
                return finish(
                    "failed",
                    self._spoken_failure(last_execution, effective_surface),
                    rounds=round_index,
                    failure_code=last_execution.status,
                )
            if last_action_verified is False:
                return finish(
                    "failed",
                    "The last desktop action did not reach the requested state.",
                    rounds=round_index,
                    failure_code="verification_failed",
                )
            if last_action_verified is None and not post_action_state_changed:
                if (
                    not verification_nudge_used
                    and observations_after_last_action == 0
                ):
                    verification_nudge_used = True
                    messages.append(message)
                    messages.append({
                        "role": "user",
                        "content": (
                            "The last action has no direct completion proof. "
                            "Re-observe the exact current window once, then "
                            "report success only if its state proves the goal."
                        ),
                    })
                    continue
                return finish(
                    "failed",
                    "I performed the action but couldn't verify the final result.",
                    rounds=round_index,
                    failure_code="unverified_outcome",
                )
            if not content:
                content = "The requested desktop action is complete."
            if _FAILURE_SUMMARY_PATTERN.search(content):
                return finish(
                    "failed",
                    content,
                    rounds=round_index,
                    failure_code="model_reported_failure",
                )
            return finish("done", content, rounds=round_index)

        return finish(
            "failed",
            "I couldn't verify the desktop result within the safe planning limit.",
            rounds=_MAX_MODEL_ROUNDS,
            failure_code="model_round_budget_exhausted",
        )

    def _effective_surface(
        self,
        goal: str,
        provided: DesktopSurfaceContext | None,
    ) -> DesktopSurfaceContext:
        deictic = bool(_DEICTIC_SURFACE_PATTERN.search(goal))
        if provided is not None:
            return replace(
                provided,
                lock_to_surface=provided.lock_to_surface or deictic,
            )
        get_active = getattr(self.observer, "get_active_window", None)
        active = get_active() if callable(get_active) else None
        return DesktopSurfaceContext.from_window_info(
            active,
            browser_page_cue=(
                deictic and "page" in goal.casefold()
            ),
            lock_to_surface=deictic,
        )

    def _system_prompt(self, surface: DesktopSurfaceContext) -> str:
        scope_rule = (
            "This task is locked to the captured surface. Every window "
            "observation and action will be forced onto it. Never call "
            "open_app and never substitute an operating-system app if a "
            "same-named page control is missing."
            if surface.lock_to_surface
            else (
                "The captured surface is context only. If the goal explicitly "
                "names another application, find or open that application and "
                "then remain on its verified window."
            )
        )
        language_rule = (
            f"Write the final spoken sentence in language code "
            f"{self.response_language!r}. Do not repeat native UI labels in a "
            "different language; describe their semantic role instead."
        )
        return (
            f"{_BASE_SYSTEM_PROMPT}\n\nCAPTURED SURFACE\n"
            f"{surface.prompt_text()}\n{scope_rule}\n{language_rule}"
        )

    @staticmethod
    def _same_surface(
        surface: DesktopSurfaceContext,
        window: WindowInfo,
    ) -> bool:
        if surface.handle is not None and window.handle is not None:
            return surface.handle == window.handle
        if surface.process_id is not None and window.process_id is not None:
            return (
                surface.process_id == window.process_id
                and surface.window_title == window.title
            )
        return bool(
            surface.window_title
            and surface.window_title.casefold() == window.title.casefold()
        )

    @staticmethod
    def _looks_like_browser_window(window: WindowInfo) -> bool:
        title = window.title.casefold()
        app = (window.app_name or window.class_name).casefold()
        return any(
            name in title
            for name in (
                "google chrome", "microsoft edge", "mozilla firefox",
                "brave browser", "opera", "vivaldi",
            )
        ) or app in {
            "chrome", "google chrome", "msedge", "microsoft edge",
            "firefox", "mozilla firefox", "brave", "brave browser",
            "opera", "vivaldi",
        }

    def _ask(
        self,
        messages: list[Any],
        *,
        allow_tools: bool = True,
    ) -> Any:
        try:
            request: dict[str, Any] = dict(
                model=self.model,
                messages=messages,
                stream=False,
                options={"temperature": 0, "num_predict": 320},
                keep_alive=self.keep_alive,
                think=False,
            )
            if allow_tools:
                request["tools"] = _TOOLS
            response = self.client.chat(**request)
        except Exception as error:
            print(
                "[Desktop Action Planner] Request failed: "
                f"{type(error).__name__}: {error}"
            )
            return None
        return self._value(response, "message", None)

    def _run_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        surface: DesktopSurfaceContext,
    ) -> _ToolExecution:
        if surface.lock_to_surface and name == "open_app":
            return _ToolExecution(
                name,
                "surface_violation",
                (
                    "That control isn't available on the current page or window."
                ),
                is_action=True,
                verified=False,
            )

        locked_window = (
            surface.as_window_info()
            if surface.lock_to_surface
            else None
        )
        try:
            if name == "list_windows":
                windows = self.observer.list_windows()
                if not windows:
                    return _ToolExecution(
                        name, "not_found", "No windows are currently open."
                    )
                message = "Open windows: " + "; ".join(w.title for w in windows)
                return _ToolExecution(
                    name,
                    "windows_listed",
                    message,
                    fingerprint=_fingerprint(message),
                )

            if name == "describe_window":
                target: str | WindowInfo = locked_window or str(
                    arguments.get("window", "")
                )
                observation = self.observer.describe_window(target)
                message = observation.as_tree_text()
                snapshot = self._snapshot_for_target(target)
                return _ToolExecution(
                    name,
                    observation.status,
                    message,
                    window_snapshot=snapshot,
                    fingerprint=(
                        _fingerprint(message)
                        if observation.status == "observed"
                        else ""
                    ),
                )

            if name == "open_app":
                app_name = str(arguments.get("app", "")).strip()
                existing = self.observer.find_window(app_name)
                if existing is not None:
                    snapshot = (
                        existing
                        if isinstance(existing, WindowInfo)
                        else self._window_info(existing)
                    )
                    focused = self.control.focus_window(snapshot)
                    if focused.succeeded and focused.verified is not False:
                        return _ToolExecution(
                            name,
                            "opened",
                            f"{snapshot.title!r} was already open and is focused.",
                            is_action=True,
                            verified=focused.verified,
                            evidence=focused.evidence,
                            window_snapshot=snapshot,
                        )
                result = self.computer_control.open_app(app_name)
                if not result.succeeded:
                    return _ToolExecution(
                        name, result.status, result.message,
                        is_action=True, verified=False,
                    )
                opened = self._wait_for_window(result.display_name or app_name)
                if opened is None:
                    return _ToolExecution(
                        name,
                        "verification_failed",
                        (
                            f"{result.display_name or app_name} started, but "
                            "its window never appeared."
                        ),
                        is_action=True,
                        verified=False,
                    )
                return _ToolExecution(
                    name,
                    "opened",
                    (
                        f"Opened {result.display_name or app_name}. Its verified "
                        f"window is {opened.title!r}."
                    ),
                    is_action=True,
                    verified=True,
                    evidence="A matching live top-level window appeared.",
                    window_snapshot=opened,
                )

            if name in {
                "focus_window", "click_control", "type_text",
                "click_then_type", "select_option", "scroll_control",
            }:
                target = locked_window or str(arguments.get("window", ""))
                snapshot = self._snapshot_for_target(target)
                result = self._run_control_action(name, target, arguments)
                pending = None
                if result.status == "confirmation_required":
                    pending = PendingConfirmation(
                        window_title=result.window_title,
                        control_name=result.control_name,
                        window_snapshot=snapshot,
                    )
                return _ToolExecution(
                    name,
                    result.status,
                    result.message,
                    is_action=True,
                    verified=result.verified,
                    evidence=result.evidence,
                    pending=pending,
                    window_snapshot=snapshot,
                    resolved_control_name=result.control_name,
                )
        except Exception as error:
            return _ToolExecution(
                name,
                "failed",
                f"That desktop step failed: {error}",
                is_action=name in _ACTION_TOOLS,
                verified=False,
            )

        return _ToolExecution(
            name,
            "invalid",
            f"The planner requested an unknown tool named {name!r}.",
            verified=False,
        )

    def _run_control_action(
        self,
        name: str,
        target: str | WindowInfo,
        arguments: dict[str, Any],
    ) -> UIActionResult:
        element_id = str(arguments.get("element_id", "") or "").strip()
        id_kwargs = {"element_id": element_id} if element_id else {}
        if name == "focus_window":
            return self.control.focus_window(target)
        if name == "click_control":
            return self.control.click_control(
                target, str(arguments.get("control", "")), **id_kwargs,
            )
        if name == "type_text":
            return self.control.type_text(
                target,
                str(arguments.get("control", "")),
                str(arguments.get("text", "")),
                **id_kwargs,
            )
        if name == "click_then_type":
            return self.control.click_then_type(
                target,
                str(arguments.get("control", "")),
                str(arguments.get("text", "")),
                **id_kwargs,
            )
        if name == "select_option":
            return self.control.select_option(
                target,
                str(arguments.get("control", "")),
                str(arguments.get("option", "")),
                **id_kwargs,
            )
        return self.control.scroll_control(
            target,
            str(arguments.get("control", "")),
            str(arguments.get("direction", "")),
            **id_kwargs,
        )

    def _snapshot_for_target(
        self,
        target: str | WindowInfo,
    ) -> WindowInfo | None:
        if isinstance(target, WindowInfo):
            return target
        live = self.observer.find_window(target)
        if live is None:
            return None
        if isinstance(live, WindowInfo):
            return live
        return self._window_info(live)

    def _wait_for_window(self, hint: str) -> WindowInfo | None:
        for _ in range(_WINDOW_APPEAR_ATTEMPTS):
            window = self.observer.find_window(hint)
            if window is not None:
                if isinstance(window, WindowInfo):
                    return window
                return self._window_info(window)
            time.sleep(_WINDOW_APPEAR_INTERVAL_SECONDS)
        return None

    def _window_info(self, window: Any) -> WindowInfo:
        title = self.observer._safe_text(window)
        safe_class = getattr(self.observer, "_safe_class_name", None)
        class_name = safe_class(window) if callable(safe_class) else ""
        safe_handle = getattr(self.observer, "_safe_handle", None)
        handle = safe_handle(window) if callable(safe_handle) else None
        safe_process_id = getattr(self.observer, "_safe_process_id", None)
        process_id = (
            safe_process_id(window) if callable(safe_process_id) else None
        )
        get_active = getattr(self.observer, "get_active_window", None)
        active = get_active() if callable(get_active) else None
        return WindowInfo(
            title=title,
            app_name=class_name,
            is_active=bool(active and active.title == title),
            handle=handle,
            process_id=process_id,
            class_name=class_name,
        )

    def resume_confirmed_click(
        self,
        *,
        window_title: str,
        control_name: str,
        window_snapshot: WindowInfo | None = None,
    ) -> ActionPlanResult:
        """Perform only the exact confirmed click on the frozen surface."""
        target: str | WindowInfo = window_snapshot or window_title
        result = self.control.click_control(
            target, control_name, confirmed=True,
        )
        surface = DesktopSurfaceContext.from_window_info(
            window_snapshot,
            lock_to_surface=True,
        ) if window_snapshot is not None else DesktopSurfaceContext(
            window_title=window_title,
            lock_to_surface=True,
        )
        # A confirmed click is intentionally a one-shot continuation. Without
        # a pre-click observation fingerprint, verified=None cannot prove a
        # state transition and must not be narrated as completed.
        succeeded = result.succeeded and result.verified is True
        failure_code = ""
        if not succeeded:
            failure_code = (
                "unverified_outcome"
                if result.succeeded and result.verified is None
                else result.status
            )
        return ActionPlanResult(
            "done" if succeeded else "failed",
            (
                "I clicked it, but couldn't verify the final result."
                if result.succeeded and result.verified is None
                else result.message
            ),
            surface_context=surface,
            model_rounds=0,
            action_steps=1,
            failure_code=failure_code,
        )

    @staticmethod
    def _call_parts(call: Any) -> tuple[str, dict[str, Any]]:
        function = DesktopActionPlanner._value(call, "function", {})
        name = str(DesktopActionPlanner._value(function, "name", "")).strip()
        arguments = DesktopActionPlanner._value(function, "arguments", {}) or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        return name, dict(arguments) if isinstance(arguments, dict) else {}

    @staticmethod
    def _call_signature(name: str, arguments: dict[str, Any]) -> str:
        safe_arguments = dict(arguments)
        if "text" in safe_arguments:
            safe_arguments["text"] = (
                "sha256:"
                + hashlib.sha256(
                    str(safe_arguments["text"]).encode("utf-8")
                ).hexdigest()[:12]
            )
        return json.dumps(
            [name, safe_arguments], sort_keys=True, ensure_ascii=False,
        )

    @staticmethod
    def _stalled_message(surface: DesktopSurfaceContext) -> str:
        if surface.browser_page_cue:
            return "I couldn't find a reliable path on the current browser page."
        if surface.window_title:
            return f"I couldn't make further progress in {surface.window_title}."
        return "I couldn't make further progress on that desktop task."

    @staticmethod
    def _verified_summary(action_message: str) -> str:
        message = str(action_message or "").strip()
        if message and len(message.split()) <= 15:
            return message
        return "The requested desktop action is complete."

    @staticmethod
    def _spoken_failure(
        execution: _ToolExecution,
        surface: DesktopSurfaceContext,
    ) -> str:
        if execution.status == "ambiguous":
            # open_app's ambiguity is about which installed application was
            # meant (e.g. a likely mishearing, "Did you mean Battle.net?"),
            # not a UI control -- that specific question is worth keeping
            # rather than flattening it to the generic control message.
            if (
                execution.tool_name == "open_app"
                and execution.message
                and len(execution.message.split()) <= 15
            ):
                return execution.message
            return "I found multiple matching controls, so I stopped."
        if execution.status in {"invalid", "unsafe_match"}:
            return "That control wasn't specific enough to use safely."
        if execution.status == "refused":
            return "I won't enter information into that protected field."
        if execution.status == "verification_failed":
            return "That action ran, but I couldn't verify its result."
        if execution.status in {"surface_unavailable", "surface_violation"}:
            return (
                "That control isn't available on the current page."
                if surface.browser_page_cue
                else "That control isn't available in the current window."
            )
        if execution.status == "unavailable":
            return "Desktop controls aren't available right now."
        if len(execution.message.split()) <= 15:
            return execution.message
        return "I couldn't complete that desktop step safely."

    @staticmethod
    def _log_execution(
        round_index: int,
        action_steps: int,
        execution: _ToolExecution,
    ) -> None:
        # Never log typed text or the full accessibility tree. The tool name,
        # status, and verification state are enough to diagnose stalls safely.
        print(
            "[Desktop Planner] "
            f"round={round_index} action_steps={action_steps} "
            f"tool={execution.tool_name} status={execution.status} "
            f"verified={execution.verified}"
        )

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)


def _fingerprint(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value)).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _completion_contract(goal: str) -> _GoalCompletionContract:
    """Infer the requested capability and bind it to the requested value."""
    # ChatEngine may append the original utterance on a second line. Infer the
    # strongest operation from both forms so an over-short router paraphrase
    # cannot erase playback, while preferring the matching normalized line for
    # its target wording when both retain the same operation.
    semantic_lines = []
    for raw_line in str(goal).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(
            r"^original\s+user\s+request\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip()
        if line:
            semantic_lines.append(line)
    if not semantic_lines:
        semantic_lines = [str(goal).strip()]
    semantic_text = " ".join(semantic_lines)
    words = set(re.findall(r"[a-z]+", semantic_text.casefold()))
    operations = {
        operation
        for word, operation in _GOAL_OPERATION_BY_WORD.items()
        if word in words
    }
    if _ACTIVATION_PHRASE_PATTERN.search(semantic_text):
        operations.add("activation")
    operation = next(
        (item for item in _GOAL_OPERATION_PRIORITY if item in operations),
        "generic",
    )
    primary_goal = next(
        (
            line for line in semantic_lines
            if _text_requests_operation(line, operation)
        ),
        semantic_lines[0],
    )
    compound_subject = (
        next(
            (
                match.group("subject").strip()
                for line in semantic_lines
                if (match := _COMPOUND_SEARCH_TO_MEDIA_PATTERN.search(line))
            ),
            "",
        )
        if operation == "activation"
        else ""
    )
    subject = compound_subject or _goal_subject(primary_goal, operation)
    subject_terms = _contract_terms(subject)
    if not subject_terms:
        subject_terms = _contract_terms(primary_goal)

    direct_control_terms: set[str] = set()
    for word in words:
        direct_control_terms.update(
            _DIRECT_CONTROL_TERMS_BY_GOAL_WORD.get(word, ())
        )
    if _ACTIVATION_PHRASE_PATTERN.search(semantic_text):
        direct_control_terms.add("play")
    return _GoalCompletionContract(
        operation=operation,
        completing_families=_COMPLETING_FAMILIES_BY_OPERATION.get(
            operation,
            _COMPLETING_FAMILIES_BY_OPERATION["generic"],
        ),
        subject_terms=subject_terms,
        direct_control_terms=frozenset(direct_control_terms),
        subject_requires_full_match=bool(compound_subject),
    )


def _text_requests_operation(text: str, operation: str) -> bool:
    words = set(re.findall(r"[a-z]+", str(text).casefold()))
    if operation == "activation" and _ACTIVATION_PHRASE_PATTERN.search(text):
        return True
    return any(
        _GOAL_OPERATION_BY_WORD.get(word) == operation
        for word in words
    )


def _goal_subject(goal: str, operation: str) -> str:
    """Extract the object/value of one concise English desktop command."""
    if operation == "activation":
        compound_match = _COMPOUND_SEARCH_TO_MEDIA_PATTERN.search(str(goal))
        if compound_match is not None:
            return compound_match.group("subject").strip()
    patterns = {
        "activation": (
            r"\b(?:play|pause|resume|skip|put\s+on|listen\s+to)\s+"
            r"(.+?)(?=\s+(?:in|inside|on|using|with)\b|$)"
        ),
        "search": (
            r"\b(?:search(?:\s+for)?|find|look\s+up|lookup)\s+"
            r"(.+?)(?=\s+(?:in|inside|on|using|with)\b|$)"
        ),
        "click": (
            r"\b(?:click|press|tap)\s+(?:the\s+)?"
            r"(.+?)(?=\s+(?:in|inside|on|within)\b|$)"
        ),
        "text_input": (
            r"\b(?:type|write|enter|input)\s+"
            r"(.+?)(?=\s+(?:in|inside|into|on)\b|$)"
        ),
        "selection": (
            r"\b(?:select|choose|pick)\s+(?:the\s+)?"
            r"(.+?)(?=\s+(?:from|in|inside|on)\b|$)"
        ),
        "scroll": (
            r"\bscroll\s+(.+?)(?=\s+(?:in|inside|on|within)\b|$)"
        ),
        "focus": (
            r"\b(?:focus(?:\s+on)?|switch\s+to|bring)\s+(.+?)"
            r"(?=\s+(?:forward|to\s+the\s+front)\b|$)"
        ),
        "launch": r"\b(?:open|launch)\s+(?:the\s+)?(.+?)$",
    }
    pattern = patterns.get(operation)
    if not pattern:
        return goal
    match = re.search(pattern, goal, flags=re.IGNORECASE)
    return match.group(1).strip() if match else goal


def _contract_terms(value: str) -> frozenset[str]:
    terms = {
        term
        for term in re.findall(
            r"[^\W_]+", str(value).casefold(), flags=re.UNICODE,
        )
        if term not in _CONTRACT_STOP_TERMS
    }
    return frozenset(terms)


def _action_completion_terms(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    resolved_name: str = "",
) -> frozenset[str]:
    value_keys = {
        "open_app": ("app",),
        "focus_window": ("window",),
        "click_control": ("control",),
        # Search/text completion is aligned to what was entered, never merely
        # to the fact that the field happened to be named Search.
        "type_text": ("text",),
        "click_then_type": ("text",),
        "select_option": ("option",),
        "scroll_control": ("direction", "control"),
    }
    parts = []
    for key in value_keys.get(tool_name, ()):
        if key == "control" and resolved_name:
            # An element_id-only call supplies no "control" argument text at
            # all -- and even when both are given, the id is what actually
            # won (see WindowsUIControl._resolve's precedence), so a
            # mismatched control argument would be a decoy, not evidence.
            # The real, verified resolved name is always the trustworthy
            # source for goal-completion matching.
            parts.append(resolved_name)
        else:
            parts.append(str(arguments.get(key, "") or ""))
    values = " ".join(parts)
    terms = set(_contract_terms(values))
    if tool_name == "click_control":
        # Operation words are normally removed from subject matching, but a
        # visible generic Play/Pause/Next control is exactly the direct
        # control evidence required by an activation contract.
        terms.update(
            term
            for term in re.findall(r"[^\W_]+", values.casefold())
            if term in _DIRECT_CONTROL_LABEL_TERMS
        )
    return frozenset(terms)


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
