"""Drive the real running Elaina end to end over her WebSocket channel.

Unlike the other ``scripts/live_*_check.py`` files, this one does not build
a planner with simulated capabilities -- it talks to the actual process:
real Ollama calls, the real router, the real browser. It exists because
every serious defect this project has hit was found this way and none of
them were visible from unit tests with a scripted client.

Usage::

    .venv/Scripts/python.exe scripts/live_conversation_check.py --scenario transcript

Start the backend first, without the Electron window::

    ELAINA_OPEN_DESKTOP=0 .venv/Scripts/python.exe main.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import websockets
except ImportError:  # pragma: no cover - operator-facing message
    raise SystemExit("pip install websockets to run this check")


DEFAULT_URL = "ws://127.0.0.1:8765"

# The exact conversation from the failure report, in order. Each turn is
# (message, what a working Elaina must do with it).
SCENARIOS: dict[str, tuple[tuple[str, str], ...]] = {
    "transcript": (
        (
            "find good places to stay near the city in Hong Kong",
            "Hong Kong is not the user's market -- no Korean site hint, and "
            "no invented prices.",
        ),
        (
            "check the price on the browser",
            "Must reach browser control, never 'that PC action isn't "
            "supported yet'.",
        ),
        (
            "ok",
            "Must resolve against whatever was just offered, never repeat "
            "the previous sentence verbatim.",
        ),
    ),
    "locale": (
        (
            "what are the best second hand websites to buy a used phone",
            "Must recommend the user's own market's marketplaces.",
        ),
    ),
    "abilities": (
        (
            "can you control my browser?",
            "An honest inventory answer, not an attempt and not a refusal.",
        ),
        (
            "what can you do?",
            "Names real abilities and which switches are off.",
        ),
    ),
    "browser": (
        (
            "Search Google for hotels in Seoul.",
            "A real, controlled browser must open.",
        ),
    ),
}


async def run(url: str, turns: tuple[tuple[str, str], ...], timeout: float) -> int:
    failures = 0
    async with websockets.connect(url, max_size=None) as socket:
        await socket.send(json.dumps({"command": "set_input_mode", "mode": "text"}))
        await socket.send(
            json.dumps({"command": "set_computer_control_mode", "enabled": True})
        )
        await asyncio.sleep(1.0)

        for message, expectation in turns:
            print("\n" + "=" * 72)
            print(f"USER: {message}")
            print(f"EXPECT: {expectation}")
            print("-" * 72)
            started = time.perf_counter()
            await socket.send(
                json.dumps({"command": "send_text_message", "text": message})
            )
            reply = await _await_reply(socket, timeout)
            elapsed = time.perf_counter() - started
            if reply is None:
                print(f"ELAINA: (no reply within {timeout:.0f}s)")
                failures += 1
                continue
            print(f"ELAINA ({elapsed:.1f}s): {reply}")
    return failures


async def _await_reply(socket, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        event = payload.get("event")
        if event == "assistant_status":
            print(f"   ... {payload.get('text', '')}")
        elif event == "assistant_finished":
            return str(payload.get("text", ""))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument(
        "--scenario", default="transcript", choices=sorted(SCENARIOS) + ["all"],
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--say", action="append", default=[],
        help="Send an ad-hoc message instead of a named scenario; repeatable.",
    )
    args = parser.parse_args()

    if args.say:
        turns = tuple((text, "(ad-hoc)") for text in args.say)
    elif args.scenario == "all":
        turns = tuple(turn for group in SCENARIOS.values() for turn in group)
    else:
        turns = SCENARIOS[args.scenario]

    failures = asyncio.run(run(args.url, turns, args.timeout))
    print("\n" + "=" * 72)
    print(f"Turns: {len(turns)}  Missing replies: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
