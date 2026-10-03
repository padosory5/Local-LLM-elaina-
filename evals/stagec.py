"""Stage C: the explanation-contract experiment, held out and frozen.

    .venv/Scripts/python.exe -m evals.stagec --review         # what --freeze would record
    .venv/Scripts/python.exe -m evals.stagec --freeze         # only once the user approves
    .venv/Scripts/python.exe -m evals.stagec --verify
    .venv/Scripts/python.exe -m evals.stagec --run            # D's plans, 4 arms x 3 runs, judge
    .venv/Scripts/python.exe -m evals.stagec --export-blind   # the concrete_first sample
    .venv/Scripts/python.exe -m evals.stagec --report docs/STAGE_C_RESULTS.md

docs/STAGE_C_PROTOCOL.md is the protocol; this file is its executable half.
Both are fingerprinted at the freeze with the corpus, the arms' exact texts
(brain/explain_contract.py), the code, the models and the runtime, so
nothing the decision rests on can move once a result has been seen.

**Arms**, all on qwen3:8b with production's soft stages
(brain/explain_contract.py):

* A -- production FAST, unchanged;
* B -- one explanation contract in place of the conflicting order and
  length rules, no extra call;
* C -- B plus a small plan from one 8B call before generation;
* D -- B plus the same plan written beforehand by the 27B (the ceiling).

**Turns.** The decision reads only the held-out suite (``explain_heldout``,
30 scored turns). The 21 Stage C diagnosis turns run beside it from the
frozen corpus v2 and are reported apart; they decide nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Silent before anything starts: the runs and the judge are child processes
# that inherit this, and a backend that spoke would spend the paid voice on
# every turn. evals/run.py forces it off again.
os.environ.setdefault("ELAINA_TTS", "off")

from brain import explain_contract as ec  # noqa: E402
from evals import compare, compare3, corpus, fastdeep, phase2  # noqa: E402
from evals import fastdeep_analysis as fd  # noqa: E402
from evals import fastdeep_v2 as v2  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402
from evals.phase2 import Arm, M8, M27, NO_STT  # noqa: E402

ROOT = fd.EVALS / "pc"
FROZEN = ROOT / "FROZEN.json"
BLIND = ROOT / "blind.json"
BLIND_KEY = ROOT / "blind_key.json"
PLANS_27B = ROOT / "plans_27b.json"
SUITE = "explain_heldout"
STAGED = PROJECT_ROOT / "evals" / "scenarios_next" / f"{SUITE}.json"
PLACED = corpus.SCENARIOS / f"{SUITE}.json"
PROTOCOL = PROJECT_ROOT / "docs" / "STAGE_C_PROTOCOL.md"
PROTOCOL_VERSION = "C1.0"
RUNS = 3

# The 21 turns corpus v2 attributed to "neither model good enough"
# (docs/STAGE_C_DIAGNOSIS.md). Reference only: they decide nothing.
REFERENCE_SUITE = v2.SUITE
REFERENCE = (
    ("v2a_gps_watching_me", 0), ("v2a_ko_wifi_too_hard", 0), ("v2a_lift_halfway", 0),
    ("v2a_photosynthesis_plain_words", 0), ("v2a_tcp_handshake_hm", 0),
    ("v2b_etf_vs_mutual_fund", 1), ("v2b_garbage_collection_how_it_knows", 1),
    ("v2b_ko_battery_full_discharge", 0), ("v2b_ko_battery_full_discharge", 1),
    ("v2b_ko_jet_lag_east", 0), ("v2b_ko_jet_lag_east", 1), ("v2b_rainbow_red_outside", 0),
    ("v2c_heat_pump_thanks", 0), ("v2c_ko_black_hole_then_time", 0), ("v2c_sky_blue_remark", 0),
    ("v2d_entropy", 0), ("v2d_ko_placebo", 0), ("v2d_ko_supply_chain", 0),
    ("v2e_fridge_then_thanks", 0), ("v2e_ko_earthquake_then_thanks", 0),
    ("v2e_ko_magnet_then_date", 0),
)
REFERENCE_SCENARIOS = tuple(sorted({key[0] for key in REFERENCE}))

# ------------------------------------------------------------------ arms

_BASE = {**NO_STT, "ELAINA_DOMAINS": "act", "ELAINA_MODEL": M8}
ARMS = {
    "A": Arm("pc-a", "pipeline", "A: production FAST, unchanged",
             env=dict(_BASE), runs=RUNS, suites=(SUITE, REFERENCE_SUITE)),
    "B": Arm("pc-b", "pipeline", "B: one explanation contract, no extra call",
             env={**_BASE, ec.ARM_ENV: ec.CONTRACT}, runs=RUNS, suites=(SUITE, REFERENCE_SUITE)),
    "C": Arm("pc-c", "pipeline", "C: B plus a plan from one 8B call",
             env={**_BASE, ec.ARM_ENV: ec.PLAN}, runs=RUNS, suites=(SUITE, REFERENCE_SUITE)),
    "D": Arm("pc-d", "pipeline", "D: B plus a plan written beforehand by the 27B",
             env={**_BASE, ec.ARM_ENV: ec.PLAN_FILE, ec.PLANS_ENV: str(PLANS_27B)},
             runs=RUNS, suites=(SUITE, REFERENCE_SUITE)),
}
ORDER = ("A", "B", "C", "D")

# ------------------------------------------------------------- criteria

# Fixed before any arm runs (the user's criteria of 2026-10-01, unchanged).
CRITERIA = {
    "net_gain_min": 0.20,            # of the 30 held-out scored turns
    "substance_regressions_max": 1,  # turns, against A
    "leads_regressions_max": 2,      # turns, against A
    "blind_confirmation_min": 0.70,  # of the judge's concrete_first credits sampled
    "plan_latency_max_s": 1.5,       # C's median turn time over B's, on planned turns
    "unusable_plans_max": 0.05,      # of C's planned held-out turn-runs
    "comparable_min": 0.95,
}
BLIND_SAMPLE = {"credited": 20, "distractors": 8, "seed": 20261001}
CRITERIA_TEXT = (
    "Every decision reads the 30 held-out scored turns only; the 21 diagnosis turns are "
    "reported apart. A comparison X -> Y uses the frozen analysis (evals/fastdeep_analysis.py): "
    "run i against run i, a stable label at 2 of 3, the tiers. Net gain X -> Y = (turns stably "
    "better in Y - turns stably worse) / 30. "
    "Blind labels: a sample of up to 20 held-out replies the judge credited with concrete_first "
    "(from B, C and D) and 8 it did not (from any arm), shuffled, labelled by the person on the "
    "existing definition. Confirmation = person yes / judge yes over the credited ones. At 70% or "
    "more, a gain resting on concrete_first counts as stable; below it, the gain is rejected and "
    "such comparisons stay pending (the frozen rule). "
    "Regressions against A: a turn where, in at least 2 of 3 run pairs, the arm's reply has a "
    "substance defect A's does not (more than 1 such turn rejects the arm), or loses "
    "leads_with_the_answer (more than 2 rejects it). "
    "B produces the required improvement when its net gain over A is at least 20%, with no "
    "rejecting regression and the blind labels confirmed. "
    "C adds value when its net gain over B is at least 20%, with no rejecting regression, its "
    "median turn time on planned held-out turns at most 1.5 s over B's on the same turns, and at "
    "most 5% of its planned held-out turn-runs unusable. "
    "D passing that bar while C does not means the plan works but the 8B cannot write it. "
    "If B produces the required improvement and C does not add value, the result is B: C is not "
    "added because it exists. "
    "Valid only if every arm has three complete runs, every run has at least 95% of its scored "
    "turns comparable, the blind labels are in, and the freeze verifies.")


# ------------------------------------------------------------ the corpus


def corpus_file() -> Path:
    return PLACED if PLACED.exists() else STAGED


def raw_corpus() -> dict:
    return json.loads(corpus_file().read_text(encoding="utf-8"))


def heldout_keys(raw: dict | None = None) -> list[tuple[str, int]]:
    raw = raw or raw_corpus()
    return [(s["id"], index) for s in raw["scenarios"]
            for index, turn in enumerate(s["turns"]) if turn.get("score")]


def languages(raw: dict | None = None) -> dict[str, str]:
    raw = raw or raw_corpus()
    found = {s["id"]: s.get("language", "en") for s in raw["scenarios"]}
    reference = json.loads(v2.corpus_file().read_text(encoding="utf-8"))
    found.update({s["id"]: s.get("language", "en") for s in reference["scenarios"]})
    return found


@contextmanager
def held_out():
    """The held-out and the reference suites visible to the frozen v1 code,
    in this process only, as corpus v2 did (evals/fastdeep_v2.held_out)."""
    saved = corpus.ALL_SUITES
    corpus.ALL_SUITES = saved + tuple(s for s in (SUITE, REFERENCE_SUITE) if s not in saved)
    try:
        yield
    finally:
        corpus.ALL_SUITES = saved


def corpus_problems() -> list[str]:
    with held_out():
        others = corpus.load(tuple(s for s in corpus.ALL_SUITES if s != SUITE))
        return corpus.problems(others + corpus._load_suite(corpus_file()))


# ------------------------------------------------------------ the freeze

EVALUATOR_FILES = ("evals/stagec.py", "evals/fastdeep_v2.py", "evals/judge.py", "evals/run.py",
                   "evals/phase2.py", "evals/fastdeep.py", "evals/integrity.py")
ARM_FILES = ("brain/explain_contract.py", "brain/chat_engine.py", "brain/response_policy.py",
             "brain/conversation_style.py", "brain/response_budget.py",
             "brain/personality_en.txt", "brain/personality_ko.txt")


def arm_texts() -> dict:
    """The exact words each arm adds or takes away."""
    from brain.personality_loader import PersonalityLoader

    loader = PersonalityLoader()
    superseded = {lang: [loader.sections(lang)[section][index]
                         for section, index in ec.SUPERSEDED_PERSONA_RULES]
                  for lang in ("en", "ko")}
    return {
        "applies_to": "response budget 'explain', shape 'explain', the answer act; every other "
                      "turn is built as in production",
        "A": "production prompt, unchanged",
        "B": {"requirements": {lang: ec.requirements(lang, max_words=130) for lang in ("en", "ko")},
              "persona_rules_removed": superseded,
              "style_lines_removed": list(ec.SUPERSEDED_STYLE_LINES)},
        "C": {"as B, plus": {lang: ec.PLAN_TEXT[lang] for lang in ("en", "ko")},
              "planner_prompt": ec.PLANNER_PROMPT,
              "planner_call": "qwen3:8b, format json, temperature 0, num_predict 256, think off; "
                              "input: the person's questions so far, no assistant text",
              "unusable": "not JSON, not an object, no scene or answer, scene or answer longer "
                          "than 40 words, or terms not a list: the turn falls back to B"},
        "D": {"as C, plan from": f"{M27}, the same prompt and call, written once before the "
                                 f"runs into {PLANS_27B.relative_to(PROJECT_ROOT).as_posix()}"},
    }


def record() -> dict:
    raw = raw_corpus()
    keys = heldout_keys(raw)
    v1_frozen, v1_recorded, v1_now = fd.frozen_state()
    system, count = v2.system_fingerprint()
    return {
        "protocol": {"version": PROTOCOL_VERSION,
                     "document": PROTOCOL.relative_to(PROJECT_ROOT).as_posix(),
                     "sha256": v2._sha(PROTOCOL) if PROTOCOL.exists() else "MISSING"},
        "corpus": {"suite": SUITE, "file": PLACED.relative_to(PROJECT_ROOT).as_posix(),
                   "sha256": v2._sha(corpus_file()), "scenarios": len(raw["scenarios"]),
                   "scored_turns": len(keys),
                   "languages": dict(sorted(Counter(s["language"] for s in raw["scenarios"]).items())),
                   "two_turn": sum(1 for s in raw["scenarios"] if len(s["turns"]) == 2)},
        "reference": {"suite": REFERENCE_SUITE, "sha256": v2._sha(v2.corpus_file()),
                      "turns": [list(k) for k in REFERENCE]},
        "analysis": {"v1_fingerprint": v1_now, "v1_frozen_fingerprint": v1_recorded,
                     "v1_still_frozen": v1_frozen, "files": v2._files(fd.FROZEN_FILES)},
        "rubric": {"version": corpus.rubric_version(), "fingerprint": corpus.rubric_fingerprint()},
        "evaluator": {"judge": DEFAULT_JUDGE, "files": v2._files(EVALUATOR_FILES)},
        "arms": {name: {"dirs": f"{ARMS[name].name}-1..{RUNS}", "about": ARMS[name].about,
                        "env": ARMS[name].env} for name in ORDER},
        "arm_texts": arm_texts(),
        "arm_files": v2._files(ARM_FILES),
        "system_under_test": {"sha256": system, "files": count,
                              "git_head": v2._git("rev-parse", "--short", "HEAD")},
        "models": v2.models(),
        "runtime": v2.runtime(),
        "runs": RUNS,
        "order": "27B plans first; then per run number: " + ", ".join(ORDER) + "; then the judge",
        "criteria": CRITERIA,
        "blind_sample": BLIND_SAMPLE,
        "criteria_text": CRITERIA_TEXT,
    }


def verify() -> list[str]:
    if not FROZEN.exists():
        return ["not frozen: run --freeze once the corpus, contract and protocol are approved"]
    found = v2._differences(json.loads(FROZEN.read_text(encoding="utf-8")), record())
    if not PLACED.exists():
        found.append(f"{PLACED.relative_to(PROJECT_ROOT).as_posix()} is missing")
    return found


def freeze() -> dict:
    problems = corpus_problems()
    if problems:
        raise SystemExit("The corpus has problems:\n  " + "\n  ".join(problems))
    if not fd.frozen_state()[0]:
        raise SystemExit("The v1 analysis is not the frozen one; Stage C reuses it unchanged.")
    if not PROTOCOL.exists():
        raise SystemExit(f"{PROTOCOL} is missing")
    if not PLACED.exists():
        shutil.move(str(STAGED), str(PLACED))
    ROOT.mkdir(parents=True, exist_ok=True)
    found = record()
    found["frozen_at"] = datetime.now().isoformat(timespec="seconds")
    FROZEN.write_text(json.dumps(found, indent=1, ensure_ascii=False), encoding="utf-8")
    return found


# ------------------------------------------------------------- running


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


def _scenario_ids() -> list[str]:
    return [s["id"] for s in raw_corpus()["scenarios"]] + list(REFERENCE_SCENARIOS)


def generate(arm: Arm, out: Path) -> int:
    phase2.unload_models()
    command = [str(phase2.PYTHON), "-m", "evals.stagec", "--child", "run", "--out", str(out),
               "--suites", SUITE, REFERENCE_SUITE, "--only", ",".join(_scenario_ids()),
               "--env", *[f"{key}={value}" for key, value in arm.env.items()]]
    phase2.log(f"generate {out.name}: {arm.about}")
    started = datetime.now()
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    phase2.log(f"generate {out.name}: exit {result.returncode} after "
               f"{(datetime.now() - started).total_seconds():.0f}s")
    return result.returncode


def judge(outs: list[Path]) -> int:
    phase2.unload_models()
    phase2.log(f"judge {len(outs)} run(s) with {DEFAULT_JUDGE}")
    result = subprocess.run(
        [str(phase2.PYTHON), "-m", "evals.stagec", "--child", "judge", "--model", DEFAULT_JUDGE,
         "--run", *[str(out) for out in outs]], cwd=str(PROJECT_ROOT), check=False)
    phase2.log(f"judge: exit {result.returncode}")
    return result.returncode


def plan_chains() -> dict[str, str]:
    """Every chain of questions a turn of these scenarios can be asked after,
    with its language: what D's plans are written for."""
    chains = {}
    reference = {s["id"]: s for s in json.loads(v2.corpus_file().read_text(encoding="utf-8"))["scenarios"]}
    scenarios = list(raw_corpus()["scenarios"]) + [reference[i] for i in REFERENCE_SCENARIOS]
    for scenario in scenarios:
        said = []
        for turn in scenario["turns"]:
            said.append(turn["say"])
            chains[ec.plan_key(said)] = scenario.get("language", "en")
    return chains


