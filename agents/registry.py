from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agents.base import AgentDefinition
from core.paths import PROJECT_ROOT, RUNTIME_ROOT


class AgentRegistry:
    """
    Load trusted built-in agents and approved user-created definitions.

    User agents are data-only YAML files. Creating one never grants arbitrary
    Python execution; it can only use tool names already implemented and
    allowed by Elaina.
    """

    _BUILT_IN_CAPABILITY_NOTES = {
        "agent_builder": (
            "Can collect requirements and propose an agent from a reviewed "
            "blueprint. It cannot invent a new executable tool or perform the "
            "new agent's action before installation and approval."
        ),
        "coding_agent": (
            "Can inspect the selected local project and prepare approval-gated "
            "file changes. It cannot edit anything before approval."
        ),
        "computer_control": (
            "Can observe and act on native Windows windows and browser pages "
            "the user is already looking at, once Desktop Control Mode is on. "
            "It cannot act while that mode is off, or on a window/page it "
            "hasn't verified is actually open."
        ),
        "conversation_agent": (
            "Handles ordinary voice conversation, personality, memory, and "
            "stable knowledge without performing external actions."
        ),
        "git_agent": (
            "Can prepare reviewed Git commits and pushes. It cannot commit or "
            "push before approval."
        ),
        "research_agent": (
            "Can perform a current one-time web search or fact check. It "
            "cannot keep monitoring something or send a future alert."
        ),
        "vision_agent": (
            "Can analyze a screen region the user explicitly selects and use "
            "web verification for identification."
        ),
    }

    def __init__(
        self,
        built_in_directory: Path | None = None,
        user_directory: Path | None = None,
    ) -> None:
        self.built_in_directory = (
            built_in_directory
            or PROJECT_ROOT / "agents" / "definitions"
        )
        self.user_directory = (
            user_directory
            or RUNTIME_ROOT / "agents"
        )
        self.user_directory.mkdir(parents=True, exist_ok=True)

        self._agents: dict[str, AgentDefinition] = {}
        self.reload()

    def reload(self) -> None:
        loaded: dict[str, AgentDefinition] = {}

        for directory, user_created in (
            (self.built_in_directory, False),
            (self.user_directory, True),
        ):
            if not directory.is_dir():
                continue

            for path in sorted(directory.glob("*.yaml")):
                try:
                    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("Top level must be a mapping.")
                    definition = AgentDefinition.from_mapping(
                        payload,
                        user_created=user_created,
                    )
                    loaded[definition.id] = definition
                except Exception as error:
                    print(
                        f"[Agent Registry] Ignoring {path.name}: "
                        f"{type(error).__name__}: {error}"
                    )

        self._agents = loaded

    def all(self) -> tuple[AgentDefinition, ...]:
        return tuple(self._agents.values())

    def capability_context(self) -> str:
        """Describe only the agents that are active in this runtime."""
        active_agents = sorted(
            (
                agent
                for agent in self._agents.values()
                if agent.enabled
            ),
            key=lambda agent: agent.name.casefold(),
        )
        lines = []
        for agent in active_agents:
            built_in_note = self._BUILT_IN_CAPABILITY_NOTES.get(agent.id)
            if built_in_note:
                lines.append(
                    f"- {agent.name}: {built_in_note}"
                )
                continue

            tools = (
                ", ".join(agent.tools)
                if agent.tools
                else "conversation only"
            )
            lines.append(
                f"- {agent.name}: {agent.description} "
                f"Available tools: {tools}."
            )

        return (
            "Elaina can currently delegate only to these active agents:\n"
            + "\n".join(lines)
        )

    def get(self, agent_id: str) -> AgentDefinition | None:
        agent = self._agents.get(str(agent_id).strip())
        if agent is None or not agent.enabled:
            return None
        return agent

    def for_intent(self, intent: str) -> AgentDefinition | None:
        for agent in self._agents.values():
            if agent.enabled and intent in agent.intents:
                return agent
        return None

    def has_agent(self, agent_id: str) -> bool:
        return self.get(agent_id) is not None

    def install_user_agent(
        self,
        definition: dict[str, Any],
    ) -> AgentDefinition:
        validated = AgentDefinition.from_mapping(
            definition,
            user_created=True,
        )

        existing = self._agents.get(validated.id)
        if existing is not None and not existing.user_created:
            raise ValueError(
                f"A user-created agent cannot replace built-in agent "
                f"'{validated.id}'."
            )

        # A user definition may reference only capabilities implemented by this
        # application. The policy layer remains responsible for each action.
        allowed_tools = {
            tool
            for agent in self._agents.values()
            if not agent.user_created
            for tool in agent.tools
        }
        unknown_tools = sorted(set(validated.tools) - allowed_tools)
        if unknown_tools:
            raise ValueError(
                "The agent requested unavailable tools: "
                + ", ".join(unknown_tools)
            )

        destination = self.user_directory / f"{validated.id}.yaml"
        destination.write_text(
            yaml.safe_dump(
                validated.to_mapping(),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        self.reload()

        installed = self.get(validated.id)
        if installed is None:
            raise RuntimeError("The agent definition could not be activated.")
        return installed
