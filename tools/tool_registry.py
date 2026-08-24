"""Namespaced tool registry: a thin, additive abstraction over where a
tool's real implementation lives (a plain Python callable, an adapter
around an existing planner's .act() entry point, or an MCP-backed name).

Not yet wired into any dispatch path -- every existing call site keeps
working exactly as before. This exists so a future ability-scoped dispatch
can ask for "the tools this ability declares" by namespaced id (web.search,
computer.ui_action, ...) instead of each caller hand-rolling its own Ollama
tool schema, mirroring the one existing example of this exact conversion
already proven by tools/project_mcp_client.py's ollama_tools().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    """Documentary description of one namespaced tool."""

    id: str  # namespaced, e.g. "web.search"
    description: str
    use_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    required_inputs: dict[str, Any] = field(default_factory=dict)  # JSON-schema shape
    preconditions: tuple[str, ...] = ()
    output_description: str = ""

    def to_ollama_function(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.required_inputs or {
                    "type": "object", "properties": {},
                },
            },
        }


@dataclass(frozen=True)
class ToolResult:
    status: str
    message: str = ""
    data: Any = None


class ToolRegistry:
    """Bind namespaced tool ids to their real backend callables."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._backends: dict[str, Callable[..., Any]] = {}

    def register(self, spec: ToolSpec, backend: Callable[..., Any]) -> None:
        self._specs[spec.id] = spec
        self._backends[spec.id] = backend

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._specs.get(tool_id)

    def to_ollama_tools(self, tool_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            self._specs[tool_id].to_ollama_function()
            for tool_id in tool_ids
            if tool_id in self._specs
        ]

    def invoke(
        self, tool_id: str, arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        backend = self._backends.get(tool_id)
        if backend is None:
            return ToolResult(
                "unknown_tool", message=f"No tool is registered as {tool_id!r}.",
            )
        try:
            result = backend(**(arguments or {}))
        except Exception as error:
            return ToolResult(
                "failed", message=f"{type(error).__name__}: {error}",
            )
        if isinstance(result, ToolResult):
            return result
        return ToolResult("done", data=result)
