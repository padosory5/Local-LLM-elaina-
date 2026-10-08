"""The completion retry, replayed: the rule as it is against a narrower one.

    .venv/Scripts/python.exe -m evals.completion_retry_replay --replay
    .venv/Scripts/python.exe -m evals.completion_retry_replay --router
    .venv/Scripts/python.exe -m evals.completion_retry_replay --tests
    .venv/Scripts/python.exe -m evals.completion_retry_replay --report
    .venv/Scripts/python.exe -m evals.completion_retry_replay --live l1
    .venv/Scripts/python.exe -m evals.completion_retry_replay --live-report l1

``completion_retry`` (brain/chat_engine.py, straight after the first
``final_form``) asks the model for a second draft when
``AnswerCompletionGuard.needs_retry`` says a value question came back without
its value, and keeps the second draft when it holds a number the first did
not. Reviewed over every recorded turn (2026-10-07): asked on 20, kept on 15,
and none of the 20 was a requested number missing and then supplied.

* Nine were price answers that already stated the price and ended on an
  offer. The rule reads a closing question as "stopped before answering"
  before it looks for a value.
* Eleven were turns that asked for no number: "not how much it actually
  helps, right?" switches the stage on by its wording, the right reply has no
  number in it, and the retry -- "Perform the calculation now" -- is asked.

The rule the replay was written to test, at that one call site only:

    1. a reply that states a number the question did not give is complete,
       closing question or not;
    2. wording alone does not switch the retry on for a turn the router read
       as asking for an explanation or an example. The calculation route is
       as it was.

That rule was ``candidate`` here when the replay, the router check and the
live test were run. Since 2026-10-07 the engine asks
``AnswerCompletionGuard.first_draft_needs_retry`` (``production`` here), which
is the candidate with the calculation route left exactly as it was; ``now`` is
the rule as it was until then, and ``candidate`` is kept as the record of what
was tested. The replay says whether production and the candidate agree on
every recorded turn.

``--replay`` re-runs the decisions on every recorded turn, from what the
trace holds, and carries the first draft of each turn whose decision changes
through the deterministic stages that follow. No model is called. Its first
pass is the check on the replay itself: the rule as it is has to ask on
exactly the turns the trace recorded a second draft being asked for.

``--router`` asks the real router -- the engine's own, on the real 8B -- how
it reads questions that do ask for a number, alone and in the middle of
recorded explanation threads. That is the risk in part 2: a real value
question read as ``explain`` would lose the retry.

``--tests`` runs the whole deterministic suite with the candidate standing at
the call site. The swap is made inside the test process by a
``sitecustomize`` module this script writes under ``runtime/`` and names on
that process's PYTHONPATH; production is not changed for it. It is run twice,
the second time with the same hook returning the rule as it is, so the hook
is shown to change nothing by itself.

``--live`` asks the real backend, flag off and silent, a fresh backend for
each conversation and the two rules turn about, through that same hook: the
price question the closing-question half was recorded on, the follow-up price
question, the knowledge questions of scripts/live_basics_check.py, and the
recorded user turns of the three explanation threads that ended in an
invented calculation. The hook writes down what each rule would have decided
on every turn of both arms.
"""

from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.response_policy import AnswerCompletionGuard as G  # noqa: E402

ROOT = PROJECT_ROOT / "runtime" / "evals"
APP = PROJECT_ROOT / "runtime" / "turn_trace"
OUT = ROOT / "completion_retry"
STAGE = "completion_retry"
RETRY_PROMPT = "That draft deferred or stopped before giving the"
EXPLAINING = frozenset({"explain", "example"})
AMOUNT = re.compile(r"\$\d+(?:,\d{3})*(?:\.\d+)?")

# ------------------------------------------------------------- the rules


def unanswered(reply: str, *, question: str, closing_question_is_fine: bool = False) -> bool:
    """``needs_retry(calculation=True)``, line for line, with one switch.

    With the switch on, a reply that states a number the question did not
    give is not sent back for ending on a question. Nothing else moves: a
    reply cut off mid-sentence, or one that offers to do the sum, still is.
    """
    cleaned = str(reply or "").strip()
    if not cleaned:
        return True
    stated = bool(question.strip()) and bool(G.worked_out(cleaned, question=question))
    if cleaned.endswith("?") and not (closing_question_is_fine and stated):
        return True
    if cleaned[-1] not in ".!?":
        return True
    if G._DEFERRAL.search(cleaned):
        return True
    if question.strip():
        if G.worked_out(cleaned, question=question):
            return False
        return not G._states_a_value(cleaned)
    return not bool(re.search(r"\d", cleaned))


def now(reply: str, *, value_response: bool, question: str, **_) -> bool:
    """The rule as it was until 2026-10-07 (named when the replay was first run)."""
    return G.needs_retry(str(reply or ""), calculation=bool(value_response), question=question)


def production(reply: str, *, value_response: bool, calculation_route: bool,
               answer_shape: str, question: str) -> bool:
    """What the engine asks today."""
    return G.first_draft_needs_retry(
        str(reply or ""), value_question=bool(value_response), calculation=bool(calculation_route),
        answer_shape=answer_shape, question=question)


def closing_question(reply: str, *, value_response: bool, question: str, **_) -> bool:
    """Part 1 alone."""
    return bool(value_response) and unanswered(reply, question=question, closing_question_is_fine=True)


def router_reading(reply: str, *, value_response: bool, calculation_route: bool,
                   answer_shape: str, question: str) -> bool:
    """Part 2 alone."""
    if not calculation_route and str(answer_shape or "").strip().lower() in EXPLAINING:
        return False
    return now(reply, value_response=value_response, question=question)


def candidate(reply: str, *, value_response: bool, calculation_route: bool,
              answer_shape: str, question: str) -> bool:
    """Both parts."""
    if not value_response:
        return False
    if not calculation_route and str(answer_shape or "").strip().lower() in EXPLAINING:
        return False
    return unanswered(reply, question=question, closing_question_is_fine=True)


RULES = {"now": now, "closing_question": closing_question,
         "router_reading": router_reading, "candidate": candidate, "production": production}


def at_the_call_site(reply, *, value_question, calculation, answer_shape, question) -> bool:
    """What the patched call site asks (see SITECUSTOMIZE)."""
    facts = {
        "value_response": bool(value_question),
        "calculation_route": bool(calculation),
        "answer_shape": str(answer_shape or ""),
        "question": str(question or ""),
    }
    was, would = now(reply, **facts), candidate(reply, **facts)
    log = os.environ.get("ELAINA_EVAL_COMPLETION_LOG", "")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": "decision", "at": time.time(), **facts,
                                     "reply": str(reply or ""), "now": was,
                                     "candidate": would}, ensure_ascii=False) + "\n")
    return {"candidate": would, "now": was}.get(
        os.environ.get("ELAINA_EVAL_COMPLETION_RULE", ""), production(reply, **facts))


# ------------------------------------------------------------ the record


def text_of(value) -> str:
    return str(value.get("text") or "") if isinstance(value, dict) else str(value or "")


