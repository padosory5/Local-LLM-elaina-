"""Who writes an explanation thread: the 8B, the 27B behind an 8B router, or the 27B alone.

    .venv/Scripts/python.exe -m evals.thread_writer_ab --run tw2 --seeds 3 4 5
    .venv/Scripts/python.exe -m evals.thread_writer_ab --run tw2x --seeds 3 --learner 35b
    .venv/Scripts/python.exe -m evals.thread_writer_ab --judge tw2 --seeds 3 4 5
    .venv/Scripts/python.exe -m evals.thread_writer_ab --report tw2 --seeds 3 4 5

The simulated-learner eval (evals/sim_learner.py), in arms that differ only in
which model the backend is told to use:

    8b     the ``current`` arm as it is: ``ELAINA_MODEL=qwen3:8b`` decides and
           writes, the turn-move owner and the explanation contract off.
    split  the same, plus ``ELAINA_CONVERSATION_MODEL=<the 27B>``: the 8B still
           routes, plans and extracts; every reply is written by the 27B
           (brain/chat_engine.py, ``self.conversation_model``).
    all27  ``ELAINA_MODEL`` and ``ELAINA_CONVERSATION_MODEL`` both the 27B: one
           model for everything, so nothing is swapped inside a turn.

Nothing in the repository is changed for it and nothing in config.yaml sets
these variables: they are the model settings the engine already reads. Every
conversation of this eval is one explanation thread, so "the 27B writes every
reply of the thread" is, here, the 27B writing every reply. The soft stages
run in every arm.

The first run of this (``tw1``, 2026-10-09, seeds 1 and 2) had the first two
arms only and named them ``8b`` and ``27b``; its ``27b`` is ``split`` here.

``--learner`` is who plays the person. The eval's own is the 27B, which in the
``split`` and ``all27`` arms is also the writer; ``35b`` is the judge's model,
for a run in which the learner is neither arm's writer.

The 8B and the 27B do not fit on the card together (5.2 GB and 13.5 GB of
weights on 16 GB), so the ``split`` arm reloads a model twice a turn. ``--run``
samples the GPU and what Ollama holds every 15 seconds beside each run, and
the traces keep each call's load time.

Runs are interleaved, the order of the arms rotating from seed to seed, and
resumable, like the eval itself.
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
from evals.judge import DEFAULT_JUDGE  # noqa: E402

# Short name -> the arm in ``sl.ARMS`` a run's meta.json records.
ARMS = {"8b": "current", "split": "thread_27b", "all27": "thread_all27",
        "person": "voice_person"}
sl.ARMS["thread_27b"] = {**sl.ARMS["current"], "ELAINA_CONVERSATION_MODEL": sl.M27}
sl.ARMS["thread_all27"] = {**sl.ARMS["current"], "ELAINA_MODEL": sl.M27,
                           "ELAINA_CONVERSATION_MODEL": sl.M27}
LEARNERS = {"27b": sl.M27, "35b": DEFAULT_JUDGE}


def names(prefix: str, seeds: list[int], arms: list[str]) -> list[tuple[str, str, int]]:
    """(run name, arm, seed), in the order they are run."""
    order = []
    for index, seed in enumerate(seeds):
        turn = index % len(arms)
        order += [(f"{prefix}-{arm}-s{seed}", arm, seed) for arm in arms[turn:] + arms[:turn]]
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
    parser.add_argument("--seeds", nargs="*", type=int, default=[3, 4, 5])
    parser.add_argument("--arms", nargs="*", choices=sorted(ARMS), default=["8b", "split", "all27"])
    parser.add_argument("--learner", default="27b",
                        help="27b (the eval's own), 35b (the judge's model), or a model name")
    parser.add_argument("--only", nargs="*", default=[], help="learner ids, for a smoke test")
    args = parser.parse_args()
    prefix = args.run or args.judge or args.report
    if not prefix:
        parser.print_help()
        return 0
    order = names(prefix, args.seeds, list(args.arms))
    learner_model = LEARNERS.get(args.learner, args.learner)
    if args.run:
        wanted = len(args.only) or len(sl.load_learners()[0])
        for name, arm, seed in order:
            out = sl.ROOT / name
            if (out / "dialogues.jsonl").exists() and len(
                    (out / "dialogues.jsonl").read_text(encoding="utf-8").splitlines()) >= wanted:
                print(f"{name}: already run", flush=True)
                continue
            out.mkdir(parents=True, exist_ok=True)
            watch = Watch(out / "gpu.jsonl")
            watch.start()
            started = time.monotonic()
            try:
                code = sl.run(name, ARMS[arm], learner_model, seed, list(args.only))
            finally:
                watch.done.set()
            print(f"{name}: arm {arm}, learner {args.learner}, seed {seed}, exit {code}, "
                  f"{time.monotonic() - started:.0f}s", flush=True)
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
