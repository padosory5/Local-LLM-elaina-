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

from brain.deliberation import ACT_AND_SAY, Decision, Goal, decide, interpret
from brain.deliberation.goal import SOURCE_ASKED, SOURCE_UTTERANCE
from brain.deliberation.profile import (
    ARTIST_FOR_TITLE,
    FAVOURITE_TRACK,
    OBSERVED,
    STATED,
)
from brain.skills import (
    MediaSurface,
    live_window_titles,
    playback_evidence,
    skill_for,
)
from brain.media_target import (
    MediaTarget,
    classify_media_request,
    classify_spotify_media_request,
    parse_spotify_media_target,
)
from tools.computer_control.computer_control import ComputerControl
from tools.computer_control.windows_ui_control import UIActionResult, WindowsUIControl
from tools.computer_control.windows_ui_observer import (
    ControlInfo,
    WindowInfo,
    WindowsUIObserver,
)


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
    # A confirmation must resume the same scan-scoped UIA element that
    # requested it.  The visible name is retained for the user, but is not a
    # sufficient replay identity because a window can contain several
    # same-named controls or change while Elaina is waiting for an answer.
    element_id: str = ""


@dataclass(frozen=True)
class PausedDesktopRun:
    """Work already done when a run was interrupted by the user.

    Carried through the takeover question so a "yes" continues from here
    instead of restarting. The completed families/terms matter as much as
    the prose steps: they are what ``_GoalCompletionContract`` reasons over,
    so seeding them is what keeps "typed the song into Search" from being
    accepted as "the song is playing" after a resume.
    """

    goal: str
    steps_taken: tuple[str, ...] = ()
    completed: tuple[tuple[str, frozenset[str]], ...] = ()
    surface_snapshot: dict[str, object] | None = None

    @property
    def progress_note(self) -> str:
        """What to tell the model it has already verifiably done."""
        if not self.steps_taken:
            return ""
        lines = "\n".join(f"- {step}" for step in self.steps_taken)
        return (
            "You already completed these verified steps before you were "
            "interrupted:\n"
            f"{lines}\n"
            "Continue from there. Do not repeat a step that is listed above."
        )


@dataclass(frozen=True)
class ActionPlanResult:
    status: str  # "done", "needs_confirmation", "needs_clarification",
                 # "interrupted", "failed"
    summary: str = ""
    pending: PendingConfirmation | None = None
    # Set only when status == "interrupted": the user took the mouse or
    # keyboard back mid-run and this is where to pick up.
    paused: PausedDesktopRun | None = None
    steps_taken: tuple[str, ...] = ()
    surface_context: DesktopSurfaceContext = field(
        default_factory=DesktopSurfaceContext
    )
    model_rounds: int = 0
    action_steps: int = 0
    recovery_used: bool = False
    failure_code: str = ""
    # Set only when status == "needs_clarification": what was asked, and
    # what answering it would complete. The caller holds this so the answer
    # continues this request instead of starting a new one.
    clarification: Decision | None = None


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
    observed_controls: tuple[ControlInfo, ...] = ()

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
    # For a concrete media request, the title is the only valid activation
    # target. Artist terms are context that must have been used to narrow the
    # search; they are never concatenated into the clickable title.
    activation_target_terms: frozenset[str] = frozenset()
    activation_context_terms: frozenset[str] = frozenset()

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
        if self.operation == "activation" and self.activation_target_terms:
            title_activated = any(
                self.activation_target_terms <= action.terms
                for action in candidates
            )
            if not title_activated:
                return False
            if not self.activation_context_terms:
                return True
            return any(
                action.family in {"text_input", "selection"}
                and self.activation_context_terms <= action.terms
                for action in completed_actions
            )
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
            "name": "play_media_item",
            "description": (
                "Start playing one exact item in a media app. This is the "
                "only correct way to play a track -- never click_control. "
                "Pass the element_id of the control whose accessible name is "
                "exactly the track title, with nothing appended: not the "
                "artist, not Radio, Mix, Station, or a playlist. It "
                "double-clicks that row the way a person does and then "
                "checks the app really is playing it."
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
                            "Fallback only, when no id is shown: the exact "
                            "track title, copied verbatim from "
                            "describe_window."
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
            "name": "press_key",
            "description": (
                "Send a key or chord to a focused window -- Enter to submit "
                "a search, Escape to close a menu. Use this only right after "
                "typing into a field that has no visible submit button. It "
                "is never a substitute for clicking a control: to press "
                "Play, Pause, or any other on-screen button, inspect the "
                "window and use click_control."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string"},
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "One key, or a chord such as [\"ctrl\", \"a\"]. "
                            "Supported: enter, tab, escape, backspace, "
                            "delete, space, home, end, pageup, pagedown, "
                            "up, down, left, right, ctrl, shift, alt."
                        ),
                    },
                },
                "required": ["window", "keys"],
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
    "title-and-artist query and re-observe the results. Then call "
    "play_media_item on the control whose name is exactly the track title -- "
    "clicking a track only opens it, so click_control can never play one. "
    "The artist is nearby context, not part of the label you activate. "
    "Never substitute a generic Play button, radio, mix, station, "
    "or playlist; do not stop after searching. A consequential click "
    "can only be offered for confirmation when its call used an exact "
    "current element_id; if the tool asks for re-observation, describe the "
    "window again instead of asking the user yet. If a tool reports "
    "confirmation_required, refused, ambiguous, verification_failed, or a "
    "scope violation, stop; "
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
    "click_then_type", "select_option", "scroll_control", "press_key",
    "play_media_item",
})
_ACTION_FAMILY_BY_TOOL = {
    "open_app": "launch",
    "focus_window": "focus",
    "click_control": "activation",
    "play_media_item": "activation",
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
    "stop": "activation",
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
    "stop": frozenset({"pause", "stop"}),
    "resume": frozenset({"resume", "play"}),
    "skip": frozenset({"skip", "next"}),
}

