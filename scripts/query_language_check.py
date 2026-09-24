"""Does a search run in the language the person was speaking?

Rule 1: the instrument first. This one exists because a Korean dogfood
session produced a finding nobody had a number for --

    [Tool] Searching web for: Washington University Seattle
    said='워싱턴 대학교는 어떤 과가 유명해?'

The router writes in English, always, and the query is built from what the
router wrote. For a Korean speaker asking a Korean question that is wrong
twice over:

**The results are for the wrong audience.** The person's locale is South
Korea and the question is in Korean; an English query returns the English
web, which is not where the specific answer usually is. The one query in
that session that *was* in Korean -- "한국전쟁 발발 원인 남침 북침" -- is
also the one whose answer the person accepted without complaint.

**A translated proper noun is a different thing.** 워싱턴 대학교 came back
as "Washington University in Seattle". "University of Washington" is in
Seattle; "Washington University" is in St. Louis. The query names a real
university, and not the one the person goes to.

Two modes, one question.

**A session log**, which measures what actually went out::

    python scripts/query_language_check.py runtime/koreanSession.log

**The case list**, which measures the rule that decides it, without a model
or a network::

    python scripts/query_language_check.py --matrix

The log mode came first and was the reason for the fix: 4 of 5 searches in
a real Korean session ran in a language the turn was not. It stays, because
the matrix can only score turns someone thought to write down.

Asking the router to write the query in the turn's language was tried
first, and measured, because it is the cheap fix and would have been the
right one if it worked: pooled over three runs against qwen3:8b it moved
9/30 to 9/30. Prompt wording does not fix a confirmed behaviour here. The
rule below uses the person's own words instead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


HANGUL = re.compile(r"[가-힣]")

# The line the tool layer prints when it goes out, and the line the surface
# layer prints afterwards carrying the turn it was for. The pairing is
# positional -- the surface line for a turn follows its searches -- which is
# what the log gives us and is why this reads forward rather than back.
_SEARCH = re.compile(
    r"^\[Tool\] (?:Searching web for|Using cached web search for):\s*(.+)$",
)
_TURN = re.compile(r"^\[Surface\] considering: said='(.*?)' searched=")

# The tool's own second pass. It is derived from the first query, so
# counting it doubles every finding without adding one.
_DERIVED = re.compile(r"^official source .* as of \d{4}-\d{2}-\d{2}$")


def searches(log: str) -> list[tuple[str, str]]:
    """Every query the session ran, paired with the turn it was for."""
    lines = log.splitlines()
    found: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        match = _SEARCH.match(line.strip())
        if not match:
            continue
        query = match.group(1).strip()
        if _DERIVED.match(query):
            continue
        turn = ""
        for later in lines[index:]:
            said = _TURN.match(later.strip())
            if said:
                turn = said.group(1)
                break
        found.append((query, turn))
    return found


def language_of(text: str) -> str:
    """Korean if it is written in Hangul, English if it is not.

    The same proxy ``brain.turn_language`` uses, minus the density rule: a
    query is a handful of terms rather than a sentence, so "which script is
    the spine" is not a question it can answer.
    """
    if HANGUL.search(str(text or "")):
        return "ko"
    return "en"


def report(log: str) -> int:
    rows = searches(log)
    if not rows:
        print("No searches in this log.")
        return 0
    disagreed = 0
    print(f"{'turn':<46}  {'said':<4}  {'searched':<8}  query")
    print("-" * 108)
    for query, turn in rows:
        spoken = language_of(turn)
        asked = language_of(query)
        mark = "" if spoken == asked else "  <-- disagrees"
        if spoken != asked:
            disagreed += 1
        print(f"{turn[:44]:<46}  {spoken:<4}  {asked:<8}  {query[:36]}{mark}")
    print()
    print(f"searches: {len(rows)}")
    print(
        f"in a language the turn was not: {disagreed} "
        f"({disagreed / len(rows):.0%})"
    )
    return disagreed


MATRIX = ROOT_MATRIX = Path(__file__).resolve().parents[1] / "tests" / "query_language_matrix.json"


def score(case: dict) -> str:
    """What the rule does with this case: "rewritten" or "kept"."""
    import sys as _sys

    root = str(Path(__file__).resolve().parents[1])
    if root not in _sys.path:
        _sys.path.insert(0, root)
    from brain import search_language

    query, _ = search_language.in_the_turns_language(
        case["router_query"], said=case["said"], language=case["language"],
    )
    return "kept" if query == case["router_query"] else "rewritten"


def report_matrix(cases: list[dict]) -> int:
    wrong = 0
    counts: dict[str, list[bool]] = {"rewritten": [], "kept": []}
    print(f"{'case':<44} {'lang':<5} {'want':<10} {'got':<10}")
    print("-" * 78)
    for case in cases:
        want = case["expect"]
        got = score(case)
        ok = want == got
        counts[want].append(ok)
        if not ok:
            wrong += 1
        print(
            f"{case['id']:<44} {case['language']:<5} {want:<10} {got:<10} "
            f"{'' if ok else '<-- wrong'}"
        )
    print()
    for want, results in counts.items():
        if results:
            right = sum(1 for value in results if value)
            print(
                f"must be {want:<10} {right}/{len(results)} "
                f"({right / len(results):.0%})"
            )
    total = len(cases)
    print(f"{'overall':<18} {total - wrong}/{total} "
          f"({(total - wrong) / total:.0%})")
    return wrong


def main(argv: list[str]) -> int:
    # A report about Korean that a Windows console renders as mojibake is
    # not a report. cp949 is still the default codepage here.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "log", type=Path, nargs="?",
        help="a session log written by the app",
    )
    parser.add_argument(
        "--matrix", nargs="?", const=MATRIX, type=Path, default=None,
        help="score tests/query_language_matrix.json instead of a log",
    )
    args = parser.parse_args(argv)
    if args.matrix is not None:
        cases = json.loads(args.matrix.read_text(encoding="utf-8"))["cases"]
        return 1 if report_matrix(cases) else 0
    if args.log is None:
        parser.error("give a session log, or --matrix")
    if not args.log.exists():
        print(f"No such log: {args.log}")
        return 2
    report(args.log.read_text(encoding="utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
