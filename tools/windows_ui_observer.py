"""Read-only Windows UI Automation observation (Phase 4B.1).

This module only ever looks -- it never clicks, types, focuses, or changes
any window state. That is a deliberate scope boundary: Phase 4B.1 is the
"observe" half of observe-then-act, and it must be safe to run automatically
under Desktop Control Mode without any confirmation, the same way "Inspect
windows and controls" is marked Automatic in the permissions table. Acting on
what is observed here is Phase 4B.2 and belongs in a separate, more careful
module once this one is proven reliable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from tools.app_name_aliases import alias_candidates as _alias_candidates

try:
    from pywinauto import Desktop as _Desktop
except Exception:  # pragma: no cover - exercised only when pywinauto is absent
    _Desktop = None

try:
    import win32gui as _win32gui
except Exception:  # pragma: no cover - exercised only when pywin32 is absent
    _win32gui = None


# A single accessible element's name can legitimately contain an entire
# paragraph (a text pane, a chat message, a status line). Measured directly
# against a real, ordinary desktop window during development: one window
# produced well over 100 named elements, several with names hundreds of
# characters long. Both caps exist so one observation can never flood the
# model's context or the spoken response with more than a screenful of
# genuinely useful, scannable information.
_MAX_NAME_LENGTH = 80
_MAX_ELEMENTS = 80
_MAX_SCANNED_ELEMENTS = 1500

# UI Automation trees are usually arranged for layout, not usefulness.  A
# Spotify-sized Electron tree can contain hundreds of named Text/Pane nodes
# before its search box.  Rank controls a person can actually operate first,
# while still retaining named containers and labels as lower-priority context.
_INTERACTIVE_ROLES = frozenset({
    "button", "checkbox", "combobox", "edit", "hyperlink", "listitem",
    "menuitem", "radiobutton", "scrollbar", "slider", "spinner",
    "splitbutton", "tabitem", "thumb", "treeitem",
})
_CONTAINER_ROLES = frozenset({
    "datagrid", "document", "group", "list", "menu", "pane", "tab",
    "table", "toolbar", "tree", "window",
})


def _normalize_accessible_text(value: str) -> str:
    """Normalize UIA names without discarding Korean or other scripts."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _role_key(role: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(role or "").casefold())


@dataclass(frozen=True)
class WindowInfo:
    title: str
    app_name: str = ""
    is_active: bool = False
    # These stable identifiers let the planner freeze a surface at utterance
    # time instead of relying on a title that can change or be duplicated.
    handle: int | None = None
    process_id: int | None = None
    class_name: str = ""

    @property
    def identity(self) -> str:
        if self.handle is not None:
            return f"hwnd:{self.handle}"
        if self.process_id is not None:
            return f"pid:{self.process_id}:{self.title}"
        return f"title:{self.title}"


@dataclass(frozen=True)
class ControlInfo:
    role: str
    name: str
    value: str = ""
    is_visible: bool | None = None
    is_enabled: bool | None = None
    is_actionable: bool = False


@dataclass(frozen=True)
class ControlLookup:
    """Diagnostic result for a safe control-name resolution attempt."""

    status: str
    control: Any = None
    role: str = ""
    name: str = ""
    candidates: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class WindowObservation:
    status: str
    title: str = ""
    controls: tuple[ControlInfo, ...] = ()
    truncated: bool = False
    message: str = ""

    def as_tree_text(self) -> str:
        if self.status != "observed":
            return self.message
        lines = [f"Window: {self.title}"]
        for control in self.controls:
            value_suffix = f", value {control.value}" if control.value else ""
            state = " [disabled]" if control.is_enabled is False else ""
            lines.append(
                f"- {control.role}: {control.name}{value_suffix}{state}"
            )
        if self.truncated:
            lines.append(
                f"... more controls exist beyond the first {len(self.controls)}"
            )
        return "\n".join(lines)


