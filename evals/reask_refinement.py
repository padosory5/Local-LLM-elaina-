"""Can the re-ask lookup rule take a refinement for a re-ask?

    .venv/Scripts/python.exe -m evals.reask_refinement --signal
    .venv/Scripts/python.exe -m evals.reask_refinement --run rf1 [--only ids]
    .venv/Scripts/python.exe -m evals.reask_refinement --report rf1 [rf2 ...]

``ChatEngine._lookup_as_first_asked`` searches a question asked again the way
it was searched the first time. It fires when two things hold: the router
wrote no query for the turn, and the message is at least 0.80 (bge-m3) from an
earlier searched question of the conversation. A refinement -- "...in Korea?"
-- is close to the question it refines, so the signal alone cannot be relied
on to tell them apart. The cases are evals/regressions/reask_lookup_cases.json:
a searched question, then a second message that repeats it, refines it,
changes it, follows it up, or leaves it.

``--signal``  The similarity of each second message to its first, and what the
              signal alone says. No model, no backend.
``--run``     Each pair through the real backend with the flag off (the
              ``current`` arm of evals/sim_learner.py): the real router, the
              real embedder, real searches.

What is checked on the second turn, from its trace:

  reuse   every search the first turn got back was searched again
  new     the rule did not fire, and the first turn's lookup was not what was
          searched
  router  the rule did not fire, and the search was the router's own query
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ELAINA_TTS", "off")

from brain import turn_move  # noqa: E402
from evals import sim_learner as sl  # noqa: E402
from evals.turn_move_shadow import _turns, embedder  # noqa: E402

CASES = PROJECT_ROOT / "evals" / "regressions" / "reask_lookup_cases.json"
ROOT = PROJECT_ROOT / "runtime" / "evals" / "reask"
ARM = "current"
KINDS = ("exact re-ask", "paraphrased re-ask", "added constraint", "changed constraint",
         "ordinary follow-up", "unrelated question")


def load_cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def signal() -> str:
    embed = embedder()
    lines = ["## The signal alone", "",
             f"bge-m3 similarity of the second message to the first. At or above "
             f"{turn_move.ASKED_AGAIN_SIMILARITY:.2f} the signal says \"asked again\".", "",
             "| case | kind | similarity | the signal says | should be |", "|---|---|---|---|---|"]
    wrong = defaultdict(int)
    total = defaultdict(int)
    for case in load_cases():
        first, second = (turn_move._unit(embed(line)) for line in case["lines"])
        similarity = turn_move._cosine(first, second)
        again = similarity >= turn_move.ASKED_AGAIN_SIMILARITY
        should = case["expect"] == "reuse"
        total[case["kind"]] += 1
        wrong[case["kind"]] += again != should
        lines.append(f"| {case['id']} | {case['kind']} | {similarity:.3f} | "
                     f"{'asked again' if again else 'a different question'} | "
                     f"{'asked again' if should else 'a different question'}{'' if again == should else ' **≠**'} |")
    lines += ["", "| kind | cases | the signal alone is wrong |", "|---|---|---|"]
    lines += [f"| {kind} | {total[kind]} | {wrong[kind]} |" for kind in KINDS]
    return "\n".join(lines) + "\n"


def run(name: str, only: list[str]) -> int:
    from evals import run as runner
    from evals.phase2 import unload_models

    if runner.port_open():
        print("Port 8765 is in use -- is Elaina already running? Close it first.")
        return 1
    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()
    (out / "meta.json").write_text(json.dumps({
        "arm": ARM, "env": sl.ARMS[ARM], "git_head": head, "uncommitted": [line[3:] for line in dirty],
        "started_at": datetime.now().isoformat(timespec="seconds")}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    path = out / "dialogues.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    unload_models()
    cases = [c for c in load_cases() if not only or c["id"] in only]
    for number, case in enumerate(cases, 1):
        if case["id"] in done:
            continue
        backend = sl.Elaina(out / "rt" / case["id"], out / "logs" / f"{case['id']}.log", sl.ARMS[ARM])
        transcript = []
        started = time.monotonic()
        try:
            for said in case["lines"]:
                reply, seconds = backend.reply(said)
                transcript.append({"person": said, "elaina": reply, "seconds": seconds})
        finally:
            backend.close()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": case["id"], "transcript": transcript}, ensure_ascii=False) + "\n")
        print(f"[{number}/{len(cases)}] {case['id']}: {time.monotonic() - started:.0f}s", flush=True)
    return 0


def rows(name: str) -> list[dict]:
    out = []
    for case in load_cases():
        paths = glob.glob(str(ROOT / name / "rt" / case["id"] / "turn_trace" / "*.jsonl"))
        if not paths:
            continue
        turns = [t for path in sorted(paths) for t in _turns(path)]
        if len(turns) < 2:
            continue
        first, second = turns[0], turns[1]
        context = second.get("context") or {}
        route = context.get("route") or {}
        move = context.get("turn_move") or {}
        searched_first = list((first.get("evidence") or {}).get("search_queries") or ())
        searched = list((second.get("evidence") or {}).get("search_queries") or ())
        fired = bool(context.get("lookup_as_first_asked"))
        router_query = str(route.get("search_query") or "").strip()
        same = bool(searched_first) and set(searched_first) <= set(searched)
        if case["expect"] == "reuse":
            # No search at all on a re-ask is the first answer again, from what is held.
            passed = same or not searched
        elif case["expect"] == "router":
            passed = not fired and (not searched or bool(router_query))
        else:
            passed = not fired and not same
        out.append({
            "run": name, "id": case["id"], "kind": case["kind"], "expect": case["expect"], "passed": passed,
            "fired": fired, "router_query": router_query, "intent": route.get("intent", ""),
            "move": move.get("move", ""), "similarity": move.get("similarity"),
            "searched_first": searched_first, "searched": searched, "same": same,
            "said": second.get("user_input", ""), "reply": str(second.get("display") or ""),
            "first_reply": str(first.get("display") or ""),
        })
    return out


def report(names: list[str]) -> str:
    every = [row for name in names for row in rows(name)]
    lines = [f"# The re-ask lookup rule and refinements: {', '.join(names)}", "",
             f"Flag off (`{ARM}` arm). {len(every)} conversations.", "", signal(), "## Through the backend", "",
             "| kind | conversations | as expected | the rule fired | the router wrote a query |", "|---|---|---|---|---|"]
    for kind in KINDS:
        group = [r for r in every if r["kind"] == kind]
        lines.append(f"| {kind} | {len(group)} | {sum(r['passed'] for r in group)} | "
                     f"{sum(r['fired'] for r in group)} | {sum(bool(r['router_query']) for r in group)} |")
    lines += ["", "| run | case | expect | result | rule fired | router's query | searched on the second turn |",
              "|---|---|---|---|---|---|---|"]
    for r in every:
        searched = " / ".join(r["searched"]) or "(no search)"
        lines.append(f"| {r['run']} | {r['id']} | {r['expect']} | {'ok' if r['passed'] else '**FAIL**'} | "
                     f"{'yes' if r['fired'] else 'no'} | {r['router_query'] or '-'} | {searched} |")
    failed = [r for r in every if not r["passed"]]
    lines += ["", "## Not as expected", ""]
    for r in failed:
        lines += [f"**{r['run']} / {r['id']}** ({r['kind']}; expected {r['expect']})", "",
                  f"- They said: {r['said']}",
                  f"- Routed as {r['intent']}; similarity {r['similarity']}; rule fired: {r['fired']}",
                  f"- First turn searched: {' / '.join(r['searched_first']) or '(nothing)'}",
                  f"- This turn searched: {' / '.join(r['searched']) or '(nothing)'}",
                  f"- She said: {' '.join(r['reply'].split())[:400]}", ""]
    if not failed:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--signal", action="store_true")
    parser.add_argument("--run", default="")
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--report", nargs="+")
    args = parser.parse_args()
    if args.signal:
        print(signal())
        return 0
    if args.run:
        return run(args.run, args.only)
    if args.report:
        text = report(args.report)
        (ROOT / args.report[0] / "REPORT.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