def write_27b_plans() -> int:
    """D's plans: the arm C prompt and call, on the 27B, once, before any arm."""
    import ollama

    phase2.unload_models()
    plans = {}
    for key, language in plan_chains().items():
        response = ollama.chat(model=M27, messages=ec.planner_messages(key.split("\n"), language),
                               stream=False, format="json",
                               options={"temperature": 0, "num_predict": 256}, think=False)
        plans[key] = str((response.get("message") or {}).get("content") or "")
    PLANS_27B.write_text(json.dumps({"model": M27, "written_at": datetime.now().isoformat(
        timespec="seconds"), "plans": plans}, ensure_ascii=False, indent=1), encoding="utf-8")
    phase2.unload_models()
    return len(plans)


def planned() -> list[tuple[Arm, Path]]:
    return [(ARMS[name], ROOT / f"{ARMS[name].name}-{n}") for n in range(1, RUNS + 1) for name in ORDER]


def _complete(out: Path) -> bool:
    path = out / "results.jsonl"
    return path.exists() and len([x for x in path.read_text(encoding="utf-8").splitlines()
                                  if x.strip()]) >= len(_scenario_ids())


def run(judge_only: bool = False) -> int:
    from evals.run import port_open

    phase2.ROOT = ROOT
    phase2.LOG = ROOT / "progress.log"
    fastdeep.ROOT = ROOT
    problems = verify()
    if problems:
        print("Not run: the freeze does not verify.\n  " + "\n  ".join(problems))
        return 2
    phase2.log(f"Stage C, protocol {PROTOCOL_VERSION}; freeze verified")
    if not judge_only:
        if not PLANS_27B.exists():
            phase2.log(f"writing D's plans with {M27}")
            phase2.log(f"D's plans: {write_27b_plans()} written, sha256 {v2._sha(PLANS_27B)[:16]}")
        for arm, out in planned():
            if _complete(out):
                phase2.log(f"generate {out.name}: already complete, skipped")
                continue
            problems = verify()
            if problems:
                phase2.log("stopping: the freeze no longer verifies: " + "; ".join(problems))
                return 2
            if port_open():
                phase2.log("stopping: port 8765 is in use -- close Elaina first")
                return 2
            fastdeep.log_gpu(f"before {out.name}")
            code = generate(arm, out)
            fastdeep.log_gpu(f"after {out.name}")
            if code != 0:
                phase2.log(f"stopping: {out.name} failed")
                return 1
    return judge([out for _arm, out in planned()])