class WindowsUIObserver:
    """List windows and describe one window's accessible control tree."""

    def __init__(self, *, desktop: Any = None, foreground_window: Any = None) -> None:
        if desktop is not None:
            self._desktop = desktop
        elif _Desktop is not None:
            self._desktop = _Desktop(backend="uia")
        else:
            self._desktop = None
        self._uses_native_foreground = (
            foreground_window is None and _win32gui is not None
        )
        self._foreground_window = foreground_window or (
            self._win32_foreground_title if _win32gui is not None else None
        )

    @property
    def available(self) -> bool:
        return self._desktop is not None

    def list_windows(self) -> tuple[WindowInfo, ...]:
        """Enumerate visible top-level windows. Never raises."""
        if not self.available:
            return ()
        active_title = self._active_title()
        active_handle = self._active_handle()
        windows: list[WindowInfo] = []
        try:
            raw_windows = self._desktop.windows()
        except Exception:
            return ()
        for window in raw_windows:
            title = self._safe_text(window)
            if not title:
                continue
            class_name = self._safe_class_name(window)
            handle = self._safe_handle(window)
            process_id = self._safe_process_id(window)
            windows.append(
                WindowInfo(
                    title=title,
                    # Keep app_name's existing class-name value for callers
                    # that already rely on it; class_name makes that meaning
                    # explicit for new surface-binding code.
                    app_name=class_name,
                    is_active=(
                        handle is not None
                        and active_handle is not None
                        and handle == active_handle
                    ) or (
                        active_handle is None
                        and bool(active_title)
                        and title == active_title
                    ),
                    handle=handle,
                    process_id=process_id,
                    class_name=class_name,
                )
            )
        return tuple(windows)

    def get_active_window(self) -> WindowInfo | None:
        for window in self.list_windows():
            if window.is_active:
                return window
        return None

    def describe_window(
        self,
        title_query: str | WindowInfo,
    ) -> WindowObservation:
        """Return the named, interactive controls inside one matched window."""
        if not self.available:
            return WindowObservation(
                status="unavailable",
                message=(
                    "Desktop observation isn't available on this system."
                ),
            )

        query = (
            title_query.title
            if isinstance(title_query, WindowInfo)
            else str(title_query).strip()
        )
        target = self.find_window(title_query)
        if target is None:
            return WindowObservation(
                status="not_found",
                title=title_query,
                message=f"I couldn't find a window matching {title_query!r}.",
            )

        title = self._safe_text(target) or title_query
        try:
            descendants = target.descendants()
        except Exception as error:
            return WindowObservation(
                status="error",
                title=title,
                message=f"I couldn't inspect that window: {error}",
            )

        ranked_by_key: dict[
            tuple[str, str, str], tuple[int, int, ControlInfo]
        ] = {}
        scan_truncated = False
        for index, element in enumerate(descendants):
            if index >= _MAX_SCANNED_ELEMENTS:
                scan_truncated = True
                break
            role, name = self._safe_role_and_name(element)
            if not name:
                # An element a planner could select "by meaning" needs a
                # name; unnamed containers are decorative nesting noise.
                continue
            visible = self._safe_state(element, "is_visible", "visible")
            if visible is False:
                continue
            enabled = self._safe_state(element, "is_enabled", "enabled")
            role = role or "Control"
            key = (
                _role_key(role),
                _normalize_accessible_text(name),
                "",
            )
            actionable = self._is_actionable(role, visible, enabled)
            role_key = _role_key(role)
            if actionable:
                priority = 3
            elif role_key in _CONTAINER_ROLES:
                priority = 2
            else:
                priority = 1
            if enabled is False:
                priority -= 1
            candidate = (
                priority,
                index,
                ControlInfo(
                    role=role,
                    name=name[:_MAX_NAME_LENGTH],
                    is_visible=visible,
                    is_enabled=enabled,
                    is_actionable=actionable,
                ),
            )
            previous = ranked_by_key.get(key)
            if previous is None or candidate[0] > previous[0]:
                ranked_by_key[key] = candidate

        ranked_controls = list(ranked_by_key.values())
        ranked_controls.sort(key=lambda item: (-item[0], item[1]))
        truncated = scan_truncated or len(ranked_controls) > _MAX_ELEMENTS
        controls = [item[2] for item in ranked_controls[:_MAX_ELEMENTS]]

        if not controls:
            return WindowObservation(
                status="empty",
                title=title,
                message=(
                    f"{title} doesn't expose an accessible control tree "
                    "(common for games, custom-rendered UIs, or protected "
                    "surfaces). Elaina can't inspect its controls this way."
                ),
            )

        return WindowObservation(
            status="observed",
            title=title,
            controls=tuple(controls),
            truncated=truncated,
        )

    def find_window(self, title_query: str | WindowInfo) -> Any:
        """Return the first live window whose title contains the query.

        Tries the literal query first, then -- only if that finds nothing --
        any known translation of a common system app name (see
        _APP_NAME_ALIASES), so asking for "Notepad" still finds a window
        actually titled "메모장" on a Korean-locale system.

        A captured ``WindowInfo`` is resolved strictly by its stable handle,
        process, and class metadata. It never falls back to another window
        that merely reused the same title, which lets action code freeze the
        user's active surface safely.
        """
        try:
            windows = list(self._desktop.windows())
        except Exception:
            return None
        if isinstance(title_query, WindowInfo):
            return self._find_captured_window(windows, title_query)
        query = str(title_query).casefold()
        if not query:
            return None
        match = self._first_matching(windows, query)
        if match is not None:
            return match
        for alias in _alias_candidates(query):
            match = self._first_matching(windows, alias)
            if match is not None:
                return match
        return None

    def _find_captured_window(
        self,
        windows: list[Any],
        snapshot: WindowInfo,
    ) -> Any:
        has_stable_identity = (
            snapshot.handle is not None or snapshot.process_id is not None
        )
        if not has_stable_identity:
            return self._first_matching(windows, snapshot.title.casefold())

        candidates: list[Any] = []
        for window in windows:
            if (
                snapshot.handle is not None
                and self._safe_handle(window) != snapshot.handle
            ):
                continue
            if (
                snapshot.process_id is not None
                and self._safe_process_id(window) != snapshot.process_id
            ):
                continue
            if (
                snapshot.class_name
                and self._safe_class_name(window) != snapshot.class_name
            ):
                continue

            # A PID identifies a process, not a top-level surface. Browsers
            # and Electron apps can expose several windows with the same PID
            # and class, so a handle-less snapshot must also match the title
            # captured at utterance time. If the title changed, it is safer to
            # make the planner observe again than to act on a sibling window.
            if snapshot.handle is None:
                captured_title = _normalize_accessible_text(snapshot.title)
                live_title = _normalize_accessible_text(self._safe_text(window))
                if not captured_title or live_title != captured_title:
                    continue
            candidates.append(window)

        # Handles should be unique, and PID/title/class can still collide.
        # Never pick whichever matching surface happened to enumerate first.
        return candidates[0] if len(candidates) == 1 else None

    def _first_matching(self, windows: list[Any], query: str) -> Any:
        for window in windows:
            title = self._safe_text(window)
            if title and query in title.casefold():
                return window
        return None

    def find_control(
        self,
        window: Any,
        control_name: str,
        *,
        expected_roles: Iterable[str] | None = None,
    ) -> Any:
        """
        Return one unambiguous live descendant matching ``control_name``.

        Existing callers still receive the element-or-None interface.  New
        callers that need to explain ambiguity should use ``resolve_control``.
        """
        return self.resolve_control(
            window,
            control_name,
            expected_roles=expected_roles,
        ).control

    def resolve_control(
        self,
        window: Any,
        control_name: str,
        *,
        expected_roles: Iterable[str] | None = None,
    ) -> ControlLookup:
        """Rank live controls and refuse unsafe substring or ambiguous ties."""
        query = _normalize_accessible_text(control_name)
        if not query or window is None:
            return ControlLookup(
                "invalid",
                message="A non-empty control name and a real window are required.",
            )
        try:
            descendants = window.descendants()
        except Exception:
            return ControlLookup(
                "error", message="The window's controls could not be inspected."
            )

        expected = {_role_key(role) for role in (expected_roles or ()) if role}
        query_variants = [query]
        for alias in _alias_candidates(query):
            normalized_alias = _normalize_accessible_text(alias)
            if normalized_alias and normalized_alias not in query_variants:
                query_variants.append(normalized_alias)

        # (rank, traversal order, identity, element, role, name)
        matches: list[tuple[tuple[int, int, int, int], int, object, Any, str, str]] = []
        seen_elements: set[object] = set()
        for index, element in enumerate(descendants):
            if index >= _MAX_SCANNED_ELEMENTS:
                break
            role, name = self._safe_role_and_name(element)
            if not name:
                continue
            visible = self._safe_state(element, "is_visible", "visible")
            enabled = self._safe_state(element, "is_enabled", "enabled")
            # Acting on a known hidden or disabled element is never useful,
            # and can accidentally hit an off-screen duplicate.
            if visible is False or enabled is False:
                continue
            normalized_name = _normalize_accessible_text(name)
            match_quality = self._name_match_quality(
                normalized_name, query_variants
            )
            if not match_quality:
                continue
            identity = self._element_identity(element)
            if identity in seen_elements:
                continue
            seen_elements.add(identity)
            role_key = _role_key(role)
            role_quality = 2 if role_key in expected else 0
            actionable = self._is_actionable(role, visible, enabled)
            action_quality = 1 if actionable else 0
            state_quality = int(visible is True) + int(enabled is True)
            matches.append((
                (match_quality, role_quality, action_quality, state_quality),
                index,
                identity,
                element,
                role,
                name,
            ))

        if not matches:
            return ControlLookup(
                "not_found",
                message=f"No visible, enabled control matches {control_name!r}.",
            )

        matches.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        best_rank = matches[0][0]
        best = [candidate for candidate in matches if candidate[0] == best_rank]
        if len(best) > 1:
            labels = tuple(
                f"{role or 'Control'}: {name}" for _, _, _, _, role, name in best[:5]
            )
            return ControlLookup(
                "ambiguous",
                candidates=labels,
                message=(
                    f"More than one equally suitable control matches "
                    f"{control_name!r}: {', '.join(labels)}."
                ),
            )

        match_quality, _, _, _ = best_rank
        # One- and two-character partial queries (for example "s" or "se")
        # are too easy for a model to guess. Exact short labels such as an X
        # close button remain usable.
        if len(query) < 3 and match_quality < 8:
            return ControlLookup(
                "unsafe_match",
                message=(
                    f"{control_name!r} is too short for a safe partial "
                    "control-name match."
                ),
            )

        _, _, _, element, role, name = best[0]
        return ControlLookup(
            "matched", control=element, role=role, name=name,
            message=f"Matched {role or 'Control'} {name!r}.",
        )

    @staticmethod
    def _name_match_quality(name: str, query_variants: list[str]) -> int:
        best = 0
        for position, query in enumerate(query_variants):
            alias_penalty = 1 if position else 0
            if name == query:
                quality = 4
            elif name.startswith(query + " ") or name.endswith(" " + query):
                quality = 3
            elif re.search(rf"(?<!\w){re.escape(query)}(?!\w)", name):
                quality = 3
            elif query in name:
                quality = 2
            else:
                continue
            best = max(best, quality * 2 - alias_penalty)
        return best

    def _active_title(self) -> str:
        if self._foreground_window is None:
            return ""
        try:
            return str(self._foreground_window()).strip()
        except Exception:
            return ""

    def _active_handle(self) -> int | None:
        if not self._uses_native_foreground or _win32gui is None:
            return None
        try:
            return int(_win32gui.GetForegroundWindow())
        except Exception:
            return None

    @staticmethod
    def _win32_foreground_title() -> str:
        handle = _win32gui.GetForegroundWindow()
        return str(_win32gui.GetWindowText(handle))

    @staticmethod
    def _safe_text(window: Any) -> str:
        try:
            return str(window.window_text()).strip()
        except Exception:
            return ""

    @staticmethod
    def _safe_class_name(window: Any) -> str:
        try:
            return str(window.friendly_class_name())
        except Exception:
            return ""

    @staticmethod
    def _safe_handle(window: Any) -> int | None:
        try:
            raw = getattr(window, "handle")
            raw = raw() if callable(raw) else raw
            handle = int(raw) if raw is not None else 0
            return handle if handle > 0 else None
        except Exception:
            pass
        try:
            raw = window.element_info.handle
            handle = int(raw) if raw is not None else 0
            return handle if handle > 0 else None
        except Exception:
            return None

    @staticmethod
    def _safe_process_id(window: Any) -> int | None:
        try:
            raw = getattr(window, "process_id")
            raw = raw() if callable(raw) else raw
            process_id = int(raw) if raw is not None else 0
            return process_id if process_id > 0 else None
        except Exception:
            pass
        try:
            raw = window.element_info.process_id
            process_id = int(raw) if raw is not None else 0
            return process_id if process_id > 0 else None
        except Exception:
            return None

    @staticmethod
    def _safe_state(
        element: Any,
        method_name: str,
        info_attribute: str,
    ) -> bool | None:
        try:
            method = getattr(element, method_name)
            return bool(method())
        except Exception:
            pass
        try:
            value = getattr(element.element_info, info_attribute)
            return bool(value)
        except Exception:
            return None

    @staticmethod
    def _is_actionable(
        role: str,
        visible: bool | None,
        enabled: bool | None,
    ) -> bool:
        return (
            _role_key(role) in _INTERACTIVE_ROLES
            and visible is not False
            and enabled is not False
        )

    @classmethod
    def _element_identity(cls, element: Any) -> object:
        """Use UIA identity where exposed, falling back to Python identity."""
        try:
            runtime_id = tuple(element.element_info.runtime_id)
            if runtime_id:
                return ("runtime", runtime_id)
        except Exception:
            pass
        handle = cls._safe_handle(element)
        if handle is not None:
            return ("handle", handle)
        return ("object", id(element))

    @staticmethod
    def _safe_role_and_name(element: Any) -> tuple[str, str]:
        try:
            info = element.element_info
            role = str(info.control_type or "").strip()
            name = str(info.name or "").strip()
            return role, name
        except Exception:
            return "", ""
