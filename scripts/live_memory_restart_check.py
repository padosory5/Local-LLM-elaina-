"""Does she still know you after she is turned off and on again?

Session 1 boots a backend on an empty, isolated runtime and tells her ten
things about a person, the way a person would say them (both languages, one
inside a request). Then she is shut down -- cleanly (the desktop window's
own "shutdown" command) or killed. Session 2 boots a fresh backend on the
*same* runtime and asks about each one, plus two things she was never told,
where the right answer is that she doesn't know.

    .venv/Scripts/python.exe scripts/live_memory_restart_check.py --name today --shutdown graceful
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import websockets

SP = Path(__file__).resolve().parent
REPO = SP.parent
# Never beside the script and never in the project's own runtime/: these
# harnesses boot real backends, and their memories are not the person's.
OUT = Path(os.environ.get("ELAINA_CHECK_OUT")
           or Path(tempfile.gettempdir()) / "elaina_checks")
OUT.mkdir(parents=True, exist_ok=True)
PY = sys.executable
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TEACH = [
    "Hey, my name is Minjun Park.",
    "나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어",
    "I'm allergic to peanuts, so please keep that in mind.",
    "우리 집 강아지 이름은 콩이야",
    "My birthday is March 14th.",
    "나 수업 끝나고 보통 젠레스 존 제로 해",
    "I don't drink coffee after 2pm.",
    "내 여동생은 부산에 살아",
    "By the way I'm vegetarian. What's a quick dinner I can make tonight?",
    "다음 주 금요일에 시애틀로 돌아가",
]

# (question, any-of expected, kind)
ASK = [
    ("What's my name?", ["minjun", "민준"], "recall"),
    ("내가 무슨 전공인지 기억해?", ["computer", "컴퓨터"], "recall"),
    ("Which school do I go to?", ["washington", "워싱턴", "uw"], "recall"),
    ("Do I have any food allergies?", ["peanut", "땅콩"], "recall"),
    ("우리 강아지 이름 뭐였지?", ["콩", "kong"], "recall"),
    ("When's my birthday?", ["march 14", "3월 14", "14th of march", "march 14th"], "recall"),
    ("내가 수업 끝나고 무슨 게임 한다고 했지?", ["zenless", "젠레스", "zzz"], "recall"),
    ("Should I grab a coffee at 5pm today?", ["2pm", "2 pm", "2 p.m", "two", "2시", "cutoff",
                                              "after 2"], "recall"),
    ("내 여동생 어디 산다고 했지?", ["부산", "busan"], "recall"),
    ("Do I follow any particular diet?", ["vegetarian", "채식"], "recall"),
    ("When am I going back to Seattle?", ["friday", "금요일"], "recall"),
    ("What's my brother's name?", [], "unknown"),
    ("내가 제일 좋아하는 색깔이 뭐였지?", [], "unknown"),
]
DONT_KNOW = [
    "don't know", "do not know", "not sure", "haven't told", "didn't tell",
    "don't have", "haven't mentioned", "no record", "not something you", "you haven't",
    "모르", "말씀하신 적", "들은 적", "기억에 없", "알려주신 적", "없습니다", "모릅니다",
    "don't have access", "haven't shared", "not provided", "haven't said",
    "기억이 나지 않", "정보가 없", "말씀해 주신 적", "알려 주신 적",
]


def port_open() -> bool:
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", 8765)) == 0
    finally:
        s.close()


def boot(code: Path, runtime: Path, log: Path) -> subprocess.Popen:
    if port_open():
        raise SystemExit("port 8765 is already in use")
    # Silent unless the environment says otherwise; her voice is billed.
    env = {"ELAINA_TTS": "off", **os.environ}
    env.update(ELAINA_OPEN_DESKTOP="0", PYTHONUNBUFFERED="1",
               PYTHONIOENCODING="utf-8", ELAINA_RUNTIME_ROOT=str(runtime))
    proc = subprocess.Popen([PY, "main.py"], cwd=str(code), env=env,
                            stdout=open(log, "w", encoding="utf-8"),
                            stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 400
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"backend exited early; see {log}")
        if "Listening on ws://" in log.read_text(encoding="utf-8", errors="replace"):
            time.sleep(3)
            return proc
        time.sleep(2)
    raise SystemExit("backend never became ready")


def kill(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    for _ in range(30):
        if not port_open():
            return
        time.sleep(1)


async def talk(turns, *, shutdown: bool = False, timeout: float = 150.0):
    replies = []
    async with websockets.connect("ws://127.0.0.1:8765", max_size=None) as ws:
        await ws.send(json.dumps({"command": "set_input_mode", "mode": "text"}))
        await ws.send(json.dumps({"command": "set_computer_control_mode", "enabled": False}))
        await asyncio.sleep(1.0)
        for said in turns:
            started = time.perf_counter()
            await ws.send(json.dumps({"command": "send_text_message", "text": said}))
            reply = None
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if payload.get("event") == "assistant_finished":
                    reply = str(payload.get("text", ""))
                    break
            replies.append(reply)
            print(f"\nYOU   : {said}\nELAINA [{time.perf_counter() - started:.1f}s]: {reply}", flush=True)
        if shutdown:
            await ws.send(json.dumps({"command": "shutdown"}))
            await asyncio.sleep(0.5)
    return replies


def personal_rows(runtime: Path) -> list[str]:
    path = runtime / "database" / "memory.db"
    if not path.exists():
        return ["(no memory.db)"]
    db = sqlite3.connect(str(path))
    try:
        return [f"[{c}] {t}" for c, t in db.execute(
            "select category, content from memories where is_active = 1 "
            "and category != 'research_evidence' order by id")]
    except sqlite3.Error as error:
        return [f"(unreadable: {error})"]
    finally:
        db.close()


def init_schema(code: Path, runtime: Path) -> None:
    """The tables an existing install already has (a fresh database never
    got them -- the bug the first run of this found)."""
    env = dict(os.environ, ELAINA_RUNTIME_ROOT=str(runtime), PYTHONIOENCODING="utf-8")
    subprocess.run(
        [PY, "-c", "from memory.database import Base, engine; import memory.models; "
                   "Base.metadata.create_all(engine)"],
        cwd=str(code), env=env, check=True, capture_output=True,
    )


def score(question, expected, kind, reply) -> bool:
    text = str(reply or "").casefold()
    if kind == "unknown":
        return any(marker in text for marker in DONT_KNOW)
    return any(token.casefold() in text for token in expected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default=str(REPO),
                            help="the tree to boot; a frozen snapshot, or this one")
    parser.add_argument("--name", default="live",
                        help="names this run's runtime and log files")
    parser.add_argument("--shutdown", choices=("graceful", "kill"), default="graceful")
    args = parser.parse_args()
    code = Path(args.code)
    runtime = OUT / f"memrun_{args.name}"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    init_schema(code, runtime)

    print(f"######## session 1 ({args.name}, then {args.shutdown}) ########", flush=True)
    proc = boot(code, runtime, OUT / f"memrun_{args.name}_1.log")
    try:
        asyncio.run(talk(TEACH, shutdown=args.shutdown == "graceful"))
        if args.shutdown == "graceful":
            try:
                proc.wait(timeout=90)
                print(f"\n[session 1 exited by itself, code {proc.returncode}]")
            except subprocess.TimeoutExpired:
                print("\n[session 1 did not exit within 90 s of shutdown; killed]")
    finally:
        kill(proc)

    rows = personal_rows(runtime)
    print(f"\n--- what she kept ({len(rows)}):")
    for row in rows:
        print("  ", row)

    print(f"\n######## session 2 (fresh boot, same runtime) ########", flush=True)
    proc = boot(code, runtime, OUT / f"memrun_{args.name}_2.log")
    try:
        replies = asyncio.run(talk([q for q, _, _ in ASK]))
    finally:
        kill(proc)

    results = []
    for (question, expected, kind), reply in zip(ASK, replies):
        ok = score(question, expected, kind, reply)
        results.append({"question": question, "kind": kind, "reply": reply, "ok": ok})
    print("\n--- scored")
    for row in results:
        print(f"  {'PASS' if row['ok'] else 'FAIL'} [{row['kind']}] {row['question']}")
    passed = sum(r["ok"] for r in results)
    print(f"\n{args.name}: {passed}/{len(results)} "
          f"(recall {sum(r['ok'] for r in results if r['kind'] == 'recall')}/"
          f"{sum(r['kind'] == 'recall' for r in results)}, "
          f"unknown {sum(r['ok'] for r in results if r['kind'] == 'unknown')}/"
          f"{sum(r['kind'] == 'unknown' for r in results)})")
    (OUT / f"memrun_{args.name}.json").write_text(
        json.dumps({"kept": rows, "results": results}, ensure_ascii=False, indent=1),
        encoding="utf-8")


if __name__ == "__main__":
    main()
