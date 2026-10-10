"""Who writes an explanation thread: the 8B, or the 27B for every reply of it.

    .venv/Scripts/python.exe -m evals.thread_writer_ab --run tw1 --seeds 1 2
    .venv/Scripts/python.exe -m evals.thread_writer_ab --judge tw1 --seeds 1 2
    .venv/Scripts/python.exe -m evals.thread_writer_ab --report tw1 --seeds 1 2

The simulated-learner eval (evals/sim_learner.py), two arms that differ in one
environment variable of the backend:

    8b    the ``current`` arm as it is: ``ELAINA_MODEL=qwen3:8b`` decides and
          writes, the turn-move owner and the explanation contract off.
    27b   the same, plus ``ELAINA_CONVERSATION_MODEL=<the 27B>``: the 8B still
          routes, plans and extracts; every reply is written by the 27B
          (brain/chat_engine.py, ``active_model = self.conversation_model``).

Nothing in the repository is changed for it and nothing in config.yaml sets
the variable: it is the words-model split the engine already has. Every
conversation of this eval is one explanation thread, so "the 27B writes every
reply of the thread" is, here, the 27B writing every reply. The soft stages
run in both arms, which the 27B's earlier arms (DEEP, SPLIT, ``deep``) did
not have.

The two models do not fit on the card together (5.2 GB and 13.5 GB of weights
on 16 GB), so the 27b arm reloads a model twice a turn. ``--run`` samples the
GPU and what Ollama holds every 15 seconds beside each run, and the traces
keep each call's load time.

Runs are interleaved (8b, 27b, 27b, 8b, ...) and resumable, like the eval
itself.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import sim_learner as sl  # noqa: E402

ARM_8B, ARM_27B = "current", "thread_27b"
sl.ARMS[ARM_27B] = {**sl.ARMS[ARM_8B], "ELAINA_CONVERSATION_MODEL": sl.M27}
SHORT = {ARM_8B: "8b", ARM_27B: "27b"}


def names(prefix: str, seeds: list[int]) -> list[tuple[str, str, int]]:
    """(run name, arm, seed), in the order they are run."""
    order = []
    for index, seed in enumerate(seeds):
        arms = (ARM_8B, ARM_27B) if index % 2 == 0 else (ARM_27B, ARM_8B)
        order += [(f"{prefix}-{SHORT[arm]}-s{seed}", arm, seed) for arm in arms]
    return order


class Watch(threading.Thread):
    """The GPU and what Ollama holds, sampled beside a run."""

    def __init__(self, path: Path, every: float = 15.0) -> None:
        super().__init__(daemon=True)
        self.path, self.every, self.done = path, every, threading.Event()

    def run(self) -> None:
        while not self.done.wait(self.every):
            sample = {"at": time.time()}
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=10).stdout.strip().split(",")
                sample["gpu_mb"], sample["gpu_util"] = int(out[0]), int(out[1])
            except Exception as error:  # a sample missed is not a run failed
                sample["gpu_error"] = type(error).__name__
            try:
                with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=5) as response:
                    held = json.loads(response.read().decode("utf-8")).get("models") or []
                sample["held"] = [{"name": m.get("name"), "size": m.get("size"), "vram": m.get("size_vram")}
                                  for m in held]
            except Exception as error:
                sample["ollama_error"] = type(error).__name__
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(sample) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", default="")
    parser.add_argument("--judge", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--seeds", nargs="*", type=int, default=[1, 2])
    args = parser.parse_args()
    prefix = args.run or args.judge or args.report
    if not prefix:
        parser.print_help()
        return 0
    order = names(prefix, args.seeds)
    if args.run:
        for name, arm, seed in order:
            out = sl.ROOT / name
            if (out / "dialogues.jsonl").exists() and len(
                    (out / "dialogues.jsonl").read_text(encoding="utf-8").splitlines()) >= 9:
                print(f"{name}: already run", flush=True)
                continue
            out.mkdir(parents=True, exist_ok=True)
            watch = Watch(out / "gpu.jsonl")
            watch.start()
            started = time.monotonic()
            try:
                code = sl.run(name, arm, sl.M27, seed, [])
            finally:
                watch.done.set()
            print(f"{name}: arm {arm}, seed {seed}, exit {code}, {time.monotonic() - started:.0f}s", flush=True)
            if code:
                return code
    if args.judge:
        for name, _arm, _seed in order:
            sl.judge(name)
    if args.report:
        print(sl.report([name for name, _arm, _seed in order]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