# ------------------------------------------------------------- analysis

CATEGORIES = {
    "concrete-first": {"concrete_first"},
    "terms-explained": {"terms_explained"},
    "leads-with-answer": {"leads_with_the_answer"},
    "answers-the-ask": {"answers_the_ask"},
    "unnecessary detail": {"only_what_helps", "no_unrelated_remark"},
    "other substance": {"accurate", "says_what_it_solves", "must_contain_any", "answers_not_asks",
                        "language", "quantity"},
}


def _arm_runs(name: str) -> list[dict]:
    return [fd.load_run(p) for p in fd._runs_of(ROOT, ARMS[name].name)]


def contract_notes(loaded: dict) -> dict:
    """{(scenario, index): the explanation_contract record, or None}."""
    records = {r.get("turn_id"): r for r in compare3._records(loaded["path"])}
    found = {}
    for result in loaded["run"]["results"]:
        for turn in result["turns"]:
            record = records.get((turn.get("trace") or {}).get("turn_id")) or {}
            found[(result["scenario"], turn["index"])] = (record.get("context") or {}).get(
                "explanation_contract")
    return found


def seconds(loaded: dict) -> dict:
    return {(r["scenario"], t["index"]): float(t["seconds"])
            for r in loaded["run"]["results"] for t in r["turns"] if t.get("display") is not None}


