"""The turn-move owner (brain/turn_move.py) in shadow, over recorded turns.

    .venv/Scripts/python.exe -m evals.turn_move_shadow pilot4-current [pilot3-current ...]
    .venv/Scripts/python.exe -m evals.turn_move_shadow --live runtime/turn_trace

Replays each conversation's turns through ``turn_move.Record`` exactly as the
engine would -- decide before the turn, record after it -- from what the
turn traces hold, and compares the move with what production did: the goal
it used, and whether it kept the conversation in the prompt. Nothing is
changed and no model is called. docs/TURN_MOVE_SHADOW.md.

A live trace (from the engine itself, once the shadow is wired in) carries
the move it decided under ``context.turn_move``; ``--live`` reads those
instead of replaying.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import turn_move  # noqa: E402

SL_ROOT = PROJECT_ROOT / "runtime" / "evals" / "sl"

# Written from the pilot-4 diagnosis before the first replay: (run, learner,
# turn) -> the move a person reading the transcript would want.
EXPECTED = {
    ("pilot4-current", "sl_ko_greenhouse", 4): turn_move.CONTINUE,
    ("pilot4-current", "sl_ko_greenhouse", 5): turn_move.DIFFERENTLY,
    ("pilot4-current", "sl_ko_greenhouse", 6): turn_move.DIFFERENTLY,
    ("pilot4-current", "sl_ko_greenhouse", 7): turn_move.DIFFERENTLY,
    ("pilot4-current", "sl_ko_greenhouse", 8): turn_move.DIFFERENTLY,
    ("pilot4-current", "sl_quantization_cs", 6): turn_move.CHECK,
    ("pilot4-current", "sl_quantization_cs", 7): turn_move.CHECK,
}


_EMBED = []


def embedder():
    """bge-m3, the model memory loads in her own use; once per process."""
    if not _EMBED:
        from memory.embedding import EmbeddingModel
        _EMBED.append(EmbeddingModel().encode)
    return _EMBED[0]


def _turns(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def replay(turns: list[dict], embed=None) -> list[dict]:
    """The move for every turn of one conversation, with what production did."""
    record = turn_move.Record(embed=embed)
    rows = []
    for number, turn in enumerate(turns, 1):
        context = turn.get("context") or {}
        route = context.get("route") or {}
        limits = context.get("limits") or {}
        move = record.decide(
            turn.get("user_input", ""),
            is_follow_up=bool(route.get("is_follow_up")),
            topic_shift=bool(route.get("topic_shift")),
            speech_act=str(route.get("speech_act") or ""),
            budget=str(limits.get("budget") or ""),
        )
        record.record(turn.get("user_input", ""), turn.get("display", ""))
        log = "\n".join(turn.get("log") or ())
        rows.append({
            "turn": number, "said": turn.get("user_input", ""), "move": move,
            "intent": route.get("intent", ""), "goal": limits.get("shape", ""),
            "budget": limits.get("budget", ""),
            "started_clean": "[Context] Starting clean" in log,
            "language": context.get("language", "en"),
        })
    return rows


def run_rows(name: str) -> dict:
    out = {}
    for path in sorted(glob.glob(str(SL_ROOT / name / "rt" / "*" / "turn_trace" / "*.jsonl"))):
        learner = Path(path).parents[1].name
        out[learner] = replay(_turns(path), embedder())
    return out


def report(names: list[str]) -> str:
    lines = ["# Turn moves in shadow", "",
             "Replayed from recorded turns; nothing changed, no model called "
             "(evals/turn_move_shadow.py, docs/TURN_MOVE_SHADOW.md).", ""]
    for name in names:
        conversations = run_rows(name)
        moves = Counter(row["move"].move for rows in conversations.values() for row in rows)
        follow_ups = [row for rows in conversations.values() for row in rows if row["move"].thread_turns]
        kept = sum(1 for row in follow_ups if row["started_clean"])
        lines += [f"## {name}", "",
                  "| move | turns | production's goal on those turns |", "|---|---|---|"]
        for move in (turn_move.FIRST, turn_move.CONTINUE, turn_move.DIFFERENTLY, turn_move.CHECK,
                     turn_move.NOT_EXPLAINING):
            rows = [row for rows in conversations.values() for row in rows if row["move"].move == move]
            goals = Counter(row["goal"] or row["budget"] or "-" for row in rows)
            lines.append(f"| {move} | {len(rows)} | "
                         f"{', '.join(f'{g} {n}' for g, n in goals.most_common()) or '-'} |")
        lines += ["", f"Follow-ups the thread kept that production started clean: {kept} of "
                      f"{len(follow_ups)}.", ""]
        flagged = [(learner, row) for learner, rows in conversations.items() for row in rows
                   if row["move"].move in (turn_move.DIFFERENTLY, turn_move.CHECK)]
        if flagged:
            lines += ["| learner | turn | move | they said | asked before / challenge | production |",
                      "|---|---|---|---|---|---|"]
            for learner, row in flagged:
                move = row["move"]
                why = (f"turns {', '.join(map(str, move.asked_before))}" if move.move == turn_move.DIFFERENTLY
                       else f"{move.challenged}" + (f" {', '.join(move.their_numbers)}" if move.their_numbers
                                                     else ""))
                said = row["said"].replace("|", "/")[:110]
                lines.append(f"| `{learner[3:]}` | {row['turn']} | {move.move} | {said} | {why} | "
                             f"{row['intent']} / {row['goal'] or row['budget']} |")
            lines.append("")
    checked = []
    for (name, learner, turn), wanted in EXPECTED.items():
        if name not in names:
            continue
        rows = run_rows(name).get(learner) or []
        got = next((row["move"].move for row in rows if row["turn"] == turn), "(no turn)")
        checked.append((name, learner, turn, wanted, got))
    if checked:
        hits = sum(1 for *_, wanted, got in checked if wanted == got)
        lines += ["## Written expectations", "", f"{hits} of {len(checked)} as expected.", "",
                  "| run | learner | turn | expected | got |", "|---|---|---|---|---|"]
        lines += [f"| {n} | `{l[3:]}` | {t} | {w} | {g}{'' if w == g else ' **≠**'} |"
                  for n, l, t, w, g in checked]
        lines.append("")
    return "\n".join(lines) + "\n"


def score(name: str, labels_path: str) -> str:
    """The recorded moves against a person's labels, written before looking.

    ``labels_path`` is JSON: {learner: {turn: "reask" | "complaint" | "challenge"}}.
    A labelled turn is caught when the move is ``differently`` or ``check``; a
    turn with no label that gets one of those is a false alarm.
    """
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    caught, missed, false_alarms = Counter(), [], []
    total = Counter()
    for path in sorted(glob.glob(str(SL_ROOT / name / "rt" / "*" / "turn_trace" / "*.jsonl"))):
        learner = Path(path).parents[1].name
        wanted = {int(k): v for k, v in (labels.get(learner) or {}).items()}
        for number, turn in enumerate(_turns(path), 1):
            move = ((turn.get("context") or {}).get("turn_move") or {}).get("move", "")
            flagged = move in (turn_move.DIFFERENTLY, turn_move.CHECK)
            kind = wanted.get(number)
            if kind:
                total[kind] += 1
                if flagged:
                    caught[kind] += 1
                else:
                    missed.append(f"{learner[3:]} t{number} ({kind}): {move}")
            elif flagged:
                false_alarms.append(f"{learner[3:]} t{number}: {move} -- {turn.get('user_input', '')[:90]}")
    lines = [f"# Scored against {Path(labels_path).name}", ""]
    for kind in sorted(total):
        lines.append(f"- {kind}: caught {caught[kind]} of {total[kind]}")
    lines.append(f"- false alarms: {len(false_alarms)}")
    lines += ["", "Missed:"] + [f"  - {m}" for m in missed] + ["", "False alarms:"] + [f"  - {f}" for f in false_alarms]
    return "\n".join(lines) + "\n"


NEAR_COPY = 0.90      # docs/TURN_MOVE_AB.md, fixed before the runs


def _unit(vector) -> list:
    values = [float(x) for x in vector]
    norm = sum(x * x for x in values) ** 0.5
    return [x / norm for x in values] if norm else values


def differently_turns(name: str) -> list[dict]:
    """Every turn a run recorded as ``differently``: her reply, her reply to
    the earlier asking, how close the two are, and what the judge said."""
    from evals import sim_learner as sl

    _records, judged, _meta = sl._load(name)
    embed = embedder()
    rows = []
    for path in sorted(glob.glob(str(SL_ROOT / name / "rt" / "*" / "turn_trace" / "*.jsonl"))):
        learner = Path(path).parents[1].name
        turns = _turns(path)
        verdicts = (judged.get(learner) or {}).get("turns") or {}
        for number, turn in enumerate(turns, 1):
            context = turn.get("context") or {}
            move = context.get("turn_move") or {}
            if move.get("move") != turn_move.DIFFERENTLY or not move.get("asked_before"):
                continue
            earlier = turns[move["asked_before"][-1] - 1]
            reply, before = turn.get("display", ""), earlier.get("display", "")
            a, b = _unit(embed(reply)), _unit(embed(before))
            draft = next((c for c in turn.get("model_calls") or ()
                          if "collect_answer" in str(c.get("caller"))), {})
            load = float(draft.get("load_duration") or 0)
            rows.append({
                "run": name, "learner": learner, "turn": number, "said": turn.get("user_input", ""),
                "reply": reply, "earlier_reply": before,
                "similarity": sum(x * y for x, y in zip(a, b)),
                "judge": (verdicts.get(number) or {}).get("after_confusion", ""),
                "applied": bool(context.get("turn_move_applied")),
                "written_by": (context.get("turn_move_applied") or {}).get("written_by", ""),
                "attempts": move.get("attempts", 0),
                # What the turn cost: the whole turn, the drafting call, the
                # model load inside it (Ollama reports nanoseconds), and the
                # turn after, which pays for the swap back.
                "seconds": float(turn.get("elapsed") or 0),
                "draft_seconds": float(draft.get("latency") or 0),
                "load_seconds": load / 1e9 if load > 1e6 else load,
                "next_seconds": float(turns[number].get("elapsed") or 0) if number < len(turns) else None,
            })
    return rows


def _all_turn_seconds(name: str) -> list[float]:
    return [float(turn.get("elapsed") or 0)
            for path in glob.glob(str(SL_ROOT / name / "rt" / "*" / "turn_trace" / "*.jsonl"))
            for turn in _turns(path)]


def _med(values) -> str:
    values = sorted(v for v in values if v is not None)
    return f"{values[len(values) // 2]:.1f}" if values else "-"


def ab(off_runs: list[str], on_runs: list[str]) -> str:
    """OFF against ON on the measures docs/TURN_MOVE_AB.md fixed."""
    from evals import sim_learner as sl

    lines = ['# Acting on "explain differently": OFF against ON', "",
             "Measures and criteria: docs/TURN_MOVE_AB.md, fixed before the runs.", ""]
    summary = {}
    for label, runs in (("OFF", off_runs), ("ON", on_runs)):
        rows = [row for name in runs for row in differently_turns(name)]
        judged = [row for row in rows if row["judge"] in ("changed approach", "repeated")]
        conversations = sum(len(sl._load(name)[0]) for name in runs)
        chains = Counter((row["run"], row["learner"]) for row in rows)
        summary[label] = {
            "rows": rows, "turns": len(rows),
            "near_copies": sum(1 for row in rows if row["similarity"] >= NEAR_COPY),
            "changed": sum(1 for row in judged if row["judge"] == "changed approach"),
            "judged": len(judged),
            "applied": sum(1 for row in rows if row["applied"]),
            "conversations": conversations, "with_re_asks": len(chains),
            "longest": max(chains.values()) if chains else 0,
        }
    lines += ["## On the turns marked `differently`", "",
              "| | OFF | ON |", "|---|---|---|"]

    def cell(label, key, of=None):
        value = summary[label][key]
        return f"{value}/{summary[label][of]}" if of else str(value)
    lines += [
        f"| turns marked | {cell('OFF', 'turns')} | {cell('ON', 'turns')} |",
        f"| ...acted on | {cell('OFF', 'applied')} | {cell('ON', 'applied')} |",
        f"| near-copies of her earlier answer (cosine >= {NEAR_COPY}) | {cell('OFF', 'near_copies', 'turns')} "
        f"| {cell('ON', 'near_copies', 'turns')} |",
        f"| judge: changed approach | {cell('OFF', 'changed', 'judged')} | {cell('ON', 'changed', 'judged')} |",
        f"| conversations with a re-ask | {cell('OFF', 'with_re_asks', 'conversations')} "
        f"| {cell('ON', 'with_re_asks', 'conversations')} |",
        f"| most re-asks in one conversation | {cell('OFF', 'longest')} | {cell('ON', 'longest')} |", "",
    ]
    lines += ["## What those turns cost (seconds; median, and the slowest)", "",
              "| | OFF | ON |", "|---|---|---|"]
    every = {"OFF": [s for name in off_runs for s in _all_turn_seconds(name)],
             "ON": [s for name in on_runs for s in _all_turn_seconds(name)]}

    def timing(label, key):
        values = [row[key] for row in summary[label]["rows"] if row[key] is not None]
        return f"{_med(values)} (max {max(values):.1f})" if values else "-"
    lines += [
        f"| the whole turn | {timing('OFF', 'seconds')} | {timing('ON', 'seconds')} |",
        f"| ...the drafting call | {timing('OFF', 'draft_seconds')} | {timing('ON', 'draft_seconds')} |",
        f"| ...of which loading the model | {timing('OFF', 'load_seconds')} | {timing('ON', 'load_seconds')} |",
        f"| the turn after | {timing('OFF', 'next_seconds')} | {timing('ON', 'next_seconds')} |",
        f"| every turn of the arm, for scale | {_med(every['OFF'])} | {_med(every['ON'])} |", "",
        "In this setup the simulated person is played by the 27B, so the 8B is reloaded at the start "
        "of every turn in both arms; in her own use only the turns around a re-ask would pay a swap.", "",
    ]
    lines += ["## Whole runs", "", "| run | arm | verified | pitched | natural | changed after confusion "
              "| ending not drafted |", "|---|---|---|---|---|---|---|"]
    for label, runs in (("OFF", off_runs), ("ON", on_runs)):
        for name in runs:
            records, judged, _meta = sl._load(name)
            m = sl.measures(records, judged)
            lines.append(f"| {name} | {label} | {sl._pair(m['verified_understood'])} "
                         f"| {sl._pair(m['fits_this_person'])} | {sl._pair(m['natural_turns'])} "
                         f"| {sl._pair(m['changed_after_confusion'])} | {sl._pair(m['ending_not_drafted'])} |")
    lines.append("")
    for label in ("OFF", "ON"):
        lines += [f"## Every `differently` turn, {label}", ""]
        for row in summary[label]["rows"]:
            lines += [f"**{row['run']} / {row['learner'][3:]} / turn {row['turn']}** "
                      f"(asked {row['attempts'] + 1} times; similarity to her earlier answer "
                      f"{row['similarity']:.2f}; judge: {row['judge'] or '-'})", "",
                      f"- They said: {row['said']}",
                      f"- Earlier she said: {row['earlier_reply']}",
                      f"- This time: {row['reply']}", ""]
    return "\n".join(lines) + "\n"


def live(where: str) -> str:
    """Moves the engine recorded itself (context.turn_move), per conversation,
    checked against a replay of the same turns. ``where`` is a folder of
    turn-trace files, or the name of a simulated-learner run."""
    run = SL_ROOT / where
    if run.is_dir():
        paths = sorted(glob.glob(str(run / "rt" / "*" / "turn_trace" / "*.jsonl")))
    else:
        paths = sorted(glob.glob(str(Path(where) / "*.jsonl")))
    rows, unrecorded, disagree = [], 0, []
    for path in paths:
        turns = _turns(path)
        name = Path(path).parents[1].name if run.is_dir() else Path(path).stem
        for row, turn in zip(replay(turns, embedder()), turns):
            move = (turn.get("context") or {}).get("turn_move")
            if not move:
                unrecorded += 1
                continue
            rows.append((name, row["turn"], turn.get("user_input", ""), move, turn.get("display", "")))
            if move["move"] != row["move"].move:
                disagree.append((name, row["turn"], move["move"], row["move"].move))
    counts = Counter(move["move"] for *_, move, _ in rows)
    lines = ["# Turn moves recorded live", "",
             f"{len(rows)} turns recorded ({unrecorded} without a move: turns that never reached "
             f"the answer path). " + ", ".join(f"{k} {v}" for k, v in counts.most_common()) + ".",
             f"Replay of the same turns disagrees on {len(disagree)}"
             + (": " + "; ".join(f"{n} t{t} live {a} vs replay {b}" for n, t, a, b in disagree)
                if disagree else "."), ""]
    flagged = [r for r in rows if r[3]["move"] in (turn_move.DIFFERENTLY, turn_move.CHECK)]
    if flagged:
        lines += ["| conversation | turn | move | they said | production goal | history kept | she said |",
                  "|---|---|---|---|---|---|---|"]
        for name, number, said, move, reply in flagged:
            lines.append(
                f"| `{name}` | {number} | {move['move']} | {said.replace('|', '/')[:100]} | "
                f"{move.get('production_goal') or '-'} | {move.get('history_inherited')} | "
                f"{reply.replace('|', '/').replace(chr(10), ' ')[:140]} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--live", default="")
    parser.add_argument("--score", nargs=2, metavar=("RUN", "LABELS"))
    parser.add_argument("--ab-off", nargs="*", default=[])
    parser.add_argument("--ab-on", nargs="*", default=[])
    args = parser.parse_args()
    if args.ab_off or args.ab_on:
        print(ab(args.ab_off, args.ab_on))
        return 0
    if args.score:
        print(score(*args.score))
        return 0
    if args.live:
        print(live(args.live))
        return 0
    print(report(args.runs or ["pilot4-current"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
