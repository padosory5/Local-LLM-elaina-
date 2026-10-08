"""A targeted regression for the turn-move owner under "deep", with the
their-words fix: scripted conversations against the real backend.

    .venv/Scripts/python.exe -m evals.turn_move_regression --run reg1
    .venv/Scripts/python.exe -m evals.turn_move_regression --run off1 --arm current
    .venv/Scripts/python.exe -m evals.turn_move_regression --report reg1

docs/TURN_MOVE_AB.md. The cases are evals/regressions/turn_move_regression.json:
the person's messages of the A/B 2 conversations that went wrong, and new
ones (knowledge re-asks, search re-asks, a re-ask worded far from the
router's reading, exact repeats). No simulated person: the lines are sent as
written, one fresh backend per case, in the ``move_deep`` arm of
evals/sim_learner.py -- the setup A/B 2 ran in -- so the 8B stays loaded
between turns and a move-owned turn pays the swap it would pay in her own use.

Checked from the turn traces, fixed before the first run:

On every turn the move owns (``turn_move_applied``):
  written_by_27b   the drafting call named the 27B
  their_words      the drafting prompt's current message is exactly what the
                   person said
  not_the_reading  the router's reading is not the current message
  trace_says_so    question_shown is "their words" where the prompt was a
                   factual or search one built from a different reading, and
                   "as built" where it carried their words already
  not_a_copy       bge-m3 cosine to her earlier answers to that question < 0.90

On every other turn:
  written_by_8b    the drafting call named the 8B
  built_as_before  a factual prompt still carries the router's reading

On every turn (added with the follow-up search fix, 2026-10-03):
  no_reasoning     the reply carries no <think> markup
  same_lookup      a question asked again, searched with no query from the
                   router, searched what an earlier turn of the conversation
                   searched

Whether a reply answers what was asked is read by hand; the report prints
every move-owned turn for that.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ELAINA_TTS", "off")

from brain import turn_move  # noqa: E402
from evals import sim_learner as sl  # noqa: E402
from evals.phase2 import M8, M27  # noqa: E402
from evals.turn_move_shadow import NEAR_COPY, _turns, _unit, embedder  # noqa: E402

CASES = PROJECT_ROOT / "evals" / "regressions" / "turn_move_regression.json"
ROOT = PROJECT_ROOT / "runtime" / "evals" / "tmr"
ARM = "move_deep"
FACTUAL = turn_move.CURRENT_MESSAGE            # "CURRENT USER MESSAGE\n"
CONVERSATIONAL = "Current user message:\n"


def load_cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def run(name: str, only: list[str], arm: str = ARM) -> int:
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
        "arm": arm, "env": sl.ARMS[arm], "git_head": head, "uncommitted": [line[3:] for line in dirty],
        "cases": CASES.name, "started_at": datetime.now().isoformat(timespec="seconds")},
        indent=1, ensure_ascii=False), encoding="utf-8")
    path = out / "dialogues.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    unload_models()
    cases = [c for c in load_cases() if not only or c["id"] in only]
    for number, case in enumerate(cases, 1):
        if case["id"] in done:
            continue
        backend = sl.Elaina(out / "rt" / case["id"], out / "logs" / f"{case['id']}.log", sl.ARMS[arm])
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
        print(f"[{number}/{len(cases)}] {case['id']}: {len(transcript)} turns "
              f"({time.monotonic() - started:.0f}s)", flush=True)
    return 0


def _draft(turn: dict) -> dict:
    return next((c for c in turn.get("model_calls") or () if "collect_answer" in str(c.get("caller"))), {})


def _seconds(value) -> float:
    value = float(value or 0)
    return value / 1e9 if value > 1e6 else value


def check(name: str) -> list[dict]:
    """One row per turn of the run, with what was checked."""
    embed = embedder()
    rows = []
    for case in load_cases():
        paths = glob.glob(str(ROOT / name / "rt" / case["id"] / "turn_trace" / "*.jsonl"))
        if not paths:
            continue
        turns = [t for path in sorted(paths) for t in _turns(path)]
        for number, turn in enumerate(turns, 1):
            context = turn.get("context") or {}
            route = context.get("route") or {}
            move = context.get("turn_move") or {}
            applied = context.get("turn_move_applied") or {}
            draft = _draft(turn)
            final = str((draft.get("messages") or [{}])[-1].get("content") or "")
            said = str(turn.get("user_input") or "").strip()
            reading = str(route.get("normalized_request") or "").strip()
            factual = FACTUAL in final
            row = {
                "case": case["id"], "turn": number, "said": said, "reading": reading,
                "intent": route.get("intent", ""), "move": move.get("move", ""), "owned": bool(applied),
                "reply": turn.get("display", ""), "named": (case.get("named") or {}).get(str(number), ""),
                "model": draft.get("model", ""), "factual": factual,
                "clipped": "chars cut]" in final, "question_shown": applied.get("question_shown", ""),
                "seconds": float(turn.get("elapsed") or 0), "draft_seconds": float(draft.get("latency") or 0),
                "load_seconds": _seconds(draft.get("load_duration")),
                "changed": [s.get("name") for s in turn.get("steps") or () if s.get("changed")],
                "checks": {},
            }
            row["searched"] = list((turn.get("evidence") or {}).get("search_queries") or ())
            row["as_first_asked"] = bool(context.get("lookup_as_first_asked"))
            row["router_query"] = str(route.get("search_query") or "").strip()
            router = next((c for c in turn.get("model_calls") or () if "intent_router" in str(c.get("caller"))), {})
            row["router_load_seconds"] = _seconds(router.get("load_duration"))
            checks = row["checks"]
            if draft:
                if applied:
                    checks["written_by_27b"] = draft.get("model") == M27 and applied.get("written_by") == M27
                    theirs = f"{FACTUAL}{said}\n" in final or f"{CONVERSATIONAL}{said}\n" in final
                    checks["their_words"] = theirs
                    checks["not_the_reading"] = reading == said or f"{FACTUAL}{reading}\n" not in final
                    expected = "their words" if (factual and reading and reading != said) else "as built"
                    checks["trace_says_so"] = applied.get("question_shown") == expected
                    before = [turns[p - 1].get("display", "") for p in move.get("asked_before") or ()]
                    if before and row["reply"].strip():
                        vector = _unit(embed(row["reply"]))
                        row["similarity"] = max(sum(x * y for x, y in zip(vector, _unit(embed(text))))
                                                for text in before if text.strip())
                        checks["not_a_copy"] = row["similarity"] < NEAR_COPY
                else:
                    checks["written_by_8b"] = draft.get("model") == M8
                    if factual and reading:
                        checks["built_as_before"] = f"{FACTUAL}{reading}\n" in final
            checks["no_reasoning"] = "think>" not in row["reply"]
            if move.get("move") == turn_move.DIFFERENTLY and row["searched"] and not row["router_query"]:
                # Every search an earlier turn got back is searched again. (One of the first
                # asking's two can come back empty; the re-ask then has more, not something else.)
                earlier = [r["searched"] for r in rows if r["case"] == case["id"] and r["searched"]]
                checks["same_lookup"] = any(set(e) <= set(row["searched"]) for e in earlier)
            rows.append(row)
    return rows


def report(name: str) -> str:
    rows = check(name)
    owned = [r for r in rows if r["owned"]]
    others = [r for r in rows if not r["owned"]]
    meta = json.loads((ROOT / name / "meta.json").read_text(encoding="utf-8"))
    lines = [f"# Turn-move regression: {name}", "",
             f"Arm `{meta['arm']}`, commit `{meta['git_head'][:7]}`"
             + (f" plus {len(meta['uncommitted'])} uncommitted file(s)" if meta["uncommitted"] else "")
             + f". {len(rows)} turns in {len({r['case'] for r in rows})} conversations; the move owned "
             f"{len(owned)}.", ""]

    def tally(group, key):
        values = [r["checks"][key] for r in group if key in r["checks"]]
        return f"{sum(values)}/{len(values)}" if values else "-"
    lines += ["## Checks", "", "| on turns the move owned | passed |", "|---|---|"]
    for key in ("written_by_27b", "their_words", "not_the_reading", "trace_says_so", "not_a_copy"):
        lines.append(f"| {key} | {tally(owned, key)} |")
    lines += ["", "| on every other turn | passed |", "|---|---|"]
    for key in ("written_by_8b", "built_as_before"):
        lines.append(f"| {key} | {tally(others, key)} |")
    lines += ["", "| on every turn | passed |", "|---|---|"]
    for key in ("no_reasoning", "same_lookup"):
        lines.append(f"| {key} | {tally(rows, key)} |")
    lines += ["", "## Searches", ""]
    for r in rows:
        if r["searched"]:
            how = ("as first asked" if r["as_first_asked"]
                   else "the router's query" if r["router_query"] else "built from the turn")
            lines.append(f"- {r['case']} t{r['turn']} [{r['move'] or '-'}; {r['intent']}; {how}] "
                         + " | ".join(r["searched"]))
    shown = [r for r in owned if r["factual"] and r["reading"] and r["reading"] != r["said"]]
    lines += ["", f"The router's reading differed from what they said on {len(shown)} of the "
              f"{len(owned)} move-owned turns built as a factual or search prompt; "
              f"{sum(1 for r in owned if not r['factual'])} were conversational prompts, which carry "
              "their words already.", ""]
    failed = [(r, key) for r in rows for key, ok in r["checks"].items() if not ok]
    lines += ["## Failed checks", ""] + ([f"- {r['case']} t{r['turn']}: {key}" for r, key in failed] or ["None."])

    def med(values):
        values = sorted(values)
        return f"{values[len(values) // 2]:.1f}" if values else "-"
    after = [rows[i + 1] for i, r in enumerate(rows[:-1]) if r["owned"] and rows[i + 1]["case"] == r["case"]]
    plain_after = [r for r in after if not r["owned"]]
    lines += ["", "## Latency (seconds)", "", "| | median | slowest | turns |", "|---|---|---|---|",
              f"| a turn the 27B wrote | {med([r['seconds'] for r in owned])} | "
              f"{max([r['seconds'] for r in owned], default=0):.1f} | {len(owned)} |",
              f"| ...its drafting call | {med([r['draft_seconds'] for r in owned])} | "
              f"{max([r['draft_seconds'] for r in owned], default=0):.1f} | |",
              f"| ...of which loading the 27B | {med([r['load_seconds'] for r in owned])} | "
              f"{max([r['load_seconds'] for r in owned], default=0):.1f} | |",
              f"| an 8B turn right after one | {med([r['seconds'] for r in plain_after])} | "
              f"{max([r['seconds'] for r in plain_after], default=0):.1f} | {len(plain_after)} |",
              f"| ...of which reloading the 8B | {med([r['router_load_seconds'] for r in plain_after])} | "
              f"{max([r['router_load_seconds'] for r in plain_after], default=0):.1f} | |",
              f"| every other 8B turn | {med([r['seconds'] for r in others if r not in plain_after])} | "
              f"{max([r['seconds'] for r in others if r not in plain_after], default=0):.1f} | "
              f"{len([r for r in others if r not in plain_after])} |", ""]
    lines += ["## Every turn the move owned", ""]
    for r in owned:
        tags = ", ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in r["checks"].items())
        lines += [f"**{r['case']} / turn {r['turn']}**" + (f" (named: {r['named']})" if r["named"] else ""),
                  "", f"- They said: {r['said']}", f"- The router's reading: {r['reading']} ({r['intent']})",
                  f"- Shown to the writer: {r['question_shown']}; similarity to her earlier answer: "
                  + (f"{r['similarity']:.2f}" if "similarity" in r else "-")
                  + f"; {r['seconds']:.1f}s; stages that changed the reply: {', '.join(r['changed']) or 'none'}",
                  f"- Checks: {tags}", f"- She said: {r['reply']}", ""]
    lines += ["## The other turns", ""]
    for r in others:
        tags = ", ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in r["checks"].items()) or "-"
        reply = r["reply"].replace("\n", " ")
        lines.append(f"- {r['case']} t{r['turn']} [{r['move'] or '-'}; {r['intent']}; {tags}] "
                     f"{r['said']} -> {reply[:160]}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--arm", default=ARM, choices=sorted(sl.ARMS))
    args = parser.parse_args()
    if args.run:
        return run(args.run, args.only, args.arm)
    if args.report:
        text = report(args.report)
        (ROOT / args.report / "REPORT.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
