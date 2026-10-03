"""Corpus v2: the held-out, frozen evaluation of the shadow FAST/DEEP router.

    .venv/Scripts/python.exe -m evals.fastdeep_v2 --review         # what --freeze would record
    .venv/Scripts/python.exe -m evals.fastdeep_v2 --freeze         # only once the user approves
    .venv/Scripts/python.exe -m evals.fastdeep_v2 --verify
    .venv/Scripts/python.exe -m evals.fastdeep_v2 --run            # 5 arms x 3 runs, then judge
    .venv/Scripts/python.exe -m evals.fastdeep_v2 --export-blind   # only what decides a criterion
    .venv/Scripts/python.exe -m evals.fastdeep_v2 --report docs/FAST_DEEP_V2.md

docs/FAST_DEEP_V2_PROTOCOL.md is the protocol; this file is its executable
half, and both are fingerprinted at the freeze, so neither the evaluation nor
its acceptance criteria can move after the results are seen.

**The arms.** Every arm has the 8B reading every turn except DEEP.

* FAST -- the 8B writes every turn, soft stages on: what she does today.
  The shadow router's decision is recorded and not acted on.
* ROUTED -- the routed system itself: FAST, except that the turns the
  shadow router decides DEEP are written by the 27B under DEEP's setting,
  with the conversation as it stands (FAST's earlier answers), and the
  next turn goes back to the 8B. The switches and reloads are real.
* LATE -- the 27B writes every turn after the first, whatever the router
  decided: for each second turn, what sending it to DEEP would have
  produced after FAST's first answer. The ground truth for the follow-ups.
* SPLIT -- the 27B writes every turn under the 8B's reading (Stage B's arm):
  the ground truth for first turns, and, on later turns, the comparison
  with its own history that Stage B relied on.
* DEEP -- the 27B reads and writes every turn: reference only.

**Labels.** The frozen v1 analysis (evals/fastdeep_analysis.py, fingerprint
unchanged) labels each turn from FAST run *i* against the counterfactual run
*i*: SPLIT for a first turn, LATE for the second. A label needs the same
history on both sides, so a third turn (group E's return turns) has none.
The v2 suite is made visible to that frozen code inside this module's
processes only (``held_out``), so v1's own fingerprint and reports are
untouched in the working tree.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import inspect
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from importlib import metadata
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Silent before anything starts: the runs and the judge are child processes
# that inherit this, and a backend that spoke would spend the paid voice on
# every one of about 900 turns. evals/run.py forces it off again.
os.environ.setdefault("ELAINA_TTS", "off")

from brain import fastdeep_apply, fastdeep_router  # noqa: E402
from evals import compare, compare3, corpus, fastdeep, phase2  # noqa: E402
from evals import fastdeep_analysis as fd  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402
from evals.phase2 import Arm, M8, M27, NO_STT, SOFT_OFF  # noqa: E402

ROOT = fd.EVALS / "pf2"
FROZEN = ROOT / "FROZEN.json"
BLIND = ROOT / "blind.json"
SUITE = "followups_v2"
STAGED = PROJECT_ROOT / "evals" / "scenarios_next" / f"{SUITE}.json"
PLACED = corpus.SCENARIOS / f"{SUITE}.json"
PROTOCOL = PROJECT_ROOT / "docs" / "FAST_DEEP_V2_PROTOCOL.md"
PROTOCOL_VERSION = "v2.0"
RUNS = 3

# ------------------------------------------------------------------ arms

_READS_8B = {**NO_STT, "ELAINA_DOMAINS": "act", "ELAINA_MODEL": M8}
ARMS = {
    "fast": Arm("pf2-fast", "pipeline",
                "FAST: the 8B reads and writes every turn, soft stages on",
                env=dict(_READS_8B), runs=RUNS, suites=(SUITE,)),
    "routed": Arm("pf2-routed", "pipeline",
                  "ROUTED: FAST, with the turns the shadow router decides DEEP written by "
                  "the 27B (soft stages off) in the same conversation",
                  env={**_READS_8B, fastdeep_apply.ROUTE_ENV: fastdeep_apply.ROUTER,
                       fastdeep_apply.MODEL_ENV: M27}, runs=RUNS, suites=(SUITE,)),
    "late": Arm("pf2-late", "pipeline",
                "LATE: FAST writes the first turn, the 27B (soft stages off) every turn after",
                env={**_READS_8B, fastdeep_apply.ROUTE_ENV: fastdeep_apply.AFTER_FIRST,
                     fastdeep_apply.MODEL_ENV: M27}, runs=RUNS, suites=(SUITE,)),
    "split": Arm("pf2-split", "pipeline",
                 "SPLIT: the 8B reads, the 27B writes every turn, soft stages off",
                 env={**_READS_8B, "ELAINA_CONVERSATION_MODEL": M27, **SOFT_OFF},
                 runs=RUNS, suites=(SUITE,)),
    "deep": Arm("pf2-deep", "pipeline",
                "DEEP: the 27B reads and writes every turn, soft stages off (reference)",
                env={**NO_STT, "ELAINA_DOMAINS": "act", "ELAINA_MODEL": M27,
                     "ELAINA_CONVERSATION_MODEL": M27, **SOFT_OFF},
                runs=RUNS, suites=(SUITE,)),
}
ORDER = ("fast", "routed", "late", "split", "deep")

# ----------------------------------------------------- roles and criteria

FIRST, CONFUSION, DEEPENING, STAY_FAST = "first", "confusion", "deepening", "stay_fast"
ROLE_OF = {("A", 1): CONFUSION, ("B", 1): DEEPENING, ("C", 1): STAY_FAST,
           ("E", 1): CONFUSION, ("E", 2): STAY_FAST}
IN_SCOPE = (CONFUSION, DEEPENING)
# A label compares two arms that wrote everything before the turn the same
# way: nothing before a first turn, FAST's first answer before a second.
LABELLED_UP_TO = 1

# The acceptance criteria, fixed before any v2 turn is generated. The
# verdict below reads them from here; the freeze records them.
CRITERIA = {
    "precision_min": 0.70,
    "recall_min": 0.50,
    "deep_worse_sent_max": 1,
    "stay_fast_sent_max": 0,
    # Seconds, set from the Stage B measurements (protocol §6), never from v2's.
    "deep_turn_p95_max_s": 18.0,      # a DEEP turn end to end, the 27B's load in it
    "reload_p95_max_s": 5.0,          # the 8B's reload on the turn after a DEEP turn
    "fast_turn_p95_margin_s": 1.0,    # ROUTED's other FAST turns against the FAST arm's
    "comparable_min": 0.95,
}
CRITERIA_TEXT = (
    "Decisions are ROUTED's: a turn is sent when the engine switched it (context.fastdeep_applied). "
    "Turn-run pairs are pooled over the three ROUTED runs. "
    "Precision: of every sent turn-run (any group, any turn), the share whose turn is stable DEEP+. "
    "Recall: of the stable DEEP+ follow-ups in scope (groups A and E second turns, group B second "
    "turns), the share of turn-runs sent. "
    "DEEP-worse sent: distinct stable DEEP-worse turns sent in any ROUTED run. "
    "Should-stay-FAST sent: group C second turns and group E third turns sent in any ROUTED run. "
    "Latency, from ROUTED's measured turns in the order they ran (each run's first turn, a cold "
    "start, left out): the p95 of DEEP turns end to end; the p95 of the 8B reload measured in "
    "each return turn (the first turn after a DEEP turn); and the p95 of the other FAST turns "
    "against the FAST arm's p95 over the same turns. "
    "A criterion that cannot be computed is not met. "
    "Valid only if every arm has three complete runs, every run of every arm has at least 95% of "
    "its scored turns comparable, and the freeze verifies.")


# ------------------------------------------------------------ the corpus


def corpus_file() -> Path:
    return PLACED if PLACED.exists() else STAGED


def raw_corpus() -> dict:
    return json.loads(corpus_file().read_text(encoding="utf-8"))


def roles(raw: dict | None = None) -> dict[tuple[str, int], str]:
    """(scenario, index) -> role, for every scored v2 turn, from the corpus alone."""
    raw = raw or raw_corpus()
    found = {}
    for scenario in raw["scenarios"]:
        for index, turn in enumerate(scenario["turns"]):
            if turn.get("score"):
                found[(scenario["id"], index)] = (
                    FIRST if index == 0 else ROLE_OF.get((scenario["group"], index), ""))
    return found


def groups(raw: dict | None = None) -> dict[str, str]:
    return {s["id"]: s["group"] for s in (raw or raw_corpus())["scenarios"]}


@contextmanager
def held_out():
    """The v2 suite visible to the frozen v1 code, in this process only."""
    saved = corpus.ALL_SUITES
    corpus.ALL_SUITES = saved if SUITE in saved else saved + (SUITE,)
    try:
        yield
    finally:
        corpus.ALL_SUITES = saved


# ------------------------------------------------------------ the freeze

ANALYSIS_FILES = fd.FROZEN_FILES
EVALUATOR_FILES = ("evals/fastdeep_v2.py", "evals/judge.py", "evals/run.py",
                   "evals/phase2.py", "evals/fastdeep.py", "evals/integrity.py")
ROUTER_FILE = "brain/fastdeep_router.py"
SWITCH_FILES = ("brain/fastdeep_apply.py", "brain/response_stages.py", "brain/chat_engine.py")
SYSTEM_DIRS = ("agents", "brain", "config", "core", "memory", "security", "tools", "vision",
               "voice")
SYSTEM_SUFFIXES = (".py", ".yaml", ".yml", ".json", ".txt")
VOLATILE = ("frozen_at", "reviewed_at")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _files(names) -> dict[str, str]:
    return {name: _sha(PROJECT_ROOT / name) for name in names}


def system_fingerprint() -> tuple[str, int]:
    """Everything the backend runs: (hash, file count)."""
    digest = hashlib.sha256()
    paths = [PROJECT_ROOT / "main.py"]
    for name in SYSTEM_DIRS:
        paths += [p for p in (PROJECT_ROOT / name).rglob("*")
                  if p.is_file() and p.suffix in SYSTEM_SUFFIXES and "__pycache__" not in p.parts]
    for path in sorted(paths):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(_sha(path).encode())
    return digest.hexdigest()[:16], len(paths)


def _git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=str(PROJECT_ROOT), capture_output=True,
                              text=True, check=False).stdout.strip()
    except OSError:
        return ""


def _ollama(path: str) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:11434{path}", timeout=10) as reply:
            return json.loads(reply.read().decode("utf-8"))
    except Exception as error:  # noqa: BLE001 - recorded, and verification then fails
        return {"error": f"{type(error).__name__}: {error}"}


def models() -> dict:
    listed = {m.get("name"): m for m in _ollama("/api/tags").get("models", ())}
    found = {}
    for role, name in (("FAST and the reading model", M8), ("DEEP", M27), ("judge", DEFAULT_JUDGE)):
        model = listed.get(name) or {}
        details = model.get("details") or {}
        found[name] = {"role": role, "digest": model.get("digest", "MISSING"),
                       "size": model.get("size"), "quantization": details.get("quantization_level"),
                       "parameters": details.get("parameter_size")}
    return found


def _ram_gb() -> float:
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                    ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
                    ("extended", ctypes.c_ulonglong)]
    try:
        status = Status()
        status.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round(status.total / 2**30, 1)
    except Exception:  # noqa: BLE001 - not Windows
        return 0.0


def runtime() -> dict:
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                              "--format=csv,noheader"], capture_output=True, text=True,
                             check=False, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        gpu = f"unavailable: {error}"

    def version(package: str) -> str:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return ""

    return {
        "ollama": _ollama("/api/version").get("version", "unreachable"),
        "ollama_env": {k: v for k, v in sorted(os.environ.items()) if k.startswith("OLLAMA_")},
        "gpu": gpu,
        "cpu": platform.processor(),
        "ram_gb": _ram_gb(),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "packages": {name: version(name) for name in ("ollama", "websockets")},
    }


def record() -> dict:
    """Everything the freeze fixes. Deterministic but for VOLATILE keys."""
    raw = raw_corpus()
    v1_frozen, v1_recorded, v1_now = fd.frozen_state()
    system, count = system_fingerprint()
    router_source = inspect.getsource(fastdeep_router.decide).encode()
    return {
        "protocol": {"version": PROTOCOL_VERSION,
                     "document": PROTOCOL.relative_to(PROJECT_ROOT).as_posix(),
                     "sha256": _sha(PROTOCOL) if PROTOCOL.exists() else "MISSING"},
        "corpus": {"suite": SUITE, "file": PLACED.relative_to(PROJECT_ROOT).as_posix(),
                   "sha256": _sha(corpus_file()),
                   "scenarios": len(raw["scenarios"]),
                   "scored_turns": len(roles(raw)),
                   "groups": dict(sorted(Counter(groups(raw).values()).items())),
                   "roles": dict(sorted(Counter(roles(raw).values()).items()))},
        "analysis": {"v1_fingerprint": v1_now, "v1_frozen_fingerprint": v1_recorded,
                     "v1_still_frozen": v1_frozen, "files": _files(ANALYSIS_FILES)},
        "rubric": {"version": corpus.rubric_version(), "fingerprint": corpus.rubric_fingerprint()},
        "evaluator": {"judge": DEFAULT_JUDGE, "files": _files(EVALUATOR_FILES)},
        "router": {"file": ROUTER_FILE, "sha256": _sha(PROJECT_ROOT / ROUTER_FILE),
                   "decide_sha256": hashlib.sha256(router_source).hexdigest(),
                   "git": "untracked" if not _git("ls-files", ROUTER_FILE) else (
                       "modified" if _git("status", "--porcelain", ROUTER_FILE) else
                       _git("log", "-1", "--format=%h", "--", ROUTER_FILE))},
        "switch": {"files": _files(SWITCH_FILES),
                   "policies": {"routed": fastdeep_apply.ROUTER, "late": fastdeep_apply.AFTER_FIRST}},
        "system_under_test": {"sha256": system, "files": count, "dirs": list(SYSTEM_DIRS) + ["main.py"],
                              "git_head": _git("rev-parse", "--short", "HEAD")},
        "models": models(),
        "runtime": runtime(),
        "arms": {name: {"dirs": f"{ARMS[name].name}-1..{RUNS}", "about": ARMS[name].about,
                        "env": ARMS[name].env} for name in ORDER},
        "runs": RUNS,
        "order": "per run number: " + ", ".join(ORDER),
        "criteria": CRITERIA,
        "criteria_text": CRITERIA_TEXT,
    }


def _differences(old, new, where="") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        found = []
        for key in sorted(set(old) | set(new)):
            if key in VOLATILE:
                continue
            found += _differences(old.get(key), new.get(key), f"{where}.{key}" if where else key)
        return found
    return [] if old == new else [f"{where}: frozen {old!r}, now {new!r}"]


def verify() -> list[str]:
    """What no longer matches the freeze; empty when everything does."""
    if not FROZEN.exists():
        return ["not frozen: run --freeze once the corpus and protocol are approved"]
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    found = _differences(frozen, record())
    if not PLACED.exists():
        found.append(f"{PLACED.relative_to(PROJECT_ROOT).as_posix()} is missing")
    return found


def review() -> dict:
    """What --freeze would write, computed now; writes nothing."""
    found = record()
    found["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
    return found


def corpus_problems() -> list[str]:
    """The corpus checks of evals/corpus.py, over v1 and v2 together."""
    with held_out():
        v1 = corpus.load(tuple(s for s in corpus.ALL_SUITES if s != SUITE))
        return corpus.problems(v1 + corpus._load_suite(corpus_file()))


def freeze() -> dict:
    problems = corpus_problems()
    if problems:
        raise SystemExit("The corpus has problems:\n  " + "\n  ".join(problems))
    if not fd.frozen_state()[0]:
        raise SystemExit("The v1 analysis is not the frozen one; v2 reuses it unchanged.")
    if not PROTOCOL.exists():
        raise SystemExit(f"{PROTOCOL} is missing")
    if not PLACED.exists():
        shutil.move(str(STAGED), str(PLACED))
        if STAGED.parent.exists() and not any(STAGED.parent.iterdir()):
            STAGED.parent.rmdir()
    ROOT.mkdir(parents=True, exist_ok=True)
    found = record()
    found["frozen_at"] = datetime.now().isoformat(timespec="seconds")
    FROZEN.write_text(json.dumps(found, indent=1, ensure_ascii=False), encoding="utf-8")
    return found


# ------------------------------------------------------------- running


def _child(argv: list[str]) -> int:
    """evals.run or evals.judge with the v2 suite visible to them."""
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


def generate(arm: Arm, out: Path) -> int:
    phase2.unload_models()
    command = [str(phase2.PYTHON), "-m", "evals.fastdeep_v2", "--child", "run",
               "--out", str(out), "--suites", SUITE,
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
        [str(phase2.PYTHON), "-m", "evals.fastdeep_v2", "--child", "judge",
         "--model", DEFAULT_JUDGE, "--run", *[str(out) for out in outs]],
        cwd=str(PROJECT_ROOT), check=False)
    phase2.log(f"judge: exit {result.returncode}")
    return result.returncode


def planned() -> list[tuple[Arm, Path]]:
    return [(ARMS[name], ROOT / f"{ARMS[name].name}-{n}")
            for n in range(1, RUNS + 1) for name in ORDER]


def _complete(out: Path) -> bool:
    path = out / "results.jsonl"
    return path.exists() and len(
        [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    ) >= len(raw_corpus()["scenarios"])


def run(judge_only: bool = False) -> int:
    from evals.run import port_open

    phase2.ROOT = ROOT
    phase2.LOG = ROOT / "progress.log"
    fastdeep.ROOT = ROOT
    problems = verify()
    if problems:
        print("Not run: the freeze does not verify.\n  " + "\n  ".join(problems))
        return 2
    phase2.log(f"corpus v2, protocol {PROTOCOL_VERSION}; freeze verified")
    outs = [out for _arm, out in planned()]
    if not judge_only:
        for arm, out in planned():
            if _complete(out):
                phase2.log(f"generate {out.name}: already complete, skipped")
                continue
            # Again before every run: Ollama updates itself, and the models
            # and the code must be the frozen ones for every arm.
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
    return judge(outs)


# ------------------------------------------------------------- analysis


def _arm_runs(name: str) -> list[dict]:
    return [fd.load_run(p) for p in fd._runs_of(ROOT, ARMS[name].name)]


def counterfactual(split: list[dict], late: list[dict]) -> list[dict]:
    """Run i of 'sending the turn to DEEP': SPLIT's first turns, LATE's later ones."""
    found = []
    for s, l in zip(split, late):
        keys = set(s["turns"]) | set(l["turns"])
        turns = {k: (s if k[1] == 0 else l)["turns"][k] for k in keys
                 if k in (s if k[1] == 0 else l)["turns"]}
        found.append({"path": l["path"], "run": l["run"], "turns": turns})
    return found


def read_blind(path: Path = BLIND) -> dict:
    """The frozen reader's rule, LATE in DEEP's place."""
    if not path.exists():
        return {}
    by_comparison: dict = {}
    for item in json.loads(path.read_text(encoding="utf-8"))["items"]:
        arm = "deep" if ARMS["late"].name in item["run"] else "fast"
        by_comparison.setdefault((item["scenario"], item["index"], item["comparison"]), {})[arm] = (
            item["labels"])
    confirmed: dict = {}
    for (scenario, index, number), arms in by_comparison.items():
        fast, deep = arms.get("fast", {}), arms.get("deep", {})
        if fast and deep and all(fast.get(n) == "no" and deep.get(n) == "yes" for n in deep):
            confirmed.setdefault((scenario, index), set()).add(number)
    return confirmed


def decisions(loaded: dict) -> dict:
    """{(scenario, index): what the engine decided and did} for one run."""
    records = {r.get("turn_id"): r for r in compare3._records(loaded["path"])}
    found = {}
    for result in loaded["run"]["results"]:
        for turn in result["turns"]:
            record = records.get((turn.get("trace") or {}).get("turn_id")) or {}
            context = record.get("context") or {}
            shadow = context.get("shadow_route") or {}
            found[(result["scenario"], turn["index"])] = {
                "decision": shadow.get("decision", fastdeep_router.FAST),
                "reason": shadow.get("reason", "(no decision recorded)"),
                "confidence": shadow.get("confidence", ""),
                "continuation": tuple(shadow.get("continuation") or ()),
                "switched": "fastdeep_applied" in context,
            }
    return found


def acceptable(turn) -> bool:
    """No substance or explanation defect: the tiers a label is made of."""
    return turn.comparable and not any(
        fd.tier(d) in ("substance", "explanation") for d in turn.defects)


def _ratio(pair) -> float | None:
    return pair[0] / pair[1] if pair[1] else None


def routing(labels: dict, role: dict, sent_by_run: list[set]) -> dict:
    """The routing measures, pooled over turn-run pairs (CRITERIA_TEXT)."""
    plus = {k for k, v in labels.items() if v.label == "stable DEEP+"}
    worse = {k for k, v in labels.items() if v.label == "stable DEEP-worse"}
    scope = {k for k, r in role.items() if r in IN_SCOPE}
    stay = {k for k, r in role.items() if r == STAY_FAST}
    pairs = [(k, i) for i, sent in enumerate(sent_by_run) for k in sent if k in role]
    target = plus & scope
    runs = len(sent_by_run)
    return {
        "precision": (sum(1 for k, _ in pairs if k in plus), len(pairs)),
        "recall": (sum(1 for sent in sent_by_run for k in target if k in sent), len(target) * runs),
        "utilization": (len(pairs), len(role) * runs),
        "deep_worse_sent": sorted(k for k in worse if any(k in s for s in sent_by_run)),
        "stay_fast_sent": sorted((k, i) for k, i in pairs if k in stay),
        "missed": sorted(k for k in target if sum(k in s for s in sent_by_run) * 2 <= runs),
        "deep_plus_in_scope": sorted(target),
        "deep_plus_first": sorted(k for k in plus if role.get(k) == FIRST),
    }


def majority(sent_by_run: list[set], key) -> bool:
    return sum(key in s for s in sent_by_run) * 2 > len(sent_by_run)


def attribute(label, role: str, sent: bool, fast_ok: int, deep_ok: int, runs: int) -> str:
    """One turn, one outcome (docs/FAST_DEEP_V2_PROTOCOL.md §7). ``sent`` is the
    majority ROUTED decision; ``fast_ok`` and ``deep_ok`` count the runs in
    which FAST's and the 27B's reply had no substance or explanation defect."""
    need = runs // 2 + 1
    fast_good, deep_good = fast_ok >= need, deep_ok >= need
    if role == STAY_FAST and sent:
        return "routing miss: escalated a should-stay-FAST turn"
    if label is None:
        # A return turn: no counterfactual with the same history exists.
        return "correct FAST" if fast_good else "FAST model failure (return turn)"
    if label.label == "stable DEEP+":
        if sent:
            return "correct escalation"
        if role in IN_SCOPE:
            return "routing miss: missed escalation"
        return "FAST model failure (outside the router's scope)"
    if label.label == "stable DEEP-worse":
        return "DEEP model failure: made it worse" if sent else "correct FAST"
    if label.label == "pending DEEP+":
        return "unstable / non-repeatable (judge-only)"
    if not fast_good and not deep_good:
        return "neither model good enough"
    if not fd.no_difference(label):
        return "unstable / non-repeatable"
    if sent:
        return "unneeded escalation (cost only)"
    return "correct FAST" if fast_good else "FAST model failure (outside the router's scope)"


OUTCOMES = ("correct escalation", "correct FAST", "routing miss: missed escalation",
            "routing miss: escalated a should-stay-FAST turn", "DEEP model failure: made it worse",
            "FAST model failure (outside the router's scope)", "FAST model failure (return turn)",
            "neither model good enough", "unstable / non-repeatable",
            "unstable / non-repeatable (judge-only)", "unneeded escalation (cost only)")


def latency_classes(routed: list[dict], fast: list[dict]) -> dict[str, list[float]]:
    """ROUTED's turns by what they paid for, in the order they ran, with the
    FAST arm's times for the same FAST turns (run i against run i)."""
    out: dict[str, list[float]] = {"deep": [], "return": [], "reload": [], "fast": [],
                                   "fast_control": []}
    for number, loaded in enumerate(routed):
        made = decisions(loaded)
        records = {r.get("turn_id"): r for r in compare3._records(loaded["path"])}
        control = {}
        if number < len(fast):
            control = {(res["scenario"], t["index"]): float(t["seconds"])
                       for res in fast[number]["run"]["results"] for t in res["turns"]
                       if t.get("display") is not None}
        previous = None
        for result in loaded["run"]["results"]:
            for turn in result["turns"]:
                key = (result["scenario"], turn["index"])
                switched = made.get(key, {}).get("switched", False)
                if previous is not None:
                    seconds = float(turn["seconds"])
                    if switched:
                        out["deep"].append(seconds)
                    elif previous:
                        record = records.get((turn.get("trace") or {}).get("turn_id")) or {}
                        out["return"].append(seconds)
                        out["reload"].append(sum(
                            (call.get("load_duration") or 0) / 1e9
                            for call in record.get("model_calls") or () if call.get("model") == M8))
                    else:
                        out["fast"].append(seconds)
                        if key in control:
                            out["fast_control"].append(control[key])
                previous = switched
    return out


