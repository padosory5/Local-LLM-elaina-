"""Exercise the real desktop-planning model against a simulated UI tree.

No real application is opened and no keyboard or mouse input is generated.
The harness verifies that the configured model can complete a Spotify search,
does not reduce a playback goal to search alone, and cannot leave a frozen
GitHub page to open Windows Settings.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import ollama


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.desktop_action_planner import (  # noqa: E402
    DesktopActionPlanner,
    DesktopSurfaceContext,
)
from config.loader import Config  # noqa: E402
from scripts.console_style import status_label  # noqa: E402
from tools.computer_control.computer_control import ComputerActionResult  # noqa: E402
from tools.computer_control.windows_ui_control import UIActionResult  # noqa: E402
from tools.computer_control.windows_ui_observer import (  # noqa: E402
    ControlInfo,
    WindowInfo,
    WindowObservation,
)


@dataclass
class SimulatedState:
    surface: str
    query: str = ""
    playing: bool = False
    opened_apps: int = 0
    clicked_controls: list[str] = field(default_factory=list)
    typed_text: str = ""
    type_attempts: list[tuple[str, str, str]] = field(default_factory=list)


class SimulatedObserver:
    def __init__(self, state: SimulatedState) -> None:
        self.state = state

    def window(self) -> WindowInfo:
        if self.state.surface == "github":
            return WindowInfo(
                "sample/repository - Google Chrome",
                app_name="Google Chrome",
                is_active=True,
                handle=200,
                process_id=201,
                class_name="Chrome_WidgetWin_1",
            )
        if self.state.surface == "notepad":
            # Class "Dialog" and a non-Latin control label are real,
            # live-observed traits of Windows 11's modern Notepad -- not
            # simplified for this simulation. See tools/windows_ui_observer
            # .py's _INTERACTIVE_ROLES comment for how this was found.
            return WindowInfo(
                "Untitled - Notepad",
                app_name="Notepad",
                is_active=True,
                handle=300,
                process_id=301,
                class_name="Dialog",
            )
        return WindowInfo(
            "Dynamite - Spotify" if self.state.playing else "Spotify Premium",
            app_name="Spotify",
            is_active=True,
            handle=100,
            process_id=101,
            class_name="Chrome_WidgetWin_1",
        )

    def get_active_window(self):
        return self.window()

    def list_windows(self):
        return (self.window(),)

    def find_window(self, target):
        if isinstance(target, WindowInfo):
            return self.window() if target.handle == self.window().handle else None
        query = str(target).casefold()
        window = self.window()
        if query in window.title.casefold():
            return window
        if self.state.surface == "spotify" and "spotify" in query:
            return window
        if self.state.surface == "github" and any(
            name in query for name in ("github", "chrome")
        ):
            return window
        if self.state.surface == "notepad" and "notepad" in query:
            return window
        return None

    def describe_window(self, target):
        if self.find_window(target) is None:
            return WindowObservation(
                "not_found", message="The scoped window is no longer available."
            )
        if self.state.surface == "github":
            controls = (
                ControlInfo("Hyperlink", "Code", is_actionable=True),
                ControlInfo("Hyperlink", "Issues", is_actionable=True),
                ControlInfo("Hyperlink", "Pull requests", is_actionable=True),
            )
        elif self.state.surface == "notepad":
            # The real editable surface, plus a decoy status control the
            # model was live-observed picking instead ("Line 1, Column 1")
            # before the _INTERACTIVE_ROLES/element_id fixes. Both carry
            # real scan-shaped ids so element_id targeting is exercised the
            # same way a real describe_window() scan would produce it.
            controls = (
                ControlInfo(
                    "Document", "텍스트 편집기", is_actionable=True,
                    element_id="scan1-e0",
                ),
                ControlInfo(
                    "Text", "줄 1, 열 1", is_actionable=False,
                    element_id="scan1-e1",
                ),
            )
        else:
            controls = [
                ControlInfo(
                    "Edit", "Search", value=self.state.query,
                    is_actionable=True,
                ),
            ]
            if self.state.query:
                # The result mirrors the submitted query. A plain search must
                # not accidentally pass by clicking an unrelated song, while
                # the playback case can prove that the requested track was
                # actually invoked rather than merely entered in Search.
                result_name = (
                    "Dynamite"
                    if "dynamite" in self.state.query.casefold()
                    else self.state.query
                )
                controls.append(ControlInfo(
                    "Button", result_name, is_actionable=True,
                ))
            controls = tuple(controls)
        return WindowObservation(
            "observed",
            title=self.window().title,
            controls=controls,
        )


class SimulatedControl:
    def __init__(self, state: SimulatedState, observer: SimulatedObserver) -> None:
        self.state = state
        self.observer = observer

    def focus_window(self, target):
        return UIActionResult(
            "focused", "Focused the requested window.",
            window_title=self.observer.window().title,
            verified=True,
            evidence="The simulated foreground window matches.",
        )

    def type_text(self, target, control, text, *, element_id=""):
        self.state.type_attempts.append((str(control), str(text), str(element_id)))
        if self.state.surface == "notepad":
            # The real driver accepts either the fresh scan id or the exact
            # visible Document control name. The id is preferred, but the
            # model is also allowed to copy the observed name; rejecting it
            # here made this simulator stricter than WindowsUIControl.
            if element_id == "scan1-e0" or control == "텍스트 편집기":
                self.state.typed_text = str(text)
                return UIActionResult(
                    "typed", "Typed into the text editor.",
                    window_title=self.observer.window().title,
                    control_name="텍스트 편집기",
                    verified=True,
                    evidence=(
                        "The simulated document contains the requested text."
                    ),
                )
            return UIActionResult(
                "refused",
                "That control isn't a text field I can type into.",
                verified=False,
            )
        if self.state.surface != "spotify" or "search" not in control.casefold():
            return UIActionResult(
                "not_found", "The requested text field was not found.",
                verified=False,
            )
        self.state.query = str(text)
        return UIActionResult(
            "typed", "Typed into Search.",
            window_title=self.observer.window().title,
            control_name="Search",
            verified=True,
            evidence="The simulated Search value contains the requested text.",
        )

    def click_control(self, target, control, *, confirmed=False, element_id=""):
        normalized_control = control.casefold().strip()
        normalized_query = self.state.query.casefold().strip()
        if (
            self.state.surface == "spotify"
            and normalized_query
            and normalized_control == normalized_query
        ):
            self.state.clicked_controls.append(control)
            self.state.playing = "dynamite" in normalized_query
            return UIActionResult(
                "clicked", f"Clicked {control}.",
                window_title=self.observer.window().title,
                control_name=control,
                verified=True,
                evidence="The simulated playback state became active.",
            )
        return UIActionResult(
            "not_found", f"No control named {control} exists in this surface.",
            window_title=self.observer.window().title,
            control_name=control,
            verified=False,
        )

    def double_click_control(
        self, target, control, *, confirmed=False, element_id="",
    ):
        normalized_control = control.casefold().strip()
        normalized_query = self.state.query.casefold().strip()
        if (
            self.state.surface == "spotify"
            and normalized_query
            and normalized_control == normalized_query
        ):
            self.state.clicked_controls.append(control)
            self.state.playing = "dynamite" in normalized_query
            return UIActionResult(
                "clicked", f"Double-clicked {control}.",
                window_title=self.observer.window().title,
                control_name=control,
                verified=True,
                evidence="The simulated playback state became active.",
            )
        return UIActionResult(
            "not_found", f"No control named {control} exists in this surface.",
            window_title=self.observer.window().title,
            control_name=control,
            verified=False,
        )

    def select_option(self, target, control, option, *, element_id=""):
        return UIActionResult(
            "not_found", "No matching selection control exists.", verified=False,
        )

    def scroll_control(self, target, control, direction, *, element_id=""):
        return UIActionResult(
            "scrolled", "Scrolled the simulated view.", verified=True,
        )


class SimulatedComputerControl:
    def __init__(self, state: SimulatedState) -> None:
        self.state = state

    def open_app(self, target):
        self.state.opened_apps += 1
        return ComputerActionResult(
            "opened", str(target), str(target), f"Opened {target}.",
            operation="open_app",
        )


def planner_for(client, model, keep_alive, state):
    observer = SimulatedObserver(state)
    return DesktopActionPlanner(
        client=client,
        model=model,
        keep_alive=keep_alive,
        observer=observer,
        control=SimulatedControl(state, observer),
        computer_control=SimulatedComputerControl(state),
        response_language="en",
    )


def main() -> int:
    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    keep_alive = config.get(
        "llm", "ollama", "keep_alive", default=-1, required=False,
    )
    client = ollama.Client(host=config.get("llm", "ollama", "base_url"))
    checks = []

    search_state = SimulatedState("spotify")
    search_result = planner_for(client, model, keep_alive, search_state).act(
        "Find BTS using Spotify's search."
    )
    checks.append((
        "Spotify search",
        search_result.status == "done"
        and search_state.query.casefold() == "bts"
        and not search_state.playing
        and not search_state.clicked_controls,
        search_result,
    ))

    play_state = SimulatedState("spotify")
    play_result = planner_for(client, model, keep_alive, play_state).act(
        "Play Dynamite in Spotify for me."
    )
    checks.append((
        "Spotify playback goal",
        play_result.status == "done" and play_state.playing,
        play_result,
    ))

    notepad_state = SimulatedState("notepad")
    notepad_result = planner_for(client, model, keep_alive, notepad_state).act(
        "Write hello world in Notepad."
    )
    checks.append((
        "Notepad Document-role text entry",
        notepad_result.status == "done"
        and notepad_state.typed_text == "hello world",
        notepad_result,
    ))

    github_state = SimulatedState("github")
    github_observer = SimulatedObserver(github_state)
    github_result = planner_for(client, model, keep_alive, github_state).act(
        "Click Settings on this page.",
        surface_context=DesktopSurfaceContext.from_window_info(
            github_observer.window(),
            browser_page_cue=True,
            lock_to_surface=True,
        ),
    )
    checks.append((
        "GitHub scope isolation",
        github_result.status == "failed" and github_state.opened_apps == 0,
        github_result,
    ))

    failures = 0
    for name, passed, result in checks:
        failures += 0 if passed else 1
        print(
            f"[{status_label(passed)}] {name}: status={result.status} "
            f"rounds={result.model_rounds} actions={result.action_steps} "
            f"recovery={result.recovery_used} "
            f"failure={result.failure_code or '(none)'}"
        )
    print(f"{len(checks) - failures}/{len(checks)} live planner checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
