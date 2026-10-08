"""``result_restored`` against ``grounded_values``, replayed.

    .venv/Scripts/python.exe -m evals.restoration_replay --replay
    .venv/Scripts/python.exe -m evals.restoration_replay --tests
    .venv/Scripts/python.exe -m evals.restoration_replay --report

The last check on a value question (brain/chat_engine.py, after ``their_name``)
puts the draft's own result sentence back when the finished reply no longer
states any number the draft worked out. It was built for a calculation whose
result a later rewrite dropped. Reviewed over every recorded turn
(2026-10-07): it had fired four times, each time straight after
``grounded_values`` had taken that sentence out as unverified, and the whole
reply was replaced by it -- once with a wrong price ("$1,099 for the 128GB
version ... $1,499 for the 1TB").

The rule the replay was written to test:

    the result sentence is not put back when it holds a number the grounding
    stage took out of the reply on this turn.

That rule was ``candidate`` here when the replay was first run. It is the
engine's own since 2026-10-08: the grounding steps keep what they took out
(``AnswerCompletionGuard.numbers_taken_out``) and ``the_result_sentence`` is
told. ``now`` is the check as it was until then, ``candidate`` is kept as the
record of what was tested, and the replay says whether production and the
candidate agree on every recorded turn.

``--replay`` re-runs the check on every recorded turn, from what the trace
holds, as it was and with that rule. No model is called. Its first pass is the
check on the replay itself: the rule as it is has to put a sentence back on
exactly the turns the trace recorded one being put back, with the same words.

The cost was known before it was run and accepted: ``grounded_values`` drops
whole sentences, so a verified price that shares a sentence with an unverified
one goes with it, and on those turns the restoration was the only reason the
verified price was shown.

``--tests`` runs the whole deterministic suite twice through a
``sitecustomize`` module this script writes under ``runtime/``, which swaps
the check inside the test process: once returning the check as it was, once
the rule. (Before the rule was applied the hook also had to collect what the
grounding steps took out; the engine does that itself now.)
"""

from __future__ import annotations

import glob
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
OUT = ROOT / "restoration"
STAGE = "result_restored"
# What the engine does between the first draft and the point it remembers
# the reply as "answered", and what it does after the check.
BEFORE_IT_IS_REMEMBERED = ("vision_retry", "final_form", "completion_retry", "computed_result")
AFTER_THE_CHECK = (STAGE, "celsius", "near_miss_prefix")
RULE_ENV, LOG_ENV = "ELAINA_EVAL_RESTORE_RULE", "ELAINA_EVAL_RESTORE_LOG"


# ------------------------------------------------------------- the rules


def taken_out(before: str, after: str) -> set:
    """The numbers a stage took out of the reply altogether."""
    return G._numbers(before) - G._numbers(after)


def production(reply: str, *, answered: str, question: str, gone: set) -> str:
    """What the engine's check puts in place of ``reply`` today, or ''."""
    if not (answered and G.dropped_the_result(reply, draft=answered, question=question)):
        return ""
    said_it = G.the_result_sentence(answered, question=question, taken_out=set(gone))
    korean = any("가" <= ch <= "힣" for ch in said_it)
    return said_it if said_it and korean == any("가" <= ch <= "힣" for ch in reply) else ""


def put_back(reply: str, *, answered: str, question: str, gone: set, respect_grounding: bool) -> str:
    """The sentence the check puts in place of ``reply``, or ''.

    As the engine has it, with one switch: with ``respect_grounding`` a
    sentence holding a number the grounding stage took out stays out.
    """
    if not (answered and G.dropped_the_result(reply, draft=answered, question=question)):
        return ""
    said_it = G.the_result_sentence(answered, question=question)
    korean = any("가" <= ch <= "힣" for ch in said_it)
    if not said_it or korean != any("가" <= ch <= "힣" for ch in reply):
        return ""
    if respect_grounding and G._numbers(said_it) & set(gone):
        return ""
    return said_it


# ------------------------------------------- the engine's side of the hook


def sentence_at_the_check(draft, *, question, taken_out) -> str:
    """The sentence the patched check is given to put back (see SITECUSTOMIZE)."""
    was = G.the_result_sentence(draft, question=question)
    would = G.the_result_sentence(draft, question=question, taken_out=set(taken_out))
    log = os.environ.get(LOG_ENV, "")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "kind": "check", "draft": str(draft or ""), "question": str(question or ""),
                "taken_out": sorted(taken_out), "now": bool(was), "candidate": bool(would),
            }, ensure_ascii=False) + "\n")
    return was if os.environ.get(RULE_ENV) == "now" else would


