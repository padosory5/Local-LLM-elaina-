"""Doing again what the person did (brain/replay_plan.py says what).

Every step is found again on the live screen, never replayed from a
remembered position: an app by its process, a control by its name inside
that app's window, a page by its address. It is the rule the desktop
driver already follows for everything she does, for the same reason --
windows move, and a remembered point clicks whatever is there now.

It stops the moment the person touches the mouse or keyboard (their input
is told from hers by the input watcher) and at the first step that cannot
be done, and says which.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from brain.replay_plan import ReplayStep

# Store apps share one host process; their window title names them.
_HOSTED = frozenset({"applicationframehost.exe", "systemsettings.exe"})


def _process_name(pid: int | None) -> str:
    if not pid:
        return ""
    try:
        import psutil

        return psutil.Process(int(pid)).name()
    except Exception:
        return ""


@dataclass
class ReplayOutcome:
    done: list[ReplayStep] = field(default_factory=list)
    failed: ReplayStep | None = None
    reason: str = ""
    interrupted: bool = False

    @property
    def finished(self) -> bool:
        return self.failed is None and not self.interrupted


class ReplayRunner:
    SETTLE_SECONDS = 0.8
    APP_WAIT_SECONDS = 12.0

    def __init__(
        self,
        *,
        computer_control: Any,
        desktop_control: Any,
        browser_service: Any,
        observer: Any,
        input_watcher: Any = None,
        sleep=time.sleep,
        clock=time.monotonic,
        process_name=_process_name,
    ) -> None:
        self.computer_control = computer_control
        self.desktop_control = desktop_control
        self.browser_service = browser_service
        self.observer = observer
        self.input_watcher = input_watcher
        self._sleep = sleep
        self._clock = clock
        self._process_name = process_name

    def run(self, steps: list[ReplayStep]) -> ReplayOutcome:
        outcome = ReplayOutcome()
        watcher = self.input_watcher
        mark = (
            watcher.mark()
            if watcher is not None and getattr(watcher, "available", False)
            else None
        )
        for step in steps:
            if mark is not None and watcher.user_input_since(mark):
                outcome.failed, outcome.interrupted = step, True
                return outcome
            try:
                ok, reason = self._do(step)
            except Exception as error:
                ok, reason = False, f"{type(error).__name__}: {error}"
            if not ok:
                outcome.failed, outcome.reason = step, str(reason or "")
                return outcome
            outcome.done.append(step)
            self._sleep(self.SETTLE_SECONDS)
        return outcome

    # --------------------------------------------------------------- steps

    def _do(self, step: ReplayStep) -> tuple[bool, str]:
        if step.action in ("open_app", "focus_app"):
            return self._bring_up(step)
        if step.action == "open_url":
            result = self.browser_service.open_url(step.target)
            return bool(getattr(result, "succeeded", False)), str(getattr(result, "message", ""))
        window = self._window_for(step)
        if window is None:
            ok, reason = self._bring_up(step)
            if not ok:
                return ok, reason
            window = self._window_for(step)
            if window is None:
                return False, f"{step.app_en} is not open"
        control = self.desktop_control
        if step.action == "click":
            result = control.click_control(window, step.target, confirmed=step.risky)
        elif step.action == "double_click":
            result = control.double_click_control(window, step.target, confirmed=step.risky)
        elif step.action == "type":
            result = control.type_text(
                window, step.target, step.text, confirmed=step.risky, submit=step.submit,
            )
        elif step.action == "press":
            keys = [part.strip().lower() for part in step.target.split("+") if part.strip()]
            result = control.press_key(window, *keys)
        else:
            return False, f"no way to repeat {step.action!r}"
        return bool(getattr(result, "succeeded", False)), str(getattr(result, "message", ""))

    def _bring_up(self, step: ReplayStep) -> tuple[bool, str]:
        window = self._window_for(step)
        if window is not None:
            result = self.desktop_control.focus_window(window)
            return bool(getattr(result, "succeeded", False)), str(getattr(result, "message", ""))
        message = ""
        stem = step.app[:-4] if step.app.casefold().endswith(".exe") else step.app
        for name in dict.fromkeys(n for n in (step.app_en, step.app_ko, stem) if n):
            result = self.computer_control.open_app(name)
            message = str(getattr(result, "message", ""))
            if getattr(result, "succeeded", False):
                break
        else:
            return False, message or f"I couldn't open {step.app_en}"
        deadline = self._clock() + self.APP_WAIT_SECONDS
        while self._clock() < deadline:
            window = self._window_for(step)
            if window is not None:
                self.desktop_control.focus_window(window)
                return True, ""
            self._sleep(0.5)
        return False, f"{step.app_en} did not open"

    def _window_for(self, step: ReplayStep) -> Any:
        exe = step.app.casefold()
        try:
            windows = list(self.observer.list_windows())
        except Exception:
            return None
        candidates = []
        for window in windows:
            title = str(getattr(window, "title", "") or "")
            if exe in _HOSTED:
                if title and (title == step.window or title in (step.app_en, step.app_ko)):
                    candidates.append(window)
            elif self._process_name(getattr(window, "process_id", None)).casefold() == exe:
                candidates.append(window)
        if not candidates:
            return None
        for window in candidates:
            if step.window and getattr(window, "title", "") == step.window:
                return window
        return candidates[0]
