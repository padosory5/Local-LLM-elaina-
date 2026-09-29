"""Write docs/RESPONSE_PIPELINE.md: every stage of the reply, in order, measured.

Three sources, and the document says which fact came from which:

* **The registry** (brain/response_stages.py): what each stage is for,
  whether it is HARD, SOFT or MIXED, why it exists, what it must follow,
  the conflicts on record. Judgment, reviewed by a person.
* **The source** (brain/chat_engine.py, parsed): where each stage is
  called, in what order, under what condition, and how many times.
* **The records**: which tests exercise each stage
  (scripts/stage_test_coverage.py) and how often it changes a real reply
  (runtime/turn_trace/ of any runtime passed with --traces).

    .venv/Scripts/python.exe scripts/stage_test_coverage.py
    .venv/Scripts/python.exe scripts/response_pipeline_report.py \
        --traces runtime <another runtime> ...

Regenerate rather than edit: the document is only as true as its sources.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import response_stages as registry  # noqa: E402
from core.paths import DEBUG_DIRECTORY  # noqa: E402


ENGINE = PROJECT_ROOT / "brain" / "chat_engine.py"
TESTS = PROJECT_ROOT / "tests"
OUT = PROJECT_ROOT / "docs" / "RESPONSE_PIPELINE.md"
COVERAGE = Path(DEBUG_DIRECTORY) / "stage_coverage.json"


# ------------------------------------------------------------------ source


@dataclass
class Site:
    order: int
    name: str
    line: int
    condition: str
    occurrence: int


def _conditions(source: str, function: ast.FunctionDef) -> list[Site]:
    """Every recorded stage in _answer_turn, in source order, with the
    innermost conditions it sits under."""
    sites: list[Site] = []

    def text(node) -> str:
        said = ast.get_source_segment(source, node) or ""
        return " ".join(said.split())

    def visit(node, conditions: tuple[str, ...]) -> None:
        if isinstance(node, ast.If):
            test = text(node.test)
            for child in node.body:
                visit(child, conditions + (test,))
            for child in node.orelse:
                visit(child, conditions + (f"not ({test})",))
            return
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "step"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "turn_trace"
        ):
            sites.append(Site(0, node.args[0].value, node.lineno,
                              " and ".join(conditions[-2:]), 0))
        for child in ast.iter_child_nodes(node):
            visit(child, conditions)

    for statement in function.body:
        visit(statement, ())
    sites.sort(key=lambda site: site.line)
    seen: dict[str, int] = defaultdict(int)
    for order, site in enumerate(sites, 1):
        site.order = order
        seen[site.name] += 1
        site.occurrence = seen[site.name]
    return sites


def source_facts() -> tuple[list[Site], int]:
    source = ENGINE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_answer_turn"
    )
    assignments = sum(
        1 for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "reply" for t in node.targets)
    )
    return _conditions(source, function), assignments


# ------------------------------------------------------------------- tests


def direct_tests() -> dict[str, list[str]]:
    """Test modules that name a stage's own code."""
    modules = {
        path.stem: path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(TESTS.glob("test_*.py"))
        if path.stem not in {"test_response_stages", "test_turn_trace"}
    }
    found: dict[str, list[str]] = {}
    for stage in registry.STAGES:
        needles = set()
        for dotted in stage.implemented_by:
            parts = dotted.split(".")
            leaf = parts[-1]
            if leaf[:1].islower() and len(parts) == 2:
                # A module: named in an import or a dotted use.
                needles.add(re.compile(
                    rf"(?:import\s+{leaf}\b|from\s+brain\s+import[^\n]*\b{leaf}\b|"
                    rf"brain\.{leaf}\b)"
                ))
            else:
                needles.add(re.compile(rf"\b{re.escape(leaf)}\b"))
        found[stage.name] = sorted(
            name for name, text in modules.items()
            if any(needle.search(text) for needle in needles)
        )
    return found


