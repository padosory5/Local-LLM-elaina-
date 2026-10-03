"""The FAST/DEEP analysis, frozen before the post-fix baseline.

    .venv/Scripts/python.exe -m evals.fastdeep_analysis --reproduce
    .venv/Scripts/python.exe -m evals.fastdeep_analysis --freeze
    .venv/Scripts/python.exe -m evals.fastdeep_analysis --out docs/FAST_DEEP_BASELINE.md

Everything here is fixed by docs/FAST_DEEP_PROTOCOL.md (with its approved
amendments) before the baseline is seen. It reads runs the way
evals/report.py reads them -- the same scored turns, the same verdicts, the
same checks -- and adds only what the protocol defines:

* the tier of each defect (§3.1),
* the per-turn comparison of FAST and DEEP in corresponding runs, and the
  stable labels (§3.3, amendment 2),
* the fixed category of each scored turn (§5, amendment 4),
* routing policies scored against those labels (§4, amendment 3),
* the pressure rule and the resource readings (§6).

``--freeze`` records a fingerprint of this file and of everything it scores
with. The report says whether the analysis that produced it is still the
frozen one; changing any of it after the baseline has been seen is a new
protocol version, not an edit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import checks, compare, compare3, corpus, report  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402

EVALS = PROJECT_ROOT / "runtime" / "evals"
ROOT = EVALS / "pf"
FROZEN = ROOT / "FROZEN.json"
HUMAN_LABELS = ROOT / "deep_plus_blind.json"
FAST_ARM, DEEP_ARM = "pf-fast", "pf-deep"
RUNS = 3
UNSTABLE = "unstable / no routing ground truth"

# ------------------------------------------------------------------ tiers

# What a defect says about the answer (protocol §3.1). A grouping of
# existing checks and rubric properties; it adds no score.
SUBSTANCE_CHECKS = frozenset(checks.CORRECTNESS) | frozenset({
    "must_contain_any", "must_not_contain", "language", "answers_not_asks", "reply",
})
SUBSTANCE_PROPERTIES = frozenset({
    "answers_the_ask", "takes_the_turn_as_meant", "accurate", "adapts_to_confusion",
})
EXPLANATION = frozenset({"says_what_it_solves", "concrete_first", "terms_explained"})
FORM = frozenset({
    "only_what_helps", "leads_with_the_answer", "no_service_phrasing",
    "notation_earned", "no_unrelated_remark",
})
PIPELINE = frozenset({"not_searched", "no_cards", "speakable", "display_math_intact"})
# Below the agreement bar in the 2026-09-29 blind labels: concrete_first 75%,
# adapts_to_confusion 75% (kappa 0.38). A DEEP+ resting only on these waits
# for a person's blind labels (amendment 2).
BELOW_BAR = frozenset({"concrete_first", "adapts_to_confusion"})


def tier(defect: str) -> str:
    if defect in SUBSTANCE_CHECKS or defect in SUBSTANCE_PROPERTIES:
        return "substance"
    if defect in EXPLANATION:
        return "explanation"
    if defect in FORM:
        return "form"
    if defect in PIPELINE:
        return "pipeline"
    return "other"


# -------------------------------------------------------------- categories

# Fixed before any arm runs, from corpus metadata only: the turn's rubric
# mode, its suite, and the scenario's identity (protocol §5, amendment 4).
# Nothing a model or a router said about a turn can move it.
_D8 = frozenset({("which_one_means_the_nearest_subject", 2),
                 ("a_new_subject_closes_the_old_one", 1)})
_F5 = frozenset({("steep_time_after_coffee", 1), ("a_finished_task_does_not_reopen", 2),
                 ("a_correction_outranks_what_was_held", 2)})
_F2 = frozenset({"fifteen_percent", "celsius_to_fahrenheit", "cup_ounces",
                 "arithmetic_in_a_social_thread"})
_F3 = frozenset({"time_now", "time_zone", "utc_offset", "time_seattle_vs_seoul", "date_today",
                 "year_now", "ko_time_now", "ko_date_today"})
_TECHNICAL = frozenset({"hash_table_how", "cpu_cache_why", "recursion_example",
                        "ko_hash_table_when"})
CATEGORIES = {
    "D1": "\"I still don't understand\" and conceptual follow-ups",
    "D2": "a different approach for someone who does not follow",
    "D4": "comparisons",
    "D8": "ambiguous, interpretation-heavy",
    "C": "first-time explanations (contested)",
    "F1": "simple factual",
    "F2": "deterministic calculation and conversion",
    "F3": "time and date",
    "F4": "straightforward definitions and formulas",
    "F5": "simple follow-ups",
    "F6": "conversational remarks and receipts",
}
DEEP_EXPECTED = ("D1", "D2", "D4", "D8")
FAST_EXPECTED = ("F1", "F2", "F3", "F4", "F5", "F6")


def category(scenario: corpus.Scenario, index: int) -> str:
    """The fixed category of one scored turn, from the corpus alone."""
    turn = scenario.turns[index]
    mode = turn.mode or scenario.mode
    if mode == "confused":
        return "D1"
    if mode == "intuition":
        return "D2"
    if mode == "comparison":
        return "D4"
    if (scenario.id, index) in _D8:
        return "D8"
    if (scenario.id, index) in _F5:
        return "F5"
    if mode in ("receipt", "remark"):
        return "F6"
    if mode in ("definition", "formula"):
        return "F4"
    if mode in ("cause", "how", "purpose", "example"):
        return "C"
    if scenario.id in _F2:
        return "F2"
    if scenario.id in _F3 or scenario.suite == "trajectories":
        # Every scored "fact" turn in a trajectory is a time question.
        return "F3"
    return "F1"


def technical(scenario_id: str) -> bool:
    return scenario_id in _TECHNICAL


def corpus_index() -> dict[tuple[str, int], tuple[corpus.Scenario, str]]:
    """(scenario, turn) -> (scenario, category) for every scored turn."""
    found = {}
    for scenario in corpus.load(corpus.ALL_SUITES):
        for index, _turn in scenario.scored:
            found[(scenario.id, index)] = (scenario, category(scenario, index))
    return found


# ----------------------------------------------------------------- turns


@dataclass(frozen=True)
class TurnOutcome:
    scenario: str
    index: int
    suite: str
    first: bool
    complete: bool                # every applicable property judged
    judge_error: bool
    checks_failed: frozenset
    properties: dict              # name -> verdict
    correctness: tuple            # (passed, total) correctness checks
    seconds: float | None
    route: dict                   # intent, budget, shape (from the turn record)
    route_seconds: float

    @property
    def defects(self) -> frozenset:
        said_no = {name for name, verdict in self.properties.items()
                   if verdict not in ("yes", "n/a")}
        return frozenset(self.checks_failed) | frozenset(said_no)

    @property
    def passed(self) -> bool:
        return self.complete and not self.checks_failed and all(
            v in ("yes", "n/a") for v in self.properties.values())

    @property
    def comparable(self) -> bool:
        return self.complete and not self.judge_error


def load_run(path: Path, *, recorded_checks: bool = False, judge: str = DEFAULT_JUDGE) -> dict:
    """A run's scored turns as TurnOutcomes, plus the raw run.

    ``recorded_checks`` reads the check results stored when the run was
    generated -- the evaluator of that day -- instead of recomputing them
    with today's evaluator. The verdicts are the same either way.
    """
    run = report.load_run(path, judge)
    scenarios = {s.id: s for s in corpus.load(corpus.ALL_SUITES)}
    outcomes: dict[tuple[str, int], TurnOutcome] = {}
    if recorded_checks:
        rows = _recorded_rows(run, scenarios)
    else:
        rows = (
            (result, stored, wanted, [(c.check, c.passed) for c in found], verdicts)
            for result, stored, wanted, found, verdicts in report.scored_turns(run)
        )
    for result, stored, wanted, found, verdicts in rows:
        properties = {name: v.get("verdict") for name, v in verdicts.items()}
        trace = stored.get("trace") or {}
        limits = trace.get("limits") or {}
        outcomes[(result["scenario"], stored["index"])] = TurnOutcome(
            scenario=result["scenario"],
            index=stored["index"],
            suite=result["suite"],
            first=stored["index"] == 0,
            complete=set(verdicts) == set(wanted.rubric),
            judge_error=any(v == "error" for v in properties.values()),
            checks_failed=frozenset(name for name, passed in found if not passed),
            properties=properties,
            correctness=(
                sum(1 for name, passed in found if name in checks.CORRECTNESS and passed),
                sum(1 for name, _ in found if name in checks.CORRECTNESS),
            ),
            seconds=stored.get("seconds") if stored.get("display") is not None else None,
            route={"intent": trace.get("intent"), "budget": limits.get("budget"),
                   "shape": limits.get("shape")},
            route_seconds=float((trace.get("timings") or {}).get("route") or 0.0),
        )
    return {"path": Path(path), "run": run, "turns": outcomes}


def _recorded_rows(run: dict, scenarios: dict):
    for result in run["results"]:
        scenario = scenarios.get(result["scenario"])
        if scenario is None:
            continue
        for stored in result["turns"]:
            index = stored["index"]
            wanted = scenario.turns[index] if index < len(scenario.turns) else None
            if wanted is None or not wanted.score:
                continue
            if stored["display"] is None:
                found = [("reply", False)]
            else:
                found = [(c["check"], bool(c["passed"])) for c in stored.get("checks") or ()]
            verdicts = {
                name: verdict
                for name, verdict in run["judged"].get((result["scenario"], index), {})
                .get("verdicts", {}).items()
                if name in wanted.rubric
            }
            yield result, stored, wanted, found, verdicts


# ------------------------------------------------------------- measuring


def measures(loaded: dict, suites: tuple[str, ...] | None = None) -> dict:
    """Scenario, turn, correctness and property counts, as report.measure
    counts them (and checked against it by --reproduce)."""
    turns = [t for t in loaded["turns"].values() if suites is None or t.suite in suites]
    scenario_pass: dict[str, bool] = {}
    correctness = [0, 0]
    properties: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for turn in turns:
        scenario_pass[turn.scenario] = scenario_pass.get(turn.scenario, True) and turn.passed
        correctness[0] += turn.correctness[0]
        correctness[1] += turn.correctness[1]
        for name, verdict in turn.properties.items():
            if verdict == "n/a":
                continue
            properties[name][0] += verdict == "yes"
            properties[name][1] += 1
    return {
        "scenarios": (sum(scenario_pass.values()), len(scenario_pass)),
        "turns": (sum(t.passed for t in turns), len(turns)),
        "correctness": tuple(correctness),
        "properties": {name: tuple(v) for name, v in properties.items()},
    }


def _only_suites(run: dict, suites) -> dict:
    if suites is None:
        return run
    return {**run, "results": [r for r in run["results"] if r["suite"] in suites]}


def pressure_affected(call: dict, normal_tps: dict[str, float]) -> bool | None:
    """The pressure rule (§6): a generation below 60% of the same model's
    normal speed this session. None when the call cannot be judged: no
    speed, no normal speed for its model, or under 20 tokens."""
    speed = call.get("tokens_per_second")
    normal = normal_tps.get(call.get("model", ""))
    if not speed or not normal or (call.get("eval_count") or 0) < 20:
        return None
    return speed < 0.6 * normal


def resources(loaded: dict, normal_tps: dict[str, float], suites=None) -> dict:
    """Latency, router, speed, loads, memory and pressure for one run."""
    run = _only_suites(loaded["run"], suites)
    latency = compare.latency(run)
    router = compare3.router_measures(loaded["path"])
    # The pressure rule (§6): a generation below 60% of the same model's
    # normal speed this session is pressure-affected.
    affected_turns = set()
    calls = flagged = 0
    for result in run["results"]:
        for turn in result["turns"]:
            for call in (turn.get("trace") or {}).get("model_calls") or ():
                affected = pressure_affected(call, normal_tps)
                if affected is None:
                    continue
                calls += 1
                if affected:
                    flagged += 1
                    affected_turns.add((result["scenario"], turn["index"]))
    clean = [
        turn["seconds"] for result in run["results"] for turn in result["turns"]
        if turn["display"] is not None and (result["scenario"], turn["index"]) not in affected_turns
    ]
    loads = []
    for record in compare3._records(loaded["path"]):
        if suites is not None and record.get("suite") not in (None, *suites):
            continue
        for call in record.get("model_calls") or ():
            seconds = (call.get("load_duration") or 0) / 1e9
            if seconds > 0.5:
                loads.append(round(seconds, 2))
    return {
        **latency,
        "router": router,
        "pressure_calls": (flagged, calls),
        "p50_clean": median(clean) if clean else 0.0,
        "p95_clean": compare._percentile(clean, 0.95),
        "loads": loads,
        "contaminated": calls > 0 and flagged / calls > 0.05,
    }


# ------------------------------------------------------------ comparison


@dataclass(frozen=True)
class Comparison:
    kind: str                     # deep+, deep-worse, mixed, form-only, same, excluded
    fixed: frozenset = frozenset()   # FAST's defects DEEP does not have
    broken: frozenset = frozenset()  # DEEP's defects FAST does not have
    judge_dependent: bool = False


def compare_turn(fast: TurnOutcome | None, deep: TurnOutcome | None) -> Comparison:
    """One corresponding pair of runs, one turn (amendment 2).

    DEEP+: DEEP removes a substance or explanation defect and adds no
    substance defect. DEEP-worse is the mirror image. A pair that meets
    both -- an explanation defect traded for another, nothing in substance
    either way -- is "mixed" and counts toward neither. Differences only in
    form are "form-only"; pipeline defects are not a model difference.
    """
    if fast is None or deep is None or not fast.comparable or not deep.comparable:
        return Comparison("excluded")
    fixed = frozenset(d for d in fast.defects - deep.defects if tier(d) != "pipeline")
    broken = frozenset(d for d in deep.defects - fast.defects if tier(d) != "pipeline")
    tiers_fixed = {tier(d) for d in fixed}
    tiers_broken = {tier(d) for d in broken}
    better = bool(tiers_fixed & {"substance", "explanation"}) and "substance" not in tiers_broken
    worse = bool(tiers_broken & {"substance", "explanation"}) and "substance" not in tiers_fixed
    if better and worse:
        return Comparison("mixed", fixed, broken)
    if better:
        evidence = {d for d in fixed if tier(d) in ("substance", "explanation")}
        return Comparison("deep+", fixed, broken, judge_dependent=evidence <= BELOW_BAR)
    if worse:
        evidence = {d for d in broken if tier(d) in ("substance", "explanation")}
        return Comparison("deep-worse", fixed, broken, judge_dependent=evidence <= BELOW_BAR)
    if not fixed and not broken:
        return Comparison("same")
    return Comparison("form-only", fixed, broken)


@dataclass
class TurnLabel:
    key: tuple
    label: str                    # stable DEEP+, pending DEEP+, stable DEEP-worse, UNSTABLE
    comparisons: list = field(default_factory=list)
    category: str = ""
    first: bool = True


def label_turn(comparisons: list[Comparison], confirmed: set[int] | None = None) -> str:
    """Stable DEEP+ needs 2 of the corresponding comparisons (amendment 2).

    A judge-dependent DEEP+ comparison counts only once a person's blind
    labels confirm it (``confirmed`` holds the run numbers, 1-based). Until
    then a turn that would reach 2 with them is "pending DEEP+".
    """
    confirmed = confirmed or set()
    need = 2
    independent = sum(1 for c in comparisons if c.kind == "deep+" and not c.judge_dependent)
    human = sum(1 for number, c in enumerate(comparisons, 1)
                if c.kind == "deep+" and c.judge_dependent and number in confirmed)
    everything = sum(1 for c in comparisons if c.kind == "deep+")
    worse = sum(1 for c in comparisons if c.kind == "deep-worse")
    if independent + human >= need:
        return "stable DEEP+"
    if everything >= need:
        return "pending DEEP+"
    if worse >= need:
        return "stable DEEP-worse"
    return UNSTABLE


def label_turns(fast_runs: list[dict], deep_runs: list[dict], human: dict | None = None) -> dict:
    """Every scored turn's label, from corresponding runs (FAST i with DEEP i)."""
    index = corpus_index()
    human = human or {}
    keys = set()
    for loaded in fast_runs + deep_runs:
        keys |= set(loaded["turns"])
    labels = {}
    for key in sorted(keys):
        if key not in index:
            continue
        comparisons = [
            compare_turn(fast["turns"].get(key), deep["turns"].get(key))
            for fast, deep in zip(fast_runs, deep_runs)
        ]
        scenario, cat = index[key]
        labels[key] = TurnLabel(
            key=key,
            label=label_turn(comparisons, human.get(key)),
            comparisons=comparisons,
            category=cat,
            first=key[1] == 0,
        )
    return labels