# ------------------------------------------------------------ the record


def text_of(value) -> str:
    return str(value.get("text") or "") if isinstance(value, dict) else str(value or "")


def turn_move_mode(parts: tuple) -> str:
    """What the run's backend had ``turn_move`` set to (its meta.json)."""
    meta = ROOT / parts[0] / parts[1] / "meta.json"
    try:
        return str((json.loads(meta.read_text(encoding="utf-8")).get("env") or {}).get("ELAINA_TURN_MOVE") or "off")
    except (OSError, ValueError):
        return "off"


def recorded() -> list[dict]:
    """Every recorded turn, with the reply as it reached the check."""
    files = [(path, Path(path).relative_to(ROOT).parts)
             for path in sorted(glob.glob(str(ROOT / "**" / "turn_trace" / "*.jsonl"), recursive=True))]
    files += [(path, ("app", Path(path).stem, Path(path).stem, "", ""))
              for path in sorted(glob.glob(str(APP / "*.jsonl")))]
    rows = []
    for path, parts in files:
        mode = turn_move_mode(parts)
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
            question = f"{said} {route.get('normalized_request') or ''}"
            draft = turn.get("draft")
            steps = sorted(turn.get("steps") or (), key=lambda s: s.get("i", 0))

            # The reply as the engine remembered it: after the first
            # final_form and whatever the completion step did.
            stood, finals = text_of(draft), 0
            for step in steps:
                name = str(step.get("name"))
                if name not in BEFORE_IT_IS_REMEMBERED:
                    break
                if name == "final_form":
                    finals += 1
                    if finals > 1:
                        break
                if step.get("changed"):
                    stood = str(step.get("after") or "")
            # The reply as it reached the check, and what grounding took out on the way.
            reply, gone, grounding, restored = text_of(draft), set(), [], None
            for step in steps:
                name = str(step.get("name"))
                if name == STAGE and step.get("changed"):
                    restored = (str(step.get("before")), str(step.get("after")))
                if name in AFTER_THE_CHECK:
                    continue
                if step.get("changed"):
                    if name == "grounded_values":
                        gone |= taken_out(str(step.get("before") or ""), str(step.get("after") or ""))
                        grounding.append((str(step.get("before")), str(step.get("after"))))
                    reply = str(step.get("after") or "")
            # A turn the turn-move owner acted on is answered as an
            # explanation: the engine drops ``value_response`` for it after
            # the trace has written the flag down (brain/chat_engine.py).
            move = context.get("turn_move") if isinstance(context.get("turn_move"), dict) else {}
            acted = mode in ("on", "deep") and move.get("move") == "differently"
            value_response = bool(context.get("value_response")) and not acted
            answered = stood if value_response and not G.needs_retry(
                stood, calculation=True, question=question) else ""
            evidence = turn.get("evidence") or {}
            held = " ".join([str(evidence.get("research") or "")]
                            + [str(item.get("text") or "") for item in evidence.get("ledger") or ()])
            rows.append({
                "where": f"{parts[0]}/{parts[1]}/{parts[-3]} t{number}", "family": parts[0],
                "said": said, "question": question, "intent": str(route.get("intent") or ""),
                "answer_shape": str(route.get("answer_shape") or ""),
                "calculation_route": route.get("intent") == "calculation", "value_response": value_response,
                "source": str(draft.get("source") or "") if isinstance(draft, dict) else "",
                "stood": stood, "answered": answered, "reply": reply, "gone": sorted(gone),
                "grounding": grounding, "restored": restored, "display": str(turn.get("display") or ""),
                "evidence_numbers": sorted(G._numbers(held)),
            })
    return rows


# ------------------------------------------------------------ the replay


