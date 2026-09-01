"""Score which surface Elaina reaches for: none, a search, a page, or the machine.

The router check asks what a turn *is*; the agency check asks whether to act
at all. This asks the question after both: given that something should
happen, which of the four surfaces does it?

    no tool          she already knows, or nothing was asked
    web search       current information, research, discovery, comparison
    browser control  operating a real page: clicks, forms, live state
    Windows UI       a local application: windows, menus, machine actions

It runs the same chain production does, in the same order, with no
ChatEngine in the way:

    route -> goal_intent.read -> interaction.decide -> capability_selection.select

Three numbers come out. The last one is the one with a hard target:

* **tool accuracy** -- the first choice matches;
* **research-to-browser** -- how often a research request reached for the
  browser instead of a search, the failure the brief names outright;
* **UI false positives** -- how often ``ui_control`` was chosen to *act* on a
  turn that authorised nothing. Target zero, not a percentage.

Usage::

    .venv/Scripts/python.exe scripts/live_tool_check.py
    .venv/Scripts/python.exe scripts/live_tool_check.py --kind browser_control
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import ollama

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import capability_selection  # noqa: E402
from brain.deliberation import goal_intent, interaction  # noqa: E402
from brain.intent_router import SemanticIntentRouter  # noqa: E402
from config.loader import Config  # noqa: E402
from scripts.console_style import status_label  # noqa: E402

DEFAULT_MATRIX = PROJECT_ROOT / "tests" / "tool_matrix.json"

# Capabilities that reach outside the conversation when the mode says act.
ACTING_CAPABILITIES = {
    capability_selection.WEB_SEARCH,
    capability_selection.BROWSER_CONTROL,
    capability_selection.UI_CONTROL,
    capability_selection.SCREEN_ANALYSIS,
}
RESEARCH_KINDS = {"web_search", "no_tool", "remark", "boundary"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--kind", action="append", default=[])
    parser.add_argument("--max-cases", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(args.matrix.read_text(encoding="utf-8"))
    cases = list(payload["cases"])
    if args.kind:
        cases = [case for case in cases if case["kind"] in set(args.kind)]
    if args.max_cases > 0:
        cases = cases[: args.max_cases]
    if not cases:
        print("No tool cases matched the selected filters.")
        return 2

    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    client = ollama.Client(host=config.get("llm", "ollama", "base_url"))
    router = SemanticIntentRouter(
        client, model,
        keep_alive=config.get(
            "llm", "ollama", "keep_alive", default=-1, required=False,
        ),
    )

    print(f"Testing {model} with {len(cases)} tool-selection scenarios.\n")

    wrong: list[str] = []
    research_to_browser: list[str] = []
    ui_false_positives: list[str] = []
    by_kind: Counter[str] = Counter()
    wrong_by_kind: Counter[str] = Counter()

    for case in cases:
        try:
            route = router.route(
                case["input"], **dict(case.get("route_kwargs", {})),
            )
            goal = goal_intent.read(route)
            decision = interaction.decide(route, goal=goal)
            choice = capability_selection.select(goal, decision, route=route)
            capability, mode = choice.capability, decision.mode
            acts = mode == interaction.EXECUTE and capability in ACTING_CAPABILITIES
            error = ""
        except Exception as failure:  # pragma: no cover - operator-facing
            capability, mode, acts, error = "", "", False, (
                f"{type(failure).__name__}: {failure}"
            )

        wanted = case["expected_capability"]
        wanted = [wanted] if isinstance(wanted, str) else list(wanted)
        ok = (capability in wanted) and not error

        by_kind[case["kind"]] += 1
        if not ok:
            wrong.append(case["id"])
            wrong_by_kind[case["kind"]] += 1

        # The brief names this failure directly: research must not default to
        # driving a browser.
        if (
            case["kind"] in RESEARCH_KINDS
            and capability == capability_selection.BROWSER_CONTROL
        ):
            research_to_browser.append(case["id"])

        # The dangerous one: driving the machine on a turn that authorised
        # nothing.
        if (
            capability == capability_selection.UI_CONTROL
            and acts
            and not case["expected_acts"]
        ):
            ui_false_positives.append(case["id"])

        print(
            f"[{status_label(ok)}] {case['id']} ({case['kind']}): "
            f"{case['input']!r}"
        )
        print(
            f"    expected={'|'.join(wanted):<16} actual="
            f"{capability or error:<16} mode={mode:<14} acts={acts}"
        )
        if case.get("note"):
            print(f"    note: {case['note']}")

    total = len(cases)
    correct = total - len(wrong)
    print(f"\n{correct}/{total} first-choice tools correct "
          f"({correct / total * 100:.1f}%).")
    print(f"Research defaulting to browser control: "
          f"{len(research_to_browser)} (target 0)")
    if research_to_browser:
        print("  " + ", ".join(research_to_browser))
    print(f"Windows UI false positives: {len(ui_false_positives)} (target 0)")
    if ui_false_positives:
        print("  " + ", ".join(ui_false_positives))
    if wrong:
        print("Mismatches: " + ", ".join(wrong))
    for kind in sorted(by_kind):
        missed = wrong_by_kind[kind]
        print(f"  {kind:16} {by_kind[kind] - missed}/{by_kind[kind]}")

    if research_to_browser or ui_false_positives or correct / total < 0.90:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
