"""Aggregate evaluation runs: what passed, how often, how fast.

    .venv/Scripts/python.exe -m evals.report runtime/evals/baseline-1 runtime/evals/baseline-2 ...
    .venv/Scripts/python.exe -m evals.report runtime/evals/baseline-* --out docs/EVAL_BASELINE.md

Every run is scored against the *current* corpus: the deterministic checks
are recomputed from the stored replies and turn records, and only judge
verdicts given under the current rubric version count. Changing a scenario
or the rubric therefore re-scores old runs the same way as new ones, rather
than leaving a report that mixes two definitions.

A scored turn passes when every check passes and every applicable judged
property is yes (n/a is neither). A scenario passes when all its scored
turns do. Every number is given as the mean and range across runs, because
one live run of this model moves by several turns on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import checks, corpus, modality_pairs  # noqa: E402


# ------------------------------------------------------------------ loading


def load_run(path: Path, judge: str | None = None) -> dict:
    """A run's results, times and verdicts -- only ``judge``'s verdicts, under
    the current rubric wording. Two judges are never averaged together."""
    if judge is None:
        from evals.judge import DEFAULT_JUDGE
        judge = DEFAULT_JUDGE
    results = [
        json.loads(line)
        for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fingerprint = corpus.rubric_fingerprint()
    judged = {}
    judged_path = path / "judged.jsonl"
    if judged_path.exists():
        for line in judged_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                # Only verdicts given under the rubric's current wording.
                if (item.get("rubric_fingerprint") == fingerprint
                        and item.get("judge_model") == judge):
                    judged[(item["scenario"], item["index"])] = item
    # When each turn began: newer runs keep it in the result, older ones
    # only in the full records beside it.
    times = {}
    for trace_file in (path / "traces").glob("*.jsonl"):
        for line in trace_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            times[record.get("turn_id")] = record.get("started_at")
    meta_path = path / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return {"path": path, "results": results, "judged": judged, "times": times,
            "meta": meta, "judge": judge}


def scored_turns(run: dict):
    """(result, stored turn, corpus turn, checks, verdicts) for every turn the
    current corpus scores, with its checks recomputed."""
    scenarios = {s.id: s for s in corpus.load(corpus.ALL_SUITES)}
    for result in run["results"]:
        scenario = scenarios.get(result["scenario"])
        if scenario is None:
            continue
        previous = ""
        for stored in result["turns"]:
            index = stored["index"]
            wanted = scenario.turns[index] if index < len(scenario.turns) else None
            if wanted is not None and wanted.score:
                if stored["display"] is None:
                    found = [checks.CheckResult("reply", False, "no reply")]
                else:
                    started = run["times"].get((stored.get("trace") or {}).get("turn_id"))
                    found = checks.run(
                        wanted.checks, checks.stored_turn(stored, previous, started),
                    )
                verdicts = {
                    name: verdict
                    for name, verdict in run["judged"].get((result["scenario"], index), {})
                    .get("verdicts", {}).items()
                    if name in wanted.rubric
                }
                yield result, stored, wanted, found, verdicts
            previous = stored["display"] or ""


# ----------------------------------------------------------------- measuring


def measure(run: dict) -> dict:
    by_suite = defaultdict(lambda: {"scenarios": 0, "passed": 0, "checks_only": 0,
                                    "turns": 0, "turns_passed": 0})
    check_counts = defaultdict(lambda: [0, 0])
    properties = defaultdict(lambda: [0, 0])
    not_applicable = defaultdict(int)
    scenario_pass: dict[str, bool] = {}
    scenario_checks: dict[str, bool] = {}
    failures: dict[str, list[str]] = defaultdict(list)
    judged_turns = judge_errors = unjudged = 0
    correctness = [0, 0]

    for result, stored, wanted, found, verdicts in scored_turns(run):
        checks_ok = all(check.passed for check in found)
        complete = set(verdicts) == set(wanted.rubric)
        unjudged += not complete
        rubric_ok = complete and all(v.get("verdict") in ("yes", "n/a") for v in verdicts.values())
        passed = checks_ok and rubric_ok
        suite = by_suite[result["suite"]]
        suite["turns"] += 1
        suite["turns_passed"] += passed
        sid = result["scenario"]
        scenario_pass[sid] = scenario_pass.get(sid, True) and passed
        scenario_checks[sid] = scenario_checks.get(sid, True) and checks_ok
        for check in found:
            check_counts[check.check][0] += check.passed
            check_counts[check.check][1] += 1
            if check.check in checks.CORRECTNESS:
                correctness[0] += check.passed
                correctness[1] += 1
            if not check.passed:
                failures[sid].append(check.check)
        judged_turns += bool(verdicts)
        for name, verdict in verdicts.items():
            answer = verdict.get("verdict")
            if answer == "n/a":
                not_applicable[name] += 1
                continue
            judge_errors += answer == "error"
            properties[name][0] += answer == "yes"
            properties[name][1] += 1
            if answer != "yes":
                failures[sid].append(name)

    suites_of = {result["scenario"]: result["suite"] for result in run["results"]}
    for sid, passed in scenario_pass.items():
        suite = by_suite[suites_of[sid]]
        suite["scenarios"] += 1
        suite["passed"] += passed
        suite["checks_only"] += scenario_checks[sid]

    # Time and work, over every turn -- scored or not -- that got a reply.
    seconds, routes, ttfts, calls, changed, searched = [], [], [], [], [], 0
    for result in run["results"]:
        for turn in result["turns"]:
            if turn["display"] is None:
                continue
            trace = turn.get("trace") or {}
            seconds.append(turn["seconds"])
            timings = trace.get("timings") or {}
            if "route" in timings:
                routes.append(timings["route"])
            for call in trace.get("model_calls") or ():
                if str(call.get("caller", "")).endswith(":collect_answer") and "ttft" in call:
                    ttfts.append(call["ttft"])
            calls.append(len(trace.get("model_calls") or ()))
            changed.append(len(trace.get("changed") or ()))
            searched += bool(trace.get("searched"))
    return {
        "suites": dict(by_suite),
        "scenarios": len(scenario_pass),
        "scenarios_passed": sum(scenario_pass.values()),
        "scenarios_passing_checks": sum(scenario_checks.values()),
        "scenario_pass": scenario_pass,
        "checks": {name: tuple(value) for name, value in check_counts.items()},
        "correctness": tuple(correctness),
        "properties": {name: tuple(value) for name, value in properties.items()},
        "not_applicable": dict(not_applicable),
        "judged_turns": judged_turns,
        "unjudged_turns": unjudged,
        "judge_errors": judge_errors,
        "failures": failures,
        "turns_answered": len(seconds),
        "seconds_p50": median(seconds) if seconds else 0.0,
        "seconds_p95": sorted(seconds)[int(0.95 * (len(seconds) - 1))] if seconds else 0.0,
        "route_p50": median(routes) if routes else 0.0,
        "ttft_p50": median(ttfts) if ttfts else 0.0,
        "model_calls_mean": mean(calls) if calls else 0.0,
        "stages_changed_mean": mean(changed) if changed else 0.0,
        "searched_turns": searched,
    }


# ------------------------------------------------------------------ writing


def _spread(values) -> str:
    values = list(values)
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0]:.2f}" if isinstance(values[0], float) else str(values[0])
    low, high, average = min(values), max(values), mean(values)
    if all(isinstance(v, int) for v in values):
        return f"{average:.1f} ({low}–{high})"
    return f"{average:.2f} ({low:.2f}–{high:.2f})"


def _rate(pairs) -> str:
    """Mean pass rate across runs, with the range, from (passed, total) pairs."""
    rates = [p / t for p, t in pairs if t]
    if not rates:
        return "-"
    total = max(t for _, t in pairs)
    if len(rates) == 1:
        return f"{pairs[0][0]}/{pairs[0][1]} ({rates[0]:.0%})"
    return f"{mean(rates):.0%} ({min(rates):.0%}–{max(rates):.0%}) of {total}"


def write(runs: list[dict]) -> str:
    measured = [measure(run) for run in runs]
    scenarios = {s.id: s for s in corpus.load(corpus.ALL_SUITES)}
    lines: list[str] = []
    add = lines.append
    meta = runs[0]["meta"]
    judges = sorted({item.get("judge_model", "?") for run in runs for item in run["judged"].values()})
    add("# Evaluation baseline")
    add("")
    add(f"*Generated by `evals/report.py` on {datetime.now().strftime('%Y-%m-%d %H:%M')} "
        f"from {len(runs)} run(s): " + ", ".join(f"`{run['path'].name}`" for run in runs)
        + f". Replies from commit `{meta.get('commit', '?')}`"
        + (" with uncommitted changes" if meta.get("uncommitted_changes") else "")
        + f", model `{meta.get('model', '?')}`"
        + (f", Ollama {meta['ollama']}" if meta.get("ollama") else ", Ollama version not recorded")
        + f". Scored against rubric version {corpus.rubric_version()} "
        f"(`{corpus.rubric_fingerprint()}`); judge: "
        + (", ".join(judges) if judges else "not judged") + ".*")
    add("")
    add("Each scenario ran on a fresh backend with a fresh runtime and speech "
        "silenced. Checks are recomputed against the current corpus. A scored "
        "turn passes when every check passes and every applicable judged "
        "property is yes (n/a counts as neither); a scenario passes when all "
        "its scored turns do. Figures are the mean across runs with the range "
        "in brackets.")
    add("")
    judged = all(m["judged_turns"] and not m["unjudged_turns"] for m in measured)
    add("## Headline")
    add("")
    if judged:
        add(f"- **Scenarios passing**: "
            f"{_rate([(m['scenarios_passed'], m['scenarios']) for m in measured])}")
    else:
        missing = sum(m["unjudged_turns"] for m in measured)
        add(f"- **Not fully judged**: {missing} scored turn(s) have no verdicts under "
            f"rubric version {corpus.rubric_version()}; run `evals.judge`. "
            "Pass rates below that need the judge are left out.")
    add(f"- **Scenarios passing their deterministic checks**: "
        f"{_rate([(m['scenarios_passing_checks'], m['scenarios']) for m in measured])}")
    add(f"- **Stated values that are correct** (clock, offset, date, arithmetic, units): "
        f"{_rate([m['correctness'] for m in measured])} checks")
    for suite in corpus.SUITES:
        pairs = [(m["suites"].get(suite, {}).get("passed", 0),
                  m["suites"].get(suite, {}).get("scenarios", 0)) for m in measured]
        checks_only = [(m["suites"].get(suite, {}).get("checks_only", 0),
                        m["suites"].get(suite, {}).get("scenarios", 0)) for m in measured]
        if pairs[0][1]:
            add(f"  - {suite}: " + (f"passing {_rate(pairs)}; " if judged else "")
                + f"passing their checks {_rate(checks_only)}")
    errors = sum(m["judge_errors"] for m in measured)
    if errors:
        add(f"- **Judge errors**: {errors} verdict(s) the judge could not give; "
            "counted as not passing.")
    rows = modality_pairs.score()
    totals = modality_pairs.summary(rows)
    add(f"- **Display and speech, without a model** ({totals['pairs']} pairs, "
        "evals/modality_pairs.json): notation preserved on screen "
        f"{totals['preserved']}/{totals['pairs']} inside a sentence and "
        f"{totals['preserved_at_start']}/{totals['pairs']} at the start of a reply; "
        f"speech speakable {totals['speakable']}/{totals['pairs']}; speech "
        f"equivalent to the display {totals['equivalent']}/{totals['pairs']}.")
    add("")

    add("## Deterministic checks")
    add("")
    add("| check | kind | pass rate |")
    add("|---|---|---|")
    names = sorted({name for m in measured for name in m["checks"]},
                   key=lambda n: (n not in checks.CORRECTNESS, n))
    for name in names:
        kind = "correctness" if name in checks.CORRECTNESS else "mechanical"
        if name in ("must_contain_any", "must_not_contain"):
            kind = "earlier subject"
        add(f"| `{name}` | {kind} | {_rate([m['checks'].get(name, (0, 0)) for m in measured])} |")
    add("")

    add("## Judged properties")
    add("")
    if any(m["judged_turns"] for m in measured):
        add("Rates are over the turns each property applies to; n/a verdicts "
            "are counted separately and excluded.")
        add("")
        add("| property | yes rate | n/a |")
        add("|---|---|---|")
        for name in corpus.rubric():
            pairs = [m["properties"].get(name, (0, 0)) for m in measured]
            if not any(total for _, total in pairs):
                continue
            na = sum(m["not_applicable"].get(name, 0) for m in measured)
            add(f"| `{name}` | {_rate(pairs)} | {na or ''} |")
    else:
        add("Not judged under this rubric version yet: run `evals.judge` on these runs.")
    add("")

    add("## Time and work per turn")
    add("")
    add("| measure | value |")
    add("|---|---|")
    add(f"| turns answered | {_spread([m['turns_answered'] for m in measured])} |")
    add(f"| seconds to the reply, p50 | {_spread([m['seconds_p50'] for m in measured])} |")
    add(f"| seconds to the reply, p95 | {_spread([m['seconds_p95'] for m in measured])} |")
    add(f"| routing, p50 s | {_spread([m['route_p50'] for m in measured])} |")
    add(f"| answer's first token, p50 s | {_spread([m['ttft_p50'] for m in measured])} |")
    add(f"| model calls per turn, mean | {_spread([m['model_calls_mean'] for m in measured])} |")
    add(f"| stages that changed the reply, mean | {_spread([m['stages_changed_mean'] for m in measured])} |")
    add(f"| turns that searched the web | {_spread([m['searched_turns'] for m in measured])} |")
    add("")

    add("## By scenario")
    add("")
    add("Passes out of runs, and what failed most often.")
    add("")
    add("| suite | scenario | passed | most frequent failures |")
    add("|---|---|---|---|")
    ids = sorted(
        {sid for m in measured for sid in m["scenario_pass"]},
        key=lambda sid: (corpus.ALL_SUITES.index(scenarios[sid].suite) if sid in scenarios else 9, sid),
    )
    for sid in ids:
        passed = sum(m["scenario_pass"].get(sid, False) for m in measured)
        seen = sum(sid in m["scenario_pass"] for m in measured)
        counts = defaultdict(int)
        for m in measured:
            for failure in m["failures"].get(sid, ()):
                counts[failure] += 1
        worst = ", ".join(f"{name} ×{count}" for name, count in
                          sorted(counts.items(), key=lambda item: -item[1])[:4])
        suite = scenarios[sid].suite if sid in scenarios else "?"
        add(f"| {suite} | `{sid}` | {passed}/{seen} | {worst} |")
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--out", default="")
    parser.add_argument("--judge", default=None, help="whose verdicts to read")
    args = parser.parse_args()
    runs = [load_run(Path(path), args.judge) for path in args.runs]
    document = write(runs)
    if args.out:
        Path(args.out).write_text(document, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
