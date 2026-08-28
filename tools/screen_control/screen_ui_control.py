"""Cursor-driven Windows UI actions for any application (Phase 4F).

The drop-in replacement for :class:`WindowsUIControl`. Same method names,
same :class:`UIActionResult` shape, same lookup discipline -- every target is
resolved in a live UI Automation tree first, and nothing is acted on because
a model claimed it exists. What changes is the actuator: instead of calling
UIA ``Invoke()`` on a control, this moves the real pointer to that control's
rectangle and clicks it, and types with real keystrokes.

That difference is not cosmetic. ``windows_ui_control.click_then_type``
documents the limit it was written around: Chromium/CEF applications --
"Spotify, Battle.net, Discord, and similar" -- "render their real search/text
fields without ever exposing them as a named, verifiable UIA control", so
the Invoke driver has to click a nearby button and type blind, and can never
return ``verified=True``. A real pointer can click the field itself, because
the field still has a *rectangle* even when it has no invocable pattern.

Three ordering rules, each learned from a measurement:

* **Restore and focus before reading any rectangle.** A minimized Spotify
  reported its controls at (-32000, -32000) and in an internal 1000x750
  layout space. Restored, the same tree gave real screen coordinates.
* **Re-resolve after focusing.** Focusing can restore, move, or resize a
  window, which invalidates every rectangle read before it.
* **Let the tree wake.** CEF and Chromium build their accessibility tree
  lazily -- Spotify measured 25 nodes cold and 1465 once queried. A single
  cold look is not evidence that a window is empty.
"""

from __future__ import annotations

import time
from typing import Any

from tools.computer_control.windows_ui_control import (
    UIActionResult,
    WindowsUIControl as _WindowsUIControl,
    is_committing_control,
    is_credential_field,
)
from tools.computer_control.action_contract import (
    blind_typing_effect,
    field_is_empty,
)
from tools.computer_control.windows_ui_observer import (
    ControlLookup,
    WindowInfo,
    WindowsUIObserver,
)
from tools.screen_control.cursor_driver import CursorDriver

try:
    import win32con as _win32con
    import win32gui as _win32gui
except Exception:  # pragma: no cover - exercised only when pywin32 is absent
    _win32con = None
    _win32gui = None

_MAX_TYPE_LENGTH = 500
_FOCUS_SETTLE_SECONDS = 0.25
_ACTION_SETTLE_SECONDS = 0.35
_VERIFY_SETTLE_ATTEMPTS = 3
_VERIFY_SETTLE_INTERVAL_SECONDS = 0.15
# A cold CEF/Chromium window exposes only its frame. Give the tree a beat to
# build rather than reporting an app as empty.
_TREE_WAKE_ATTEMPTS = 3
_TREE_WAKE_DELAY_SECONDS = 0.4
# One retry for the click-then-type race described in type_text.
_TYPE_ATTEMPTS = 2
_SCROLL_NOTCHES = 4

_CLICK_ROLES = frozenset({
    "Button", "CheckBox", "ComboBox", "Hyperlink", "ListItem", "MenuItem",
    "RadioButton", "SplitButton", "TabItem", "TreeItem", "Text", "Image",
    "DataItem", "Group",
})
_TEXT_ROLES = frozenset({"Edit", "ComboBox", "Document"})


