"""Of the things a person tells her about themselves, how many does she keep, and are they theirs?

Boots a real backend on an empty, isolated runtime, tells her things the way a
person says them (both languages, some inside a request), and then closes her
-- cleanly, or killed the moment the last reply lands, the way the desktop
window does it. A second boot on the same runtime finishes whatever was left,
and the database is read:

    kept      a memory holds the thing they said (its key word is in a row)
    theirs    that memory says nothing they did not (memory/faithful.py)
    not hers  how many memories were made of things that are not facts about
              the person (moods, questions, commands, things about the world)

    .venv/Scripts/python.exe scripts/live_memory_coverage_check.py --name today --shutdown kill

Speech is off (her voice is billed) and nothing touches the project's own runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

SP = Path(__file__).resolve().parent
REPO = SP.parent
for folder in (str(SP), str(REPO)):
    if folder not in sys.path:
        sys.path.insert(0, folder)

import live_memory_restart_check as restart  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = restart.OUT

# (kind, what they say, a word of it that the memory has to carry)
TELLS = [
    ("identity", "My name is Jisoo.", ["jisoo"]),
    ("identity", "I'm 24 years old.", ["24"]),
    ("identity", "I was born in Busan.", ["born"]),
    ("identity", "I moved to the US last year.", ["moved"]),
    ("identity", "나 부산에서 태어났어", ["태어"]),
    ("identity", "작년에 미국으로 이사 왔어", ["미국"]),
    ("home", "I live in Seattle.", ["seattle"]),
    ("home", "I'm staying in an apartment near Capitol Hill.", ["capitol hill"]),
    ("home", "지금 기숙사에서 지내고 있어", ["기숙사"]),
    ("work", "I work at Amazon as a backend engineer.", ["amazon"]),
    ("work", "I just got a new job at Microsoft.", ["microsoft"]),
    ("work", "I freelance as a translator.", ["translator"]),
    ("work", "나 스타트업에서 개발자로 일해", ["스타트업"]),
    ("school", "I'm a junior at UW.", ["uw"]),
    ("school", "I graduated from Yonsei in 2023.", ["yonsei"]),
    ("school", "나 컴퓨터공학 전공하고 있어", ["컴퓨터공학"]),
    ("school", "올해 대학원에 입학했어", ["대학원"]),
    ("family", "My sister lives in Busan.", ["sister"]),
    ("family", "I have two younger brothers.", ["brothers"]),
    ("family", "I'm married.", ["married"]),
    ("family", "우리 부모님은 대구에서 식당 하셔", ["대구"]),
    ("pet", "My dog's name is Kong.", ["kong"]),
    ("pet", "I adopted a cat last week.", ["adopted"]),
    ("health", "I'm allergic to peanuts.", ["peanut"]),
    ("health", "I have asthma.", ["asthma"]),
    ("health", "I'm lactose intolerant.", ["lactose"]),
    ("health", "I take medication for high blood pressure.", ["blood pressure"]),
    ("health", "나 천식이 있어", ["천식"]),
    ("health", "유당불내증이라 우유 못 마셔", ["유당불내증", "우유"]),
    ("diet", "I don't eat pork.", ["pork"]),
    ("diet", "나 매운 거 못 먹어", ["매운"]),
    ("preference", "My favorite color is blue.", ["blue"]),
    ("preference", "I can't stand horror movies.", ["horror"]),
    ("preference", "I'm a huge Mariners fan.", ["mariners"]),
    ("preference", "내가 제일 좋아하는 색은 파란색이야", ["파란"]),
    ("preference", "나는 아이스 아메리카노만 마셔", ["아메리카노"]),
    ("habit", "I usually go to the gym after work.", ["gym"]),
    ("habit", "I wake up at six every day.", ["wake"]),
    ("habit", "I play tennis on Saturdays.", ["tennis"]),
    ("habit", "주말마다 등산 가", ["등산"]),
    ("plan", "I have a job interview on the 20th.", ["interview"]),
    ("plan", "I'm getting married in June.", ["june"]),
    ("plan", "We're moving to Portland next month.", ["portland"]),
    ("plan", "다음 달에 면접 있어", ["면접"]),
    ("goal", "I'm training for a marathon.", ["marathon"]),
    ("goal", "I'm saving up for a car.", ["saving"]),
    ("goal", "I'm learning Japanese.", ["japanese"]),
    ("goal", "요즘 일본어 공부하고 있어", ["일본어"]),
    ("things", "I drive a Hyundai Ioniq.", ["ioniq"]),
    ("things", "My phone number is 206-555-0142.", ["0142"]),
    ("things", "예산은 50만 원 정도야", ["50만"]),
    ("in a request", "I'm allergic to shellfish, find me somewhere for dinner.", ["shellfish"]),
    ("in a request", "Since I work nights, what's a good time to exercise?", ["nights"]),
    ("in a request", "나 채식주의자인데 저녁 뭐 먹을까?", ["채식"]),
    ("asked to remember", "Remember that I park on level 3.", ["level 3"]),
    ("asked to remember", "내가 3층에 주차한 거 기억해 줘", ["3층"]),
]

# Not facts about the person.
NOT_TELLS = [
    "I'm tired.", "I'm so hungry right now.", "I had a rough night.",
    "What's my name?", "Do I have any allergies?",
    "What's the weather like?", "How does quantization work?",
    "The capital of France is Paris.", "That makes sense.", "Okay, thanks.",
    "오늘 좀 피곤해", "배고파", "우리 강아지 이름 뭐였지?", "오늘 날씨 어때?", "그렇구나", "고마워",
]


def rows(runtime: Path) -> tuple[list[tuple[int, str]], list[tuple[str, str, int | None]]]:
    """(memories, what was written down as said) -- the second is empty on a tree without the journal."""
    path = runtime / "database" / "memory.db"
    if not path.exists():
        return [], []
    db = sqlite3.connect(str(path))
    try:
        memories = [(int(i), str(c)) for i, c in db.execute(
            "select id, content from memories where is_active = 1 and category != 'research_evidence' order by id")]
        try:
            said = [(str(t), str(s), m) for t, s, m in db.execute("select text, state, memory_id from said order by id")]
        except sqlite3.Error:
            said = []
        return memories, said
    finally:
        db.close()


def wait_until_settled(runtime: Path, seconds: float) -> int:
    """How many sentences are still waiting once the time is up (0 on a tree without the journal)."""
    deadline = time.monotonic() + seconds
    left = 0
    while time.monotonic() < deadline:
        _memories, said = rows(runtime)
        left = sum(1 for _text, state, _memory in said if state == "waiting")
        if not left:
            return 0
        time.sleep(3)
    return left


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default=str(REPO))
    parser.add_argument("--name", default="coverage")
    parser.add_argument("--shutdown", choices=("graceful", "kill"), default="kill")
    parser.add_argument("--limit", type=int, default=0, help="only the first N things told (0: all)")
    args = parser.parse_args()
    code = Path(args.code)
    runtime = OUT / f"memcov_{args.name}"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    restart.init_schema(code, runtime)
    tells = TELLS[: args.limit] if args.limit else TELLS
    # The things that are not facts go first, so the last sentences before she is closed are real ones.
    turns = NOT_TELLS + [said for _kind, said, _keys in tells]

    print(f"######## session 1 ({args.name}: {len(tells)} things told, then {args.shutdown}) ########", flush=True)
    proc = restart.boot(code, runtime, OUT / f"memcov_{args.name}_1.log")
    try:
        asyncio.run(restart.talk(turns, shutdown=args.shutdown == "graceful"))
        if args.shutdown == "graceful":
            try:
                proc.wait(timeout=120)
            except Exception:
                print("[session 1 did not exit by itself; killed]")
    finally:
        restart.kill(proc)
    memories_before, said_before = rows(runtime)
    waiting = sum(1 for _t, state, _m in said_before if state == "waiting")
    print(f"\n--- at the moment she was closed: {len(memories_before)} memories; "
          f"{len(said_before)} sentences written down, {waiting} not yet made into anything")

    print("\n######## session 2 (fresh boot, same runtime) ########", flush=True)
    proc = restart.boot(code, runtime, OUT / f"memcov_{args.name}_2.log")
    try:
        left = wait_until_settled(runtime, 600)
    finally:
        restart.kill(proc)
    memories, said = rows(runtime)

    try:
        from memory import faithful
    except ImportError:          # a tree from before the check existed
        faithful = None
    results, used = [], set()
    by_id = dict(memories)
    # Which memory a sentence became, from the journal when the tree has one;
    # otherwise by a word of the sentence that the memory has to carry.
    became = {text: memory for text, state, memory in said if state == "kept" and memory in by_id}
    for kind, told, keys in tells:
        if said:
            memory = became.get(" ".join(told.split()))
            match = (memory, by_id[memory]) if memory is not None else None
        else:
            match = next(((i, c) for i, c in memories
                          if any(key.casefold() in c.casefold() for key in keys)), None)
        theirs = None
        if match is not None:
            used.add(match[0])
            if faithful is not None:
                theirs = not faithful.added(told, match[1])
        results.append({"kind": kind, "said": told, "kept": match is not None,
                        "memory": match[1] if match else "", "theirs": theirs})
    extra = [c for i, c in memories if i not in used]

    print("\n--- each thing told")
    for row in results:
        mark = "KEPT   " if row["kept"] else "MISSING"
        flag = "" if row["theirs"] in (True, None) else "   <-- SAYS MORE THAN THEY DID"
        print(f"  {mark} [{row['kind']}] {row['said']}\n            {row['memory']}{flag}")
    print(f"\n--- memories not matched to anything told ({len(extra)}):")
    for content in extra:
        print("   ", content)
    kept = sum(r["kept"] for r in results)
    judged = [r for r in results if r["kept"] and r["theirs"] is not None]
    print(f"\n{args.name}: kept {kept}/{len(results)}"
          + (f" | of those, theirs {sum(r['theirs'] for r in judged)}/{len(judged)}" if judged else "")
          + f" | other memories {len(extra)} (after {len(NOT_TELLS)} things that are not facts about them)"
          + f" | written down as said {len(said)} | still waiting after the second start {left}"
          + f" | memories when she was closed {len(memories_before)}, after the second start {len(memories)}")
    by_kind: dict[str, list[bool]] = {}
    for row in results:
        by_kind.setdefault(row["kind"], []).append(row["kept"])
    print("   " + ", ".join(f"{kind} {sum(v)}/{len(v)}" for kind, v in by_kind.items()))
    (OUT / f"memcov_{args.name}.json").write_text(json.dumps(
        {"results": results, "extra": extra, "said": said, "memories_before": len(memories_before)},
        ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
