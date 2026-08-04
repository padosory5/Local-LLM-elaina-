from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionPolicy:
    action: str
    risk: str
    approval_required: bool
    description: str


class PolicyEngine:
    """Local, deterministic authorization boundary for all agent actions."""

    POLICIES = {
        "web.search": ActionPolicy(
            "web.search",
            "read_only",
            False,
            "Search public web information.",
        ),
        "screen.analyze": ActionPolicy(
            "screen.analyze",
            "read_only",
            False,
            "Analyze a region explicitly selected by the user.",
        ),
        "computer.open_app": ActionPolicy(
            "computer.open_app",
            "local_reversible_action",
            False,
            "Open one locally allowlisted application.",
        ),
        "computer.close_app": ActionPolicy(
            "computer.close_app",
            "local_reversible_action",
            False,
            "Ask one locally resolved application to close gracefully.",
        ),
        "computer.force_quit_app": ActionPolicy(
            "computer.force_quit_app",
            "local_destructive_action",
            True,
            "Terminate locally resolved application processes after confirmation.",
        ),
        "browser.open_url": ActionPolicy(
            "browser.open_url",
            "local_reversible_action",
            False,
            "Open one grounded HTTP or HTTPS address in the default browser.",
        ),
        "filesystem.create": ActionPolicy(
            "filesystem.create",
            "reversible_write",
            False,
            "Create one new empty file or folder beneath an allowed root.",
        ),
        "filesystem.delete": ActionPolicy(
            "filesystem.delete",
            "recoverable_destructive_action",
            True,
            "Move one exact allowlisted file or folder to the Recycle Bin.",
        ),
        "project.read": ActionPolicy(
            "project.read",
            "read_only",
            False,
            "Read files inside the configured project root.",
        ),
        "project.write": ActionPolicy(
            "project.write",
            "reversible_write",
            True,
            "Modify approved project files.",
        ),
        "git.commit": ActionPolicy(
            "git.commit",
            "external_write",
            True,
            "Create a Git commit.",
        ),
        "git.push": ActionPolicy(
            "git.push",
            "external_commitment",
            True,
            "Push a commit to a remote repository.",
        ),
        "agent.install": ActionPolicy(
            "agent.install",
            "capability_change",
            True,
            "Activate a new agent definition.",
        ),
        "calendar.create_event": ActionPolicy(
            "calendar.create_event",
            "external_write",
            True,
            "Create an event in the user's Google Calendar.",
        ),
    }

    def get(self, action: str) -> ActionPolicy:
        try:
            return self.POLICIES[action]
        except KeyError as error:
            raise PermissionError(
                f"No permission policy exists for action '{action}'."
            ) from error

    def requires_approval(self, action: str) -> bool:
        return self.get(action).approval_required
