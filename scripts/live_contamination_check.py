"""Drive the real Elaina through turns that must not inherit their history.

A3's instrument. Routing accuracy and grounding are both already measured,
and neither of them catches the fault this looks for: a *correct answer to
the previous question*. The router classified "what's 2+2" as a calculation
and "얼마나 우려야 돼?" as a steeping time, both correctly, and the reply
came back about sleep and about London.

Each case restarts the backend. Conversational state lives in the running
ChatEngine, so running two cases in one process would let the first
contaminate the second -- which is the very thing being measured, and would
turn a real result into an unreadable one.

Usage::

    .venv/Scripts/python.exe scripts/live_contamination_check.py
    .venv/Scripts/python.exe scripts/live_contamination_check.py --case steep_time_after_coffee
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

try:
    import websockets
except ImportError:  # pragma: no cover - operator-facing message
    raise SystemExit("pip install websockets to run this check")

MATRIX = PROJECT_ROOT / "tests" / "contamination_matrix.json"
BACKEND_LOG = PROJECT_ROOT / "runtime" / "contamination_backend.log"
# --backend-log points this somewhere else. The backend's own stdout is
# the only place the answer path narrates itself ("[Style]", "[Response
# Guard]"), and the default path both lands in the person's runtime/ and
# is truncated by the next case.
# ELAINA_WS_PORT keeps a verification run off the default channel when
# another backend already holds it.
PORT = os.environ.get("ELAINA_WS_PORT") or "8765"
URL = f"ws://127.0.0.1:{PORT}"


def load_cases(only: str = "") -> list[dict]:
    cases = json.loads(MATRIX.read_text(encoding="utf-8"))["cases"]
    return [case for case in cases if not only or case["id"] == only]


# --------------------------------------------------------------- the backend


_BACKEND: subprocess.Popen | None = None
_LOG_IN_USE = BACKEND_LOG


def stop_backend() -> None:
    """Stop the backend this script started -- and only that one.

    It used to stop every python.exe whose command line mentioned main.py,
    which on a machine where the person has Elaina open is their Elaina.
    """
    global _BACKEND
    process, _BACKEND = _BACKEND, None
    if process is None:
        return
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True,
    )
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def _fresh_runtime() -> dict[str, str]:
    """A runtime folder of its own for this case, when ELAINA_RUNTIME_BASE
    names where to make them -- so no case reads another's memory, and
    nothing reaches the person's own runtime/."""
    base = os.environ.get("ELAINA_RUNTIME_BASE", "").strip()
    if not base:
        return {}
    folder = Path(base) / f"case-{time.time_ns()}"
    folder.mkdir(parents=True, exist_ok=True)
    return {"ELAINA_RUNTIME_ROOT": str(folder)}


def backend_log_path(tag: str = "") -> Path:
    """Where this case's backend stdout goes."""
    if not tag:
        return BACKEND_LOG
    return BACKEND_LOG.with_name(f"{BACKEND_LOG.stem}-{tag}{BACKEND_LOG.suffix}")


def start_backend(tag: str = "") -> subprocess.Popen:
    global _BACKEND, _LOG_IN_USE
    _LOG_IN_USE = backend_log_path(tag)
    _LOG_IN_USE.parent.mkdir(parents=True, exist_ok=True)
    handle = _LOG_IN_USE.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PROJECT_ROOT / ".venv/Scripts/python.exe"), "-u", "main.py"],
        cwd=str(PROJECT_ROOT),
        stdout=handle, stderr=subprocess.STDOUT,
        # Silent unless the environment says otherwise: the matrix measures
        # what she says, and her voice is billed per character.
        env={"ELAINA_TTS": "off", **os.environ, "ELAINA_OPEN_DESKTOP": "0",
             **_fresh_runtime()},
    )
    _BACKEND = process
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        text = _LOG_IN_USE.read_text(encoding="utf-8", errors="replace")
        if "Lifecycle] READY" in text:
            return process
        if process.poll() is not None:
            raise SystemExit("the backend exited before it was ready")
        time.sleep(2)
    raise SystemExit("the backend did not become ready within 180s")


# ------------------------------------------------------------------ one case


async def run_case(case: dict, timeout: float) -> dict:
    said: list[str] = []
    async with websockets.connect(URL, max_size=None) as socket:
        await socket.send(json.dumps({"command": "set_input_mode",
                                      "mode": "text"}))
        await asyncio.sleep(1.0)
        for message in case.get("setup", ()):
            await socket.send(json.dumps({"command": "send_text_message",
                                          "text": message}))
            await _await_reply(socket, timeout)
        await socket.send(json.dumps({"command": "send_text_message",
                                      "text": case["turn"]}))
        reply = await _await_reply(socket, timeout) or ""
        said.append(reply)

    folded = reply.casefold()
    leaked = [
        marker for marker in case.get("must_not_mention", ())
        if marker.casefold() in folded
    ]
    wanted = case.get("must_mention", ())
    answered = (not wanted) or any(
        marker.casefold() in folded for marker in wanted
    )
    return {
        "id": case["id"],
        "turn": case["turn"],
        "reply": reply,
        "leaked": leaked,
        "answered": answered,
        "passed": not leaked and answered,
        "why": case.get("why", ""),
    }


async def _await_reply(socket, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if payload.get("event") == "assistant_finished":
            return str(payload.get("text", ""))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--out", default="runtime/contamination_result.json")
    parser.add_argument(
        "--backend-log", default="",
        help="where to write the backend's stdout; one file per case, the "
             "case id appended when more than one case runs",
    )
    args = parser.parse_args()

    global BACKEND_LOG
    if args.backend_log:
        BACKEND_LOG = Path(args.backend_log)

    cases = load_cases(args.case)
    if not cases:
        raise SystemExit(f"no case named {args.case!r}")

    results = []
    try:
        for index, case in enumerate(cases, start=1):
            print(f"\n{'=' * 72}\n[{index}/{len(cases)}] {case['id']}")
            print(f"  setup: {case.get('setup') or '(none)'}")
            print(f"  turn : {case['turn']}")
            stop_backend()
            time.sleep(2)
            process = start_backend(case["id"] if len(cases) > 1 else "")
            print(f"  log  : {backend_log_path(case['id'] if len(cases) > 1 else '')}")
            try:
                result = asyncio.run(run_case(case, args.timeout))
            finally:
                process.terminate()
                stop_backend()
            results.append(result)
            mark = "PASS" if result["passed"] else "FAIL"
            print(f"  reply: {result['reply'][:150]}")
            print(f"  {mark}"
                  + (f"  inherited {result['leaked']}" if result["leaked"] else "")
                  + ("" if result["answered"] else "  did not answer the turn"))
    finally:
        stop_backend()

    passed = sum(1 for result in results if result["passed"])
    print(f"\n{'=' * 72}")
    print(f"{passed}/{len(results)} turns answered without inheriting.")
    for result in results:
        if not result["passed"]:
            print(f"\n  {result['id']}")
            print(f"    turn : {result['turn']}")
            print(f"    reply: {result['reply'][:180]}")
            print(f"    why  : {result['why']}")

    if args.out:
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps({"passed": passed, "total": len(results),
                        "cases": results}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nWrote {destination}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
