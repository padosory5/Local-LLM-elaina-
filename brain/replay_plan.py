"""From what the person did to what she can do again.

The activity log records every click and page -- including the ones that
were only the way there. Live, "open Settings, then System, then Display"
came out as seven rows: Windows Search, the taskbar's Start button, a
switch to Settings, a click in the Start menu, 시스템, 디스플레이. Repeating
those literally would press Start and type into a search box that is no
longer on screen.

So a run of activity is first turned into what a person would say they
did -- "open Settings, click 시스템, click 디스플레이" -- and only that is
repeated:

* the Windows shell (Start, Search, the taskbar) is how they got somewhere,
  not something they did; a window reached through it is *opened*;
* a page is its address, opened again;
* a click is the control's name, found again in the live window -- never a
  screen position, which moves when a window does;
* typing is repeated only when its text is known (kept in memory for a few
  hours, never on disk), and never a password;
* a step that commits something (delete, send, buy, Enter in a messenger)
  is marked risky, and said so before anything is done.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from brain import activity_describe
from memory.activity_log import Activity

ENGLISH = "en"
KOREAN = "ko"

# Windows' own shell: activity_describe.is_shell.
is_shell = activity_describe.is_shell
# Enter in these sends something to someone.
MESSAGING = frozenset({
    "kakaotalk.exe", "discord.exe", "slack.exe", "teams.exe", "ms-teams.exe",
    "telegram.exe", "whatsapp.exe", "line.exe", "outlook.exe", "olk.exe",
})
_MOUSE_KEYS = {"Mouse Back": "Alt+Left", "Mouse Forward": "Alt+Right"}


def _committing(name: str) -> bool:
    try:
        from tools.computer_control.windows_ui_control import is_committing_control
    except Exception:  # pragma: no cover - import failure on odd hosts
        return False
    return is_committing_control(name)


@dataclass
class ReplayStep:
    action: str            # open_app, focus_app, open_url, click, double_click, type, press
    app: str               # the executable
    app_en: str
    app_ko: str
    window: str = ""       # the window's title at the time
    target: str = ""       # control or field name, address, or key chord
    text: str = ""         # what to type
    submit: bool = False   # Enter after typing
    risky: bool = False
    at: float = 0.0

    def app_label(self, language: str = ENGLISH) -> str:
        return self.app_ko if language == KOREAN else self.app_en

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in (
            "action", "app", "app_en", "app_ko", "window", "target", "text",
            "submit", "risky", "at",
        ) if getattr(self, name) or name in ("action", "app")}

    @classmethod
    def from_dict(cls, data: dict) -> "ReplayStep":
        known = {k: data[k] for k in (
            "action", "app", "app_en", "app_ko", "window", "target", "text",
            "submit", "risky", "at",
        ) if k in data}
        known.setdefault("app_en", known.get("app", ""))
        known.setdefault("app_ko", known.get("app_en", ""))
        return cls(**known)


@dataclass
class ReplayPlan:
    steps: list[ReplayStep] = field(default_factory=list)
    # Why something they did will not be repeated, one reason once.
    skipped: list[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        if reason not in self.skipped:
            self.skipped.append(reason)

    @property
    def risky(self) -> list[ReplayStep]:
        return [step for step in self.steps if step.risky]


@dataclass
class PendingReplay:
    """Steps she listed and asked about, waiting for a yes."""

    steps: list[ReplayStep]
    seconds: float = 180.0
    created: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > self.seconds


def _step(action: str, activity: Activity, **values) -> ReplayStep:
    return ReplayStep(
        action=action, app=activity.app,
        app_en=activity_describe.app_name(activity, ENGLISH),
        app_ko=activity_describe.app_name(activity, KOREAN),
        window=activity.window, at=activity.at, **values,
    )


def build(activities: list[Activity]) -> ReplayPlan:
    """What of this run of activity can be done again, in order."""
    plan = ReplayPlan()
    through_shell = False
    for activity in activities:
        kind = activity.kind
        if is_shell(activity):
            through_shell = True
            continue
        if kind == "switch":
            plan.steps.append(_step("open_app" if through_shell else "focus_app", activity))
            through_shell = False
            continue
        through_shell = False
        detail = activity.detail or {}
        if kind == "page":
            if activity.url:
                plan.steps.append(_step("open_url", activity, target=activity.url))
        elif kind in ("click", "double_click"):
            if activity.target:
                plan.steps.append(_step(
                    kind, activity, target=activity.target,
                    risky=_committing(activity.target),
                ))
            else:
                plan.skip("unnamed_click")
        elif kind == "type":
            if detail.get("password"):
                plan.skip("password")
            elif activity.text and activity.target:
                submit = detail.get("ended_by") == "Enter"
                plan.steps.append(_step(
                    "type", activity, target=activity.target, text=activity.text,
                    submit=submit,
                    risky=submit and activity.app.casefold() in MESSAGING,
                ))
            else:
                plan.skip("typed_text_unknown")
        elif kind == "key":
            chord = _MOUSE_KEYS.get(activity.text, activity.text)
            if chord:
                plan.steps.append(_step("press", activity, target=chord))
        elif kind in ("right_click", "middle_click"):
            plan.skip("right_click")
        elif kind == "fullscreen":
            plan.skip("fullscreen")
        elif kind == "scroll":
            plan.skip("scroll")
        # view, private and start rows are context, not actions.
    plan.steps = _tidy(plan.steps)
    return plan


def _tidy(steps: list[ReplayStep]) -> list[ReplayStep]:
    """Passing through windows is not going to them: of a run of switches,
    only the one they stayed in counts; the same page twice is once."""
    out: list[ReplayStep] = []
    for step in steps:
        if out:
            last = out[-1]
            if last.action == "focus_app" and step.action in ("focus_app", "open_app"):
                out[-1] = step
                continue
            if last.action == "open_app" and step.action == "focus_app" and last.app == step.app:
                continue
            if last.action == step.action == "open_url" and last.target == step.target:
                continue
        out.append(step)
    # A plan that ends by merely switching somewhere did nothing there.
    while out and out[-1].action == "focus_app" and len(out) > 1:
        out.pop()
    return out


def last(plan: ReplayPlan, count: int) -> ReplayPlan:
    """The last ``count`` things they did."""
    if count <= 0 or count >= len(plan.steps):
        return plan
    return ReplayPlan(steps=plan.steps[-count:], skipped=list(plan.skipped))


# --------------------------------------------------------------- wording


def step_text(step: ReplayStep, language: str = ENGLISH) -> str:
    korean = language == KOREAN
    app = step.app_label(language)
    target = activity_describe._short(step.target, 50)
    if step.action == "open_app":
        line = f"{app} 열기" if korean else f"open {app}"
    elif step.action == "focus_app":
        line = f"{app} 창으로 이동" if korean else f"go to {app}"
    elif step.action == "open_url":
        where = activity_describe.short_url(step.target)
        line = f"{where} 열기" if korean else f"open {where}"
    elif step.action in ("click", "double_click"):
        if korean:
            verb = "더블클릭" if step.action == "double_click" else "클릭"
            line = f"{app}에서 “{target}” {verb}"
        else:
            verb = "double-click" if step.action == "double_click" else "click"
            line = f"{verb} “{target}” in {app}"
    elif step.action == "type":
        text = activity_describe._short(step.text, 50)
        if korean:
            line = f"“{target}” 입력란에 “{text}” 입력" + (" 후 Enter" if step.submit else "")
        else:
            line = f"type “{text}” into “{target}”" + (" and press Enter" if step.submit else "")
    elif step.action == "press":
        line = f"{app}에서 {step.target} 누르기" if korean else f"press {step.target} in {app}"
    else:
        line = step.action
    if step.risky:
        line += " (되돌릴 수 없음)" if korean else " (can't be undone)"
    return line


def steps_text(steps: list[ReplayStep], language: str = ENGLISH, *, limit: int = 8) -> str:
    parts = [f"{n}) {step_text(step, language)}" for n, step in enumerate(steps[:limit], 1)]
    more = len(steps) - limit
    if more > 0:
        parts.append(f"외 {more}단계" if language == KOREAN else f"and {more} more")
    return ", ".join(parts)


_SKIPPED = {
    "unnamed_click": ("a click on something with no name", "이름 없는 곳 클릭"),
    "typed_text_unknown": ("typing from more than a few hours ago (the text isn't kept)",
                           "몇 시간 전에 입력한 내용(글자는 저장하지 않음)"),
    "password": ("a password", "비밀번호 입력"),
    "right_click": ("right-click menus", "우클릭 메뉴"),
    "fullscreen": ("what happened inside a full-screen app", "전체 화면 앱 안에서 한 일"),
    "scroll": ("scrolling", "스크롤"),
}


def skipped_text(reasons: list[str], language: str = ENGLISH) -> str:
    index = 1 if language == KOREAN else 0
    return ", ".join(_SKIPPED[reason][index] for reason in reasons if reason in _SKIPPED)
