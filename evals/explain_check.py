"""The revised explanation contract's check: forced examples, answer-first, quality.

    .venv/Scripts/python.exe -m evals.explain_check --run
    .venv/Scripts/python.exe -m evals.explain_check --report docs/EXPLANATION_CHECK_RESULTS.md

docs/EXPLANATION_CHECK.md. Stage C's contract made a concrete opening the
rule; the goal is an understandable, natural explanation, with an example as
a tool. The revision (brain/explain_contract.CONTRACT_TEXT) puts a direct
answer first and adds an example only when it helps. Before her own use runs
it, this checks three things, on qwen3:8b with production's post-processing:

* **forced examples** -- on turns where a direct answer is the natural
  explanation, does the reply open with a scene before answering?
* **answer first** -- does the first sentence do what was asked
  (``leads_with_the_answer``, the rubric's strict definition, unchanged)?
* **explanation quality** -- substance and explanation defects other than
  ``concrete_first``, which here is read, not targeted.

Arms: A (current production), B (Stage C's contract, word for word), R (the
revision), three runs each on ``explain_check`` (18 new first explanations,
each marked in advance ``direct`` or ``example``); and R three times on Stage
C's held-out set, compared with Stage C's own A and B runs there. The
criteria below were written before any of these turns ran.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ELAINA_TTS", "off")

from brain import explain_contract as ec  # noqa: E402
from evals import corpus, fastdeep, phase2, stagec  # noqa: E402
from evals import fastdeep_analysis as fd  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402
from evals.phase2 import Arm, M8, NO_STT  # noqa: E402

ROOT = fd.EVALS / "pcx"
SUITE = "explain_check"
HELDOUT = stagec.SUITE
RUNS = 3
_BASE = {**NO_STT, "ELAINA_DOMAINS": "act", "ELAINA_MODEL": M8, ec.EXPERIMENT_ENV: "off"}
ARMS = {
    "A": Arm("pcx-a", "pipeline", "A: current production", env=dict(_BASE), runs=RUNS, suites=(SUITE,)),
    "B": Arm("pcx-b", "pipeline", "B: Stage C's contract, word for word",
             env={**_BASE, ec.ARM_ENV: ec.CONTRACT_STAGE_C}, runs=RUNS, suites=(SUITE,)),
    "R": Arm("pcx-r", "pipeline", "R: the revised contract",
             env={**_BASE, ec.ARM_ENV: ec.CONTRACT}, runs=RUNS, suites=(SUITE,)),
    "RH": Arm("pcx-rh", "pipeline", "R on Stage C's held-out set",
              env={**_BASE, ec.ARM_ENV: ec.CONTRACT}, runs=RUNS, suites=(HELDOUT,)),
}

# Written before any check turn ran.
CRITERIA = {
    "leads_regressions_max": 1,      # R against A, stable, on each set
    "leads_rate_drop_max": 0.05,     # R's leads_with_the_answer yes rate, against A's, on each set
    "forced_rate_max": 0.10,         # 'direct' turns: scene first and the answer not first
    "forced_over_current_max": 0.05,
    "quality_net_min": 0,            # R against A, stable better - worse, on each set
    "substance_regressions_max": 1,  # R against A, on each set
}
QUALITY = (fd.SUBSTANCE_CHECKS | fd.SUBSTANCE_PROPERTIES | fd.EXPLANATION) - {"concrete_first"}
_EXAMPLE = re.compile(r"for example|for instance|imagine|suppose|say you|let's say|think of|it's like|"
                      r"it is like|like when|예를 들어|예컨대|가령|만약|마치|처럼 ")


# ------------------------------------------------------------- running


@contextmanager
def held_out():
    saved = corpus.ALL_SUITES
    corpus.ALL_SUITES = saved + tuple(s for s in (SUITE, HELDOUT) if s not in saved)
    try:
        yield
    finally:
        corpus.ALL_SUITES = saved


def _child(argv: list[str]) -> int:
    which, rest = argv[0], argv[1:]
    with held_out():
        if which == "run":
            from evals import run as runner
            sys.argv = ["evals.run", *rest]
            return runner.main()
        if which == "judge":
            from evals import judge as judging
            sys.argv = ["evals.judge", *rest]
            return judging.main()
    raise SystemExit(f"unknown child {which!r}")


def planned() -> list[tuple[Arm, Path]]:
    found = [(ARMS[name], ROOT / f"{ARMS[name].name}-{n}") for n in range(1, RUNS + 1)
             for name in ("A", "B", "R")]
    return found + [(ARMS["RH"], ROOT / f"{ARMS['RH'].name}-{n}") for n in range(1, RUNS + 1)]


def _expected(arm: Arm) -> int:
    with held_out():
        return len(corpus.load(arm.suites))


def _complete(arm: Arm, out: Path) -> bool:
    path = out / "results.jsonl"
    return path.exists() and len([x for x in path.read_text(encoding="utf-8").splitlines()
                                  if x.strip()]) >= _expected(arm)


def run() -> int:
    from evals.run import port_open

    phase2.ROOT = ROOT
    phase2.LOG = ROOT / "progress.log"
    fastdeep.ROOT = ROOT
    phase2.log(f"explanation check; revised contract {ec.CONTRACT_VERSION}, Stage C "
               f"{ec.STAGE_C_CONTRACT_VERSION}")
    for arm, out in planned():
        if _complete(arm, out):
            phase2.log(f"generate {out.name}: already complete, skipped")
            continue
        if port_open():
            phase2.log("stopping: port 8765 is in use -- close Elaina first")
            return 2
        phase2.unload_models()
        command = [str(phase2.PYTHON), "-m", "evals.explain_check", "--child", "run", "--out", str(out),
                   "--suites", *arm.suites, "--env", *[f"{k}={v}" for k, v in arm.env.items()]]
        phase2.log(f"generate {out.name}: {arm.about}")
        fastdeep.log_gpu(f"before {out.name}")
        code = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False).returncode
        fastdeep.log_gpu(f"after {out.name}")
        phase2.log(f"generate {out.name}: exit {code}")
        if code != 0:
            return 1
    phase2.unload_models()
    phase2.log(f"judge with {DEFAULT_JUDGE}")
    code = subprocess.run([str(phase2.PYTHON), "-m", "evals.explain_check", "--child", "judge",
                           "--model", DEFAULT_JUDGE, "--run", *[str(out) for _a, out in planned()]],
                          cwd=str(PROJECT_ROOT), check=False).returncode
    phase2.log(f"judge: exit {code}")
    return code


# ------------------------------------------------------------- analysis


def need() -> dict[str, str]:
    raw = json.loads((corpus.SCENARIOS / f"{SUITE}.json").read_text(encoding="utf-8"))
    return {s["id"]: s["need"] for s in raw["scenarios"]}


def _runs(root: Path, prefix: str) -> list[dict]:
    with held_out():
        return [fd.load_run(p) for p in fd._runs_of(root, prefix)]


def _verdict(turn, name: str) -> str:
    return str(turn.properties.get(name, ""))


def rates(runs: list[dict], keys) -> dict:
    """Shares of comparable turn-runs: leads yes, concrete_first yes, scene first
    with the answer not first ("forced"), an example anywhere (a reading aid),
    and no quality defect."""
    rows = [r["turns"][k] for r in runs for k in keys if k in r["turns"] and r["turns"][k].comparable]
    displays = {(r["path"], k): t["display"] or "" for r in runs for res in r["run"]["results"]
                for t in res["turns"] for k in [(res["scenario"], t["index"])] if k in keys}
    n = len(rows)

    def share(test):
        return sum(1 for t in rows if test(t)) / n if n else None

    examples = [bool(_EXAMPLE.search(text.lower())) for text in displays.values()]
    return {
        "n": n,
        "leads": share(lambda t: _verdict(t, "leads_with_the_answer") == "yes"),
        "concrete_first": share(lambda t: _verdict(t, "concrete_first") == "yes"),
        "forced": share(lambda t: _verdict(t, "concrete_first") == "yes"
                        and _verdict(t, "leads_with_the_answer") == "no"),
        "quality": share(lambda t: not (t.defects & QUALITY)),
        "example_anywhere": sum(examples) / len(examples) if examples else None,
        "words": median(len(text.split()) for text in displays.values()) if displays else None,
    }


def quality_net(base: list[dict], arm: list[dict], keys) -> tuple[int, int]:
    """Stable (2 of 3 run pairs) better and worse turns on quality defects only."""
    better = worse = 0
    for key in keys:
        kinds = []
        for b, a in zip(base, arm):
            tb, ta = b["turns"].get(key), a["turns"].get(key)
            if not (tb and ta and tb.comparable and ta.comparable):
                continue
            fixed, broken = (tb.defects & QUALITY) - ta.defects, (ta.defects & QUALITY) - tb.defects
            kinds.append("better" if fixed and not broken else "worse" if broken and not fixed else "")
        better += kinds.count("better") >= 2
        worse += kinds.count("worse") >= 2
    return better, worse


def analyse() -> dict:
    with held_out():
        check_keys = [(s.id, i) for s in corpus.load((SUITE,)) for i, _ in s.scored]
    heldout_keys = stagec.heldout_keys()
    needs = need()
    direct = [k for k in check_keys if needs[k[0]] == "direct"]
    example = [k for k in check_keys if needs[k[0]] == "example"]
    runs = {name: _runs(ROOT, ARMS[name].name) for name in ARMS}
    stage_c = {name: _runs(stagec.ROOT, stagec.ARMS[name].name) for name in ("A", "B")}
    sets = {
        "check": {"keys": check_keys, "A": runs["A"], "B": runs["B"], "R": runs["R"]},
        "held-out": {"keys": heldout_keys, "A": stage_c["A"], "B": stage_c["B"], "R": runs["RH"]},
    }
    found = {"runs": runs, "groups": {"direct": direct, "example": example}, "sets": {}}
    for name, s in sets.items():
        found["sets"][name] = {
            "rates": {arm: rates(s[arm], s["keys"]) for arm in ("A", "B", "R")},
            "regressions": {arm: stagec.regressions(s["A"], s[arm], s["keys"]) for arm in ("B", "R")},
            "quality_net": {arm: quality_net(s["A"], s[arm], s["keys"]) for arm in ("B", "R")},
            "r_vs_b": quality_net(s["B"], s["R"], s["keys"]),
        }
    found["group_rates"] = {g: {arm: rates(runs[arm], keys) for arm in ("A", "B", "R")}
                            for g, keys in found["groups"].items()}
    return found


def verdict(a: dict) -> list[tuple[str, str, bool]]:
    rows = []
    for name, s in a["sets"].items():
        leads_reg = len(s["regressions"]["R"][1])
        rows.append((f"{name}: answer-first regressions, R against A",
                     f"{leads_reg} (limit {CRITERIA['leads_regressions_max']})",
                     leads_reg <= CRITERIA["leads_regressions_max"]))
        r_leads, a_leads = s["rates"]["R"]["leads"], s["rates"]["A"]["leads"]
        ok = r_leads is not None and a_leads is not None and r_leads >= a_leads - CRITERIA["leads_rate_drop_max"]
        rows.append((f"{name}: answer-first rate, R against A", f"{_p(r_leads)} vs {_p(a_leads)}", ok))
        better, worse = s["quality_net"]["R"]
        rows.append((f"{name}: quality, R against A (stably better - worse)",
                     f"{better} - {worse} = {better - worse:+d}", better - worse >= CRITERIA["quality_net_min"]))
        sub = len(s["regressions"]["R"][0])
        rows.append((f"{name}: substance regressions, R against A",
                     f"{sub} (limit {CRITERIA['substance_regressions_max']})",
                     sub <= CRITERIA["substance_regressions_max"]))
    direct = a["group_rates"]["direct"]
    forced_r, forced_a = direct["R"]["forced"], direct["A"]["forced"]
    rows.append(("'direct' turns: forced example openings, R",
                 f"{_p(forced_r)} (A {_p(forced_a)}, B {_p(direct['B']['forced'])})",
                 forced_r is not None and forced_a is not None
                 and forced_r <= CRITERIA["forced_rate_max"]
                 and forced_r <= forced_a + CRITERIA["forced_over_current_max"]))
    return rows


def _p(value) -> str:
    return "-" if value is None else f"{value:.0%}"


def report() -> str:
    a = analyse()
    rows = verdict(a)
    passed = all(ok for *_, ok in rows)
    lines = ["# The revised explanation contract: check", "",
             f"*Generated by `evals/explain_check.py` on {datetime.now():%Y-%m-%d %H:%M}. Revised contract "
             f"`{ec.CONTRACT_VERSION}`, Stage C contract `{ec.STAGE_C_CONTRACT_VERSION}`. "
             "docs/EXPLANATION_CHECK.md.*", "",
             "## Outcome", "", "**" + ("PASSES" if passed else "DOES NOT PASS") + "**", "",
             "| criterion | measured | met |", "|---|---|---|"]
    lines += [f"| {name} | {measured} | {'yes' if ok else '**no**'} |" for name, measured, ok in rows]
    lines += ["", "## Rates", "",
              "Shares of judged turn-runs. *Forced*: concrete_first yes and leads_with_the_answer no "
              "(a scene before the answer). *Example anywhere* is a wording search: a reading aid.", ""]
    head = "| set | arm | n | answer first | concrete first | forced | quality, no defect | example anywhere | words |"
    lines += [head, "|---|---|---|---|---|---|---|---|---|"]
    tables = [(f"check: {g}", rates_) for g, rates_ in a["group_rates"].items()]
    tables += [(name, s["rates"]) for name, s in a["sets"].items()]
    for label, by_arm in tables:
        for arm in ("A", "B", "R"):
            r = by_arm[arm]
            lines.append(f"| {label} | {arm} | {r['n']} | {_p(r['leads'])} | {_p(r['concrete_first'])} "
                         f"| {_p(r['forced'])} | {_p(r['quality'])} | {_p(r['example_anywhere'])} "
                         f"| {r['words'] if r['words'] is not None else '-'} |")
    lines += ["", "## Against current production (A) and Stage C's contract (B)", ""]
    for name, s in a["sets"].items():
        for arm in ("B", "R"):
            sub, leads = s["regressions"][arm]
            better, worse = s["quality_net"][arm]
            lines.append(f"- {name}, {arm} against A: quality {better} better, {worse} worse; substance "
                         f"regressions {len(sub)}; answer-first regressions {len(leads)}"
                         + (f" ({', '.join(f'`{k[0]}`' for k in leads)})" if leads else ""))
        better, worse = s["r_vs_b"]
        lines.append(f"- {name}, R against B: quality {better} better, {worse} worse")
    lines += ["", "The held-out comparison uses Stage C's own A and B runs (runtime/evals/pc), from an "
              "earlier session with the same code apart from the contract.", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        return _child(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    if args.run:
        return run()
    if args.report:
        Path(args.report).write_text(report(), encoding="utf-8")
        print(f"Wrote {args.report}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
