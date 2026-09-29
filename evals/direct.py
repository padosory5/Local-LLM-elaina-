"""The model on its own: the persona and the conversation, nothing else.

    .venv/Scripts/python.exe -m evals.direct --model qwen3:8b --out runtime/evals/p2-direct-8b-1

The no-pipeline arm of the model experiment. Each scenario's turns go to
the model with the persona file for the scenario's language as the system
prompt and the conversation so far as history -- no router, no search, no
clock, no memory, no guards. It answers one question: what does this model
write when nothing is done to it?

Two consequences worth knowing before reading its numbers:

* It has no clock and no search, so clock and date scenarios are expected
  to fail their correctness checks. That is the pipeline's value, measured.
* Its reply goes to the display unfiltered, and speech is the same text.

Results are written in evals/run.py's format, so evals.judge and
evals.report read them unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ollama  # noqa: E402

from brain.personality_loader import PersonalityLoader  # noqa: E402
from config.loader import Config  # noqa: E402
from evals import checks, corpus  # noqa: E402

# The engine answers knowledge questions at 0.1; the corpus is mostly
# knowledge questions. One setting for every turn keeps the arm simple to
# read; it is recorded in the run's meta.
TEMPERATURE = 0.1
NUM_PREDICT = 600


def _reply(client, model: str, messages: list[dict]) -> tuple[str, dict]:
    """Stream one reply; return its text and the call's measurements."""
    started = time.perf_counter()
    first = None
    parts: list[str] = []
    final = {}
    for chunk in client.chat(
        model=model, messages=messages, stream=True, think=False,
        options={"temperature": TEMPERATURE, "num_predict": NUM_PREDICT},
        keep_alive="30m",
    ):
        text = chunk["message"]["content"] or ""
        if text and first is None:
            first = time.perf_counter()
        parts.append(text)
        final = chunk
    latency = time.perf_counter() - started
    call = {
        "caller": "evals.direct",
        "model": model,
        "latency": round(latency, 4),
        "done_reason": final.get("done_reason"),
        "eval_count": final.get("eval_count"),
        "prompt_eval_count": final.get("prompt_eval_count"),
    }
    if first is not None:
        call["ttft"] = round(first - started, 4)
    if final.get("eval_count") and final.get("eval_duration"):
        call["tokens_per_second"] = round(final["eval_count"] / (final["eval_duration"] / 1e9), 1)
    return "".join(parts).strip(), call


def run_scenario(client, model: str, scenario: corpus.Scenario, persona: PersonalityLoader) -> dict:
    messages = [{"role": "system", "content": persona.load(scenario.language)}]
    turns = []
    previous = ""
    for index, turn in enumerate(scenario.turns):
        messages.append({"role": "user", "content": turn.say})
        started_at = datetime.now().isoformat(timespec="milliseconds")
        began = time.perf_counter()
        text, call = _reply(client, model, messages)
        seconds = round(time.perf_counter() - began, 2)
        messages.append({"role": "assistant", "content": text})
        trace = {
            "turn_id": f"direct-{scenario.id}-{index}",
            "started_at": started_at,
            "chain_ok": True,
            "intent": "",
            "draft": text,
            "draft_source": "model",
            "changed": [],
            "speech": [text],
            "searched": False,
            "cards": None,
            "model_calls": [call],
            "timings": {"generation": call["latency"], "ttft": call.get("ttft", 0.0)},
            "outcome": "answered",
        }
        entry = {
            "index": index, "said": turn.say, "display": text,
            "seconds": seconds, "scored": turn.score, "trace": trace,
        }
        if turn.score:
            entry["checks"] = [
                c.as_dict() for c in checks.run(turn.checks, checks.stored_turn(entry, previous))
            ]
            entry["mode"] = turn.mode
            entry["rubric"] = list(turn.rubric)
        turns.append(entry)
        previous = text
    return {
        "scenario": scenario.id, "suite": scenario.suite, "kind": scenario.kind,
        "language": scenario.language, "mode": scenario.mode, "domain": scenario.domain,
        "startup_seconds": 0.0, "records": len(turns), "turns": turns,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--suites", nargs="*", default=None, choices=corpus.ALL_SUITES)
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    scenarios = corpus.load(args.suites)
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        scenarios = [s for s in scenarios if s.id in wanted]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["scenario"])
    meta_path = out / "meta.json"
    if not meta_path.exists():
        from evals.run import _provenance
        meta = _provenance()
        meta.update({"arm": "direct", "model": args.model, "conversation_model": args.model,
                     "temperature": TEMPERATURE, "num_predict": NUM_PREDICT,
                     "pipeline": "none: persona and conversation only"})
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    client = ollama.Client(host=Config().get("llm", "ollama", "base_url"))
    persona = PersonalityLoader()
    todo = [s for s in scenarios if s.id not in done]
    print(f"{len(scenarios)} scenarios, {len(done)} already done, {len(todo)} to run.")
    for number, scenario in enumerate(todo, 1):
        result = run_scenario(client, args.model, scenario, persona)
        with results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        seconds = sum(turn["seconds"] for turn in result["turns"])
        print(f"[{number}/{len(todo)}] {scenario.suite}/{scenario.id}: {seconds:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
