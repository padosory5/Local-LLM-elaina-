"""Run Elaina's deterministic and optional live-model feature regressions."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.console_style import status_label  # noqa: E402


MATRIX_PATH = PROJECT_ROOT / "tests" / "feature_matrix.json"


@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("quick", "live", "all"),
        default="quick",
        help="quick=deterministic, live=Ollama, all=both (default: quick).",
    )
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="Use every router paraphrase instead of one smoke case per feature.",
    )
    parser.add_argument(
        "--feature",
        action="append",
        default=[],
        help="Limit live routing to a feature group; may be repeated.",
    )
    parser.add_argument(
        "--list-features",
        action="store_true",
        help="Print matrix feature names and variant counts, then exit.",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help=(
            "Print the natural-language test phrases and expected decisions "
            "without calling Ollama; combine with --feature to filter."
        ),
    )
    return parser.parse_args()


def matrix_cases() -> list[dict]:
    payload = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


def expected_summary(expected: dict) -> str:
    fields = (
        "intent",
        "computer_operation",
        "action_target",
        "computer_location",
        "action_requested",
    )
    return ", ".join(
        f"{field}={expected[field]!r}"
        for field in fields
        if field in expected
    )


def print_cases(cases: list[dict]) -> None:
    current_feature = None
    for case in cases:
        if case["feature"] != current_feature:
            current_feature = case["feature"]
            print(f"\n{current_feature}:")
        print(f"  [{case['tier']}] {case['input']}")
        print(f"    -> {expected_summary(case['expected'])}")
    print(f"\nTotal: {len(cases)} test phrases")


def quick_checks() -> list[Check]:
    python = sys.executable
    return [
        Check(
            "unit tests",
            (python, "-m", "unittest", "discover", "-s", "tests", "-q"),
        ),
        Check(
            "Python source compilation",
            (
                python,
                "-m",
                "compileall",
                "-q",
                "agents",
                "brain",
                "config",
                "core",
                "memory",
                "scripts",
                "security",
                "tests",
                "tools",
                "vision",
                "voice",
                "main.py",
            ),
        ),
    ]


def live_checks(args: argparse.Namespace) -> list[Check]:
    python = sys.executable
    router_command = [python, "scripts/live_router_check.py"]
    if args.exhaustive:
        router_command.append("--all")
    for feature in args.feature:
        router_command.extend(("--feature", feature))

    checks = [Check("live semantic routing", tuple(router_command))]
    if not args.feature or "computer_ui_action" in args.feature:
        checks.append(Check(
            "live simulated desktop planner",
            (python, "scripts/live_desktop_planner_check.py"),
        ))
    if not args.feature:
        checks.extend([
            Check("live voice advice", (python, "scripts/live_advice_check.py")),
            Check(
                "live brief response variety",
                (python, "scripts/live_brief_response_check.py"),
            ),
            Check(
                "live calculation responses",
                (python, "scripts/live_response_check.py"),
            ),
        ])
    return checks


def check_javascript() -> tuple[bool, float]:
    started = time.perf_counter()
    files = sorted(
        path
        for path in (PROJECT_ROOT / "desktop").rglob("*.js")
        if not {"node_modules", "dist", "build"}.intersection(path.parts)
    )
    for path in files:
        result = subprocess.run(
            ("node", "--check", str(path)),
            cwd=PROJECT_ROOT,
            check=False,
        )
        if result.returncode:
            return False, time.perf_counter() - started
    return True, time.perf_counter() - started


def run_check(check: Check) -> tuple[bool, float]:
    started = time.perf_counter()
    result = subprocess.run(
        check.command,
        cwd=PROJECT_ROOT,
        check=False,
    )
    return result.returncode == 0, time.perf_counter() - started


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cases = matrix_cases()
    counts = Counter(case["feature"] for case in cases)
    if args.list_features:
        for feature in sorted(counts):
            print(f"{feature}: {counts[feature]} variants")
        print(f"Total: {sum(counts.values())} cases across {len(counts)} features")
        return 0

    unknown = set(args.feature) - set(counts)
    if unknown:
        print("Unknown feature group(s): " + ", ".join(sorted(unknown)))
        return 2

    if args.list_cases:
        if args.feature:
            selected = set(args.feature)
            cases = [case for case in cases if case["feature"] in selected]
        print_cases(cases)
        return 0

    checks: list[Check] = []
    if args.mode in {"quick", "all"}:
        checks.extend(quick_checks())
    if args.mode in {"live", "all"}:
        checks.extend(live_checks(args))

    print(
        f"Running {len(checks)} regression stages in {args.mode} mode "
        f"({'exhaustive' if args.exhaustive else 'smoke'} live matrix).\n"
    )
    failures = 0
    for check in checks:
        print(f"=== {check.name} ===", flush=True)
        passed, duration = run_check(check)
        failures += 0 if passed else 1
        print(
            f"[{status_label(passed)}] {check.name} "
            f"({duration:.1f}s)\n",
            flush=True,
        )

    if args.mode in {"quick", "all"}:
        print("=== JavaScript syntax ===", flush=True)
        try:
            passed, duration = check_javascript()
        except FileNotFoundError:
            passed, duration = False, 0.0
            print("Node.js was not found on PATH.")
        failures += 0 if passed else 1
        print(
            f"[{status_label(passed)}] JavaScript syntax "
            f"({duration:.1f}s)\n",
            flush=True,
        )

    total = len(checks) + (1 if args.mode in {"quick", "all"} else 0)
    print(f"Regression result: {total - failures}/{total} stages passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