def replicate_flips(runs: list[dict]) -> tuple[int, int]:
    """Turns whose pass/fail differs between any two runs of one arm."""
    keys = set.intersection(*(set(r["turns"]) for r in runs)) if runs else set()
    flips = sum(1 for k in keys if len({r["turns"][k].passed for r in runs}) > 1)
    return flips, len(keys)


# --------------------------------------------------------------- routing


def no_difference(label: "TurnLabel") -> bool:
    """For the report only: an unlabelled turn where FAST and DEEP were
    materially the same (same or form-only) in at least 2 comparisons. It
    stays without routing ground truth; this says why."""
    return sum(1 for c in label.comparisons if c.kind in ("same", "form-only")) >= 2


POLICIES = {
    "all FAST": lambda route: False,
    "all DEEP": lambda route: True,
    "P1 budget explain|elaborate": lambda route: route.get("budget") in ("explain", "elaborate"),
    "P2 budget elaborate": lambda route: route.get("budget") == "elaborate",
    "P3 shape explain|example|elaborate":
        lambda route: route.get("shape") in ("explain", "example", "elaborate"),
}
ORACLE = "perfect knowledge"


def route_policy(name: str, fast_turn: TurnOutcome, label: TurnLabel | None) -> bool:
    if name == ORACLE:
        return label is not None and label.label == "stable DEEP+"
    return POLICIES[name](fast_turn.route)


