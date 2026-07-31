"""Run transcript regressions against the user's real local Ollama model."""

from __future__ import annotations

import sys
from pathlib import Path

import ollama


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.consent import (  # noqa: E402
    AgentConsentGate,
    SemanticConsentClassifier,
)
from brain.intent_router import SemanticIntentRouter  # noqa: E402
from config.loader import Config  # noqa: E402


def main() -> int:
    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    keep_alive = config.get(
        "llm", "ollama", "keep_alive", default=-1, required=False
    )
    client = ollama.Client(
        host=config.get("llm", "ollama", "base_url")
    )
    router = SemanticIntentRouter(client, model, keep_alive=keep_alive)

    cases = [
        (
            "button complaint",
            "I think the buttons on this project look boring.",
            "agent_offer",
        ),
        (
            "direct search",
            "Can you search for when Elon Musk was born?",
            "web_search",
        ),
        (
            "project life update",
            "I'm continuing my project tonight.",
            "conversation",
        ),
        (
            "avatar advice",
            "Should I use Live2D or a 3D model for my local LLM avatar?",
            "conversation",
        ),
    ]

    failures = 0
    for name, transcript, expected in cases:
        result = router.route(transcript)
        passed = result.intent == expected
        failures += 0 if passed else 1
        print(
            f"{'PASS' if passed else 'FAIL'} {name}: "
            f"expected={expected} actual={result.intent} "
            f"action={result.action_requested}"
        )

    gate = AgentConsentGate(expiry_seconds=300)
    offer = gate.offer(
        intent="project_edit",
        request="Redesign the project's buttons.",
    )
    consent = SemanticConsentClassifier(
        client,
        model,
        keep_alive=keep_alive,
    ).classify("Yeah, let's do that.", offer)
    consent_passed = consent.decision == "accept"
    failures += 0 if consent_passed else 1
    print(
        f"{'PASS' if consent_passed else 'FAIL'} contextual consent: "
        f"expected=accept actual={consent.decision}"
    )

    print(f"\n{5 - failures}/5 live routing checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
