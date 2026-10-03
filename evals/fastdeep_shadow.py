"""The shadow FAST/DEEP router, evaluated offline against the frozen evidence.

    .venv/Scripts/python.exe -m evals.fastdeep_shadow --out docs/FAST_DEEP_SHADOW.md

The router is brain/fastdeep_router.py -- the same ``decide`` the engine runs
in shadow on every answered turn. Here it is fed the signals each frozen
FAST turn recorded (the 8B router's reading, the response budget, the
domain claim, the previous turn's budget), and its decisions are scored with
the frozen analysis (evals/fastdeep_analysis.py), unchanged:

* against the baseline's FAST/DEEP labels (runtime/evals/pf, blind labels
  applied) -- every turn of the corpus;
* against Stage B's FAST/SPLIT labels (runtime/evals/pb, blind labels
  applied) -- the follow-up scenarios, where DEEP answers under the 8B's
  reading, as a routed system would.

All of it is development data: the rule was designed on these turns. Whether
it generalises is corpus v2's question (docs/CORPUS_V2_PLAN.md).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import fastdeep_router as router  # noqa: E402
from evals import compare, compare3  # noqa: E402
from evals import fastdeep_analysis as fd  # noqa: E402
from evals import fastdeep_stageb as stageb  # noqa: E402
from evals.phase2 import M8, M27  # noqa: E402

SHADOW = "shadow router"
SHADOW_HIGH = "shadow router, high confidence only"


# ------------------------------------------------------------ decisions


def _signals(trace_record: dict, stored_trace: dict, previous_budget: str) -> router.ShadowSignals:
    context = trace_record.get("context") or {}
    route = context.get("route") if isinstance(context.get("route"), dict) else {}
    limits = stored_trace.get("limits") or {}
    confidence = route.get("confidence")
    return router.ShadowSignals(
        intent=str(route.get("intent") or stored_trace.get("intent") or ""),
        speech_act=str(route.get("speech_act") or ""),
        answer_shape=str(route.get("answer_shape") or ""),
        is_follow_up=bool(route.get("is_follow_up")),
        topic_shift=bool(route.get("topic_shift")),
        detailed=bool(route.get("detailed_response")),
        budget=str(limits.get("budget") or ""),
        shape=str(limits.get("shape") or ""),
        previous_budget=previous_budget,
        domain_claimed=bool(context.get("domain_claim")),
        language=str(context.get("language") or ""),
        router_confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
    )


def decisions(loaded: dict) -> dict:
    """{(scenario, index): ShadowDecision} for every turn of one run, in order,
    carrying the previous turn's budget as the engine does."""
    records = {r.get("turn_id"): r for r in compare3._records(loaded["path"])}
    found = {}
    for result in loaded["run"]["results"]:
        previous = ""
        for turn in result["turns"]:
            trace = turn.get("trace") or {}
            record = records.get(trace.get("turn_id")) or {}
            found[(result["scenario"], turn["index"])] = router.decide(
                _signals(record, trace, previous))
            previous = str((trace.get("limits") or {}).get("budget") or "")
    return found


def attach(runs: list[dict]) -> None:
    """Put each turn's shadow decision where the frozen policy scoring reads."""
    for run in runs:
        made = decisions(run)
        for key, turn in run["turns"].items():
            decision = made.get(key)
            turn.route["shadow"] = decision.decision if decision else router.FAST
            turn.route["shadow_confidence"] = decision.confidence if decision else ""
            turn.route["shadow_reason"] = decision.reason if decision else ""


fd.POLICIES[SHADOW] = lambda route: route.get("shadow") == router.DEEP
fd.POLICIES[SHADOW_HIGH] = lambda route: (route.get("shadow") == router.DEEP
                                          and route.get("shadow_confidence") == "high")


# ------------------------------------------------------------ attribution


def _majority_sent(runs: list[dict], key) -> bool:
    votes = [r["turns"][key].route.get("shadow") == router.DEEP for r in runs if key in r["turns"]]
    return sum(votes) * 2 > len(votes)


