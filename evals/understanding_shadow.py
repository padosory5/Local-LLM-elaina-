"""What the person has understood so far, read from the conversation alone: can it be kept?

    .venv/Scripts/python.exe -m evals.understanding_shadow --estimate u1 --runs tw2-8b-s3 tw2-split-s3
    .venv/Scripts/python.exe -m evals.understanding_shadow --essentials u1
    .venv/Scripts/python.exe -m evals.understanding_shadow --score u1

Step 2 of the understanding-loop plan, offline: nothing here runs in the backend
and nothing the model is shown in a real turn changes. It replays recorded
simulated-learner conversations (evals/sim_learner.py) and, at each point where
the person has just spoken, asks a model for a small record of where they are:

    said     which points of the explanation Elaina has actually made
    held     which of them the person has shown they understand
    lost     whether their latest message shows they did not follow her reply
    unknown  words they have shown they do not know
    believes something they believe that is wrong

It is given the conversation and the points, never the person's hidden brief.
The record is then scored against what the eval knows and the engine does not:
the simulated person's own account after each reply (which ideas it now holds,
what it did not follow) and the judge's reading of the turn each idea was
conveyed on.

The points are the eval's checklist. In real use she would have to write her
own, so ``--essentials`` asks the model for them from the opening question
alone and has the judge's model say which checklist ideas they cover.

Why (runtime/evals/sl/tw2_report.md): with the 8B, the 27B behind the 8B's
router, and the 27B alone, the conversations that failed mostly failed the same
way -- an answer restated, a "no" to a summary that was right, the last idea
never raised. Each is a failure to keep track of the person, not of wording.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import sim_learner as sl  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402

OUT = PROJECT_ROOT / "runtime" / "evals" / "understanding"
MODELS = {"8b": sl.M8, "27b": sl.M27}
# Ollama cuts a prompt at 4,096 tokens unless told otherwise; a long thread is longer.
OPTIONS = {"temperature": 0, "seed": 0, "num_predict": 400, "num_ctx": 6144}

STATE_PROMPT = """You keep a record of what a person has understood so far, in a spoken conversation where an assistant named Elaina is explaining something to them. Judge only from what was actually said.

THE POINTS OF THIS EXPLANATION
{points}

For each point decide two things:
- said: Elaina has actually stated this point, in any words, in one of her replies so far.
- held: the person has shown they understand it, by saying it in their own words or by building on it correctly. "Okay" on its own is not understanding, and asking about a point is not understanding it. A point they stated wrongly is not held.

And three things about the person:
- lost: their latest message shows they did not follow her last reply (they ask what something means, ask again, object, or say they do not get it).
- unknown_words: words or terms they have shown they do not know, as they said them.
- believes: one short sentence for something they currently believe that is wrong, or "" when there is nothing.

Return one JSON object and nothing else:
{{"points": {{{shape}}}, "lost": true or false, "unknown_words": [], "believes": ""}}"""

ESSENTIALS_PROMPT = """Someone asks a voice assistant: "{opening}"

Before answering, write down the three things they need to come away understanding, most important first. One plain sentence each, in the language of the question. Return one JSON object and nothing else:
{{"points": ["...", "...", "..."]}}"""

COVERAGE_PROMPT = """KEY IDEAS
{ideas}

A LIST SOMEONE WROTE
{listed}

For each key idea, say whether the list expresses it: the same meaning, in any words. Return one JSON object and nothing else:
{{"covered": {{{shape}}}}}"""


def _chat(model: str, messages: list[dict], **options) -> str:
    import ollama

    response = ollama.chat(model=model, messages=messages, stream=False, format="json", think=False,
                           options={**OPTIONS, **options})
    return str((response.get("message") or {}).get("content") or "")


def _records(run: str) -> list[dict]:
    path = sl.ROOT / run / "dialogues.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _verdicts(run: str) -> dict:
    path = sl.ROOT / run / "judged.jsonl"
    if not path.exists():
        return {}
    return {row["learner"]: (row.get("verdict") or {})
            for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())}


def next_message(record: dict, step: int) -> str:
    """What the person said after her reply ``step`` (1-based); "" when the talk ended there."""
    transcript = record["transcript"]
    if step < len(transcript):
        return str(transcript[step]["person"])
    return str(record.get("closing") or "")


def state_messages(learner, record: dict, step: int) -> list[dict]:
    """The conversation up to the person's message after reply ``step``. No hidden brief."""
    system = STATE_PROMPT.format(
        points="\n".join(f"- {i}: {idea}" for i, idea in learner.understand),
        shape=", ".join(f'"{i}": {{"said": true or false, "held": true or false}}' for i in learner.item_ids))
    lines = []
    for turn in record["transcript"][:step]:
        lines += [f"Person: {turn['person']}", f"Elaina: {turn['elaina']}"]
    lines.append(f"Person (latest): {next_message(record, step)}")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_state(text: str, learner) -> dict | None:
    try:
        data = json.loads(str(text or "").strip())
    except (TypeError, ValueError):
        return None
    points = data.get("points") if isinstance(data, dict) else None
    if not isinstance(points, dict) or set(points) != set(learner.item_ids):
        return None
    words = data.get("unknown_words") or []
    return {
        "said": sorted(i for i, p in points.items() if isinstance(p, dict) and p.get("said") is True),
        "held": sorted(i for i, p in points.items() if isinstance(p, dict) and p.get("held") is True),
        "lost": data.get("lost") is True,
        "unknown_words": [str(w).strip() for w in words if str(w).strip()] if isinstance(words, list) else [],
        "believes": str(data.get("believes") or "").strip(),
    }


