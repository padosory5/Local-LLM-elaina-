"""Phase 2 side by side: every arm's quality against its time and memory.

    .venv/Scripts/python.exe -m evals.compare                     # print
    .venv/Scripts/python.exe -m evals.compare --out docs/PHASE2_RESULTS.md

Reads runtime/evals/p2/<arm>-<n>/ for the arms in evals/phase2.py, scores
each run exactly as evals/report.py does (checks recomputed against the
current corpus, only the named judge's verdicts under the current rubric
wording) and puts the arms in one table per question.

**Reading a difference.** One live run of this model moves by several
turns on its own. Every quality arm ran twice, so each arm has its own
run-to-run spread; a difference between two arms is marked as clear (●)
only when it is larger than the spread of both. Everything else is within
the noise and says nothing either way.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import corpus, integrity, phase2, report  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402

CONTROL = "p-8b-soft"


# ---------------------------------------------------------------- measuring


def _answer_call(trace: dict) -> dict | None:
    """The call that wrote the reply's words."""
    for call in trace.get("model_calls") or ():
        caller = str(call.get("caller", ""))
        if caller.endswith(":collect_answer") or caller == "evals.direct":
            return call
    return None


def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[int(share * (len(ordered) - 1))]


def latency(run: dict) -> dict:
    """Time, speed and memory over every turn of a run that got a reply."""
    seconds, ttfts, answer_speed, all_speed, calls, routes = [], [], [], [], [], []
    vram, processors, timeouts = [], set(), 0
    for result in run["results"]:
        resources = result.get("resources") or {}
        if resources.get("vram_used_mib"):
            vram.append(resources["vram_used_mib"])
        for model in resources.get("ollama_ps") or ():
            processors.add(f"{model['name'].split('/')[-1]} {model['processor']}")
        for turn in result["turns"]:
            if turn["display"] is None:
                timeouts += 1
                continue
            trace = turn.get("trace") or {}
            seconds.append(turn["seconds"])
            answer = _answer_call(trace)
            if answer and answer.get("ttft") is not None:
                ttfts.append(answer["ttft"])
            if answer and answer.get("tokens_per_second"):
                answer_speed.append(answer["tokens_per_second"])
            for call in trace.get("model_calls") or ():
                if call.get("tokens_per_second"):
                    all_speed.append(call["tokens_per_second"])
            calls.append(len(trace.get("model_calls") or ()))
            timings = trace.get("timings") or {}
            if "route" in timings:
                routes.append(timings["route"])
    return {
        "turns": len(seconds),
        "timeouts": timeouts,
        "p50": median(seconds) if seconds else 0.0,
        "p95": _percentile(seconds, 0.95),
        "ttft_p50": median(ttfts) if ttfts else 0.0,
        "route_p50": median(routes) if routes else 0.0,
        "answer_tps": median(answer_speed) if answer_speed else 0.0,
        "tps": median(all_speed) if all_speed else 0.0,
        "calls": mean(calls) if calls else 0.0,
        "vram_max": max(vram) if vram else 0,
        "processors": sorted(processors),
    }


def turn_passes(run: dict) -> tuple[int, int]:
    """Scored turns passing (every check and every applicable property)."""
    passed = total = 0
    for _result, _stored, wanted, found, verdicts in report.scored_turns(run):
        complete = set(verdicts) == set(wanted.rubric)
        ok = (all(check.passed for check in found) and complete
              and all(v.get("verdict") in ("yes", "n/a") for v in verdicts.values()))
        passed += ok
        total += 1
    return passed, total


def load_arms(root: Path, judge: str) -> dict[str, list[dict]]:
    """Every run on disk, by arm, in phase2's order."""
    arms: dict[str, list[dict]] = {}
    for arm in phase2.ARMS:
        runs = []
        for number in range(1, arm.runs + 1):
            path = root / f"{arm.name}-{number}"
            if (path / "results.jsonl").exists():
                run = report.load_run(path, judge)
                run["measured"] = report.measure(run)
                run["latency"] = latency(run)
                run["turn_passes"] = turn_passes(run)
                run["integrity"] = integrity.check_run(path).counts()
                runs.append(run)
        if runs:
            arms[arm.name] = runs
    return arms


# ------------------------------------------------------------------ helpers


def _rates(pairs) -> list[float]:
    return [p / t for p, t in pairs if t]


def _cell(pairs) -> str:
    rates = _rates(pairs)
    if not rates:
        return "-"
    if len(rates) == 1:
        return f"{rates[0]:.0%}"
    return f"{mean(rates):.0%} ({min(rates):.0%}–{max(rates):.0%})"


def _clear(pairs, control_pairs) -> str:
    """● when the gap to the control exceeds both arms' run-to-run spread."""
    rates, base = _rates(pairs), _rates(control_pairs)
    if len(rates) < 2 or len(base) < 2:
        return ""
    gap = mean(rates) - mean(base)
    spread = max(max(rates) - min(rates), max(base) - min(base))
    if abs(gap) > spread and abs(gap) >= 0.05:
        return " ●▲" if gap > 0 else " ●▼"
    return ""


