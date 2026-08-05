"""Outcome-checked, structured computer actions for authorized Elaina turns."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from security.policy import PolicyEngine
from tools.safe_browser import SafeBrowserControl
from tools.safe_filesystem import SafeFilesystemControl
from tools.windows_app_catalog import (
    AppEntry,
    AppResolution,
    WindowsAppCatalog,
    normalize_app_name,
)
from tools.windows_process_control import WindowsProcessControl
from tools.windows_ui_observer import WindowInfo, WindowsUIObserver


_GENERIC_TARGET_WORDS = {"app", "application", "launcher", "the"}
_COMMON_URL_SUFFIXES = {"com", "org", "net", "io", "co", "gov", "edu", "dev"}

COMPUTER_OPERATIONS = frozenset({
    "none",
    "open_app",
    "close_app",
    "force_quit_app",
    "open_url",
    "create_file",
    "create_folder",
    "delete_file",
    "delete_folder",
    "list_windows",
    "describe_window",
    "ui_action",
    "unsupported",
})

# Phase 4B.1: pure observation, never a state change. Always safe to run the
# instant Desktop Control Mode is on -- these can never enter
# HIGH_RISK_OPERATIONS or need a confirmation turn.
OBSERVATION_OPERATIONS = frozenset({"list_windows", "describe_window"})

# Phase 4B.2: a natural-language UI request (click/type/focus/select/scroll)
# handled by brain.desktop_action_planner's tool-calling loop rather than a
# single structured operation -- the loop itself resolves the real window
# and control names and enforces per-step confirmation, so this operation
# has no single named target to ground the way open_app/open_url do.
UI_ACTION_OPERATIONS = frozenset({"ui_action"})
# ui_action itself never runs through ComputerControl.execute()'s generic
# HIGH_RISK gate below -- brain.chat_engine calls DesktopActionPlanner
# directly, and the planner/WindowsUIControl decide per-control whether a
# specific click needs confirmation. It's listed here only so
# ComputerConsentGate.offer() (which checks this same set) will accept a
# pending ui_action confirmation for a click the planner flagged as
# committing.
HIGH_RISK_OPERATIONS = frozenset({
    "force_quit_app",
    "delete_file",
    "delete_folder",
    "ui_action",
})


def transcript_names_target(transcript: str, target: str) -> bool:
    """Ensure the model cannot silently substitute a different target."""
    transcript_name = normalize_app_name(transcript)
    target_words = [
        word
        for word in re.findall(r"[^\W_]+", str(target).casefold())
        if word not in _GENERIC_TARGET_WORDS
    ]
    # A model may reasonably complete a spoken site name with its standard
    # domain suffix ("github" -> "github.com"). Strip one trailing suffix
    # like that before grounding, so the check still requires the actual
    # name the user said, not a TLD they never spoke.
    if len(target_words) > 1 and target_words[-1] in _COMMON_URL_SUFFIXES:
        target_words = target_words[:-1]
    target_name = normalize_app_name("".join(target_words))
    return bool(target_name and target_name in transcript_name)


def transcript_names_location(transcript: str, location: str) -> bool:
    """Require every model-provided filesystem location to be user-grounded."""
    normalized_location = normalize_app_name(location)
    return bool(
        normalized_location
        and normalized_location in normalize_app_name(transcript)
    )


@dataclass(frozen=True)
class ComputerActionRequest:
    operation: str
    target: str
    location: str = ""
    url: str = ""


@dataclass(frozen=True)
class PreparedComputerAction:
    """Exact locally resolved payload stored across a consent turn."""

    operation: str
    target: str
    display_name: str
    entry_id: str = ""
    path: str = ""
    url: str = ""
    # Phase 4B.2: which window a ui_action control lives in, so a confirmed
    # click can be re-resolved and re-verified rather than replayed blind.
    window_title: str = ""
    window_snapshot: WindowInfo | None = None

    @property
    def request(self) -> str:
        verbs = {
            "open_app": "Open",
            "close_app": "Close",
            "force_quit_app": "Force quit",
            "open_url": "Open",
            "create_file": "Create file",
            "create_folder": "Create folder",
            "delete_file": "Delete file",
            "delete_folder": "Delete folder",
            "ui_action": "Click",
        }
        return f"{verbs.get(self.operation, 'Run')} {self.display_name}".strip()

    @property
    def consent_subject(self) -> str:
        verbs = {
            "open_app": "open",
            "close_app": "close",
            "open_url": "open",
            "create_file": "create",
            "create_folder": "create",
            "delete_file": "delete",
            "delete_folder": "delete",
            "ui_action": "click",
        }
        verb = verbs.get(self.operation, self.operation.replace("_", " "))
        return f"{verb} {self.display_name}".strip()


@dataclass(frozen=True)
class ComputerActionResult:
    status: str
    target: str
    display_name: str
    message: str
    operation: str = ""
    candidates: tuple[str, ...] = ()
    entry_id: str = ""
    path: str = ""
    url: str = ""
    error: str = ""
    prepared: PreparedComputerAction | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in {
            "opened",
            "closed",
            "close_requested",
            "force_quit",
            "url_opened",
            "file_created",
            "folder_created",
            "file_deleted",
            "folder_deleted",
            "windows_listed",
            "window_described",
            "ui_action_done",
        }


class ComputerControl:
    """Prepare exact actions locally, then execute only prepared descriptors."""

    def __init__(
        self,
        policy: PolicyEngine,
        *,
        enabled: bool = True,
        catalog: WindowsAppCatalog | None = None,
        launcher: Callable[[AppEntry], object] | None = None,
        processes: WindowsProcessControl | None = None,
        browser: SafeBrowserControl | None = None,
        filesystem: SafeFilesystemControl | None = None,
        ui_observer: WindowsUIObserver | None = None,
    ) -> None:
        self.policy = policy
        self.enabled = bool(enabled)
        self.catalog = catalog or WindowsAppCatalog()
        self._launcher = launcher or self.catalog.launch
        self.processes = processes or WindowsProcessControl()
        self.browser = browser or SafeBrowserControl()
        self.filesystem = filesystem or SafeFilesystemControl()
        self.ui_observer = ui_observer or WindowsUIObserver()

    def prepare(self, request: ComputerActionRequest) -> ComputerActionResult:
        operation = str(request.operation).strip().lower()
        if operation not in COMPUTER_OPERATIONS or operation in {"none", "unsupported"}:
            return self._result(
                "blocked", request.target, "",
                "That computer action is not supported.", operation=operation,
            )
        self.policy.get(self._policy_name(operation))
        if not self.enabled:
            return self._result(
                "disabled", request.target, "",
                "Computer control is disabled in Elaina's configuration.",
                operation=operation,
            )

        if operation in OBSERVATION_OPERATIONS:
            # Nothing to resolve ahead of time; there is no side effect to
            # stage, so preparing and executing happen in the same step.
            prepared = PreparedComputerAction(
                operation=operation,
                target=request.target,
                display_name=request.target or "the desktop",
            )
            return self._result(
                "prepared", request.target, prepared.display_name,
                "Ready to look.", operation=operation, prepared=prepared,
            )

        if operation in {"open_app", "close_app", "force_quit_app"}:
            resolution = self.catalog.resolve(request.target)
            if resolution.status != "resolved" or resolution.entry is None:
                return self._from_app_resolution(resolution, operation)
            entry = resolution.entry
            prepared = PreparedComputerAction(
                operation=operation,
                target=request.target,
                display_name=entry.display_name,
                entry_id=entry.id,
            )
            return self._result(
                "prepared", request.target, entry.display_name,
                f"Ready to {operation.replace('_', ' ')} {entry.display_name}.",
                operation=operation, entry_id=entry.id, prepared=prepared,
            )

        if operation == "open_url":
            resolution = self.browser.resolve(request.target, request.url)
            if resolution.status != "resolved":
                return self._result(
                    resolution.status, request.target, request.target,
                    resolution.message, operation=operation,
                )
            prepared = PreparedComputerAction(
                operation=operation,
                target=request.target,
                display_name=request.target,
                url=resolution.url,
            )
            return self._result(
                "prepared", request.target, request.target, resolution.message,
                operation=operation, url=resolution.url, prepared=prepared,
            )

        if operation in {"create_file", "create_folder"}:
            file_resolution = self.filesystem.resolve_creation(
                name=request.target,
                location=request.location,
            )
        else:
            file_resolution = self.filesystem.resolve_deletion(
                name=request.target,
                location=request.location,
                expected_kind=(
                    "file" if operation == "delete_file" else "folder"
                ),
            )
        if file_resolution.status != "resolved":
            return self._result(
                file_resolution.status, request.target, request.target,
                file_resolution.message, operation=operation,
                path=file_resolution.path,
            )
        prepared = PreparedComputerAction(
            operation=operation,
            target=request.target,
            display_name=request.target,
            path=file_resolution.path,
        )
        return self._result(
            "prepared", request.target, request.target, file_resolution.message,
            operation=operation, path=file_resolution.path, prepared=prepared,
        )

    def execute(
        self,
        prepared: PreparedComputerAction,
        *,
        confirmed: bool = False,
    ) -> ComputerActionResult:
        operation = prepared.operation
        self.policy.get(self._policy_name(operation))
        if operation in HIGH_RISK_OPERATIONS and not confirmed:
            return self._result(
                "confirmation_required",
                prepared.target,
                prepared.display_name,
                "Force quit requires a separate confirmation.",
                operation=operation,
                entry_id=prepared.entry_id,
            )
        if not self.enabled:
            return self._result(
                "disabled", prepared.target, prepared.display_name,
                "Computer control is disabled in Elaina's configuration.",
                operation=operation,
            )
        try:
            if operation == "list_windows":
                windows = self.ui_observer.list_windows()
                if not windows:
                    return self._result(
                        "windows_listed", prepared.target, prepared.display_name,
                        "I don't see any open windows right now.",
                        operation=operation,
                    )
                summary = "; ".join(
                    f"{window.title}{' (active)' if window.is_active else ''}"
                    for window in windows
                )
                return self._result(
                    "windows_listed", prepared.target, prepared.display_name,
                    f"Open windows: {summary}.", operation=operation,
                )

            if operation == "describe_window":
                query = prepared.target.strip()
                if not query:
                    active = self.ui_observer.get_active_window()
                    query = active.title if active is not None else ""
                if not query:
                    return self._result(
                        "not_found", prepared.target, prepared.display_name,
                        "I can't tell which window you mean.",
                        operation=operation,
                    )
                observation = self.ui_observer.describe_window(query)
                if observation.status != "observed":
                    return self._result(
                        observation.status, prepared.target, observation.title,
                        observation.message, operation=operation,
                    )
                return self._result(
                    "window_described", prepared.target, observation.title,
                    observation.as_tree_text(), operation=operation,
                )

            if operation == "open_app":
                entry = self.catalog.get(prepared.entry_id)
                if entry is None:
                    return self._missing_prepared_app(prepared)
                self._launcher(entry)
                return self._result(
                    "opened", prepared.target, entry.display_name,
                    f"Opened {entry.display_name}.", operation=operation,
                    entry_id=entry.id,
                )

            if operation in {"close_app", "force_quit_app"}:
                entry = self.catalog.get(prepared.entry_id)
                if entry is None:
                    return self._missing_prepared_app(prepared)
                resolution = self.processes.resolve(entry)
                if resolution.status != "resolved":
                    return self._result(
                        resolution.status, prepared.target, entry.display_name,
                        resolution.message, operation=operation, entry_id=entry.id,
                    )
                status = (
                    self.processes.close(resolution.processes)
                    if operation == "close_app"
                    else self.processes.force_quit(resolution.processes)
                )
                messages = {
                    "closed": f"Closed {entry.display_name}.",
                    "close_requested": f"Asked {entry.display_name} to close.",
                    "force_quit": f"Force quit {entry.display_name}.",
                    "failed": f"I couldn't close {entry.display_name}.",
                }
                return self._result(
                    status, prepared.target, entry.display_name,
                    messages.get(status, f"I couldn't close {entry.display_name}."),
                    operation=operation, entry_id=entry.id,
                    error=(
                        str(getattr(self.processes, "last_error", ""))
                        if status == "failed"
                        else ""
                    ),
                )

            if operation == "open_url":
                self.browser.open(prepared.url)
                return self._result(
                    "url_opened", prepared.target, prepared.display_name,
                    f"Opened {prepared.url} in a new tab.", operation=operation,
                    url=prepared.url,
                )

            if operation == "create_file":
                self.filesystem.create_file(prepared.path)
                return self._result(
                    "file_created", prepared.target, prepared.display_name,
                    f"Created {prepared.display_name}.", operation=operation,
                    path=prepared.path,
                )

            if operation == "create_folder":
                self.filesystem.create_folder(prepared.path)
                return self._result(
                    "folder_created", prepared.target, prepared.display_name,
                    f"Created {prepared.display_name}.", operation=operation,
                    path=prepared.path,
                )

            if operation == "delete_file":
                self.filesystem.delete_file(prepared.path)
                return self._result(
                    "file_deleted", prepared.target, prepared.display_name,
                    f"Moved {prepared.display_name} to the Recycle Bin.",
                    operation=operation, path=prepared.path,
                )

            if operation == "delete_folder":
                self.filesystem.delete_folder(prepared.path)
                return self._result(
                    "folder_deleted", prepared.target, prepared.display_name,
                    f"Moved {prepared.display_name} to the Recycle Bin.",
                    operation=operation, path=prepared.path,
                )
        except FileExistsError as error:
            return self._result(
                "already_exists", prepared.target, prepared.display_name,
                f"{prepared.display_name} already exists.", operation=operation,
                path=prepared.path, error=f"{type(error).__name__}: {error}",
            )
        except (OSError, RuntimeError, PermissionError) as error:
            failure_message = {
                "open_app": f"I couldn't open {prepared.display_name}.",
                "open_url": f"I couldn't open {prepared.display_name}.",
                "close_app": f"I couldn't close {prepared.display_name}.",
                "force_quit_app": f"I couldn't force quit {prepared.display_name}.",
                "create_file": f"I couldn't create {prepared.display_name}.",
                "create_folder": f"I couldn't create {prepared.display_name}.",
                "delete_file": f"I couldn't delete {prepared.display_name}.",
                "delete_folder": f"I couldn't delete {prepared.display_name}.",
            }.get(
                operation,
                f"I couldn't complete that action for {prepared.display_name}.",
            )
            return self._result(
                "failed", prepared.target, prepared.display_name,
                failure_message,
                operation=operation, path=prepared.path, url=prepared.url,
                error=f"{type(error).__name__}: {error}",
            )
        return self._result(
            "blocked", prepared.target, prepared.display_name,
            "That computer action is not supported.", operation=operation,
        )

    # Phase 2 compatibility helpers remain useful to callers and tests.
    def resolve_app(self, target: str) -> ComputerActionResult:
        result = self.prepare(ComputerActionRequest("open_app", target))
        return replace(result, status="resolved") if result.prepared else result

    def open_app(self, target: str) -> ComputerActionResult:
        result = self.resolve_app(target)
        return self.execute(result.prepared) if result.prepared else result

    def open_entry(self, entry_id: str) -> ComputerActionResult:
        entry = self.catalog.get(entry_id)
        if entry is None:
            return self._result(
                "not_found", "", "", "I couldn't find that application anymore.",
                operation="open_app",
            )
        return self.execute(PreparedComputerAction(
            "open_app", entry.display_name, entry.display_name, entry_id=entry.id,
        ))

    @staticmethod
    def requires_extra_confirmation(operation: str) -> bool:
        return operation in HIGH_RISK_OPERATIONS

    @staticmethod
    def _policy_name(operation: str) -> str:
        return {
            "open_app": "computer.open_app",
            "close_app": "computer.close_app",
            "force_quit_app": "computer.force_quit_app",
            "open_url": "browser.open_url",
            "create_file": "filesystem.create",
            "create_folder": "filesystem.create",
            "delete_file": "filesystem.delete",
            "delete_folder": "filesystem.delete",
            "list_windows": "computer.observe_ui",
            "describe_window": "computer.observe_ui",
            "ui_action": "computer.ui_action",
        }[operation]

    def _from_app_resolution(
        self,
        resolution: AppResolution,
        operation: str,
    ) -> ComputerActionResult:
        if resolution.status == "ambiguous":
            if len(resolution.candidates) == 1:
                message = f"Did you mean {resolution.candidates[0]}?"
            else:
                choices = ", ".join(resolution.candidates)
                message = f"I found more than one match: {choices}."
            return self._result(
                "ambiguous", resolution.query, "",
                message, operation=operation,
                candidates=resolution.candidates,
            )
        return self._result(
            "not_found", resolution.query, "",
            f"I couldn't find {resolution.query} in your installed apps.",
            operation=operation,
        )

    def _missing_prepared_app(self, prepared: PreparedComputerAction) -> ComputerActionResult:
        return self._result(
            "not_found", prepared.target, prepared.display_name,
            "I couldn't find that application anymore.", operation=prepared.operation,
        )

    @staticmethod
    def _result(
        status: str,
        target: str,
        display_name: str,
        message: str,
        *,
        operation: str = "",
        candidates: tuple[str, ...] = (),
        entry_id: str = "",
        path: str = "",
        url: str = "",
        error: str = "",
        prepared: PreparedComputerAction | None = None,
    ) -> ComputerActionResult:
        audit = (
            f"[Computer Control] action={operation or '(none)'} "
            f"target={target or '(none)'} status={status}"
        )
        if error:
            audit += f" error={error}"
        print(audit)
        return ComputerActionResult(
            status=status,
            target=target,
            display_name=display_name,
            message=message,
            operation=operation,
            candidates=candidates,
            entry_id=entry_id,
            path=path,
            url=url,
            error=error,
            prepared=prepared,
        )
