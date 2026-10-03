"""Offline: what stops her repeating herself on a question asked again?

    .venv/Scripts/python.exe -m evals.turn_move_replay --run
    .venv/Scripts/python.exe -m evals.turn_move_replay --report

The A/B (docs/TURN_MOVE_AB.md) showed that a goal line telling the 8B not to
reuse its earlier answer does not stop it: the draft was the same answer
again. This replays the recorded re-ask turns -- no backend, no pipeline,
only the drafting call -- under variants of what the model is shown and of
which model writes, and counts how often the draft is a near-copy of her
earlier answer to that question.

Each turn's prompt is rebuilt from its trace: the persona (stored whole),
the final message (stored whole unless over the trace's limit; those turns
are left out), and the history, which the trace keeps only the length and
last two messages of -- so it is rebuilt from the conversation's earlier
turns and checked against those two.

Variants:

  A  production's prompt as recorded with the flag off      8B
  B  the move's line, quoting her earlier answer (the A/B)   8B
  C  the move's line without the quote, history as it was    8B
  D  C, and her earlier exchanges on this question removed
     from the history and from the "verified result" block   8B
  E  A, written by the 27B
  F  B, written by the 27B
  G  D, written by the 27B

Drafts only: nothing after the draft runs here, so this compares what the
model writes, not what would finally be shown.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import response_budget, turn_move  # noqa: E402
from core import turn_trace  # noqa: E402
from evals.phase2 import M8, M27  # noqa: E402
from evals.turn_move_shadow import NEAR_COPY, SL_ROOT, _turns, _unit, embedder  # noqa: E402

RUNS = ("ab1-off-s1b", "ab1-on-s1b", "ab1-off-s2", "ab1-on-s2")
OUT = SL_ROOT / "ab1-freeze-b" / "replay"
SEEDS = (1, 2)
VARIANTS = {
    "A": ("production prompt", M8),
    "B": ("move's line, quoting her answer", M8),
    "C": ("move's line, no quote", M8),
    "D": ("no quote + her earlier exchanges removed", M8),
    "E": ("production prompt", M27),
    "F": ("move's line, quoting her answer", M27),
    "G": ("no quote + her earlier exchanges removed", M27),
}
NO_QUOTE = {
    "en": ("This is the {count}{ordinal} time they have asked this, and the earlier explanations did "
           "not land. Give a different explanation: start from something they already know and take "
           "one small step at a time."),
    "ko": ("같은 질문을 {count}번째 하셨습니다. 앞의 설명들은 전달되지 않았습니다. 다르게 설명합니다: "
           "이미 알고 계신 것에서 시작해서 한 걸음씩 설명합니다."),
}
_GROUNDED = re.compile(r"RECENT VERIFIED CONTEXT\n.*?acknowledge that directly\.\n*", re.DOTALL)
_STYLE = "\n\nHOW TO SOUND"


def collect(runs=RUNS) -> tuple[list[dict], dict]:
    """Every recorded ``differently`` turn that can be replayed, and why the rest cannot."""
    rows, left_out = [], defaultdict(int)
    for run in runs:
        for path in sorted(glob.glob(str(SL_ROOT / run / "rt" / "*" / "turn_trace" / "*.jsonl"))):
            learner = Path(path).parents[1].name
            turns = _turns(path)
            for number, turn in enumerate(turns, 1):
                context = turn.get("context") or {}
                move = context.get("turn_move") or {}
                if move.get("move") != turn_move.DIFFERENTLY or not move.get("asked_before"):
                    continue
                if any((t.get("context", {}).get("route") or {}).get("topic_shift") for t in turns[:number]):
                    left_out["the thread was reset earlier (positions are not turn numbers)"] += 1
                    continue
                draft = next((c for c in turn["model_calls"] if "collect_answer" in str(c.get("caller"))), None)
                if draft is None or "content" not in draft["messages"][0]:
                    left_out["no drafting call or persona recorded"] += 1
                    continue
                final = draft["messages"][-1]["content"]
                if "chars cut]" in final:
                    left_out["final message over the trace's limit"] += 1
                    continue
                conversation = []
                for earlier in turns[:number - 1]:
                    conversation += [{"role": "user", "content": earlier.get("user_input", "")},
                                     {"role": "assistant", "content": earlier.get("display", "")}]
                sent = int(draft.get("history") or 0)
                history = conversation[-sent:] if sent else []
                stored = [m.get("content", "") for m in draft["messages"][1:-1]]
                rebuilt = [turn_trace._clip(m["content"], turn_trace._HISTORY_LIMIT) for m in history[-2:]]
                limits = context.get("limits") or {}
                applied = context.get("turn_move_applied") or {}
                language = "ko" if str(context.get("language", "")).startswith("ko") else "en"
                was_act = (applied.get("was") or {}).get("act") or context.get("act")
                goal = (response_budget.ResponseBudget(limits.get("budget", ""), 0, 0, limits.get("shape", ""))
                        .goal(language) if was_act == "answer" else "")
                rows.append({
                    "id": f"{run}/{learner}/t{number}", "run": run, "learner": learner, "turn": number,
                    "language": language, "said": turn.get("user_input", ""),
                    "system": draft["messages"][0]["content"], "history": history, "final": final,
                    "history_matches_trace": stored == rebuilt,
                    "options": dict(draft.get("options") or {}),
                    "instruction": move.get("instruction", ""), "goal": goal,
                    "applied": bool(applied), "attempts": int(move.get("attempts") or 0),
                    "asked_before": list(move["asked_before"]),
                    "earlier_replies": [turns[p - 1].get("display", "") for p in move["asked_before"]],
                    "recorded_reply": turn.get("display", ""),
                })
    return rows, dict(left_out)


def _with_line(final: str, row: dict, line: str) -> str:
    """The final message with ``line`` where the goal (or the move's line) was."""
    if row["applied"] and row["instruction"] and row["instruction"] in final:
        return final.replace(row["instruction"], line)
    if row["goal"] and row["goal"] in final:
        return final.replace(row["goal"], line)
    # A turn whose act carried no goal: put the line at the end of the requirements.
    return final.replace(_STYLE, f" {line}{_STYLE}", 1) if line else final


def messages_for(row: dict, variant: str) -> list[dict]:
    final, history = row["final"], list(row["history"])
    if variant in ("A", "E"):
        final = _with_line(final, row, row["goal"])
    elif variant in ("B", "F"):
        final = _with_line(final, row, row["instruction"])
    else:
        count = row["attempts"] + 1
        line = NO_QUOTE[row["language"]].format(count=count, ordinal=turn_move._ordinal(count))
        final = _with_line(final, row, line)
        if variant in ("D", "G"):
            # Her earlier exchanges on this question, out of what she is shown:
            # the history, and the "verified result" block that can repeat one.
            sent = len(history) // 2
            first = row["turn"] - 1 - sent            # turns before the history window
            drop = {position - 1 - first for position in row["asked_before"]}
            history = [m for index, m in enumerate(history) if index // 2 not in drop]
            final = _GROUNDED.sub("", final)
    return [{"role": "system", "content": row["system"]}, *history, {"role": "user", "content": final}]


def run() -> int:
    import ollama

    from evals.phase2 import unload_models

    rows, left_out = collect()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "turns.json").write_text(json.dumps(
        {"replayed": [r["id"] for r in rows], "left_out": left_out,
         "history_matches_trace": sum(r["history_matches_trace"] for r in rows)},
        ensure_ascii=False, indent=1), encoding="utf-8")
    path = OUT / "drafts.jsonl"
    done = set()
    if path.exists():
        done = {(d["id"], d["variant"], d["seed"]) for d in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    for model in (M8, M27):
        unload_models()
        for variant, (_label, variant_model) in VARIANTS.items():
            if variant_model != model:
                continue
            for row in rows:
                for seed in SEEDS:
                    if (row["id"], variant, seed) in done:
                        continue
                    options = {**row["options"], "seed": seed}
                    response = ollama.chat(model=model, messages=messages_for(row, variant), stream=False,
                                           options=options, think=False)
                    text = str((response.get("message") or {}).get("content") or "").strip()
                    with path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"id": row["id"], "variant": variant, "seed": seed,
                                                 "model": model, "draft": text}, ensure_ascii=False) + "\n")
            print(f"variant {variant} ({model}) done", flush=True)
    unload_models()
    return 0


