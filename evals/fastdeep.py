"""The FAST/DEEP post-fix baseline: exactly the two production candidates.

    .venv/Scripts/python.exe -m evals.fastdeep                 # generate, then judge
    .venv/Scripts/python.exe -m evals.fastdeep --judge-only
    .venv/Scripts/python.exe -m evals.fastdeep --arms pd-8b pd-27b

docs/FAST_DEEP_PROTOCOL.md (amended), §2:

* FAST = qwen3:8b routing and answering, soft stages on;
* DEEP = the 27B routing and answering, soft stages off;

both with speech recognition off, over all six suites (the followups suite
included), three runs each. Around them: the direct drift arms, which set
each model's normal speed for the pressure rule, and the recogniser-resident
arms, which measure resource pressure (latency only).

Phase 2's machinery does the work (evals/phase2.py): every model is unloaded
before each run, and judging waits until generation is done. Added here:
FAST and DEEP runs alternate, so a change in the machine's speed during the
session falls on both; the GPU's other tenants are logged before and after
every run; and nothing starts unless the analysis is the frozen one
(evals/fastdeep_analysis.py --freeze).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import corpus, fastdeep_analysis, phase2  # noqa: E402
from evals.phase2 import Arm, M8, M27, NO_STT, SOFT_OFF, LATENCY  # noqa: E402

ROOT = fastdeep_analysis.ROOT
ACT = {"ELAINA_DOMAINS": "act"}
DEEP = {"ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27, **SOFT_OFF}

FAST_ARM = Arm(fastdeep_analysis.FAST_ARM, "pipeline",
               "FAST: qwen3:8b, soft stages on, recogniser off, all six suites",
               env={**NO_STT, **ACT}, runs=fastdeep_analysis.RUNS,
               suites=corpus.ALL_SUITES)
DEEP_ARM = Arm(fastdeep_analysis.DEEP_ARM, "pipeline",
               "DEEP: 27B, soft stages off, recogniser off, all six suites",
               env={**NO_STT, **ACT, **DEEP}, runs=fastdeep_analysis.RUNS,
               suites=corpus.ALL_SUITES)
ARMS: tuple[Arm, ...] = (
    # Each model's normal speed this session, for the pressure rule.
    Arm("pd-8b", "direct", "qwen3:8b, persona only (normal speed)", model=M8, runs=1,
        judged=False),
    Arm("pd-27b", "direct", "27B, persona only (normal speed)", model=M27, runs=1,
        judged=False),
    FAST_ARM,
    DEEP_ARM,
    # As she runs: the speech recogniser resident. Resource pressure; latency only.
    Arm("pv-8b", "pipeline", "FAST with the speech recogniser resident",
        env=dict(ACT), runs=1, only=LATENCY, judged=False),
    Arm("pv-27b", "pipeline", "DEEP with the speech recogniser resident",
        env={**ACT, **DEEP}, runs=1, only=LATENCY, judged=False),
)


def order(arms) -> list[tuple[Arm, Path]]:
    """The drift arms, then FAST and DEEP alternating run by run, then the
    recogniser arms."""
    names = {arm.name for arm in arms}
    planned: list[tuple[Arm, Path]] = []
    for arm in arms:
        if arm.kind == "direct":
            planned += [(arm, ROOT / f"{arm.name}-1")]
    pair = [arm for arm in (FAST_ARM, DEEP_ARM) if arm.name in names]
    for number in range(1, fastdeep_analysis.RUNS + 1):
        planned += [(arm, ROOT / f"{arm.name}-{number}") for arm in pair]
    for arm in arms:
        if arm.kind == "pipeline" and arm not in (FAST_ARM, DEEP_ARM):
            planned += [(arm, ROOT / f"{arm.name}-1")]
    return planned


def log_gpu(moment: str) -> None:
    """Who else is on the card, and how full it is."""
    queries = (
        ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
         "--format=csv,noheader"],
        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
         "--format=csv,noheader"],
        ["ollama", "ps"],
    )
    lines = [f"== {datetime.now():%Y-%m-%d %H:%M:%S} {moment}"]
    for query in queries:
        try:
            done = subprocess.run(query, capture_output=True, text=True, check=False, timeout=30)
            lines += [line for line in done.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.TimeoutExpired) as error:
            lines.append(f"{query[0]}: {error}")
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "gpu.log").open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _complete(arm: Arm, out: Path) -> bool:
    expected = corpus.load(arm.suites or None)
    if arm.only:
        expected = [s for s in expected if s.id in arm.only]
    if not (out / "results.jsonl").exists():
        return False
    lines = (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
    return len([line for line in lines if line.strip()]) >= len(expected)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arms", nargs="*", default=None)
    parser.add_argument("--judge-only", action="store_true")
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    phase2.ROOT = ROOT
    phase2.LOG = ROOT / "progress.log"
    frozen, recorded, now = fastdeep_analysis.frozen_state()
    if not frozen:
        print(f"The analysis is not the frozen one (frozen {recorded or 'none'}, now {now}). "
              "Freeze it first: python -m evals.fastdeep_analysis --freeze")
        return 2
    phase2.log(f"analysis frozen at {recorded}")
    arms = [arm for arm in ARMS if not args.arms or arm.name in args.arms]
    planned = order(arms)
    if not args.judge_only:
        for arm, out in planned:
            if _complete(arm, out):
                phase2.log(f"generate {out.name}: already complete, skipped")
                continue
            log_gpu(f"before {out.name}")
            code = phase2.generate(arm, out)
            log_gpu(f"after {out.name}")
            if code != 0:
                phase2.log(f"stopping: {out.name} failed")
                return 1
    if not args.generate_only:
        return phase2.judge([out for arm, out in planned if arm.judged])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
