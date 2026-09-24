"""Are the everyday answers on point?

Two things nothing else measured, asked the way a person asks them:

  knowledge       a plain fact she should know without looking anything up
                  ("How many ounces in a pound?", "물은 몇 도에서 끓어?").
                  Right answer, and no punting to a search.
  recommendation  "recommend a film", "책 한 권 추천해줘". One thing named,
                  a reason for it, in the language asked -- not a list, not
                  a genre question back, not a cinema when a film was asked
                  for (measured: "the Alamo Drafthouse Cinema in Los
                  Angeles" and "Tonight and Every Night (1945)").

Every reply is printed. The checks catch the shapes that went wrong live;
the number that counts is still the one read by a person.

    .venv/Scripts/python.exe scripts/live_basics_check.py --name today
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

SP = Path(__file__).resolve().parent
REPO = SP.parent
OUT = Path(os.environ.get("ELAINA_CHECK_OUT")
           or Path(tempfile.gettempdir()) / "elaina_checks")
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SP))
from live_memory_restart_check import boot, init_schema, kill, talk  # noqa: E402

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


def has_hangul(reply):
    return any("가" <= ch <= "힣" for ch in reply)


# Punting, in either language: no answer, only a promise or a question back.
PUNTED = has_none(
    "couldn't verify", "could not verify", "i don't have", "i cannot confirm",
    "확인되지", "확인해 드릴 수", "검색해 보겠", "알아보겠",
)

KNOWLEDGE = [
    ("capital_france", ["What's the capital of France?"], has_any("paris")),
    ("ounces_pound", ["How many ounces are in a pound?"], has_any("16")),
    ("percent", ["What's 15% of 80?"], has_any("12")),
    ("pride_prejudice", ["Who wrote Pride and Prejudice?"], has_any("austen")),
    # Measured twice as 694,000 before the encyclopedia check existed -- an
    # order of magnitude out. The range is what is being checked, not one
    # figure: the city is about 68,000 and a live search legitimately comes
    # back with 70,227 for a newer estimate, while 694,000 is the failure.
    ("population_maine", ["What's the population of Portland, Maine?"],
     has_re(r"\b(?:6[5-9]|7[0-2])[,.]?\d{3}\b")),
    ("continents", ["How many continents are there?"], has_any("7", "seven")),
    ("brazil_language", ["What language do they speak in Brazil?"],
     has_any("portuguese", "포르투갈")),
    ("freezing_f", ["What is the freezing point of water in Fahrenheit?"], has_any("32")),
    # Celsius, because the question is Korean. Measured repeatedly: she
    # answered "물의 끓는점은 212°F입니다" -- the right fact in the unit of
    # the English turns before it. Decided 2026-09-23: the market is the
    # United States (user.country: "US") and English answers stay in
    # Fahrenheit, but the unit follows the language of the question, so a
    # Korean one is converted (brain/units.py).
    ("boiling_ko", ["물은 몇 도에서 끓어?"], has_any("100")),
    ("weeks_ko", ["일 년은 몇 주야?"], has_any("52")),
    ("capital_korea_ko", ["대한민국 수도가 어디야?"], has_any("서울", "seoul")),
]

# A list instead of one thing, or a genre question instead of an answer.
NOT_A_LIST = has_none("1.", "2.", "- ", "첫째", "둘째")
NO_QUESTION_BACK = has_none("what kind", "which genre", "what genre", "어떤 장르",
                            "어떤 종류", "무슨 장르")

RECOMMENDATION = [
    ("film_en", ["Recommend a film for tonight."],
     both(PUNTED, NOT_A_LIST, NO_QUESTION_BACK,
          # Measured: a cinema in Los Angeles, for "recommend a movie".
          has_none("cinema", "theater", "theatre", "drafthouse"))),
    ("series_en", ["What should I watch if I liked Severance?"],
     both(PUNTED, NOT_A_LIST, NO_QUESTION_BACK)),
    ("mouse_en", ["Recommend a wireless mouse under $50."],
     both(PUNTED, NOT_A_LIST, NO_QUESTION_BACK)),
    ("film_ko", ["영화 하나 추천해줘"],
     both(PUNTED, NOT_A_LIST, NO_QUESTION_BACK, has_hangul)),
    ("book_ko", ["책 한 권 추천해줘"],
     both(PUNTED, NOT_A_LIST, NO_QUESTION_BACK, has_hangul)),
    ("dinner_ko", ["오늘 저녁 뭐 먹을지 추천해줘"],
     both(PUNTED, NOT_A_LIST, NO_QUESTION_BACK, has_hangul)),
]


def run(code: Path, name: str, cases, kind: str) -> list[dict]:
    runtime = OUT / f"basics_{name}_{kind}"
    import shutil
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    init_schema(code, runtime)
    proc = boot(code, runtime, OUT / f"basics_{name}_{kind}.log")
    rows = []
    try:
        for case_id, turns, check in cases:
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
    rows = (run(code, args.name, KNOWLEDGE, "knowledge")
            + run(code, args.name, RECOMMENDATION, "recommendation"))
    print("\n--- scored")
    for kind in ("knowledge", "recommendation"):
        group = [r for r in rows if r["kind"] == kind]
        print(f"  {kind:14} {sum(r['ok'] for r in group)}/{len(group)}")
    for row in rows:
        if not row["ok"]:
            print(f"  FAIL {row['id']}: {row['replies'][-1]!r}")
    print(f"\n{args.name}: {sum(r['ok'] for r in rows)}/{len(rows)}")
    (OUT / f"basics_{args.name}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
