"""What the ``named_recommendation`` stage removed, and whether it should have.

    .venv/Scripts/python.exe -m evals.named_stage_replay

The stage (``ChatEngine._enforce_named_recommendation``) is HARD: a search that
found nothing may not still name the answer. It runs on every turn that
searched and holds no recommendation candidates, and removes each sentence
carrying a proper name that is not, word for word, in the evidence or the
request. In the engine-order runs (docs/TURN_MOVE_AB.md, 2026-10-04) it took
the sentence naming the phones out of 10 of 31 correct English drafts.

This replays every recorded turn the stage changed (``runtime/evals``), from
the trace: the reply as it reached the stage, the evidence, what was asked.
Nothing is changed. For each name it flagged:

  date        what is left of "As of October" once the sentence's first word is
              dropped: a connector and a month
  in evidence every word of the name is in the evidence or the request, but
              not as that exact phrase ("Pro and Pro Max")
  absent      a word of the name is nowhere in the evidence or the request

and the same turns under a narrower reading (``narrower``), which is the
candidate change: a name does not begin with a connector, and a name is found
when each of its words is. A narrower reading can only flag fewer names, so a
turn the stage left alone is still left alone.
"""

from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import grounded_values as gv  # noqa: E402

ROOT = PROJECT_ROOT / "runtime" / "evals"
CONNECTORS = frozenset({"of", "de", "the", "and"})
WORD = re.compile(r"[^\W_]+")


def words(name: str) -> list[str]:
    return [w.casefold() for w in WORD.findall(str(name or ""))]


def kind(name: str, seen: set[str]) -> str:
    """Why this flagged name is, or is not, a thing the search did not find."""
    parts = words(name)
    while parts and parts[0] in CONNECTORS:
        parts = parts[1:]
    if not parts or all(p in gv._NOT_A_BUSINESS or p.isdigit() for p in parts):
        return "date"
    if all(p in seen or p in CONNECTORS for p in parts):
        return "in evidence"
    return "absent"


def narrower(text: str, *, evidence: str, request: str) -> tuple[str, ...]:
    """The candidate: the names the stage would still flag."""
    seen = set(words(f"{evidence} {request}"))
    return tuple(name for name in gv.names_an_unfound_thing(text, evidence=evidence, request=request)
                 if kind(name, seen) == "absent")


def changed_turns() -> tuple[list[dict], int, int]:
    rows, turns, searched = [], 0, 0
    for path in sorted(glob.glob(str(ROOT / "**" / "turn_trace" / "*.jsonl"), recursive=True)):
        where = Path(path).relative_to(ROOT).parts
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            turn = json.loads(line)
            turns += 1
            evidence = str((turn.get("evidence") or {}).get("research") or "")
            searched += bool(evidence)
            step = next((s for s in turn.get("steps") or () if s.get("name") == "named_recommendation"
                         and s.get("changed")), None)
            if step is None:
                continue
            context = turn.get("context") or {}
            request = str(context.get("user_input_resolved") or turn.get("user_input") or "")
            before = str(step.get("before") or "")
            flagged = gv.names_an_unfound_thing(before, evidence=evidence, request=request)
            seen = set(words(f"{evidence} {request}"))
            rows.append({
                "where": f"{where[0]}/{where[1]}/{where[-3]} t{number}", "request": request,
                "language": str(context.get("language") or ""), "before": before,
                "after": str(step.get("after") or ""), "flagged": flagged,
                "kinds": [kind(name, seen) for name in flagged],
                "still": narrower(before, evidence=evidence, request=request),
                "intent": str((context.get("route") or {}).get("intent") or ""),
            })
    return rows, turns, searched


def report() -> str:
    rows, turns, searched = changed_turns()
    names = Counter()
    kinds = Counter()
    for row in rows:
        for name, why in zip(row["flagged"], row["kinds"]):
            names[(name, why)] += 1
            kinds[why] += 1
    only_false = [r for r in rows if r["flagged"] and not r["still"]]
    partly = [r for r in rows if r["still"] and len(r["still"]) < len(r["flagged"])]
    unchanged = [r for r in rows if r["flagged"] and len(r["still"]) == len(r["flagged"])]
    nothing = [r for r in rows if not r["flagged"]]
    lines = ["# The named_recommendation stage, replayed", "",
             f"{turns} recorded turns under `runtime/evals`; {searched} searched; the stage changed the reply "
             f"in {len(rows)}.", "",
             "| what it flagged | names | turns it accounts for alone |", "|---|---|---|"]
    for why in ("date", "in evidence", "absent"):
        alone = sum(1 for r in rows if r["kinds"] and set(r["kinds"]) == {why})
        lines.append(f"| {why} | {kinds[why]} | {alone} |")
    lines += ["", "| under the narrower reading | turns |", "|---|---|",
              f"| the stage would leave the reply alone | {len(only_false)} |",
              f"| it would still act, on fewer names | {len(partly)} |",
              f"| no difference | {len(unchanged)} |",
              f"| changed for another reason (nothing flagged; the recommendation line was added) | {len(nothing)} |",
              "", "The names, most frequent first:", "", "| name | kind | turns |", "|---|---|---|"]
    for (name, why), count in names.most_common():
        lines.append(f"| {name} | {why} | {count} |")
    for title, group in (("Left alone under the narrower reading", only_false),
                         ("Still acted on", partly + unchanged)):
        lines += ["", f"## {title}", ""]
        for row in group:
            lines += [f"**{row['where']}** ({row['intent']}) — asked: {row['request'][:110]}",
                      f"- flagged: " + ", ".join(f"{n} [{k}]" for n, k in zip(row["flagged"], row["kinds"])),
                      f"- before: {' '.join(row['before'].split())[:330]}",
                      f"- after: {' '.join(row['after'].split())[:230]}", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    text = report()
    out = ROOT / "named_stage"
    out.mkdir(parents=True, exist_ok=True)
    (out / "REPORT.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
