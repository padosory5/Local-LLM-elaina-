"""Does she understand what was actually said -- and catch what was wrong?

Two ordinary conversations (one mostly English, one mostly Korean), each on
a fresh isolated backend, with cases following one another as a real
conversation drifts. Four kinds:

  fact     they tell her something about themselves; she must take it as a
           fact (not an event happening now, not a question to echo back)
  premise  they say something false while asking; she must correct it
  correct  she answered one reading, they say which they meant; she must
           switch to it
  vague    a reference with nothing to refer to; she must ask

Each case has an automatic check. Every reply is also printed, because the
number that counts is the one read by a person.

    .venv/Scripts/python.exe scripts/live_misread_check.py --name today
"""
from __future__ import annotations

import argparse
import os
import asyncio
import json
import sys
import tempfile
from pathlib import Path

SP = Path(__file__).resolve().parent
REPO = SP.parent
# Never beside the script and never in the project's own runtime/: these
# harnesses boot real backends, and their memories are not the person's.
OUT = Path(os.environ.get("ELAINA_CHECK_OUT")
           or Path(tempfile.gettempdir()) / "elaina_checks")
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SP))
from live_memory_restart_check import boot, kill, talk  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def has_any(*needles):
    return lambda reply: any(n.casefold() in reply.casefold() for n in needles)


def has_none(*needles):
    return lambda reply: not any(n.casefold() in reply.casefold() for n in needles)


def both(*checks):
    return lambda reply: all(check(reply) for check in checks)


def has_re(pattern):
    import re
    return lambda reply: bool(re.search(pattern, reply, re.IGNORECASE))


def has_number(reply):
    import re
    return bool(re.search(r"\d", reply))


ASKS = has_any("what", "which", "무엇", "어떤", "뭘", "무슨", "?")

# (id, kind, turns, check on the last reply)
ENGLISH = [
    ("vague_open_it", "vague", ["Open it."], ASKS),
    ("fact_birthday", "fact", ["My birthday is March 14th."],
     has_none("happy birthday", "enjoy the celebration", "celebrat")),
    ("fact_sister_married", "fact", ["My sister got married last year."],
     has_none("on your wedding", "you got married", "your own wedding")),
    ("fact_bakery", "fact", ["I work part-time at a bakery on weekends."],
     has_none("congratulations on the new job", "new job")),
    ("premise_capital", "premise", ["Why is Seattle the capital of Washington state?"],
     has_any("olympia")),
    ("premise_great_wall", "premise",
     ["The Great Wall of China is visible from space with the naked eye, right?"],
     # Tightened: "can be seen from space ... though it's not always
     # visible" passed the first version on its "not".
     both(has_any("not visible", "isn't visible", "can't be seen", "cannot be seen",
                  "can't see", "cannot see", "not be seen", "myth", "misconception",
                  "magnification", "without aid", "too narrow"),
          has_none("can be seen from space", "visible from orbit", "is visible from space",
                   "can be seen with the naked eye"))),
    ("premise_tokyo", "premise", ["Tokyo is the capital of China, right?"],
     has_any("beijing", "not the capital", "isn't the capital", "japan")),
    ("premise_bainbridge", "premise",
     ["Since there's a casino on Bainbridge Island, which one should I go to?"],
     has_any("no casino", "isn't a casino", "not a casino", "doesn't have a casino",
             "don't have a casino", "there are no casinos", "no casinos")),
    ("correct_portland", "correct",
     ["What's the population of Portland?", "No, I meant Portland, Maine."],
     both(has_any("maine"), has_number)),
    ("correct_series", "correct",
     ["Recommend a movie for tonight.", "No, I meant a TV series, not a movie."],
     has_any("series", "show", "season")),
]