def score_policy(name: str, fast: dict, deep: dict, labels: dict, swap: tuple[float, float]) -> dict:
    """One policy on one corresponding pair of runs (amendment 3)."""
    deep_plus = {k for k, v in labels.items() if v.label == "stable DEEP+"}
    worse = {k for k, v in labels.items() if v.label == "stable DEEP-worse"}
    keys = sorted(k for k in labels if k in fast["turns"] and k in deep["turns"])
    sent = {k for k in keys if route_policy(name, fast["turns"][k], labels.get(k))}
    chosen = {k: (deep if k in sent else fast)["turns"][k] for k in keys}
    by_scenario: dict[str, bool] = {}
    for key, turn in chosen.items():
        by_scenario[key[0]] = by_scenario.get(key[0], True) and turn.passed
    correctness = [sum(t.correctness[0] for t in chosen.values()),
                   sum(t.correctness[1] for t in chosen.values())]
    # Latency: a DEEP turn is routed by the 8B (FAST's route time), answered
    # by the 27B (DEEP's turn time less its own route time), and pays the
    # 27B's load; the turn after it pays the 8B's reload (§6).
    load_27, load_8 = swap
    seconds, previous_deep = [], {}
    for key in keys:
        f, d = fast["turns"][key], deep["turns"][key]
        if f.seconds is None or d.seconds is None:
            continue
        after_deep = previous_deep.get(key[0], False)
        if key in sent:
            value = f.route_seconds + max(d.seconds - d.route_seconds, 0.0) + load_27
        else:
            value = f.seconds
        seconds.append(value + (load_8 if after_deep else 0.0))
        previous_deep[key[0]] = key in sent
    return {
        "recall": (len(sent & deep_plus), len(deep_plus)),
        "precision": (len(sent & deep_plus), len(sent)),
        "utilization": (len(sent), len(keys)),
        "worse_sent": (len(sent & worse), len(worse)),
        "turns_passing": (sum(t.passed for t in chosen.values()), len(chosen)),
        "scenarios_passing": (sum(by_scenario.values()), len(by_scenario)),
        "correctness": tuple(correctness),
        "p50": median(seconds) if seconds else 0.0,
        "p95": compare._percentile(seconds, 0.95),
        "sent": sent,
    }