# Real applications are not in English. Spotify's Korean UI labels its
# transport controls "재생하기" and "일시 정지하기", and Korean is
# agglutinative, so the token equality used for English terms above can
# never match them -- "정지하기" is not "정지". These stems are therefore
# matched as substrings and mapped onto the English vocabulary the
# completion contract already reasons in, the same approach
# windows_ui_control.py already takes for its Korean committing keywords.
# Without this, Elaina really does pause the track and then reports that
# she could not.
_LOCALISED_CONTROL_STEMS = (
    ("재생", "play"),
    ("일시 정지", "pause"),
    ("일시정지", "pause"),
    ("정지", "pause"),
    ("중지", "pause"),
    ("다음", "next"),
    ("건너뛰기", "next"),
)


# Substrings that contain a transport stem but are not transport controls.
# "재생 목록" is "playlist": a library full of them would otherwise look like
# a wall of play buttons.
_LOCALISED_CONTROL_DECOYS = ("재생 목록", "재생목록", "재생 중", "재생중")


# Korean verb endings that turn a transport stem into a control label:
# "재생" (play) becomes "재생하기". Removing both is what lets a label be
# recognised as *only* an operation.
_LOCALISED_CONTROL_SUFFIXES = ("하기", "시키기", "해줘", "하세요")


def _without_localised_affixes(label: str) -> str:
    """A label with its non-English transport wording removed."""
    text = str(label or "").casefold()
    for decoy in _LOCALISED_CONTROL_DECOYS:
        if decoy in text:
            return text
    for stem, _english in _LOCALISED_CONTROL_STEMS:
        text = text.replace(stem, " ")
    for suffix in _LOCALISED_CONTROL_SUFFIXES:
        text = text.replace(suffix, " ")
    return text


def _localised_control_terms(label: str) -> set[str]:
    """English equivalents for transport controls named in another language."""
    text = str(label or "").casefold()
    for decoy in _LOCALISED_CONTROL_DECOYS:
        text = text.replace(decoy, " ")
    return {
        english for stem, english in _LOCALISED_CONTROL_STEMS if stem in text
    }