KOREAN = [
    ("vague_how_long", "vague", ["그거 얼마나 걸려?"], ASKS),
    ("fact_game_habit", "fact", ["나 수업 끝나고 보통 젠레스 존 제로 해"],
     has_none("하셨나요", "힘들었겠", "하셨어요?")),
    # Tightened: "일정이 궁금하시다면 알려주시면 도와드리겠습니다" passed --
    # nothing in it shows the trip was heard.
    ("fact_trip_back", "fact", ["다음 주 금요일에 시애틀로 돌아가"],
     both(has_none("돌아가시나요", "돌아가세요?", "돌아가십니까"),
          has_any("시애틀", "돌아가", "돌아오", "금요일", "여행", "기억해"))),
    ("fact_sister_busan", "fact", ["내 여동생은 부산에 살아"],
     has_none("제가 아는데", "계신다면")),
    ("fact_dog_name", "fact", ["우리 집 강아지 이름은 콩이야"],
     has_none("우리 집 강아지", "우리 강아지", "제 강아지")),
    ("fact_moving", "fact", ["저 다음 달에 이사가요"],
     both(has_none("이사가시나요", "이사 가시나요", "이사하시나요"),
          has_any("이사", "준비", "새 집", "기억해"))),
    ("premise_yen", "premise", ["한국은 엔화 쓰잖아, 환전할 때 엔으로 바꾸면 되지?"],
     has_any("원화", "won", "krw", "원으로", "원을")),
    ("premise_cpt_phd", "premise", ["CPT는 박사과정 학생만 신청할 수 있는 거지?"],
     has_any("아닙니다", "아니", "않습니다", "학부", "석사", "undergrad", "뿐만")),
    # Tightened: "영하 20도" passed the first version, which looked for "0도".
    ("premise_water", "premise", ["물은 100도에서 얼잖아, 그럼 냉동실은 몇 도야?"],
     has_re(r"(?<![\d.])0\s*(?:도|°|℃)|영도|섭씨\s*0(?!\d)|끓")),
    ("premise_galaxy", "premise", ["갤럭시는 애플에서 만든 폰이잖아, 맞지?"],
     has_any("삼성", "samsung")),
    # Tightened: "텍사스에 있는 파리 날씨가 궁금하시군요… 검색해
    # 보겠습니다" passed -- a promise, not the weather.
    ("correct_paris", "correct", ["파리 날씨 어때?", "아니 텍사스에 있는 파리 말하는 거야"],
     both(has_any("텍사스", "texas"),
          has_re(r"\d+\s*(?:°|도|℉|℃)|맑|흐리|비가|눈이"),
          # Only a promise *instead of* the weather fails; the temperature
          # above is required anyway, so a trailing offer is fine.
          has_none("검색해 보겠", "알아보겠"))),
    ("correct_opt", "correct", ["CPT 신청 서류 뭐가 필요해?", "아니 CPT 말고 OPT"],
     both(has_any("opt"),
          has_any("서류", "i-20", "i-765", "ead", "신청서", "문서", "필요"))),
]


def run(code: Path, name: str, cases) -> list[dict]:
    runtime = OUT / f"misread_{name}"
    import shutil
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    from live_memory_restart_check import init_schema
    init_schema(code, runtime)
    proc = boot(code, runtime, OUT / f"misread_{name}.log")
    rows = []
    try:
        for case_id, kind, turns, check in cases:
            print(f"\n==== {case_id} [{kind}]", flush=True)
            replies = asyncio.run(talk(turns))
            last = replies[-1] or ""
            ok = bool(last) and check(last)
            rows.append({"id": case_id, "kind": kind, "turns": turns,
                         "replies": replies, "ok": ok})
            print(f"  -> {'PASS' if ok else 'FAIL'}", flush=True)
    finally:
        kill(proc)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default=str(REPO),
                            help="the tree to boot; a frozen snapshot, or this one")
    parser.add_argument("--name", default="live",
                        help="names this run's runtime and log files")
    args = parser.parse_args()
    code = Path(args.code)
    rows = run(code, f"{args.name}_en", ENGLISH) + run(code, f"{args.name}_ko", KOREAN)
    print("\n--- scored")
    for kind in ("fact", "premise", "correct", "vague"):
        group = [r for r in rows if r["kind"] == kind]
        print(f"  {kind:8} {sum(r['ok'] for r in group)}/{len(group)}")
    for row in rows:
        if not row["ok"]:
            print(f"  FAIL {row['id']}: {row['replies'][-1]!r}")
    print(f"\n{args.name}: {sum(r['ok'] for r in rows)}/{len(rows)}")
    (OUT / f"misread_{args.name}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
