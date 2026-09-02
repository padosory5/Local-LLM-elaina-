from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any

import websockets

from core.event_bus import Event, EventBus


class WebSocketServer:
    def __init__(
        self,
        event_bus: EventBus,
        host: str = "127.0.0.1",
        port: int = 8765,
        command_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.events = event_bus
        self.host = host
        self.port = port
        self.command_handler = command_handler

        self._clients: set[Any] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._stopping_async: asyncio.Event | None = None

        self._event_names = (
            "tts_started",
            "tts_finished",
            "tts_interrupted",
            "speech_started",
            "emotion_changed",
            "lip_sync",

            "input_mode_changed",
            "computer_control_mode_changed",

            "user_message",
            "assistant_started",
            "assistant_status",
            "assistant_interrupted",
            "assistant_stream",
            "assistant_finished",
            "screen_region_ready",
            "screen_region_error",
            "visual_match_found",
            "project_change_proposed",
            "project_change_applied",
            "project_change_rejected",
            "project_change_error",
            "git_action_proposed",
            "git_action_completed",
            "git_action_rejected",
            "git_action_partial",
            "git_action_error",
            "agent_task_started",
            "action_approval_requested",
            "action_approval_completed",
            "action_approval_rejected",
            "action_approval_error",
        )

        for event_name in self._event_names:
            self.events.subscribe(
                event_name,
                self._on_event,
            )

    def start(self) -> None:
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._run_server,
            daemon=True,
        )
        self._thread.start()

        if not self._started.wait(timeout=5):
            raise RuntimeError(
                "WebSocket server failed to start."
            )

    def _run_server(self) -> None:
        asyncio.run(self._serve())

    def stop(self, timeout: float = 5.0) -> None:
        """Release the port and stop serving. Safe to call more than once.

        Called from the shutdown path rather than relying on the daemon
        thread dying with the process, so a failed startup unwinds the
        listener it already opened instead of leaving it bound.
        """
        thread, loop = self._thread, self._loop
        self._thread = None
        if thread is None:
            return
        if loop is not None and self._stopping_async is not None:
            try:
                loop.call_soon_threadsafe(self._stopping_async.set)
            except RuntimeError:
                # The loop is already closed; the listener is gone with it.
                pass
        thread.join(timeout=timeout)
        self._started.clear()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping_async = asyncio.Event()

        async with websockets.serve(
            self._handle_client,
            self.host,
            self.port,
        ):
            print(
                f"[WebSocket] Listening on "
                f"ws://{self.host}:{self.port}"
            )

            self._started.set()

            # Runs until stop() sets this from another thread. This used to
            # be a bare Future that nothing ever resolved, so the port was
            # only ever released by the process dying -- which meant a
            # failed startup, or a restart in the same process, met the
            # address still bound.
            await self._stopping_async.wait()

        print("[WebSocket] Stopped listening.")

    async def _handle_client(self, websocket) -> None:
        self._clients.add(websocket)

        print(
            f"[WebSocket] Electron connected. "
            f"Clients: {len(self._clients)}"
        )

        try:
            async for raw_message in websocket:
                await self._handle_message(raw_message)
        finally:
            self._clients.discard(websocket)

            print(
                f"[WebSocket] Electron disconnected. "
                f"Clients: {len(self._clients)}"
            )

    async def _handle_message(self, raw_message: str) -> None:
        """Receive commands initiated by the Electron interface."""
        if self.command_handler is None:
            return

        try:
            message = json.loads(raw_message)

            if not isinstance(message, dict):
                raise ValueError("WebSocket command must be an object.")

            await asyncio.to_thread(
                self.command_handler,
                message,
            )
        except json.JSONDecodeError:
            print("[WebSocket Command Error] Invalid JSON.")
        except Exception as error:
            print(f"[WebSocket Command Error] {error}")

    def _on_event(self, event: Event) -> None:

        if self._loop is None:
            return

        message = {
            "event": event.name,
            **event.data,
        }

        payload = json.dumps(message)

        asyncio.run_coroutine_threadsafe(
            self._broadcast(payload),
            self._loop,
        )

    async def _broadcast(self, payload: str) -> None:
        if not self._clients:
            return

        disconnected = []

        for client in list(self._clients):
            try:
                await client.send(payload)
            except websockets.ConnectionClosed:
                disconnected.append(client)
            except Exception as error:
                print(
                    f"[WebSocket Send Error] {error}"
                )
                disconnected.append(client)

        for client in disconnected:
            self._clients.discard(client)
