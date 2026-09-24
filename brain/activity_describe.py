"""What the person did, said back in the turn's language.

The activity recorder writes rows (memory/activity_log.py); this is the
sentence for each one, and the short list for a recording or a question
like "what did I do this morning?". Deterministic on purpose: the names in
these lines -- a button's label, a page's title, a time -- are exactly the
kind of thing a small model paraphrases into something that was not there.

Korean lines avoid a particle straight after a name the code did not write
(an app or a page title can end in anything): "크롬 창으로", "“검색” 입력란에",
never "크롬으로" guessed.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from memory.activity_log import Activity

ENGLISH = "en"
KOREAN = "ko"

# exe -> (English, Korean). Anything else is its executable's own name.
_APP_NAMES = {
    "chrome.exe": ("Chrome", "크롬"),
    "msedge.exe": ("Edge", "엣지"),
    "whale.exe": ("Whale", "웨일"),
    "firefox.exe": ("Firefox", "파이어폭스"),
    "brave.exe": ("Brave", "브레이브"),
    "explorer.exe": ("File Explorer", "파일 탐색기"),
    "systemsettings.exe": ("Settings", "설정"),
    "spotify.exe": ("Spotify", "스포티파이"),
    "code.exe": ("VS Code", "VS Code"),
    "discord.exe": ("Discord", "디스코드"),
    "kakaotalk.exe": ("KakaoTalk", "카카오톡"),
    "notepad.exe": ("Notepad", "메모장"),
    "winword.exe": ("Word", "워드"),
    "excel.exe": ("Excel", "엑셀"),
    "powerpnt.exe": ("PowerPoint", "파워포인트"),
    "steam.exe": ("Steam", "스팀"),
    "taskmgr.exe": ("Task Manager", "작업 관리자"),
    "windowsterminal.exe": ("Terminal", "터미널"),
    "cmd.exe": ("Command Prompt", "명령 프롬프트"),
    "mspaint.exe": ("Paint", "그림판"),
}
# Windows' host for store apps: the window's own title names the app.
_HOSTS = {"applicationframehost.exe"}

# Windows' own shell -- Start, Search, the taskbar. How the person got
# somewhere, not something they did (brain/replay_plan.py compresses it
# away). Live, it came out as "switched to SearchHost" and "clicked in
# StartMenuExperienceHost".
SHELL_PROCESSES = frozenset({
    "searchhost.exe", "searchapp.exe", "searchui.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "lockapp.exe", "shellhost.exe",
})
_SHELL_NAMES = {
    "searchhost.exe": ("Windows Search", "Windows 검색"),
    "searchapp.exe": ("Windows Search", "Windows 검색"),
    "searchui.exe": ("Windows Search", "Windows 검색"),
    "startmenuexperiencehost.exe": ("the Start menu", "시작 메뉴"),
    "shellexperiencehost.exe": ("the taskbar", "작업 표시줄"),
    "textinputhost.exe": ("the keyboard panel", "입력 패널"),
}


def is_shell(activity: Activity) -> bool:
    """A row about the Windows shell. The taskbar and the desktop are
    explorer.exe windows with no title; a File Explorer window has one."""
    exe = str(activity.app or "").casefold()
    if exe in SHELL_PROCESSES:
        return True
    return exe == "explorer.exe" and not str(activity.window or "").strip()


def app_name(activity: Activity, language: str = ENGLISH) -> str:
    exe = str(activity.app or "").strip()
    key = exe.casefold()
    if key in _SHELL_NAMES or (key == "explorer.exe" and not activity.window.strip()):
        english, korean = _SHELL_NAMES.get(key, ("the taskbar", "작업 표시줄"))
        return korean if language == KOREAN else english
    if key in _HOSTS and activity.window.strip():
        return _short(activity.window, 30)
    if key in _APP_NAMES:
        english, korean = _APP_NAMES[key]
        return korean if language == KOREAN else english
    if key.endswith(".exe"):
        exe = exe[:-4]
    return exe or ("앱" if language == KOREAN else "an app")


def clock(at: float) -> str:
    return datetime.fromtimestamp(float(at)).strftime("%H:%M")


def short_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return _short(url, 45)
    host = parts.netloc.removeprefix("www.")
    path = parts.path.rstrip("/")
    return _short(f"{host}{path}" if host else url, 45)


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def step_line(activity: Activity, language: str = ENGLISH) -> str:
    """One thing the person did, as a clause."""
    korean = language == KOREAN
    app = app_name(activity, language)
    target = _short(activity.target, 50)
    kind = activity.kind
    detail = activity.detail or {}

    if kind in ("click", "double_click", "right_click", "middle_click"):
        toggled = detail.get("toggled")
        # Only a real switch is "turned on". The taskbar's Start button
        # exposes the same toggle pattern, and came out "turned “시작” on".
        switch = (
            activity.target_type == "CheckBox"
            or "toggleswitch" in str(detail.get("class_name", "")).casefold()
        )
        if toggled in ("on", "off") and target and switch:
            if korean:
                return f"{app}에서 “{target}” {'켬' if toggled == 'on' else '끔'}"
            return f"turned “{target}” {toggled} in {app}"
        verb_en = {"click": "clicked", "double_click": "double-clicked",
                   "right_click": "right-clicked", "middle_click": "middle-clicked"}[kind]
        verb_ko = {"click": "클릭", "double_click": "더블클릭",
                   "right_click": "우클릭", "middle_click": "가운데 버튼 클릭"}[kind]
        if korean:
            return f"{app}에서 “{target}” {verb_ko}" if target else f"{app} 창 {verb_ko}"
        return f"{verb_en} “{target}” in {app}" if target else f"{verb_en} in {app}"

    if kind == "type":
        text = _short(activity.text, 60)
        submitted = detail.get("ended_by") == "Enter"
        if detail.get("password"):
            return (f"{app}에서 비밀번호 입력 (내용은 저장하지 않음)" if korean
                    else f"typed a password in {app} (not saved)")
        if korean:
            place = f"“{target}” 입력란에" if target else f"{app}에서"
            said = f"{place} “{text}” 입력" if text else f"{place} 입력"
            return said + (" 후 Enter" if submitted else "")
        place = f"into “{target}”" if target else f"in {app}"
        said = f"typed “{text}” {place}" if text else f"typed {place}"
        return said + (" and pressed Enter" if submitted else "")

    if kind == "key":
        return f"{app}에서 {activity.text} 누름" if korean else f"pressed {activity.text} in {app}"

    if kind == "scroll":
        down = int(detail.get("delta", 0) or 0) < 0
        if korean:
            return f"{app}에서 {'아래로' if down else '위로'} 스크롤"
        return f"scrolled {'down' if down else 'up'} in {app}"

    if kind == "switch":
        title = _short(activity.window, 50)
        if korean:
            return f"{app} 창으로 전환 — {title}" if title else f"{app} 창으로 전환"
        return f"switched to {app} — {title}" if title else f"switched to {app}"

    if kind == "page":
        where = short_url(activity.url) if activity.url else ""
        title = target or _short(activity.window, 50)
        if korean:
            if title and where:
                return f"“{title}” 페이지 열람 ({where})"
            return f"“{title or where}” 페이지 열람"
        if title and where:
            return f"opened “{title}” ({where})"
        return f"opened {where or f'“{title}”'}"

    if kind == "view":
        return f"{app}: “{_short(activity.window, 50)}”"

    if kind == "fullscreen":
        until = detail.get("until")
        if korean:
            return (f"{app} 전체 화면 사용 ({clock(until)}까지)" if until
                    else f"{app} 전체 화면 사용")
        return (f"used {app} full screen until {clock(until)}" if until
                else f"used {app} full screen")

    if kind == "private":
        return f"{app} 시크릿 창 사용" if korean else f"used a private {app} window"

    if kind == "start":
        title = _short(activity.window, 50)
        if korean:
            return f"시작 위치: {app} — {title}" if title else f"시작 위치: {app}"
        return f"started in {app} — {title}" if title else f"started in {app}"

    return f"{app}: {kind}"


def _collapse(activities: list[Activity], language: str) -> list[tuple[Activity, str, int]]:
    """Consecutive identical lines become one, counted."""
    out: list[tuple[Activity, str, int]] = []
    for activity in activities:
        line = step_line(activity, language)
        if out and out[-1][1] == line:
            first, _, count = out[-1]
            out[-1] = (first, line, count + 1)
        else:
            out.append((activity, line, 1))
    return out


def steps_text(activities: list[Activity], language: str = ENGLISH, *, limit: int = 8) -> str:
    """A recording's steps, numbered, in one line she can say."""
    collapsed = _collapse(
        [a for a in activities if a.kind != "start"] or list(activities), language,
    )
    parts = []
    for number, (_, line, count) in enumerate(collapsed[:limit], start=1):
        parts.append(f"{number}) {line}" + (f" ×{count}" if count > 1 else ""))
    more = len(collapsed) - limit
    if more > 0:
        parts.append(f"외 {more}단계" if language == KOREAN else f"and {more} more")
    return ", ".join(parts)