class ScreenUIControl:
    """Focus, click, type, select and scroll -- with the real mouse."""

    def __init__(
        self,
        *,
        observer: WindowsUIObserver | None = None,
        cursor: CursorDriver | None = None,
        sleeper=None,
        window_at_point=None,
    ) -> None:
        self.observer = observer or WindowsUIObserver()
        self.cursor = cursor or CursorDriver()
        self._sleep = sleeper or time.sleep
        self._window_at_point = window_at_point or self._default_window_at_point

    @property
    def available(self) -> bool:
        return self.observer.available and self.cursor.available

    # ------------------------------------------------------------------
    # window handling

    @staticmethod
    def _default_window_at_point(point: tuple[int, int]) -> int:
        if _win32gui is None:  # pragma: no cover - absent pywin32
            return 0
        try:
            return int(_win32gui.WindowFromPoint(point))
        except Exception:
            return 0

    @staticmethod
    def _handle_of(window: Any) -> int | None:
        for reader in ("handle",):
            value = getattr(window, reader, None)
            if isinstance(value, int) and value:
                return value
        try:
            return int(window.element_info.handle)
        except Exception:
            return None

    def _bring_forward(self, window: Any) -> tuple[bool, str]:
        """Restore and focus, verifying rather than assuming.

        SetForegroundWindow returns before the foreground has actually
        changed and is refused outright from a process that does not own it,
        so each attempt is followed by a settle and a real check. Delivering
        a keystroke first is what earns the right to change the foreground.
        """
        handle = self._handle_of(window)
        if handle is None or _win32gui is None:
            try:
                window.set_focus()
                return True, "set_focus"
            except Exception as error:
                return False, f"{type(error).__name__}"

        def _is_front() -> bool:
            try:
                return int(_win32gui.GetForegroundWindow()) == int(handle)
            except Exception:
                return False

        try:
            if _win32gui.IsIconic(handle):
                _win32gui.ShowWindow(handle, _win32con.SW_RESTORE)
                self._sleep(_FOCUS_SETTLE_SECONDS)
        except Exception:
            pass
        if _is_front():
            return True, "already frontmost"

        def _plain() -> None:
            _win32gui.SetForegroundWindow(handle)

        def _with_key() -> None:
            self.cursor.press("alt")
            _win32gui.SetForegroundWindow(handle)

        def _switch() -> None:
            import ctypes

            ctypes.windll.user32.SwitchToThisWindow(handle, True)

        for attempt in (_plain, _with_key, _switch):
            try:
                attempt()
            except Exception:
                pass
            self._sleep(_FOCUS_SETTLE_SECONDS)
            if _is_front():
                return True, "brought to the front"
        return False, "the window would not come forward"

    def _require_window(self, target: str | WindowInfo) -> Any | UIActionResult:
        try:
            window = self.observer.find_window(target)
        except Exception as error:
            return UIActionResult(
                "failed", f"I couldn't find that window: {error}",
            )
        if window is None:
            return UIActionResult(
                "not_found",
                f"I couldn't find a window matching {self._target_title(target)!r}.",
            )
        return window

    @staticmethod
    def _target_title(target: str | WindowInfo) -> str:
        if isinstance(target, WindowInfo):
            return target.title
        return str(target)

    def focus_window(self, title_query: str | WindowInfo) -> UIActionResult:
        window = self._require_window(title_query)
        if isinstance(window, UIActionResult):
            return window
        focused, evidence = self._bring_forward(window)
        title = (
            self.observer._safe_text(window) or self._target_title(title_query)
        )
        if not focused:
            return UIActionResult(
                "verification_failed",
                f"I asked {title} to come forward, but it is not focused.",
                window_title=title, verified=False, evidence=evidence,
            )
        return UIActionResult(
            "focused", f"Focused {title}.",
            window_title=title, verified=True, evidence=evidence,
        )

    # ------------------------------------------------------------------
    # resolution

    def _resolve_live(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        element_id: str,
        *,
        expected_roles: frozenset[str] | None = None,
    ) -> tuple[Any, str, ControlLookup] | UIActionResult:
        """Focus the window, then resolve the control in the *woken* tree."""
        window = self._require_window(title_query)
        if isinstance(window, UIActionResult):
            return window
        focused, evidence = self._bring_forward(window)
        window_title = (
            self.observer._safe_text(window) or self._target_title(title_query)
        )
        if not focused:
            return UIActionResult(
                "failed",
                f"I couldn't bring {window_title} forward, so I will not "
                "click at screen coordinates it may not own.",
                window_title=window_title, evidence=evidence,
            )
        self._sleep(_FOCUS_SETTLE_SECONDS)

        lookup: ControlLookup | None = None
        for attempt in range(_TREE_WAKE_ATTEMPTS):
            if element_id:
                lookup = self.observer.resolve_control_by_id(window, element_id)
            else:
                lookup = self.observer.resolve_control(
                    window, control_name, expected_roles=expected_roles,
                )
            if lookup is not None and lookup.status == "matched":
                return window, window_title, lookup
            if attempt < _TREE_WAKE_ATTEMPTS - 1:
                # Cold CEF/Chromium tree: it builds on being queried.
                self._sleep(_TREE_WAKE_DELAY_SECONDS)
        return self._lookup_failure(
            lookup, window_title, element_id or control_name,
        )

    @staticmethod
    def _lookup_failure(
        lookup: ControlLookup | None, window_title: str, requested: str,
    ) -> UIActionResult:
        if lookup is None:
            return UIActionResult(
                "not_found",
                f"I couldn't find {requested!r} in {window_title}.",
                window_title=window_title,
            )
        message = lookup.message or (
            f"I couldn't find {requested!r} in {window_title}."
        )
        if lookup.candidates:
            shown = ", ".join(repr(name) for name in lookup.candidates[:5])
            message = f"{message} I can see: {shown}."
        return UIActionResult(
            lookup.status if lookup.status != "matched" else "not_found",
            message,
            window_title=window_title,
        )

    def _reresolve(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        element_id: str,
        expected_roles: frozenset[str] | None,
    ) -> Any | None:
        """Look the control up again in a freshly read tree, or None."""
        try:
            window = self.observer.find_window(title_query)
            if window is None:
                return None
            if element_id:
                lookup = self.observer.resolve_control_by_id(window, element_id)
                if lookup is not None and lookup.status == "matched":
                    return lookup.control
            lookup = self.observer.resolve_control(
                window, control_name, expected_roles=expected_roles,
            )
        except Exception:
            return None
        if lookup is not None and lookup.status == "matched":
            return lookup.control
        return None

    def _click_point(self, control: Any) -> tuple[int, int] | None:
        try:
            rect = control.element_info.rectangle
            left, top = int(rect.left), int(rect.top)
            right, bottom = int(rect.right), int(rect.bottom)
        except Exception:
            return None
        if right <= left or bottom <= top:
            return None
        point = ((left + right) // 2, (top + bottom) // 2)
        if not self.cursor.point_is_on_screen(point):
            return None
        return point

    def _owns_point(self, window: Any, point: tuple[int, int]) -> bool:
        """Whether the pixel we are about to click belongs to this window."""
        handle = self._handle_of(window)
        if handle is None or _win32gui is None:
            return True
        owner = self._window_at_point(point)
        if not owner:
            return False
        if int(owner) == int(handle):
            return True
        try:
            return int(_win32gui.GetAncestor(owner, 2)) == int(handle)  # GA_ROOT
        except Exception:
            return False

    # ------------------------------------------------------------------
    # actions

    def click_control(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        *,
        confirmed: bool = False,
        element_id: str = "",
    ) -> UIActionResult:
        resolved = self._resolve_live(
            title_query, control_name, element_id, expected_roles=_CLICK_ROLES,
        )
        if isinstance(resolved, UIActionResult):
            return resolved
        window, window_title, lookup = resolved
        control = lookup.control
        real_name = lookup.name or control_name

        if is_committing_control(real_name) and not confirmed:
            return UIActionResult(
                "confirmation_required",
                f"Clicking {real_name!r} needs confirmation first.",
                window_title=window_title, control_name=real_name,
            )

        point = self._click_point(control)
        if point is None:
            return UIActionResult(
                "not_actionable",
                f"{real_name!r} has no usable position on screen, so I "
                "cannot click it.",
                window_title=window_title, control_name=real_name,
            )
        if not self._owns_point(window, point):
            return UIActionResult(
                "blocked",
                f"Something else is covering {real_name!r} on screen, so I "
                "did not click there.",
                window_title=window_title, control_name=real_name,
            )

        before_toggle = self._read_toggle_state(control)
        outcome = self.cursor.click(point)
        if not outcome.succeeded:
            return UIActionResult(
                outcome.status, outcome.message,
                window_title=window_title, control_name=real_name,
            )
        self._sleep(_ACTION_SETTLE_SECONDS)
        verified, evidence = self._verify_click(control, before_toggle)
        return UIActionResult(
            "clicked", f"Clicked {real_name}.",
            window_title=window_title, control_name=real_name,
            verified=verified, evidence=evidence,
        )

    def double_click_control(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        *,
        confirmed: bool = False,
        element_id: str = "",
    ) -> UIActionResult:
        """Double-click a control with the real pointer.

        Search results are list rows, and a list row reads one click and two
        clicks as different instructions. Measured against Spotify: clicking
        a track title once navigates to what that title links to (its album),
        while a double-click on the same pixel starts playing it. This is the
        actuator for "play exactly this item"; the caller is responsible for
        proving playback actually started.
        """
        resolved = self._resolve_live(
            title_query, control_name, element_id, expected_roles=_CLICK_ROLES,
        )
        if isinstance(resolved, UIActionResult):
            return resolved
        window, window_title, lookup = resolved
        control = lookup.control
        real_name = lookup.name or control_name

        if is_committing_control(real_name) and not confirmed:
            return UIActionResult(
                "confirmation_required",
                f"Double-clicking {real_name!r} needs confirmation first.",
                window_title=window_title, control_name=real_name,
            )

        point = self._click_point(control)
        if point is None:
            return UIActionResult(
                "not_actionable",
                f"{real_name!r} has no usable position on screen, so I "
                "cannot double-click it.",
                window_title=window_title, control_name=real_name,
            )
        if not self._owns_point(window, point):
            return UIActionResult(
                "blocked",
                f"Something else is covering {real_name!r} on screen, so I "
                "did not double-click there.",
                window_title=window_title, control_name=real_name,
            )

        outcome = self.cursor.double_click(point)
        if not outcome.succeeded:
            return UIActionResult(
                outcome.status, outcome.message,
                window_title=window_title, control_name=real_name,
            )
        self._sleep(_ACTION_SETTLE_SECONDS)
        return UIActionResult(
            "clicked", f"Double-clicked {real_name}.",
            window_title=window_title, control_name=real_name,
            verified=None,
            evidence=(
                "A double-click has no readable postcondition of its own; "
                "the caller verifies what it was meant to start."
            ),
        )

    def type_text(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        text: str,
        *,
        confirmed: bool = False,
        element_id: str = "",
        submit: bool = False,
    ) -> UIActionResult:
        """Click into a field and type, then read the value back."""
        resolved = self._resolve_live(
            title_query, control_name, element_id, expected_roles=_TEXT_ROLES,
        )
        if isinstance(resolved, UIActionResult):
            return resolved
        window, window_title, lookup = resolved
        control = lookup.control
        real_name = lookup.name or control_name

        # The desktop predicate matches on the control's accessible name
        # alone -- unlike the browser one, there is no input type to consult.
        if is_credential_field(real_name):
            return UIActionResult(
                "refused",
                f"{real_name!r} looks like a credential field -- please type "
                "that one yourself.",
                window_title=window_title, control_name=real_name,
            )
        bounded = str(text)[:_MAX_TYPE_LENGTH]
        point = self._click_point(control)
        if point is None:
            return UIActionResult(
                "not_actionable",
                f"{real_name!r} has no usable position on screen, so I "
                "cannot type into it.",
                window_title=window_title, control_name=real_name,
            )
        if not self._owns_point(window, point):
            return UIActionResult(
                "blocked",
                f"Something else is covering {real_name!r} on screen.",
                window_title=window_title, control_name=real_name,
            )

        # Typing into a CEF search box races the app: clicking the field can
        # make Spotify rebuild that part of its tree, so the click may land
        # a moment before the box is ready for keystrokes. Measured live,
        # this succeeded on some runs and read back empty on others with no
        # change in the code path. One bounded retry converts that flake
        # into a reliable result, and a second failure is still reported as
        # a failure rather than assumed to have worked.
        verified: bool | None = False
        evidence = ""
        for attempt in range(_TYPE_ATTEMPTS):
            if attempt:
                refreshed = self._reresolve(
                    title_query, real_name, element_id, _TEXT_ROLES,
                )
                if refreshed is not None:
                    control = refreshed
                    retry_point = self._click_point(control)
                    if retry_point is not None:
                        point = retry_point
                self._sleep(_ACTION_SETTLE_SECONDS)

            # Read the field before typing: the settling verifier uses it to
            # tell "the tree has not caught up yet" from "the value really
            # did not change", which Electron/CEF apps genuinely differ on.
            # It is also the precondition -- whether anything is in the way.
            before_value = self._read_text_value(control)
            ready = field_is_empty(before_value[0] if before_value else None)

            # Clicking the field is what gives it keyboard focus; typing
            # goes wherever focus is, so this ordering is load-bearing.
            clicked = self.cursor.click(point)
            if not clicked.succeeded:
                return UIActionResult(
                    clicked.status, clicked.message,
                    window_title=window_title, control_name=real_name,
                )
            self._sleep(_ACTION_SETTLE_SECONDS)
            cleared = self.cursor.clear_field()
            if not cleared.succeeded:
                return UIActionResult(
                    cleared.status, cleared.message,
                    window_title=window_title, control_name=real_name,
                )
            typed = self.cursor.type_text(bounded)
            if not typed.succeeded:
                return UIActionResult(
                    typed.status, typed.message,
                    window_title=window_title, control_name=real_name,
                )
            self._sleep(_ACTION_SETTLE_SECONDS)
            # Re-resolve before reading back. CEF apps rebuild their
            # accessibility tree when a field takes focus, so the element
            # that was clicked can be detached by the time the value is read
            # -- a stale node still answers, with the value it had before
            # any of this happened, which reads as "the typing did not
            # land". Verified live against Spotify's search box.
            fresh = self._reresolve(
                title_query, real_name, element_id, _TEXT_ROLES,
            )
            effect = _WindowsUIControl._settled_replacement(
                fresh if fresh is not None else control, bounded, before_value,
            )
            verified = effect.holds
            evidence = _WindowsUIControl._contract_evidence(ready, effect)
            if verified is not False:
                break

        if submit and verified is not False:
            pressed = self.cursor.press("enter")
            if not pressed.succeeded:
                return UIActionResult(
                    pressed.status, pressed.message,
                    window_title=window_title, control_name=real_name,
                )
            self._sleep(_ACTION_SETTLE_SECONDS)
        if verified is False:
            return UIActionResult(
                "verification_failed",
                f"I typed into {real_name!r}, but it does not read back as "
                f"{bounded!r}.",
                window_title=window_title, control_name=real_name,
                verified=False, evidence=evidence,
            )
        return UIActionResult(
            "typed", f"Typed {bounded!r} into {real_name}.",
            window_title=window_title, control_name=real_name,
            verified=verified, evidence=evidence,
        )

    def click_then_type(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        text: str,
        *,
        confirmed: bool = False,
        element_id: str = "",
    ) -> UIActionResult:
        """Click a control that reveals a field, then type into it.

        Kept for compatibility with the Invoke driver's tool surface. On
        this driver it is rarely needed: a CEF search box has a rectangle
        even when it exposes no invocable pattern, so ``type_text`` can
        usually click the field itself and actually verify the result.
        """
        clicked = self.click_control(
            title_query, control_name, confirmed=confirmed, element_id=element_id,
        )
        if clicked.status != "clicked":
            return clicked
        self._sleep(_ACTION_SETTLE_SECONDS)
        # Whatever this opened may already hold text -- a previous search,
        # most often. Typing without selecting it first appends, producing
        # a query that is two requests glued together and matches nothing.
        self.cursor.select_all()
        typed = self.cursor.type_text(str(text)[:_MAX_TYPE_LENGTH])
        if not typed.succeeded:
            return UIActionResult(
                typed.status, typed.message,
                window_title=clicked.window_title,
                control_name=clicked.control_name,
            )
        effect = blind_typing_effect(repr(clicked.control_name))
        return UIActionResult(
            "typed",
            f"Clicked {clicked.control_name} and typed into whatever field "
            "it opened.",
            window_title=clicked.window_title,
            control_name=clicked.control_name,
            verified=effect.holds,
            evidence=(
                f"Any existing contents were selected first. {effect.evidence}"
            ),
        )

    def press_key(
        self, title_query: str | WindowInfo, *keys: str,
    ) -> UIActionResult:
        """Send a key or chord to a focused window (Enter, Escape, ...)."""
        window = self._require_window(title_query)
        if isinstance(window, UIActionResult):
            return window
        focused, evidence = self._bring_forward(window)
        window_title = (
            self.observer._safe_text(window) or self._target_title(title_query)
        )
        if not focused:
            return UIActionResult(
                "failed",
                f"I couldn't bring {window_title} forward, so I did not send "
                "any keys.",
                window_title=window_title, evidence=evidence,
            )
        pressed = self.cursor.press(*keys)
        if not pressed.succeeded:
            return UIActionResult(
                pressed.status, pressed.message, window_title=window_title,
            )
        return UIActionResult(
            "typed", f"Pressed {'+'.join(keys)}.",
            window_title=window_title,
            verified=None,
            evidence="Keys were sent to the focused window.",
        )

    def select_option(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        option: str,
        *,
        confirmed: bool = False,
        element_id: str = "",
    ) -> UIActionResult:
        """Open a combo box and click the named option inside it."""
        resolved = self._resolve_live(
            title_query, control_name, element_id,
            expected_roles=frozenset({"ComboBox", "List", "Tab"}),
        )
        if isinstance(resolved, UIActionResult):
            return resolved
        window, window_title, lookup = resolved
        real_name = lookup.name or control_name
        point = self._click_point(lookup.control)
        if point is None or not self._owns_point(window, point):
            return UIActionResult(
                "not_actionable",
                f"{real_name!r} is not in a clickable position on screen.",
                window_title=window_title, control_name=real_name,
            )
        opened = self.cursor.click(point)
        if not opened.succeeded:
            return UIActionResult(
                opened.status, opened.message,
                window_title=window_title, control_name=real_name,
            )
        self._sleep(_ACTION_SETTLE_SECONDS)
        # The option list is a fresh subtree once the control is open.
        option_lookup = self.observer.resolve_control(
            window, option,
            expected_roles=frozenset({"ListItem", "MenuItem", "TreeItem", "Text"}),
        )
        if option_lookup is None or option_lookup.status != "matched":
            return UIActionResult(
                "not_found",
                f"I opened {real_name!r} but couldn't find {option!r} in it.",
                window_title=window_title, control_name=real_name,
            )
        option_point = self._click_point(option_lookup.control)
        if option_point is None:
            return UIActionResult(
                "not_actionable",
                f"{option!r} has no usable position on screen.",
                window_title=window_title, control_name=real_name,
            )
        chosen = self.cursor.click(option_point)
        if not chosen.succeeded:
            return UIActionResult(
                chosen.status, chosen.message,
                window_title=window_title, control_name=real_name,
            )
        self._sleep(_ACTION_SETTLE_SECONDS)
        verified, evidence = self._verify_selected_option(lookup.control, option)
        return UIActionResult(
            "selected", f"Selected {option} in {real_name}.",
            window_title=window_title, control_name=real_name,
            verified=verified, evidence=evidence,
        )

    def scroll_control(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        direction: str = "down",
        *,
        element_id: str = "",
    ) -> UIActionResult:
        heading = str(direction).strip().lower()
        if heading not in {"up", "down"}:
            return UIActionResult("refused", "I can scroll up or down.")
        resolved = self._resolve_live(title_query, control_name, element_id)
        if isinstance(resolved, UIActionResult):
            return resolved
        window, window_title, lookup = resolved
        real_name = lookup.name or control_name
        point = self._click_point(lookup.control)
        if point is None or not self._owns_point(window, point):
            return UIActionResult(
                "not_actionable",
                f"{real_name!r} is not in a scrollable position on screen.",
                window_title=window_title, control_name=real_name,
            )
        outcome = self.cursor.scroll(
            point, _SCROLL_NOTCHES if heading == "up" else -_SCROLL_NOTCHES,
        )
        if not outcome.succeeded:
            return UIActionResult(
                outcome.status, outcome.message,
                window_title=window_title, control_name=real_name,
            )
        return UIActionResult(
            "scrolled", f"Scrolled {heading} in {real_name}.",
            window_title=window_title, control_name=real_name,
            verified=None,
            evidence="Wheel input was delivered at the control's position.",
        )

    # ------------------------------------------------------------------
    # verification

    # Delegated to the Invoke driver's readers rather than reimplemented:
    # what counts as evidence that a click landed or a value was typed does
    # not depend on how the input was delivered, and these already handle
    # the asynchronous accessibility trees of Electron/CEF apps.

    @staticmethod
    def _read_toggle_state(control: Any) -> object:
        return _WindowsUIControl._read_toggle_state(control)

    @staticmethod
    def _read_text_value(control: Any) -> tuple[str, str, bool] | None:
        return _WindowsUIControl._read_text_value(control)

    def _verify_click(
        self, control: Any, before_toggle: object,
    ) -> tuple[bool | None, str]:
        return _WindowsUIControl._verify_click(control, before_toggle)

    def _verify_typed_text(
        self,
        control: Any,
        expected: str,
        before: tuple[str, str, bool] | None = None,
    ) -> tuple[bool | None, str]:
        return _WindowsUIControl._verify_typed_text_with_settle(
            control, expected, before,
        )

    def _verify_selected_option(
        self, control: Any, expected: str,
    ) -> tuple[bool | None, str]:
        return _WindowsUIControl._verify_selected_option(control, expected)