def read_blind() -> tuple[float | None, dict]:
    """(confirmation rate or None while unlabelled, counts)."""
    if not BLIND.exists() or not BLIND_KEY.exists():
        return None, {}
    items = json.loads(BLIND.read_text(encoding="utf-8"))["items"]
    key = {k["item"]: k for k in json.loads(BLIND_KEY.read_text(encoding="utf-8"))["items"]}
    labelled = [(item, key[item["item"]]) for item in items
                if item["labels"].get("concrete_first") in ("yes", "no")]
    if len(labelled) < len(items):
        return None, {"labelled": len(labelled), "items": len(items)}
    credited = [(i, k) for i, k in labelled if k["judge"] == "yes"]
    others = [(i, k) for i, k in labelled if k["judge"] != "yes"]
    confirmed = sum(1 for i, _ in credited if i["labels"]["concrete_first"] == "yes")
    agree_no = sum(1 for i, _ in others if i["labels"]["concrete_first"] == "no")
    rate = confirmed / len(credited) if credited else None
    return rate, {"credited": len(credited), "confirmed": confirmed,
                  "distractors": len(others), "distractors_agreed": agree_no}


def _human(rate) -> dict:
    """The frozen reader's confirmations: all judge-dependent comparisons when
    the sample confirms the judge, none otherwise."""
    if rate is None or rate < CRITERIA["blind_confirmation_min"]:
        return {}
    every = set(range(1, RUNS + 1))
    with held_out():
        return {key: set(every) for key in fd.corpus_index()}


