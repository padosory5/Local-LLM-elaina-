"""Drive every scenario through a fresh backend, and keep what happened.

    .venv/Scripts/python.exe -m evals.run --out runtime/evals/baseline-1
    .venv/Scripts/python.exe -m evals.run --out runtime/evals/try --suites relevance
    .venv/Scripts/python.exe -m evals.run --out runtime/evals/try --only time_now,taylor_example_when_used

Each scenario gets its own backend and its own runtime directory, so no
scenario inherits another's conversation, memory or open task -- the
contamination a single long session would add is measured by the
trajectories, on purpose, and nowhere else. The backend is the real one
(main.py, driven over its WebSocket like the desktop window), with speech
silenced: a run never reaches ElevenLabs, and refuses to start if the log
does not say so.

Per scenario the run keeps the replies, the deterministic check results for
each scored turn, a summary of each turn's record from core/turn_trace.py
(route, evidence, draft, the stages that changed it, speech, model calls,
timings), the full records, and the backend log. Rubric properties are
judged afterwards by ``evals.judge``; ``evals.report`` aggregates runs. An
interrupted run resumes where it stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# Measuring her decisions never needs her voice, and the voice is billed.
os.environ.setdefault("ELAINA_TTS", "off")

try:
    import websockets
except ImportError:  # pragma: no cover - operator-facing message
    raise SystemExit("pip install websockets to run the evaluations")

from config.loader import Config  # noqa: E402
from evals import checks, corpus  # noqa: E402


URL = "ws://127.0.0.1:8765"
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
READY_MARK = "[Lifecycle] READY"
SILENT_MARK = "[TTS] Speech is off"
START_TIMEOUT = 240.0


# ------------------------------------------------------------------ backend


def port_open() -> bool:
    probe = socket.socket()
    probe.settimeout(1)
    try:
        return probe.connect_ex(("127.0.0.1", 8765)) == 0
    finally:
        probe.close()


def start_backend(runtime: Path, log: Path, arm_env: dict | None = None) -> subprocess.Popen:
    runtime.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {
        # Silent first, and not overridable from here: an evaluation that
        # spoke would spend credits on every one of hundreds of turns.
        **os.environ,
        # The arm's own settings (a words model, soft stages off) --
        # applied before the ones below, so none of them can turn speech on.
        **(arm_env or {}),
        "ELAINA_TTS": "off",
        "ELAINA_OPEN_DESKTOP": "0",
        "ELAINA_RUNTIME_ROOT": str(runtime),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    handle = log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PYTHON), "-u", "main.py"], cwd=str(PROJECT_ROOT), env=env,
        stdout=handle, stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        text = log.read_text(encoding="utf-8", errors="replace")
        if READY_MARK in text:
            if SILENT_MARK not in text:
                stop_backend(process)
                raise SystemExit(
                    "the backend did not confirm silent speech; stopped it "
                    "before any turn could reach a voice provider"
                )
            return process
        if process.poll() is not None:
            raise SystemExit(f"the backend exited before it was ready; see {log}")
        time.sleep(1.0)
    stop_backend(process)
    raise SystemExit(f"the backend was not ready within {START_TIMEOUT:.0f}s; see {log}")


def stop_backend(process: subprocess.Popen) -> None:
    """Stop this backend and its children -- only this one."""
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True,
    )
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    # The port is released a moment after the process goes.
    deadline = time.monotonic() + 15
    while port_open() and time.monotonic() < deadline:
        time.sleep(0.5)


# -------------------------------------------------------------------- turns


async def _drive(turns, timeout: float) -> list[dict]:
    replies: list[dict] = []
    async with websockets.connect(URL, max_size=None) as connection:
        await connection.send(json.dumps({"command": "set_input_mode", "mode": "text"}))
        # Hands off the machine. An evaluation asks questions; a model under
        # test that misroutes one into an action must not be able to move
        # the person's mouse or open their apps while it happens.
        await connection.send(json.dumps({
            "command": "set_computer_control_mode", "enabled": False,
        }))
        await asyncio.sleep(1.0)
        for turn in turns:
            started = time.monotonic()
            await connection.send(json.dumps({"command": "send_text_message", "text": turn.say}))
            text = None
            deadline = started + timeout
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(
                        connection.recv(), timeout=max(0.1, deadline - time.monotonic()),
                    )
                except asyncio.TimeoutError:
                    break
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if payload.get("event") == "assistant_finished":
                    text = str(payload.get("text", ""))
                    break
            replies.append({
                "said": turn.say,
                "display": text,
                "seconds": round(time.monotonic() - started, 2),
            })
    return replies


def read_traces(runtime: Path) -> list[dict]:
    records = []
    for path in sorted((runtime / "turn_trace").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records.sort(key=lambda record: record.get("started_at", ""))
    return records


def _wait_for_records(runtime: Path, count: int, timeout: float = 20.0) -> list[dict]:
    """The records are written just after each reply; wait for the last one."""
    deadline = time.monotonic() + timeout
    records = read_traces(runtime)
    while len(records) < count and time.monotonic() < deadline:
        time.sleep(0.5)
        records = read_traces(runtime)
    return records


def searched(record: dict) -> bool:
    evidence = record.get("evidence") or {}
    if evidence.get("search_queries"):
        return True
    if "web_search" in (record.get("timings") or {}):
        return True
    return any(
        "research" in str(call.get("caller", "")) or "web_search" in str(call.get("caller", ""))
        for call in record.get("model_calls") or ()
    )


def summarize(record: dict) -> dict:
    """The parts of a turn record a reader of the results needs first."""
    context = record.get("context") or {}
    route = context.get("route") if isinstance(context.get("route"), dict) else {}
    evidence = record.get("evidence") or {}
    return {
        "turn_id": record.get("turn_id"),
        "started_at": record.get("started_at"),
        "chain_ok": record.get("chain_ok"),
        "intent": route.get("intent", ""),
        "normalized": route.get("normalized_request", ""),
        "detailed": route.get("detailed_response"),
        "act": context.get("act", ""),
        "limits": context.get("limits"),
        "searched": searched(record),
        "search_queries": evidence.get("search_queries"),
        "clock": evidence.get("clock"),
        "cards": (context.get("surface") or {}).get("items"),
        "draft": (record.get("draft") or {}).get("text", ""),
        "draft_source": (record.get("draft") or {}).get("source", ""),
        "changed": [
            {key: step.get(key) for key in ("name", "before", "after", "log") if step.get(key)}
            for step in record.get("steps") or () if step.get("changed")
        ],
        "speech": [entry.get("spoken", "") for entry in record.get("speech") or ()],
        "model_calls": [
            {key: call.get(key) for key in (
                "caller", "model", "latency", "ttft", "tokens_per_second",
                "eval_count", "prompt_eval_count", "done_reason", "error",
            ) if call.get(key) is not None}
            for call in record.get("model_calls") or ()
        ],
        "timings": record.get("timings"),
        "outcome": record.get("outcome"),
    }


# ----------------------------------------------------------------- scenario


def _resources() -> dict:
    """What the models occupy right now: Ollama's own view, and the card's.

    Taken while the scenario's backend is still up, so it shows the models
    the arm actually used -- and, for an arm whose models do not fit
    together, the share of one that Ollama pushed onto the CPU.
    """
    found: dict = {"ollama_ps": [], "vram_used_mib": None}
    try:
        lines = subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True, check=False, timeout=20,
        ).stdout.splitlines()[1:]
        for line in lines:
            columns = [c for c in line.split("  ") if c.strip()]
            if len(columns) >= 4:
                found["ollama_ps"].append({
                    "name": columns[0].strip(), "size": columns[2].strip(),
                    "processor": columns[3].strip(),
                })
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        used = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=False, timeout=20,
        ).stdout.strip().splitlines()
        found["vram_used_mib"] = int(used[0]) if used else None
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return found


def run_scenario(scenario: corpus.Scenario, out: Path, timeout: float,
                 arm_env: dict | None = None) -> dict:
    runtime = out / "rt" / scenario.id
    if runtime.exists():
        shutil.rmtree(runtime, ignore_errors=True)
    log = out / "logs" / f"{scenario.id}.log"
    started = time.monotonic()
    process = start_backend(runtime, log, arm_env)
    startup = round(time.monotonic() - started, 1)
    try:
        replies = asyncio.run(_drive(scenario.turns, timeout))
        records = _wait_for_records(runtime, len(scenario.turns))
        resources = _resources()
    finally:
        stop_backend(process)

    traces_out = out / "traces" / f"{scenario.id}.jsonl"
    traces_out.parent.mkdir(parents=True, exist_ok=True)
    traces_out.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    shutil.rmtree(runtime, ignore_errors=True)

    # One record per turn, in order. Matched by what was said, so a record
    # the backend wrote for anything else cannot shift the alignment.
    remaining = list(records)
    turns_out = []
    previous_reply = ""
    for index, (turn, reply) in enumerate(zip(scenario.turns, replies)):
        record = next(
            (r for r in remaining if str(r.get("user_input", "")).strip() == turn.say.strip()),
            None,
        )
        if record is not None:
            remaining.remove(record)
        summary = summarize(record) if record else {}
        display = reply["display"]
        entry = {
            "index": index,
            "said": turn.say,
            "display": display,
            "seconds": reply["seconds"],
            "scored": turn.score,
            "trace": summary,
        }
        if turn.score:
            if display is None:
                entry["checks"] = [{"check": "reply", "passed": False,
                                    "detail": f"no reply within {timeout:.0f}s"}]
            else:
                entry["checks"] = [
                    c.as_dict() for c in checks.run(
                        turn.checks, checks.stored_turn(entry, previous_reply),
                    )
                ]
            entry["mode"] = turn.mode
            entry["rubric"] = list(turn.rubric)
        turns_out.append(entry)
        previous_reply = display or ""

    return {
        "scenario": scenario.id,
        "suite": scenario.suite,
        "kind": scenario.kind,
        "language": scenario.language,
        "mode": scenario.mode,
        "domain": scenario.domain,
        "startup_seconds": startup,
        "records": len(records),
        "resources": resources,
        "turns": turns_out,
    }


def _provenance() -> dict:
    def git(*args) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=str(PROJECT_ROOT), capture_output=True,
                text=True, check=False,
            ).stdout.strip()
        except OSError:
            return ""
    config = Config()
    try:
        # The model runtime is part of the result: Ollama updates itself,
        # and it did so between the baseline's generation and its judging.
        ollama_version = subprocess.run(
            ["ollama", "--version"], capture_output=True, text=True, check=False,
        ).stdout.strip().rsplit(" ", 1)[-1]
    except OSError:
        ollama_version = ""
    return {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "commit": git("rev-parse", "--short", "HEAD"),
        "uncommitted_changes": bool(git("status", "--porcelain")),
        "ollama": ollama_version,
        "model": config.get("llm", "ollama", "model", default="", required=False),
        "conversation_model": config.get(
            "llm", "ollama", "conversation_model", default="", required=False,
        ) or "(same)",
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="a directory for this run")
    parser.add_argument("--suites", nargs="*", default=None, choices=corpus.ALL_SUITES)
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--env", nargs="*", default=[],
                        help="the arm's settings, as KEY=VALUE (ELAINA_* only)")
    args = parser.parse_args()
    arm_env = {}
    for setting in args.env:
        key, _, value = setting.partition("=")
        if not key.startswith("ELAINA_") or key == "ELAINA_TTS":
            parser.error(f"not an arm setting: {key}")
        arm_env[key] = value

    problems = corpus.problems()
    if problems:
        print("The corpus has problems:\n  " + "\n  ".join(problems))
        return 1
    scenarios = corpus.load(args.suites)
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        scenarios = [s for s in scenarios if s.id in wanted]
    if port_open():
        print("Port 8765 is in use -- is Elaina already running? Close it first.")
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / "results.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["scenario"])
            except (json.JSONDecodeError, KeyError):
                continue
    meta_path = out / "meta.json"
    if not meta_path.exists():
        meta = _provenance()
        meta["arm_env"] = arm_env
        if arm_env.get("ELAINA_CONVERSATION_MODEL"):
            meta["conversation_model"] = arm_env["ELAINA_CONVERSATION_MODEL"]
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    todo = [s for s in scenarios if s.id not in done]
    print(f"{len(scenarios)} scenarios, {len(done)} already done, {len(todo)} to run.")
    for number, scenario in enumerate(todo, 1):
        result = run_scenario(scenario, out, args.timeout, arm_env)
        with results_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        scored = [turn for turn in result["turns"] if turn["scored"]]
        passed = sum(all(c["passed"] for c in turn.get("checks", ())) for turn in scored)
        seconds = sum(turn["seconds"] for turn in result["turns"])
        print(f"[{number}/{len(todo)}] {scenario.suite}/{scenario.id}: "
              f"{passed}/{len(scored)} scored turns pass their checks, "
              f"{seconds:.1f}s of turns (+{result['startup_seconds']}s start)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