def loads(runs: list[dict]) -> dict:
    """Model loads seen in the traces: per model, and turns that loaded twice or more."""
    found: dict = {M8: [], M27: []}
    multiple = 0
    for loaded in runs:
        for record in compare3._records(loaded["path"]):
            count = 0
            for call in record.get("model_calls") or ():
                value = (call.get("load_duration") or 0) / 1e9
                if value > 0.5:
                    count += 1
                    found.setdefault(call.get("model"), []).append(value)
            multiple += count >= 2
    return {"by_model": found, "turns_loading_twice": multiple}


def quality(chosen_by_run: list[dict]) -> dict:
    """Turns passing (frozen definition), acceptable, correctness, scenarios passing."""
    out = {"passing": [], "acceptable": [], "correctness": [], "scenarios": []}
    for chosen in chosen_by_run:
        turns = list(chosen.values())
        by_scenario: dict = {}
        for key, turn in chosen.items():
            by_scenario[key[0]] = by_scenario.get(key[0], True) and turn.passed
        out["passing"].append((sum(t.passed for t in turns), len(turns)))
        out["acceptable"].append((sum(acceptable(t) for t in turns), len(turns)))
        out["correctness"].append((sum(t.correctness[0] for t in turns),
                                   sum(t.correctness[1] for t in turns)))
        out["scenarios"].append((sum(by_scenario.values()), len(by_scenario)))
    return out


