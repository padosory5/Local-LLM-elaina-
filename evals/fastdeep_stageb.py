"""Stage B diagnostic: DEEP under the FAST router, on the follow-up turns.

    .venv/Scripts/python.exe -m evals.fastdeep_stageb              # generate, judge
    .venv/Scripts/python.exe -m evals.fastdeep_stageb --report docs/FAST_DEEP_STAGEB.md

docs/FAST_DEEP_PROTOCOL.md §12. The baseline's DEEP wins on follow-up
turns mostly fell where the 27B's router read the turn differently from the
8B's (9 of the 12 turns rule R2 catches): the 27B called a confusion
follow-up a clarification, which also chose the re-explanation instruction
the answer was written under. A FAST/DEEP system routes with the 8B, so
the question is what the 27B's answer is worth under the 8B's reading.

Three arms, the same code and session, three runs each, alternating, on the
scenarios that hold follow-up turns:

* FAST  -- the 8B reads, the 8B answers, soft stages on;
* SPLIT -- the 8B reads, the 27B answers, soft stages off (DEEP's setting);
* DEEP  -- the 27B reads, the 27B answers, soft stages off.

Per turn, with the frozen analysis's comparison and 2-of-3 rule:

* FAST against SPLIT is what sending the turn to DEEP buys under the 8B's
  reading -- the routing question;
* SPLIT against DEEP is what the 27B's reading adds, with the answer model
  and soft stages held -- the interpretation effect;
* FAST against DEEP repeats the baseline comparison in this session.

SPLIT's history is the 27B's own earlier answers, as DEEP's is; a system
that routes only the follow-up would have FAST's. That needs a model switch
inside a conversation, which does not exist yet.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import compare, compare3, corpus, fastdeep, phase2  # noqa: E402
from evals import fastdeep_analysis as fd  # noqa: E402
from evals.phase2 import Arm, M8, M27, NO_STT, SOFT_OFF  # noqa: E402

ROOT = PROJECT_ROOT / "runtime" / "evals" / "pb"
RUNS = 3
# Every scenario with a scored turn after the first that the baseline's
# follow-up analysis touched: the follow-up suite, the trajectories, and the
# contamination cases whose later turn is an explanation or a follow-up.
SCENARIOS = (
    "index_fund_what_do_you_mean", "noise_cancelling_another_way", "p_value_last_part",
    "ko_quantum_computer_still_lost", "ko_exchange_rate_another_way",
    "ko_climate_weather_last_part",
    "live_session_2026_09_23", "still_confused_derivative", "ko_taylor_then_time",
    "explanation_thanks_time",
    "fresh_question_after_time", "a_correction_outranks_what_was_held",
    "thanks_closes_rather_than_continues", "steep_time_after_coffee",
)
ACT = {"ELAINA_DOMAINS": "act"}
ARMS = {
    "fast": Arm("pb-fast", "pipeline", "FAST: 8B reads and answers, soft on",
                env={**NO_STT, **ACT, "ELAINA_MODEL": M8}, runs=RUNS,
                suites=corpus.ALL_SUITES, only=SCENARIOS),
    "split": Arm("pb-split", "pipeline", "SPLIT: 8B reads, 27B answers, soft off",
                 env={**NO_STT, **ACT, "ELAINA_MODEL": M8, "ELAINA_CONVERSATION_MODEL": M27,
                      **SOFT_OFF},
                 runs=RUNS, suites=corpus.ALL_SUITES, only=SCENARIOS),
    "deep": Arm("pb-deep", "pipeline", "DEEP: 27B reads and answers, soft off",
                env={**NO_STT, **ACT, "ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27,
                     **SOFT_OFF},
                runs=RUNS, suites=corpus.ALL_SUITES, only=SCENARIOS),
}


def planned() -> list[tuple[Arm, Path]]:
    """FAST, SPLIT, DEEP alternating run by run."""
    return [(arm, ROOT / f"{arm.name}-{n}") for n in range(1, RUNS + 1) for arm in ARMS.values()]


def _complete(out: Path) -> bool:
    path = out / "results.jsonl"
    return path.exists() and len(
        [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]) >= len(SCENARIOS)


def run() -> int:
    phase2.ROOT = ROOT
    phase2.LOG = ROOT / "progress.log"
    frozen, recorded, now = fd.frozen_state()
    if not frozen:
        print(f"The analysis is not the frozen one ({recorded} vs {now}); nothing run.")
        return 2
    phase2.log(f"Stage B diagnostic; analysis frozen at {recorded}")
    outs = []
    for arm, out in planned():
        outs.append(out)
        if _complete(out):
            phase2.log(f"generate {out.name}: already complete, skipped")
            continue
        fastdeep.ROOT = ROOT
        fastdeep.log_gpu(f"before {out.name}")
        code = phase2.generate(arm, out)
        fastdeep.log_gpu(f"after {out.name}")
        if code != 0:
            phase2.log(f"stopping: {out.name} failed")
            return 1
    return phase2.judge(outs)


# ------------------------------------------------------------------ report


def _runs(name: str) -> list[dict]:
    return [fd.load_run(p) for p in fd._runs_of(ROOT, ARMS[name].name)]


BLIND = ROOT / "split_blind.json"


def read_split_labels(path: Path = BLIND) -> dict:
    """{(scenario, index): {confirmed run numbers}} for FAST against SPLIT.

    The frozen reader's rule (evals/fastdeep_analysis.read_human_labels),
    with SPLIT in DEEP's place: confirmed when the person labelled the FAST
    reply "no" and the SPLIT reply "yes" on every property it hinges on.
    """
    import json

    if not path.exists():
        return {}
    by_comparison: dict = {}
    for item in json.loads(path.read_text(encoding="utf-8"))["items"]:
        arm = "split" if ARMS["split"].name in item["run"] else "fast"
        key = (item["scenario"], item["index"], item["comparison"])
        by_comparison.setdefault(key, {})[arm] = item["labels"]
    confirmed: dict = {}
    for (scenario, index, number), arms in by_comparison.items():
        fast, split = arms.get("fast", {}), arms.get("split", {})
        if fast and split and all(fast.get(n) == "no" and split.get(n) == "yes" for n in split):
            confirmed.setdefault((scenario, index), set()).add(number)
    return confirmed


def labels_between(first: list[dict], second: list[dict], human: dict | None = None) -> dict:
    """The frozen comparison and 2-of-3 rule, first arm as 'FAST', second as
    'DEEP'. A comparison resting only on a below-bar property counts once a
    person's blind labels confirm it (``human``); until then the turn reads
    "better (judge-only)"."""
    return fd.label_turns(first, second, human)


def _readings(runs: list[dict], key) -> str:
    counted = Counter((t["turns"][key].route.get("intent"), t["turns"][key].route.get("budget"))
                      for t in runs if key in t["turns"])
    return ", ".join(f"{i}/{b} ×{n}" for (i, b), n in counted.most_common())


def _short(label: str) -> str:
    return {"stable DEEP+": "better", "pending DEEP+": "better (judge-only)",
            "stable DEEP-worse": "worse", fd.UNSTABLE: "—"}.get(label, label)


def report() -> str:
    fast, split, deep = _runs("fast"), _runs("split"), _runs("deep")
    fs = labels_between(fast, split, read_split_labels())
    sd, fdp = labels_between(split, deep), labels_between(fast, deep)
    base_fast = [fd.load_run(p) for p in fd._runs_of(fd.ROOT, fd.FAST_ARM)]
    base_deep = [fd.load_run(p) for p in fd._runs_of(fd.ROOT, fd.DEEP_ARM)]
    baseline = fd.label_turns(base_fast, base_deep, fd.read_human_labels())
    later = sorted(k for k in fs if k[1] > 0 and k[0] in SCENARIOS)
    first = sorted(k for k in fs if k[1] == 0 and k[0] in SCENARIOS)
    lines: list[str] = []
    add = lines.append
    frozen, recorded, now = fd.frozen_state()
    add("# Stage B: DEEP under the FAST router, on the follow-up turns")
    add("")
    add(f"*Generated by `evals/fastdeep_stageb.py` on {datetime.now():%Y-%m-%d %H:%M}. Runs in "
        f"`runtime/evals/pb/` (FAST, SPLIT, DEEP; {RUNS} runs each, one session, current code). "
        f"Comparisons use the frozen analysis (`{now}`, "
        + ("frozen" if frozen else "**NOT frozen**") + "). Protocol: docs/FAST_DEEP_PROTOCOL.md §12.*")
    add("")
    add("Each column compares two arms turn by turn: \"better\" means the second arm is stably "
        "better (2 of 3 runs, substance or explanation, no substance loss), \"worse\" the "
        "reverse, \"—\" neither. **FAST→SPLIT** is what routing the turn to DEEP buys with the "
        "8B's reading; **SPLIT→DEEP** is what the 27B's reading adds on top; **FAST→DEEP** is "
        "the baseline comparison repeated in this session.")
    add("")
    for title, keys in (("Later turns (the follow-ups)", later), ("First turns", first)):
        add(f"## {title}")
        add("")
        add("| turn | category | baseline FAST→DEEP | FAST→DEEP now | FAST→SPLIT | SPLIT→DEEP "
            "| 8B reading (SPLIT) | 27B reading (DEEP) |")
        add("|---|---|---|---|---|---|---|---|")
        for key in keys:
            add(f"| `{key[0]}` t{key[1]} | {fs[key].category} "
                f"| {_short(baseline[key].label) if key in baseline else '-'} "
                f"| {_short(fdp[key].label)} | {_short(fs[key].label)} | {_short(sd[key].label)} "
                f"| {_readings(split, key)} | {_readings(deep, key)} |")
        add("")
        for name, table in (("FAST→DEEP now", fdp), ("FAST→SPLIT", fs), ("SPLIT→DEEP", sd)):
            counted = Counter(_short(table[k].label) for k in keys)
            add(f"- {name}: " + ", ".join(f"{v} {counted.get(v, 0)}"
                                          for v in ("better", "better (judge-only)", "worse", "—")))
        add("")
    # R2's baseline catches, followed through
    r2 = [k for k, v in baseline.items() if v.label == "stable DEEP+" and k[0] in SCENARIOS and k[1] > 0]
    kept = [k for k in r2 if k in fs and fs[k].label == "stable DEEP+"]
    add("## The baseline's follow-up DEEP+ turns, under the 8B's reading")
    add("")
    add(f"Of the {len(r2)} later turns that were stable DEEP+ in the baseline, "
        f"{len(kept)} are stably better with the 27B answering under the 8B's reading (FAST→SPLIT), "
        f"and {sum(1 for k in r2 if k in fdp and fdp[k].label == 'stable DEEP+')} are stably "
        "better again with DEEP's own reading in this session (FAST→DEEP now).")
    add("")
    # latency and the real swaps
    add("## Time and model swaps")
    add("")
    add("SPLIT loads the 8B and the 27B in turn on every turn: its loads are real swaps, the "
        "worst case a routed system could see.")
    add("")
    add("| arm | turn p50 / p95 s | 8B loads (count, median s) | 27B loads (count, median s) |")
    add("|---|---|---|---|")
    for name, runs in (("FAST", fast), ("SPLIT", split), ("DEEP", deep)):
        seconds = [s for r in runs for s in [compare.latency(r["run"])["p50"]]]
        p95 = [compare.latency(r["run"])["p95"] for r in runs]
        loads = {M8: [], M27: []}
        for r in runs:
            for record in compare3._records(r["path"]):
                for call in record.get("model_calls") or ():
                    value = (call.get("load_duration") or 0) / 1e9
                    if value > 0.5 and call.get("model") in loads:
                        loads[call["model"]].append(value)
        describe = lambda xs: f"{len(xs)}, {median(xs):.1f}" if xs else "0"  # noqa: E731
        add(f"| {name} | {mean(seconds):.1f} / {mean(p95):.1f} | {describe(loads[M8])} "
            f"| {describe(loads[M27])} |")
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", default="", help="write the report here instead of running")
    args = parser.parse_args()
    if args.report:
        text = report()
        Path(args.report).write_text(text, encoding="utf-8")
        print(f"Wrote {args.report}")
        return 0
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
