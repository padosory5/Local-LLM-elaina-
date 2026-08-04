"""Run data-driven transcript regressions against the configured Ollama model.

By default this runs one smoke case per feature. Pass ``--all`` to run every
paraphrase in tests/feature_matrix.json. The script only calls the semantic
router; it never executes project, Git, agent-install, or calendar writes.
"""

from __future__ import annotations

import argparse
import json
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
from scripts.console_style import status_label  # noqa: E402
from security.computer_consent import ComputerConsentGate  # noqa: E402


DEFAULT_MATRIX = PROJECT_ROOT / "tests" / "feature_matrix.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run smoke and extended variants instead of smoke cases only.",
    )
    parser.add_argument(
        "--feature",
        action="append",
        default=[],
        help="Run only this feature group; may be supplied more than once.",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Path to a feature matrix JSON file.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Optional development limit after filtering.",
    )
    parser.add_argument(
        "--skip-consent",
        action="store_true",
        help="Skip the contextual agent-consent model check.",
    )
    return parser.parse_args()


def load_cases(args: argparse.Namespace) -> list[dict]:
    payload = json.loads(args.matrix.read_text(encoding="utf-8"))
    cases = list(payload["cases"])
    if not args.all:
        cases = [case for case in cases if case["tier"] == "smoke"]
    if args.feature:
        selected = set(args.feature)
        cases = [case for case in cases if case["feature"] in selected]
    if args.max_cases > 0:
        cases = cases[: args.max_cases]
    return cases


def mismatches(result, expected: dict) -> list[str]:
    differences = []
    for field, expected_value in expected.items():
        actual_value = getattr(result, field, None)
        if actual_value != expected_value:
            differences.append(
                f"{field}: expected={expected_value!r} actual={actual_value!r}"
            )
    return differences


def main() -> int:
    args = parse_args()
    cases = load_cases(args)
    if not cases:
        print("No feature cases matched the selected filters.")
        return 2

    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    keep_alive = config.get(
        "llm", "ollama", "keep_alive", default=-1, required=False
    )
    client = ollama.Client(host=config.get("llm", "ollama", "base_url"))
    router = SemanticIntentRouter(client, model, keep_alive=keep_alive)

    failures = 0
    tier = "all" if args.all else "smoke"
    print(
        f"Testing {model} with {len(cases)} {tier} routing cases "
        f"from {args.matrix.name}.\n"
    )

    for case in cases:
        try:
            result = router.route(
                case["input"],
                **dict(case.get("route_kwargs", {})),
            )
            differences = mismatches(result, case["expected"])
        except Exception as error:
            result = None
            differences = [f"raised {type(error).__name__}: {error}"]

        passed = not differences
        failures += 0 if passed else 1
        print(
            f"[{status_label(passed)}] {case['id']} "
            f"({case['feature']}): {case['input']}"
        )
        if result is not None:
            print(
                "  actual: "
                f"intent={result.intent} action={result.action_requested} "
                f"target={result.action_target or '(none)'} "
                f"operation={result.computer_operation} "
                f"location={result.computer_location or '(none)'} "
                f"freshness={result.information_freshness} "
                f"external={result.requires_external_evidence} "
                f"recommendation={result.recommendation_needed} "
                f"domain={result.advice_domain} urgent={result.urgent_safety}"
            )
        for difference in differences:
            print(f"  mismatch: {difference}")

    consent_checks = 0
    if not args.skip_consent and not args.feature:
        consent_checks = 5
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
            f"[{status_label(consent_passed)}] contextual_consent: "
            f"expected='accept' actual={consent.decision!r}"
        )

        computer_consent_cases = (
            ("computer_consent_accept", "Yeah, go ahead.", "accept"),
            ("computer_consent_reject", "No, leave it closed.", "reject"),
            (
                "computer_consent_modify",
                "Actually, open Steam instead.",
                "modify",
            ),
            (
                "computer_consent_unrelated",
                "What's the weather tomorrow?",
                "unrelated",
            ),
        )
        for name, reply, expected in computer_consent_cases:
            pending = ComputerConsentGate(expiry_seconds=90).offer(
                target_name="Discord",
                entry_id="test-discord-entry",
            )
            decision = SemanticConsentClassifier(
                client,
                model,
                keep_alive=keep_alive,
            ).classify(reply, pending)
            passed = decision.decision == expected
            if expected == "modify":
                passed = passed and "steam" in decision.modified_request.casefold()
            failures += 0 if passed else 1
            print(
                f"[{status_label(passed)}] {name}: expected={expected!r} "
                f"actual={decision.decision!r}"
            )

    total = len(cases) + consent_checks
    print(f"\n{total - failures}/{total} live routing checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
