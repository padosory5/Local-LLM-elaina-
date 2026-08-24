from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentDefinition:
    """Declarative description of one specialist agent."""

    id: str
    name: str
    description: str
    intents: tuple[str, ...]
    tools: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()
    settings: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    user_created: bool = False
    # Documentary metadata for the ability registry. Not yet consumed by any
    # dispatch logic -- these describe when this ability is/isn't the right
    # one and what must hold true before it runs, for a human or a future
    # router to read, not an enforced contract yet.
    use_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls,
        payload: dict[str, Any],
        *,
        user_created: bool = False,
    ) -> "AgentDefinition":
        agent_id = str(payload.get("id", "")).strip()
        name = str(payload.get("name", "")).strip()
        description = str(payload.get("description", "")).strip()

        if not agent_id or not name or not description:
            raise ValueError(
                "Agent definitions require id, name, and description."
            )

        if not agent_id.replace("_", "").isalnum():
            raise ValueError(
                "Agent ids may contain only letters, numbers, and underscores."
            )

        intents = payload.get("intents", [])
        tools = payload.get("tools", [])
        instructions = payload.get("instructions", [])
        settings = payload.get("settings", {})
        use_when = payload.get("use_when", [])
        avoid_when = payload.get("avoid_when", [])
        preconditions = payload.get("preconditions", [])

        if not isinstance(intents, list) or not all(
            isinstance(item, str) for item in intents
        ):
            raise ValueError("Agent intents must be a list of strings.")
        if not isinstance(tools, list) or not all(
            isinstance(item, str) for item in tools
        ):
            raise ValueError("Agent tools must be a list of strings.")
        if not isinstance(instructions, list) or not all(
            isinstance(item, str) for item in instructions
        ):
            raise ValueError("Agent instructions must be a list of strings.")
        if not isinstance(settings, dict):
            raise ValueError("Agent settings must be a mapping.")
        for label, value in (
            ("use_when", use_when),
            ("avoid_when", avoid_when),
            ("preconditions", preconditions),
        ):
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise ValueError(f"Agent {label} must be a list of strings.")

        return cls(
            id=agent_id,
            name=name,
            description=description,
            intents=tuple(item.strip() for item in intents if item.strip()),
            tools=tuple(item.strip() for item in tools if item.strip()),
            instructions=tuple(
                item.strip() for item in instructions if item.strip()
            ),
            settings=dict(settings),
            enabled=bool(payload.get("enabled", True)),
            user_created=user_created,
            use_when=tuple(item.strip() for item in use_when if item.strip()),
            avoid_when=tuple(
                item.strip() for item in avoid_when if item.strip()
            ),
            preconditions=tuple(
                item.strip() for item in preconditions if item.strip()
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "intents": list(self.intents),
            "tools": list(self.tools),
            "instructions": list(self.instructions),
            "settings": dict(self.settings),
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "preconditions": list(self.preconditions),
        }
