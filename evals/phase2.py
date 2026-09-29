"""Phase 2: how much of the problem is the model.

    .venv/Scripts/python.exe -m evals.phase2                 # generate every arm, then judge
    .venv/Scripts/python.exe -m evals.phase2 --judge-only
    .venv/Scripts/python.exe -m evals.phase2 --arms d-8b p-8b-soft

Each arm is one configuration of Elaina, run over the whole corpus on its
own fresh backends (evals/run.py) or, for the no-pipeline arms, straight
against the model (evals/direct.py). Every run lands in
runtime/evals/p2/<arm>-<n>/ and resumes if interrupted.

**What fits on this machine decides the arms.** The card has 16 GB.

* A words model beside the 8B decision model does not fit for either
  candidate -- the pilot measured every turn reloading both, 10 to 116
  seconds to a first token -- so each candidate runs as the only model,
  deciding and speaking. The real split (27B words, 8B decisions) runs
  once, on the explanation suite, as a latency measurement.
* The speech recogniser (large-v3-turbo, float16 on the GPU) is resident in
  the real app. Beside it the 27B filled the card to 15.6 GB and generated at
  3.3 tokens/s against 51 on an empty card. So the quality arms run with it
  off (ELAINA_STT=off; text turns never use it), and the "v-" arms measure
  the latency each model has with the voice stack loaded, on six scenarios.

Before every run all loaded models are unloaded, so no arm pays for the
one before it, and nothing else may use the GPU: judging waits until
generation is done.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals.judge import DEFAULT_JUDGE  # noqa: E402

PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
ROOT = PROJECT_ROOT / "runtime" / "evals" / "p2"
LOG = ROOT / "progress.log"

M8 = "qwen3:8b"
M8Q = "qwen3:8b-q8_0"
M27 = "hf.co/jrell/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller:latest"
SOFT_OFF = {"ELAINA_SOFT_STAGES": "off"}
NO_STT = {"ELAINA_STT": "off"}
# The latency subset: explanation, relevance, notation, Korean.
LATENCY = ("taylor_example_when_used", "hash_table_how", "cpu_cache_why",
           "time_now", "derivative_polynomial", "ko_taylor_example")


@dataclass(frozen=True)
class Arm:
    name: str
    kind: str                      # "pipeline" or "direct"
    about: str
    env: dict = field(default_factory=dict)
    model: str = ""                # direct arms
    runs: int = 2
    suites: tuple[str, ...] = ()   # empty: every suite
    only: tuple[str, ...] = ()     # a subset of scenario ids
    judged: bool = True            # False: a latency measurement only


ARMS: tuple[Arm, ...] = (
    Arm("d-8b", "direct", "qwen3:8b, persona only", model=M8),
    Arm("d-8bq8", "direct", "qwen3:8b-q8_0, persona only", model=M8Q),
    Arm("d-27b", "direct", "27B, persona only", model=M27),
    Arm("p-8b-soft", "pipeline", "today's Elaina (control)", env=dict(NO_STT)),
    Arm("p-8b-nosoft", "pipeline", "qwen3:8b, soft stages off", env={**NO_STT, **SOFT_OFF}),
    Arm("p-8bq8-soft", "pipeline", "qwen3:8b-q8_0 for everything",
        env={**NO_STT, "ELAINA_MODEL": M8Q, "ELAINA_CONVERSATION_MODEL": M8Q}),
    Arm("p-8bq8-nosoft", "pipeline", "qwen3:8b-q8_0 for everything, soft stages off",
        env={**NO_STT, "ELAINA_MODEL": M8Q, "ELAINA_CONVERSATION_MODEL": M8Q, **SOFT_OFF}),
    Arm("p-27b-soft", "pipeline", "27B for everything",
        env={**NO_STT, "ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27}),
    Arm("p-27b-nosoft", "pipeline", "27B for everything, soft stages off",
        env={**NO_STT, "ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27, **SOFT_OFF}),
    Arm("p-split27", "pipeline", "27B words, 8B decisions (latency only)",
        env={**NO_STT, "ELAINA_CONVERSATION_MODEL": M27}, runs=1, suites=("explanation",), judged=False),
    # With the voice stack loaded, as she actually runs: latency only.
    Arm("v-8b", "pipeline", "qwen3:8b, speech recogniser resident", env={}, runs=1, only=LATENCY, judged=False),
    Arm("v-8bq8", "pipeline", "qwen3:8b-q8_0, speech recogniser resident",
        env={"ELAINA_MODEL": M8Q, "ELAINA_CONVERSATION_MODEL": M8Q}, runs=1, only=LATENCY, judged=False),
    Arm("v-27b", "pipeline", "27B, speech recogniser resident",
        env={"ELAINA_MODEL": M27, "ELAINA_CONVERSATION_MODEL": M27}, runs=1, only=LATENCY, judged=False),
)


def log(line: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    ROOT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {line}\n")
    print(f"[{stamp}] {line}", flush=True)


def unload_models() -> None:
    """Stop every model Ollama holds, so the next run starts from nothing."""
    listing = subprocess.run(["ollama", "ps"], capture_output=True, text=True, check=False)
    for line in listing.stdout.splitlines()[1:]:
        name = line.split()[0] if line.split() else ""
        if name:
            subprocess.run(["ollama", "stop", name], capture_output=True, check=False)
    time.sleep(3)


def run_dirs(arms) -> list[tuple[Arm, Path]]:
    return [(arm, ROOT / f"{arm.name}-{n}") for arm in arms for n in range(1, arm.runs + 1)]


def generate(arm: Arm, out: Path) -> int:
    unload_models()
    if arm.kind == "direct":
        command = [str(PYTHON), "-m", "evals.direct", "--model", arm.model, "--out", str(out)]
    else:
        command = [str(PYTHON), "-m", "evals.run", "--out", str(out)]
        if arm.env:
            command += ["--env", *[f"{key}={value}" for key, value in arm.env.items()]]
    if arm.suites:
        command += ["--suites", *arm.suites]
    if arm.only:
        command += ["--only", ",".join(arm.only)]
    started = time.monotonic()
    log(f"generate {out.name}: {arm.about}")
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    log(f"generate {out.name}: exit {result.returncode} after {time.monotonic() - started:.0f}s")
    return result.returncode


def judge(outs: list[Path]) -> int:
    unload_models()
    started = time.monotonic()
    log(f"judge {len(outs)} run(s) with {DEFAULT_JUDGE}")
    result = subprocess.run(
        [str(PYTHON), "-m", "evals.judge", "--model", DEFAULT_JUDGE, "--run",
         *[str(out) for out in outs]],
        cwd=str(PROJECT_ROOT), check=False,
    )
    log(f"judge: exit {result.returncode} after {time.monotonic() - started:.0f}s")
    return result.returncode


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arms", nargs="*", default=None)
    parser.add_argument("--judge-only", action="store_true")
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    arms = [arm for arm in ARMS if not args.arms or arm.name in args.arms]
    outs = run_dirs(arms)
    if not args.judge_only:
        for arm, out in outs:
            if generate(arm, out) != 0:
                log(f"stopping: {out.name} failed")
                return 1
    if not args.generate_only:
        return judge([out for arm, out in outs if arm.judged])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
