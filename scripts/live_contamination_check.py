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
URL = "ws://127.0.0.1:8765"


def load_cases(only: str = "") -> list[dict]:
    cases = json.loads(MATRIX.read_text(encoding="utf-8"))["cases"]
    return [case for case in cases if not only or case["id"] == only]


# --------------------------------------------------------------- the backend


def stop_backend() -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*main.py*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True,
    )


def start_backend() -> subprocess.Popen:
    BACKEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    handle = BACKEND_LOG.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PROJECT_ROOT / ".venv/Scripts/python.exe"), "-u", "main.py"],
        cwd=str(PROJECT_ROOT),
        stdout=handle, stderr=subprocess.STDOUT,
        env={**__import__("os").environ, "ELAINA_OPEN_DESKTOP": "0"},
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        text = BACKEND_LOG.read_text(encoding="utf-8", errors="replace")
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
    args = parser.parse_args()

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
            process = start_backend()
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