def step_count(activities: list[Activity]) -> int:
    return len(_collapse([a for a in activities if a.kind != "start"], ENGLISH))


def timeline_text(
    activities: list[Activity], language: str = ENGLISH, *, limit: int = 10,
) -> tuple[str, int]:
    """The log, as "09:41 clicked …; 09:43 opened …". Returns the text and
    how many collapsed lines did not fit."""
    collapsed = _collapse(activities, language)
    shown = collapsed[-limit:]
    parts = [
        f"{clock(activity.at)} {line}" + (f" ×{count}" if count > 1 else "")
        for activity, line, count in shown
    ]
    return "; ".join(parts), max(0, len(collapsed) - len(shown))


_WHEN = {
    "today": ("today", "오늘"),
    "yesterday": ("yesterday", "어제"),
    "this_morning": ("this morning", "오늘 오전에"),
    "this_afternoon": ("this afternoon", "오늘 오후에"),
    "this_evening": ("this evening", "오늘 저녁에"),
    "just_now": ("in the last few minutes", "방금 전에"),
    "earlier": ("in the last few hours", "최근 몇 시간 동안"),
    "recently": ("recently", "최근에"),
}


def when_phrase(label: str, language: str = ENGLISH) -> str:
    korean = language == KOREAN
    if label.startswith("minutes:"):
        n = label.split(":", 1)[1]
        return f"지난 {n}분 동안" if korean else f"in the last {n} minutes"
    if label.startswith("hours:"):
        n = label.split(":", 1)[1]
        if korean:
            return f"지난 {n}시간 동안"
        return "in the last hour" if n == "1" else f"in the last {n} hours"
    english, korean_phrase = _WHEN.get(label, _WHEN["recently"])
    return korean_phrase if korean else english
