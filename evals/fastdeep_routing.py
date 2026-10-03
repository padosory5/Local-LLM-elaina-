"""Shadow routing: can existing router and state signals find the DEEP+ turns?

    .venv/Scripts/python.exe -m evals.fastdeep_routing --features
    .venv/Scripts/python.exe -m evals.fastdeep_routing --out docs/FAST_DEEP_ROUTING.md

Offline only, on the frozen FAST/DEEP baseline (runtime/evals/pf). Nothing
here routes a real conversation. It reads the 8B router's recorded output and
the conversation state the engine already records for each FAST turn -- no
new field, no extra model call, no phrase rule, nothing from the scenario's
identity or the eval's metadata -- and scores deterministic candidate rules
with the frozen analysis (evals/fastdeep_analysis.py), unchanged: the same
stable labels, the same combined-quality and swap-cost accounting.

Every rule below was written after looking at the same 73 turns it is scored
on, so its numbers are in-sample. A rule that looks good here is a hypothesis
for a held-out run, not a result.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evals import compare3  # noqa: E402
from evals import fastdeep_analysis as fd  # noqa: E402

GROUPS = ("stable DEEP+", "stable DEEP-worse", "no difference", "inconsistent")


# ------------------------------------------------------------- signals


def signals(loaded: dict) -> dict:
    """{(scenario, index): existing router and state fields} for one FAST run.

    Everything is read from the turn's record as the engine wrote it. The one
    derived field, ``after_explanation``, is the previous turn's own recorded
    budget in the same conversation.
    """
    by_turn: dict[str, dict] = {}
    for record in compare3._records(loaded["path"]):
        by_turn[record.get("turn_id")] = record
    found = {}
    for result in loaded["run"]["results"]:
        previous_budget = None
        for turn in result["turns"]:
            trace = turn.get("trace") or {}
            record = by_turn.get(trace.get("turn_id")) or {}
            context = record.get("context") or {}
            route = context.get("route") if isinstance(context.get("route"), dict) else {}
            decision = context.get("decision") if isinstance(context.get("decision"), dict) else {}
            state = context.get("state") if isinstance(context.get("state"), dict) else {}
            limits = trace.get("limits") or {}
            found[(result["scenario"], turn["index"])] = {
                "intent": route.get("intent") or trace.get("intent"),
                "speech_act": route.get("speech_act"),
                "answer_shape": route.get("answer_shape"),
                "is_follow_up": bool(route.get("is_follow_up")),
                "detailed_response": bool(route.get("detailed_response")),
                "topic_shift": bool(route.get("topic_shift")),
                "request_explicitness": route.get("request_explicitness"),
                "confidence": route.get("confidence"),
                "decision_intent": decision.get("intent"),
                "decision_mode": decision.get("mode"),
                "has_usable_context": bool(decision.get("has_usable_context")),
                "budget": limits.get("budget"),
                "shape": limits.get("shape"),
                "language": context.get("language"),
                "has_history": int(state.get("history_messages") or 0) > 0,
                "after_explanation": previous_budget in ("explain", "elaborate"),
            }
            previous_budget = limits.get("budget")
    return found


def group(label: fd.TurnLabel) -> str:
    if label.label in ("stable DEEP+", "stable DEEP-worse"):
        return label.label
    return "no difference" if fd.no_difference(label) else "inconsistent"


def majority(values):
    counted = Counter(values)
    return counted.most_common(1)[0][0] if counted else None


def feature_table(fast: list[dict], labels: dict) -> list[str]:
    """Each field's majority value over the FAST runs, by label group."""
    per_run = [signals(run) for run in fast]
    fields = list(next(iter(per_run[0].values())).keys())
    rows = []
    for name in fields:
        if name == "confidence":
            continue
        tallies = {g: Counter() for g in GROUPS}
        for key, label in labels.items():
            value = majority([s[key][name] for s in per_run if key in s])
            tallies[group(label)][value] += 1
        values = sorted({v for t in tallies.values() for v in t}, key=str)
        for value in values:
            counts = [tallies[g][value] for g in GROUPS]
            if sum(counts):
                rows.append(f"| `{name}` | `{value}` | " + " | ".join(str(c) for c in counts) + " |")
    return rows