def recorded(under: Path | None = None) -> list[dict]:
    """Every recorded turn, with what the stage was given and what it did.

    ``under``: one run's folder, instead of everything recorded before this
    tool's own live runs.
    """
    files = [(path, Path(path).relative_to(ROOT).parts)
             for path in sorted(glob.glob(str((under or ROOT) / "**" / "turn_trace" / "*.jsonl"), recursive=True))]
    if under is None:
        files = [(path, parts) for path, parts in files if parts[0] != OUT.name]
        files += [(path, ("app", Path(path).stem, Path(path).stem, "", ""))
                  for path in sorted(glob.glob(str(APP / "*.jsonl")))]
    rows = []
    for path, parts in files:
        previous, said_before, history = "", [], []
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError:
                continue
            context = turn.get("context") or {}
            route = context.get("route") or {}
            said = str(turn.get("user_input") or "")
            draft = turn.get("draft")
            steps = sorted(turn.get("steps") or (), key=lambda s: s.get("i", 0))

            # What the stage was given: the first draft as the first
            # final_form left it. A kept second draft leaves a step that
            # says so itself.
            first = text_of(draft)
            for step in steps:
                if step.get("name") == "final_form":
                    if step.get("changed"):
                        first = str(step.get("after") or "")
                    break
            kept, after, later, text, reached_grounding = False, "", [], text_of(draft), None
            for step in steps:
                name = str(step.get("name"))
                if name == STAGE and step.get("changed"):
                    first, after, kept = str(step.get("before")), str(step.get("after")), True
                elif name == "grounded_values" and reached_grounding is None:
                    reached_grounding = str(step.get("before")) if step.get("changed") else text
                elif kept and step.get("changed"):
                    later.append(name)
                if step.get("changed"):
                    text = str(step.get("after") or "")
            call = next((c for c in turn.get("model_calls") or ()
                         if str(((c.get("messages") or [{}])[-1]).get("content") or "").startswith(RETRY_PROMPT)),
                        None)
            limits = context.get("limits") if isinstance(context.get("limits"), dict) else {}
            decision = context.get("decision") if isinstance(context.get("decision"), dict) else {}
            display = str(turn.get("display") or "")
            rows.append({
                "where": f"{parts[0]}/{parts[1]}/{parts[-3]} t{number}", "family": parts[0],
                "said": said, "resolved": str(context.get("user_input_resolved") or said),
                "language": str(context.get("language") or "en"),
                "intent": str(route.get("intent") or ""), "speech_act": str(route.get("speech_act") or ""),
                "answer_shape": str(route.get("answer_shape") or ""),
                "topic": str(route.get("topic") or ""), "entity": str(route.get("entity") or ""),
                "calculation_route": route.get("intent") == "calculation",
                "value_response": bool(context.get("value_response")),
                "wording": G.asks_for_a_value(said),
                "question": f"{said} {route.get('normalized_request') or ''}",
                "source": str(draft.get("source") or "") if isinstance(draft, dict) else "",
                "first": first, "asked": call is not None, "second": str((call or {}).get("output") or ""),
                "latency": (call or {}).get("latency"), "kept": kept, "after": after, "later": later,
                "reached_grounding": reached_grounding, "display": display,
                "max_words": int(limits.get("max_words") or 0), "max_sentences": int(limits.get("max_sentences") or 0),
                "acts": decision.get("mode") == "execute",
                "action_requested": bool(route.get("action_requested")),
                "action_performed": bool(context.get("action_performed")),
                "trusted": bool(str(context.get("tool_result") or "").strip()),
                "searched": "web_search" in (turn.get("timings") or {}),
                "researched": bool(str((turn.get("evidence") or {}).get("research") or "").strip()),
                "evidence_amounts": sorted(set(AMOUNT.findall(str((turn.get("evidence") or {}).get("research") or "")))),
                "changed": [str(s.get("name")) for s in steps if s.get("changed")],
                "ident": parts[-3], "number": number,
                "seconds": float(turn.get("elapsed") or 0.0),
                "previous": previous, "said_before": tuple(said_before), "history": tuple(history),
            })
            if display.strip():
                said_before.append(display)
                previous = display
            history.append((said, display))
    return rows


def facts(row: dict) -> dict:
    return {"value_response": row["value_response"], "calculation_route": row["calculation_route"],
            "answer_shape": row["answer_shape"], "question": row["question"]}


# ------------------------------------------- the stages after this one


class Chain:
    """The deterministic stages that follow, on an engine that can do nothing else.

    ``grounded_values`` as it is today, the stage that takes a closing offer
    off the end (``redundant_permission``), and the last check on a value
    question (``result_restored``). The length rewrite between the retry and
    these is a model call and is not made here; ``too_long`` says when it
    would have been.
    """

    def __init__(self) -> None:
        from evals import disagreement_replay as dr

        self.dr = dr
        self.stage = dr.Stage()
        self.searched = {row["where"]: row for row in dr.recorded_turns()}

    def _row(self, row: dict) -> dict:
        found = self.searched.get(row["where"])
        if found is not None:
            return found
        return {"ledger": [], "language": row["language"], "previous": row["previous"],
                "said_before": row["said_before"], "said": row["resolved"],
                "action_performed": row["action_performed"], "trusted": row["trusted"],
                "searched": row["searched"]}

    def run(self, text: str, row: dict, *, answered: str, rule=None) -> tuple[str, list[str]]:
        """What the person is shown of ``text``, and which stages changed it."""
        from types import SimpleNamespace

        from brain import attribute_values as av

        changed = []
        grounded = self.stage.run({**self._row(row), "given": text}, rule or av.conflicting_claims)
        if grounded != text:
            changed.append("grounded_values")
        engine = self.stage.engine
        engine.capability_offer.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            offered = engine._refuse_redundant_permission(
                grounded, decision=SimpleNamespace(acts=row["acts"]),
                route=SimpleNamespace(action_requested=row["action_requested"]),
                action_performed=row["action_performed"])
        if offered != grounded:
            changed.append("redundant_permission")
        final = offered
        if answered and G.dropped_the_result(final, draft=answered, question=row["question"]):
            said_it = G.the_result_sentence(answered, question=row["question"])
            korean = any("가" <= ch <= "힣" for ch in said_it)
            if said_it and korean == any("가" <= ch <= "힣" for ch in final):
                final = said_it
                changed.append("result_restored")
        return final, changed


def answered(text: str, row: dict) -> str:
    """``answered_calculation``: the reply as it stood, when it stood complete."""
    if row["value_response"] and not G.needs_retry(text, calculation=True, question=row["question"]):
        return text
    return ""


def too_long(text: str, row: dict) -> bool:
    from brain.response_policy import ResponseLimits

    return ResponseLimits(max_words=row["max_words"], max_sentences=row["max_sentences"]).exceeds(text)


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace(" — ", " -- ").replace("—", "--")).strip()


# ------------------------------------------------------------ the replay


