"""Phase 3E report: the cleaned pipeline against Phase 2, and the model decision.

    .venv/Scripts/python.exe -m evals.compare3
    .venv/Scripts/python.exe -m evals.compare3 --out docs/PHASE3_RESULTS.md

Reads the Phase 3 arms (runtime/evals/p3/, evals/phase3.py) and the Phase 2
arms they are compared with (runtime/evals/p2/, evals/phase2.py), all scored
the same way: checks recomputed against the current corpus, only the named
judge's verdicts under the current rubric wording. The "before" for every
arm is its own Phase 2 counterpart, so each table answers what the cleaning
changed with the model held fixed; the last tables hold the pipeline fixed
and compare the models.

● marks a difference larger than both runs' own run-to-run spread and at
least five points, as in evals/compare.py. Single-run arms are never marked.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import realize, response_stages  # noqa: E402
from evals import checks as check_module  # noqa: E402
from evals import compare, corpus, integrity, phase2, phase3, report  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402


# ---------------------------------------------------------------- loading


def _load(root: Path, arms, judge: str) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for arm in arms:
        for number in range(1, arm.runs + 1):
            path = root / f"{arm.name}-{number}"
            if (path / "results.jsonl").exists():
                run = report.load_run(path, judge)
                run["measured"] = report.measure(run)
                run["latency"] = compare.latency(run)
                run["turn_passes"] = compare.turn_passes(run)
                run["integrity"] = integrity.check_run(path).counts()
                run["speech"] = speech_measures(path)
                run["words"] = reply_words(run)
                run["stages"] = soft_stage_changes(path)
                run["router"] = router_measures(path)
                found.setdefault(arm.name, []).append(run)
    return found


def _records(path: Path):
    for trace_file in sorted((path / "traces").glob("*.jsonl")):
        for line in trace_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def speech_measures(path: Path) -> dict:
    """What the voice was handed against what the screen showed."""
    coverages, characters, shown_characters = [], [], []
    for record in _records(path):
        display = record.get("display") or ""
        spoken = " ".join(entry.get("spoken", "") for entry in record.get("speech") or ())
        if not display or not spoken:
            continue
        coverages.append(realize.coverage(display, spoken))
        characters.append(len(spoken))
        shown_characters.append(len(display))
    return {
        "turns": len(coverages),
        "coverage_mean": mean(coverages) if coverages else 0.0,
        "coverage_below_90": sum(c < 0.9 for c in coverages),
        "speech_chars_p50": median(characters) if characters else 0,
        "display_chars_p50": median(shown_characters) if shown_characters else 0,
    }


def reply_words(run: dict) -> dict:
    """Words per reply, by suite, over scored turns."""
    by_suite = defaultdict(list)
    for result, stored, wanted, found, verdicts in report.scored_turns(run):
        by_suite[result["suite"]].append(len((stored["display"] or "").split()))
    return {suite: median(values) for suite, values in by_suite.items() if values}


def router_measures(path: Path) -> dict:
    """The routing call on its own: first answers, overflows, fallbacks.

    A routed turn's first ``:route`` call is its answer; any further ones
    are the repair fallback, which runs when the first answer could not be
    parsed -- most often because it was cut off at the context window.
    """
    routed = truncated = fallback = 0
    first, repair, prompts = [], [], []
    for record in _records(path):
        calls = [c for c in record.get("model_calls") or ()
                 if str(c.get("caller", "")).endswith(":route")]
        if not calls:
            continue
        routed += 1
        first.append(float(calls[0].get("latency") or 0.0))
        prompts.append(int(calls[0].get("prompt_eval_count") or 0))
        truncated += calls[0].get("done_reason") == "length"
        if len(calls) > 1:
            fallback += 1
            repair.extend(float(c.get("latency") or 0.0) for c in calls[1:])
    q95 = lambda values: sorted(values)[int(0.95 * (len(values) - 1))] if values else 0.0  # noqa: E731
    return {
        "routed": routed,
        "truncated": truncated,
        "fallback": fallback,
        "first_p50": median(first) if first else 0.0,
        "first_p95": q95(first),
        "fallback_p50": median(repair) if repair else 0.0,
        "prompt_p50": median(prompts) if prompts else 0,
        "prompt_max": max(prompts) if prompts else 0,
    }


def soft_stage_changes(path: Path) -> Counter:
    """How often each SOFT stage changed a reply."""
    counts: Counter = Counter()
    for record in _records(path):
        for step in record.get("steps") or ():
            if step.get("changed") and response_stages.classify(step.get("name", "")) == response_stages.SOFT:
                counts[step["name"]] += 1
    return counts


# ---------------------------------------------------------------- writing


def _pairs(runs, key):
    return [run[key] for run in runs]


def _m(runs, key):
    return [run["measured"][key] for run in runs]


def _scen(runs):
    return [(m["scenarios_passed"], m["scenarios"]) for m in (r["measured"] for r in runs)]


def _prop(runs, name):
    return [r["measured"]["properties"].get(name, (0, 0)) for r in runs]


def _lat(runs, key):
    return [r["latency"][key] for r in runs]


def write(before: dict, after: dict, judge: str) -> str:
    lines: list[str] = []
    add = lines.append
    first = next(iter(after.values()))[0]["meta"] if after else {}
    add("# Phase 3: the cleaned pipeline, and the model decision")
    add("")
    add(f"*Generated by `evals/compare3.py` on {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
        f"Phase 3 replies from commit `{first.get('commit', '?')}`"
        + (" with uncommitted changes" if first.get("uncommitted_changes") else "")
        + (f", Ollama {first['ollama']}" if first.get("ollama") else "")
        + f". Rubric version {corpus.rubric_version()} (`{corpus.rubric_fingerprint()}`), "
        f"judge `{judge}`. Phase 2 runs are runtime/evals/p2/, Phase 3 runs "
        "runtime/evals/p3/.*")
    add("")
    add("Each Phase 3 arm is read against its own Phase 2 counterpart: same "
        "model, same corpus, same judge; the pipeline is what changed. "
        "● marks a difference larger than both arms' run-to-run spread and at "
        "least five points (▲ better, ▼ worse); single-run arms are not marked.")
    add("")

    pairs = [(name, phase3.BEFORE.get(name)) for name in after]

    add("## Before and after, model held fixed")
    add("")
    add("| arm (Phase 3) | vs | scenarios passing | scored turns passing | correct values | p50 s | p95 s | calls/turn |")
    add("|---|---|---|---|---|---|---|---|")
    for name, prior in pairs:
        runs = after[name]
        if not any(arm.name == name and arm.judged for arm in phase3.ARMS):
            continue
        if name.startswith("f3-"):
            continue
        old = before.get(prior, [])
        scen_old, scen_new = _scen(old), _scen(runs)
        turns_old, turns_new = _pairs(old, "turn_passes"), _pairs(runs, "turn_passes")
        cor_old, cor_new = _m(old, "correctness"), _m(runs, "correctness")
        add(f"| `{name}` | `{prior}` "
            f"| {compare._cell(scen_old)} → {compare._cell(scen_new)}{compare._clear(scen_new, scen_old)} "
            f"| {compare._cell(turns_old)} → {compare._cell(turns_new)}{compare._clear(turns_new, turns_old)} "
            f"| {compare._cell(cor_old)} → {compare._cell(cor_new)}{compare._clear(cor_new, cor_old)} "
            f"| {compare._seconds(_lat(old, 'p50'))} → {compare._seconds(_lat(runs, 'p50'))} "
            f"| {compare._seconds(_lat(old, 'p95'))} → {compare._seconds(_lat(runs, 'p95'))} "
            f"| {mean(_lat(old, 'calls')) if old else 0:.1f} → {mean(_lat(runs, 'calls')):.1f} |")
    add("")

    add("## The model decision, pipeline held fixed")
    add("")
    decision = [n for n in ("p3-8b-soft", "p3-27b-soft", "p3-8b-nosoft", "p3-27b-nosoft", "p3-8bq8-soft") if n in after]
    add("| arm | scenarios passing | scored turns passing | correct values | p50 s | p95 s | routing p50 s | first token s | tok/s | VRAM MiB |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    base = after.get("p3-8b-soft", [])
    for name in decision:
        runs = after[name]
        lat = [r["latency"] for r in runs]
        mark = lambda new, old: "" if name == "p3-8b-soft" else compare._clear(new, old)  # noqa: E731
        add(f"| `{name}` | {compare._cell(_scen(runs))}{mark(_scen(runs), _scen(base))} "
            f"| {compare._cell(_pairs(runs, 'turn_passes'))}{mark(_pairs(runs, 'turn_passes'), _pairs(base, 'turn_passes'))} "
            f"| {compare._cell(_m(runs, 'correctness'))}{mark(_m(runs, 'correctness'), _m(base, 'correctness'))} "
            f"| {compare._seconds([x['p50'] for x in lat])} | {compare._seconds([x['p95'] for x in lat])} "
            f"| {compare._seconds([x['route_p50'] for x in lat])} | {compare._seconds([x['ttft_p50'] for x in lat])} "
            f"| {compare._seconds([x['answer_tps'] for x in lat])} | {max(x['vram_max'] for x in lat) or '-'} |")
    add("")
    add("● here is against `p3-8b-soft`, the production candidate.")
    add("")

    add("## By suite")
    add("")
    suites = list(corpus.SUITES)
    add("| arm | " + " | ".join(suites) + " |")
    add("|---|" + "---|" * len(suites))
    for name in [n for n in ("p-8b-soft", "p-27b-soft") if n in before] + decision:
        runs = before.get(name) or after.get(name)
        cells = [compare._cell(compare._suite_pairs(runs, s, "passed")) for s in suites]
        add(f"| `{name}` | " + " | ".join(cells) + " |")
    add("")

    add("## Judged properties")
    add("")
    columns = [n for n in ("p-8b-soft", "p3-8b-soft", "p-27b-soft", "p3-27b-soft", "d3-8b", "d3-27b") if n in before or n in after]
    add("| property | " + " | ".join(f"`{n}`" for n in columns) + " |")
    add("|---|" + "---|" * len(columns))
    for prop in corpus.rubric():
        cells = []
        for name in columns:
            runs = before.get(name) or after.get(name) or []
            cell = compare._cell(_prop(runs, prop))
            prior = phase3.BEFORE.get(name)
            if prior and prior in before:
                cell += compare._clear(_prop(runs, prop), _prop(before[prior], prop))
            cells.append(cell)
        if any(c.strip() != "-" for c in cells):
            add(f"| `{prop}` | " + " | ".join(cells) + " |")
    add("")
    add("Phase 3 columns are marked against the Phase 2 column to their left.")
    add("")

    add("## Correctness checks")
    add("")
    names = sorted({c for runs in after.values() for r in runs for c in r["measured"]["checks"]
                    if c in check_module.CORRECTNESS})
    cols = [n for n in ("p-8b-soft", "p3-8b-soft", "p-27b-soft", "p3-27b-soft") if n in before or n in after]
    add("| check | " + " | ".join(f"`{n}`" for n in cols) + " |")
    add("|---|" + "---|" * len(cols))
    for check in names:
        cells = [compare._cell([r["measured"]["checks"].get(check, (0, 0))
                                for r in (before.get(n) or after.get(n) or [])]) for n in cols]
        add(f"| `{check}` | " + " | ".join(cells) + " |")
    add("")

    add("## Pipeline integrity")
    add("")
    add("Summed over each arm's runs (evals/integrity.py).")
    add("")
    add("| arm | turns | clock turns searched | values deleted that the evidence held | display changed by speech shaping | notation broken by a deterministic stage |")
    add("|---|---|---|---|---|---|")
    for name in [n for n in ("p-8b-soft", "p-8b-nosoft", "p-27b-soft", "p-27b-nosoft") if n in before] + [n for n in after if not n.startswith("d3-")]:
        runs = before.get(name) or after.get(name)
        total = lambda key: sum(r["integrity"][key] for r in runs)  # noqa: E731
        add(f"| `{name}` | {total('turns')} | {total('clock_turns_searched')} of {total('clock_turns')} "
            f"| {total('values_deleted_with_evidence')} | {total('display_speech_shaped')} | {total('notation_changed')} |")
    add("")

    add("## Display and speech")
    add("")
    add("Coverage is the share of the display's content words and numbers "
        "the voice was handed (brain/realize.coverage): the criterion is "
        "substance, not string equality.")
    add("")
    add("| arm | turns spoken | mean coverage | turns under 90% | speech characters p50 | display characters p50 |")
    add("|---|---|---|---|---|---|")
    for name in [n for n in ("p-8b-soft", "p-27b-soft") if n in before] + [n for n in after if not n.startswith("d3-")]:
        runs = before.get(name) or after.get(name)
        speech = [r["speech"] for r in runs]
        add(f"| `{name}` | {sum(s['turns'] for s in speech)} "
            f"| {mean(s['coverage_mean'] for s in speech):.2f} "
            f"| {sum(s['coverage_below_90'] for s in speech)} "
            f"| {mean(s['speech_chars_p50'] for s in speech):.0f} "
            f"| {mean(s['display_chars_p50'] for s in speech):.0f} |")
    add("")

    add("## Reply length")
    add("")
    add("Median words per scored reply, by suite.")
    add("")
    add("| arm | " + " | ".join(corpus.SUITES) + " |")
    add("|---|" + "---|" * len(corpus.SUITES))
    for name in [n for n in ("p-8b-soft", "p-27b-soft", "d-8b", "d-27b") if n in before] + decision:
        runs = before.get(name) or after.get(name)
        cells = []
        for suite in corpus.SUITES:
            values = [r["words"].get(suite) for r in runs if r["words"].get(suite) is not None]
            cells.append(f"{mean(values):.0f}" if values else "-")
        add(f"| `{name}` | " + " | ".join(cells) + " |")
    add("")

    add("## Soft stages")
    add("")
    add("How often each SOFT stage changed a reply, summed over runs, and what "
        "turning them all off did.")
    add("")
    stage_names = sorted({s for runs in list(before.values()) + list(after.values()) for r in runs for s in r["stages"]})
    stage_cols = [n for n in ("p-8b-soft", "p3-8b-soft", "p-27b-soft", "p3-27b-soft") if n in before or n in after]
    add("| stage | " + " | ".join(f"`{n}`" for n in stage_cols) + " |")
    add("|---|" + "---|" * len(stage_cols))
    for stage in stage_names:
        add(f"| `{stage}` | " + " | ".join(
            str(sum(r["stages"].get(stage, 0) for r in (before.get(n) or after.get(n) or [])))
            for n in stage_cols) + " |")
    add("")
    for model in ("8b", "27b"):
        on, off = after.get(f"p3-{model}-soft"), after.get(f"p3-{model}-nosoft")
        if on and off:
            add(f"- **{model.upper()}**: soft on {compare._cell(_scen(on))}, soft off "
                f"{compare._cell(_scen(off))}{compare._clear(_scen(off), _scen(on))} scenarios passing; "
                f"`only_what_helps` {compare._cell(_prop(on, 'only_what_helps'))} vs "
                f"{compare._cell(_prop(off, 'only_what_helps'))}; `no_service_phrasing` "
                f"{compare._cell(_prop(on, 'no_service_phrasing'))} vs "
                f"{compare._cell(_prop(off, 'no_service_phrasing'))}.")
    add("")

    add("## Follow-ups (R9), reported apart")
    add("")
    add("The `followups` suite: an explanation, then the person saying it did "
        "not land. Not part of the Phase 2 corpus.")
    add("")
    add("| arm | scored turns passing | answers rather than asks | stays on the subject | `answers_the_ask` | `adapts_to_confusion` |")
    add("|---|---|---|---|---|---|")
    for name in [n for n in after if n.startswith("f3-")]:
        runs = after[name]
        add(f"| `{name}` | {compare._cell(_pairs(runs, 'turn_passes'))} "
            f"| {compare._cell([r['measured']['checks'].get('answers_not_asks', (0, 0)) for r in runs])} "
            f"| {compare._cell([r['measured']['checks'].get('must_contain_any', (0, 0)) for r in runs])} "
            f"| {compare._cell(_prop(runs, 'answers_the_ask'))} "
            f"| {compare._cell(_prop(runs, 'adapts_to_confusion'))} |")
    add("")

    add("## Drift check")
    add("")
    add("The no-pipeline arms run code Phase 3 did not touch. If they move, "
        "the environment moved.")
    add("")
    add("| arm | Phase 2 | Phase 3 | turns passing P2 → P3 | tok/s P2 → P3 |")
    add("|---|---|---|---|---|")
    for name in ("d3-8b", "d3-27b"):
        if name in after:
            prior = phase3.BEFORE[name]
            old, new = before.get(prior, []), after[name]
            add(f"| `{name}` | `{prior}` | | {compare._cell(_pairs(old, 'turn_passes'))} → "
                f"{compare._cell(_pairs(new, 'turn_passes'))} | "
                f"{compare._seconds(_lat(old, 'answer_tps'))} → {compare._seconds(_lat(new, 'answer_tps'))} |")
    add("")

    add("## Latency with the voice stack loaded")
    add("")
    add("| arm | vs | p50 s | p95 s | routing p50 s | answer tok/s | VRAM MiB |")
    add("|---|---|---|---|---|---|---|")
    for name in ("v3-8b", "v3-27b"):
        if name in after:
            prior = phase3.BEFORE[name]
            old, new = before.get(prior, []), after[name]
            add(f"| `{name}` | `{prior}` | {compare._seconds(_lat(old, 'p50'))} → {compare._seconds(_lat(new, 'p50'))} "
                f"| {compare._seconds(_lat(old, 'p95'))} → {compare._seconds(_lat(new, 'p95'))} "
                f"| {compare._seconds(_lat(old, 'route_p50'))} → {compare._seconds(_lat(new, 'route_p50'))} "
                f"| {compare._seconds(_lat(old, 'answer_tps'))} → {compare._seconds(_lat(new, 'answer_tps'))} "
                f"| {max(x['vram_max'] for x in (r['latency'] for r in new)) or '-'} |")
    add("")

    add("## What the judge can and cannot be trusted with")
    add("")
    add(f"`{judge}` agreed with the v3 human labels on 87% of verdicts (kappa "
        "0.73). `adapts_to_confusion` is below the agreement bar -- read it as a "
        "direction. Korean `accurate` verdicts are weaker than English ones. A "
        "rise in a rarely-yes property (`concrete_first`, `says_what_it_solves`, "
        "`adapts_to_confusion`) needs a blind human check before it is believed.")
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--judge", default=DEFAULT_JUDGE)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    before = _load(phase2.ROOT, phase2.ARMS, args.judge)
    after = _load(phase3.ROOT, phase3.ARMS, args.judge)
    if not after:
        print(f"No Phase 3 runs under {phase3.ROOT}.")
        return 1
    document = write(before, after, args.judge)
    if args.out:
        Path(args.out).write_text(document, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