def report() -> str:
    rows, left_out = collect()
    by_id = {r["id"]: r for r in rows}
    drafts = [json.loads(line) for line in (OUT / "drafts.jsonl").read_text(encoding="utf-8").splitlines()]
    embed = embedder()
    earlier = {r["id"]: [_unit(embed(text)) for text in r["earlier_replies"] if text.strip()] for r in rows}
    scored = defaultdict(list)
    for d in drafts:
        if not d["draft"]:
            scored[d["variant"]].append({**d, "similarity": None})
            continue
        vector = _unit(embed(d["draft"]))
        similarity = max((sum(x * y for x, y in zip(vector, e)) for e in earlier[d["id"]]), default=0.0)
        scored[d["variant"]].append({**d, "similarity": similarity})
    lines = ["# Re-asked questions, replayed offline", "",
             f"{len(rows)} recorded re-ask turns from {', '.join(RUNS)}, {len(SEEDS)} drafts each per variant. "
             "Drafts only -- nothing after the draft ran. A near-copy is a draft whose bge-m3 cosine to "
             f"one of her earlier answers to that question is at least {NEAR_COPY}.", "",
             f"Left out: {left_out or 'none'}. Rebuilt history matches the trace's last two messages on "
             f"{sum(r['history_matches_trace'] for r in rows)} of {len(rows)} turns.", "",
             "| | what the model is shown | model | near-copies | median similarity | turns with no near-copy | empty |",
             "|---|---|---|---|---|---|---|"]
    for variant, (label, model) in VARIANTS.items():
        items = scored.get(variant, [])
        real = [i for i in items if i["similarity"] is not None]
        copies = sum(1 for i in real if i["similarity"] >= NEAR_COPY)
        clean = defaultdict(lambda: True)
        for i in real:
            clean[i["id"]] = clean[i["id"]] and i["similarity"] < NEAR_COPY
        lines.append(f"| {variant} | {label} | {'8B' if model == M8 else '27B'} | {copies}/{len(real)} "
                     f"| {median(i['similarity'] for i in real):.2f} | {sum(clean.values())}/{len(clean)} "
                     f"| {len(items) - len(real)} |" if real else f"| {variant} | {label} | - | - | - | - | - |")
    lines.append("")
    for row in rows:
        lines += [f"## {row['id']} (asked {row['attempts'] + 1} times)", "", f"- They said: {row['said']}",
                  f"- Her earlier answer: {row['earlier_replies'][-1]}",
                  f"- What she said in the run: {row['recorded_reply']}", ""]
        for variant in VARIANTS:
            for item in (i for i in scored.get(variant, []) if i["id"] == row["id"]):
                similarity = "empty" if item["similarity"] is None else f"{item['similarity']:.2f}"
                text = item["draft"].replace("\n", " ")
                lines.append(f"- **{variant}** (seed {item['seed']}, {similarity}): {text}")
        lines.append("")
    return "\n".join(lines) + "\n"


