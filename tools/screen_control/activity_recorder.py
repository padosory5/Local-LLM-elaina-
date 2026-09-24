"""Watch what the person does on the machine and write it down.

The input watcher (input_watcher.py) already sees every click and key the
person makes -- that is how Elaina tells their input from her own. This
turns those events into things a person would say they did: "clicked
“Bluetooth” in Settings, and it went on", "opened youtube.com — “lofi hip
hop radio”", "typed in “Search”", "pressed Ctrl+T in Chrome".

Everything it records goes to the activity log (memory/activity_log.py);
inside a recording the person started ("record this" ... "stop recording")
it is also kept as the recording's steps (memory/routines.py), with what a
replay would need: the control's automation id and rectangle, the point in
it, the window it was in.

How it stays out of the way:

* **Nothing slow runs in the hook.** The hook appends a click or key to a
  queue (InputWatcher.activity_feed) and returns. This thread drains it;
  naming the control under a click is a cross-process UI Automation call
  and costs what it costs here, not in the desktop's input path.
* **Mouse moves are never queued**, so a game's constant movement adds
  nothing. A full-screen window (almost always a game or a video) is not
  looked into at all -- one row says it was used, and until when.
* **Typing is read once, as a result.** A keystroke only starts or extends
  a burst; the field is read when the person pauses. That is also what
  makes Korean work: the hook sees the keys the IME composes from, never
  the Hangul, and the field has the Hangul.

What it never records: the text of a password field (it is never read),
anything in a private browsing window beyond that one was used, and
Elaina's own window. Typed text is kept only inside a recording unless
``activity.typed_text`` says otherwise.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

from memory.activity_log import Activity, ActivityLog

# ------------------------------------------------------------------ keys

_KEY_UP_MESSAGES = (0x101, 0x105)
_MODIFIERS = {
    0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift",
    0x11: "Ctrl", 0xA2: "Ctrl", 0xA3: "Ctrl",
    0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt",
    0x5B: "Win", 0x5C: "Win",
}
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Win")
_NAMED_KEYS = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc",
    0x20: "Space", 0x21: "PageUp", 0x22: "PageDown", 0x23: "End",
    0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    **{0x70 + number: f"F{number + 1}" for number in range(24)},
}
_OEM_KEYS = {
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
}
# The Korean/English toggle and the IME's own keys: part of typing, never
# a thing the person did on their own.
_IME_KEYS = frozenset({0x15, 0x19, 0xE5})
_ENDS_TYPING = frozenset({"Enter", "Tab", "Esc"})
_EDITING = frozenset({"Backspace", "Delete", "Space", "Left", "Right", "Home", "End"})
_NAVIGATION = frozenset({"Up", "Down", "Left", "Right", "PageUp", "PageDown", "Home", "End"})
# Recorded in the log on their own; arrows and paging only inside a
# recording, where a replay needs them and a summary does not.
_STANDALONE = frozenset(
    {"Enter", "Esc", "Delete", "PrintScreen", "Insert"}
    | {f"F{number}" for number in range(1, 25)}
)


def key_name(vk: int) -> str:
    if vk in _NAMED_KEYS:
        return _NAMED_KEYS[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return f"Num{vk - 0x60}"
    if vk in _OEM_KEYS:
        return _OEM_KEYS[vk]
    return f"VK{vk:02X}"


def _types_a_character(vk: int) -> bool:
    return (
        0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A or 0x60 <= vk <= 0x6F
        or vk in _OEM_KEYS or vk == 0xE2
    )


# -------------------------------------------------------------- controls

# What a click is *on*: the nearest of these above the element under the
# pointer. Chromium exposes web links and buttons with these types too.
_ACTIONABLE = frozenset({
    "Button", "SplitButton", "MenuItem", "Hyperlink", "ListItem", "TabItem",
    "CheckBox", "RadioButton", "ComboBox", "Edit", "TreeItem", "DataItem",
    "Slider", "Spinner", "HeaderItem", "MenuBar",
})
_EDITABLE = frozenset({"Edit", "Document", "ComboBox"})


def pick_target(chain: list[tuple[str, str]]) -> tuple[int, str]:
    """Which element in a pointer's ancestor chain the click was on.

    ``chain`` is (control type, name) from the element under the pointer
    upwards. The nearest actionable element wins; when it has no name of
    its own, the name of what was under the pointer names it (a Button
    whose label is a Text inside it). With nothing actionable nearby, the
    deepest named element.
    """
    for index, (control_type, name) in enumerate(chain[:5]):
        if control_type in _ACTIONABLE:
            label = name or next((n for _, n in chain[:index] if n), "")
            return index, label
    for index, (_, name) in enumerate(chain[:3]):
        if name:
            return index, name
    return 0, ""


@dataclass
class WindowInfo:
    handle: int
    title: str
    process_name: str
    pid: int
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    fullscreen: bool = False


@dataclass
class ControlInfo:
    name: str
    control_type: str
    automation_id: str = ""
    class_name: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    is_password: bool = False
    toggle: str | None = None
    ancestors: tuple[tuple[str, str], ...] = ()
    element: Any = None

    @property
    def editable(self) -> bool:
        return self.control_type in _EDITABLE


# Private browsing: that it was used is recorded, nothing in it is.
_PRIVATE_MARKERS = (
    "inprivate", "incognito", "private browsing", "시크릿", "개인 정보 보호",
)


def page_title(window_title: str) -> str:
    """A browser window's title without the browser's own suffix."""
    title = str(window_title or "").strip()
    head, separator, tail = title.rpartition(" - ")
    if separator and head and len(tail) <= 30:
        return head.strip()
    return title


def _is_browser(process_name: str) -> bool:
    try:
        from tools.screen_browser.browser_window import executable_is_browser
    except Exception:  # pragma: no cover - import failure on odd hosts
        return False
    return executable_is_browser(process_name)


# ------------------------------------------------------------- recorder


@dataclass
class _Typing:
    started: float
    last_key: float
    window: WindowInfo
    control: ControlInfo | None
    typing: bool
    keys: int = 0
    value: str = ""
    read_for: float = -1.0


@dataclass
class Recording:
    id: str
    name: str
    said: str
    started: float
    steps: list[Activity] = field(default_factory=list)


class ActivityRecorder:
    POLL_SECONDS = 0.05
    FOREGROUND_SECONDS = 0.5
    READ_AFTER_PAUSE = 0.4
    TYPING_DONE = 1.5
    TITLE_SETTLE = 1.0
    TOGGLE_REREAD = 0.5
    DOUBLE_CLICK_SECONDS = 0.5
    DOUBLE_CLICK_PIXELS = 4
    SCROLL_DONE = 1.0
    STUCK_MODIFIER_SECONDS = 10.0

    def __init__(
        self,
        *,
        watcher: Any,
        log: ActivityLog | None,
        resolver: Any = None,
        clock=time.time,
        typed_text: str = "recordings_only",
        skip_apps: tuple[str, ...] = (),
        own_process_names: tuple[str, ...] = ("electron.exe",),
        own_pids: tuple[int, ...] | None = None,
    ) -> None:
        self.watcher = watcher
        self.log = log
        self.resolver = resolver
        self._clock = clock
        # memory_only (the default): what they typed is kept in memory for
        # a few hours so "repeat what I just did" can type it again, and the
        # log on disk says only "typed in <field>". "recordings_only" is
        # the earlier name of the same setting.
        typed_text = "memory_only" if typed_text == "recordings_only" else typed_text
        self.typed_text = (
            typed_text if typed_text in {"memory_only", "always", "never"}
            else "memory_only"
        )
        self.skip_apps = {str(app).casefold() for app in skip_apps}
        self.own_process_names = {str(name).casefold() for name in own_process_names}
        self.own_pids = set(own_pids if own_pids is not None else (os.getpid(),))
        self.paused = False
        self.error = ""
        self._feed = None
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._flush_requested = threading.Event()
        self._flushed = threading.Event()
        self._recording: Recording | None = None
        self._needs_start_step = False
        self._typing: _Typing | None = None
        self._held_click: tuple[Activity, float, tuple[int, int], float] | None = None
        self._toggles: list[tuple[float, Activity, ControlInfo]] = []
        self._scroll: tuple[Activity, float] | None = None
        self._modifiers: dict[str, float] = {}
        self._foreground: WindowInfo | None = None
        self._foreground_polled = -1e9
        self._pending_title: tuple[str, float] | None = None
        self._last_url: dict[int, str] = {}
        self._fullscreen_row = 0
        self._failures: set[str] = set()
        # Everything done lately, as it was done -- typed text included --
        # in memory only, for "repeat my last five actions".
        self.recent: deque[Activity] = deque(maxlen=500)
        self.started_at = self._clock()

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        if self.running:
            return True
        if not getattr(self.watcher, "available", False):
            self.error = "input watching is not available"
            return False
        self._feed = self.watcher.activity_feed()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="elaina-activity", daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None
        if self._fullscreen_row and self.log is not None:
            try:
                self.log.extend(self._fullscreen_row, until=round(self._clock(), 3))
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            import comtypes

            # UI Automation from a thread of our own needs its own COM
            # apartment; the main thread's is not ours to use.
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            pass
        if self.resolver is None:
            try:
                self.resolver = ScreenResolver()
            except Exception as error:
                self.error = f"cannot read the screen: {type(error).__name__}: {error}"
                print(f"[Activity] {self.error}")
                return
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as error:  # never let one odd window stop it
                key = type(error).__name__
                if key not in self._failures:
                    self._failures.add(key)
                    print(f"[Activity] {key}: {error}")
            self._stop.wait(self.POLL_SECONDS)

    # ----------------------------------------------------------- recording

    @property
    def recording(self) -> Recording | None:
        return self._recording

    def start_recording(self, name: str = "", said: str = "") -> Recording:
        with self._lock:
            self._recording = Recording(
                id=f"rec-{int(self._clock() * 1000)}", name=str(name or "").strip(),
                said=str(said or ""), started=self._clock(),
            )
            self._needs_start_step = True
            return self._recording

    def stop_recording(self) -> Recording | None:
        """End the recording, with whatever the worker was still holding (a
        click waiting to see if it becomes a double click, a typing burst)."""
        self._settle_now()
        with self._lock:
            recording, self._recording = self._recording, None
            self._needs_start_step = False
        return recording

    def cancel_recording(self) -> Recording | None:
        with self._lock:
            recording, self._recording = self._recording, None
            self._needs_start_step = False
        return recording

    def _settle_now(self) -> None:
        if self.running:
            self._flushed.clear()
            self._flush_requested.set()
            self._flushed.wait(1.0)
        else:
            self._flush_everything(self._clock())

    # ------------------------------------------------------------ the loop

    def step(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        if self._needs_start_step:
            self._start_step(now)
        self._drain(now)
        if now - self._foreground_polled >= self.FOREGROUND_SECONDS:
            self._foreground_polled = now
            self._poll_foreground(now)
        self._settle(now)
        if self._flush_requested.is_set():
            self._flush_requested.clear()
            # Drained again: a click queued while this step was already past
            # the drain above belongs to the recording being stopped.
            self._drain(now)
            self._flush_everything(now)
            self._flushed.set()

    def _drain(self, now: float) -> None:
        feed = self._feed
        while feed:
            try:
                event = feed.popleft()
            except IndexError:
                break
            self._event(event, now)

    def _event(self, event: Any, now: float) -> None:
        if event.kind == "mouse":
            if event.what in ("left_down", "right_down", "middle_down"):
                self._mouse_down(event, now)
            elif event.what in ("wheel", "hwheel"):
                self._wheel(event, now)
            elif event.what == "x_down":
                self._named_key("Mouse Back" if event.extra == 1 else "Mouse Forward", now)
            return
        what = str(event.what or "")
        if not what.startswith("vk_0x"):
            return
        vk = int(what[5:], 16)
        if event.extra in _KEY_UP_MESSAGES:
            if vk in _MODIFIERS:
                self._modifiers.pop(_MODIFIERS[vk], None)
            return
        if vk in _MODIFIERS:
            self._modifiers[_MODIFIERS[vk]] = now
            return
        self._key_down(vk, now)

    # --------------------------------------------------------------- mouse

    def _mouse_down(self, event: Any, now: float) -> None:
        kind = {"left_down": "click", "right_down": "right_click",
                "middle_down": "middle_click"}[event.what]
        point = (int(event.x), int(event.y))
        # A click ends a typing burst, with the value read before it moved
        # the focus.
        self._flush_typing(now)
        self._flush_scroll()
        if kind == "click" and self._held_click is not None:
            held, _, held_point, held_at = self._held_click
            if (
                float(event.at) - held_at <= self.DOUBLE_CLICK_SECONDS
                and abs(point[0] - held_point[0]) <= self.DOUBLE_CLICK_PIXELS
                and abs(point[1] - held_point[1]) <= self.DOUBLE_CLICK_PIXELS
            ):
                self._held_click = None
                held.kind = "double_click"
                self._commit(held)
                return
        self._release_held_click()
        window = self.resolver.window_at(*point)
        if not self._watchable(window):
            return
        control = self.resolver.control_at(*point)
        activity = Activity(
            at=now, kind=kind, app=window.process_name, window=window.title,
            target=control.name if control else "",
            target_type=control.control_type if control else "",
            detail=_click_detail(point, window, control),
        )
        if control is not None and control.toggle is not None:
            self._toggles.append((now + self.TOGGLE_REREAD, activity, control))
        if kind == "click":
            self._held_click = (activity, now, point, float(event.at))
        else:
            self._commit(activity)

    def _release_held_click(self) -> None:
        if self._held_click is not None:
            activity = self._held_click[0]
            self._held_click = None
            self._commit(activity)

    def _wheel(self, event: Any, now: float) -> None:
        if self._recording is None:
            return
        window = self.resolver.window_at(int(event.x), int(event.y))
        if not self._watchable(window):
            return
        if (
            self._scroll is not None
            and self._scroll[0].detail.get("handle") == window.handle
            and now - self._scroll[1] < self.SCROLL_DONE
        ):
            self._scroll[0].detail["delta"] += int(event.extra)
            self._scroll = (self._scroll[0], now)
            return
        self._flush_scroll()
        self._scroll = (Activity(
            at=now, kind="scroll", app=window.process_name, window=window.title,
            detail={"delta": int(event.extra), "point": [int(event.x), int(event.y)],
                    "horizontal": event.what == "hwheel", "handle": window.handle},
        ), now)

    def _flush_scroll(self) -> None:
        if self._scroll is not None:
            activity = self._scroll[0]
            self._scroll = None
            self._commit(activity, to_log=False)

    # ---------------------------------------------------------------- keys

    def _held_modifiers(self, now: float) -> list[str]:
        for name, since in list(self._modifiers.items()):
            if now - since > self.STUCK_MODIFIER_SECONDS:
                # A release the hook never saw (Win+L locks before it).
                self._modifiers.pop(name, None)
        return [name for name in _MODIFIER_ORDER if name in self._modifiers]

    def _key_down(self, vk: int, now: float) -> None:
        if vk in _IME_KEYS:
            return
        name = key_name(vk)
        held = self._held_modifiers(now)
        if any(modifier in held for modifier in ("Ctrl", "Alt", "Win")):
            self._flush_typing(now)
            window = self.resolver.foreground()
            if self._watchable(window):
                self._commit(Activity(
                    at=now, kind="key", app=window.process_name,
                    window=window.title, text="+".join([*held, name]),
                    detail={"handle": window.handle},
                ))
            return
        if name in _ENDS_TYPING and self._typing is not None:
            self._flush_typing(now, ended_by=name)
            return
        if _types_a_character(vk) or (name in _EDITING and self._typing is not None):
            self._keystroke(now)
            return
        self._named_key(name, now)

    def _named_key(self, name: str, now: float) -> None:
        in_log = name in _STANDALONE or name.startswith("Mouse ")
        if not in_log and not (name in _NAVIGATION and self._recording is not None):
            return
        window = self.resolver.foreground()
        if not self._watchable(window):
            return
        self._commit(Activity(
            at=now, kind="key", app=window.process_name, window=window.title,
            text=name, detail={"handle": window.handle},
        ), to_log=in_log)

    def _keystroke(self, now: float) -> None:
        typing = self._typing
        if typing is None:
            window = self.resolver.foreground()
            if not self._watchable(window):
                return
            control = self.resolver.focused_control()
            typing = self._typing = _Typing(
                started=now, last_key=now, window=window, control=control,
                # Keys pressed where there is no text field -- a game, a
                # list, a video's shortcuts -- are not typing.
                typing=bool(control is not None and control.editable),
            )
        typing.keys += 1
        typing.last_key = now

    def _may_keep_text(self) -> bool:
        return self.typed_text != "never"

    RECENT_SECONDS = 3 * 3600.0

    def recent_activities(self, *, within: float | None = None) -> list[Activity]:
        """What they did lately, as it was done (typed text included), oldest
        first. Held in memory only; gone when she is closed."""
        cutoff = self._clock() - (self.RECENT_SECONDS if within is None else within)
        with self._lock:
            return [activity for activity in self.recent if activity.at >= cutoff]

    def _read_typed(self, typing: _Typing) -> None:
        control = typing.control
        if control is None or control.is_password or not self._may_keep_text():
            return
        typing.value = self.resolver.read_value(control)
        typing.read_for = typing.last_key

    def _flush_typing(self, now: float, *, ended_by: str = "") -> None:
        typing, self._typing = self._typing, None
        if typing is None or not typing.typing or typing.keys == 0:
            return
        if typing.read_for != typing.last_key:
            self._read_typed(typing)
        control = typing.control
        password = bool(control is not None and control.is_password)
        detail: dict[str, Any] = {
            "keys": typing.keys, "handle": typing.window.handle,
        }
        if password:
            detail["password"] = True
        if ended_by:
            detail["ended_by"] = ended_by
        if control is not None:
            if control.automation_id:
                detail["automation_id"] = control.automation_id
            if any(control.rect):
                detail["rect"] = list(control.rect)
        self._commit(Activity(
            at=typing.started, kind="type", app=typing.window.process_name,
            window=typing.window.title,
            target=control.name if control else "",
            target_type=control.control_type if control else "",
            text="" if password else typing.value,
            detail=detail,
        ))

    # ------------------------------------------------------------- windows

    def _poll_foreground(self, now: float) -> None:
        window = self.resolver.foreground()
        if window is None:
            return
        previous = self._foreground
        if (
            previous is None or window.handle != previous.handle
            or window.fullscreen != previous.fullscreen
        ):
            self._flush_typing(now)
            self._end_fullscreen(now)
            self._foreground = window
            self._pending_title = None
            self._arrived(window, now)
            return
        if window.title != previous.title:
            self._foreground = window
            self._pending_title = (window.title, now)
            return
        pending = self._pending_title
        if pending is not None and now - pending[1] >= self.TITLE_SETTLE:
            self._pending_title = None
            if not self._watchable(window):
                return
            if _is_browser(window.process_name):
                self._note_page(window, now)
            else:
                self._commit(Activity(
                    at=now, kind="view", app=window.process_name,
                    window=window.title, target=window.title,
                    detail={"handle": window.handle},
                ))

    def _arrived(self, window: WindowInfo, now: float) -> None:
        if self._is_own(window) or window.process_name.casefold() in self.skip_apps:
            return
        if not window.title.strip():
            return
        if window.fullscreen:
            self._fullscreen_row = self._commit(Activity(
                at=now, kind="fullscreen", app=window.process_name,
                window=window.title, detail={"handle": window.handle},
            ))
            return
        if self._is_private(window):
            self._commit(Activity(at=now, kind="private", app=window.process_name))
            return
        self._commit(Activity(
            at=now, kind="switch", app=window.process_name, window=window.title,
            detail={"handle": window.handle},
        ))
        if _is_browser(window.process_name):
            self._note_page(window, now)

    def _end_fullscreen(self, now: float) -> None:
        if self._fullscreen_row and self.log is not None:
            self.log.extend(self._fullscreen_row, until=round(now, 3))
        self._fullscreen_row = 0

    def _note_page(self, window: WindowInfo, now: float) -> None:
        url = self.resolver.browser_url(window)
        if url and self._last_url.get(window.handle) == url:
            # The same page retitled itself ("(3) YouTube").
            return
        if url:
            self._last_url[window.handle] = url
        self._commit(Activity(
            at=now, kind="page", app=window.process_name, window=window.title,
            target=page_title(window.title), url=url,
            detail={"handle": window.handle},
        ))

    def _start_step(self, now: float) -> None:
        self._needs_start_step = False
        window = self.resolver.foreground()
        if window is None or self._is_own(window):
            # She was spoken to through her own window; the recording starts
            # wherever the person goes next.
            return
        url = self.resolver.browser_url(window) if _is_browser(window.process_name) else ""
        self._commit(Activity(
            at=now, kind="start", app=window.process_name, window=window.title,
            target=page_title(window.title), url=url,
            detail={"handle": window.handle, "window_rect": list(window.rect)},
        ), to_log=False)

    # -------------------------------------------------------------- settle

    def _settle(self, now: float) -> None:
        typing = self._typing
        if typing is not None:
            idle = now - typing.last_key
            if (
                typing.typing and idle >= self.READ_AFTER_PAUSE
                and typing.read_for != typing.last_key
            ):
                self._read_typed(typing)
            if idle >= self.TYPING_DONE:
                self._flush_typing(now)
        if self._held_click is not None and now - self._held_click[1] >= self.DOUBLE_CLICK_SECONDS:
            self._release_held_click()
        if self._toggles:
            due = [entry for entry in self._toggles if entry[0] <= now]
            self._toggles = [entry for entry in self._toggles if entry[0] > now]
            for _, activity, control in due:
                state = self.resolver.read_toggle(control)
                if state and state != control.toggle:
                    activity.detail["toggled"] = state
                    if activity.id and self.log is not None:
                        self.log.extend(activity.id, toggled=state)
        if self._scroll is not None and now - self._scroll[1] >= self.SCROLL_DONE:
            self._flush_scroll()

    def _flush_everything(self, now: float) -> None:
        self._flush_typing(now)
        self._release_held_click()
        self._flush_scroll()

    # -------------------------------------------------------------- commit

    def _commit(self, activity: Activity, *, to_log: bool = True) -> int:
        with self._lock:
            recording = self._recording
            if recording is not None:
                activity.recording = recording.id
                recording.steps.append(activity)
            self.recent.append(activity)
        if not to_log or self.log is None or self.paused:
            return 0
        logged = activity
        if activity.kind == "type" and self.typed_text != "always":
            # Kept in the recording's steps, not in the day's log.
            logged = replace(activity, text="")
        try:
            activity.id = self.log.add(logged)
        except Exception as error:
            print(f"[Activity] could not write: {type(error).__name__}: {error}")
            return 0
        return activity.id

    # ------------------------------------------------------------- filters

    def _is_own(self, window: WindowInfo) -> bool:
        return (
            window.pid in self.own_pids
            or window.process_name.casefold() in self.own_process_names
        )

    @staticmethod
    def _is_private(window: WindowInfo) -> bool:
        title = window.title.casefold()
        return _is_browser(window.process_name) and any(
            marker in title for marker in _PRIVATE_MARKERS
        )

    def _watchable(self, window: WindowInfo | None) -> bool:
        return bool(
            window is not None
            and not self._is_own(window)
            and window.process_name.casefold() not in self.skip_apps
            and not window.fullscreen
            and not self._is_private(window)
        )


def _click_detail(
    point: tuple[int, int], window: WindowInfo, control: ControlInfo | None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {"point": list(point), "handle": window.handle}
    left, top, right, bottom = window.rect
    if right > left and bottom > top:
        detail["window_rect"] = list(window.rect)
        detail["in_window"] = [
            round((point[0] - left) / (right - left), 4),
            round((point[1] - top) / (bottom - top), 4),
        ]
    if control is not None:
        if control.automation_id:
            detail["automation_id"] = control.automation_id
        if control.class_name:
            detail["class_name"] = control.class_name
        if any(control.rect):
            detail["rect"] = list(control.rect)
        if control.ancestors:
            detail["within"] = [list(pair) for pair in control.ancestors if pair[1]][:3]
        if control.toggle is not None:
            detail["toggle_before"] = control.toggle
    return detail


# --------------------------------------------------------------- screen


class ScreenResolver:
    """The real screen: Win32 for windows, UI Automation for controls.

    Created on the recorder's own thread, after its COM apartment exists.
    """

    _VALUE = 30045            # UIA_ValueValuePropertyId
    _IS_PASSWORD = 30019      # UIA_IsPasswordPropertyId
    _IS_TOGGLE = 30041        # UIA_IsTogglePatternAvailablePropertyId
    _TOGGLE_STATE = 30086     # UIA_ToggleToggleStatePropertyId
    _TOGGLE_NAMES = {0: "off", 1: "on", 2: "mixed"}

    def __init__(self) -> None:
        from tools.screen_control.dpi import ensure_per_monitor_dpi_aware

        ensure_per_monitor_dpi_aware()
        import psutil
        import win32api
        import win32con
        import win32gui
        import win32process
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo

        self._psutil = psutil
        self._api = win32api
        self._con = win32con
        self._gui = win32gui
        self._process = win32process
        self._iuia = IUIA()
        self._info = UIAElementInfo
        self._names: dict[int, str] = {}

    # windows
    def _window(self, handle: int) -> WindowInfo | None:
        if not handle:
            return None
        gui = self._gui
        try:
            root = gui.GetAncestor(handle, 2) or handle  # GA_ROOT
            title = gui.GetWindowText(root)
            _, pid = self._process.GetWindowThreadProcessId(root)
            rect = tuple(gui.GetWindowRect(root))
        except Exception:
            return None
        return WindowInfo(
            handle=int(root), title=str(title or ""),
            process_name=self._process_name(int(pid)), pid=int(pid),
            rect=rect, fullscreen=self._fullscreen(root, rect),
        )

    def _process_name(self, pid: int) -> str:
        if pid not in self._names:
            try:
                self._names[pid] = self._psutil.Process(pid).name()
            except Exception:
                self._names[pid] = ""
            if len(self._names) > 512:
                self._names.clear()
        return self._names.get(pid, "")

    def _fullscreen(self, handle: int, rect: tuple[int, ...]) -> bool:
        try:
            if self._gui.GetClassName(handle) in ("Progman", "WorkerW", "Shell_TrayWnd"):
                return False
            monitor = self._api.GetMonitorInfo(self._api.MonitorFromWindow(handle, 2))
            left, top, right, bottom = monitor["Monitor"]
            style = self._gui.GetWindowLong(handle, self._con.GWL_STYLE)
        except Exception:
            return False
        covers = rect[0] <= left and rect[1] <= top and rect[2] >= right and rect[3] >= bottom
        return covers and (style & self._con.WS_CAPTION) != self._con.WS_CAPTION

    def foreground(self) -> WindowInfo | None:
        return self._window(self._gui.GetForegroundWindow())

    def window_at(self, x: int, y: int) -> WindowInfo | None:
        try:
            return self._window(self._gui.WindowFromPoint((int(x), int(y))))
        except Exception:
            return None

    # controls
    def _control(self, info: Any, chain: list[Any] | None = None) -> ControlInfo:
        element = info.element
        rect = (0, 0, 0, 0)
        try:
            r = info.rectangle
            rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            pass
        toggle = None
        try:
            if element.GetCurrentPropertyValue(self._IS_TOGGLE):
                toggle = self._TOGGLE_NAMES.get(
                    int(element.GetCurrentPropertyValue(self._TOGGLE_STATE)),
                )
        except Exception:
            toggle = None
        try:
            password = bool(element.GetCurrentPropertyValue(self._IS_PASSWORD))
        except Exception:
            password = False
        ancestors = tuple(
            (str(getattr(item, "control_type", "") or ""),
             str(getattr(item, "name", "") or "")[:80])
            for item in (chain or [])
        )
        return ControlInfo(
            name=str(info.name or "")[:120],
            control_type=str(info.control_type or ""),
            automation_id=str(info.automation_id or "")[:120],
            class_name=str(info.class_name or "")[:80],
            rect=rect, is_password=password, toggle=toggle,
            ancestors=ancestors, element=element,
        )

    def control_at(self, x: int, y: int) -> ControlInfo | None:
        try:
            info = self._info.from_point(int(x), int(y))
        except Exception:
            return None
        chain = [info]
        try:
            parent = info.parent
            while parent is not None and len(chain) < 6:
                chain.append(parent)
                parent = parent.parent
        except Exception:
            pass
        pairs = []
        for item in chain:
            try:
                pairs.append((str(item.control_type or ""), str(item.name or "")))
            except Exception:
                pairs.append(("", ""))
        index, label = pick_target(pairs)
        control = self._control(chain[index], chain[index + 1:index + 4])
        control.name = str(label or control.name)[:120]
        return control

    def focused_control(self) -> ControlInfo | None:
        try:
            element = self._iuia.get_focused_element()
            if element is None:
                return None
            return self._control(self._info(element))
        except Exception:
            return None

    def read_value(self, control: ControlInfo) -> str:
        if control is None or control.is_password or control.element is None:
            return ""
        try:
            value = control.element.GetCurrentPropertyValue(self._VALUE)
        except Exception:
            return ""
        return " ".join(str(value or "").split())[:500]

    def read_toggle(self, control: ControlInfo) -> str | None:
        if control is None or control.element is None:
            return None
        try:
            return self._TOGGLE_NAMES.get(
                int(control.element.GetCurrentPropertyValue(self._TOGGLE_STATE)),
            )
        except Exception:
            return None

    def browser_url(self, window: WindowInfo) -> str:
        """The address bar's value: the first Edit whose value reads as an
        address (Chromium hides the scheme, so it is put back)."""
        try:
            root = self._info(window.handle)
            edits = root.descendants(control_type="Edit")
        except Exception:
            return ""
        for info in edits[:6]:
            try:
                value = str(info.element.GetCurrentPropertyValue(self._VALUE) or "").strip()
            except Exception:
                continue
            if not value or " " in value:
                continue
            if value.startswith(("http://", "https://", "about:", "file:", "chrome:", "edge:")):
                return value[:400]
            if "." in value.split("/")[0]:
                return f"https://{value}"[:400]
        return ""
