"""Measure the router's own call, isolated from the rest of a turn.

The full latency benchmark drives whole turns, which is the right way to see
perceived latency but a slow way to iterate on one component. This calls
``SemanticIntentRouter.route`` directly over a fixed set of inputs and reports
median/p90, so one change to the prompt or the schema can be measured in a
couple of minutes instead of twenty.

It also reports how large the prompt actually is, since that is what the cost
is made of: ~3,860 tokens of prompt was the starting point, of which the
routing-rules block alone was 70%.

    .venv/Scripts/python.exe scripts/router_latency_check.py
    .venv/Scripts/python.exe scripts/router_latency_check.py --runs 3
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import ollama

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.intent_router import SemanticIntentRouter  # noqa: E402
from config.loader import Config  # noqa: E402

# A spread across the intents that actually reach the model, so a change that
# helps one shape and hurts another shows up.
INPUTS = (
    "What's the weather in Seattle tomorrow?",
    "Good restaurants in Seattle?",
    "Open Spotify.",
    "Close Discord normally.",
    "What's the capital of France?",
    "I'm thinking about getting a monitor.",
    "Click the Sign in button on this page.",
    "Find me a good mechanical keyboard under $150.",
    "Create an empty file named notes.txt in Documents.",
    "What apps do I have open right now?",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()

    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    client = ollama.Client(host=config.get("llm", "ollama", "base_url"))
    router = SemanticIntentRouter(
        client, model,
        keep_alive=config.get(
            "llm", "ollama", "keep_alive", default=-1, required=False,
        ),
    )

    prompt = SemanticIntentRouter._build_prompt(
        user_input="x", recent_turns=[], has_screen_selection=False,
        has_selected_text=False, project_tools_available=False,
        conversation_state={}, pending_action="",
        computer_control_enabled=False,
    )
    print(f"Router prompt: {len(prompt)} chars (~{len(prompt)//4} tokens)")
    print(f"Model: {model}\n")

    # One warm-up so the first measured call is not paying for a model load.
    router.route(INPUTS[0])

    timings: list[float] = []
    for index in range(args.runs):
        for text in INPUTS:
            started = time.perf_counter()
            router.route(text)
            elapsed = time.perf_counter() - started
            timings.append(elapsed)
            print(f"  {elapsed:5.2f}s  {text[:52]!r}")
        print(f"-- run {index + 1} done --")

    ordered = sorted(timings)
    p90 = ordered[max(0, round(0.9 * len(ordered)) - 1)]
    print("\n" + "=" * 60)
    print(f"router.route() over {len(timings)} calls")
    print(f"  median {statistics.median(timings):.2f}s")
    print(f"  p90    {p90:.2f}s")
    print(f"  min    {min(timings):.2f}s")
    print(f"  max    {max(timings):.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