def _seconds(values) -> str:
    values = [v for v in values if v]
    if not values:
        return "-"
    if len(values) == 1 or max(values) - min(values) < 0.05:
        return f"{mean(values):.1f}"
    return f"{mean(values):.1f} ({min(values):.1f}–{max(values):.1f})"


def _suite_pairs(runs, suite: str, key: str) -> list[tuple[int, int]]:
    return [(r["measured"]["suites"].get(suite, {}).get(key, 0),
             r["measured"]["suites"].get(suite, {}).get("scenarios", 0)) for r in runs]


# ------------------------------------------------------------------ writing


def write(arms: dict[str, list[dict]], judge: str) -> str:
    by_name = {arm.name: arm for arm in phase2.ARMS}
    judged = [name for name in arms if by_name[name].judged]
    timing_only = [name for name in arms if not by_name[name].judged]
    control = arms.get(CONTROL, [])
    lines: list[str] = []
    add = lines.append
    first = next(iter(arms.values()))[0]["meta"]
    add("# Phase 2: the model experiment")
    add("")
    add(f"*Generated by `evals/compare.py` on {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
        f"Replies from commit `{first.get('commit', '?')}`"
        + (" with uncommitted changes" if first.get("uncommitted_changes") else "")
        + (f", Ollama {first['ollama']}" if first.get("ollama") else "")
        + f". Rubric version {corpus.rubric_version()} (`{corpus.rubric_fingerprint()}`), "
        f"judge `{judge}`.*")
    add("")
    add("Figures are the mean over an arm's runs, with the range in brackets. "
        f"● marks a difference from the control (`{CONTROL}`, today's Elaina) that is "
        "larger than both arms' own run-to-run spread and at least five points; "
        "▲ better, ▼ worse. Anything unmarked is inside the noise.")
    add("")

    add("## The arms")
    add("")
    add("| arm | what it is | runs | scope |")
    add("|---|---|---|---|")
    for name, runs in arms.items():
        arm = by_name[name]
        scope = ("six latency scenarios" if arm.only else
                 ", ".join(arm.suites) if arm.suites else "whole corpus")
        add(f"| `{name}` | {arm.about} | {len(runs)} | {scope}"
            + ("" if arm.judged else "; timed, not judged") + " |")
    add("")

    if judged:
        add("## Quality against time")
        add("")
        add("| arm | scenarios passing | scored turns passing | correct values | "
            "p50 s | p95 s | first token s | tok/s | calls/turn | VRAM MiB |")
        add("|---|---|---|---|---|---|---|---|---|---|")
        for name in judged:
            runs = arms[name]
            m = [r["measured"] for r in runs]
            scen = [(x["scenarios_passed"], x["scenarios"]) for x in m]
            turns = [r["turn_passes"] for r in runs]
            correct = [x["correctness"] for x in m]
            lat = [r["latency"] for r in runs]
            control_m = [r["measured"] for r in control]
            add(f"| `{name}` "
                f"| {_cell(scen)}{_clear(scen, [(x['scenarios_passed'], x['scenarios']) for x in control_m])} "
                f"| {_cell(turns)}{_clear(turns, [r['turn_passes'] for r in control])} "
                f"| {_cell(correct)}{_clear(correct, [x['correctness'] for x in control_m])} "
                f"| {_seconds([x['p50'] for x in lat])} | {_seconds([x['p95'] for x in lat])} "
                f"| {_seconds([x['ttft_p50'] for x in lat])} | {_seconds([x['answer_tps'] for x in lat])} "
                f"| {mean(x['calls'] for x in lat):.1f} | {max(x['vram_max'] for x in lat) or '-'} |")
        add("")
        add("*p50/p95 s*: from sending the words to the reply on screen, every turn. "
            "*First token*: of the call that writes the reply. *tok/s*: that call's "
            "generation speed, median. *VRAM*: the most the card held at the end of "
            "any scenario. The direct arms have no clock and no search, so their "
            "correct-values column measures what the pipeline supplies.")
        add("")

        add("## By suite")
        add("")
        add("Scenarios passing (all checks and all applicable properties).")
        add("")
        suites = [s for s in corpus.SUITES
                  if any(_suite_pairs(arms[n], s, "passed")[0][1] for n in judged)]
        add("| arm | " + " | ".join(suites) + " |")
        add("|---|" + "---|" * len(suites))
        for name in judged:
            cells = []
            for suite in suites:
                pairs = _suite_pairs(arms[name], suite, "passed")
                cells.append(_cell(pairs) + _clear(pairs, _suite_pairs(control, suite, "passed")))
            add(f"| `{name}` | " + " | ".join(cells) + " |")
        add("")

        add("## Judged properties")
        add("")
        add("Yes rate over the turns each property applies to.")
        add("")
        add("| property | " + " | ".join(f"`{n}`" for n in judged) + " |")
        add("|---|" + "---|" * len(judged))
        for prop in corpus.rubric():
            cells = []
            control_pairs = [r["measured"]["properties"].get(prop, (0, 0)) for r in control]
            for name in judged:
                pairs = [r["measured"]["properties"].get(prop, (0, 0)) for r in arms[name]]
                cells.append(_cell(pairs) + ("" if name == CONTROL else _clear(pairs, control_pairs)))
            if any(cell != "-" for cell in cells):
                add(f"| `{prop}` | " + " | ".join(cells) + " |")
        add("")

        add("## Correctness checks")
        add("")
        from evals import checks as check_module
        names = sorted({c for n in judged for r in arms[n] for c in r["measured"]["checks"]
                        if c in check_module.CORRECTNESS})
        add("| check | " + " | ".join(f"`{n}`" for n in judged) + " |")
        add("|---|" + "---|" * len(judged))
        for check in names:
            cells = [_cell([r["measured"]["checks"].get(check, (0, 0)) for r in arms[n]])
                     for n in judged]
            add(f"| `{check}` | " + " | ".join(cells) + " |")
        add("")

        add("## Scenarios that moved")
        add("")
        add("Against the control, scenario by scenario: *won* means it passed in "
            "every run of the arm and in no run of the control; *lost* the reverse.")
        add("")
        add("| arm | won | lost |")
        add("|---|---|---|")
        base = defaultdict(list)
        for run in control:
            for sid, ok in run["measured"]["scenario_pass"].items():
                base[sid].append(ok)
        for name in judged:
            if name == CONTROL:
                continue
            mine = defaultdict(list)
            for run in arms[name]:
                for sid, ok in run["measured"]["scenario_pass"].items():
                    mine[sid].append(ok)
            won = sorted(s for s in mine if all(mine[s]) and base.get(s) and not any(base[s]))
            lost = sorted(s for s in mine if not any(mine[s]) and base.get(s) and all(base[s]))
            add(f"| `{name}` | {', '.join(f'`{s}`' for s in won) or '-'} "
                f"| {', '.join(f'`{s}`' for s in lost) or '-'} |")
        add("")

    add("## Time and memory")
    add("")
    add("Every arm, including the ones only timed. The *v-* arms run with the "
        "speech recogniser resident, as the app does; the others have it off.")
    add("")
    add("| arm | turns | no reply | p50 s | p95 s | routing p50 s | first token s "
        "| answer tok/s | all calls tok/s | VRAM MiB | where the models ran |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, runs in arms.items():
        lat = [r["latency"] for r in runs]
        where = sorted({p for x in lat for p in x["processors"]})
        add(f"| `{name}` | {sum(x['turns'] for x in lat)} | {sum(x['timeouts'] for x in lat)} "
            f"| {_seconds([x['p50'] for x in lat])} | {_seconds([x['p95'] for x in lat])} "
            f"| {_seconds([x['route_p50'] for x in lat])} | {_seconds([x['ttft_p50'] for x in lat])} "
            f"| {_seconds([x['answer_tps'] for x in lat])} | {_seconds([x['tps'] for x in lat])} "
            f"| {max(x['vram_max'] for x in lat) or '-'} | {'; '.join(where) or '-'} |")
    add("")
    if timing_only:
        add("Arms timed but not judged: " + ", ".join(f"`{n}`" for n in timing_only) + ".")
        add("")

    add("## Pipeline integrity")
    add("")
    add("What the machinery did that it should never do, counted over every "
        "recorded turn (evals/integrity.py), summed over an arm's runs.")
    add("")
    add("| arm | turns | clock turns searched | values deleted that the evidence held "
        "| display changed by speech shaping | notation broken by a deterministic stage |")
    add("|---|---|---|---|---|---|")
    for name, runs in arms.items():
        counts = [r["integrity"] for r in runs]
        total = lambda key: sum(c[key] for c in counts)  # noqa: E731
        add(f"| `{name}` | {total('turns')} "
            f"| {total('clock_turns_searched')} of {total('clock_turns')} "
            f"| {total('values_deleted_with_evidence')} "
            f"| {total('display_speech_shaped')} | {total('notation_changed')} |")
    add("")

    add("## What the judge can and cannot be trusted with")
    add("")
    if judge == DEFAULT_JUDGE:
        add(f"`{judge}` agreed with the v3 human labels on 87% of verdicts (kappa 0.73; "
            "docs/EVAL_RUBRIC.md). Three limits apply to these tables:")
        add("")
        add("- `adapts_to_confusion` is below the agreement bar. Its column is a "
            "direction, not a measurement.")
        add("- It accepted a false Korean claim as accurate. Korean `accurate` "
            "verdicts are weaker than English ones.")
    else:
        add(f"`{judge}` is not the calibrated Phase 2 judge; see docs/EVAL_RUBRIC.md "
            "for its agreement with the human labels.")
        add("")
    add("- A rise in a property that is rarely yes (`concrete_first`, "
        "`says_what_it_solves`, `adapts_to_confusion`) needs a small blind human "
        "check before it is believed: the calibration set had few yes examples "
        "of them.")
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(phase2.ROOT))
    parser.add_argument("--judge", default=DEFAULT_JUDGE)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    arms = load_arms(Path(args.root), args.judge)
    if not arms:
        print(f"No runs under {args.root}.")
        return 1
    document = write(arms, args.judge)
    if args.out:
        Path(args.out).write_text(document, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