def net(labels: dict, keys) -> tuple[int, int, float]:
    better = sum(1 for k in keys if k in labels and labels[k].label == "stable DEEP+")
    worse = sum(1 for k in keys if k in labels and labels[k].label == "stable DEEP-worse")
    return better, worse, (better - worse) / len(keys) if keys else 0.0


def regressions(base: list[dict], arm: list[dict], keys) -> tuple[list, list]:
    substance, leads = [], []
    for key in keys:
        pairs = [fd.compare_turn(b["turns"].get(key), a["turns"].get(key)) for b, a in zip(base, arm)]
        if sum(1 for c in pairs if any(fd.tier(d) == "substance" for d in c.broken)) >= 2:
            substance.append(key)
        if sum(1 for c in pairs if "leads_with_the_answer" in c.broken) >= 2:
            leads.append(key)
    return substance, leads


def planned_turns(runs: list[dict], keys) -> tuple[list[tuple], int, int]:
    """((key, run) planned, unusable count, planned count) over held-out turns."""
    found, unusable = [], 0
    for number, loaded in enumerate(runs):
        for key, note in contract_notes(loaded).items():
            if key in keys and note and note.get("arm") in (ec.PLAN, ec.PLAN_FILE):
                found.append((key, number))
                unusable += bool(note.get("unusable"))
    return found, unusable, len(found)


