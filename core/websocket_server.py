from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import websockets

from core.event_bus import Event, EventBus


class WebSocketServer:
    def __init__(
        self,
        event_bus: EventBus,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.events = event_bus
        self.host = host
        self.port = port

        self._clients: set[Any] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

        self._event_names = (
            "tts_started",
            "tts_finished",
            "tts_interrupted",
            "speech_started",
            "emotion_changed",
            "lip_sync",

            "user_message",
            "assistant_started",
            "assistant_stream",
            "assistant_finished",
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

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()

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

            await asyncio.Future()

    async def _handle_client(self, websocket) -> None:
        self._clients.add(websocket)

        print(
            f"[WebSocket] Electron connected. "
            f"Clients: {len(self._clients)}"
        )

        try:
            await websocket.wait_closed()
        finally:
            self._clients.discard(websocket)

            print(
                f"[WebSocket] Electron disconnected. "
                f"Clients: {len(self._clients)}"
            )

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