def attribution(fast: list[dict], deep: list[dict], labels: dict) -> dict:
    """Each turn's outcome under the shadow router (its majority decision)."""
    found = {}
    for key, label in labels.items():
        sent = _majority_sent(fast, key)
        if label.label == "stable DEEP+":
            found[key] = "correct escalation" if sent else "routing miss"
            continue
        if not sent:
            found[key] = "correct FAST"
            continue
        if label.label == "stable DEEP-worse":
            found[key] = "DEEP made it worse"
        elif label.label == "pending DEEP+" or not fd.no_difference(label):
            found[key] = "unstable or judge-only difference"
        else:
            fast_fails = sum(not r["turns"][key].passed for r in fast if key in r["turns"])
            deep_fails = sum(not r["turns"][key].passed for r in deep if key in r["turns"])
            found[key] = ("neither model good enough" if fast_fails >= 2 and deep_fails >= 2
                          else "DEEP did not improve it (unneeded escalation)")
    return found


# ------------------------------------------------------------ tables


POLICY_ORDER = ("all FAST", "all DEEP", SHADOW, SHADOW_HIGH, fd.ORACLE)


def policy_table(fast, deep, labels, swap, *, latency: bool = True) -> list[str]:
    """``latency`` off when the second arm's turn times already hold the
    swaps (SPLIT swaps on every turn), so they are not charged twice."""
    head = ("| policy | DEEP+ recall | DEEP precision | DEEP utilization | DEEP-worse sent "
            "| turns passing | scenarios passing | correctness |")
    if latency:
        head += " p50 / p95 s (with switching) | p50 / p95 s (generation only) |"
    rows = [head, "|" + "---|" * (head.count("|") - 1)]
    cell = fd._mean_pct
    for name in POLICY_ORDER:
        scored = [fd.score_policy(name, f, d, labels, swap) for f, d in zip(fast, deep)]
        free = [fd.score_policy(name, f, d, labels, (0.0, 0.0)) for f, d in zip(fast, deep)]
        row = (
            f"| {name} | {cell([s['recall'] for s in scored])} | {cell([s['precision'] for s in scored])} "
            f"| {cell([s['utilization'] for s in scored])} | {cell([s['worse_sent'] for s in scored])} "
            f"| {cell([s['turns_passing'] for s in scored])} | {cell([s['scenarios_passing'] for s in scored])} "
            f"| {cell([s['correctness'] for s in scored])} |")
        if latency:
            row += (f" {mean(s['p50'] for s in scored):.1f} / {mean(s['p95'] for s in scored):.1f} "
                    f"| {mean(s['p50'] for s in free):.1f} / {mean(s['p95'] for s in free):.1f} |")
        rows.append(row)
    return rows


def _loads_by_model(runs: list[dict]) -> dict:
    found = {M8: [], M27: []}
    for run in runs:
        for record in compare3._records(run["path"]):
            for call in record.get("model_calls") or ():
                value = (call.get("load_duration") or 0) / 1e9
                if value > 0.5 and call.get("model") in found:
                    found[call["model"]].append(value)
    return found


def _generation_seconds(run: dict) -> dict:
    """{(scenario, index): turn seconds less the model loads inside it}."""
    records = {r.get("turn_id"): r for r in compare3._records(run["path"])}
    found = {}
    for result in run["run"]["results"]:
        for turn in result["turns"]:
            if turn.get("display") is None:
                continue
            record = records.get((turn.get("trace") or {}).get("turn_id")) or {}
            loads = sum((c.get("load_duration") or 0) / 1e9 for c in record.get("model_calls") or ())
            found[(result["scenario"], turn["index"])] = max(float(turn["seconds"]) - loads, 0.0)
    return found


