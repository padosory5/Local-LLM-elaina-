"""Stage C: why both models' explanations miss the shape -- the numbers.

    .venv/Scripts/python.exe -m evals.stagec_diagnosis

Read-only over the frozen corpus v2 runs (runtime/evals/pf2): no model call,
nothing written. It prints the tables behind docs/STAGE_C_DIAGNOSIS.md:

* the turns corpus v2 attributed to "neither model good enough";
* each one's consistent failures (2 of 3 runs), for FAST, for the 27B
  writing under the 8B's reading (SPLIT on a first turn, LATE after it) and
  for DEEP (the 27B reading too);
* whether the response pipeline changed a failing reply after the model
  wrote it (the trace's draft against the displayed reply);
* where in the reply its illustration sits. That last measure looks for
  wording ("for example", "예를 들어", ...): it is a reading aid for this
  diagnosis and is not, and must not become, a check on her replies.
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import compare3  # noqa: E402
from evals import fastdeep_v2 as v2  # noqa: E402

CATEGORIES = {
    "concrete-first": {"concrete_first"},
    "terms-explained": {"terms_explained"},
    "leads-with-answer": {"leads_with_the_answer"},
    "answers-the-ask": {"answers_the_ask"},
    "unnecessary detail": {"only_what_helps", "no_unrelated_remark"},
    "other": {"accurate", "says_what_it_solves", "adapts_to_confusion", "must_contain_any",
              "quantity", "answers_not_asks", "takes_the_turn_as_meant", "language"},
}
_ILLUSTRATION = re.compile(r"for example|for instance|think of it|imagine|it's like|it is like|"
                           r"works like|예를 들어|예컨대|마치|비유하자면|처럼 ")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|(?<=다\.)\s*|(?<=요\.)\s*")


def neither_turns(a: dict) -> list[tuple[str, int]]:
    found = []
    for key, name in a["role"].items():
        sent = v2.majority(a["sent_by_run"], key)
        fast = a["runs"]["fast"] if key[1] <= v2.LABELLED_UP_TO else a["runs"]["routed"]
        fast_ok = sum(1 for r in fast if key in r["turns"] and v2.acceptable(r["turns"][key]))
        deep_ok = sum(1 for r in a["cf"] if key in r["turns"] and v2.acceptable(r["turns"][key]))
        if v2.attribute(a["labels"].get(key), name, sent, fast_ok, deep_ok, v2.RUNS) == \
                "neither model good enough":
            found.append(key)
    return sorted(found)


def replies(a: dict, key) -> dict[str, list[dict]]:
    """Per arm, per run: the outcome, the displayed reply and the model's draft."""
    out: dict[str, list[dict]] = defaultdict(list)
    sources = {"FAST": a["runs"]["fast"],
               "27B": a["runs"]["split"] if key[1] == 0 else a["runs"]["late"],
               "DEEP": a["runs"]["deep"]}
    for arm, runs in sources.items():
        for loaded in runs:
            records = {r.get("turn_id"): r for r in compare3._records(loaded["path"])}
            stored = next(t for res in loaded["run"]["results"] if res["scenario"] == key[0]
                          for t in res["turns"] if t["index"] == key[1])
            record = records.get((stored.get("trace") or {}).get("turn_id")) or {}
            out[arm].append({
                "defects": loaded["turns"][key].defects,
                "display": stored.get("display") or "",
                "draft": (record.get("draft") or {}).get("text", ""),
                "changed": [s.get("name") for s in record.get("steps") or () if s.get("changed")],
            })
    return out


def illustration_position(text: str) -> str:
    sentences = [s for s in _SENTENCE.split(text.strip()) if s.strip()]
    where = next((i for i, s in enumerate(sentences) if _ILLUSTRATION.search(s.lower())), None)
    if where is None:
        return "none"
    if where == 0:
        return "first"
    return "last" if where == len(sentences) - 1 else "middle"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = v2.analyse()
    keys = neither_turns(a)
    print(f"{len(keys)} turns: neither model good enough\n")
    consistent = {arm: defaultdict(set) for arm in ("FAST", "27B", "DEEP")}
    pipeline = Counter()
    position = {arm: Counter() for arm in ("FAST", "27B", "DEEP")}
    print("| turn | FAST | 27B (8B reads) | DEEP (27B reads) |")
    print("|---|---|---|---|")
    for key in keys:
        found = replies(a, key)
        cells = []
        for arm, rows in found.items():
            counted = Counter(c for row in rows for c, props in CATEGORIES.items()
                              if row["defects"] & props)
            held = sorted(c for c, n in counted.items() if n * 2 > len(rows))
            for c in held:
                consistent[arm][c].add(key)
            cells.append(", ".join(held) or "-")
            for row in rows:
                position[arm][illustration_position(row["display"])] += 1
                failing = any(row["defects"] & props for props in CATEGORIES.values())
                if failing:
                    same = row["draft"].strip() == row["display"].strip()
                    pipeline[(arm, "unchanged" if same else "changed after generation")] += 1
        print(f"| `{key[0]}` t{key[1]} | " + " | ".join(cells) + " |")
    print("\n| failure | FAST | 27B | DEEP | FAST and 27B both |")
    print("|---|---|---|---|---|")
    for c in CATEGORIES:
        f, d, dp = consistent["FAST"][c], consistent["27B"][c], consistent["DEEP"][c]
        print(f"| {c} | {len(f)} | {len(d)} | {len(dp)} | {len(f & d)} |")
    print("\nFailing replies, against the model's own draft:", dict(pipeline))
    print("Where the illustration sits:", {arm: dict(c) for arm, c in position.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
