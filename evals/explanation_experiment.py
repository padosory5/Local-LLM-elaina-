"""The explanation-contract experiment on her real turns: B against current.

    .venv/Scripts/python.exe -m evals.explanation_experiment                 # summary
    .venv/Scripts/python.exe -m evals.explanation_experiment --since 2026-10-02
    .venv/Scripts/python.exe -m evals.explanation_experiment --export-blind 20
    .venv/Scripts/python.exe -m evals.explanation_experiment --labels        # after labelling

Reads the turn traces she writes in her own use (runtime/turn_trace), and
only the turns the experiment logged (context.explanation_experiment, set on
every explanation turn: docs/EXPLANATION_EXPERIMENT.md). Read-only over the
traces; no model call.

* **Summary** -- per arm (``contract`` = Stage C arm B, ``current``): how many
  turns, their length, where the illustration sits, whether the response
  stages changed the model's draft, and how long the turn took. The
  illustration position looks for wording ("for example", "예를 들어", ...):
  a reading aid, never a check on her replies.
* **Blind sample** -- an equal number of turns from each arm, shuffled, for a
  person to label on the rubric's strict ``concrete_first`` and
  ``leads_with_the_answer`` definitions, unchanged (evals/rubric.json). The
  file the person labels shows no arm; the key beside it does.
* **Labels** -- each arm's yes rate on both properties, from those labels.

Real turns are not a controlled sample: the questions differ from turn to
turn, so a difference between arms is evidence to read with its counts, not
a frozen verdict.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import corpus  # noqa: E402

TRACES = PROJECT_ROOT / "runtime" / "turn_trace"
OUT = PROJECT_ROOT / "runtime" / "explanation_experiment"
PROPERTIES = ("concrete_first", "leads_with_the_answer")
ARMS = ("contract", "current")

_ILLUSTRATION = re.compile(r"for example|for instance|think of it|imagine|it's like|it is like|"
                           r"works like|예를 들어|예컨대|마치|비유하자면|처럼 ")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|(?<=다\.)\s*|(?<=요\.)\s*")


def read_records(root: Path = TRACES, since: str = "") -> list[dict]:
    """Every traced turn under ``root``, oldest first (``since`` a YYYY-MM-DD)."""
    found = []
    for path in sorted(Path(root).glob("*.jsonl")):
        if since and path.stem < since:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                found.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    found.sort(key=lambda r: str(r.get("started_at", "")))
    return found


def experiment_turns(records: list[dict]) -> list[dict]:
    """The explanation turns the experiment logged, each with its session's
    earlier turns (up to three) for context."""
    by_session: dict[str, list[dict]] = defaultdict(list)
    turns = []
    for record in records:
        session = str(record.get("session_id") or "")
        context = record.get("context") or {}
        logged = context.get("explanation_experiment")
        if logged:
            display = str(record.get("display") or "")
            turns.append({
                "turn_id": record.get("turn_id"), "started_at": record.get("started_at"),
                "arm": logged.get("variant"), "mode": logged.get("mode"),
                "contract_version": logged.get("contract_version"),
                "applied": bool(context.get("explanation_contract")),
                "language": context.get("language", ""),
                "person": str(record.get("user_input") or ""), "elaina": display,
                "draft": str((record.get("draft") or {}).get("text") or ""),
                "seconds": record.get("elapsed"),
                "history": [{"person": str(r.get("user_input") or ""),
                             "elaina": str(r.get("display") or "")} for r in by_session[session][-3:]],
            })
        by_session[session].append(record)
    return turns


def illustration_position(text: str) -> str:
    sentences = [s for s in _SENTENCE.split(text.strip()) if s.strip()]
    where = next((i for i, s in enumerate(sentences) if _ILLUSTRATION.search(s.lower())), None)
    if where is None:
        return "none"
    if where == 0:
        return "first"
    return "last" if where == len(sentences) - 1 else "middle"


def _pct(values, share):
    ordered = sorted(values)
    return ordered[int(share * (len(ordered) - 1))] if ordered else None


def summary(turns: list[dict]) -> str:
    lines = ["# The explanation-contract experiment, real turns", ""]
    if not turns:
        return "\n".join(lines + ["No explanation turn has been logged yet.", ""])
    versions = Counter(t["contract_version"] for t in turns)
    modes = Counter(t["mode"] for t in turns)
    lines += [f"{len(turns)} explanation turns, {turns[0]['started_at']} to {turns[-1]['started_at']}; "
              f"modes {dict(modes)}; contract versions {dict(versions)}.", ""]
    mismatched = [t for t in turns if (t["arm"] == "contract") != t["applied"]]
    if mismatched:
        lines += [f"**{len(mismatched)} turns whose logged arm and applied contract disagree.**", ""]
    lines += ["| arm | turns | en / ko | words p50 | sentences p50 | illustration first / middle / last / none "
              "| changed after generation | turn seconds p50 / p95 |",
              "|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        group = [t for t in turns if t["arm"] == arm]
        if not group:
            lines.append(f"| {arm} | 0 | - | - | - | - | - | - |")
            continue
        langs = Counter(t["language"] for t in group)
        words = [len(t["elaina"].split()) for t in group]
        sentences = [len([s for s in _SENTENCE.split(t["elaina"].strip()) if s.strip()]) for t in group]
        where = Counter(illustration_position(t["elaina"]) for t in group)
        changed = sum(1 for t in group if t["draft"] and t["draft"].strip() != t["elaina"].strip())
        seconds = [float(t["seconds"]) for t in group if isinstance(t["seconds"], (int, float))]
        p50, p95 = _pct(seconds, 0.5), _pct(seconds, 0.95)
        lines.append(
            f"| {arm} | {len(group)} | {langs.get('en', 0)} / {langs.get('ko', 0)} | {median(words):.0f} "
            f"| {median(sentences):.0f} | {where.get('first', 0)} / {where.get('middle', 0)} / "
            f"{where.get('last', 0)} / {where.get('none', 0)} | {changed} "
            f"| {'-' if p50 is None else f'{p50:.1f} / {p95:.1f}'} |")
    lines += ["", "The illustration column is a reading aid. Whether a reply is concrete first or leads "
              "with the answer is a person's label on the rubric's strict definitions "
              "(`--export-blind`, then `--labels`).", ""]
    return "\n".join(lines)


def export_blind(turns: list[dict], per_arm: int, seed: int = 20261002, out: Path = OUT) -> int:
    rubric = corpus.rubric()
    rng = random.Random(seed)
    chosen = []
    for arm in ARMS:
        group = [t for t in turns if t["arm"] == arm and t["elaina"].strip()]
        rng.shuffle(group)
        chosen += group[:per_arm]
    rng.shuffle(chosen)
    items, key = [], []
    for number, turn in enumerate(chosen, 1):
        items.append({"item": number, "conversation": turn["history"], "person": turn["person"],
                      "elaina": turn["elaina"], "labels": {name: "" for name in PROPERTIES},
                      "definitions": {name: rubric[name]["definition"] for name in PROPERTIES},
                      "allowed": {name: ["yes", "no"] for name in PROPERTIES}})
        key.append({"item": number, "turn_id": turn["turn_id"], "arm": turn["arm"],
                    "contract_version": turn["contract_version"]})
    out.mkdir(parents=True, exist_ok=True)
    (out / "blind.json").write_text(json.dumps({
        "instructions": "Label each reply yes or no on each property, by the definition given. "
                        "Which arm wrote it is hidden on purpose.",
        "rubric_fingerprint": corpus.rubric_fingerprint(), "items": items},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "blind_key.json").write_text(json.dumps({"items": key}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    lines = [f"# Blind labels: {len(items)} real explanation turns", ""]
    for item in items:
        lines += [f"## Item {item['item']}", ""]
        for said in item["conversation"]:
            lines += [f"> **Person:** {said['person']}", ">", f"> **Elaina:** {said['elaina']}", ""]
        lines += [f"**Person:** {item['person']}", "", f"**Elaina:** {item['elaina']}", ""]
    for name in PROPERTIES:
        lines += [f"`{name}`: {rubric[name]['definition']}", ""]
    (out / "blind.md").write_text("\n".join(lines), encoding="utf-8")
    return len(items)


def label_rates(out: Path = OUT) -> str:
    items = json.loads((out / "blind.json").read_text(encoding="utf-8"))["items"]
    key = {k["item"]: k for k in json.loads((out / "blind_key.json").read_text(encoding="utf-8"))["items"]}
    lines = ["| arm | labelled | concrete_first yes | leads_with_the_answer yes | both |", "|---|---|---|---|---|"]
    for arm in ARMS:
        group = [i for i in items if key[i["item"]]["arm"] == arm
                 and all(i["labels"].get(p) in ("yes", "no") for p in PROPERTIES)]
        cf = sum(i["labels"]["concrete_first"] == "yes" for i in group)
        lead = sum(i["labels"]["leads_with_the_answer"] == "yes" for i in group)
        both = sum(all(i["labels"][p] == "yes" for p in PROPERTIES) for i in group)
        lines.append(f"| {arm} | {len(group)} | {cf} | {lead} | {both} |")
    unlabelled = sum(1 for i in items if not all(i["labels"].get(p) in ("yes", "no") for p in PROPERTIES))
    if unlabelled:
        lines += ["", f"{unlabelled} items not labelled yet."]
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(TRACES))
    parser.add_argument("--since", default="", help="YYYY-MM-DD")
    parser.add_argument("--export-blind", type=int, default=0, metavar="PER_ARM")
    parser.add_argument("--labels", action="store_true")
    args = parser.parse_args()
    if args.labels:
        print(label_rates())
        return 0
    turns = experiment_turns(read_records(Path(args.root), args.since))
    if args.export_blind:
        count = export_blind(turns, args.export_blind)
        print(f"Wrote {OUT / 'blind.json'}: {count} replies to label blind")
        return 0
    print(summary(turns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
