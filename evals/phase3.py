"""Phase 3E: the rerun on the cleaned pipeline, against Phase 2.

    .venv/Scripts/python.exe -m evals.phase3                  # generate every arm, then judge
    .venv/Scripts/python.exe -m evals.phase3 --judge-only
    .venv/Scripts/python.exe -m evals.phase3 --arms p3-8b-soft

The matrix is docs/PHASE3_PLAN.md §6: the Phase 2 corpus, unchanged, on the
cleaned pipeline, for both models, soft stages on and off; one run of each
no-pipeline arm as a drift check (it must reproduce Phase 2); the voice
stack's latency; and the R9 follow-up suite, reported apart. Phase 2's runs
in runtime/evals/p2/ are the "before" and are not regenerated.

Every quality arm routes clock, arithmetic and conversion turns by their
own grammar (ELAINA_DOMAINS=act) and has speech recognition off; TTS is off
everywhere (evals/run.py forces it). Before every run all models are
unloaded, and judging waits until generation is done -- the same
discipline as Phase 2 (evals/phase2.py).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import phase2  # noqa: E402
from evals.phase2 import Arm, M8, M8Q, M27, NO_STT, SOFT_OFF, LATENCY  # noqa: E402

ROOT = PROJECT_ROOT / "runtime" / "evals" / "p3"
ACT = {"ELAINA_DOMAINS": "act"}

ARMS: tuple[Arm, ...] = (
    Arm("p3-8b-soft", "pipeline", "cleaned pipeline, qwen3:8b (production candidate)",
        env={**NO_STT, **ACT}),
    Arm("p3-8b-nosoft", "pipeline", "cleaned pipeline, qwen3:8b, soft stages off",
        env={**NO_STT, **ACT, **SOFT_OFF}),
    Arm("p3-27b-soft", "pipeline", "cleaned pipeline, 27B for everything",
        env={**NO_STT, **ACT, "ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27}),
    Arm("p3-27b-nosoft", "pipeline", "cleaned pipeline, 27B for everything, soft stages off",
        env={**NO_STT, **ACT, "ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27, **SOFT_OFF}),
    Arm("p3-8bq8-soft", "pipeline", "cleaned pipeline, qwen3:8b-q8_0 (optional)",
        env={**NO_STT, **ACT, "ELAINA_MODEL": M8Q, "ELAINA_CONVERSATION_MODEL": M8Q}, runs=1),
    # Drift check: nothing Phase 3 changed touches these.
    Arm("d3-8b", "direct", "qwen3:8b, persona only (drift check)", model=M8, runs=1),
    Arm("d3-27b", "direct", "27B, persona only (drift check)", model=M27, runs=1),
    # R9, reported apart from the Phase 2 corpus.
    Arm("f3-8b", "pipeline", "follow-up suite, cleaned pipeline, qwen3:8b",
        env={**NO_STT, **ACT}, suites=("followups",)),
    Arm("f3-27b", "pipeline", "follow-up suite, cleaned pipeline, 27B",
        env={**NO_STT, **ACT, "ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27},
        suites=("followups",)),
    # As she actually runs: the speech recogniser resident. Latency only.
    Arm("v3-8b", "pipeline", "cleaned pipeline, qwen3:8b, speech recogniser resident",
        env=dict(ACT), runs=1, only=LATENCY, judged=False),
    Arm("v3-27b", "pipeline", "cleaned pipeline, 27B, speech recogniser resident",
        env={**ACT, "ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27},
        runs=1, only=LATENCY, judged=False),
)

# Each Phase 3 arm and the Phase 2 arm it is read against.
BEFORE = {
    "p3-8b-soft": "p-8b-soft", "p3-8b-nosoft": "p-8b-nosoft",
    "p3-27b-soft": "p-27b-soft", "p3-27b-nosoft": "p-27b-nosoft",
    "p3-8bq8-soft": "p-8bq8-soft", "d3-8b": "d-8b", "d3-27b": "d-27b",
    "v3-8b": "v-8b", "v3-27b": "v-27b",
}


def run_dirs(arms) -> list[tuple[Arm, Path]]:
    return [(arm, ROOT / f"{arm.name}-{n}") for arm in arms for n in range(1, arm.runs + 1)]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arms", nargs="*", default=None)
    parser.add_argument("--judge-only", action="store_true")
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    # Phase 2's machinery, pointed at this phase's directory and log.
    phase2.ROOT = ROOT
    phase2.LOG = ROOT / "progress.log"
    arms = [arm for arm in ARMS if not args.arms or arm.name in args.arms]
    outs = run_dirs(arms)
    if not args.judge_only:
        for arm, out in outs:
            if (out / "results.jsonl").exists() and _complete(arm, out):
                phase2.log(f"generate {out.name}: already complete, skipped")
                continue
            if phase2.generate(arm, out) != 0:
                phase2.log(f"stopping: {out.name} failed")
                return 1
    if not args.generate_only:
        return phase2.judge([out for arm, out in outs if arm.judged])
    return 0


def _complete(arm: Arm, out: Path) -> bool:
    """Whether a run already holds every scenario it should."""
    from evals import corpus

    expected = corpus.load(arm.suites or None)
    if arm.only:
        expected = [s for s in expected if s.id in arm.only]
    lines = (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
    return len([line for line in lines if line.strip()]) >= len(expected)


if __name__ == "__main__":
    raise SystemExit(main())