def plan_latency(b_runs: list[dict], c_runs: list[dict], planned: list[tuple]) -> float | None:
    b_times = [seconds(r) for r in b_runs]
    c_times = [seconds(r) for r in c_runs]
    pairs = [(c_times[n][k], b_times[n][k]) for k, n in planned
             if n < len(b_times) and k in c_times[n] and k in b_times[n]]
    if not pairs:
        return None
    return median(c for c, _ in pairs) - median(b for _, b in pairs)


def categories(runs: list[dict], keys) -> Counter:
    counted = Counter()
    for key in keys:
        hits = Counter(c for r in runs if key in r["turns"]
                       for c, props in CATEGORIES.items() if r["turns"][key].defects & props)
        counted.update(c for c, n in hits.items() if n * 2 > len(runs))
    return counted


def analyse() -> dict:
    keys = heldout_keys()
    rate, blind_counts = read_blind()
    human = _human(rate)
    with held_out():
        runs = {name: _arm_runs(name) for name in ORDER}
        pairs = {(x, y): fd.label_turns(runs[x], runs[y], human)
                 for x, y in (("A", "B"), ("B", "C"), ("C", "D"), ("B", "D"), ("A", "C"), ("A", "D"))}
    planned_c, unusable_c, total_c = planned_turns(runs["C"], set(keys))
    _planned_d, unusable_d, total_d = planned_turns(runs["D"], set(keys))
    return {
        "keys": keys, "runs": runs, "pairs": pairs, "blind_rate": rate, "blind": blind_counts,
        "net": {pair: net(labels, keys) for pair, labels in pairs.items()},
        "regressions": {arm: regressions(runs["A"], runs[arm], keys) for arm in ("B", "C", "D")},
        "plan_latency": plan_latency(runs["B"], runs["C"], planned_c),
        "unusable": {"C": (unusable_c, total_c), "D": (unusable_d, total_d)},
        "coverage": {arm: sum(1 for r in runs[arm] for k, n in contract_notes(r).items()
                              if k in set(keys) and n) for arm in ("B", "C", "D")},
    }


def decide(a: dict) -> dict:
    """The frozen decision: what each comparison shows, and the outcome."""
    gain = CRITERIA["net_gain_min"]
    blind_ok = a["blind_rate"] is not None and a["blind_rate"] >= CRITERIA["blind_confirmation_min"]

    def clean(arm):
        substance, leads = a["regressions"][arm]
        return (len(substance) <= CRITERIA["substance_regressions_max"]
                and len(leads) <= CRITERIA["leads_regressions_max"])

    unusable, planned_n = a["unusable"]["C"]
    latency = a["plan_latency"]
    b_improves = a["net"][("A", "B")][2] >= gain and clean("B") and blind_ok
    c_adds = (a["net"][("B", "C")][2] >= gain and clean("C") and blind_ok
              and latency is not None and latency <= CRITERIA["plan_latency_max_s"]
              and planned_n > 0 and unusable / planned_n <= CRITERIA["unusable_plans_max"])
    d_adds = a["net"][("B", "D")][2] >= gain and clean("D") and blind_ok
    if c_adds:
        outcome = "C: question-specific planning adds value over the contract alone"
    elif b_improves:
        outcome = "B: one contract produces the improvement; the plan adds insufficient value"
    else:
        outcome = "neither: no arm produces the required improvement"
    if d_adds and not c_adds:
        outcome += "; D passes where C does not -- the plan works, the 8B cannot write it"
    return {"blind_ok": blind_ok, "b_improves": b_improves, "c_adds": c_adds, "d_adds": d_adds,
            "clean": {arm: clean(arm) for arm in ("B", "C", "D")}, "outcome": outcome}


def validity(a: dict, frozen_problems: list[str]) -> list[str]:
    problems = list(frozen_problems)
    expected = len(_scenario_ids())
    for name in ORDER:
        runs = a["runs"][name]
        if len(runs) != RUNS:
            problems.append(f"{name}: {len(runs)} runs, not {RUNS}")
        for number, loaded in enumerate(runs, 1):
            if len(loaded["run"]["results"]) < expected:
                problems.append(f"{name} run {number}: {len(loaded['run']['results'])} of {expected} scenarios")
            turns = [t for k, t in loaded["turns"].items() if k in set(a["keys"])]
            share = sum(t.comparable for t in turns) / len(turns) if turns else 0.0
            if share < CRITERIA["comparable_min"]:
                problems.append(f"{name} run {number}: {share:.0%} of held-out turns comparable")
    if a["blind_rate"] is None:
        problems.append("blind labels not in yet (--export-blind, then label runtime/evals/pc/blind.json)")
    return problems