# ---------------------------------------------------------- candidates

# Deterministic rules over one turn's signals. Each reads only what the
# 8B router and the engine's state already recorded for the turn.
def _explains(s) -> bool:
    return s["budget"] in ("explain", "elaborate")


CANDIDATES = {
    "R0 explanation budget (the baseline's P1)": _explains,
    "R1 clarification only": lambda s: s["intent"] == "clarification",
    "R2 explanation + router says follow-up": lambda s: _explains(s) and s["is_follow_up"],
    "R3 explanation + right after an explanation": lambda s: _explains(s) and s["after_explanation"],
    "R4 explanation + conversation under way": lambda s: _explains(s) and s["has_history"],
    "R5 R2, or an explanation in Korean":
        lambda s: _explains(s) and (s["is_follow_up"] or s["language"] == "ko"),
    "R6 R4, or an explanation in Korean":
        lambda s: _explains(s) and (s["has_history"] or s["language"] == "ko"),
}


def _attach(fast: list[dict]) -> None:
    """Make each FAST turn's route carry its full signals, so the frozen
    analysis's own policy scoring can read them."""
    for run in fast:
        found = signals(run)
        for key, turn in run["turns"].items():
            turn.route.update(found.get(key, {}))


def score(fast, deep, labels, swap) -> list[dict]:
    rows = []
    names = ["all FAST", "all DEEP", *CANDIDATES, fd.ORACLE]
    for name, rule in CANDIDATES.items():
        fd.POLICIES[name] = rule          # in memory only; the file is unchanged
    agreeing = router_agreement_by_turn(fast, deep)
    for name in names:
        scored = [fd.score_policy(name, f, d, labels, swap) for f, d in zip(fast, deep)]
        free = [fd.score_policy(name, f, d, labels, (0.0, 0.0)) for f, d in zip(fast, deep)]
        flips = fd.decision_stability(name, fast, labels) if name != fd.ORACLE else (0, 0)
        caught = set.intersection(*(s["sent"] for s in scored)) & {
            k for k, v in labels.items() if v.label == "stable DEEP+"}
        rows.append({
            "name": name, "scored": scored, "free": free, "flips": flips,
            "caught_agree": sum(1 for k in caught if agreeing.get(k)),
            "caught_disagree": sum(1 for k in caught if not agreeing.get(k)),
        })
    return rows


def router_agreement_by_turn(fast, deep) -> dict:
    """Whether the two routers read the turn alike (intent, budget, shape)
    in at least 2 of the corresponding runs."""
    counts = defaultdict(int)
    for f, d in zip(fast, deep):
        for key, turn in f["turns"].items():
            other = d["turns"].get(key)
            if other is None:
                continue
            same = (turn.route.get("intent"), turn.route.get("budget"), turn.route.get("shape")) == (
                other.route.get("intent"), other.route.get("budget"), other.route.get("shape"))
            counts[key] += same
    return {key: n >= 2 for key, n in counts.items()}


