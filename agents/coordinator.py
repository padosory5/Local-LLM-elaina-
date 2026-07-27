from __future__ import annotations

from dataclasses import dataclass

from agents.base import AgentDefinition
from agents.registry import AgentRegistry
from agents.task_manager import AgentTask, AgentTaskManager


@dataclass(frozen=True)
class AgentAssignment:
    definition: AgentDefinition
    task: AgentTask


class AgentCoordinator:
    """Resolve a semantic intent to one constrained specialist agent."""

    def __init__(
        self,
        registry: AgentRegistry,
        tasks: AgentTaskManager,
    ) -> None:
        self.registry = registry
        self.tasks = tasks

    def assign(self, intent: str, request: str) -> AgentAssignment:
        definition = self.registry.for_intent(intent)
        if definition is None:
            # Conversation is the safe fallback: an unknown intent may not
            # acquire action tools merely because a model requested them.
            definition = self.registry.get("conversation_agent")
        if definition is None:
            raise RuntimeError("The conversation agent is not registered.")

        task = self.tasks.start(definition.id, request)
        print(
            f"[Agent] {definition.name} accepted task {task.id[:8]} "
            f"for intent {intent}."
        )
        return AgentAssignment(definition=definition, task=task)