# Reach: the observer's window digest is capped at the most useful ~80
# controls, and a media transport bar sits well below that cut on a busy
# app -- measured on Spotify, whose tree holds 734 named elements. The
# direct path therefore asks the observer to resolve specific labels
# against the *live* tree instead of searching the capped digest.
# Kept deliberately short: each entry costs one full live tree scan, and a
# busy app is expensive to walk -- Spotify measured ~3s per scan over its
# 734 named elements. Two candidates per term (the user's language and
# English) covers the realistic cases without turning a "stop it" into a
# twenty-second search.
_TRANSPORT_LABELS_BY_TERM = {
    "pause": ("일시 정지하기", "Pause"),
    "stop": ("일시 정지하기", "Pause"),
    "play": ("재생하기", "Play"),
    "resume": ("재생하기", "Play"),
    "next": ("다음 트랙", "Next"),
    "skip": ("다음 트랙", "Next"),
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
# "ambiguous" is deliberately NOT here. It means several live controls
# matched the name the model used -- which is a recoverable mistake, not a
# dead end: the fix is to re-inspect the window and address one of them by
# its exact scan id. Measured live on Spotify, where a playing track offers
# several pause-like controls at once; treating that as terminal ended the
# run instead of letting the model name the one it meant. The round and
# repeated-step budgets still bound the retry.
_TERMINAL_FAILURES = frozenset({
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
# An app that was closed to the tray, or is cold-starting, needs longer than
# one that is merely being focused.
_LAUNCHED_WINDOW_ATTEMPTS = 16
# Playback starts a beat after the double-click lands, and the app
# renames its own window only once audio is actually running. Each
# attempt is one cheap window-title read, not a tree scan.
_PLAYBACK_VERIFY_ATTEMPTS = 8
_PLAYBACK_VERIFY_INTERVAL_SECONDS = 0.4

# A media app's search affordance is the one control the deterministic play
# path has to find by meaning rather than by id. Stems, not equality:
# Spotify labels it "Search", Korean Spotify "검색", and both appear with
# extra words around them ("Search Spotify", "검색하기").
# Removed from a control label to see what the label is *about*. Korean is
# agglutinative, so the longer form has to be tried first.
_PLAY_VERBS = ("재생하기", "재생", "play", "듣기")
_MEDIA_SEARCH_STEMS = ("search", "검색", "찾기", "찾아보기")
_MEDIA_SEARCH_FIELD_ROLES = frozenset({"edit", "combobox"})
# The app's own search control is named for the verb alone. Anything with
# more words around it belongs to something else.
_MEDIA_SEARCH_LABELS = frozenset({
    "search", "검색", "검색하기", "찾기", "찾아보기", "search spotify",
})
# Results arrive asynchronously after the query is typed, and a CEF tree
# rebuilds a beat behind what is already drawn.
# A cold tree needs waking before it can be said to lack a search box.
_MEDIA_TREE_WAKE_ATTEMPTS = 3
_MEDIA_TREE_WAKE_SECONDS = 0.4
_MEDIA_RESULT_ATTEMPTS = 3
_MEDIA_RESULT_SETTLE_SECONDS = 1.2
# One retry: a first double-click that lands while the result list is still
# reflowing hits the row that was there a moment ago.
_MEDIA_ACTIVATION_ATTEMPTS = 2


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
        session_actions: Any = None,
        sleeper=None,
        profile: Any = None,
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        # Waiting for a result list to arrive and for playback to start is
        # real elapsed time on a real app; tests supply their own clock.
        self._sleep = sleeper or time.sleep
        # What she has learned about this person across sessions. Optional:
        # without one she simply knows less and asks more.
        self.profile = profile
        self.observer = observer or WindowsUIObserver()
        self.control = control or WindowsUIControl(observer=self.observer)
        self.response_language = str(response_language or "en").strip().lower()
        # Records verified actions so a later "stop it" resolves against what
        # Elaina actually did rather than against model recall.
        self.session_actions = session_actions
        if computer_control is not None:
            self.computer_control = computer_control
        else:
            from security.policy import PolicyEngine

            self.computer_control = ComputerControl(
                PolicyEngine(), ui_observer=self.observer,
            )

    def act(
        self,
        goal: str | Goal,
        *,
        surface_context: DesktopSurfaceContext | None = None,
        prior_progress: PausedDesktopRun | None = None,
        assumption: str = "",
    ) -> ActionPlanResult:
        # A caller may hand over a request already read into slots, or the
        # words and let this read them. Either way the rest of the run works
        # from the typed goal: it is what says which values this request
        # actually named, and therefore what may be entered anywhere.
        already_decided = isinstance(goal, Goal)
        if already_decided:
            # The turn read this request and put it through the gate before
            # choosing this planner. Deciding again applies a second
            # assumption on top of the first and reports only the last one
            # -- measured live: "play some music" filled the title from
            # what she plays most, then the planner's own gate filled the
            # artist too and spoke only that.
            request = goal
            goal = request.utterance
        else:
            goal = str(goal).strip()
            request = interpret(goal)
        completion_contract = _completion_contract(goal)
        selected_provider = request.value("provider") if already_decided else ""
        media_request = classify_media_request(
            goal,
            application=selected_provider or "Spotify",
            preferred_provider=bool(selected_provider),
        )
        media_target = media_request.target
        if request.kind == "play_track" and request.has("title"):
            media_target = MediaTarget(
                application=selected_provider or "Spotify",
                title=request.value("title"),
                artist=request.value("artist"),
            )
        effective_surface = self._effective_surface(goal, surface_context)
        if effective_surface.lock_to_surface and not effective_surface.available:
            return ActionPlanResult(
                "failed",
                "I couldn't identify the page or window you meant.",
                surface_context=effective_surface,
                failure_code="surface_unavailable",
            )

        user_content = goal
        recent_note = self._recent_actions_note()
        if recent_note:
            user_content = f"{user_content}\n\n{recent_note}"
        if prior_progress is not None and prior_progress.progress_note:
            # Resuming after an interruption. The already-verified steps are
            # stated as fact so the model continues rather than redoing them.
            user_content = f"{goal}\n\n{prior_progress.progress_note}"
        # A bare "stop it" is the one request the model reliably gets wrong:
        # the target is not named in the goal at all, and on a real app the
        # right control sits among dozens of similarly-named ones in the
        # user's own language. Elaina already recorded what she played, so
        # this is resolvable from state without asking the model to guess.
        direct = self._try_direct_media_control(goal, effective_surface)
        if direct is not None:
            return direct
        # The gate. Every request passes through here before anything is
        # touched: act, act and say what was assumed, or ask one question.
        # A play request that names no track ("play any songs from my liked
        # list") has nothing to aim at, and asking is the correct outcome --
        # the alternative, before this existed, was to treat the sentence
        # itself as a search query and type it into the app.
        # What the caller already decided to assume, if it gated this
        # request itself. Saying it out loud is the whole point of the
        # act-and-say exit, so it has to survive the handover.
        if prior_progress is None and not already_decided:
            decision = decide(
                request,
                recent_subject=self._recent_media_subject(),
                profile=self.profile,
            )
            if decision.asks:
                return ActionPlanResult(
                    "needs_clarification",
                    decision.question,
                    surface_context=effective_surface,
                    failure_code="needs_clarification",
                    clarification=decision,
                )
            if decision.action == ACT_AND_SAY:
                # A value she filled in herself, on a cheap and reversible
                # action. She does it and says so, rather than spending a
                # turn asking about something easily undone.
                request = decision.goal
                assumption = decision.assumption
                if request.has("title"):
                    media_target = MediaTarget(
                        application=request.value("provider") or "Spotify",
                        title=request.value("title"),
                        artist=request.value("artist"),
                    )

        direct_text = self._try_direct_text_input(request, effective_surface)
        if direct_text is not None:
            return direct_text

        # A concrete "play <title> by <artist>" is resolvable from live state
        # alone, and that is the request the model half of this loop gets
        # wrong most expensively -- it types the query, then clicks the
        # radio station named after the song. Try it deterministically
        # first; anything this cannot resolve falls through to the model.
        # A surface-locked request ("play it here") named a window, and this
        # path would go looking for the media app instead. That belongs to
        # the ordinary loop, which honours the lock.
        if prior_progress is None and not effective_surface.lock_to_surface:
            played = self._run_skill(
                request, effective_surface, goal, assumption,
            )
            if played is not None:
                return played

        system_prompt = self._system_prompt(effective_surface)
        if media_target is not None:
            system_prompt += "\n" + media_target.planner_constraint()
        messages: list[Any] = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {"role": "user", "content": user_content},
        ]
        steps: list[str] = list(
            prior_progress.steps_taken if prior_progress is not None else ()
        )
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
        completed_actions: list[_CompletedAction] = [
            _CompletedAction(family=family, terms=terms)
            for family, terms in (
                prior_progress.completed if prior_progress is not None else ()
            )
        ]
        completion_nudge_used = False
        verification_nudge_used = False
        observations_after_last_action = 0
        completion_ready = False
        last_successful_action_message = ""
        latest_observed_controls: tuple[ControlInfo, ...] = ()

        def paused_run() -> PausedDesktopRun:
            return PausedDesktopRun(
                goal=goal,
                steps_taken=tuple(steps),
                completed=tuple(
                    (action.family, action.terms) for action in completed_actions
                ),
                surface_snapshot=effective_surface.to_public_snapshot(),
            )

        def finish(
            status: str,
            summary: str,
            *,
            rounds: int,
            pending: PendingConfirmation | None = None,
            paused: PausedDesktopRun | None = None,
            failure_code: str = "",
        ) -> ActionPlanResult:
            return ActionPlanResult(
                status,
                summary,
                pending=pending,
                paused=paused,
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
                if last_execution is not None and (
                    last_execution.status == "wrong_media_target"
                ):
                    return finish(
                        "failed",
                        last_execution.message,
                        rounds=round_index,
                        failure_code="wrong_media_target",
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

                    refusal = self._media_activation_refusal(
                        media_target,
                        tool_name,
                        arguments,
                        latest_observed_controls,
                    ) or self._unrequested_value_refusal(request, tool_name, arguments)
                    execution = refusal or self._run_tool_call(
                        tool_name,
                        arguments,
                        surface=effective_surface,
                        media_target=media_target,
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
                    if execution.observed_controls:
                        latest_observed_controls = execution.observed_controls

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

                    if execution.status == "user_took_over":
                        # The person reclaimed the mouse or keyboard. Stop at
                        # once and keep what is already verified, so a "yes"
                        # continues rather than starting the job again.
                        return finish(
                            "interrupted",
                            execution.message,
                            rounds=round_index,
                            paused=paused_run(),
                            failure_code="user_took_over",
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
                                self._remember_action(
                                    execution, arguments, family,
                                )
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

    def _remember_action(
        self,
        execution: "_ToolExecution",
        arguments: dict[str, Any],
        family: str,
    ) -> None:
        """Record one verified action so "stop it" has a real target.

        Only verified actions are kept. An unconfirmed click is not evidence
        that anything started playing, and remembering it would let a later
        "stop it" act on a track that never began.
        """
        memory = self.session_actions
        if memory is None:
            return
        snapshot = execution.window_snapshot
        window_title = str(getattr(snapshot, "title", "") or "")
        # WindowInfo.app_name carries the UIA class ("Dialog" for Spotify),
        # which is meaningless to a person and useless as a follow-up
        # target. The window title is the identity a user would name.
        app = window_title or str(getattr(snapshot, "app_name", "") or "")
        subject = str(
            arguments.get("text")
            or arguments.get("option")
            or arguments.get("app")
            or ""
        ).strip()
        if not subject and family in {"activation", "selection"}:
            # A clicked item usually names itself: Spotify's play button is
            # called "Weightless by Marconi Union <play>". A bare transport
            # control (Play/Pause/Next) names only the operation, and must
            # not be recorded as though it were the thing being played.
            resolved = execution.resolved_control_name.strip()
            if resolved and not self._is_generic_control(resolved):
                subject = resolved
        try:
            memory.record(
                app=app,
                family=family,
                subject=subject,
                window_title=window_title,
                control_name=execution.resolved_control_name,
                window_handle=getattr(snapshot, "handle", None),
            )
        except Exception:
            # Memory is an aid to later turns, never a reason to fail this one.
            pass

    def _learn_from(self, request: Goal) -> None:
        """Let a verified play teach her what this person means.

        Only values the person actually supplied are learned from. A slot
        she filled in *from the profile* is not new evidence for the
        profile -- without that rule one lucky guess becomes a certainty by
        being repeated back to itself.
        """
        profile = self.profile
        if profile is None or request.kind != "play_track":
            return
        title_slot = request.slots.get("title")
        if title_slot is None or title_slot.source not in {
            SOURCE_UTTERANCE, SOURCE_ASKED,
        }:
            return
        try:
            profile.observe(FAVOURITE_TRACK, title_slot.value, source=OBSERVED)
            artist = request.slots.get("artist")
            if artist is not None and artist.source in {
                SOURCE_UTTERANCE, SOURCE_ASKED,
            }:
                # Naming the artist outright settles which of two songs
                # with this title they mean, now and next time.
                profile.observe(
                    ARTIST_FOR_TITLE,
                    artist.value,
                    key=title_slot.value,
                    source=STATED,
                )
        except Exception:
            # Learning is a convenience; it never fails a completed action.
            pass

    def _recent_media_subject(self) -> str:
        """The last thing she verifiably played this session, if any.

        Real recorded state, not a guess about taste: only verified actions
        reach session memory, and a bare transport click never becomes a
        subject. This is what lets "play some music" be answered by doing
        something sensible and saying so, instead of by a question.
        """
        memory = self.session_actions
        if memory is None:
            return ""
        try:
            # Activations only. Measured live: last_subject() also returns
            # launches, so opening Spotify made "Spotify" the remembered
            # song, and a later "play some music" tried to play the app.
            played = memory.recent(family="activation")
        except Exception:
            return ""
        for action in reversed(list(played or ())):
            subject = str(getattr(action, "subject", "") or "").strip()
            if subject and not self._is_generic_control(subject):
                return subject
        return ""

    def _run_skill(
        self,
        request: Goal,
        surface: DesktopSurfaceContext,
        goal: str,
        assumption: str = "",
    ) -> ActionPlanResult | None:
        """Run the named procedure that serves this request, if there is one.

        A skill establishes its own target from live state and proves its
        own outcome; when it cannot, it hands back and the ordinary
        planning loop takes over with every guard still in place. Handing
        back is the only way this returns None -- it never means acting on
        something the skill could not establish.
        """
        skill = skill_for(request)
        if skill is None:
            return None
        result = skill.run(
            request,
            MediaSurface(
                observer=self.observer,
                control=self.control,
                computer_control=self.computer_control,
                session_actions=self.session_actions,
                sleeper=self._sleep,
            ),
        )
        if result.handed_back:
            return None
        if result.status == "done":
            self._learn_from(request)
        if result.status == "interrupted":
            return ActionPlanResult(
                "interrupted",
                result.summary,
                paused=PausedDesktopRun(
                    goal=goal,
                    steps_taken=result.steps,
                    surface_snapshot=surface.to_public_snapshot(),
                ),
                steps_taken=result.steps,
                surface_context=surface,
                action_steps=len(result.steps),
                failure_code="user_took_over",
            )
        return ActionPlanResult(
            result.status,
            # A value she filled in herself is said out loud in place of the
            # ordinary summary, so being wrong costs one sentence.
            assumption if assumption and result.status == "done"
            else result.summary,
            steps_taken=result.steps,
            surface_context=surface,
            action_steps=len(result.steps),
            failure_code=result.failure_code,
        )

    def _try_direct_text_input(
        self,
        request: Goal,
        surface: DesktopSurfaceContext,
    ) -> ActionPlanResult | None:
        """Type into one unambiguous, live-observed editable surface.

        A request such as "Write hello in Notepad" already names both the
        exact value and an active document. Asking the planner model to copy
        a scan-scoped id adds latency and can make it invent an id. This path
        acts only when the current window has exactly one enabled Document
        control; the trusted UI driver still resolves and verifies that
        control before reporting success.  Ordinary Edit and ComboBox fields
        keep using the planning loop, which can inspect and verify them
        across multiple UI states.
        """
        if request.kind != "text_input" or not request.has("text"):
            return None
        window = surface.as_window_info()
        if window is None:
            try:
                window = self.observer.get_active_window()
            except Exception:
                return None
        if window is None:
            return None
        # Windows 11's modern Notepad exposes its editor as a Korean-named
        # Document under a Dialog window.  This small, observed fast path
        # prevents the planner model from fabricating its scan id there.
        # Every other surface continues through the normal inspect/act/
        # re-inspect loop below.
        if (
            window.class_name != "Dialog"
            or "notepad" not in window.title.casefold()
        ):
            return None
        try:
            observation = self.observer.describe_window(window)
        except Exception:
            return None
        if getattr(observation, "status", "") != "observed":
            return None
        editable = [
            control for control in observation.controls
            if str(getattr(control, "role", ""))
            == "Document"
            and getattr(control, "is_enabled", True) is not False
        ]
        if len(editable) != 1:
            return None
        control = editable[0]
        focused = self.control.focus_window(window)
        if focused.status == "user_took_over":
            return ActionPlanResult(
                "interrupted", focused.message,
                surface_context=surface,
                failure_code="user_took_over",
            )
        if focused.status != "focused":
            return None
        typed = self.control.type_text(
            window,
            control.name,
            request.value("text"),
            element_id=control.element_id,
        )
        if typed.status == "user_took_over":
            return ActionPlanResult(
                "interrupted", typed.message,
                steps_taken=(focused.message,),
                surface_context=surface,
                action_steps=1,
                failure_code="user_took_over",
            )
        # When the driver cannot verify the entry, retain the ordinary
        # planner loop: it will re-observe the window before declaring the
        # request complete.  This shortcut is only safe for a confirmed
        # write.
        if typed.status != "typed" or typed.verified is not True:
            return None
        return ActionPlanResult(
            "done",
            typed.message,
            steps_taken=(focused.message, typed.message),
            surface_context=surface,
            action_steps=1,
        )

    def _try_direct_media_control(
        self, goal: str, surface: DesktopSurfaceContext,
    ) -> ActionPlanResult | None:
        """Resolve "stop it" / "pause that" from what Elaina actually did.

        Returns None whenever this is not clearly such a request, so the
        ordinary planning loop still handles everything else. Nothing here
        is model-chosen: the app comes from recorded state, and the control
        comes from a live scan of that app.
        """
        memory = self.session_actions
        if memory is None:
            return None
        words = re.findall(r"[^\W_]+", str(goal).casefold())
        # Deictic and short. "Pause the song I played in Spotify yesterday"
        # is a real request, but not this one.
        if not words or len(words) > 5:
            return None
        wanted_word = next(
            (word for word in words if word in _DIRECT_CONTROL_TERMS_BY_GOAL_WORD),
            None,
        )
        if wanted_word is None:
            return None
        # Everything else in the goal must be filler or a generic stand-in
        # ("it", "the music"). A named target belongs to the normal path.
        remainder = (
            set(words) - {wanted_word} - _CONTRACT_STOP_TERMS
            - _GENERIC_MEDIA_SUBJECT_TERMS
        )
        if remainder:
            return None
        try:
            last = memory.last_subject() or memory.last_action()
        except Exception:
            return None
        if last is None:
            return None
        wanted_terms = _DIRECT_CONTROL_TERMS_BY_GOAL_WORD[wanted_word]

        target = self._live_titles_by_handle().get(
            last.window_handle, last.window_title or last.app,
        )
        if not target:
            return None
        focus = self.control.focus_window(target)
        if focus.status != "focused":
            return None
        result = None
        tried: set[str] = set()
        candidates = [
            label
            for term in sorted(wanted_terms)
            for label in _TRANSPORT_LABELS_BY_TERM.get(term, ())
        ]
        for label in candidates:
            if label in tried:
                continue
            tried.add(label)
            attempt = self._click_transport_label(target, label)
            if attempt.status == "clicked":
                result = attempt
                break
            if attempt.status == "confirmation_required":
                # A transport control should never be committing, but if one
                # is, the ordinary path owns that conversation.
                return None
        if result is None:
            return None
        self._remember_direct_action(result, last)
        subject = last.subject or "it"
        return ActionPlanResult(
            "done",
            f"Stopped {subject}." if wanted_word in {"stop", "pause"}
            else f"{wanted_word.capitalize()}d {subject}.",
            steps_taken=(focus.message, result.message),
            surface_context=surface,
            action_steps=2,
        )

    def _click_transport_label(self, target: Any, label: str) -> UIActionResult:
        """Click one transport control, tolerating duplicate names.

        Measured live: a playing Spotify exposes three buttons all named
        "일시 정지하기" -- the main bar, the mini player, and the now-playing
        view -- so name matching refuses the whole request as ambiguous and
        "stop it" dies there. They are three renderings of the same control,
        so any visible one is the right one; it is still addressed by a real
        scan id rather than by the name that could not distinguish them.
        """
        attempt = self.control.click_control(target, label)
        if attempt.status != "ambiguous":
            return attempt
        observation = self.observer.describe_window(target)
        if getattr(observation, "status", "") != "observed":
            return attempt
        key = _normalized_label(label)
        matches = [
            control for control in observation.controls
            if _normalized_label(control.name) == key
        ]
        chosen = next(
            (control for control in matches if control.is_actionable),
            matches[0] if matches else None,
        )
        if chosen is None or not chosen.element_id:
            return attempt
        return self.control.click_control(
            target, chosen.name, element_id=chosen.element_id,
        )

    @staticmethod
    def _control_matches_terms(name: str, wanted: frozenset[str]) -> bool:
        """Whether a control's label names one of the wanted operations."""
        text = str(name or "").casefold()
        if not text:
            return False
        tokens = set(re.findall(r"[^\W_]+", text))
        return bool(
            (tokens & wanted) or (_localised_control_terms(text) & wanted)
        )

    def _remember_direct_action(self, result: Any, previous: Any) -> None:
        memory = self.session_actions
        if memory is None:
            return
        try:
            memory.record(
                app=result.window_title or previous.app,
                family="activation",
                subject=previous.subject,
                window_title=result.window_title,
                control_name=result.control_name,
                window_handle=previous.window_handle,
            )
        except Exception:
            pass

    def _recent_actions_note(self) -> str:
        """What Elaina has already done, for deictic follow-ups.

        "Stop it" carries no target of its own. Without this the planner has
        only the model's recollection of the conversation to go on, which
        for a small local model means a confident, plausible, wrong guess.
        This is recorded state: only verified actions reach it.
        """
        memory = self.session_actions
        if memory is None:
            return ""
        try:
            recent = memory.recent_context()
        except Exception:
            return ""
        if not recent:
            return ""
        live_titles = self._live_titles_by_handle()
        lines = []
        for item in recent[-4:]:
            subject = item.get("subject") or ""
            action = item.get("action") or ""
            # Name the window as it is *now*. Spotify renames its window to
            # the track it is playing, so the title recorded when a song was
            # started names nothing by the time "stop it" arrives -- and the
            # planner then wastes rounds looking for a window that no longer
            # exists under that name.
            handle = item.get("handle")
            app = live_titles.get(handle) or item.get("app") or ""
            lines.append(
                f"- {action} {subject!r} in {app}" if subject
                else f"- {action} in {app}"
            )
        return (
            "Things you have already done on this machine, most recent last. "
            "Use these to resolve a request that refers to something without "
            "naming it (\"stop it\", \"pause that\"):\n" + "\n".join(lines)
        )

    def _live_titles_by_handle(self) -> dict[int, str]:
        """Current title of every open window, keyed by handle."""
        return live_window_titles(self.observer)

    @staticmethod
    def _is_generic_control(name: str) -> bool:
        """True when a control name is only an operation, not an item.

        Measured live: this was English-only, so on a Korean Spotify the
        bare "재생하기" -- Play, and nothing else -- read as a named item
        and was clicked, starting whatever happened to be queued. That is
        the exact failure the media guard exists to prevent, walking
        through it in another language.
        """
        text = _without_localised_affixes(str(name))
        terms = {term for term in re.findall(r"[^\W_]+", text.casefold())}
        if not terms:
            return True
        return terms <= (_DIRECT_CONTROL_LABEL_TERMS | _GENERIC_MEDIA_SUBJECT_TERMS)

    @staticmethod
    def _unrequested_value_refusal(
        request: Goal,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> _ToolExecution | None:
        """Refuse to enter anything the request did not name.

        This is the boundary the whole Goal type exists to create. Before
        it, a request travelled from the microphone to the keyboard as one
        unbroken string, and the model -- asked to type something, holding
        a sentence -- typed the sentence: "Play any songs from my liked
        list" went into Spotify's search box verbatim, on top of the
        previous query. A value that the request named passes. The request
        restating itself does not, whatever field it is aimed at.
        """
        if tool_name not in {"type_text", "click_then_type"}:
            return None
        text = str(arguments.get("text", "") or "").strip()
        if not text or request.permits_typing(text):
            return None
        return _ToolExecution(
            tool_name,
            "unrequested_value",
            (
                f"{text!r} is the request itself, not a value from it. "
                f"{request.refusal_hint()}"
            ),
            is_action=True,
            verified=False,
        )

    @staticmethod
    def _media_activation_refusal(
        media_target: MediaTarget | None,
        tool_name: str,
        arguments: dict[str, Any],
        observed_controls: tuple[ControlInfo, ...],
    ) -> _ToolExecution | None:
        """Keep a media request on the one item the person actually asked for.

        This runs before the pointer moves, and it draws two different lines.

        Getting to the results is ordinary work: opening Search, typing the
        query, switching to the Songs filter, going back. None of that is
        touched -- an earlier version refused every click while a media goal
        was active, which blocked Spotify's own Search navigation and left
        the run no way to reach the track at all.

        Activating is where the damage happens. "Bang Bang Radio" sits
        directly beside "Bang Bang" in Spotify's results, and a generic Play
        button plays whatever the app has queued: both satisfy a naive "did
        something start playing" check while playing the wrong thing. So an
        activation must be play_media_item on the exact title, with the
        artist visible as nearby context rather than glued onto the label.
        """
        if media_target is None or tool_name not in {
            "click_control", "play_media_item",
        }:
            return None

        title_key = _normalized_label(media_target.title)
        title_terms = _contract_terms(media_target.title)
        supplied_id = str(arguments.get("element_id", "") or "").strip()
        supplied_name = str(arguments.get("control", "") or "").strip()
        candidates = [
            (index, control)
            for index, control in enumerate(observed_controls)
            if _normalized_label(control.name) == title_key
        ]
        selected: tuple[int, ControlInfo] | None = None
        if supplied_id:
            selected = next(
                (
                    (index, control)
                    for index, control in enumerate(observed_controls)
                    if control.element_id == supplied_id
                ),
                None,
            )
        elif supplied_name and _normalized_label(supplied_name) == title_key:
            if len(candidates) == 1:
                selected = candidates[0]

        if tool_name == "click_control":
            label = _normalized_label(
                selected[1].name if selected is not None else supplied_name
            )
            if not label:
                # Nothing resolvable to judge. A named preparation step is
                # still allowed; an id that is not in the latest observation
                # is a stale address the model should refresh.
                if not supplied_id:
                    return None
                return _ToolExecution(
                    tool_name,
                    "wrong_media_target",
                    (
                        f"{supplied_id!r} is not in the latest observation of "
                        "this window. Observe it again before clicking."
                    ),
                    is_action=True,
                    verified=False,
                )
            if label == title_key:
                return _ToolExecution(
                    tool_name,
                    "wrong_media_target",
                    (
                        f"A single click on {media_target.title!r} opens it "
                        "rather than playing it. Call play_media_item with "
                        "that same element id instead."
                    ),
                    is_action=True,
                    verified=False,
                )
            label_terms = _contract_terms(label)
            if title_terms and (title_terms & label_terms):
                return _ToolExecution(
                    tool_name,
                    "wrong_media_target",
                    (
                        f"{label!r} is not the requested track. Radio, Mix, "
                        "Station, playlist, and title-plus-artist rows share "
                        f"the words in {media_target.title!r} and play "
                        "something else. Use play_media_item on the row whose "
                        f"name is exactly {media_target.title!r}."
                    ),
                    is_action=True,
                    verified=False,
                )
            # Raw tokens, not _contract_terms: that helper strips exactly
            # the operation words ("play", "pause") this check is looking for.
            label_tokens = set(
                re.findall(r"[^\W_]+", label)
            ) | _localised_control_terms(label)
            if DesktopActionPlanner._is_generic_control(label) and (
                label_tokens & _DIRECT_CONTROL_LABEL_TERMS
            ):
                return _ToolExecution(
                    tool_name,
                    "wrong_media_target",
                    (
                        f"{label!r} is a generic transport control, so it "
                        "plays whatever the app has queued. Use "
                        "play_media_item on the exact title "
                        f"{media_target.title!r}."
                    ),
                    is_action=True,
                    verified=False,
                )
            # Everything else is preparation: search, navigation, filters.
            return None

        if selected is None or _normalized_label(selected[1].name) != title_key:
            return _ToolExecution(
                tool_name,
                "wrong_media_target",
                (
                    f"Do not play {supplied_name or supplied_id or 'that control'!r}. "
                    "Re-observe Spotify and play only the exact track title "
                    f"{media_target.title!r} by its current element id. Generic "
                    "Play, Radio, Mix, Station, playlist, and title-plus-artist "
                    "labels are not the requested track."
                ),
                is_action=True,
                verified=False,
            )

        if media_target.artist:
            artist_key = _normalized_label(media_target.artist)
            selected_index = selected[0]
            nearby = observed_controls[
                max(0, selected_index - 6):selected_index + 7
            ]
            artist_visible = any(
                artist_key in _normalized_label(
                    " ".join((control.name, control.value))
                )
                for control in nearby
            )
            if not artist_visible:
                return _ToolExecution(
                    tool_name,
                    "wrong_media_target",
                    (
                        f"The exact title {media_target.title!r} is visible, but "
                        f"artist {media_target.artist!r} is not visible near that "
                        "result. Narrow the search and re-observe instead of "
                        "risking a same-title track."
                    ),
                    is_action=True,
                    verified=False,
                )
        return None

    def _verified_playback(
        self,
        name: str,
        result: UIActionResult,
        snapshot: WindowInfo | None,
        media_target: MediaTarget | None,
        *,
        baseline: str = "",
    ) -> _ToolExecution:
        """Report a play attempt only as well as it can actually be proved."""
        playing, evidence = self._playback_evidence(
            snapshot, media_target, baseline=baseline,
        )
        if playing:
            return _ToolExecution(
                name,
                "clicked",
                f"{result.message} It is playing now.",
                is_action=True,
                verified=True,
                evidence=evidence,
                window_snapshot=snapshot,
                resolved_control_name=result.control_name,
            )
        return _ToolExecution(
            name,
            "playback_unverified",
            (
                f"{result.message} Nothing shows it actually playing yet. "
                "Observe the window again and, if the track is still not "
                "playing, activate it from its own row rather than from a "
                "generic control."
            ),
            is_action=True,
            verified=False,
            evidence=evidence,
            window_snapshot=snapshot,
            resolved_control_name=result.control_name,
        )

    def _playback_evidence(
        self,
        snapshot: WindowInfo | None,
        media_target: MediaTarget | None,
        *,
        baseline: str = "",
    ) -> tuple[bool, str]:
        """Whether the requested track is audibly playing, from live state."""
        if media_target is None:
            return False, "No media target was bound to this activation."
        return playback_evidence(
            self.observer,
            snapshot,
            media_target.title,
            baseline=baseline,
            sleeper=self._sleep,
        )

    def _run_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        surface: DesktopSurfaceContext,
        media_target: MediaTarget | None = None,
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
                    observed_controls=tuple(observation.controls),
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
                "press_key", "play_media_item",
            }:
                target = locked_window or str(arguments.get("window", ""))
                snapshot = self._snapshot_for_target(target)
                baseline_title = (
                    self._live_titles_by_handle().get(
                        getattr(snapshot, "handle", None), ""
                    )
                    if name == "play_media_item"
                    else ""
                )
                result = self._run_control_action(name, target, arguments)
                if name == "play_media_item" and result.status == "clicked":
                    return self._verified_playback(
                        name, result, snapshot, media_target,
                        baseline=baseline_title,
                    )
                pending = None
                if result.status == "confirmation_required":
                    element_id = str(
                        arguments.get("element_id", "") or ""
                    ).strip()
                    if not element_id:
                        # Name matching is a useful recovery route while
                        # planning, but it is not a stable identity to carry
                        # across a consent turn.  Do not create a pending
                        # action that a later "yes" would replay by name;
                        # make the model obtain a fresh live scan instead.
                        return _ToolExecution(
                            name,
                            "needs_reobservation",
                            (
                                f"{result.message} I need to inspect the "
                                "window again and use its exact control id "
                                "before I can ask for confirmation."
                            ),
                            is_action=True,
                            verified=False,
                            evidence=(
                                "A native confirmation requires a current "
                                "scan-scoped element id."
                            ),
                            window_snapshot=snapshot,
                            resolved_control_name=result.control_name,
                        )
                    pending = PendingConfirmation(
                        window_title=result.window_title,
                        control_name=result.control_name,
                        window_snapshot=snapshot,
                        element_id=element_id,
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
        if name == "play_media_item":
            # A list row reads one click and two clicks as different
            # instructions: on Spotify a single click on a track title opens
            # its album page, and only a double-click plays the track. A
            # driver without a real pointer cannot express that at all, so it
            # falls back to the invoke it does have rather than silently
            # doing nothing.
            activator = getattr(self.control, "double_click_control", None)
            if activator is None:
                return self.control.click_control(
                    target, str(arguments.get("control", "")), **id_kwargs,
                )
            return activator(
                target, str(arguments.get("control", "")), **id_kwargs,
            )
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
        if name == "press_key":
            keys = arguments.get("keys") or []
            if isinstance(keys, str):
                keys = [keys]
            keys = [str(key).strip() for key in keys if str(key).strip()]
            if not keys:
                return UIActionResult(
                    "refused", "Tell me which key to press.",
                )
            presser = getattr(self.control, "press_key", None)
            if presser is None:
                # The Invoke driver has no keyboard of its own. Say so rather
                # than silently doing nothing the model will treat as done.
                return UIActionResult(
                    "unavailable",
                    "Sending raw keystrokes needs the screen driver; this "
                    "one can only operate named controls.",
                )
            return presser(target, *keys)
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

    def _wait_for_window(
        self, hint: str, *, attempts: int = _WINDOW_APPEAR_ATTEMPTS,
    ) -> WindowInfo | None:
        for _ in range(attempts):
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
        element_id: str = "",
    ) -> ActionPlanResult:
        """Perform only the exact confirmed click on the frozen surface."""
        element_id = str(element_id or "").strip()
        surface = DesktopSurfaceContext.from_window_info(
            window_snapshot,
            lock_to_surface=True,
        ) if window_snapshot is not None else DesktopSurfaceContext(
            window_title=window_title,
            lock_to_surface=True,
        )
        if not element_id:
            return ActionPlanResult(
                "failed",
                (
                    "I lost the exact control reference, so I won't replay "
                    "that click by name. Please ask me to inspect the window "
                    "again."
                ),
                surface_context=surface,
                model_rounds=0,
                failure_code="missing_element_id",
            )
        target: str | WindowInfo = window_snapshot or window_title
        result = self.control.click_control(
            target, control_name, confirmed=True, element_id=element_id,
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
    media_target = parse_spotify_media_target(semantic_text)
    return _GoalCompletionContract(
        operation=operation,
        completing_families=_COMPLETING_FAMILIES_BY_OPERATION.get(
            operation,
            _COMPLETING_FAMILIES_BY_OPERATION["generic"],
        ),
        subject_terms=subject_terms,
        direct_control_terms=frozenset(direct_control_terms),
        subject_requires_full_match=bool(compound_subject),
        activation_target_terms=(
            _contract_terms(media_target.title)
            if media_target is not None and operation == "activation"
            else frozenset()
        ),
        activation_context_terms=(
            _contract_terms(media_target.artist)
            if media_target is not None and operation == "activation"
            else frozenset()
        ),
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


def _without_play_verb(label: str) -> str:
    """The label with its play verb removed, in either language."""
    text = _normalized_label(label)
    for verb in _PLAY_VERBS:
        text = text.replace(verb, " ")
    return " ".join(text.split())


def _names_play(label: str) -> bool:
    """Whether a control label is a play/resume transport control."""
    text = _normalized_label(label)
    tokens = set(re.findall(r"[^\W_]+", text)) | _localised_control_terms(text)
    return "play" in tokens


def _is_search_label(label: str) -> bool:
    """Whether a label names the search affordance and nothing else."""
    return _normalized_label(label) in _MEDIA_SEARCH_LABELS


def _has_search_stem(label: str) -> bool:
    """Whether a control label names the app's own search affordance."""
    text = _normalized_label(label)
    return any(stem in text for stem in _MEDIA_SEARCH_STEMS)


def _role_key(role: str) -> str:
    return re.sub(r"[^a-z]", "", str(role or "").casefold())


def _normalized_label(value: str) -> str:
    """Unicode-safe exact-label key used before any media click."""
    import unicodedata

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split()).strip()


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
        "play_media_item": ("control",),
        # Search/text completion is aligned to what was entered, never merely
        # to the fact that the field happened to be named Search.
        "type_text": ("text",),
        "click_then_type": ("text",),
        "select_option": ("option",),
        "scroll_control": ("direction", "control"),
        "press_key": (),
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
    if tool_name in {"click_control", "play_media_item"}:
        # Operation words are normally removed from subject matching, but a
        # visible generic Play/Pause/Next control is exactly the direct
        # control evidence required by an activation contract.
        terms.update(
            term
            for term in re.findall(r"[^\W_]+", values.casefold())
            if term in _DIRECT_CONTROL_LABEL_TERMS
        )
        terms.update(_localised_control_terms(values))
    return frozenset(terms)


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
