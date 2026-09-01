"""The one entry point for Elaina's tests.

Everything runnable lives behind this file:

    python tests/run_tests.py            # the complete deterministic suite
    python tests/run_tests.py smoke      # "can she still start and take a turn"
    python tests/run_tests.py intent     # one category of unit tests
    python tests/run_tests.py live       # the checks that need a real model

There used to be two entry points (``scripts/run_feature_regression.py`` and a
bare ``unittest discover``) plus nineteen live check scripts that could only be
found by listing the ``scripts`` folder. That is why a check written for one
phase stopped being run in the next one. Nothing is hidden now: ``--list``
prints every suite, every category, and every live check with what it needs.

Suites
------

``smoke``   ``tests/test_smoke.py`` only -- config, event bus, WebSocket,
            tool and agent registries, and one whole turn through
            ``ChatEngine.chat()``. Seconds, no external services.
``unit``    every ``tests/test_*.py`` module.
``static``  byte-compile the Python packages and ``node --check`` the
            Electron sources.
``all``     ``unit`` + ``static``. The default, and the complete suite that
            needs nothing running.
``live``    the ``scripts/live_*_check.py`` checks. These need Ollama, and
            some need a real browser, a real app, or the running backend --
            so they are chosen by ``--tier`` and never run by ``all``.

Any category name (see ``--list``) runs that slice of the unit tests.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.console_style import status_label  # noqa: E402


TESTS_DIR = PROJECT_ROOT / "tests"
MATRIX_PATH = TESTS_DIR / "feature_matrix.json"

SMOKE_MODULE = "test_smoke"


# --------------------------------------------------------------- categories
#
# A category is a filter over the unit tests, not a second place they live:
# ``unit`` always runs every discovered module, so a module missing from this
# map is still tested. ``tests/test_suite_registry.py`` fails when one is
# missing, which is what keeps the map honest as tests are added.

CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "smoke": (
        "startup and one whole turn",
        (SMOKE_MODULE,),
    ),
    "intent": (
        "what she understood the user to mean",
        (
            "test_front_door",
            "test_hotel_and_subject_regressions",
            "test_goal_and_capability",
            "test_intent_router",
            "test_korean_requests",
            "test_personality_routing",
            "test_router_surface_policy",
            "test_polite_requests",
            "test_task_intent_gate",
        ),
    ),
    "planning": (
        "what she decided to do about it",
        (
            "test_action_commitment",
            "test_browser_action_planner",
            "test_calculation_planner",
            "test_clarification_gate",
            "test_deliberation_goal",
            "test_desktop_action_planner",
            "test_desktop_resume",
            "test_interaction_decision",
            "test_media_target",
            "test_task_discovery_locale",
            "test_task_discovery_policy",
            "test_research_recall",
            "test_task_extractor",
            "test_task_planner",
            "test_web_search_planner",
        ),
    ),
    "conversation": (
        "how the answer comes out",
        (
            "test_action_status",
            "test_active_task_continuity",
            "test_answer_condenser",
            "test_brief_response",
            "test_capabilities",
            "test_candidate_fit",
            "test_candidate_shape",
            "test_capability_rescue",
            "test_context_policy",
            "test_conversation_focus",
            "test_followup_subject",
            "test_grounded_values",
            "test_response_cases",
            "test_response_language",
            "test_response_policy",
            "test_response_quality",
            "test_social_lines",
            "test_speak_window_list",
            "test_spoken_label",
            "test_text_filter",
            "test_unfinished_sentence",
            "test_user_locale",
            "test_user_profile",
        ),
    ),
    "permission": (
        "what she must ask before doing",
        (
            "test_agent_consent_flow",
            "test_capability_offer",
            "test_computer_consent",
            "test_computer_control_mode",
            "test_preferences",
            "test_recommendation_policy",
            "test_recommendation_state",
            "test_agency_offers",
            "test_task_consent",
            "test_task_strategy_consent",
        ),
    ),
    "browser": (
        "the CDP and screen-native browser drivers",
        (
            "test_browser_connection",
            "test_browser_control",
            "test_browser_observer",
            "test_browser_outcome",
            "test_browser_overlays",
            "test_browser_service",
            "test_browser_service_timeout",
            "test_safe_browser",
            "test_screen_browser_control",
            "test_screen_browser_service",
            "test_screen_browser_window",
            "test_screen_page_observer",
        ),
    ),
    "desktop": (
        "native Windows control and the real cursor",
        (
            "test_action_contract",
            "test_computer_control",
            "test_cursor_driver",
            "test_desktop_surface_context",
            "test_input_watcher",
            "test_screen_ui_control",
            "test_session_action_memory",
            "test_session_item_memory",
            "test_surface_control",
            "test_windows_app_catalog",
            "test_windows_process_control",
            "test_windows_ui_control",
            "test_windows_ui_observer",
        ),
    ),
    "tools": (
        "the tools themselves, and how they are registered",
        (
            "test_calculator",
            "test_console_style",
            "test_feature_matrix",
            "test_live_router_check",
            "test_paths",
            "test_suite_registry",
            "test_tool_registry",
            "test_tool_selection",
            "test_tool_surface_policy",
            "test_web_search_tool",
        ),
    ),
    "agents": (
        "the agent layer",
        (
            "test_agent_system",
            "test_research_agent",
        ),
    ),
    "voice": (
        "microphone, VAD and echo handling",
        (
            "test_audio_manager_echo_window",
            "test_audio_manager_language",
            "test_stt_echo_detection",
            "test_stt_transcription_guard",
            "test_vad_audio",
        ),
    ),
    "ui": (
        "what Electron is told, and what it may send back",
        (
            "test_desktop_control_ui",
            "test_phase3a_controls",
        ),
    ),
    "integration": (
        "whole turns, end to end, with only the model and machine replaced",
        (
            "test_browser_action_flow",
            "test_browser_navigation_flow",
            "test_computer_action_flow",
            "test_execution_preferences",
            "test_media_play_flow",
            "test_turn_behaviour",
        ),
    ),
}

SUITES = ("all", "unit", "static", "live")


# ------------------------------------------------------------- live checks

@dataclass(frozen=True)
class LiveCheck:
    """One ``scripts/live_*_check.py``, and what it needs to be true."""

    name: str
    script: str
    tier: str
    summary: str


LIVE_TIERS: dict[str, str] = {
    "model": "needs Ollama only -- nothing on screen moves",
    "browser": "drives a real browser window",
    "desktop": "drives the real mouse and keyboard against a real app",
    "app": "needs the Elaina backend already running (main.py)",
}

LIVE_CHECKS: tuple[LiveCheck, ...] = (
    LiveCheck(
        "router", "live_router_check.py", "model",
        "the feature matrix, routed by the real model",
    ),
    LiveCheck(
        "tool", "live_tool_check.py", "model",
        "which surface satisfies the request: none, search, page or machine",
    ),
    LiveCheck(
        "agency", "live_agency_check.py", "model",
        "answer/offer/ask/act, and what a reply to an offer resolves to",
    ),
    LiveCheck(
        "desktop-planner", "live_desktop_planner_check.py", "model",
        "desktop planning against a simulated UI tree",
    ),
    LiveCheck(
        "task-planner", "live_task_planner_check.py", "model",
        "multi-step planning against simulated capabilities",
    ),
    LiveCheck(
        "information-need", "live_information_need_check.py", "model",
        "the Information Acquisition layer's freshness and verification calls",
    ),
    LiveCheck(
        "advice", "live_advice_check.py", "model",
        "short, actionable spoken advice",
    ),
    LiveCheck(
        "brief-response", "live_brief_response_check.py", "model",
        "short-response variety, with no action tools",
    ),
    LiveCheck(
        "response", "live_response_check.py", "model",
        "answer completion on representative calculation cases",
    ),
    LiveCheck(
        "booking-gate", "live_booking_gate_check.py", "model",
        "a booking is asked about before anything is opened",
    ),
    LiveCheck(
        "browser-stress", "live_browser_stress_check.py", "browser",
        "cold launch, scan and one click against real sites, no model",
    ),
    LiveCheck(
        "consent-wall", "live_consent_wall_check.py", "browser",
        "reject-not-accept on real cookie-wall DOMs",
    ),
    LiveCheck(
        "screen-browser", "live_screen_browser_check.py", "browser",
        "the screen-native driver on the window you already have open",
    ),
    LiveCheck(
        "screen-browser-task", "live_screen_browser_task_check.py", "browser",
        "a whole multi-turn browser goal, planner and model included",
    ),
    LiveCheck(
        "source-preference", "live_source_preference_check.py", "browser",
        "a saved source preference yields concrete live-page candidates",
    ),
    LiveCheck(
        "desktop-control", "live_desktop_control_check.py", "desktop",
        "whole-desktop cursor control against Spotify",
    ),
    LiveCheck(
        "spotify-track", "live_spotify_exact_track_check.py", "desktop",
        "the exact named track plays, or nothing is clicked",
    ),
    LiveCheck(
        "media-request", "live_media_request_check.py", "desktop",
        "what a media request actually named",
    ),
    LiveCheck(
        "clarification", "live_clarification_check.py", "desktop",
        "the clarification gate's three exits",
    ),
    LiveCheck(
        "learning", "live_learning_check.py", "desktop",
        "she learns which one you meant, and says so",
    ),
    LiveCheck(
        "skill", "live_skill_check.py", "desktop",
        "one run per media skill",
    ),
    LiveCheck(
        "conversation", "live_conversation_check.py", "app",
        "the running process, over its own WebSocket channel",
    ),
)


COMPILED_PACKAGES = (
    "agents", "brain", "config", "core", "memory",
    "scripts", "security", "tests", "tools", "vision", "voice", "main.py",
)


# ------------------------------------------------------------------ helpers

def discovered_modules() -> list[str]:
    """Every unit-test module on disk, by bare module name."""
    return sorted(path.stem for path in TESTS_DIR.glob("test_*.py"))


def categorized_modules() -> dict[str, str]:
    """module name -> the one category that claims it."""
    owner: dict[str, str] = {}
    for category, (_summary, modules) in CATEGORIES.items():
        for module in modules:
            owner[module] = category
    return owner


def run_unittest(modules: list[str], *, verbose: bool, failfast: bool) -> bool:
    """Run modules in-process, buffering their console noise.

    Elaina's tests print her turn logs. Buffering keeps a passing run
    readable and still shows the output of anything that fails.
    """
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        loader.loadTestsFromName(f"tests.{module}") for module in modules
    )
    runner = unittest.TextTestRunner(
        verbosity=2 if verbose else 1,
        buffer=True,
        failfast=failfast,
    )
    return runner.run(suite).wasSuccessful()


def run_command(
    command: tuple[str, ...],
    *,
    env: dict[str, str] | None = None,
) -> bool:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        env=env,
    ).returncode == 0


def compile_sources() -> bool:
    # Compilation should validate a checkout without populating it with
    # __pycache__ files or requiring the source tree itself to be writable.
    with tempfile.TemporaryDirectory(prefix="elaina-pycache-") as cache:
        env = os.environ.copy()
        env["PYTHONPYCACHEPREFIX"] = cache
        return run_command(
            (sys.executable, "-m", "compileall", "-q") + COMPILED_PACKAGES,
            env=env,
        )


def check_javascript() -> bool:
    files = sorted(
        path
        for path in (PROJECT_ROOT / "desktop").rglob("*.js")
        if not {"node_modules", "dist", "build"}.intersection(path.parts)
    )
    try:
        for path in files:
            if not run_command(("node", "--check", str(path))):
                return False
    except FileNotFoundError:
        print("Node.js was not found on PATH.")
        return False
    return True


def live_command(check: LiveCheck, args: argparse.Namespace) -> tuple[str, ...]:
    command = [sys.executable, f"scripts/{check.script}"]
    if check.name == "router":
        if args.exhaustive:
            command.append("--all")
        for feature in args.feature:
            command.extend(("--feature", feature))
    return tuple(command)


def matrix_cases() -> list[dict]:
    return list(json.loads(MATRIX_PATH.read_text(encoding="utf-8"))["cases"])


def expected_summary(expected: dict) -> str:
    fields = (
        "intent",
        "computer_operation",
        "action_target",
        "computer_location",
        "action_requested",
    )
    return ", ".join(
        f"{field}={expected[field]!r}" for field in fields if field in expected
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


def print_listing() -> None:
    print("Suites:")
    print("  all      unit + static (the default; needs nothing running)")
    print("  unit     every tests/test_*.py module")
    print("  static   byte-compile Python, node --check the Electron sources")
    print("  live     the scripts/live_*_check.py checks (see --tier)")
    print("\nUnit-test categories:")
    for category, (summary, modules) in CATEGORIES.items():
        print(f"  {category:<13} {len(modules):>2} modules   {summary}")
    print("\nLive checks (python tests/run_tests.py live --check NAME):")
    for tier, description in LIVE_TIERS.items():
        print(f"\n  --tier {tier}  ({description})")
        for check in LIVE_CHECKS:
            if check.tier == tier:
                print(f"    {check.name:<20} {check.summary}")
    owner = categorized_modules()
    missing = [name for name in discovered_modules() if name not in owner]
    if missing:
        print("\nNot in any category (still run by 'unit'):")
        for name in missing:
            print(f"  {name}")


# --------------------------------------------------------------------- main

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "suite",
        nargs="?",
        default="all",
        help="all, unit, static, live, or a category name (default: all).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print every suite, category and live check, then exit.",
    )
    parser.add_argument(
        "--tier",
        choices=tuple(LIVE_TIERS),
        default="model",
        help="Which live checks to run (default: model, the safe ones).",
    )
    parser.add_argument(
        "--check",
        action="append",
        default=[],
        help="Run one live check by name; may be repeated.",
    )
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="Live routing uses every matrix paraphrase, not one per feature.",
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
        help="Print the matrix phrases without calling Ollama.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Name every unit test as it runs.",
    )
    parser.add_argument(
        "--failfast",
        action="store_true",
        help="Stop at the first failing unit test.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.list:
        print_listing()
        return 0

    counts = Counter(case["feature"] for case in matrix_cases())
    if args.list_features:
        for feature in sorted(counts):
            print(f"{feature}: {counts[feature]} variants")
        print(
            f"Total: {sum(counts.values())} cases across {len(counts)} features"
        )
        return 0

    unknown = set(args.feature) - set(counts)
    if unknown:
        print("Unknown feature group(s): " + ", ".join(sorted(unknown)))
        return 2

    if args.list_cases:
        cases = matrix_cases()
        if args.feature:
            selected = set(args.feature)
            cases = [case for case in cases if case["feature"] in selected]
        print_cases(cases)
        return 0

    suite = args.suite
    if suite not in SUITES and suite not in CATEGORIES:
        print(f"Unknown suite or category: {suite}")
        print("Run 'python tests/run_tests.py --list' to see what exists.")
        return 2

    stages: list[tuple[str, Callable[[], bool]]] = []

    if suite in CATEGORIES:
        modules = list(CATEGORIES[suite][1])
        stages.append((
            f"{suite} tests ({len(modules)} modules)",
            lambda: run_unittest(
                modules, verbose=args.verbose, failfast=args.failfast
            ),
        ))
    if suite in {"all", "unit"}:
        modules = discovered_modules()
        stages.append((
            f"unit tests ({len(modules)} modules)",
            lambda: run_unittest(
                modules, verbose=args.verbose, failfast=args.failfast
            ),
        ))
    if suite in {"all", "static"}:
        stages.append(("Python compilation", compile_sources))
        stages.append(("JavaScript syntax", check_javascript))
    if suite == "live":
        wanted = set(args.check) - {check.name for check in LIVE_CHECKS}
        if wanted:
            print("Unknown live check(s): " + ", ".join(sorted(wanted)))
            return 2
        selected = [
            check for check in LIVE_CHECKS
            if (check.name in args.check)
            or (not args.check and check.tier == args.tier)
        ]
        if not args.check:
            print(f"Tier '{args.tier}': {LIVE_TIERS[args.tier]}.\n")
        for check in selected:
            command = live_command(check, args)
            stages.append((
                f"live {check.name}",
                lambda command=command: run_command(command),
            ))

    print(f"Running {len(stages)} stage(s) for '{suite}'.\n")

    failures = 0
    started = time.perf_counter()
    for name, stage in stages:
        print(f"=== {name} ===", flush=True)
        stage_started = time.perf_counter()
        passed = stage()
        failures += 0 if passed else 1
        print(
            f"[{status_label(passed)}] {name} "
            f"({time.perf_counter() - stage_started:.1f}s)\n",
            flush=True,
        )

    print(
        f"{suite}: {len(stages) - failures}/{len(stages)} stages passed "
        f"in {time.perf_counter() - started:.1f}s."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