def analyse() -> dict:
    """Everything the report and the verdict read."""
    role = roles()
    with held_out():
        runs = {name: _arm_runs(name) for name in ORDER}
        cf = counterfactual(runs["split"], runs["late"])
        labelled = {k for k in role if k[1] <= LABELLED_UP_TO}
        labels = {k: v for k, v in fd.label_turns(runs["fast"], cf, read_blind()).items()
                  if k in labelled}
        stageb_style = fd.label_turns(runs["fast"], runs["split"])
        history = fd.label_turns(runs["late"], runs["split"])
        reading = fd.label_turns(runs["split"], runs["deep"])
    made = [decisions(r) for r in runs["routed"]]
    recorded = [decisions(r) for r in runs["fast"]]
    sent_by_run = [{k for k, d in m.items() if d["switched"] and k in role} for m in made]
    return {"role": role, "runs": runs, "cf": cf, "labels": labels, "stageb_style": stageb_style,
            "history": history, "reading": reading, "made": made, "recorded": recorded,
            "sent_by_run": sent_by_run, "routing": routing(labels, role, sent_by_run),
            "latency": latency_classes(runs["routed"], runs["fast"])}


def validity(a: dict, frozen_problems: list[str]) -> list[str]:
    problems = list(frozen_problems)
    expected = len(raw_corpus()["scenarios"])
    for name in ORDER:
        runs = a["runs"][name]
        if len(runs) != RUNS:
            problems.append(f"{name}: {len(runs)} runs, not {RUNS}")
        for number, loaded in enumerate(runs, 1):
            if len(loaded["run"]["results"]) < expected:
                problems.append(f"{name} run {number}: {len(loaded['run']['results'])} of "
                                f"{expected} scenarios")
            turns = [t for k, t in loaded["turns"].items() if k in a["role"]]
            share = sum(t.comparable for t in turns) / len(turns) if turns else 0.0
            if share < CRITERIA["comparable_min"]:
                problems.append(f"{name} run {number}: {share:.0%} of scored turns comparable")
    return problems