def decision_stability(name: str, fast_runs: list[dict], labels: dict) -> tuple[int, int]:
    """First turns (identical input) whose decision changes between FAST runs."""
    keys = [k for k in labels if k[1] == 0 and all(k in r["turns"] for r in fast_runs)]
    changed = sum(
        1 for k in keys
        if len({route_policy(name, r["turns"][k], labels.get(k)) for r in fast_runs}) > 1
    )
    return changed, len(keys)


def router_agreement(fast_runs: list[dict], deep_runs: list[dict]) -> dict:
    """How often the two routers read a turn the same way (§3.4)."""
    counts = Counter()
    for fast, deep in zip(fast_runs, deep_runs):
        for key, f in fast["turns"].items():
            d = deep["turns"].get(key)
            if d is None:
                continue
            where = "first" if f.first else "later"
            counts[(where, "turns")] += 1
            counts[(where, "intent same")] += f.route.get("intent") == d.route.get("intent")
            counts[(where, "budget+shape same")] += (
                (f.route.get("budget"), f.route.get("shape"))
                == (d.route.get("budget"), d.route.get("shape")))
    return dict(counts)


# ------------------------------------------------------------ human labels


def export_blind(labels: dict, fast_runs: list[dict], deep_runs: list[dict], out: Path,
                 seed: int = 20260929) -> int:
    """Judge-dependent DEEP+ comparisons, for blind labelling (amendment 2).

    Both replies of each such comparison, shuffled, in evals/calibration.py's
    format, labelled on the properties the comparison hinges on. The
    readable copy beside it shows no arm, no run and no verdict.
    """
    rubric = corpus.rubric()
    items = []
    for key, label in labels.items():
        for number, comparison in enumerate(label.comparisons, 1):
            if comparison.kind != "deep+" or not comparison.judge_dependent:
                continue
            hinge = sorted(d for d in comparison.fixed if tier(d) in ("substance", "explanation"))
            for loaded in (fast_runs[number - 1], deep_runs[number - 1]):
                result = next(r for r in loaded["run"]["results"] if r["scenario"] == key[0])
                history, stored = [], None
                for turn in result["turns"]:
                    if turn["index"] == key[1]:
                        stored = turn
                        break
                    history.append({"person": turn["said"], "elaina": turn["display"] or ""})
                if stored is None:
                    continue
                items.append({
                    "run": str(loaded["path"]), "scenario": key[0], "suite": result["suite"],
                    "index": key[1], "conversation": history, "person": stored["said"],
                    "elaina": stored["display"] or "",
                    "labels": {name: "" for name in hinge},
                    "definitions": {name: rubric[name]["definition"] for name in hinge},
                    "allowed": {name: ["yes", "no"] for name in hinge},
                    "comparison": number,
                })
    random.Random(seed).shuffle(items)
    for number, item in enumerate(items, 1):
        item["item"] = number
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "rubric_version": corpus.rubric_version(),
        "rubric_fingerprint": corpus.rubric_fingerprint(),
        "instructions": ("Label each reply on its own, yes or no, by the definition given. "
                         "Which configuration wrote it is hidden on purpose."),
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# Blind labels: {len(items)} replies", ""]
    for item in items:
        lines += [f"## Item {item['item']}", ""]
        for said in item["conversation"]:
            lines += [f"> **Person:** {said['person']}", ">", f"> **Elaina:** {said['elaina']}", ""]
        lines += [f"**Person:** {item['person']}", "", f"**Elaina:** {item['elaina']}", ""]
        for name in item["labels"]:
            lines += [f"- `{name}`: {item['definitions'][name]}"]
        lines.append("")
    out.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    return len(items)


