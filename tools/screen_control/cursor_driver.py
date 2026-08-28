"""Real mouse and keyboard input for screen-native control (Phase 4E/4F).

This is the only module in the package that changes anything -- and unlike
every other actuator in this codebase, what it changes is the machine
itself, not an application's internal state. It moves the pointer the user
is holding and types into whatever currently has focus. Three consequences
shaped it:

* **Coordinates must be physical.**  Every point here is a physical screen
  pixel under per-monitor DPI awareness. Without that, SendInput's absolute
  coordinates and UI Automation's rectangles disagree by the display scale
  factor and clicks land somewhere else entirely. See dpi.py.
* **The user can take the mouse back at any moment.**  There is no way to
  reserve the pointer, so the driver instead notices. Two signals, because
  neither alone is enough: an :class:`InputWatcher` reports real (non-
  injected) input, which is the only way to see *typing*; and the pointer
  being somewhere it was not parked catches anything the hooks miss. A run
  that discovers either stops rather than fighting for control.
* **Input goes wherever focus is.**  Typing is only safe after a verified
  click into a known field, which is why this module exposes typing as a
  primitive and leaves the "did we actually focus the right thing" question
  to the control layer above it -- screen_browser_control.py for a page,
  screen_ui_control.py for any other window -- which can re-observe and check.

Synthetic input is deliberately *not* instant.  A pointer that teleports and
clicks in the same tick does not produce the hover/mouseover sequence real
sites rely on to open menus and reveal controls, so moves are interpolated
and clicks are separated from the move that precedes them.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time
from dataclasses import dataclass

from tools.screen_control.dpi import (
    coordinates_are_trustworthy,
    ensure_per_monitor_dpi_aware,
)
from tools.screen_control.input_watcher import InputWatcher

ensure_per_monitor_dpi_aware()

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

_ABSOLUTE_RANGE = 65535
_WHEEL_DELTA = 120

# Movement is interpolated so hover-driven UI (menus, tooltips, lazy
# toolbars) actually fires. These are small enough to stay imperceptible in
# aggregate and large enough to generate a real mousemove stream.
_MOVE_STEPS = 12
_MOVE_STEP_SECONDS = 0.006
_SETTLE_BEFORE_CLICK_SECONDS = 0.05
_CLICK_HOLD_SECONDS = 0.03
# Two presses count as one double-click only if they arrive inside the
# system's double-click time (500 ms by default). This is well inside it
# while still leaving each press its own down/up pair.
_DOUBLE_CLICK_GAP_SECONDS = 0.06
_KEYSTROKE_SECONDS = 0.004

# How far the pointer may sit from where we parked it before we treat the
# difference as the user having grabbed the mouse. A couple of pixels covers
# pointer-precision and rounding; anything more is a person.
_TAKEOVER_TOLERANCE_PIXELS = 6

_VIRTUAL_KEYS = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "space": 0x20, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "f5": 0x74, "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
    "a": 0x41, "c": 0x43, "v": 0x56, "l": 0x4C, "t": 0x54, "w": 0x57,
}
# Keys that must carry the extended-key flag or Windows delivers the numpad
# equivalent instead.
_EXTENDED_KEYS = frozenset({
    0x2E, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27,
})


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


@dataclass(frozen=True)
class InputResult:
    status: str  # done | user_took_over | unsafe_coordinates | out_of_bounds | unavailable
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "done"


class UserTookOverError(RuntimeError):
    """The pointer moved somewhere this driver did not put it."""


class CursorDriver:
    """Move the real pointer, click, scroll, and type."""

    def __init__(
        self,
        *,
        sender=None,
        cursor_reader=None,
        sleeper=None,
        input_watcher: InputWatcher | None = None,
    ) -> None:
        self._sender = sender or self._default_sender
        self._cursor_reader = cursor_reader or self._default_cursor_reader
        self._sleep = sleeper or time.sleep
        # Optional: when present and running, real user input is what
        # decides a takeover. Without it only pointer drift is visible,
        # which cannot see the user typing.
        self.input_watcher = input_watcher
        # Real input after this checkpoint counts as the user intervening.
        # Reset on begin_run and whenever a takeover is granted.
        self._input_mark: float | None = None
        # Where we last parked the pointer. None means we have not moved it
        # yet this session, so there is nothing to compare against.
        self._parked_at: tuple[int, int] | None = None
        # Where the pointer was before a run started, so it can be handed
        # back exactly where the user left it.
        self._restore_to: tuple[int, int] | None = None

    # ------------------------------------------------------------------
    # platform primitives

    @staticmethod
    def _default_sender(inputs: list[_INPUT]) -> int:
        array = (_INPUT * len(inputs))(*inputs)
        return int(ctypes.windll.user32.SendInput(
            len(inputs), array, ctypes.sizeof(_INPUT),
        ))

    @staticmethod
    def _default_cursor_reader() -> tuple[int, int]:
        point = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return (int(point.x), int(point.y))

    @staticmethod
    def virtual_screen() -> tuple[int, int, int, int]:
        """(left, top, width, height) across every monitor, physical pixels."""
        metrics = ctypes.windll.user32.GetSystemMetrics
        return (
            int(metrics(_SM_XVIRTUALSCREEN)), int(metrics(_SM_YVIRTUALSCREEN)),
            int(metrics(_SM_CXVIRTUALSCREEN)), int(metrics(_SM_CYVIRTUALSCREEN)),
        )

    @property
    def available(self) -> bool:
        return coordinates_are_trustworthy()

    # ------------------------------------------------------------------
    # Run-scoped emergency-stop tracking

    def begin_run(self) -> None:
        """Mark the start of a batch of actions and remember the pointer."""
        try:
            self._restore_to = self._cursor_reader()
        except Exception:
            self._restore_to = None
        self._parked_at = None
        self._input_mark = self._watcher_mark()

    def end_run(self, *, restore: bool = True) -> None:
        """Hand the pointer back where the user left it."""
        target = self._restore_to
        self._restore_to = None
        self._parked_at = None
        self._input_mark = None
        if restore and target is not None:
            try:
                self._move_to(target)
            except Exception:
                pass

    def _watcher_mark(self) -> float | None:
        watcher = self.input_watcher
        if watcher is None or not watcher.available:
            return None
        try:
            return watcher.mark()
        except Exception:
            return None

    def user_took_over(self) -> bool:
        """True when the person has intervened since we last checked in.

        Real input is the primary signal because it is the only one that
        sees typing. Pointer drift stays as a second, independent check:
        stopping when we did not have to is a great deal better than
        wrestling someone for their own mouse.
        """
        watcher = self.input_watcher
        if (
            watcher is not None
            and watcher.available
            and self._input_mark is not None
        ):
            try:
                if watcher.user_input_since(self._input_mark):
                    return True
            except Exception:
                pass
        if self._parked_at is None:
            return False
        try:
            current = self._cursor_reader()
        except Exception:
            return False
        return (
            abs(current[0] - self._parked_at[0]) > _TAKEOVER_TOLERANCE_PIXELS
            or abs(current[1] - self._parked_at[1]) > _TAKEOVER_TOLERANCE_PIXELS
        )

    def _guard(self) -> InputResult | None:
        if not self.available:
            return InputResult(
                "unsafe_coordinates",
                "Screen coordinates are not trustworthy on this process "
                "(DPI awareness could not be set), so I will not click blindly.",
            )
        if self.user_took_over():
            return InputResult(
                "user_took_over",
                "You moved the mouse, so I stopped and gave it back to you.",
            )
        return None

    # ------------------------------------------------------------------
    # movement

    def _to_absolute(self, point: tuple[int, int]) -> tuple[int, int]:
        left, top, width, height = self.virtual_screen()
        if width <= 1 or height <= 1:
            return (0, 0)
        x = int(round((point[0] - left) * _ABSOLUTE_RANGE / (width - 1)))
        y = int(round((point[1] - top) * _ABSOLUTE_RANGE / (height - 1)))
        return (
            max(0, min(_ABSOLUTE_RANGE, x)),
            max(0, min(_ABSOLUTE_RANGE, y)),
        )

    def point_is_on_screen(self, point: tuple[int, int]) -> bool:
        left, top, width, height = self.virtual_screen()
        return (
            left <= point[0] < left + width and top <= point[1] < top + height
        )

    def _mouse_event(self, flags: int, absolute: tuple[int, int] = (0, 0),
                     data: int = 0) -> _INPUT:
        event = _INPUT()
        event.type = _INPUT_MOUSE
        event.union.mi = _MOUSEINPUT(
            dx=absolute[0], dy=absolute[1], mouseData=data,
            dwFlags=flags, time=0, dwExtraInfo=None,
        )
        return event

    def _move_to(self, point: tuple[int, int]) -> None:
        absolute = self._to_absolute(point)
        self._sender([self._mouse_event(
            _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK,
            absolute,
        )])
        self._parked_at = point

    def move(self, point: tuple[int, int]) -> InputResult:
        """Glide the pointer to a physical screen point."""
        blocked = self._guard()
        if blocked is not None:
            return blocked
        if not self.point_is_on_screen(point):
            return InputResult(
                "out_of_bounds",
                f"The target point {point} is not on any screen.",
            )
        try:
            start = self._cursor_reader()
        except Exception:
            start = point
        # Interpolate so the page sees a real mousemove stream and hover
        # handlers fire before the click.
        for step in range(1, _MOVE_STEPS + 1):
            fraction = step / _MOVE_STEPS
            eased = fraction * fraction * (3 - 2 * fraction)  # smoothstep
            interim = (
                int(round(start[0] + (point[0] - start[0]) * eased)),
                int(round(start[1] + (point[1] - start[1]) * eased)),
            )
            self._move_to(interim)
            if step < _MOVE_STEPS:
                self._sleep(_MOVE_STEP_SECONDS)
        self._move_to(point)
        return InputResult("done")

    # ------------------------------------------------------------------
    # actions

    def click(self, point: tuple[int, int]) -> InputResult:
        """Move to a point and press the left button there."""
        moved = self.move(point)
        if not moved.succeeded:
            return moved
        # A click in the same tick as the move does not produce the
        # hover-then-press sequence sites expect.
        self._sleep(_SETTLE_BEFORE_CLICK_SECONDS)
        blocked = self._guard()
        if blocked is not None:
            return blocked
        self._sender([self._mouse_event(_MOUSEEVENTF_LEFTDOWN)])
        self._sleep(_CLICK_HOLD_SECONDS)
        self._sender([self._mouse_event(_MOUSEEVENTF_LEFTUP)])
        return InputResult("done")

    def double_click(self, point: tuple[int, int]) -> InputResult:
        """Move to a point and press the left button twice, as a person does.

        A single click is not a weaker double-click: in list-shaped UI they
        mean different things. Spotify's search results are the case this was
        written for -- one click on a track row selects it or opens what the
        row links to, and only a double-click starts playing it. Sending two
        separate click() calls would not do this either; the second move and
        settle push the presses past the system double-click time, and the app
        sees two unrelated single clicks.
        """
        moved = self.move(point)
        if not moved.succeeded:
            return moved
        self._sleep(_SETTLE_BEFORE_CLICK_SECONDS)
        blocked = self._guard()
        if blocked is not None:
            return blocked
        for press in range(2):
            if press:
                self._sleep(_DOUBLE_CLICK_GAP_SECONDS)
            self._sender([self._mouse_event(_MOUSEEVENTF_LEFTDOWN)])
            self._sleep(_CLICK_HOLD_SECONDS)
            self._sender([self._mouse_event(_MOUSEEVENTF_LEFTUP)])
        return InputResult("done")

    def scroll(self, point: tuple[int, int], notches: int) -> InputResult:
        """Wheel-scroll at a point. Positive scrolls up, negative down."""
        moved = self.move(point)
        if not moved.succeeded:
            return moved
        blocked = self._guard()
        if blocked is not None:
            return blocked
        for _ in range(abs(int(notches))):
            self._sender([self._mouse_event(
                _MOUSEEVENTF_WHEEL,
                data=_WHEEL_DELTA if notches > 0 else -_WHEEL_DELTA,
            )])
            self._sleep(_KEYSTROKE_SECONDS)
        return InputResult("done")

    # ------------------------------------------------------------------
    # keyboard

    def _key_event(self, *, vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
        event = _INPUT()
        event.type = _INPUT_KEYBOARD
        event.union.ki = _KEYBDINPUT(
            wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None,
        )
        return event

    def type_text(self, text: str) -> InputResult:
        """Type literal text into whatever currently has focus.

        Sent as Unicode scan codes rather than virtual keys, so Korean and
        every other non-Latin script types correctly regardless of the
        active keyboard layout. A virtual-key path would silently produce
        the wrong characters here.
        """
        blocked = self._guard()
        if blocked is not None:
            return blocked
        for character in str(text):
            for unit in self._utf16_units(character):
                self._sender([
                    self._key_event(scan=unit, flags=_KEYEVENTF_UNICODE),
                ])
                self._sender([
                    self._key_event(
                        scan=unit, flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
                    ),
                ])
            self._sleep(_KEYSTROKE_SECONDS)
        return InputResult("done")

    @staticmethod
    def _utf16_units(character: str) -> tuple[int, ...]:
        """UTF-16 code units, so astral characters (emoji) survive."""
        encoded = character.encode("utf-16-le")
        return tuple(
            int.from_bytes(encoded[offset:offset + 2], "little")
            for offset in range(0, len(encoded), 2)
        )

    def press(self, *keys: str) -> InputResult:
        """Press a key, or a chord like press("ctrl", "a")."""
        blocked = self._guard()
        if blocked is not None:
            return blocked
        codes: list[int] = []
        for key in keys:
            code = _VIRTUAL_KEYS.get(str(key).strip().lower())
            if code is None:
                return InputResult(
                    "unavailable", f"I do not have a key mapping for {key!r}.",
                )
            codes.append(code)
        for code in codes:
            flags = _KEYEVENTF_EXTENDEDKEY if code in _EXTENDED_KEYS else 0
            self._sender([self._key_event(vk=code, flags=flags)])
            self._sleep(_KEYSTROKE_SECONDS)
        for code in reversed(codes):
            flags = _KEYEVENTF_KEYUP
            if code in _EXTENDED_KEYS:
                flags |= _KEYEVENTF_EXTENDEDKEY
            self._sender([self._key_event(vk=code, flags=flags)])
            self._sleep(_KEYSTROKE_SECONDS)
        return InputResult("done")

    def clear_field(self) -> InputResult:
        """Select-all then delete, for replacing a field's contents."""
        selected = self.press("ctrl", "a")
        if not selected.succeeded:
            return selected
        return self.press("delete")