def _pct(value) -> str:
    return "-" if value is None else f"{value:.0%}"


def report() -> str:
    frozen_problems = verify()
    a = analyse()
    d = decide(a)
    problems = validity(a, frozen_problems)
    keys = a["keys"]
    lines: list[str] = []
    add = lines.append
    add("# Stage C: the explanation contract, held out")
    add("")
    add(f"*Generated by `evals/stagec.py` on {datetime.now():%Y-%m-%d %H:%M}. Protocol "
        f"{PROTOCOL_VERSION} (docs/STAGE_C_PROTOCOL.md). Freeze: "
        + ("verified" if not frozen_problems else "**does not verify**: " + "; ".join(frozen_problems))
        + ". Decisions read the 30 held-out turns only.*")
    add("")
    add("## Outcome")
    add("")
    add("**" + ("INVALID / NOT FINAL: " + "; ".join(problems) if problems else d["outcome"]) + "**")
    add("")
    add("| comparison | stably better | stably worse | net gain | bar |")
    add("|---|---|---|---|---|")
    for pair, name in ((("A", "B"), "1. A -> B: does one contract help?"),
                       (("B", "C"), "2. B -> C: does an 8B plan add to it?"),
                       (("C", "D"), "3. C -> D: is the 8B the limit?"),
                       (("B", "D"), "B -> D (the ceiling's gain over the contract)"),
                       (("A", "C"), "A -> C (information)"), (("A", "D"), "A -> D (information)")):
        better, worse, gain = a["net"][pair]
        add(f"| {name} | {better} | {worse} | {gain:+.0%} | {CRITERIA['net_gain_min']:.0%} |")
    add("")
    add(f"4. **Blind labels:** confirmation {_pct(a['blind_rate'])} "
        f"(bar {CRITERIA['blind_confirmation_min']:.0%}); {a['blind']}.")
    add("")
    add("5. **Regressions against A** (substance, at most "
        f"{CRITERIA['substance_regressions_max']}; leads-with-the-answer, at most "
        f"{CRITERIA['leads_regressions_max']}):")
    for arm in ("B", "C", "D"):
        substance, leads = a["regressions"][arm]
        add(f"   - {arm}: substance {len(substance)} "
            f"({', '.join(f'`{k[0]}` t{k[1]}' for k in substance) or 'none'}); "
            f"leads {len(leads)} ({', '.join(f'`{k[0]}` t{k[1]}' for k in leads) or 'none'})")
    latency = a["plan_latency"]
    add(f"6. **Latency:** C's median turn time on planned turns is "
        f"{'-' if latency is None else f'{latency:+.2f} s'} against B's on the same turns "
        f"(bar +{CRITERIA['plan_latency_max_s']:.1f} s).")
    for name in ORDER:
        values = [compare.latency(r["run"]) for r in a["runs"][name]]
        if values:
            add(f"   - {name}: turn p50 {sum(v['p50'] for v in values) / len(values):.1f} s, "
                f"p95 {sum(v['p95'] for v in values) / len(values):.1f} s")
    for arm in ("C", "D"):
        bad, total = a["unusable"][arm]
        add(f"7. **Unusable plans, {arm}:** {bad} of {total} "
            f"({_pct(bad / total if total else None)}; bar {CRITERIA['unusable_plans_max']:.0%}).")
    add(f"   Contract applied on held-out turn-runs: "
        + ", ".join(f"{arm} {n}/{len(keys) * RUNS}" for arm, n in a["coverage"].items()) + ".")
    add("")
    add("## 8. Failure categories")
    add("")
    add("Turns failing the category in at least 2 of 3 runs.")
    add("")
    add("| category | " + " | ".join(f"{arm} held-out" for arm in ORDER) + " | "
        + " | ".join(f"{arm} diagnosis 21" for arm in ORDER) + " |")
    add("|---|" + "---|" * (2 * len(ORDER)))
    held = {arm: categories(a["runs"][arm], keys) for arm in ORDER}
    ref = {arm: categories(a["runs"][arm], REFERENCE) for arm in ORDER}
    for name in CATEGORIES:
        add(f"| {name} | " + " | ".join(f"{held[arm].get(name, 0)}/{len(keys)}" for arm in ORDER)
            + " | " + " | ".join(f"{ref[arm].get(name, 0)}/{len(REFERENCE)}" for arm in ORDER) + " |")
    add("")
    add(CRITERIA_TEXT)
    add("")
    return "\n".join(lines) + "\n"