# ------------------------------------------------- their words (after A/B 2)
#
# A/B 2: on 12 of the 17 turns the 27B wrote, the drafting prompt's current
# message was the router's reading of what the person said, not their words,
# and on some the reading is what got answered. The engine now shows their
# words on a turn the move owns (turn_move.with_their_words). This replays
# exactly those turns, written by the 27B, as recorded and with their words.

THEIRS_RUNS = ("ab2-deep-s1", "ab2-deep-s2")
THEIRS_OUT = SL_ROOT / "ab2-freeze" / "theirs"


def collect_theirs(runs=THEIRS_RUNS) -> list[dict]:
    """Every 27B-written turn whose prompt showed the router's reading."""
    rows = []
    for run in runs:
        for path in sorted(glob.glob(str(SL_ROOT / run / "rt" / "*" / "turn_trace" / "*.jsonl"))):
            learner = Path(path).parents[1].name
            turns = _turns(path)
            for number, turn in enumerate(turns, 1):
                context = turn.get("context") or {}
                applied = context.get("turn_move_applied") or {}
                if not applied.get("written_by"):
                    continue
                route = context.get("route") or {}
                draft = next(c for c in turn["model_calls"] if "collect_answer" in str(c.get("caller")))
                final = draft["messages"][-1]["content"]
                reading = str(route.get("normalized_request") or "")
                theirs, changes = turn_move.with_their_words(final, shown=reading, said=turn.get("user_input", ""))
                if not changes:
                    continue
                conversation = []
                for earlier in turns[:number - 1]:
                    conversation += [{"role": "user", "content": earlier.get("user_input", "")},
                                     {"role": "assistant", "content": earlier.get("display", "")}]
                sent = int(draft.get("history") or 0)
                history = conversation[-sent:] if sent else []
                stored = [m.get("content", "") for m in draft["messages"][1:-1]]
                rebuilt = [turn_trace._clip(m["content"], turn_trace._HISTORY_LIMIT) for m in history[-2:]]
                rows.append({
                    "id": f"{run}/{learner}/t{number}", "said": turn.get("user_input", ""), "reading": reading,
                    "intent": route.get("intent", ""), "system": draft["messages"][0].get("content", ""),
                    "history": history, "recorded_final": final, "theirs_final": theirs,
                    # Over the trace's limit: the middle of the search evidence is cut.
                    "evidence_cut": "chars cut]" in final,
                    "history_matches_trace": stored == rebuilt,
                    "options": dict(draft.get("options") or {}), "model": applied["written_by"],
                    "recorded_reply": turn.get("display", ""),
                })
    return rows