def verdict(a: dict) -> list[tuple[str, str, str, bool]]:
    """(criterion, measured, threshold, met), in CRITERIA's order."""
    r = a["routing"]
    rows = []
    precision, recall = _ratio(r["precision"]), _ratio(r["recall"])
    rows.append(("DEEP precision", _pct(r["precision"]), f">= {CRITERIA['precision_min']:.0%}",
                 precision is not None and precision >= CRITERIA["precision_min"]))
    rows.append(("DEEP+ recall (in-scope follow-ups)", _pct(r["recall"]),
                 f">= {CRITERIA['recall_min']:.0%}",
                 recall is not None and recall >= CRITERIA["recall_min"]))
    rows.append(("stable DEEP-worse turns sent", str(len(r["deep_worse_sent"])),
                 f"<= {CRITERIA['deep_worse_sent_max']}",
                 len(r["deep_worse_sent"]) <= CRITERIA["deep_worse_sent_max"]))
    rows.append(("should-stay-FAST turn-runs sent", str(len(r["stay_fast_sent"])),
                 f"<= {CRITERIA['stay_fast_sent_max']}",
                 len(r["stay_fast_sent"]) <= CRITERIA["stay_fast_sent_max"]))
    lat = a["latency"]
    p95 = lambda values: compare._percentile(values, 0.95) if values else None  # noqa: E731
    deep, reload = p95(lat["deep"]), p95(lat["reload"])
    fast, control = p95(lat["fast"]), p95(lat["fast_control"])
    rows.append(("p95 latency, DEEP turns end to end",
                 f"{deep:.1f} s (n={len(lat['deep'])})" if deep is not None else "no DEEP turns",
                 f"<= {CRITERIA['deep_turn_p95_max_s']:.1f} s",
                 deep is not None and deep <= CRITERIA["deep_turn_p95_max_s"]))
    rows.append(("p95 8B reload on return turns",
                 f"{reload:.1f} s (n={len(lat['reload'])})" if reload is not None else "no return turns",
                 f"<= {CRITERIA['reload_p95_max_s']:.1f} s",
                 reload is not None and reload <= CRITERIA["reload_p95_max_s"]))
    rows.append(("p95 latency, other FAST turns",
                 f"{fast:.1f} s against FAST's {control:.1f} s" if fast is not None and control is not None
                 else "no FAST turns",
                 f"<= FAST's + {CRITERIA['fast_turn_p95_margin_s']:.1f} s",
                 fast is not None and control is not None
                 and fast <= control + CRITERIA["fast_turn_p95_margin_s"]))
    return rows