def export_blind() -> int:
    """The concrete_first sample, blind: no arm, no run, no verdict in the file
    the person labels; the key beside it holds those."""
    keys = set(heldout_keys())
    rubric = corpus.rubric()
    pool_yes, pool_no = [], []
    with held_out():
        runs = {name: _arm_runs(name) for name in ORDER}
    for name in ORDER:
        for number, loaded in enumerate(runs[name], 1):
            for result in loaded["run"]["results"]:
                history = []
                for turn in result["turns"]:
                    key = (result["scenario"], turn["index"])
                    outcome = loaded["turns"].get(key)
                    if key in keys and outcome is not None:
                        verdict = outcome.properties.get("concrete_first")
                        entry = {"arm": name, "run": number, "scenario": key[0], "index": key[1],
                                 "judge": verdict, "conversation": list(history),
                                 "person": turn["said"], "elaina": turn["display"] or ""}
                        if verdict == "yes" and name in ("B", "C", "D"):
                            pool_yes.append(entry)
                        elif verdict == "no":
                            pool_no.append(entry)
                    history.append({"person": turn["said"], "elaina": turn["display"] or ""})
    rng = random.Random(BLIND_SAMPLE["seed"])
    by_arm = defaultdict(list)
    for entry in pool_yes:
        by_arm[entry["arm"]].append(entry)
    chosen = []
    for arm in ("B", "C", "D"):
        rng.shuffle(by_arm[arm])
    while len(chosen) < BLIND_SAMPLE["credited"] and any(by_arm.values()):
        for arm in ("B", "C", "D"):
            if by_arm[arm] and len(chosen) < BLIND_SAMPLE["credited"]:
                chosen.append(by_arm[arm].pop())
    rng.shuffle(pool_no)
    chosen += pool_no[:BLIND_SAMPLE["distractors"]]
    rng.shuffle(chosen)
    items, key_items = [], []
    for number, entry in enumerate(chosen, 1):
        items.append({"item": number, "conversation": entry["conversation"], "person": entry["person"],
                      "elaina": entry["elaina"], "labels": {"concrete_first": ""},
                      "definitions": {"concrete_first": rubric["concrete_first"]["definition"]},
                      "allowed": {"concrete_first": ["yes", "no"]}})
        key_items.append({"item": number, **{k: entry[k] for k in ("arm", "run", "scenario", "index", "judge")}})
    ROOT.mkdir(parents=True, exist_ok=True)
    BLIND.write_text(json.dumps({"instructions": "Label each reply yes or no on concrete_first, by the "
                                 "definition given. Which configuration wrote it is hidden on purpose.",
                                 "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    BLIND_KEY.write_text(json.dumps({"items": key_items}, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# Blind labels: {len(items)} replies, concrete_first", ""]
    for item in items:
        lines += [f"## Item {item['item']}", ""]
        for said in item["conversation"]:
            lines += [f"> **Person:** {said['person']}", ">", f"> **Elaina:** {said['elaina']}", ""]
        lines += [f"**Person:** {item['person']}", "", f"**Elaina:** {item['elaina']}", ""]
    lines += ["`concrete_first`: " + rubric["concrete_first"]["definition"], ""]
    BLIND.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    return len(items)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        return _child(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for flag in ("--review", "--freeze", "--verify", "--run", "--judge-only", "--export-blind"):
        parser.add_argument(flag, action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    if args.review:
        found = record()
        found["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
        print(json.dumps(found, indent=1, ensure_ascii=False))
        return 0
    if args.freeze:
        found = freeze()
        print(f"Frozen: corpus {found['corpus']['sha256'][:16]}, contract "
              f"{found['arm_files']['brain/explain_contract.py'][:16]}, system "
              f"{found['system_under_test']['sha256']} -> {FROZEN}")
        return 0
    if args.verify:
        problems = verify()
        print("verified" if not problems else "does not verify:\n  " + "\n  ".join(problems))
        return 0 if not problems else 1
    if args.run or args.judge_only:
        return run(judge_only=args.judge_only)
    if args.export_blind:
        print(f"Wrote {BLIND}: {export_blind()} replies to label blind")
        return 0
    if args.report:
        Path(args.report).write_text(report(), encoding="utf-8")
        print(f"Wrote {args.report}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