def run_theirs() -> int:
    import ollama

    from evals.phase2 import unload_models

    rows = collect_theirs()
    THEIRS_OUT.mkdir(parents=True, exist_ok=True)
    path = THEIRS_OUT / "drafts.jsonl"
    done = set()
    if path.exists():
        done = {(d["id"], d["variant"], d["seed"]) for d in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    unload_models()
    for row in rows:
        for variant, final in (("recorded", row["recorded_final"]), ("theirs", row["theirs_final"])):
            for seed in SEEDS:
                if (row["id"], variant, seed) in done:
                    continue
                messages = [{"role": "system", "content": row["system"]}, *row["history"],
                            {"role": "user", "content": final}]
                response = ollama.chat(model=row["model"], messages=messages, stream=False,
                                       options={**row["options"], "seed": seed}, think=False)
                text = str((response.get("message") or {}).get("content") or "").strip()
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"id": row["id"], "variant": variant, "seed": seed,
                                             "draft": text}, ensure_ascii=False) + "\n")
        print(f"{row['id']} done", flush=True)
    unload_models()
    return 0


def report_theirs() -> str:
    rows = collect_theirs()
    drafts = [json.loads(line) for line in (THEIRS_OUT / "drafts.jsonl").read_text(encoding="utf-8").splitlines()]
    lines = ["# The writer shown their words: the affected turns, replayed", "",
             f"{len(rows)} turns the 27B wrote in {', '.join(THEIRS_RUNS)} whose prompt showed the router's "
             f"reading. Each is drafted again by the 27B, {len(SEEDS)} drafts as recorded and {len(SEEDS)} with "
             "the person's own words as the current message. Drafts only. History matches the trace on "
             f"{sum(r['history_matches_trace'] for r in rows)} of {len(rows)}.", ""]
    for cut, title in ((False, "Replayed exactly"),
                       (True, "Search turns: evidence shortened by the trace (same in both variants)")):
        group = [r for r in rows if r["evidence_cut"] == cut]
        if not group:
            continue
        lines += [f"## {title} ({len(group)})", ""]
        for row in group:
            lines += [f"### {row['id']} ({row['intent']})", "", f"- They said: {row['said']}",
                      f"- The router's reading: {row['reading']}",
                      f"- What she said in the run: {row['recorded_reply']}", ""]
            for variant in ("recorded", "theirs"):
                for item in (d for d in drafts if d["id"] == row["id"] and d["variant"] == variant):
                    text = item["draft"].replace("\n", " ")
                    lines.append(f"- **{variant}** (seed {item['seed']}): {text}")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--theirs-run", action="store_true")
    parser.add_argument("--theirs-report", action="store_true")
    args, _rest = parser.parse_known_args()
    if args.theirs_run:
        return run_theirs()
    if args.theirs_report:
        text = report_theirs()
        (THEIRS_OUT / "THEIRS.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--check", action="store_true", help="what would be replayed, and one prompt per variant")
    args = parser.parse_args()
    if args.check:
        rows, left_out = collect()
        print(f"{len(rows)} turns; left out {left_out}; history matches trace on "
              f"{sum(r['history_matches_trace'] for r in rows)}")
        for row in rows:
            print(row["id"], "applied" if row["applied"] else "shadow", "history", len(row["history"]),
                  "goal" if row["goal"] else "no goal", row["options"])
        return 0
    if args.run:
        return run()
    if args.report:
        text = report()
        (OUT / "REPLAY.md").write_text(text, encoding="utf-8")
        print(text.split("\n## ")[0])
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