def routed_latency(fast: list[dict], split: list[dict], sent_by_run: list[set], load_27: float,
                   load_8: float) -> tuple[float, float]:
    """End to end on the Stage B scenarios: FAST's own time on FAST turns;
    SPLIT's generation time plus the 27B's load on DEEP turns; the 8B's
    reload on the turn after a DEEP turn."""
    values = []
    for f, s, sent in zip(fast, split, sent_by_run):
        fast_time = _generation_seconds(f)
        split_time = _generation_seconds(s)
        previous_deep: dict = {}
        for key in sorted(fast_time):
            if key not in split_time:
                continue
            after = previous_deep.get(key[0], False)
            value = (split_time[key] + load_27) if key in sent else fast_time[key]
            values.append(value + (load_8 if after else 0.0))
            previous_deep[key[0]] = key in sent
    ordered = sorted(values)
    return (median(ordered) if ordered else 0.0,
            ordered[int(0.95 * (len(ordered) - 1))] if ordered else 0.0)


# ------------------------------------------------------------ report


def write() -> str:
    frozen, recorded, now = fd.frozen_state()
    fast = [fd.load_run(p) for p in fd._runs_of(fd.ROOT, fd.FAST_ARM)]
    deep = [fd.load_run(p) for p in fd._runs_of(fd.ROOT, fd.DEEP_ARM)]
    labels = fd.label_turns(fast, deep, fd.read_human_labels())
    attach(fast)
    pb_fast, pb_split, pb_deep = stageb._runs("fast"), stageb._runs("split"), stageb._runs("deep")
    split_labels = fd.label_turns(pb_fast, pb_split, stageb.read_split_labels())
    attach(pb_fast)
    loads = _loads_by_model(pb_split)
    load_27 = median(loads[M27]) if loads[M27] else 0.0
    load_8 = median(loads[M8]) if loads[M8] else 0.0

    lines: list[str] = []
    add = lines.append
    add("# The shadow FAST/DEEP router, against the frozen evidence")
    add("")
    add(f"*Generated by `evals/fastdeep_shadow.py` on {datetime.now():%Y-%m-%d %H:%M}. Router: "
        "`brain/fastdeep_router.py`, the function the engine records in shadow. Scored with the "
        f"frozen analysis (`{now}`, " + ("frozen" if frozen else "**NOT frozen**") + "). "
        "**Development data**: the rule was designed on these turns; generalisation is corpus "
        "v2's question.*")
    add("")
    add("**The rule.** DEEP when the turn's budget is an explanation, the topic has not shifted, "
        "no deterministic domain claimed it, and at least one continuation signal holds: the "
        "router marks it a follow-up, the previous turn was an explanation, or the router reads "
        "it as a clarification. Two or more signals make it high-confidence.")
    add("")

    # ---- baseline
    add("## 1. Against the baseline's FAST/DEEP labels (every turn)")
    add("")
    add(f"Switching cost charged as measured in Stage B: entering DEEP loads the 27B "
        f"({load_27:.1f} s median); the turn after a DEEP turn reloads the 8B ({load_8:.1f} s). "
        "\"Generation only\" leaves both out. Times are over scored turns, with DEEP turns "
        "answered at the baseline DEEP arm's speed less its own routing time.")
    add("")
    lines.extend(policy_table(fast, deep, labels, (load_27, load_8)))
    add("")
    reasons = Counter((r.route.get("shadow"), r.route.get("shadow_reason"))
                      for run in fast for r in run["turns"].values())
    add("Decisions over all FAST turns (three runs): " + "; ".join(
        f"{d} \"{reason}\" {n}" for (d, reason), n in reasons.most_common()) + ".")
    add("")
    outcome = attribution(fast, deep, labels)
    counted = Counter(outcome.values())
    add("### What happened to each turn (the router's majority decision)")
    add("")
    add("| outcome | turns |")
    add("|---|---|")
    for name in ("correct escalation", "routing miss", "DEEP made it worse",
                 "DEEP did not improve it (unneeded escalation)", "neither model good enough",
                 "unstable or judge-only difference", "correct FAST"):
        add(f"| {name} | {counted.get(name, 0)} |")
    add("")
    for name in ("routing miss", "DEEP made it worse", "DEEP did not improve it (unneeded escalation)",
                 "neither model good enough", "unstable or judge-only difference"):
        keys = sorted(k for k, v in outcome.items() if v == name)
        if keys:
            add(f"- **{name}**: " + ", ".join(
                f"`{k[0]}` t{k[1]} ({labels[k].category})" for k in keys))
    add("")

    # ---- Stage B
    add("## 2. Against Stage B: DEEP answering under the 8B's reading (follow-up scenarios)")
    add("")
    add("A routed system answers a DEEP turn with the 27B under the 8B's reading -- Stage B's "
        "SPLIT arm. Here the labels are FAST against SPLIT (blind labels applied), and the "
        "router's decisions come from the Stage B FAST runs.")
    add("")
    lines.extend(policy_table(pb_fast, pb_split, split_labels, (load_27, load_8), latency=False))
    add("")
    add("Latency for these turns is in section 3: SPLIT swapped models on every turn, so its turn "
        "times already include the loads and are not reused here.")
    add("")
    split_outcome = attribution(pb_fast, pb_split, split_labels)
    counted = Counter(split_outcome.values())
    add("| outcome | turns |")
    add("|---|---|")
    for name in ("correct escalation", "routing miss", "DEEP made it worse",
                 "DEEP did not improve it (unneeded escalation)", "neither model good enough",
                 "unstable or judge-only difference", "correct FAST"):
        add(f"| {name} | {counted.get(name, 0)} |")
    add("")
    for name in ("correct escalation", "routing miss", "DEEP made it worse",
                 "DEEP did not improve it (unneeded escalation)", "unstable or judge-only difference"):
        keys = sorted(k for k, v in split_outcome.items() if v == name)
        if keys:
            add(f"- **{name}**: " + ", ".join(f"`{k[0]}` t{k[1]}" for k in keys))
    add("")

    # ---- latency
    add("## 3. Latency, switching included")
    add("")
    fast_gen = [v for r in pb_fast for v in _generation_seconds(r).values()]
    split_gen = [v for r in pb_split for v in _generation_seconds(r).values()]
    sent_by_run = [{k for k, t in r["turns"].items() if t.route.get("shadow") == router.DEEP}
                   for r in pb_fast]
    oracle_by_run = [{k for k, v in split_labels.items() if v.label == "stable DEEP+"}
                     for _ in pb_fast]
    every_by_run = [set(r["turns"]) for r in pb_fast]
    add("Measured on the Stage B runs (current code, the follow-up scenarios):")
    add("")
    add("| part | seconds |")
    add("|---|---|")
    add(f"| FAST turn, generation (p50) | {median(fast_gen):.1f} |")
    add(f"| DEEP turn under the 8B's reading, generation without loads (p50) | {median(split_gen):.1f} |")
    add(f"| entering DEEP: loading the 27B (median of {len(loads[M27])}) | {load_27:.1f} |")
    add(f"| returning to FAST: reloading the 8B (median of {len(loads[M8])}) | {load_8:.1f} |")
    add(f"| expected cost of one DEEP turn over FAST (load + generation difference) | "
        f"{load_27 + median(split_gen) - median(fast_gen):.1f} |")
    add(f"| expected cost on the turn after it (8B reload) | {load_8:.1f} |")
    add("")
    add("| routing | end-to-end p50 / p95 s |")
    add("|---|---|")
    for name, sent in (("all FAST", [set() for _ in pb_fast]), (SHADOW, sent_by_run),
                       (fd.ORACLE, oracle_by_run), ("all DEEP (every turn switches)", every_by_run)):
        p50, p95 = routed_latency(pb_fast, pb_split, sent, load_27, load_8)
        add(f"| {name} | {p50:.1f} / {p95:.1f} |")
    deep_latency = [compare.latency(r["run"]) for r in pb_deep]
    add(f"| all DEEP, the 27B resident for everything (no switching; the DEEP arm) "
        f"| {mean(x['p50'] for x in deep_latency):.1f} / {mean(x['p95'] for x in deep_latency):.1f} |")
    add("")
    add("\"all DEEP (every turn switches)\" is SPLIT with the 27B load charged on every turn, as "
        "Stage B measured it (SPLIT p50 17.2 s). A routed system pays the 27B load only on the "
        "turns it sends, and the 8B reload on the turn after.")
    add("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    text = write()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
