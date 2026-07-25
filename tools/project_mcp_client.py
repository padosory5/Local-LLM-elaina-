from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ProjectMCPClient:
    def __init__(
        self,
        project_root: str | Path,
        approval_token: str = "",
    ) -> None:
        self.project_root = Path(
            project_root
        ).resolve()
        self.approval_token = approval_token

        self.session = None
        self._stdio_context = None
        self._session_context = None

    async def connect(self) -> None:
        if not self.project_root.is_dir():
            raise ValueError(
                "The selected project does not exist."
            )

        server_script = (
            Path(__file__).resolve().parent
            / "project_mcp_server.py"
        )

        environment = {
            **os.environ,
            "ELAINA_PROJECT_ROOT": str(
                self.project_root
            ),
            "ELAINA_APPROVAL_TOKEN": self.approval_token,
        }

        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(server_script)],
            env=environment,
        )

        self._stdio_context = stdio_client(
            parameters
        )

        read_stream, write_stream = (
            await self._stdio_context.__aenter__()
        )

        self._session_context = ClientSession(
            read_stream,
            write_stream,
        )

        self.session = (
            await self._session_context.__aenter__()
        )

        await self.session.initialize()

    async def list_tools(self):
        if self.session is None:
            raise RuntimeError(
                "Project MCP client is not connected."
            )

        result = await self.session.list_tools()
        return result.tools

    async def call_tool(
        self,
        name: str,
        arguments: dict | None = None,
    ) -> str:
        if self.session is None:
            raise RuntimeError(
                "Project MCP client is not connected."
            )

        result = await self.session.call_tool(
            name,
            arguments or {},
        )

        output: list[str] = []

        for item in result.content:
            text = getattr(item, "text", None)

            if text:
                output.append(text)

        combined_output = "\n".join(output)
        is_error = getattr(
            result,
            "isError",
            getattr(result, "is_error", False),
        )

        if is_error:
            raise RuntimeError(
                combined_output or f"Project tool '{name}' failed."
            )

        return combined_output

    async def close(self) -> None:
        if self._session_context is not None:
            await self._session_context.__aexit__(
                None,
                None,
                None,
            )

        if self._stdio_context is not None:
            await self._stdio_context.__aexit__(
                None,
                None,
                None,
            )

        self.session = None


class ProjectMCPManager:
    """
    Keep the asynchronous MCP connection alive for synchronous Elaina code.

    ChatEngine is synchronous, while the MCP SDK is asynchronous. This manager
    owns one background event loop and one persistent MCP connection so Elaina
    does not need to start the project server again for every question.
    """

    def __init__(
        self,
        project_root: str | Path,
        startup_timeout: float = 15.0,
        tool_timeout: float = 30.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.startup_timeout = startup_timeout
        self.tool_timeout = tool_timeout

        self._client: ProjectMCPClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: Exception | None = None
        self._tools: list[Any] = []
        self._tool_names: set[str] = set()
        self._approval_token = secrets.token_urlsafe(32)
        self._internal_tools = {
            "apply_project_proposal",
            "reject_project_proposal",
            "revise_project_proposal",
            "execute_git_proposal",
            "reject_git_proposal",
            "prepare_git_proposal",
        }

    def start(self) -> None:
        """Start the MCP server and wait until its tools are available."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._thread_main,
            name="elaina-project-mcp",
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(self.startup_timeout):
            raise TimeoutError(
                "Timed out while starting the project MCP server."
            )

        if self._startup_error is not None:
            raise RuntimeError(
                f"Could not start project MCP: {self._startup_error}"
            ) from self._startup_error

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()
            self._loop = None

    async def _serve(self) -> None:
        """
        Own both connect and close in one async task.

        The MCP stdio transport uses AnyIO cancel scopes, which must be entered
        and exited by the same task. Keeping the lifecycle here prevents the
        shutdown errors caused by opening and closing it from different tasks.
        """
        self._client = ProjectMCPClient(
            self.project_root,
            self._approval_token,
        )
        self._stop_event = asyncio.Event()

        try:
            await self._client.connect()
            self._tools = await self._client.list_tools()
            self._tool_names = {
                self._read_value(tool, "name", "")
                for tool in self._tools
            }
            self._tool_names.discard("")
            self._ready.set()
            await self._stop_event.wait()
        except Exception as error:
            self._startup_error = error
            self._ready.set()
        finally:
            if self._client is not None:
                try:
                    await self._client.close()
                except Exception as error:
                    print(f"[Project MCP] Shutdown warning: {error}")

            self._client = None

    @staticmethod
    def _read_value(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    def ollama_tools(self) -> list[dict]:
        """Convert MCP tool definitions to Ollama's function-tool format."""
        definitions: list[dict] = []

        for tool in self._tools:
            name = self._read_value(tool, "name", "")
            if not name or name in self._internal_tools:
                continue

            schema = self._read_value(tool, "inputSchema")
            if schema is None:
                schema = self._read_value(tool, "input_schema")
            if schema is None:
                schema = {
                    "type": "object",
                    "properties": {},
                }

            definitions.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": self._read_value(
                        tool,
                        "description",
                        "",
                    ),
                    "parameters": schema,
                },
            })

        return definitions

    def call_tool(
        self,
        name: str,
        arguments: dict | None = None,
        timeout: float | None = None,
    ) -> str:
        """Run one approved MCP tool and return its text result."""
        if name not in self._tool_names:
            raise ValueError(f"Unknown project tool: {name}")
        if self._loop is None or self._client is None:
            raise RuntimeError("Project MCP is not connected.")

        future = asyncio.run_coroutine_threadsafe(
            self._client.call_tool(name, arguments),
            self._loop,
        )
        return future.result(
            timeout=self.tool_timeout if timeout is None else timeout
        )

    def resolve_proposal(
        self,
        proposal_id: str,
        approved: bool,
        revised_texts: list[str] | None = None,
    ) -> str:
        """
        Apply or reject a proposal using the secret hidden from Ollama.

        Only ChatEngine's Electron command handler calls this method.
        """
        if approved and revised_texts is not None:
            revision_result = self.call_tool(
                "revise_project_proposal",
                {
                    "proposal_id": proposal_id,
                    "approval_token": self._approval_token,
                    "revised_texts": revised_texts,
                },
            )
            revision = json.loads(revision_result)
            proposal_id = revision["proposal_id"]

        tool_name = (
            "apply_project_proposal"
            if approved
            else "reject_project_proposal"
        )
        return self.call_tool(
            tool_name,
            {
                "proposal_id": proposal_id,
                "approval_token": self._approval_token,
            },
        )

    def prepare_git_proposal(self) -> str:
        """Create a read-only Git proposal for Electron review."""
        return self.call_tool(
            "prepare_git_proposal",
            {"commit_message": ""},
            timeout=120,
        )

    def resolve_git_proposal(
        self,
        proposal_id: str,
        approved: bool,
        commit_message: str = "",
        push: bool = True,
    ) -> str:
        """Execute or reject a Git proposal using the hidden approval secret."""
        if not approved:
            return self.call_tool(
                "reject_git_proposal",
                {
                    "proposal_id": proposal_id,
                    "approval_token": self._approval_token,
                },
                timeout=30,
            )

        return self.call_tool(
            "execute_git_proposal",
            {
                "proposal_id": proposal_id,
                "approval_token": self._approval_token,
                "commit_message": commit_message,
                "push": bool(push),
            },
            timeout=180,
        )

    def close(self) -> None:
        """Request a clean MCP shutdown and wait briefly for its thread."""
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)

        if self._thread is not None:
            self._thread.join(timeout=5)

        self._thread = None