def write(root: Path = fd.ROOT) -> str:
    frozen, recorded, now = fd.frozen_state()
    fast = [fd.load_run(p) for p in fd._runs_of(root, fd.FAST_ARM)]
    deep = [fd.load_run(p) for p in fd._runs_of(root, fd.DEEP_ARM)]
    labels = fd.label_turns(fast, deep, fd.read_human_labels())
    speeds = fd.normal_speeds(root)
    loads_27 = [x for r in deep for x in fd.resources(r, speeds)["loads"]]
    loads_8 = [x for r in fast for x in fd.resources(r, speeds)["loads"]]
    from statistics import median
    swap = (median(loads_27) if loads_27 else 0.0, median(loads_8) if loads_8 else 0.0)
    lines: list[str] = []
    add = lines.append
    add("# FAST/DEEP routing: existing signals, offline (shadow)")
    add("")
    add(f"*Generated by `evals/fastdeep_routing.py` on {datetime.now():%Y-%m-%d %H:%M}, from the "
        f"frozen baseline (`{root.relative_to(PROJECT_ROOT)}`) and the frozen analysis "
        f"(`{now}`, " + ("frozen" if frozen else "**NOT frozen**") + "). Nothing here routes a "
        "real conversation.*")
    add("")
    add("Every candidate reads only the 8B router's recorded output and the engine's recorded "
        "conversation state for the turn. Its numbers are **in-sample**: the rules were written "
        "after looking at these 73 turns.")
    add("")
    counts = Counter(group(v) for v in labels.values())
    add("Groups: " + ", ".join(f"{g} {counts.get(g, 0)}" for g in GROUPS) + ".")
    add("")
    add("## 1. The signals, by group")
    add("")
    add("Each field's majority value over the three FAST runs; counts are turns.")
    add("")
    add("| field | value | " + " | ".join(GROUPS) + " |")
    add("|---|---|" + "---|" * len(GROUPS))
    lines.extend(feature_table(fast, labels))
    add("")
    _attach(fast)
    add("## 2. Candidate rules, scored with the frozen analysis")
    add("")
    add(f"Latency with swaps charges a DEEP turn the 8B's route, the 27B's answer and a 27B load of "
        f"{swap[0]:.1f} s, and the turn after it an 8B reload of {swap[1]:.1f} s; over scored turns.")
    add("")
    add("| rule | DEEP+ recall | DEEP precision | DEEP utilization | DEEP-worse routed | turns passing "
        "| scenarios passing | correctness | p50 / p95 s (swaps) | p50 / p95 s (no swaps) "
        "| first-turn decision flips | DEEP+ caught: routers agreed / differed |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|")
    cell = fd._mean_pct
    for row in score(fast, deep, labels, swap):
        s, free = row["scored"], row["free"]
        add(f"| {row['name']} | {cell([x['recall'] for x in s])} | {cell([x['precision'] for x in s])} "
            f"| {cell([x['utilization'] for x in s])} | {cell([x['worse_sent'] for x in s])} "
            f"| {cell([x['turns_passing'] for x in s])} | {cell([x['scenarios_passing'] for x in s])} "
            f"| {cell([x['correctness'] for x in s])} "
            f"| {mean(x['p50'] for x in s):.1f} / {mean(x['p95'] for x in s):.1f} "
            f"| {mean(x['p50'] for x in free):.1f} / {mean(x['p95'] for x in free):.1f} "
            f"| {row['flips'][0]}/{row['flips'][1]} | {row['caught_agree']} / {row['caught_disagree']} |")
    add("")
    add("\"all FAST\" is FAST-only quality and \"all DEEP\" DEEP-only quality. \"DEEP+ caught\" counts "
        "stable DEEP+ turns a rule sends to DEEP in every run, split by whether the two routers "
        "read the turn alike (intent, budget and shape, in at least 2 of 3 run pairs). Where they "
        "differed, the win may be the 27B's interpretation rather than its generation.")
    add("")
    add("## 3. Interpretation or generation: how each router read the follow-ups")
    add("")
    add("Every stable DEEP+ or DEEP-worse turn after the first, with each router's (intent, "
        "budget) over the three runs. In DEEP the 27B's reading also chose the instruction the "
        "answer was written under (a clarification gets the re-explanation budget).")
    add("")
    add("| turn | label | 8B router | 27B router |")
    add("|---|---|---|---|")
    fast_signals = [signals(r) for r in fast]
    deep_signals = [signals(r) for r in deep]
    for key, label in sorted(labels.items()):
        if key[1] == 0 or label.label not in ("stable DEEP+", "stable DEEP-worse"):
            continue
        read = lambda runs: ", ".join(  # noqa: E731
            f"{intent}/{budget} ×{n}" for (intent, budget), n in Counter(
                (s[key]["intent"], s[key]["budget"]) for s in runs if key in s).most_common())
        add(f"| `{key[0]}` t{key[1]} | {label.label} | {read(fast_signals)} | {read(deep_signals)} |")
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--features", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if args.features:
        fast = [fd.load_run(p) for p in fd._runs_of(fd.ROOT, fd.FAST_ARM)]
        deep = [fd.load_run(p) for p in fd._runs_of(fd.ROOT, fd.DEEP_ARM)]
        labels = fd.label_turns(fast, deep, fd.read_human_labels())
        print("| field | value | " + " | ".join(GROUPS) + " |")
        print("\n".join(feature_table(fast, labels)))
        return 0
    text = write()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