def read_human_labels(path: Path = HUMAN_LABELS) -> dict:
    """{(scenario, index): {confirmed run numbers}} from filled-in blind labels.

    A comparison is confirmed when the person labelled the FAST reply "no"
    and the DEEP reply "yes" on every property it hinges on.
    """
    if not path.exists():
        return {}
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    by_comparison = defaultdict(dict)
    for item in items:
        arm = "deep" if DEEP_ARM in item["run"] else "fast"
        by_comparison[(item["scenario"], item["index"], item["comparison"])][arm] = item["labels"]
    confirmed = defaultdict(set)
    for (scenario, index, number), arms in by_comparison.items():
        fast, deep = arms.get("fast", {}), arms.get("deep", {})
        if fast and deep and all(fast.get(n) == "no" and deep.get(n) == "yes" for n in deep):
            confirmed[(scenario, index)].add(number)
    return dict(confirmed)


# ------------------------------------------------------------ freezing


FROZEN_FILES = ("evals/fastdeep_analysis.py", "evals/checks.py", "evals/report.py",
                "evals/compare.py", "evals/compare3.py", "evals/corpus.py")


def fingerprint() -> str:
    """This analysis, the evaluator it scores with, the corpus and the rubric."""
    digest = hashlib.sha256()
    for name in FROZEN_FILES:
        digest.update((PROJECT_ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    digest.update(corpus.rubric_fingerprint().encode())
    scenarios = [(s.id, s.suite, [(t.say, t.score, t.mode, json.dumps(t.checks, sort_keys=True))
                                  for t in s.turns]) for s in corpus.load(corpus.ALL_SUITES)]
    digest.update(json.dumps(scenarios, ensure_ascii=False, sort_keys=True).encode())
    return digest.hexdigest()[:16]


def frozen_state() -> tuple[bool, str, str]:
    """(still frozen, the frozen fingerprint, today's)."""
    now = fingerprint()
    if not FROZEN.exists():
        return False, "", now
    recorded = json.loads(FROZEN.read_text(encoding="utf-8")).get("fingerprint", "")
    return recorded == now, recorded, now


def freeze() -> str:
    ROOT.mkdir(parents=True, exist_ok=True)
    value = fingerprint()
    FROZEN.write_text(json.dumps({
        "fingerprint": value, "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "files": list(FROZEN_FILES), "rubric_fingerprint": corpus.rubric_fingerprint(),
        "judge": DEFAULT_JUDGE,
    }, indent=1), encoding="utf-8")
    return value


# ------------------------------------------------------------- history

# The runs the historical columns read (§7), and what the documents of the
# day reported for them, to reproduce.
HISTORY = {
    "Phase 3": (EVALS / "p3", {"fast": ("p3-8b-soft", "f3-8b"), "deep": ("p3-27b-nosoft", "f3-27b")}),
    "final": (EVALS / "p3f", {"fast": ("p3-8b-soft", "f3-8b"), "deep": ("p3-27b-nosoft", "f3-27b")}),
}
MAIN_SUITES = tuple(s for s in corpus.SUITES)
# (scenarios, turns, correctness) as the documents reported them: mean (min-max) %.
REPORTED = {
    # docs/PHASE3_RESULTS.md, 2026-09-27: the evaluator of that day.
    ("Phase 3", "recorded", "p3-8b-soft"): ("31% (30%–31%)", "34% (34%–34%)", "95% (93%–96%)"),
    ("Phase 3", "recorded", "p3-8b-nosoft"): ("18% (17%–19%)", "20% (20%–21%)", "95% (93%–96%)"),
    ("Phase 3", "recorded", "p3-27b-soft"): ("47% (46%–48%)", "51% (49%–52%)", "95% (93%–96%)"),
    ("Phase 3", "recorded", "p3-27b-nosoft"): ("51% (50%–52%)", "54% (52%–56%)", "95% (93%–96%)"),
    ("Phase 3", "recorded", "p3-8bq8-soft"): ("31%", "33%", "100%"),
    # docs/PHASE3_FINAL_RESULTS.md as first generated (16:50, before the check fixes).
    ("final", "recorded", "p3-8b-soft"): ("32% (31%–33%)", "34% (33%–36%)", "96% (96%–96%)"),
    ("final", "recorded", "p3-8b-nosoft"): ("19% (17%–22%)", "21% (20%–23%)", "96% (96%–96%)"),
    ("final", "recorded", "p3-27b-soft"): ("50% (50%–50%)", "55% (54%–56%)", "93% (93%–93%)"),
    ("final", "recorded", "p3-27b-nosoft"): ("45% (44%–46%)", "49% (48%–51%)", "96% (96%–96%)"),
    ("final", "recorded", "p3-8bq8-soft"): ("31%", "38%", "93%"),
    # docs/PHASE3_FINAL_RESULTS.md regenerated with the corrected checks.
    ("Phase 3", "current", "p3-8b-soft"): ("31% (30%–33%)", "35% (34%–36%)", "96% (96%–96%)"),
    ("Phase 3", "current", "p3-27b-soft"): ("51% (50%–52%)", "54% (52%–56%)", "100% (100%–100%)"),
    ("Phase 3", "current", "p3-27b-nosoft"): ("54% (54%–54%)", "57% (54%–59%)", "100% (100%–100%)"),
    ("final", "current", "p3-8b-soft"): ("32% (31%–33%)", "34% (33%–36%)", "96% (96%–96%)"),
    ("final", "current", "p3-27b-soft"): ("54% (54%–54%)", "58% (57%–59%)", "100% (100%–100%)"),
    ("final", "current", "p3-27b-nosoft"): ("49% (48%–50%)", "52% (51%–54%)", "100% (100%–100%)"),
    ("final", "current", "p3-8bq8-soft"): ("33%", "39%", "96%"),
}


def _runs_of(root: Path, arm: str) -> list[Path]:
    return sorted(p for p in root.glob(f"{arm}-*") if (p / "results.jsonl").exists())


def reproduce() -> tuple[str, bool]:
    """Recompute the historical numbers with this analysis (§7, amendment 5)."""
    lines, ok = [], True
    add = lines.append
    add("## 1. The analysis against report.measure, run by run")
    add("")
    add("| run | checks | scenarios | turns | correctness | properties | same |")
    add("|---|---|---|---|---|---|---|")
    for phase, (root, _) in HISTORY.items():
        for arm in ("p3-8b-soft", "p3-8b-nosoft", "p3-27b-soft", "p3-27b-nosoft",
                    "p3-8bq8-soft", "f3-8b", "f3-27b"):
            for path in _runs_of(root, arm):
                ours = measures(load_run(path))
                theirs = report.measure(report.load_run(path))
                same = (ours["scenarios"] == (theirs["scenarios_passed"], theirs["scenarios"])
                        and ours["turns"] == compare.turn_passes(report.load_run(path))
                        and ours["correctness"] == tuple(theirs["correctness"])
                        and ours["properties"] == dict(theirs["properties"]))
                ok &= same
                add(f"| {phase} `{path.name}` | current | {ours['scenarios'][0]}/{ours['scenarios'][1]} "
                    f"| {ours['turns'][0]}/{ours['turns'][1]} | {ours['correctness'][0]}/{ours['correctness'][1]} "
                    f"| {len(ours['properties'])} | {'yes' if same else '**NO**'} |")
    add("")
    add("## 2. The reported numbers, reproduced")
    add("")
    add("| phase | evaluator | arm | reported (scenarios / turns / correct) | reproduced | match |")
    add("|---|---|---|---|---|---|")
    for (phase, mode, arm), wanted in REPORTED.items():
        root = HISTORY[phase][0]
        runs = [measures(load_run(p, recorded_checks=(mode == "recorded"))) for p in _runs_of(root, arm)]
        got = (compare._cell([r["scenarios"] for r in runs]),
               compare._cell([r["turns"] for r in runs]),
               compare._cell([r["correctness"] for r in runs]))
        match = got == wanted
        ok &= match
        add(f"| {phase} | {mode} | `{arm}` | {' / '.join(wanted)} | {' / '.join(got)} "
            f"| {'yes' if match else '**NO**'} |")
    add("")
    add("## 3. The protocol's method check (§9), with the frozen labels")
    add("")
    root = HISTORY["final"][0]
    fast = [load_run(p) for p in _runs_of(root, "p3-8b-soft")]
    deep = [load_run(p) for p in _runs_of(root, "p3-27b-nosoft")]
    for number in (1, 2):
        fast[number - 1]["turns"].update(load_run(root / f"f3-8b-{number}")["turns"])
        deep[number - 1]["turns"].update(load_run(root / f"f3-27b-{number}")["turns"])
    add(f"- Replicate pass/fail flips: FAST {replicate_flips(fast)[0]} of {replicate_flips(fast)[1]}, "
        f"DEEP {replicate_flips(deep)[0]} of {replicate_flips(deep)[1]} (§9 said 9 and 15 of 73).")
    labels = label_turns(fast, deep)
    counts = Counter(v.label for v in labels.values())
    add(f"- Labels with 2 runs (2 of 2 needed): {dict(sorted(counts.items()))}.")
    add("  §9 used the same rules without the \"mixed\" case and without blind-label"
        " gating: 15 DEEP+, 4 DEEP-worse.")
    per = Counter(c.kind for v in labels.values() for c in v.comparisons)
    add(f"- Comparisons by kind: {dict(sorted(per.items()))}.")
    add("")
    return "\n".join(lines) + "\n", ok


# -------------------------------------------------------------- report

PROPERTIES = ("answers_the_ask", "takes_the_turn_as_meant", "leads_with_the_answer",
              "concrete_first", "says_what_it_solves", "terms_explained", "adapts_to_confusion",
              "only_what_helps", "no_service_phrasing", "notation_earned", "no_unrelated_remark",
              "accurate")


def _pct(pair) -> str:
    return f"{pair[0] / pair[1]:.0%} ({pair[0]}/{pair[1]})" if pair[1] else "-"


def _mean_pct(pairs) -> str:
    return compare._cell([p for p in pairs if p[1]])


def normal_speeds(root: Path) -> dict[str, float]:
    """Each model's normal generation speed this session: its drift arm."""
    speeds = {}
    for arm, model in (("pd-8b", "qwen3:8b"),
                       ("pd-27b", "hf.co/jrell/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller:latest")):
        for path in _runs_of(root, arm):
            tps = compare.latency(report.load_run(path))["answer_tps"]
            if tps:
                speeds[model] = tps
    return speeds


def write_report(root: Path = ROOT) -> str:
    frozen, recorded, now = frozen_state()
    fast_paths, deep_paths = _runs_of(root, FAST_ARM), _runs_of(root, DEEP_ARM)
    fast = [load_run(p) for p in fast_paths]
    deep = [load_run(p) for p in deep_paths]
    speeds = normal_speeds(root)
    human = read_human_labels()
    labels = label_turns(fast, deep, human)
    lines: list[str] = []
    add = lines.append
    add("# FAST/DEEP baseline: end-to-end FAST vs DEEP capability")
    add("")
    add(f"*Generated by `evals/fastdeep_analysis.py` on {datetime.now():%Y-%m-%d %H:%M}. "
        f"Analysis fingerprint `{now}`; frozen fingerprint `{recorded or 'none'}` -- "
        + ("**frozen analysis**" if frozen else "**NOT the frozen analysis**")
        + f". Runs in `{root.relative_to(PROJECT_ROOT)}`; judge `{DEFAULT_JUDGE}`, rubric "
        f"v{corpus.rubric_version()} (`{corpus.rubric_fingerprint()}`). Protocol: "
        "docs/FAST_DEEP_PROTOCOL.md.*")
    add("")
    add("FAST is the 8B routing and answering with soft stages on; DEEP is the 27B routing "
        "and answering with soft stages off. The comparison is **end to end**: a DEEP "
        "win may come from the 27B's reading of the turn as much as from its answer "
        "(§3 below), and is not attributed to the 27B's generation alone.")
    add("")

    # ---- 1. the arms
    add("## 1. The arms, end to end")
    add("")
    for title, suites in (("All six suites", None), ("The five historical suites", MAIN_SUITES),
                          ("The follow-up suite", ("followups",))):
        add(f"### {title}")
        add("")
        add("| arm | runs | scenarios | turns | correctness | " + " | ".join(f"`{p}`" for p in PROPERTIES) + " |")
        add("|---|---|---|---|---|" + "---|" * len(PROPERTIES))
        for name, runs in ((FAST_ARM, fast), (DEEP_ARM, deep)):
            m = [measures(r, suites) for r in runs]
            add(f"| `{name}` | {len(runs)} | {_mean_pct([x['scenarios'] for x in m])} "
                f"| {_mean_pct([x['turns'] for x in m])} | {_mean_pct([x['correctness'] for x in m])} | "
                + " | ".join(_mean_pct([x["properties"].get(p, (0, 0)) for x in m]) for p in PROPERTIES)
                + " |")
        add("")

    # ---- 2. latency and resources
    add("## 2. Latency and resources")
    add("")
    add(f"Normal speed this session (drift arms): "
        + (", ".join(f"{m.split('/')[-1]} {s:.1f} tok/s" for m, s in speeds.items()) or "not measured")
        + ". A call below 60% of it is pressure-affected (§6).")
    add("")
    add("| arm | turn p50 / p95 s (all) | p50 / p95 s (without pressure) | router first call p50 / p95 s "
        "| router fallback | truncations | answer tok/s | model loads s | VRAM max | GPU/CPU | pressure-affected calls |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    arm_runs = [(FAST_ARM, fast), (DEEP_ARM, deep)]
    for extra in ("pv-8b", "pv-27b", "pd-8b", "pd-27b"):
        paths = _runs_of(root, extra)
        if paths:
            arm_runs.append((extra, [load_run(p) for p in paths]))
    for name, runs in arm_runs:
        for number, loaded in enumerate(runs, 1):
            r = resources(loaded, speeds)
            router = r["router"]
            flagged, calls = r["pressure_calls"]
            add(f"| `{name}-{number}` | {r['p50']:.1f} / {r['p95']:.1f} | {r['p50_clean']:.1f} / {r['p95_clean']:.1f} "
                f"| {router['first_p50']:.1f} / {router['first_p95']:.1f} | {router['fallback']}/{router['routed']} "
                f"| {router['truncated']} | {r['answer_tps']:.1f} | {', '.join(map(str, r['loads'][:4])) or '-'} "
                f"| {r['vram_max'] / 1024:.1f} GB | {'; '.join(r['processors']) or '-'} "
                f"| {flagged}/{calls}{' **contaminated**' if r['contaminated'] else ''} |")
    add("")
    tenants = root / "gpu.log"
    add(f"Other GPU consumers at the start and end of every arm: `{tenants.relative_to(PROJECT_ROOT)}`.")
    add("")

    # ---- 3. the router confound
    add("## 3. The router confound")
    add("")
    agreement = router_agreement(fast, deep)
    for where in ("first", "later"):
        total = agreement.get((where, "turns"), 0)
        if total:
            add(f"- {where.capitalize()} turns: intent the same {agreement[(where, 'intent same')]}/{total}, "
                f"budget and shape the same {agreement[(where, 'budget+shape same')]}/{total} "
                "(all corresponding run pairs).")
    add("- Before this baseline, on the final rerun: the routers disagreed on intent in 11 of "
        "28 later turns (3 of 45 first turns).")
    add("")

    # ---- 4. labels
    add("## 4. Where DEEP is materially better: stable labels")
    add("")
    add("Per turn, FAST run *i* against DEEP run *i*. **Stable DEEP+**: DEEP removes a substance "
        "or explanation defect and adds no substance defect in at least 2 of 3 comparisons; a "
        "comparison resting only on `concrete_first` or `adapts_to_confusion` counts once a "
        "person's blind labels confirm it (**pending** until then). **Stable DEEP-worse**: the "
        "mirror image, in at least 2 of 3. Everything else is **unstable / no routing ground "
        "truth**. Form-only differences never make DEEP+.")
    add("")
    counts = Counter(v.label for v in labels.values())
    add("| label | all | first turns | later turns |")
    add("|---|---|---|---|")
    for name in ("stable DEEP+", "pending DEEP+", "stable DEEP-worse", UNSTABLE):
        add(f"| {name} | {counts.get(name, 0)} "
            f"| {sum(1 for v in labels.values() if v.label == name and v.first)} "
            f"| {sum(1 for v in labels.values() if v.label == name and not v.first)} |")
    add("")
    unlabelled = [v for v in labels.values() if v.label == UNSTABLE]
    add(f"Of the {len(unlabelled)} turns without routing ground truth, "
        f"{sum(no_difference(v) for v in unlabelled)} were materially the same in at least 2 "
        "comparisons (same or form-only) and the rest were inconsistent. This is information "
        "only; neither group is labelled FAST or DEEP.")
    add("")
    kinds = Counter(c.kind for v in labels.values() for c in v.comparisons)
    add(f"Comparisons by kind: {', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))}.")
    add("")
    add(f"Replicate pass/fail flips: FAST {replicate_flips(fast)[0]}/{replicate_flips(fast)[1]}, "
        f"DEEP {replicate_flips(deep)[0]}/{replicate_flips(deep)[1]}.")
    add("")
    add("### By fixed category (hypothesis groups)")
    add("")
    add("| category | turns | stable DEEP+ | pending | stable DEEP-worse | no ground truth |")
    add("|---|---|---|---|---|---|")
    for cat, about in CATEGORIES.items():
        group = [v for v in labels.values() if v.category == cat]
        if not group:
            continue
        c = Counter(v.label for v in group)
        add(f"| {cat} {about} | {len(group)} | {c.get('stable DEEP+', 0)} | {c.get('pending DEEP+', 0)} "
            f"| {c.get('stable DEEP-worse', 0)} | {c.get(UNSTABLE, 0)} |")
    add("")
    add("### Every stable or pending DEEP+ and every stable DEEP-worse turn")
    add("")
    add("| turn | category | label | comparisons (kind: what changed) |")
    add("|---|---|---|---|")
    for key, v in sorted(labels.items(), key=lambda kv: (kv[1].label, kv[0])):
        if v.label == UNSTABLE:
            continue
        described = "; ".join(
            f"{c.kind}{' (judge-dependent)' if c.judge_dependent else ''}"
            + (f": +{','.join(sorted(c.fixed))}" if c.fixed else "")
            + (f" -{','.join(sorted(c.broken))}" if c.broken else "")
            for c in v.comparisons)
        add(f"| `{key[0]}` t{key[1]} | {v.category} | {v.label} | {described} |")
    add("")

    # ---- 5. routing
    add("## 5. Routing, scored against the stable labels")
    add("")
    loads_27 = [x for r in deep for x in resources(r, speeds)["loads"]]
    loads_8 = [x for r in fast for x in resources(r, speeds)["loads"]]
    swap = (median(loads_27) if loads_27 else 0.0, median(loads_8) if loads_8 else 0.0)
    add(f"Routing signal: the 8B router's recorded reading in each FAST run. Latency charges a "
        f"DEEP turn the 8B's route time, the 27B's answer time and a 27B load of {swap[0]:.1f} s, "
        f"and the turn after it an 8B reload of {swap[1]:.1f} s (this session's median cold "
        "loads; Stage B measures real swaps). Without swaps is the lower bound.")
    add("")
    add("| policy | DEEP+ recall | DEEP precision | DEEP utilization | DEEP-worse routed | turns passing "
        "| scenarios passing | correctness | p50 / p95 s (with swaps) | p50 / p95 s (no swaps) | first-turn decision flips |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for name in (*POLICIES, ORACLE):
        scored = [score_policy(name, f, d, labels, swap) for f, d in zip(fast, deep)]
        free = [score_policy(name, f, d, labels, (0.0, 0.0)) for f, d in zip(fast, deep)]
        flips = decision_stability(name, fast, labels) if name != ORACLE else (0, 0)
        add(f"| {name} | {_mean_pct([s['recall'] for s in scored])} | {_mean_pct([s['precision'] for s in scored])} "
            f"| {_mean_pct([s['utilization'] for s in scored])} | {_mean_pct([s['worse_sent'] for s in scored])} "
            f"| {_mean_pct([s['turns_passing'] for s in scored])} | {_mean_pct([s['scenarios_passing'] for s in scored])} "
            f"| {_mean_pct([s['correctness'] for s in scored])} "
            f"| {mean(s['p50'] for s in scored):.1f} / {mean(s['p95'] for s in scored):.1f} "
            f"| {mean(s['p50'] for s in free):.1f} / {mean(s['p95'] for s in free):.1f} "
            f"| {flips[0]}/{flips[1]} |")
    add("")
    add("Recall and precision count only stable DEEP+ (confirmed) turns as DEEP+; pending and "
        "unstable turns sent to DEEP count against precision. Combined quality takes each turn "
        "from the arm the policy picks, in corresponding runs: exact for first turns, "
        "approximate for later ones (each arm followed its own earlier replies). Latency here is "
        "over scored turns.")
    add("")

    # ---- 6. historical comparability
    add("## 6. Historical comparability")
    add("")
    add("Evaluator-only change: the same replies and verdicts, scored by the evaluator of the day "
        "against today's. Generation change: today's evaluator, new replies.")
    add("")
    add("| arm | column | scenarios | turns | correctness |")
    add("|---|---|---|---|---|")
    for side, name, runs in (("fast", FAST_ARM, fast), ("deep", DEEP_ARM, deep)):
        for phase, recorded_mode, column in (("Phase 3", True, "Phase 3 as reported"),
                                             ("Phase 3", False, "Phase 3, today's evaluator"),
                                             ("final", True, "final rerun as reported"),
                                             ("final", False, "final rerun, today's evaluator")):
            root_then, arms = HISTORY[phase]
            arm = arms[side][0]
            m = [measures(load_run(p, recorded_checks=recorded_mode)) for p in _runs_of(root_then, arm)]
            add(f"| `{arm}` | {column} | {_mean_pct([x['scenarios'] for x in m])} "
                f"| {_mean_pct([x['turns'] for x in m])} | {_mean_pct([x['correctness'] for x in m])} |")
        m = [measures(r, MAIN_SUITES) for r in runs]
        add(f"| `{name}` | **post-fix baseline** (five historical suites) | {_mean_pct([x['scenarios'] for x in m])} "
            f"| {_mean_pct([x['turns'] for x in m])} | {_mean_pct([x['correctness'] for x in m])} |")
    add("")

    # ---- 7. limits
    add("## 7. What this corpus cannot say")
    add("")
    add("The corpus has no turns for multi-step reasoning, planning, coding tasks or synthesis "
        "across sources, and none for routine actions. Nothing above extrapolates to them; "
        "Stage B's supplementary suite is where they would be measured.")
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reproduce", action="store_true")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--fingerprint", action="store_true")
    parser.add_argument("--export-blind", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if args.fingerprint:
        frozen, recorded, now = frozen_state()
        print(f"fingerprint {now}; frozen {recorded or 'none'}; {'frozen' if frozen else 'NOT frozen'}")
        return 0
    if args.reproduce:
        text, ok = reproduce()
        print(text)
        print("REPRODUCED" if ok else "NOT REPRODUCED")
        return 0 if ok else 1
    if args.freeze:
        print(f"frozen at fingerprint {freeze()}")
        return 0
    if args.export_blind:
        fast = [load_run(p) for p in _runs_of(ROOT, FAST_ARM)]
        deep = [load_run(p) for p in _runs_of(ROOT, DEEP_ARM)]
        count = export_blind(label_turns(fast, deep), fast, deep, HUMAN_LABELS)
        print(f"Wrote {HUMAN_LABELS}: {count} replies to label blind")
        return 0
    text = write_report()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