def estimate(name: str, runs: list[str], model: str) -> int:
    learners = {x.id: x for x in sl.load_learners()[0]}
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"states-{model}.jsonl"
    done = set()
    if path.exists():
        done = {(r["run"], r["learner"], r["step"])
                for r in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())}
    for run in runs:
        started, wrote = time.monotonic(), 0
        for record in _records(run):
            learner = learners[record["learner"]]
            for step in range(1, len(record["transcript"]) + 1):
                if not next_message(record, step) or (run, learner.id, step) in done:
                    continue
                began = time.monotonic()
                raw = _chat(MODELS[model], state_messages(learner, record, step))
                row = {"run": run, "learner": learner.id, "step": step, "model": model,
                       "state": parse_state(raw, learner), "seconds": round(time.monotonic() - began, 2)}
                if row["state"] is None:
                    row["raw"] = raw[:600]
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                wrote += 1
        print(f"{run}: {wrote} states by the {model}, {time.monotonic() - started:.0f}s", flush=True)
    return 0


def essentials(name: str, model: str, seeds: int) -> int:
    learners, _settings = sl.load_learners()
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"essentials-{model}.jsonl"
    rows = []
    for learner in learners:
        for seed in range(seeds):
            raw = _chat(MODELS[model], [{"role": "user", "content": ESSENTIALS_PROMPT.format(opening=learner.opening)}],
                        temperature=0.6, seed=seed)
            try:
                listed = [str(p).strip() for p in (json.loads(raw).get("points") or []) if str(p).strip()][:3]
            except (TypeError, ValueError, AttributeError):
                listed = []
            rows.append({"learner": learner.id, "seed": seed, "model": model, "points": listed})
    # The judge's model reads the lists once the writer is done, so the card holds one at a time.
    for row in rows:
        learner = next(x for x in learners if x.id == row["learner"])
        covered = {}
        if row["points"]:
            raw = _chat(DEFAULT_JUDGE, [{"role": "user", "content": COVERAGE_PROMPT.format(
                ideas="\n".join(f"- {i}: {idea}" for i, idea in learner.understand),
                listed="\n".join(f"{n}. {p}" for n, p in enumerate(row["points"], 1)),
                shape=", ".join(f'"{i}": true or false' for i in learner.item_ids))}], num_predict=200)
            try:
                covered = {i: json.loads(raw).get("covered", {}).get(i) is True for i in learner.item_ids}
            except (TypeError, ValueError, AttributeError):
                covered = {}
        row["covered"] = covered
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} lists by the {model}, judged by {DEFAULT_JUDGE}", flush=True)
    return 0


class Tally:
    """A yes/no reading against a reference."""

    def __init__(self) -> None:
        self.c = Counter()

    def add(self, said: bool, truth: bool) -> None:
        self.c[(bool(said), bool(truth))] += 1

    def line(self, label: str) -> str:
        tp, fp, fn, tn = self.c[(True, True)], self.c[(True, False)], self.c[(False, True)], self.c[(False, False)]
        n = tp + fp + fn + tn
        if not n:
            return f"   {label}: nothing to score"
        return (f"   {label}: n {n}, true in the reference {100 * (tp + fn) / n:.0f}% | agrees {100 * (tp + tn) / n:.0f}% | "
                f"when it says yes it is right {tp}/{tp + fp} ({100 * tp / max(1, tp + fp):.0f}%) | "
                f"of the true ones it finds {tp}/{tp + fn} ({100 * tp / max(1, tp + fn):.0f}%) | "
                f"when it says no it is right {tn}/{tn + fn} ({100 * tn / max(1, tn + fn):.0f}%)")


