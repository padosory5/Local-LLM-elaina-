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
            "latest completed tournament",
            "Who won the latest FIFA World Cup?",
            "web_search",
        ),
        (
            "latest model release",
            "When was the latest Qwen series released?",
            "web_search",
        ),
        (
            "live exchange rate",
            "What is the current exchange rate from US dollars to Korean won?",
            "web_search",
        ),
        (
            "historical exchange rate",
            "What was the USD to KRW rate on January 15, 2024?",
            "web_search",
        ),
        (
            "stable exchange rate concept",
            "Why do currency exchange rates fluctuate?",
            "knowledge_question",
        ),
        (
            "grounded medication recommendation",
            "Can you recommend something I can take for insomnia?",
            "web_search",
        ),
        (
            "urgent medication danger",
            "I took too many sleep pills and I'm struggling to breathe. What should I do?",
            "conversation",
        ),
        (
            "current weather",
            "What is the weather in Seoul right now?",
            "web_search",
        ),
        (
            "changing public statistic",
            "What is South Korea's current population?",
            "web_search",
        ),
        (
            "optional screen identification",
            "I wonder who drew this picture.",
            "agent_offer",
        ),
        (
            "natural git publish wording",
            "Please publish everything I changed to the remote repository.",
            "git_publish",
        ),
        (
            "natural local commit wording",
            "Record my current work in Git history, but don't upload it.",
            "git_commit",
        ),
        (
            "natural project inspection wording",
            "Inspect the codebase and explain how voice input reaches chat.",
            "project_question",
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
        (
            "proportional distribution",
            (
                "We put in 100, 100, and 50 dollars and made 650 dollars. "
                "What's the distribution?"
            ),
            "calculation",
        ),
    ]

    failures = 0
    for name, transcript, expected in cases:
        result = router.route(transcript)
        requires_verification = name in {
            "latest completed tournament",
            "latest model release",
            "live exchange rate",
            "current weather",
            "changing public statistic",
            "grounded medication recommendation",
        }
        passed = (
            result.intent == expected
            and (
                name != "grounded medication recommendation"
                or (
                    result.recommendation_needed
                    and result.advice_domain == "health"
                )
            )
            and (
                name != "urgent medication danger"
                or result.urgent_safety
            )
            and (
                not requires_verification
                or result.verification_required
            )
        )
        failures += 0 if passed else 1
        print(
            f"{'PASS' if passed else 'FAIL'} {name}: "
            f"expected={expected} actual={result.intent} "
            f"action={result.action_requested} "
            f"freshness={result.information_freshness} "
            f"external={result.requires_external_evidence} "
            f"recommendation={result.recommendation_needed} "
            f"domain={result.advice_domain} "
            f"urgent={result.urgent_safety} "
            f"verify={result.verification_required}"
        )

    screen_result = router.route(
        "Look across both of my monitors and tell me what is visible."
    )
    screen_passed = (
        screen_result.intent == "screen_analysis"
        and screen_result.screen_target == "all"
        and screen_result.action_requested
    )
    failures += 0 if screen_passed else 1
    print(
        f"{'PASS' if screen_passed else 'FAIL'} semantic screen target: "
        f"intent={screen_result.intent} target={screen_result.screen_target} "
        f"action={screen_result.action_requested}"
    )

    memory_result = router.route(
        "Explain in depth what you remember about my favorite foods."
    )
    memory_passed = (
        memory_result.memory_relevant
        and memory_result.detailed_response
    )
    failures += 0 if memory_passed else 1
    print(
        f"{'PASS' if memory_passed else 'FAIL'} semantic response metadata: "
        f"memory={memory_result.memory_relevant} "
        f"detailed={memory_result.detailed_response}"
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

    total_checks = len(cases) + 3
    print(
        f"\n{total_checks - failures}/{total_checks} "
        "live routing checks passed."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