def load_coverage(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ records


def live_rates(runtimes: list[Path]) -> tuple[dict[str, dict], int, int]:
    stats: dict[str, dict] = {}
    turns = broken = 0
    for runtime in runtimes:
        # A runtime keeps its records in turn_trace/; an evaluation run
        # (evals/run.py) keeps each scenario's in traces/.
        paths = sorted((runtime / "turn_trace").glob("*.jsonl")) + sorted(
            (runtime / "traces").glob("*.jsonl")
        )
        for path in paths:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                turns += 1
                broken += 0 if record.get("chain_ok") else 1
                for step in record.get("steps") or ():
                    if step.get("gap"):
                        continue
                    entry = stats.setdefault(step["name"], {
                        "runs": 0, "changed": 0, "dt": [], "calls": 0,
                    })
                    entry["runs"] += 1
                    entry["changed"] += 1 if step.get("changed") else 0
                    entry["dt"].append(step.get("dt") or 0.0)
                    entry["calls"] += len(step.get("model_calls") or ())
    return stats, turns, broken


# ------------------------------------------------------------------ writing


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def write(sites, assignments, tests, coverage, rates, live_turns, live_broken,
          runtimes) -> str:
    by_name = registry.BY_NAME
    names_in_order: list[str] = []
    for site in sites:
        if site.name not in names_in_order:
            names_in_order.append(site.name)
    occurrences = defaultdict(list)
    for site in sites:
        occurrences[site.name].append(site)

    counts = defaultdict(int)
    for name in names_in_order:
        counts[by_name[name].kind] += 1
    repeated = [name for name in names_in_order if len(occurrences[name]) > 1]
    model_stages = [name for name in names_in_order if by_name[name].model_call]
    last_hard = max(
        (site.order for site in sites if by_name[site.name].kind == registry.HARD
         and by_name[site.name].exists_because == registry.INVARIANT
         and site.name not in {"celsius"}),
        default=0,
    )
    after_hard = [
        site for site in sites
        if site.order > last_hard and by_name[site.name].model_call
    ]
    reasons = defaultdict(list)
    for name in names_in_order:
        reasons[by_name[name].exists_because].append(name)

    lines: list[str] = []
    add = lines.append
    add("# The reply pipeline, stage by stage")
    add("")
    add("*Generated by `scripts/response_pipeline_report.py` on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}. Do not edit by hand: "
        "change `brain/response_stages.py` or the code, and regenerate.*")
    add("")
    add("Everything between the model's draft and what the person sees and "
        "hears happens in `ChatEngine._answer_turn`. Each place the reply can "
        "change is recorded by `core/turn_trace.py` under a stage name. This "
        "document puts three things side by side for every stage: what it is "
        "(the registry, a person's judgment); where and when it runs (the "
        "source, parsed); and whether it does anything (the test suite and "
        "live turn records).")
    add("")
    add("## Summary")
    add("")
    raw_draft = [site for site in sites if site.name in {"budget_cut", "vision_retry"}]
    add(f"- **{assignments}** assignments to `reply` in `_answer_turn`: one "
        f"initial `\"\"` and **{len(sites) - len(raw_draft)}** recorded stages. "
        f"**{len(raw_draft)}** more (`budget_cut`, `vision_retry`) act on the "
        f"raw draft before it becomes the reply: **{len(sites)}** recorded "
        "sites in all.")
    add(f"- **{len(names_in_order)}** distinct stages: **{counts[registry.HARD]} "
        f"HARD**, **{counts[registry.SOFT]} SOFT**, **{counts[registry.MIXED]} "
        "MIXED**.")
    add(f"- **{len(repeated)}** stages have more than one call site: "
        + ", ".join(f"`{name}` ×{len(occurrences[name])}" for name in repeated)
        + ".")
    add(f"- **{len(model_stages)}** stages can call the model: "
        + ", ".join(f"`{name}`" for name in model_stages) + ".")
    if after_hard:
        add("- **Model text after the last hard-guard pass**: "
            + ", ".join(f"`{site.name}` (line {site.line})" for site in after_hard)
            + ". Whatever these generate reaches the person without the truth "
            "guards having seen it.")
    add("- **Why they exist**: "
        + "; ".join(f"{reason} {len(reasons[reason])}" for reason in registry.REASONS
                    if reasons[reason]) + ". *Pipeline* stages repair other "
        "stages and go away when the order is fixed; *modality* stages make "
        "screen text speakable and move to speech realisation; *model* stages "
        "are the ones evaluation can retire.")
    if coverage:
        add(f"- **Tests**: {coverage.get('tests')} tests, of which "
            f"{coverage.get('tests_with_turns')} run whole turns "
            f"({coverage.get('turns')} turns). Most stages are tested by "
            "calling their own code, not through a turn.")
    if live_turns:
        add(f"- **Live records**: {live_turns} turns from "
            f"{len(runtimes)} runtime(s), {live_broken} with a broken chain.")
    add("")

    add("## In order")
    add("")
    add("Conditional stages are recorded only when their condition holds, so "
        "for them *ran* means *fired*.")
    add("")
    add("| # | stage | class | exists because | does | model | runs when | line |")
    add("|---|---|---|---|---|---|---|---|")
    for site in sites:
        stage = by_name[site.name]
        mark = f" (#{site.occurrence})" if len(occurrences[site.name]) > 1 else ""
        add(
            f"| {site.order} | `{site.name}`{mark} | {stage.kind} | "
            f"{stage.exists_because} | {stage.action} | "
            f"{'yes' if stage.model_call else ''} | "
            f"{_cell(site.condition[:90]) or 'always'} | {site.line} |"
        )
    add("")

    add("## What each stage is, and what exercises it")
    add("")
    add("*Model calls* under **Live** are the calls made between the previous "
        "recorded stage and this one. A conditional stage that ran but kept "
        "the reply -- a length rewrite that was rejected, a premise check that "
        "found nothing -- records nothing, so its calls appear under the next "
        "stage that does. `scripts/turn_trace_report.py calls` attributes calls "
        "by the function that made them instead.")
    add("")
    stats = (coverage or {}).get("stages", {})
    for name in names_in_order:
        stage = by_name[name]
        add(f"### `{name}` — {stage.kind}")
        add("")
        add(stage.purpose)
        add("")
        if stage.protects:
            add(f"- **Holds**: {stage.protects}")
        if stage.split:
            add(f"- **Split**: {stage.split}")
        add(f"- **Exists because**: {stage.exists_because}; **does**: "
            f"{stage.action}{'; can call the model' if stage.model_call else ''}")
        add(f"- **Called at**: " + ", ".join(
            f"line {site.line}" + (f" when `{_cell(site.condition[:80])}`"
                                   if site.condition else "")
            for site in occurrences[name]))
        if stage.implemented_by:
            add("- **Code**: " + ", ".join(f"`{item}`" for item in stage.implemented_by))
        if stage.runs_after:
            add("- **Must run after**: " + ", ".join(f"`{item}`" for item in stage.runs_after))
        for conflict in stage.conflicts:
            add(f"- **Conflict on record**: {conflict}")
        direct = tests.get(name, [])
        add(f"- **Tests naming its code**: {len(direct)}"
            + (" (" + ", ".join(f"`{module}`" for module in direct[:8])
               + (", …" if len(direct) > 8 else "") + ")" if direct else ""))
        entry = stats.get(name)
        if coverage:
            if entry:
                changing = entry["tests_changing"]
                add(f"- **Whole-turn tests**: ran in {len(entry['tests_running'])}, "
                    f"changed the reply in {len(changing)}"
                    + (" (" + ", ".join(f"`{test.split('.')[1]}.{test.split('.')[-1]}`"
                                         for test in changing[:5])
                       + (", …" if len(changing) > 5 else "") + ")" if changing else ""))
            else:
                add("- **Whole-turn tests**: never reached in any whole-turn test")
        live = rates.get(name)
        if live_turns:
            if live:
                rate = live["changed"] / live["runs"] if live["runs"] else 0
                add(f"- **Live**: ran {live['runs']}×, changed the reply "
                    f"{live['changed']}× ({rate:.0%}); p50 {median(live['dt']):.2f}s"
                    + (f"; {live['calls']} model call(s) since the previous "
                       "recorded stage" if live["calls"] else ""))
            else:
                add("- **Live**: never recorded in these runs")
        add("")

    add("## Conflicts on record")
    add("")
    add("Each of these is a case, written down in the code, of one stage "
        "undoing or damaging another. Correctness that depends on order.")
    add("")
    for name in names_in_order:
        for conflict in by_name[name].conflicts:
            add(f"- `{name}`: {conflict}")
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--traces", nargs="*", default=[],
                        help="runtime directories (turn_trace/) or evaluation "
                             "runs (traces/) whose records to read")
    parser.add_argument("--coverage", default=str(COVERAGE))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    sites, assignments = source_facts()
    unknown = sorted({site.name for site in sites} - set(registry.BY_NAME))
    if unknown:
        print(f"Unclassified stages (add them to brain/response_stages.py): {unknown}")
        return 1
    runtimes = [Path(item) for item in args.traces]
    rates, live_turns, live_broken = live_rates(runtimes)
    document = write(
        sites, assignments, direct_tests(), load_coverage(Path(args.coverage)),
        rates, live_turns, live_broken, runtimes,
    )
    Path(args.out).write_text(document, encoding="utf-8")
    print(f"Wrote {args.out}: {len(sites)} stage sites, "
          f"{len({site.name for site in sites})} stages, {live_turns} live turns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
