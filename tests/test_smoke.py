"""Can Elaina still start up, and take one turn?

The rest of the suite tests behaviour in detail. This module tests the thing
every one of those tests assumes: that the pieces `main.py` wires together on
startup still load, still talk to each other, and still get all the way from
a sentence to a reply.

It is deliberately shallow and deliberately fast. It needs no Ollama, no
microphone, no browser and no Electron -- so it can be the first thing run
after any change, and a failure here means something structural broke rather
than a behaviour changing.

    python tests/run_tests.py smoke
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import unittest

import websockets
import yaml

from agents.registry import AgentRegistry
from config.loader import Config
from core.event_bus import EventBus
from core.paths import (
    DATA_DIRECTORY,
    DATABASE_DIRECTORY,
    PROJECT_ROOT,
    ensure_runtime_directories,
)
from core.websocket_server import WebSocketServer
from tests.turn_harness import build_engine, machine_actions, reset


def _free_port() -> int:
    """A port nothing is listening on, for the WebSocket round trip."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ConfigurationTests(unittest.TestCase):
    """config.yaml still parses, and still answers what startup asks it."""

    def test_config_loads_and_validates(self):
        config = Config()

        # main.py -> ChatEngine reads all of these before it can start.
        self.assertTrue(config.get("llm", "ollama", "model"))
        self.assertIsNotNone(config.get("llm", "ollama", "temperature"))
        self.assertIsInstance(config.section("responses"), dict)

    def test_runtime_directories_can_be_created(self):
        ensure_runtime_directories()

        self.assertTrue(DATABASE_DIRECTORY.is_dir())
        self.assertTrue(DATA_DIRECTORY.is_dir())


class AgentDefinitionTests(unittest.TestCase):
    """Every YAML in agents/definitions is loadable and uniquely named."""

    def test_definitions_on_disk_all_parse(self):
        paths = sorted((PROJECT_ROOT / "agents" / "definitions").glob("*.yaml"))
        self.assertTrue(paths, "no agent definitions were found")

        for path in paths:
            with self.subTest(definition=path.name):
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertIsInstance(payload, dict)
                self.assertTrue(payload.get("id"), f"{path.name} has no id")

    def test_registry_loads_them(self):
        registry = AgentRegistry()
        agents = registry.all()

        self.assertTrue(agents, "the agent registry loaded nothing")
        identifiers = [agent.id for agent in agents]
        self.assertEqual(
            len(identifiers),
            len(set(identifiers)),
            f"duplicate agent ids: {identifiers}",
        )


class EventBusTests(unittest.TestCase):
    def test_a_subscriber_receives_what_was_emitted(self):
        events = EventBus()
        received: list[tuple[str, dict]] = []
        events.subscribe("assistant_finished", lambda e: received.append(
            (e.name, e.data)
        ))

        events.emit("assistant_finished", text="hello")

        self.assertEqual(received, [("assistant_finished", {"text": "hello"})])

    def test_one_failing_subscriber_does_not_stop_the_others(self):
        events = EventBus()
        reached: list[str] = []

        def explodes(_event):
            raise RuntimeError("subscriber broke")

        events.subscribe("tts_started", explodes)
        events.subscribe("tts_started", lambda _e: reached.append("second"))

        events.emit("tts_started")

        self.assertEqual(reached, ["second"])


class DesktopChannelTests(unittest.TestCase):
    """The Electron bridge, both directions, over a real socket.

    A published event has to reach a connected client as JSON, and a command
    sent by that client has to reach the handler main.py registers. This is
    the whole contract between the backend and the renderer.
    """

    @classmethod
    def setUpClass(cls):
        cls.events = EventBus()
        cls.commands: list[dict] = []
        cls.port = _free_port()
        cls.received = threading.Event()

        def handle(message: dict) -> None:
            cls.commands.append(message)
            cls.received.set()

        cls.server = WebSocketServer(
            event_bus=cls.events,
            host="127.0.0.1",
            port=cls.port,
            command_handler=handle,
        )
        cls.server.start()

    def test_events_go_out_and_commands_come_back(self):
        async def exchange() -> dict:
            url = f"ws://127.0.0.1:{self.port}"
            async with websockets.connect(url) as client:
                # Broadcasting only reaches registered clients, so emit after
                # the connection exists.
                self.events.emit("assistant_started", turn="smoke")
                raw = await asyncio.wait_for(client.recv(), timeout=5)

                await client.send(json.dumps({
                    "command": "set_input_mode",
                    "mode": "text",
                }))
                return json.loads(raw)

        published = asyncio.run(exchange())

        self.assertEqual(published["event"], "assistant_started")
        self.assertEqual(published["turn"], "smoke")

        self.assertTrue(
            self.received.wait(timeout=5),
            "the backend never received the renderer's command",
        )
        self.assertEqual(
            self.commands[-1],
            {"command": "set_input_mode", "mode": "text"},
        )

    def test_malformed_json_from_the_renderer_is_survived(self):
        async def send_rubbish() -> None:
            url = f"ws://127.0.0.1:{self.port}"
            async with websockets.connect(url) as client:
                await client.send("not json at all")
                # A second, valid command proves the connection is still good.
                await client.send(json.dumps({"command": "ping"}))
                await asyncio.sleep(0.2)

        self.received.clear()
        asyncio.run(send_rubbish())

        self.assertTrue(self.received.wait(timeout=5))
        self.assertEqual(self.commands[-1], {"command": "ping"})


class WholeTurnTests(unittest.TestCase):
    """The engine main.py builds, answering something, touching nothing."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_she_is_wired_together(self):
        for part in (
            "intent_router",
            "desktop_action_planner",
            "browser_action_planner",
            "agent_registry",
            "events",
        ):
            with self.subTest(part=part):
                self.assertIsNotNone(getattr(self.engine, part, None))

    def test_an_ordinary_sentence_gets_an_ordinary_reply(self):
        reset(self.engine)

        reply = self.engine.chat("hey, how's your day going")

        self.assertTrue(str(reply).strip(), "she said nothing at all")
        self.assertEqual(
            machine_actions(self.engine),
            [],
            "small talk touched the machine",
        )

    def test_the_turn_is_announced_on_the_event_bus(self):
        reset(self.engine)
        seen: list[str] = []
        for name in ("assistant_started", "assistant_finished"):
            self.engine.events.subscribe(name, lambda e: seen.append(e.name))

        self.engine.chat("what's your name")

        self.assertIn("assistant_started", seen)


if __name__ == "__main__":
    unittest.main()
