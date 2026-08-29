"""Compatibility command for Elaina's canonical test runner.

The test implementation lives in ``tests/run_tests.py``. This file preserves
the established ``scripts/run_feature_regression.py --mode ...`` command and
only translates its legacy arguments; it contains no suites or test registry
of its own.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "tests" / "run_tests.py"
LEGACY_LIVE_CHECKS = (
    "router",
    "desktop-planner",
    "advice",
    "brief-response",
    "response",
)


def _run(*arguments: str) -> int:
    return subprocess.run(
        (sys.executable, str(RUNNER), *arguments),
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("quick", "live", "all"), default="quick"
    )
    parser.add_argument("--exhaustive", action="store_true")
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument("--list-features", action="store_true")
    parser.add_argument("--list-cases", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    listing = []
    if args.list_features:
        listing.append("--list-features")
    if args.list_cases:
        listing.append("--list-cases")
    for feature in args.feature:
        listing.extend(("--feature", feature))
    if listing:
        return _run(*listing)

    if args.mode in {"quick", "all"}:
        result = _run("all")
        if result:
            return result

    if args.mode in {"live", "all"}:
        command = ["live"]
        checks = list(LEGACY_LIVE_CHECKS)
        if args.feature and "computer_ui_action" not in args.feature:
            checks.remove("desktop-planner")
        if args.feature:
            checks = [name for name in checks if name in {"router", "desktop-planner"}]
        for check in checks:
            command.extend(("--check", check))
        if args.exhaustive:
            command.append("--exhaustive")
        for feature in args.feature:
            command.extend(("--feature", feature))
        return _run(*command)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