def score(name: str, model: str) -> str:
    learners = {x.id: x for x in sl.load_learners()[0]}
    path = OUT / name / f"states-{model}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = {run: {r["learner"]: r for r in _records(run)} for run in {r["run"] for r in rows}}
    verdicts = {run: _verdicts(run) for run in records}
    said, held, lost, lost_judge, gaps = Tally(), Tally(), Tally(), Tally(), Tally()
    unreadable = flips = 0
    seconds, last = [], {}
    previous = defaultdict(set)
    beliefs = defaultdict(list)
    for row in sorted(rows, key=lambda r: (r["run"], r["learner"], r["step"])):
        state = row["state"]
        if state is None:
            unreadable += 1
            continue
        seconds.append(row["seconds"])
        record = records[row["run"]][row["learner"]]
        verdict = verdicts[row["run"]].get(row["learner"]) or {}
        learner, step = learners[row["learner"]], row["step"]
        transcript = record["transcript"]
        really_held = {i for turn in transcript[:step] for i in (turn.get("learner") or {}).get("gained") or ()}
        when = verdict.get("item_turns") or {}
        for i in learner.item_ids:
            held.add(i in state["held"], i in really_held)
            if when:
                said.add(i in state["said"], 0 < int(when.get(i) or 0) <= step)
        key = (row["run"], row["learner"])
        flips += len(previous[key] - set(state["held"]))
        previous[key] = set(state["held"])
        lost.add(state["lost"], bool((transcript[step - 1].get("learner") or {}).get("confused_by")))
        if step < len(transcript):
            tag = ((verdict.get("turns") or {}).get(str(step + 1)) or {}).get("after_confusion")
            if tag:
                lost_judge.add(state["lost"], tag in ("changed approach", "repeated"))
        last[key] = (state, when, learner)
        if state["believes"]:
            beliefs[row["learner"]].append(state["believes"])
    # At the last point of each conversation: the ideas the judge found never conveyed.
    for state, when, learner in last.values():
        for i in learner.item_ids:
            if when:
                gaps.add(i not in state["said"], int(when.get(i) or 0) == 0)
    lines = [f"# Understanding record, kept by the {model} ({name})", "",
             f"{len(rows)} points in {len(last)} conversations of {len(records)} runs; unreadable {unreadable}; "
             f"seconds per record median {sorted(seconds)[len(seconds) // 2] if seconds else 0}",
             "", "Against the eval's references:",
             said.line("said (judge: the turn each idea was conveyed)"),
             held.line("held (the person's own account)"),
             lost.line("lost (the person's own account of her last reply)"),
             lost_judge.line("lost (judge: her next reply came after confusion)"),
             gaps.line("never said, at the end (judge: idea not conveyed)"),
             f"   an idea marked held and later not: {flips} times", "", "What it wrote under `believes`, per learner:"]
    for who, said_ in beliefs.items():
        common = Counter(said_).most_common(2)
        lines.append(f"   {who} (believed: {learners[who].misconception or 'nothing wrong'}) -- {len(said_)} of "
                     f"{sum(1 for r in rows if r['learner'] == who and r['state'])} records:")
        lines += [f"      {count}x {text[:150]}" for text, count in common]
    ess = OUT / name / f"essentials-{model}.jsonl"
    if ess.exists():
        lists = [json.loads(line) for line in ess.read_text(encoding="utf-8").splitlines() if line.strip()]
        lines += ["", "Her own three points, from the opening question alone, against the checklist:"]
        total = Counter()
        for who in learners:
            mine = [r for r in lists if r["learner"] == who and r["covered"]]
            if not mine:
                continue
            per = {i: sum(r["covered"].get(i, False) for r in mine) for i in learners[who].item_ids}
            for i, count in per.items():
                total["covered"] += count
                total["asked"] += len(mine)
            lines.append(f"   {who}: " + ", ".join(f"{i} {count}/{len(mine)}" for i, count in per.items()))
        lines.append(f"   all: {total['covered']}/{total['asked']} checklist ideas covered")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--estimate", default="")
    parser.add_argument("--essentials", default="")
    parser.add_argument("--score", default="")
    parser.add_argument("--runs", nargs="*", default=[])
    parser.add_argument("--model", choices=sorted(MODELS), default="8b")
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # the report quotes Korean
    if args.estimate:
        return estimate(args.estimate, args.runs, args.model)
    if args.essentials:
        return essentials(args.essentials, args.model, args.seeds)
    if args.score:
        print(score(args.score, args.model))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