def _pct(pair) -> str:
    return f"{pair[0] / pair[1]:.0%} ({pair[0]}/{pair[1]})" if pair[1] else "- (0/0)"


def _p(values, share) -> str:
    return f"{compare._percentile(values, share):.1f}" if values else "-"


def report() -> str:
    frozen_problems = verify()
    a = analyse()
    role, runs, labels = a["role"], a["runs"], a["labels"]
    group_of = groups()
    problems = validity(a, frozen_problems)
    rows = verdict(a)
    accepted = not problems and all(met for *_, met in rows)
    lines: list[str] = []
    add = lines.append
    add("# Corpus v2: the shadow FAST/DEEP router, held out")
    add("")
    add(f"*Generated by `evals/fastdeep_v2.py` on {datetime.now():%Y-%m-%d %H:%M}. Protocol "
        f"{PROTOCOL_VERSION} (docs/FAST_DEEP_V2_PROTOCOL.md). Freeze: "
        + ("verified" if not frozen_problems else "**does not verify**: " + "; ".join(frozen_problems))
        + ".*")
    add("")
    add("## 1. Verdict")
    add("")
    add("**" + ("INVALID: " + "; ".join(problems) if problems
                else "ACCEPTED" if accepted else "NOT ACCEPTED") + "**")
    add("")
    add("| criterion | measured | frozen threshold | met |")
    add("|---|---|---|---|")
    for name, measured, threshold, met in rows:
        add(f"| {name} | {measured} | {threshold} | {'yes' if met else '**no**'} |")
    add("")
    add(CRITERIA_TEXT)
    add("")

    r = a["routing"]
    add("## 2. Routing")
    add("")
    add(f"- DEEP use: {_pct(r['utilization'])} of turn-runs.")
    add(f"- Stable DEEP+ follow-ups in scope: {len(r['deep_plus_in_scope'])}; missed by the "
        f"majority decision: " + (", ".join(f"`{k[0]}` t{k[1]}" for k in r["missed"]) or "none") + ".")
    add("- Stable DEEP-worse turns sent: " + (", ".join(f"`{k[0]}` t{k[1]}" for k in r["deep_worse_sent"])
                                              or "none") + ".")
    add("- Should-stay-FAST turn-runs sent: " + (", ".join(f"`{k[0]}` t{k[1]} (run {i + 1})"
                                                         for k, i in r["stay_fast_sent"]) or "none") + ".")
    add(f"- First turns stably better with the 27B (outside the rule's scope by design): "
        f"{len(r['deep_plus_first'])}.")
    add("")
    add("| group | role | turns | sent (turn-runs) | stable DEEP+ | stable DEEP-worse "
        "| judge-only | no stable difference | no label |")
    add("|---|---|---|---|---|---|---|---|---|")
    for group in sorted(set(group_of.values())):
        for name in (FIRST, CONFUSION, DEEPENING, STAY_FAST):
            keys = [k for k, v in role.items() if v == name and group_of[k[0]] == group]
            if not keys:
                continue
            sent = sum(1 for s in a["sent_by_run"] for k in keys if k in s)
            got = Counter(labels[k].label if k in labels else "no label" for k in keys)
            add(f"| {group} | {name} | {len(keys)} | {sent}/{len(keys) * len(a['sent_by_run'])} "
                f"| {got.get('stable DEEP+', 0)} | {got.get('stable DEEP-worse', 0)} "
                f"| {got.get('pending DEEP+', 0)} | {got.get(fd.UNSTABLE, 0)} "
                f"| {got.get('no label', 0)} |")
    add("")
    reasons = Counter((d["decision"], d["reason"]) for m in a["made"] for k, d in m.items() if k in role)
    add("ROUTED decisions: " + "; ".join(f"{d} \"{why}\" {n}" for (d, why), n in reasons.most_common()) + ".")
    unstable = [k for k in role if len({m.get(k, {}).get("decision") for m in a["made"]}) > 1]
    add(f"Decisions that changed between ROUTED runs: {len(unstable)} of {len(role)} turns"
        + (": " + ", ".join(f"`{k[0]}` t{k[1]}" for k in sorted(unstable)) if unstable else "") + ".")
    agree = [(m.get(k, {}).get("decision") == f.get(k, {}).get("decision"))
             for m, f in zip(a["made"], a["recorded"]) for k in role if k[1] <= 1]
    add(f"ROUTED's decisions against FAST's recorded shadow decisions on first and second turns "
        f"(same history): {sum(agree)}/{len(agree)} agree.")
    add("")

    add("## 3. What happened to each turn")
    add("")
    outcome = {}
    for key, name in role.items():
        sent = majority(a["sent_by_run"], key)
        fast_source = runs["fast"] if key[1] <= LABELLED_UP_TO else runs["routed"]
        fast_ok = sum(1 for run_ in fast_source if key in run_["turns"] and acceptable(run_["turns"][key]))
        deep_ok = sum(1 for run_ in a["cf"] if key in run_["turns"] and acceptable(run_["turns"][key]))
        outcome[key] = attribute(labels.get(key), name, sent, fast_ok, deep_ok, RUNS)
    counted = Counter(outcome.values())
    add("| outcome | turns |")
    add("|---|---|")
    for name in OUTCOMES:
        add(f"| {name} | {counted.get(name, 0)} |")
    add("")
    for name in OUTCOMES:
        keys = sorted(k for k, v in outcome.items() if v == name)
        if keys and name not in ("correct FAST",):
            add(f"- **{name}**: " + ", ".join(f"`{k[0]}` t{k[1]}" for k in keys))
    add("")

    add("## 4. Quality")
    add("")
    add("Each row takes every scored turn from one source, run by run. \"Acceptable\" is no substance "
        "or explanation defect; \"passing\" is the frozen definition (every check and property).")
    add("")
    add("| source | turns passing | acceptable | correctness | scenarios passing |")
    add("|---|---|---|---|---|")
    perfect = {k for k, v in labels.items() if v.label == "stable DEEP+"}

    def pick(rule):
        found = []
        for i, (f, d) in enumerate(zip(runs["fast"], a["cf"])):
            found.append({k: (d if rule(k, i) and k in d["turns"] else f)["turns"][k]
                          for k in role if k in f["turns"]})
        return found

    sources = (
        ("all FAST (FAST arm)", [{k: r_["turns"][k] for k in role if k in r_["turns"]} for r_ in runs["fast"]]),
        ("ROUTED (the routed system, measured)", [{k: r_["turns"][k] for k in role if k in r_["turns"]}
                                                  for r_ in runs["routed"]]),
        ("perfect knowledge (FAST, or the 27B where stable DEEP+)", pick(lambda k, i: k in perfect)),
        ("the 27B writing every turn after FAST's history (SPLIT first turns, LATE later)",
         [{k: r_["turns"][k] for k in role if k in r_["turns"]} for r_ in a["cf"]]),
        ("SPLIT arm (its own history)", [{k: r_["turns"][k] for k in role if k in r_["turns"]}
                                         for r_ in runs["split"]]),
        ("DEEP arm (reference)", [{k: r_["turns"][k] for k in role if k in r_["turns"]} for r_ in runs["deep"]]),
    )
    for name, chosen in sources:
        q = quality(chosen)
        add(f"| {name} | {fd._mean_pct(q['passing'])} | {fd._mean_pct(q['acceptable'])} "
            f"| {fd._mean_pct(q['correctness'])} | {fd._mean_pct(q['scenarios'])} |")
    add("")

    add("## 5. Does the earlier history matter?")
    add("")
    later = [k for k in role if k[1] == 1]
    def tally(table):
        return Counter(table[k].label for k in later if k in table)
    for name, table in (("FAST -> LATE (the 27B after FAST's first answer; the ground truth)", labels),
                        ("FAST -> SPLIT (the 27B after its own first answer; Stage B's method)",
                         a["stageb_style"]),
                        ("LATE -> SPLIT (what the 27B's own history adds)", a["history"]),
                        ("SPLIT -> DEEP (what the 27B's reading adds; reference)", a["reading"])):
        t = tally(table)
        add(f"- {name}: better {t.get('stable DEEP+', 0)}, judge-only {t.get('pending DEEP+', 0)}, "
            f"worse {t.get('stable DEEP-worse', 0)}, neither {t.get(fd.UNSTABLE, 0)}")
    overstated = sorted(k for k in later if k in a["stageb_style"]
                        and a["stageb_style"][k].label == "stable DEEP+"
                        and labels.get(k) is not None and labels[k].label != "stable DEEP+")
    add("- Second turns Stage B's method calls better and the ground truth does not: "
        + (", ".join(f"`{k[0]}`" for k in overstated) or "none") + ".")
    add("")

    add("## 6. Latency, switching measured")
    add("")
    lat = a["latency"]
    add("| ROUTED turns | n | p50 s | p95 s | frozen budget |")
    add("|---|---|---|---|---|")
    add(f"| DEEP, end to end (27B load and answer) | {len(lat['deep'])} | {_p(lat['deep'], 0.5)} "
        f"| {_p(lat['deep'], 0.95)} | p95 <= {CRITERIA['deep_turn_p95_max_s']:.1f} |")
    add(f"| return, the 8B reload in it | {len(lat['reload'])} | {_p(lat['reload'], 0.5)} "
        f"| {_p(lat['reload'], 0.95)} | p95 <= {CRITERIA['reload_p95_max_s']:.1f} |")
    add(f"| return, end to end (information) | {len(lat['return'])} | {_p(lat['return'], 0.5)} "
        f"| {_p(lat['return'], 0.95)} | - |")
    add(f"| other FAST turns | {len(lat['fast'])} | {_p(lat['fast'], 0.5)} | {_p(lat['fast'], 0.95)} "
        f"| p95 <= FAST's + {CRITERIA['fast_turn_p95_margin_s']:.1f} |")
    add(f"| the same turns in the FAST arm | {len(lat['fast_control'])} | {_p(lat['fast_control'], 0.5)} "
        f"| {_p(lat['fast_control'], 0.95)} | - |")
    every = lat["deep"] + lat["return"] + lat["fast"]
    add(f"| every ROUTED turn (this corpus's mix; information only) | {len(every)} | {_p(every, 0.5)} "
        f"| {_p(every, 0.95)} | - |")
    add("")
    seen = loads(runs["routed"])
    for model, values in seen["by_model"].items():
        if values:
            add(f"- ROUTED loads of `{model}`: {len(values)}, median {median(values):.1f} s, "
                f"p95 {compare._percentile(values, 0.95):.1f} s.")
    add(f"- ROUTED turns that loaded a model twice or more: {seen['turns_loading_twice']}.")
    add("")
    add("| arm | turn p50 / p95 s |")
    add("|---|---|")
    for name in ORDER:
        values = [compare.latency(r_["run"]) for r_ in runs[name]]
        if values:
            add(f"| {name} | {sum(v['p50'] for v in values) / len(values):.1f} / "
                f"{sum(v['p95'] for v in values) / len(values):.1f} |")
    add("")
    add("## 7. Limits")
    add("")
    add("Thirty scenarios, sixty scored turns, three runs: a turn's label rests on three "
        "comparisons, and a criterion can turn on one turn. The corpus was written by someone who "
        "had seen the rule and reviewed by the person who set the criteria. Reasoning, planning, "
        "coding and synthesis turns are not in it.")
    add("")
    return "\n".join(lines) + "\n"


def export_blind() -> int:
    """Pending DEEP+ second turns that decide a criterion: in scope, or sent."""
    a = analyse()
    sent_any = set().union(*a["sent_by_run"]) if a["sent_by_run"] else set()
    wanted = {k: v for k, v in a["labels"].items()
              if v.label == "pending DEEP+" and k[1] == 1
              and (a["role"].get(k) in IN_SCOPE or k in sent_any)}
    with held_out():
        return fd.export_blind(wanted, a["runs"]["fast"], a["runs"]["late"], BLIND)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        return _child(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--judge-only", action="store_true")
    parser.add_argument("--export-blind", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    if args.review:
        print(json.dumps(review(), indent=1, ensure_ascii=False))
        return 0
    if args.freeze:
        found = freeze()
        print(f"Frozen: corpus {found['corpus']['sha256'][:16]}, router {found['router']['sha256'][:16]}, "
              f"system {found['system_under_test']['sha256']} -> {FROZEN}")
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
