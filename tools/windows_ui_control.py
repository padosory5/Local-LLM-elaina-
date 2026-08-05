"""Verified Windows UI actions: focus, click, type, select, scroll (4B.2).

This is the only module in the desktop-control system that can change
anything on screen -- windows_ui_observer.py stays strictly read-only. Every
target here is looked up in a live UI Automation tree through
WindowsUIObserver first; nothing is ever acted on just because the model
claimed a name existed.

Two hard safety boundaries, independent of Desktop Control Mode:
- A control whose accessible name suggests a committing action (send,
  submit, pay, delete, confirm, install, ...) always needs a separate
  confirmation, the same gate already used for force-quit and delete
  elsewhere in the desktop control system.
- A field whose accessible name or role suggests a credential is refused
  outright -- not even confirmable. The user types those themselves.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from tools.windows_ui_observer import ControlLookup, WindowInfo, WindowsUIObserver

_MAX_TYPE_LENGTH = 500

# Native controls (Notepad, Settings) reflect a typed value in their UIA
# tree the instant type_keys() returns. Electron/Chromium apps (Spotify,
# Discord, VS Code) update their accessibility tree asynchronously from the
# DOM, a beat behind what is already visible on screen -- reading the value
# back immediately can see a stale (often empty) value and wrongly report
# verification_failed for typing that actually landed. These settle the
# read, not the typing itself.
_VERIFY_SETTLE_ATTEMPTS = 3
_VERIFY_SETTLE_INTERVAL_SECONDS = 0.15

_COMMITTING_KEYWORDS = (
    "send", "submit", "post", "publish", "pay", "purchase", "buy",
    "checkout", "order", "confirm", "delete", "remove", "discard",
    "accept", "agree", "allow", "install", "uninstall", "unsubscribe",
    "sign out", "log out", "deactivate",
    # Matched against real Korean-language app UI during 4B.2 testing.
    # This is a best-effort allowlist, not a translation layer -- any
    # other language a control's accessible name appears in is still
    # only caught by the English keywords above.
    "전송", "보내기", "제출", "게시", "결제", "구매", "주문", "확인",
    "삭제", "제거", "수락", "동의", "허용", "설치", "로그아웃", "비활성화",
)

_CREDENTIAL_KEYWORDS = (
    "password", "passcode", "pin", "secret", "ssn", "social security",
    "credit card", "card number", "cvv", "cvc",
    "비밀번호", "암호", "신용카드", "카드 번호",
)

_SCROLLABLE_DIRECTIONS = frozenset({"up", "down", "left", "right"})

_CLICK_ROLES = frozenset({
    "Button", "CheckBox", "ComboBox", "Custom", "Edit", "Hyperlink",
    "ListItem", "MenuItem", "RadioButton", "Slider", "SplitButton",
    "TabItem", "TreeItem",
})
_TEXT_ROLES = frozenset({"Edit", "ComboBox", "Document"})
_SELECT_ROLES = frozenset({
    "ComboBox", "List", "ListItem", "Tree", "TreeItem",
})
_SCROLL_ROLES = frozenset({
    "DataGrid", "Document", "List", "Pane", "Table", "Tree",
})


def is_committing_control(name: str) -> bool:
    """True if clicking this control is a consequential, not-undoable step."""
    lowered = name.casefold()
    return any(keyword in lowered for keyword in _COMMITTING_KEYWORDS)


def is_credential_field(name: str) -> bool:
    """True if typing here would mean entering a credential for the user."""
    lowered = name.casefold()
    return any(keyword in lowered for keyword in _CREDENTIAL_KEYWORDS)


@dataclass(frozen=True)
class UIActionResult:
    status: str
    message: str
    window_title: str = ""
    control_name: str = ""
    # True means a readable postcondition proved the action, False means a
    # readable postcondition contradicted it, and None means the control does
    # not expose enough state to verify independently.
    verified: bool | None = None
    evidence: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {
            "focused", "clicked", "typed", "selected", "scrolled",
        }


class WindowsUIControl:
    """Focus, click, type, select, and scroll -- only on verified targets."""

    def __init__(self, *, observer: WindowsUIObserver | None = None) -> None:
        self.observer = observer or WindowsUIObserver()

    @property
    def available(self) -> bool:
        return self.observer.available

    def focus_window(self, title_query: str | WindowInfo) -> UIActionResult:
        window = self._require_window(title_query)
        if isinstance(window, UIActionResult):
            return window
        try:
            window.set_focus()
        except Exception as error:
            return UIActionResult("failed", f"I couldn't focus that window: {error}")
        title = self.observer._safe_text(window) or self._target_title(title_query)
        verified, evidence = self._verify_window_focus(window, title)
        if verified is False:
            return UIActionResult(
                "verification_failed",
                f"I asked {title} to come forward, but it is not focused.",
                window_title=title,
                verified=False,
                evidence=evidence,
            )
        return UIActionResult(
            "focused",
            f"Focused {title}.",
            window_title=title,
            verified=verified,
            evidence=evidence,
        )

    def click_control(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        *,
        confirmed: bool = False,
    ) -> UIActionResult:
        window = self._require_window(title_query)
        if isinstance(window, UIActionResult):
            return window
        window_title = (
            self.observer._safe_text(window) or self._target_title(title_query)
        )

        lookup = self.observer.resolve_control(
            window, control_name, expected_roles=_CLICK_ROLES,
        )
        if lookup.status != "matched":
            return self._lookup_failure(lookup, window_title, control_name)
        control = lookup.control
        real_name = lookup.name or control_name

        if is_committing_control(real_name) and not confirmed:
            return UIActionResult(
                "confirmation_required",
                f"Clicking {real_name!r} needs confirmation first.",
                window_title=window_title,
                control_name=real_name,
            )

        before_toggle = self._read_toggle_state(control)
        try:
            self._invoke(control)
        except Exception as error:
            return UIActionResult(
                "failed",
                f"I couldn't click {real_name!r}: {error}",
                window_title=window_title,
                control_name=real_name,
            )
        verified, evidence = self._verify_click(control, before_toggle)
        return UIActionResult(
            "clicked",
            f"Clicked {real_name}.",
            window_title=window_title,
            control_name=real_name,
            verified=verified,
            evidence=evidence,
        )

    def type_text(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        text: str,
    ) -> UIActionResult:
        window = self._require_window(title_query)
        if isinstance(window, UIActionResult):
            return window
        window_title = (
            self.observer._safe_text(window) or self._target_title(title_query)
        )

        lookup = self.observer.resolve_control(
            window, control_name, expected_roles=_TEXT_ROLES,
        )
        if lookup.status != "matched":
            return self._lookup_failure(lookup, window_title, control_name)
        control = lookup.control
        role = lookup.role
        real_name = lookup.name or control_name

        if is_credential_field(real_name):
            return UIActionResult(
                "refused",
                (
                    f"{real_name!r} looks like a credential field -- "
                    "please enter that yourself."
                ),
                window_title=window_title,
                control_name=real_name,
            )
        if role not in _TEXT_ROLES:
            return UIActionResult(
                "refused",
                f"{real_name!r} isn't a text field I can type into.",
                window_title=window_title,
                control_name=real_name,
            )

        text = str(text)[:_MAX_TYPE_LENGTH]
        before_value = self._read_text_value(control)
        try:
            control.set_focus()
            # Measured directly against this system's Notepad: without a
            # pause, simulated keystrokes arrive faster than the app's
            # input handling can keep up, and characters are silently
            # dropped -- "Phase 4B.2 planner test" landed as just "Phase".
            # That's a correctness bug, not a cosmetic one; the small
            # latency cost is worth never silently typing the wrong text.
            control.type_keys(
                text, with_spaces=True, with_tabs=False, pause=0.03,
            )
        except Exception as error:
            return UIActionResult(
                "failed",
                f"I couldn't type into {real_name!r}: {error}",
                window_title=window_title,
                control_name=real_name,
            )
        verified, evidence = self._verify_typed_text_with_settle(
            control, text, before_value,
        )
        if verified is False:
            return UIActionResult(
                "verification_failed",
                (
                    f"Typing was sent to {real_name}, but the field did not "
                    "report the requested text."
                ),
                window_title=window_title,
                control_name=real_name,
                verified=False,
                evidence=evidence,
            )
        return UIActionResult(
            "typed",
            f"Typed into {real_name}.",
            window_title=window_title,
            control_name=real_name,
            verified=verified,
            evidence=evidence,
        )

    def select_option(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        option: str,
    ) -> UIActionResult:
        window = self._require_window(title_query)
        if isinstance(window, UIActionResult):
            return window
        window_title = (
            self.observer._safe_text(window) or self._target_title(title_query)
        )

        lookup = self.observer.resolve_control(
            window, control_name, expected_roles=_SELECT_ROLES,
        )
        if lookup.status != "matched":
            return self._lookup_failure(lookup, window_title, control_name)
        control = lookup.control
        real_name = lookup.name or control_name

        try:
            control.select(option)
        except Exception as error:
            return UIActionResult(
                "failed",
                f"I couldn't select {option!r} in {real_name!r}: {error}",
                window_title=window_title,
                control_name=real_name,
            )
        verified, evidence = self._verify_selected_option(control, option)
        if verified is False:
            return UIActionResult(
                "verification_failed",
                f"{real_name} did not report {option!r} as selected.",
                window_title=window_title,
                control_name=real_name,
                verified=False,
                evidence=evidence,
            )
        return UIActionResult(
            "selected",
            f"Selected {option} in {real_name}.",
            window_title=window_title,
            control_name=real_name,
            verified=verified,
            evidence=evidence,
        )

    def scroll_control(
        self,
        title_query: str | WindowInfo,
        control_name: str,
        direction: str,
    ) -> UIActionResult:
        direction = direction.strip().casefold()
        if direction not in _SCROLLABLE_DIRECTIONS:
            return UIActionResult(
                "failed",
                f"{direction!r} isn't a scroll direction I understand.",
            )
        window = self._require_window(title_query)
        if isinstance(window, UIActionResult):
            return window
        window_title = (
            self.observer._safe_text(window) or self._target_title(title_query)
        )

        lookup = self.observer.resolve_control(
            window, control_name, expected_roles=_SCROLL_ROLES,
        )
        if lookup.status != "matched":
            return self._lookup_failure(lookup, window_title, control_name)
        control = lookup.control
        real_name = lookup.name or control_name

        before_position = self._read_scroll_position(control, direction)
        try:
            control.scroll(direction, "line")
        except Exception as error:
            return UIActionResult(
                "failed",
                f"I couldn't scroll {real_name!r}: {error}",
                window_title=window_title,
                control_name=real_name,
            )
        after_position = self._read_scroll_position(control, direction)
        verified = (
            True
            if before_position is not None
            and after_position is not None
            and before_position != after_position
            else None
        )
        evidence = (
            "The control's reported scroll position changed."
            if verified
            else "The control exposes no changed scroll position to verify."
        )
        return UIActionResult(
            "scrolled",
            f"Scrolled {real_name} {direction}.",
            window_title=window_title,
            control_name=real_name,
            verified=verified,
            evidence=evidence,
        )

    def _require_window(self, title_query: str | WindowInfo) -> Any:
        """Return the live window, or a ready-to-return failure result."""
        if not self.available:
            return UIActionResult(
                "unavailable", "Desktop control isn't available on this system."
            )
        window = self.observer.find_window(title_query)
        if window is None:
            return UIActionResult(
                "not_found",
                f"I couldn't find the captured window "
                f"{self._target_title(title_query)!r}.",
            )
        return window

    @staticmethod
    def _target_title(target: str | WindowInfo) -> str:
        return target.title if isinstance(target, WindowInfo) else str(target)

    @staticmethod
    def _lookup_failure(
        lookup: ControlLookup,
        window_title: str,
        control_name: str,
    ) -> UIActionResult:
        status = lookup.status if lookup.status in {
            "ambiguous", "unsafe_match", "error", "invalid",
        } else "not_found"
        return UIActionResult(
            status,
            f"In {window_title}, {lookup.message}",
            window_title=window_title,
            control_name=control_name,
            verified=False,
            evidence=lookup.message,
        )

    def _verify_window_focus(
        self,
        window: Any,
        title: str,
    ) -> tuple[bool | None, str]:
        direct = self._call_bool(window, "has_focus")
        if direct is not None:
            return direct, (
                "The target window reports keyboard focus."
                if direct
                else "The target window reports that it does not have focus."
            )
        active = self.observer.get_active_window()
        if active is not None:
            handle = self.observer._safe_handle(window)
            if handle is not None and active.handle is not None:
                matched = handle == active.handle
            else:
                matched = _normalized(active.title) == _normalized(title)
            return matched, (
                "The foreground-window snapshot matches the target."
                if matched
                else "The foreground-window snapshot names another window."
            )
        return None, "The system did not expose a foreground-window postcondition."

    @classmethod
    def _verify_click(
        cls,
        control: Any,
        before_toggle: object,
    ) -> tuple[bool | None, str]:
        try:
            exists = control.exists(timeout=0)
        except Exception:
            exists = None
        if exists is False:
            return True, "The invoked control left the accessible tree."
        after_toggle = cls._read_toggle_state(control)
        if (
            before_toggle is not None
            and after_toggle is not None
            and before_toggle != after_toggle
        ):
            return True, "The control's toggle or checked state changed."
        return None, "The invoke completed, but the control exposes no changed state."

    @classmethod
    def _verify_typed_text(
        cls,
        control: Any,
        expected: str,
        before: tuple[str, str, bool] | None,
    ) -> tuple[bool | None, str]:
        after = cls._read_text_value(control)
        if after is None:
            return None, "The field exposes no readable value after typing."
        after_value, source, high_confidence = after
        contains_expected = _normalized(expected) in _normalized(after_value)
        changed = before is None or after_value != before[0]
        if contains_expected and (changed or not expected):
            return True, (
                f"{source} contains all {len(expected)} requested characters."
            )
        if high_confidence:
            return False, (
                f"{source} was readable but did not contain all requested characters."
            )
        return None, (
            f"{source} did not provide a conclusive text postcondition."
        )

    @classmethod
    def _verify_typed_text_with_settle(
        cls,
        control: Any,
        expected: str,
        before: tuple[str, str, bool] | None,
    ) -> tuple[bool | None, str]:
        """Poll briefly before accepting a verification result.

        Native controls (Notepad) reflect a typed value the instant
        type_keys() returns, but Electron/Chromium apps (Spotify, Discord,
        VS Code) update their accessibility tree a beat behind the DOM --
        the very first read can see a stale, often empty value. A True
        result returns immediately; a False result is only accepted after
        every attempt still disagrees, since a slow app just needs another
        moment, not an early failure.
        """
        result: tuple[bool | None, str] = (None, "")
        for attempt in range(_VERIFY_SETTLE_ATTEMPTS):
            result = cls._verify_typed_text(control, expected, before)
            if result[0] is True:
                return result
            if attempt < _VERIFY_SETTLE_ATTEMPTS - 1:
                time.sleep(_VERIFY_SETTLE_INTERVAL_SECONDS)
        return result

    @classmethod
    def _verify_selected_option(
        cls,
        control: Any,
        expected: str,
    ) -> tuple[bool | None, str]:
        selected = cls._read_selected_value(control)
        if selected is None:
            return None, "The control exposes no readable selected option."
        value, source, high_confidence = selected
        if _normalized(expected) in _normalized(value):
            return True, f"{source} reports the requested option as selected."
        if high_confidence:
            return False, f"{source} reports a different selected option."
        return None, f"{source} did not provide a conclusive selection postcondition."

    @staticmethod
    def _read_text_value(control: Any) -> tuple[str, str, bool] | None:
        try:
            return str(control.get_value()), "UI Automation value", True
        except Exception:
            pass
        try:
            return str(control.iface_value.CurrentValue), "UI Automation value", True
        except Exception:
            pass
        try:
            properties = control.legacy_properties()
            if "Value" in properties:
                return str(properties["Value"]), "legacy accessible value", True
        except Exception:
            pass
        try:
            return str(control.window_text()), "accessible text", False
        except Exception:
            return None

    @staticmethod
    def _read_selected_value(control: Any) -> tuple[str, str, bool] | None:
        try:
            return str(control.selected_text()), "selected-text state", True
        except Exception:
            pass
        try:
            selection = control.get_selection()
            names: list[str] = []
            for item in selection or ():
                try:
                    names.append(str(item.window_text()))
                except Exception:
                    names.append(str(item))
            return " ".join(names), "selection state", True
        except Exception:
            pass
        return WindowsUIControl._read_text_value(control)

    @staticmethod
    def _read_toggle_state(control: Any) -> object:
        for method_name in ("get_toggle_state", "get_check_state"):
            try:
                return getattr(control, method_name)()
            except Exception:
                continue
        return None

    @staticmethod
    def _read_scroll_position(control: Any, direction: str) -> float | None:
        attribute = (
            "CurrentVerticalScrollPercent"
            if direction in {"up", "down"}
            else "CurrentHorizontalScrollPercent"
        )
        try:
            return float(getattr(control.iface_scroll, attribute))
        except Exception:
            return None

    @staticmethod
    def _call_bool(target: Any, method_name: str) -> bool | None:
        try:
            return bool(getattr(target, method_name)())
        except Exception:
            return None

    @staticmethod
    def _invoke(control: Any) -> None:
        """Prefer the UIA Invoke pattern; fall back to a real click."""
        try:
            control.invoke()
            return
        except Exception:
            pass
        control.click_input()


def _normalized(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", normalized).strip()