def replay() -> dict:
    rows = recorded()
    for row in rows:
        for name, respect in (("now", False), ("candidate", True)):
            row[name] = put_back(row["reply"], answered=row["answered"], question=row["question"],
                                 gone=set(row["gone"]), respect_grounding=respect)
        row["production"] = production(row["reply"], answered=row["answered"], question=row["question"],
                                       gone=set(row["gone"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "replay.json").write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"rows": rows}


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def replay_report(result: dict) -> list[str]:
    rows = result["rows"]
    on = [r for r in rows if r["value_response"]]
    lines = ["## Offline replay", "",
             f"{len(rows)} recorded turns ({dict(Counter(r['family'] for r in rows))}). The check is armed on a "
             f"value question; {len(on)} of them are, {sum(r['calculation_route'] for r in on)} on the calculation "
             "route. No model was called.", ""]

    # the check on the replay
    recorded_back = [r for r in rows if r["restored"]]
    agree = sum(bool(r["now"]) == bool(r["restored"]) for r in rows)
    words = sum(squash(r["now"]) == squash(r["restored"][1]) for r in recorded_back)
    reached = sum(squash(r["reply"]) == squash(r["restored"][0]) for r in recorded_back)
    lines += ["### The check on the replay", "",
              f"- The check as it was, re-run on each recorded turn, puts a sentence back on "
              f"{sum(bool(r['now']) for r in rows)} turns; the traces recorded {len(recorded_back)}. The two agree "
              f"on {agree} of {len(rows)} turns.",
              f"- On the recorded {len(recorded_back)}: the reply the replay hands the check is the one the trace "
              f"recorded in {reached}, and the sentence it puts back is the recorded one in {words}.", ""]
    for r in rows:
        if bool(r["now"]) != bool(r["restored"]):
            lines.append(f"  - disagrees: {r['where']} | recorded={bool(r['restored'])} replay={bool(r['now'])} | "
                         f"draft source={r['source']!r} | {r['said'][:70]!r}")
    lines.append("")

    # the counts
    after_grounding = [r for r in rows if r["now"] and r["gone"]]
    blocked = [r for r in rows if r["now"] and not r["candidate"]]
    other = [r for r in rows if bool(r["candidate"]) and not bool(r["now"])]
    changed = [r for r in rows if r["now"] != r["candidate"]]
    lines += ["### What each rule does", "", "| | as it was | candidate |", "|---|---|---|",
              f"| a sentence put back | {sum(bool(r['now']) for r in rows)} | {sum(bool(r['candidate']) for r in rows)} |",
              f"| ...after the grounding stage had taken a number out on that turn | {len(after_grounding)} | "
              f"{sum(bool(r['candidate']) and bool(r['gone']) for r in rows)} |",
              f"| ...holding a number the grounding stage took out | "
              f"{sum(bool(G._numbers(r['now']) & set(r['gone'])) for r in rows if r['now'])} | "
              f"{sum(bool(G._numbers(r['candidate']) & set(r['gone'])) for r in rows if r['candidate'])} |",
              "",
              f"- Production decides as the tested candidate on "
              f"{sum(r.get('production', r['candidate']) == r['candidate'] for r in rows)} of {len(rows)} "
              "recorded turns.",
              f"- Turns whose reply changes: {len(changed)}. Turns the candidate puts a sentence back on that the "
              f"check as it was does not: {len(other)}.",
              f"- Turns the grounding stage took a number out of, on a value question: "
              f"{sum(bool(r['gone']) for r in on)}. The check as it was then put a sentence back on "
              f"{len(after_grounding)} of them.",
              f"- Turns the check is armed on where no stage took the number out and it was lost anyway (the case "
              f"the check was built for): {sum(bool(r['now']) and not r['gone'] for r in rows)}.", ""]

    lines += ["### Every turn whose reply changes", ""]
    for i, r in enumerate(changed, 1):
        sentence = r["now"]
        mine = sorted(G._numbers(sentence))
        gone = set(r["gone"])
        held = set(r["evidence_numbers"])
        lines += [f"**{i}. {r['where']}** ({r['intent']}, shape `{r['answer_shape'] or '(none)'}`)", "",
                  f"- asked: {r['said'][:260]}",
                  f"- the draft, as it stood: {r['stood'][:420]}"]
        for before, after in r["grounding"]:
            lines += [f"- the grounding stage was given: {before[:420]}", f"- and left: {after[:420]}"]
        lines += [f"- numbers in the sentence put back: " + ", ".join(
                      f"{n} ({'taken out by grounding' if n in gone else 'not taken out'}; "
                      f"{'in' if n in held else 'not in'} the turn's evidence)" for n in mine),
                  f"- shown, as it was: {sentence[:420]}",
                  f"- **shown, with the candidate: {r['reply'][:420]}**", ""]
    return lines


# ------------------------------------------------------------- the tests

SITECUSTOMIZE = '''"""Eval-only (evals/restoration_replay.py): the last check on a value question,
told what the grounding stage took out, swapped in as brain/chat_engine.py is loaded."""
import importlib.abc
import importlib.machinery
import os
import sys

ROOT = {root!r}
HELP = '__import__("evals.restoration_replay", fromlist=["x"])'
OLD = """                said_it = AnswerCompletionGuard.the_result_sentence(
                    answered_calculation, question=asked_with_values,
                    taken_out=taken_out_by_grounding,
                )"""
NEW = """                said_it = %s.sentence_at_the_check(
                    answered_calculation, question=asked_with_values,
                    taken_out=taken_out_by_grounding,
                )""" % HELP


def _patched(source):
    if source.count(OLD) != 1:
        raise ImportError("the restoration check was not found as expected")
    return source.replace(OLD, NEW)


class _Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        code = compile(_patched(self.get_source(fullname)), self.path, "exec", dont_inherit=True)
        log = os.environ.get("ELAINA_EVAL_RESTORE_LOG", "")
        if log:
            with open(log, "a", encoding="utf-8") as handle:
                handle.write('{{"kind": "installed", "rule": "%s"}}\\n' % os.environ.get("ELAINA_EVAL_RESTORE_RULE", ""))
        return code


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != "brain.chat_engine":
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            spec.loader = _Loader(spec.loader.name, spec.loader.path)
        return spec


if os.environ.get("ELAINA_EVAL_RESTORE_RULE"):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    sys.meta_path.insert(0, _Finder())
'''


def hook_env(rule: str, log: Path) -> dict:
    hook = OUT / "hook"
    hook.mkdir(parents=True, exist_ok=True)
    (hook / "sitecustomize.py").write_text(SITECUSTOMIZE.format(root=str(PROJECT_ROOT)), encoding="utf-8")
    return {**os.environ,
            "PYTHONPATH": os.pathsep.join(p for p in (str(hook), os.environ.get("PYTHONPATH", "")) if p),
            RULE_ENV: rule, LOG_ENV: str(log), "ELAINA_TTS": "off", "HF_HUB_OFFLINE": "1",
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
        print(f"suite '{suite}' with the check returning the rule '{rule}': exit {code}, "
              f"{time.monotonic() - started:.0f}s", flush=True)


def tests_report() -> list[str]:
    lines = ["## The suite, with the check as it was swapped back in", ""]
    for rule, label in (("candidate", "the rule"), ("now", "the check as it was")):
        output, log = OUT / f"tests-{rule}.log", OUT / f"tests-{rule}.jsonl"
        if not output.exists():
            lines += [f"- {label}: not run.", ""]
            continue
        text = output.read_text(encoding="utf-8", errors="replace")
        ran = re.findall(r"Ran (\d+) tests? in", text)
        summary = [line.strip() for line in text.splitlines()
                   if re.search(r"stages passed|^(?:OK|FAILED)\b|\[Turn trace\]", line.strip())]
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        checks = [c for c in calls if c.get("kind") == "check"]
        differ = [c for c in checks if c["now"] != c["candidate"]]
        lines += [f"**With {label}**", "",
                  f"- tests run: {', '.join(ran) or '?'}; " + " | ".join(summary[-3:]),
                  f"- the check found the result gone {len(checks)} times; it had a sentence to put back on "
                  f"{sum(c['now'] for c in checks)} as it was and {sum(c['candidate'] for c in checks)} with the "
                  f"rule; grounding had taken a number out on {sum(bool(c['taken_out']) for c in checks)}; the two "
                  f"differ on {len(differ)}.", ""]
        for c in differ[:12]:
            lines.append(f"  - taken out {c['taken_out']} | asked {c['question'][:60]!r} | draft {c['draft'][:110]!r}")
        lines.append("")
    return lines


def report() -> str:
    path = OUT / "replay.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else replay()
    lines = ["# `result_restored` against `grounded_values`", "",
             "Written by `evals/restoration_replay.py`. Production is not changed by anything here.", ""]
    lines += replay_report(result) + tests_report()
    text = "\n".join(lines) + "\n"
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if "--replay" in args:
        print("\n".join(replay_report(replay())))
    if "--tests" in args:
        tests_run()
    if "--report" in args or not args:
        text = report()
        print(f"written: {OUT / 'REPORT.md'} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