def replay() -> dict:
    rows = recorded()
    for row in rows:
        row["replica_agrees"] = (
            unanswered(row["first"], question=row["question"]) ==
            G.needs_retry(row["first"], calculation=True, question=row["question"]))
        for name, rule in RULES.items():
            row[name] = rule(row["first"], **facts(row))
    chain = None
    for row in rows:
        if row["now"] == row["candidate"] and not row["kept"]:
            continue
        chain = chain or Chain()
        if row["kept"]:
            # The record's own path from the grounding stage on, as a check
            # on the chain: under the rule that stage had then, and today's.
            start = row["reached_grounding"] if row["reached_grounding"] is not None else row["after"]
            row["chain_then"], _ = chain.run(start, row, answered=answered(row["after"], row), rule=chain.dr.before)
            row["with_retry_today"], row["with_retry_stages"] = chain.run(
                start, row, answered=answered(row["after"], row))
        # Without the second draft: the first one goes on down the pipeline.
        row["without_retry"], row["without_stages"] = chain.run(row["first"], row, answered=answered(row["first"], row))
        # The same, had part 1 been written into the rule itself and so reached
        # the last check on a value question as well (``result_restored``).
        whole = row["first"] if row["value_response"] and not unanswered(
            row["first"], question=row["question"], closing_question_is_fine=True) else ""
        row["without_retry_wide"], _ = chain.run(row["first"], row, answered=whole)
        row["too_long"] = too_long(row["first"], row)
    result = {"rows": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "replay.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def numbers(text: str) -> set:
    return G._numbers(text)


def left_out_line(text: str) -> bool:
    """Whether the reply ends on the grounding stage's "couldn't confirm the number" line."""
    from brain import guard_lines

    return any(guard_lines.say("unverified_figure_searched", language) in squash(text) for language in ("en", "ko"))


def replay_report(result: dict) -> list[str]:
    rows = result["rows"]
    on = [r for r in rows if r["value_response"]]
    asked = [r for r in rows if r["asked"]]
    lines = ["## A. Offline replay", "",
             f"{len(rows)} recorded turns ({dict(Counter(r['family'] for r in rows))}); the stage was switched on "
             f"for {len(on)}. No model was called.", ""]

    # the check on the replay
    agree = sum(r["now"] == r["asked"] for r in rows)
    replica = sum(r["replica_agrees"] for r in rows)
    lines += ["### The check on the replay", "",
              f"- The rule as it was, re-run on the recorded first draft, asks on {sum(r['now'] for r in rows)} turns; "
              f"the trace recorded a second draft being asked for on {len(asked)}. The two agree on "
              f"{agree} of {len(rows)} turns.",
              f"- The line-for-line copy of the rule used for the candidate, with its switch off, agrees with "
              f"`needs_retry` on {replica} of {len(rows)} first drafts.", ""]
    for r in rows:
        if r["now"] != r["asked"]:
            lines.append(f"  - disagrees: {r['where']} | recorded asked={r['asked']} | rule={r['now']} | "
                         f"draft source={r['source']!r} | {r['said'][:80]!r} -> {r['first'][:120]!r}")
    lines.append("")

    # the counts
    lines += ["### How often each rule asks", "",
              "| Rule | Asks | Of those, the record kept the second draft |", "|---|---|---|"]
    names = {"now": "As it was", "closing_question": "Part 1 alone (a stated number is complete)",
             "router_reading": "Part 2 alone (not on a turn read as explain/example)",
             "candidate": "Candidate (both), as tested",
             "production": "Production (`first_draft_needs_retry`)"}
    for name, label in names.items():
        fires = [r for r in rows if r[name]]
        lines.append(f"| {label} | {len(fires)} | {sum(r['kept'] for r in fires)} |")
    outside = [r for r in rows if r["candidate"] and not r["now"]]
    changed = [r for r in rows if r["candidate"] != r["now"]]
    apart = [r for r in rows if r["production"] != r["candidate"]]
    lines += ["", f"- Production and the tested candidate decide alike on {len(rows) - len(apart)} of {len(rows)} "
                  "recorded turns." + ("" if not apart else " They differ on: " + "; ".join(
                      f"{r['where']} ({r['intent']})" for r in apart)),
              f"- Turns the candidate asks on that the rule as it was does not: {len(outside)}.",
              f"- Turns whose decision changes: {len(changed)}. Second drafts the record kept that the candidate "
              f"would still ask for: {sum(r['kept'] and r['candidate'] for r in rows)} of "
              f"{sum(r['kept'] for r in rows)}.", ""]

    # the case the stage is for
    state = [r for r in on if r["answer_shape"] not in EXPLAINING or r["calculation_route"]]
    bare = [r for r in state if not G.worked_out(r["first"], question=r["question"])
            and not G._states_a_value(r["first"])]
    lost = [r for r in bare if r["now"] and not r["candidate"]]
    lines += ["### The case the stage exists for", "",
              f"- Turns the stage is on for that the router did not read as explain/example: {len(state)}. "
              f"First drafts among them with no value in them: {len(bare)}. The rule as it was asks on "
              f"{sum(r['now'] for r in bare)} of those, the candidate on {sum(r['candidate'] for r in bare)}; "
              f"a retry lost: {len(lost)}.", ""]
    for r in bare:
        lines.append(f"  - {r['where']} | {r['intent']}/{r['answer_shape'] or '(none)'} | {r['said'][:90]!r} -> "
                     f"{r['first'][:140]!r} | now={r['now']} candidate={r['candidate']} | kept={r['kept']}")
    lines.append("")

    # what part 1 lets through
    through = [r for r in rows if r["now"] and not r["closing_question"]]
    money = re.compile(r"[$₩]\s?\d|\d[\d,]*\s?(?:원|달러|dollars)")
    lines += ["### What part 1 lets through", "",
              f"Replies no longer sent back for ending on a question: {len(through)}. A currency amount is "
              f"stated in {sum(bool(money.search(r['first'])) for r in through)} of them.", ""]
    still = [r for r in on if G._DEFERRAL.search(r["first"]) and G.worked_out(r["first"], question=r["question"])]
    lines += [f"Recorded first drafts that state a number and also use the offer wording the rule looks for "
              f"(\"want me to\", \"would you like me to\", \"should I\"), which part 1 leaves as it is: "
              f"{len(still)}.", ""]

    # the turns still asked
    lines += ["### Turns the candidate still asks on", ""]
    for r in rows:
        if r["candidate"]:
            lines += [f"- **{r['where']}** ({r['intent']}, shape `{r['answer_shape'] or '(none)'}`), second draft "
                      f"{'kept' if r['kept'] else 'not kept'} on record",
                      f"  - asked: {r['said'][:200]}", f"  - draft: {r['first'][:260]}", ""]

    # every changed turn
    lines += ["### Every turn whose decision changes", "",
              "For each: the first draft, what the record shows the person was given, and what the first draft "
              "becomes when it is carried through the stages after this one that can be replayed without a model "
              "(`grounded_values` as it is today, `redundant_permission`, `result_restored`). Where the first "
              "draft is over the turn's length limit the length rewrite, a model call, would also have run on "
              "it; that is marked and not replayed.", ""]
    fidelity = [r for r in changed if r["kept"]]
    same = sum(squash(r["chain_then"]) == squash(r["display"]) for r in fidelity)
    lines += [f"Check on the chain: run on the record's own second-draft path under the grounding rule of the "
              f"time, it gives back the reply the trace recorded in {same} of {len(fidelity)} kept turns.", ""]
    for r in fidelity:
        if squash(r["chain_then"]) != squash(r["display"]):
            lines += [f"  - differs: {r['where']}", f"    - recorded: {r['display'][:300]}",
                      f"    - chain   : {r['chain_then'][:300]}"]
    lines.append("")
    priced = [r for r in changed if AMOUNT.search(r["first"])]
    cells = (("as recorded", lambda r: r["display"]),
             ("with the retry, on today's code", lambda r: r["with_retry_today"] if r["kept"] else r["display"]),
             ("without the retry", lambda r: r["without_retry"]))
    lines += [f"Of the {len(changed)}, the first draft states a dollar amount in {len(priced)}. What is shown of "
              "those:", "", "| | " + " | ".join(label for label, _ in cells) + " |", "|---|---|---|---|",
              "| an amount from the first draft is shown | " + " | ".join(
                  str(sum(bool(set(AMOUNT.findall(r["first"])) & set(AMOUNT.findall(get(r)))) for r in priced))
                  for _, get in cells) + " |",
              "| a dollar amount that is not in the first draft is shown | " + " | ".join(
                  str(sum(bool(set(AMOUNT.findall(get(r))) - set(AMOUNT.findall(r["first"]))) for r in priced))
                  for _, get in cells) + " |",
              "| ends on \"couldn't confirm the specific number\" | " + " | ".join(
                  str(sum(left_out_line(get(r)) for r in priced)) for _, get in cells) + " |", "",
              f"First drafts over the turn's length limit, on which the length rewrite would also have run: "
              f"{sum(r['too_long'] for r in changed)} of {len(changed)} ({sum(r['too_long'] for r in priced)} of "
              f"the {len(priced)} with a dollar amount). The record cannot say what it makes of them: of the "
              f"{sum(r['value_response'] and not r['asked'] for r in rows)} turns the stage was on for and did "
              f"not ask on, {sum(r['value_response'] and not r['asked'] and too_long(r['first'], r) for r in rows)} "
              "had a first draft over the limit.", ""]
    wide = [r for r in changed if squash(r["without_retry_wide"]) != squash(r["without_retry"])]
    lines += ["Scope. The candidate stands at the retry's call site only. Written into the rule itself, part 1 "
              "would also reach the last check on a value question, which puts the first draft's result sentence "
              f"back when a later stage has taken every number out. That changes {len(wide)} of the "
              f"{len(changed)}:", ""]
    for r in wide:
        lines += [f"- {r['where']}", f"  - retry site only: {r['without_retry'][:300]}",
                  f"  - the rule itself: {r['without_retry_wide'][:300]}"]
    lines.append("")
    for i, r in enumerate(changed, 1):
        why = ("ends on a question, number stated" if not r["closing_question"]
               else f"read as `{r['answer_shape']}`")
        lines += [f"**{i}. {r['where']}** ({r['intent']}, shape `{r['answer_shape'] or '(none)'}`; {why}; "
                  f"second draft {'kept' if r['kept'] else 'not kept'} on record)", "",
                  f"- asked: {r['said'][:330]}",
                  f"- first draft: {r['first'][:500]}"]
        if r["kept"]:
            lines += [f"- second draft (recorded): {r['second'][:500]}",
                      f"- shown, as recorded: {r['display'][:500]}",
                      f"- shown, with the retry, on today's code: {r['with_retry_today'][:500]}"]
        else:
            lines += [f"- shown, as recorded (the second draft was not kept): {r['display'][:500]}"]
        new = sorted(numbers(r["without_retry"]) - numbers(r["first"]))
        lines += [f"- **without the retry: {r['without_retry'][:500]}**",
                  f"  - stages that changed it: {', '.join(r['without_stages']) or 'none'}"
                  f"{'; over the length limit, so the length rewrite would also run' if r['too_long'] else ''}"
                  f"{'; numbers not in the first draft: ' + ', '.join(new) if new else ''}", ""]
    return lines


# ------------------------------------------------------------ the router

# Questions that do ask for a number. The first group is the project's own:
# tests/test_response_policy.py, scripts/live_basics_check.py and the
# arithmetic case of tests/contamination_matrix.json, less the three that
# name nothing by themselves ("how much does it cost?" -- those are read in
# their recorded conversations instead). The second group was written for
# this check.
ALONE = [
    ("own", "How many ounces are in a pound?"),
    ("own", "What is the freezing point of water in Fahrenheit?"),
    ("own", "What's the temperature in Seattle?"),
    ("own", "물은 몇 도에서 끓어?"),
    ("own", "일 년은 몇 주야?"),
    ("own", "What's 15% of 80?"),
    ("own", "What's the population of Portland, Maine?"),
    ("own", "How many continents are there?"),
    ("own", "what's 2+2"),
    ("added", "How long is the flight from Seattle to Tokyo?"),
    ("added", "How far is the Moon from Earth?"),
    ("added", "How tall is Mount Rainier?"),
    ("added", "How old is the universe?"),
    ("added", "How much does a gallon of water weigh?"),
    ("added", "What percent of the Earth's surface is water?"),
    ("added", "What's the distance from Seattle to Portland?"),
    ("added", "What's the price of a Costco membership?"),
    ("added", "서울에서 부산까지 몇 킬로야?"),
    ("added", "에베레스트는 높이가 몇 미터야?"),
    ("added", "한국 인구가 얼마야?"),
]
# Asked in the middle of a recorded explanation thread on the same subject.
IN_A_THREAD = [
    ("sl_quantization_cs", "How many different values can 8 bits represent?"),
    ("sl_quantization_cs", "How many bits does a normal float weight use?"),
    ("sl_quantization_cs", "How much smaller does the file get going from 32-bit to 8-bit?"),
    ("sl_quantization_cs", "What's 15% of 80?"),
    ("sl_statistical_significance", "What percent is the usual cutoff for calling something significant?"),
    ("sl_statistical_significance", "How many flukes would you expect out of 100 tests at that cutoff?"),
    ("sl_ko_dividend", "삼성전자는 한 주에 배당금이 얼마야?"),
    ("sl_ko_dividend", "주당 500원이면 100주 갖고 있을 때 얼마 받아?"),
    ("sl_ko_quantization_novice", "8비트면 값이 몇 개까지 돼?"),
    ("sl_ko_llm", "GPT 같은 모델은 숫자가 몇 개나 들어 있어?"),
]
THREADS_EACH = 5
HISTORY_TURNS = 3


class Router:
    """The engine's own router on the real model; everything else scripted."""

    def __init__(self) -> None:
        import ollama

        from core import model_context
        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine.computer_control_mode.set_enabled(False)
        router = self.engine.intent_router
        router.client = model_context.ContextSizedClient(
            ollama.Client(host=self.engine.config.get("llm", "ollama", "base_url")),
            model_context.configured(self.engine.config))
        router.print_confidence_log = False
        self.model = router.model

    def read(self, question: str, history=(), topic: str = "", entity: str = "") -> dict:
        engine = self.engine
        engine._router_history.clear()
        for said, reply in history:
            engine._router_history.extend([{"role": "user", "content": said},
                                           {"role": "assistant", "content": reply}])
        engine._active_topic, engine._active_entity = topic, entity
        started = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            decision = engine.intent_router.route(
                question, recent_turns=list(engine._router_history), has_screen_selection=False,
                project_tools_available=engine.project_mcp is not None,
                conversation_state=engine._build_conversation_state(), pending_action="",
                computer_control_enabled=False)
        return {"intent": decision.intent, "answer_shape": decision.answer_shape,
                "speech_act": decision.speech_act, "normalized": decision.normalized_request,
                "seconds": round(time.monotonic() - started, 2)}


def stage_on(question: str, reading: dict) -> tuple[bool, bool]:
    """Whether the retry can be asked on this turn at all: as it is, and under the candidate."""
    calculation = reading["intent"] == "calculation"
    as_is = calculation or G.asks_for_a_value(question)
    return as_is, as_is and (calculation or reading["answer_shape"] not in EXPLAINING)


def router_run(repeats: int = 5) -> dict:
    rows = recorded()
    router = Router()
    print(f"router model: {router.model}", flush=True)
    out = {"model": router.model, "recorded": [], "alone": [], "thread": []}

    def note(group: str, entry: dict) -> None:
        out[group].append(entry)
        (OUT / "router.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{group}] {entry.get('question', '')[:60]!r} -> {entry['reading']['intent']}/"
              f"{entry['reading']['answer_shape'] or '(none)'}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    # 1. The instrument: recorded turns read again, from their recorded conversation.
    on = [r for r in rows if r["value_response"] and r["family"] != "app"]
    learners = [r for r in on if r["family"] == "sl"]
    follow = [r for r in on if r["family"] in ("tmr", "reask")]
    fresh = [r for r in on if r["family"] in ("search_order", "disagreement") and not r["history"]]
    fresh = fresh[::max(1, len(fresh) // 10)][:10]
    for row in learners + follow + fresh:
        history = row["history"][-HISTORY_TURNS:]
        earlier = next((r for r in reversed(rows[:rows.index(row)])
                        if r["where"].rsplit(" t", 1)[0] == row["where"].rsplit(" t", 1)[0]), None)
        reading = router.read(row["said"], history, topic=(earlier or {}).get("topic", ""),
                              entity=(earlier or {}).get("entity", ""))
        note("recorded", {"where": row["where"], "question": row["said"], "reading": reading,
                          "recorded": {"intent": row["intent"], "answer_shape": row["answer_shape"]}})

    # 2. Real value questions, with nothing said before.
    for source, question in ALONE:
        assert G.asks_for_a_value(question) or "2+2" in question or "15%" in question, question
        for attempt in range(repeats):
            note("alone", {"source": source, "question": question, "attempt": attempt,
                           "reading": router.read(question)})

    # 3. Real value questions in the middle of recorded explanation threads.
    for scenario, question in IN_A_THREAD:
        threads = {}
        for row in rows:
            if row["family"] == "sl" and row["where"].split("/")[2].split(" t")[0] == scenario \
                    and len(row["history"]) == HISTORY_TURNS:
                threads.setdefault(row["where"].split("/")[1], row)
        for run_name, row in sorted(threads.items())[:THREADS_EACH]:
            earlier = rows[rows.index(row) - 1]
            note("thread", {"scenario": scenario, "thread": run_name, "question": question,
                            "after": row["history"][-1][0][:120],
                            "reading": router.read(question, row["history"], topic=earlier["topic"],
                                                   entity=earlier["entity"])})
    return out


def router_report() -> list[str]:
    path = OUT / "router.json"
    if not path.exists():
        return ["## B2. The router on real value questions", "", "Not run.", ""]
    out = json.loads(path.read_text(encoding="utf-8"))
    lines = ["## B2. The router on real value questions", "",
             f"The engine's own router on `{out['model']}`, temperature 0, with the context size production "
             "sends. Part 2 leaves the retry off when the router answers `explain` or `example` on a turn that "
             "is not the calculation route.", ""]

    rec = out["recorded"]
    same = sum(e["reading"]["answer_shape"] == e["recorded"]["answer_shape"] for e in rec)
    side = sum((e["reading"]["answer_shape"] in EXPLAINING) == (e["recorded"]["answer_shape"] in EXPLAINING)
               for e in rec)
    lines += ["### The instrument", "",
              f"{len(rec)} recorded turns the stage was on for, read again from their recorded conversation "
              f"(the last {HISTORY_TURNS} exchanges and the topic the turn before left). The answer shape is the "
              f"one the trace recorded in {same}; it falls on the same side of explain/example in {side}.", ""]
    for e in rec:
        if (e["reading"]["answer_shape"] in EXPLAINING) != (e["recorded"]["answer_shape"] in EXPLAINING):
            lines.append(f"- other side: {e['where']} | recorded `{e['recorded']['answer_shape'] or '(none)'}`, "
                         f"read again `{e['reading']['answer_shape'] or '(none)'}` | {e['question'][:110]}")
    lines.append("")

    def table(group: str, title: str, key) -> None:
        entries = out[group]
        lines.extend([f"### {title}", "", "| Question | Runs | Route | Shape | Stage on: now | candidate |",
                      "|---|---|---|---|---|---|"])
        questions = []
        for e in entries:
            if key(e) not in questions:
                questions.append(key(e))
        lost_runs = total_on = 0
        for q in questions:
            mine = [e for e in entries if key(e) == q]
            gates = [stage_on(e["question"], e["reading"]) for e in mine]
            total_on += sum(a for a, _ in gates)
            lost_runs += sum(a and not b for a, b in gates)
            lines.append(
                f"| {q} | {len(mine)} | {dict(Counter(e['reading']['intent'] for e in mine))} | "
                f"{dict(Counter(e['reading']['answer_shape'] or '(none)' for e in mine))} | "
                f"{sum(a for a, _ in gates)} | {sum(b for _, b in gates)} |")
        lines.extend(["", f"Runs with the stage on as it is: {total_on} of {len(entries)}. Of those, runs where "
                          f"the candidate leaves it off: **{lost_runs}**"
                          f"{f' ({100 * lost_runs / total_on:.0f}%)' if total_on else ''}.", ""])

    table("alone", "Asked with nothing said before", lambda e: f"{e['question']} ({e['source']})")
    table("thread", "Asked in the middle of a recorded explanation thread",
          lambda e: f"{e['question']} ({e['scenario']})")
    return lines


# ------------------------------------------------------------- the tests

SITECUSTOMIZE = '''"""Eval-only (evals/completion_retry_replay.py): the completion-retry decision,
swapped at its one call site in brain/chat_engine.py as the module is loaded."""
import importlib.abc
import importlib.machinery
import os
import sys

ROOT = {root!r}
OLD = """                and AnswerCompletionGuard.first_draft_needs_retry(
                    reply,
                    value_question=value_response,
                    calculation=calculation_response,
                    answer_shape=getattr(route, "answer_shape", ""),
                    question=asked_with_values,
                )"""
NEW = """                and __import__("evals.completion_retry_replay", fromlist=["x"]).at_the_call_site(
                    reply,
                    value_question=value_response,
                    calculation=calculation_response,
                    answer_shape=getattr(route, "answer_shape", ""),
                    question=asked_with_values,
                )"""


class _Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        source = self.get_source(fullname)
        if source.count(OLD) != 1:
            raise ImportError("completion-retry call site not found exactly once")
        log = os.environ.get("ELAINA_EVAL_COMPLETION_LOG", "")
        if log:
            with open(log, "a", encoding="utf-8") as handle:
                handle.write('{{"kind": "installed", "rule": "%s"}}\\n' % os.environ.get("ELAINA_EVAL_COMPLETION_RULE", ""))
        return compile(source.replace(OLD, NEW), self.path, "exec", dont_inherit=True)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != "brain.chat_engine":
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            spec.loader = _Loader(spec.loader.name, spec.loader.path)
        return spec


if os.environ.get("ELAINA_EVAL_COMPLETION_RULE"):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    sys.meta_path.insert(0, _Finder())
'''


def hook_settings(rule: str, log: Path) -> dict:
    """What a process needs in its environment to load with the call site swapped."""
    hook = OUT / "hook"
    hook.mkdir(parents=True, exist_ok=True)
    (hook / "sitecustomize.py").write_text(SITECUSTOMIZE.format(root=str(PROJECT_ROOT)), encoding="utf-8")
    return {"PYTHONPATH": os.pathsep.join(p for p in (str(hook), os.environ.get("PYTHONPATH", "")) if p),
            "ELAINA_EVAL_COMPLETION_RULE": rule, "ELAINA_EVAL_COMPLETION_LOG": str(log)}


def hook_env(rule: str, log: Path) -> dict:
    return {**os.environ, **hook_settings(rule, log), "ELAINA_TTS": "off", "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}


def tests_run(suite: str = "all") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for rule in ("candidate", "now"):
        log, output = OUT / f"tests-{rule}.jsonl", OUT / f"tests-{rule}.log"
        log.unlink(missing_ok=True)
        started = time.monotonic()
        with open(output, "w", encoding="utf-8") as handle:
            code = subprocess.run([sys.executable, "tests/run_tests.py", suite], cwd=PROJECT_ROOT,
                                  env=hook_env(rule, log), stdout=handle, stderr=subprocess.STDOUT).returncode
        print(f"suite '{suite}' with the call site returning the rule '{rule}': exit {code}, "
              f"{time.monotonic() - started:.0f}s", flush=True)


def tests_report() -> list[str]:
    lines = ["## B1. The existing tests with the candidate at the call site", ""]
    for rule, label in (("candidate", "the candidate"), ("now", "the rule as it is, through the same hook")):
        output, log = OUT / f"tests-{rule}.log", OUT / f"tests-{rule}.jsonl"
        if not output.exists():
            lines += [f"- {label}: not run.", ""]
            continue
        text = output.read_text(encoding="utf-8", errors="replace")
        ran = re.findall(r"Ran (\d+) tests? in", text)
        summary = [line.strip() for line in text.splitlines()
                   if re.search(r"stages passed|^(?:OK|FAILED)\b|expected failures?=|\[Turn trace\]", line.strip())]
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        calls = [c for c in calls if c.get("kind", "decision") == "decision"]
        differ = [c for c in calls if c["now"] != c["candidate"]]
        lines += [f"**With {label}**", "",
                  f"- tests run: {', '.join(ran) or '?'}; " + " | ".join(summary[-4:]),
                  f"- the patched call site was reached {len(calls)} times in the run; the two rules gave a "
                  f"different answer on {len(differ)} of them.", ""]
        for c in differ[:20]:
            lines.append(f"  - now={c['now']} candidate={c['candidate']} | shape `{c['answer_shape'] or '(none)'}` "
                         f"calculation route={c['calculation_route']} | asked {c['question'][:70]!r} | "
                         f"reply {c['reply'][:110]!r}")
        lines.append("")
    return lines


# ---------------------------------------------------------- the live test

PRICE = "How much does the iPhone 18 Pro cost?"
FOLLOW_UP = ("What's the newest iPhone model right now?", "How much does it cost?")
# The threads that ended in an invented calculation, and the turn it came on.
THREADS = (("sl/ab2-deep-s1/sl_statistical_significance", 6),
           ("sl/ab2-off-s2/sl_statistical_significance", 5),
           ("sl/pilot6-current/sl_statistical_significance", 3))
LIVE_RULES = ("now", "candidate")
PRICE_PAIRS, FOLLOW_UP_PAIRS, OTHER_PAIRS = 10, 5, 2
# The closing-question half only shows on a first draft that states the price
# and ends on an offer -- about one recorded price turn in nine. Price pairs
# go on past PRICE_PAIRS until the candidate arm has met WANTED such drafts,
# or PRICE_MOST pairs have been asked.
WANTED, PRICE_MOST = 5, 20


def knowledge() -> list:
    from scripts.live_basics_check import KNOWLEDGE

    return [(case, turns[0], check) for case, turns, check in KNOWLEDGE]


def live_plan(rules: tuple = LIVE_RULES, price: int = PRICE_PAIRS, follow_up: int = FOLLOW_UP_PAIRS,
              other: int = OTHER_PAIRS) -> list[dict]:
    rows = recorded()
    threads = [(key.split("/")[1], [r["said"] for r in rows if r["where"].rsplit(" t", 1)[0] == key][:turns])
               for key, turns in THREADS]
    basics = [question for _, question, _ in knowledge()]
    plan = []

    def pair(kind: str, number: int, turns) -> None:
        for rule in (rules if number % 2 else rules[::-1]):
            plan.append({"id": f"{kind}-{rule}-{number}", "set": kind.split("_")[0], "rule": rule,
                         "turns": list(turns)})

    # Past ``price`` only where the candidate arm is there to be waited on.
    most = max(PRICE_MOST, price) if "candidate" in rules and price else price
    for number in range(1, max(most, follow_up, other) + 1):
        if number <= most:
            pair("price", number, [PRICE])
        if number <= follow_up:
            pair("followup", number, FOLLOW_UP)
        if number <= other:
            pair("basics", number, basics)
            for name, said in threads:
                pair(f"thread_{name}", number, said)
    return plan


def decisions(log: Path) -> list[dict]:
    if not log.exists():
        return []
    found = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [entry for entry in found if entry.get("kind") == "decision"]


def live(name: str, rules: tuple = LIVE_RULES, price: int = PRICE_PAIRS, follow_up: int = FOLLOW_UP_PAIRS,
         other: int = OTHER_PAIRS) -> int:
    """``rules``: "now" and "candidate" are swapped in at the call site; "production" swaps nothing."""
    from datetime import datetime

    os.environ.setdefault("ELAINA_TTS", "off")
    from evals import run as runner
    from evals import sim_learner as sl
    from evals.phase2 import unload_models

    if runner.port_open():
        print("Port 8765 is in use -- is Elaina already running? Close it first.")
        return 1
    out = OUT / name
    (out / "logs").mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()
    plan = live_plan(tuple(rules), price, follow_up, other)
    (out / "meta.json").write_text(json.dumps({
        "arm": "current", "env": sl.ARMS["current"], "rules": list(rules), "git_head": head,
        "uncommitted": [line[3:] for line in dirty], "price_pairs": price, "price_most": PRICE_MOST,
        "wanted": WANTED, "started_at": datetime.now().isoformat(timespec="seconds")},
        indent=1, ensure_ascii=False), encoding="utf-8")
    path = out / "dialogues.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}

    def met() -> int:
        """Candidate-arm price turns on which the two rules decided differently."""
        return sum(any(d["now"] != d["candidate"] for d in decisions(out / "logs" / f"{item['id']}.rule.jsonl"))
                   for item in plan if item["set"] == "price" and item["rule"] == "candidate")

    unload_models()
    for item in plan:
        ident = item["id"]
        number = int(ident.rsplit("-", 1)[1])
        if item["set"] == "price" and number > price:
            # Past the first ten pairs only while the half being tested has not shown often enough;
            # a pair once begun is finished, so the arms stay the same size.
            first_of_pair = not any(other["id"] in done for other in plan
                                    if other["set"] == "price" and other["id"].rsplit("-", 1)[1] == str(number))
            if first_of_pair and met() >= WANTED:
                break
        if ident in done:
            continue
        log = out / "logs" / f"{ident}.rule.jsonl"
        log.unlink(missing_ok=True)
        env = {**sl.ARMS["current"], **({} if item["rule"] == "production" else hook_settings(item["rule"], log))}
        backend = sl.Elaina(out / "rt" / ident, out / "logs" / f"{ident}.log", env)
        started, turns = time.monotonic(), []
        try:
            for said in item["turns"]:
                reply, seconds = backend.reply(said)
                turns.append({"said": said, "reply": reply, "seconds": seconds})
        finally:
            backend.close()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": ident, "set": item["set"], "rule": item["rule"], "turns": turns},
                                    ensure_ascii=False) + "\n")
        done.add(ident)
        print(f"{ident}: {len(turns)} turn(s), {time.monotonic() - started:.0f}s; candidate-arm price drafts "
              f"met so far: {met()}", flush=True)
    return 0


def live_report(name: str) -> str:
    out = OUT / name
    talks = [json.loads(line) for line in (out / "dialogues.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    rows = recorded(out)
    by = {}
    for row in rows:
        by.setdefault(row["ident"], []).append(row)
    for row in rows:
        row["now"], row["candidate"] = now(row["first"], **facts(row)), candidate(row["first"], **facts(row))
        row["production"] = production(row["first"], **facts(row))
        row["too_long"] = too_long(row["first"], row)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    LIVE_RULES = tuple(meta["rules"])
    swapped = [rule for rule in LIVE_RULES if rule != "production"]
    label = {"now": "rule as it was", "candidate": "candidate", "production": "production"}
    bar = "|---|" + "---|" * len(LIVE_RULES)
    lines = [f"# The completion retry, live: `{name}`", "",
             f"Real backend, `turn_move` and the explanation contract off, speech off, a fresh backend for each "
             f"conversation, rules: {', '.join(label[rule] for rule in LIVE_RULES)}. HEAD "
             f"`{meta['git_head'][:7]}` with the working tree's uncommitted changes. "
             + ("A swapped rule is put in at the call site by an eval-only hook; production is not changed for it."
                if swapped else "Nothing is swapped: this is the engine as it is."), ""]

    # the hook, and whether it governed
    installed = governed = turns_all = 0
    for talk in talks:
        log = out / "logs" / f"{talk['id']}.rule.jsonl"
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        installed += '"installed"' in text
        for row in by.get(talk["id"], []):
            turns_all += 1
            governed += row["asked"] == row[talk["rule"]]
    logged = sum(len(decisions(out / "logs" / f"{talk['id']}.rule.jsonl")) for talk in talks)
    lines += ["## The rule in force", "",
              f"- Conversations: {len(talks)} ({dict(Counter(t['set'] + '/' + t['rule'] for t in talks))}); "
              f"turns recorded: {turns_all}.",
              f"- The patched call site was loaded in {installed} of "
              f"{sum(t['rule'] != 'production' for t in talks)} backends that were to have it, and reached "
              f"{logged} times.",
              f"- A second draft was asked for on exactly the turns the arm's own rule says to ask on: "
              f"{governed} of {turns_all} turns.", ""]

    def of(kind: str, rule: str, turn: int | None = None) -> list[dict]:
        found = []
        for talk in talks:
            if talk["set"] == kind and talk["rule"] == rule:
                mine = by.get(talk["id"], [])
                found += mine if turn is None else [r for r in mine if r["number"] == turn]
        return found

    def shown_amounts(row: dict) -> set:
        return set(AMOUNT.findall(row["display"]))

    def table(kind: str, turn: int, title: str) -> None:
        measures = (
            ("turns", lambda r: True),
            ("the evidence holds a dollar amount", lambda r: bool(r["evidence_amounts"])),
            ("first draft states an amount the evidence holds",
             lambda r: bool(set(AMOUNT.findall(r["first"])) & set(r["evidence_amounts"]))),
            ("first draft states the price and ends on a question (the two rules differ)",
             lambda r: r["now"] != r["candidate"]),
            ("first draft over the length limit", lambda r: r["too_long"]),
            ("second draft asked for", lambda r: r["asked"]),
            ("second draft kept", lambda r: r["kept"]),
            ("length rewrite changed the reply", lambda r: "length_rewrite" in r["changed"]),
            ("`grounded_values` changed the reply", lambda r: "grounded_values" in r["changed"]),
            ("`result_restored` changed the reply", lambda r: "result_restored" in r["changed"]),
            ("**shown: an amount the evidence holds**", lambda r: bool(shown_amounts(r) & set(r["evidence_amounts"]))),
            ("shown: an amount the evidence does not hold",
             lambda r: bool(shown_amounts(r) - set(r["evidence_amounts"]))),
            ("shown: ends on \"couldn't confirm the specific number\"", lambda r: left_out_line(r["display"])),
            ("shown: ends on a question", lambda r: r["display"].strip().endswith("?")),
        )
        lines.extend([f"## {title}", "", "| | " + " | ".join(label[rule] for rule in LIVE_RULES) + " |", bar])
        for text, test in measures:
            lines.append(f"| {text} | " + " | ".join(str(sum(bool(test(r)) for r in of(kind, rule, turn)))
                                                      for rule in LIVE_RULES) + " |")
        seconds = {rule: sorted(r["seconds"] for r in of(kind, rule, turn)) for rule in LIVE_RULES}
        lines.append("| median seconds for the turn | " + " | ".join(
            f"{seconds[rule][len(seconds[rule]) // 2]:.1f}" if seconds[rule] else "-" for rule in LIVE_RULES) + " |")
        lines.append("")
        differ = [r for rule in LIVE_RULES for r in of(kind, rule, turn) if r["now"] != r["candidate"] or r["asked"]]
        lines.extend([f"Every turn on which the rules differ or a second draft was asked for ({len(differ)}):", ""])
        for r in differ:
            arm = next(t["rule"] for t in talks if t["id"] == r["ident"])
            lines.extend([f"- **{r['ident']}** ({label[arm]}; {r['intent']}, shape `{r['answer_shape'] or '(none)'}`)",
                          f"  - first draft: {r['first'][:420]}"])
            if r["asked"]:
                lines.append(f"  - second draft ({'kept' if r['kept'] else 'not kept'}): {r['second'][:420]}")
            lines.extend([f"  - stages that changed the reply: {', '.join(r['changed']) or 'none'}",
                          f"  - shown: {r['display'][:420]}"])
        lines.append("")

    table("price", 1, f"1. The price question: {PRICE!r}")
    table("followup", 2, f"2. The follow-up price question: {FOLLOW_UP[1]!r} after {FOLLOW_UP[0]!r}")

    # knowledge
    cases = knowledge()
    lines += ["## 3. The knowledge questions (scripts/live_basics_check.py), one backend for all of them", "",
              "| Question | " + " | ".join(f"{label[rule]}: right" for rule in LIVE_RULES) + " | "
              + " | ".join(f"{label[rule]}: second draft asked / kept" for rule in LIVE_RULES) + " |",
              "|---|" + "---|" * (2 * len(LIVE_RULES))]
    wrong = []
    totals = {rule: [0, 0] for rule in LIVE_RULES}
    for index, (case, question, check) in enumerate(cases, 1):
        cells, retries = [], []
        for rule in LIVE_RULES:
            mine = of("basics", rule, index)
            right = [bool(check(r["display"])) for r in mine]
            totals[rule][0] += sum(right)
            totals[rule][1] += len(right)
            cells.append(f"{sum(right)} of {len(right)}")
            retries.append(f"{sum(r['asked'] for r in mine)} / {sum(r['kept'] for r in mine)}")
            wrong += [(rule, question, r) for r, ok in zip(mine, right) if not ok]
        lines.append(f"| {question} | " + " | ".join(cells) + " | " + " | ".join(retries) + " |")
    lines += ["| **all** | " + " | ".join(f"**{totals[rule][0]} of {totals[rule][1]}**" for rule in LIVE_RULES)
              + " |" + " |" * len(LIVE_RULES), ""]
    for rule, question, r in wrong:
        lines += [f"- not right ({label[rule]}, {r['ident']}): {question}",
                  f"  - first draft: {r['first'][:300]}", f"  - shown: {r['display'][:300]}",
                  f"  - second draft asked: {r['asked']}; kept: {r['kept']}; stages that changed the reply: "
                  f"{', '.join(r['changed']) or 'none'}"]
    lines.append("")
    differ = [r for rule in LIVE_RULES for r in of("basics", rule) if r["now"] != r["candidate"]]
    lines += [f"Knowledge turns on which the two rules differ: {len(differ)}.", ""]
    for r in differ:
        lines += [f"- {r['ident']} t{r['number']}: {r['said']}", f"  - first draft: {r['first'][:300]}",
                  f"  - shown: {r['display'][:300]}"]
    lines.append("")

    # the threads
    lines += ["## 4. The explanation threads that ended in an invented calculation", "",
              "The recorded user turns of each, said again in order. Her replies are new, so the later turns "
              "answer a conversation that is not word for word the one they were written in.", "",
              "| | " + " | ".join(label[rule] for rule in LIVE_RULES) + " |", bar]
    for text, test in (("turns", lambda r: True),
                       ("the stage is switched on (wording)", lambda r: r["value_response"]),
                       ("read as explain/example by the router", lambda r: r["answer_shape"] in EXPLAINING),
                       ("the two rules differ", lambda r: r["now"] != r["candidate"]),
                       ("second draft asked for", lambda r: r["asked"]),
                       ("second draft kept", lambda r: r["kept"])):
        lines.append(f"| {text} | " + " | ".join(str(sum(bool(test(r)) for r in of("thread", rule)))
                                                  for rule in LIVE_RULES) + " |")
    lines.append("")
    for talk in talks:
        if talk["set"] != "thread":
            continue
        mine = by.get(talk["id"], [])
        lines += [f"**{talk['id']}** ({label[talk['rule']]})", ""]
        for r in mine:
            marks = []
            if r["value_response"]:
                marks.append(f"stage on, shape `{r['answer_shape'] or '(none)'}`")
            if r["now"] != r["candidate"]:
                marks.append("rules differ")
            if r["asked"]:
                marks.append(f"second draft asked, {'kept' if r['kept'] else 'not kept'}")
            last = r is mine[-1]
            if not (marks or last):
                continue
            lines += [f"- t{r['number']}{' (the turn the invented calculation came on)' if last else ''}: "
                      f"{r['said'][:200]}" + (f" [{'; '.join(marks)}]" if marks else ""),
                      f"  - first draft: {r['first'][:420]}"]
            if r["asked"]:
                lines.append(f"  - second draft: {r['second'][:420]}")
            lines.append(f"  - shown: {r['display'][:420]}")
        lines.append("")

    text = "\n".join(lines) + "\n"
    (out / "LIVE.md").write_text(text, encoding="utf-8")
    return text


# ------------------------------------------------------------ the report


def report() -> str:
    path = OUT / "replay.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else replay()
    lines = ["# The completion retry: the rule as it is against a narrower one", "",
             "Written by `evals/completion_retry_replay.py`. Production is not changed by anything here.", ""]
    lines += replay_report(result) + tests_report() + router_report()
    text = "\n".join(lines) + "\n"
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if "--replay" in args:
        result = replay()
        print("\n".join(replay_report(result)))
    if "--router" in args:
        router_run()
    if "--tests" in args:
        tests_run(next((a.split("=", 1)[1] for a in args if a.startswith("--suite=")), "all"))
    def option(name: str, default):
        found = next((a.split("=", 1)[1] for a in args if a.startswith(f"--{name}=")), None)
        return default if found is None else type(default)(found)

    if "--live" in args:
        return live(args[args.index("--live") + 1], tuple(option("rules", ",".join(LIVE_RULES)).split(",")),
                    option("price", PRICE_PAIRS), option("followup", FOLLOW_UP_PAIRS), option("other", OTHER_PAIRS))
    if "--live-report" in args:
        text = live_report(args[args.index("--live-report") + 1])
        print(text)
        return 0
    if "--report" in args or not args:
        text = report()
        print(f"written: {OUT / 'REPORT.md'} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
