"""The final check's "restatement" strip, replayed: the rule as it is against two narrower ones.

    .venv/Scripts/python.exe -m evals.echo_strip_replay --replay
    .venv/Scripts/python.exe -m evals.echo_strip_replay --tests
    .venv/Scripts/python.exe -m evals.echo_strip_replay --router
    .venv/Scripts/python.exe -m evals.echo_strip_replay --report

``final_check`` (brain/chat_engine.py::_final_response_check) takes a
restatement of the person's message off the front of a reply
(``ResponseQualityGuard.strip_current_turn_echo``). One of its readings is a
first sentence at least 80% character-alike to what they said: their own
sentence handed back with the ending changed ("회사에서 하루 종일 회의만
했어" -> "...했네요"). Reviewed over every recorded turn (2026-10-08): it had
fired 12 times, all through that reading and all on a turn that asked
something, where the sentence that mirrors the question is its answer:

    What is the freezing point of water in Fahrenheit?
    -> [The freezing point of water is 32 degrees Fahrenheit.] That's when ...
    Okay, so it doesn't mean it's a big difference, right?
    -> [No, it doesn't mean it's a big difference.] Significance just means ...

Six were put back by the last check on a value question; six, the yes/no
verdicts, were not.

Two rules are tried beside the one in force, each touching that reading only
(the exact repeat, and the clause before a dash or comma, stay as they are):

    react_only      the reading applies only when she is reacting to something
                    the person told her -- the style layer's ``react`` act,
                    which the check is already handed;
    not_a_question  the reading is passed over when what they said ends in a
                    question mark.
    both            added after the router check: the reading applies only
                    when she is reacting *and* what they said does not end in
                    a question mark. Each rule alone has a hole the other
                    covers -- a "..., right?" read as a statement, and a
                    spoken question that arrives without its mark.

``both`` is the engine's own rule since 2026-10-08 (``production`` here):
``_final_response_check`` works out whether she was told something and passes
``re_ended`` to the strip. ``now`` is the rule as it was until then; the arms
are kept as the record of what was tested, and the replay says whether
production and ``both`` agree on every recorded turn.

``--replay`` re-runs them on every recorded turn the check ran on. No
model is called. Its first pass is the check on the replay itself: the rule
as it is has to strip exactly the turns the traces recorded being stripped,
and leave what they recorded.

``--tests`` runs the whole deterministic suite once for each rule, swapped in
at the check's two calls by a ``sitecustomize`` module this script writes
under ``runtime/``; production is not changed for it.

``--router`` asks the engine's own router, on the real 8B, how it reads
things people tell her and things they ask, with and without a question
mark, and works out the act the style layer would give each. That is the risk
in ``react_only``: a statement read as anything else keeps an echoed opener.
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

from brain.response_quality import ResponseQualityGuard as Q  # noqa: E402

ROOT = PROJECT_ROOT / "runtime" / "evals"
APP = PROJECT_ROOT / "runtime" / "turn_trace"
OUT = ROOT / "echo_strip"
STAGE = "final_check"
LINE = "Removed a restatement of the current message"
REACT, RECEIPT = "react", "receipt"
RULE_ENV, LOG_ENV = "ELAINA_EVAL_ECHO_RULE", "ELAINA_EVAL_ECHO_LOG"
ARMS = ("now", "react_only", "not_a_question", "both")
LABEL = {"now": "as it was", "react_only": "only when reacting", "not_a_question": "not after a question mark",
         "both": "reacting, and no question mark"}


# ------------------------------------------------------------- the rules


def strip(reply: str, said: str, *, re_ended: bool = True) -> str:
    """``strip_current_turn_echo``, line for line, with one switch: whether a
    first sentence that is their sentence re-ended counts as a restatement."""
    text = str(reply or "").strip()
    said = str(said or "").strip()
    if not text or not said:
        return text
    sentences = Q._ECHO_SENTENCE.split(text, maxsplit=1)
    if len(sentences) == 2 and Q._normalize(sentences[0]) == Q._normalize(said):
        return sentences[1].strip()
    if re_ended and len(sentences) == 2 and Q._says_it_back(sentences[0], said):
        return sentences[1].strip()
    for separator in (Q._ECHO_DASH, Q._ECHO_COMMA):
        stripped = Q._strip_before(text, said, separator)
        if stripped != text:
            return stripped
    return text


def production(reply: str, said: str, act: str) -> str:
    """What the engine's check leaves of ``reply`` today (brain/chat_engine.py)."""
    told = str(act or "") == REACT and not str(said or "").rstrip().endswith(("?", "？"))
    return Q.strip_current_turn_echo(reply, said, re_ended=told)


def stripped_by(rule: str, reply: str, said: str, act: str) -> str:
    """What ``rule`` leaves of ``reply``."""
    if rule == "production":
        return production(reply, said, act)
    if rule == "react_only":
        return strip(reply, said, re_ended=str(act or "") == REACT)
    if rule == "not_a_question":
        return strip(reply, said, re_ended=not str(said or "").strip().endswith(("?", "？")))
    if rule == "both":
        return strip(reply, said, re_ended=str(act or "") == REACT
                     and not str(said or "").strip().endswith(("?", "？")))
    return Q.strip_current_turn_echo(reply, said)


def at_the_check(reply, said, *, act) -> str:
    """What the patched check is handed back (see SITECUSTOMIZE)."""
    results = {rule: stripped_by(rule, reply, said, act) for rule in ARMS}
    log = os.environ.get(LOG_ENV, "")
    if log and len(set(results.values()) | {str(reply or "").strip()}) > 1:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": "strip", "reply": str(reply or ""), "said": str(said or ""),
                                     "act": str(act or ""), **{rule: results[rule] != str(reply or "").strip()
                                                               for rule in ARMS}}, ensure_ascii=False) + "\n")
    return results.get(os.environ.get(RULE_ENV, ""), production(reply, said, act))


# ------------------------------------------------------------ the record


def text_of(value) -> str:
    return str(value.get("text") or "") if isinstance(value, dict) else str(value or "")


def recorded() -> list[dict]:
    """Every recorded turn the check ran on, with the reply as it reached it."""
    files = [(path, Path(path).relative_to(ROOT).parts)
             for path in sorted(glob.glob(str(ROOT / "**" / "turn_trace" / "*.jsonl"), recursive=True))]
    files += [(path, ("app", Path(path).stem, Path(path).stem, "", ""))
              for path in sorted(glob.glob(str(APP / "*.jsonl")))]
    rows = []
    for path, parts in files:
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError:
                continue
            steps = sorted(turn.get("steps") or (), key=lambda s: s.get("i", 0))
            step = next((s for s in steps if s.get("name") == STAGE), None)
            if step is None:
                continue
            context = turn.get("context") or {}
            route = context.get("route") or {}
            draft = turn.get("draft")
            given = text_of(draft)
            for earlier in steps:
                if earlier is step:
                    if earlier.get("changed"):
                        given = str(earlier.get("before") or "")
                    break
                if earlier.get("changed"):
                    given = str(earlier.get("after") or "")
            left = str(step.get("after")) if step.get("changed") else given
            log = " ".join(str(x) for x in (step.get("log") or [])) + " " + " ".join(
                str(x) for x in (turn.get("log") or []))
            later = [str(s.get("name")) for s in steps if s.get("i", 0) > step.get("i", 0) and s.get("changed")]
            rows.append({
                "where": f"{parts[0]}/{parts[1]}/{parts[-3]} t{number}", "family": parts[0],
                "said": str(turn.get("user_input") or ""), "act": str(context.get("act") or ""),
                "intent": str(route.get("intent") or ""), "speech_act": str(route.get("speech_act") or ""),
                "source": str(draft.get("source") or "") if isinstance(draft, dict) else "",
                "given": given, "left": left, "said_so": bool(step.get("changed")) and LINE in log,
                "changed": bool(step.get("changed")), "later": later, "display": str(turn.get("display") or ""),
            })
    return rows


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def replay() -> dict:
    rows = recorded()
    for row in rows:
        row["copy_agrees"] = strip(row["given"], row["said"]) == Q.strip_current_turn_echo(row["given"], row["said"])
        for rule in ARMS:
            row[rule] = stripped_by(rule, row["given"], row["said"], row["act"])
        row["production"] = production(row["given"], row["said"], row["act"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "replay.json").write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"rows": rows}


def replay_report(result: dict) -> list[str]:
    rows = result["rows"]
    lines = ["## A. Offline replay", "",
             f"{len(rows)} recorded turns the check ran on ({dict(Counter(r['family'] for r in rows))}); acts: "
             f"{dict(Counter(r['act'] for r in rows))}. No model was called.", ""]

    # The check passes a receipt and a hand-written reply over before it strips anything.
    def runs(r: dict) -> bool:
        return r["act"] != RECEIPT and r["source"] in ("", "model")

    would = [r for r in rows if runs(r) and squash(r["now"]) != squash(r["given"])]
    did = [r for r in rows if r["said_so"]]
    agree = sum((runs(r) and squash(r["now"]) != squash(r["given"])) == r["said_so"] for r in rows)
    same = sum(squash(r["now"]) == squash(r["left"]) for r in did)
    lines += ["### The check on the replay", "",
              f"- The rule as it was, re-run on each recorded reply, strips {len(would)}; the traces recorded "
              f"{len(did)}. The two agree on {agree} of {len(rows)} turns.",
              f"- On the recorded {len(did)}, what the replay leaves is what the trace recorded in {same}.",
              f"- The line-for-line copy used for the two rules, with its switch on, agrees with the engine's own "
              f"on {sum(r['copy_agrees'] for r in rows)} of {len(rows)} replies.", ""]
    for r in rows:
        if (runs(r) and squash(r["now"]) != squash(r["given"])) != r["said_so"]:
            lines.append(f"  - disagrees: {r['where']} | act {r['act']} source {r['source']!r} | recorded "
                         f"{r['said_so']} | {r['said'][:60]!r} -> {r['given'][:90]!r}")
    lines.append("")

    lines += ["### What each rule strips", "", "| | " + " | ".join(LABEL[rule] for rule in ARMS) + " |",
              "|---|" + "---|" * len(ARMS)]

    def cut(rule: str) -> list[dict]:
        return [r for r in rows if runs(r) and squash(r[rule]) != squash(r["given"])]

    lines += ["| replies stripped | " + " | ".join(str(len(cut(rule))) for rule in ARMS) + " |",
              "| ...of the " + str(len(did)) + " recorded | " + " | ".join(
                  str(sum(r["said_so"] for r in cut(rule))) for rule in ARMS) + " |",
              "| ...on a turn she was reacting to something told | " + " | ".join(
                  str(sum(r["act"] == REACT for r in cut(rule))) for rule in ARMS) + " |",
              "| replies a rule strips that the rule as it is does not | " + " | ".join(
                  str(sum(squash(r["now"]) == squash(r["given"]) for r in cut(rule))) for rule in ARMS) + " |", ""]
    lines.append(f"- Production leaves what `both` leaves on "
                 f"{sum(squash(r.get('production', r['both'])) == squash(r['both']) for r in rows)} of {len(rows)} "
                 "recorded replies.")
    for rule in ARMS[1:]:
        still = [r for r in did if squash(r[rule]) != squash(r["given"])]
        lines.append(f"- `{rule}` still strips {len(still)} of the recorded {len(did)}"
                     + (": " + "; ".join(f"{r['where']} ({r['said'][:50]!r})" for r in still) if still else "."))
    lines.append("")

    # What the reply goes on to become when the check leaves it alone.
    quiet = [r for r in rows if not r["changed"]]
    after = Counter(name for r in quiet for name in r["later"])
    lines += ["### After the check, when it changes nothing", "",
              f"On the {len(quiet)} recorded turns the check left alone, a later stage changed the reply on "
              f"{sum(bool(r['later']) for r in quiet)}: {dict(after)}. So what a rule leaves is, but for those, "
              "what the person is shown.", ""]

    lines += ["### Every recorded strip", ""]
    for i, r in enumerate(did, 1):
        gone = r["given"][: len(r["given"]) - len(r["now"])].strip() if r["given"].endswith(r["now"]) else "?"
        kept = [rule for rule in ARMS[1:] if squash(r[rule]) == squash(r["given"])]
        lines += [f"**{i}. {r['where']}** (act `{r['act']}`, {r['intent']}/{r['speech_act']})", "",
                  f"- said: {r['said'][:260]}", f"- taken off: {gone[:260]}",
                  f"- shown, as recorded: {r['display'][:330]}"
                  + (" (put back by `result_restored`)" if "result_restored" in r["later"] else ""),
                  f"- left whole by: {', '.join(kept) or 'neither rule'}",
                  f"- **shown with those: {r['given'][:420]}**", ""]
    return lines


# ------------------------------------------------------------- the tests

SITECUSTOMIZE = '''"""Eval-only (evals/echo_strip_replay.py): the final check's restatement strip,
swapped at its two calls as brain/chat_engine.py is loaded."""
import importlib.abc
import importlib.machinery
import os
import sys

ROOT = {root!r}
HELP = '__import__("evals.echo_strip_replay", fromlist=["x"])'
FIRST = """        without_echo = ResponseQualityGuard.strip_current_turn_echo(
            text, user_input, re_ended=told,
        )"""
FIRST_NEW = """        without_echo = %s.at_the_check(
            text, user_input, act=act,
        )""" % HELP
SECOND = """        fresh = ResponseQualityGuard.strip_current_turn_echo(
            fresh, user_input, re_ended=told,
        )"""
SECOND_NEW = """        fresh = %s.at_the_check(
            fresh, user_input, act=act,
        )""" % HELP


def _patched(source):
    if source.count(FIRST) != 1 or source.count(SECOND) != 1:
        raise ImportError("the restatement strip was not found where expected")
    return source.replace(FIRST, FIRST_NEW).replace(SECOND, SECOND_NEW)


class _Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        code = compile(_patched(self.get_source(fullname)), self.path, "exec", dont_inherit=True)
        log = os.environ.get("ELAINA_EVAL_ECHO_LOG", "")
        if log:
            with open(log, "a", encoding="utf-8") as handle:
                handle.write('{{"kind": "installed", "rule": "%s"}}\\n' % os.environ.get("ELAINA_EVAL_ECHO_RULE", ""))
        return code


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != "brain.chat_engine":
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            spec.loader = _Loader(spec.loader.name, spec.loader.path)
        return spec


if os.environ.get("ELAINA_EVAL_ECHO_RULE"):
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


def tests_run(suite: str = "all", only: tuple = ()) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for rule in only or (ARMS[1:] + ARMS[:1]):
        log, output = OUT / f"tests-{rule}.jsonl", OUT / f"tests-{rule}.log"
        log.unlink(missing_ok=True)
        started = time.monotonic()
        with open(output, "w", encoding="utf-8") as handle:
            code = subprocess.run([sys.executable, "tests/run_tests.py", suite], cwd=PROJECT_ROOT,
                                  env=hook_env(rule, log), stdout=handle, stderr=subprocess.STDOUT).returncode
        print(f"suite '{suite}' with the strip returning the rule '{rule}': exit {code}, "
              f"{time.monotonic() - started:.0f}s", flush=True)


def tests_report() -> list[str]:
    lines = ["## B. The existing tests with each rule at the check", ""]
    for rule in ARMS[1:] + ARMS[:1]:
        output, log = OUT / f"tests-{rule}.log", OUT / f"tests-{rule}.jsonl"
        if not output.exists():
            lines += [f"- `{rule}`: not run.", ""]
            continue
        text = output.read_text(encoding="utf-8", errors="replace")
        ran = re.findall(r"Ran (\d+) tests? in", text)
        summary = [line.strip() for line in text.splitlines()
                   if re.search(r"stages passed|^(?:OK|FAILED)\b|\[Turn trace\]", line.strip())]
        failed = [line.strip() for line in text.splitlines() if re.match(r"(?:FAIL|ERROR): ", line.strip())]
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        strips = [c for c in calls if c.get("kind") == "strip"]
        lines += [f"**`{rule}`** ({LABEL[rule]})", "",
                  f"- tests run: {', '.join(ran) or '?'}; " + " | ".join(summary[-3:]),
                  f"- replies in the run that at least one rule would strip: {len(strips)}; stripped "
                  + ", ".join(f"`{arm}` {sum(bool(c.get(arm)) for c in strips)}" for arm in ARMS
                              if any(arm in c for c in strips)) + "; by the act the check was handed: "
                  + str(dict(Counter(c["act"] for c in strips))) + "."]
        lines += [f"  - {line}" for line in failed[:12]]
        if rule == ARMS[-1]:
            for c in strips:
                if c["now"] and not all(c.get(arm) for arm in ARMS[1:]):
                    lines.append("  - as it is strips; " + ", ".join(
                        f"`{arm}` {'strips' if c.get(arm) else 'keeps'}" for arm in ARMS[1:])
                        + f" | act `{c['act']}` | said {c['said'][:60]!r} | reply {c['reply'][:90]!r}")
        lines.append("")
    return lines


# ------------------------------------------------------------ the router

# Things people tell her. "own": measured in this project (the tests of the
# echo guard, the docstrings beside it, the app's own traces). "added": written
# for this check. The kind says what the turn is besides a plain statement.
TOLD = [
    ("own", "plain", "회사에서 하루 종일 회의만 했어"),
    ("own", "plain", "I'm moving to Seattle next month"),
    ("own", "plain", "회의만 했어"),
    ("own", "plain", "오늘 좀 힘들었어"),
    ("own", "plain", "그건 봤어"),
    ("own", "plain", "just back to back meetings honestly"),
    ("own", "plain", "I'm moving to seattle"),
    ("own", "plain", "I'm feeling very full today, I just ate"),
    ("own", "plain", "아 말하고 있어"),
    ("added", "plain", "I got the job offer today"),
    ("added", "plain", "어제 새 노트북 샀어"),
    ("added", "plain", "I finally finished the report"),
    ("added", "plain", "오늘 비가 많이 오네"),
    ("added", "plain", "My flight got delayed by three hours"),
    ("added", "plain", "나 다음 달에 시애틀로 이사 가"),
    ("own", "a correction", "no I mean I'm going to UW"),
    ("own", "a correction", "아니 CPT 말고 OPT"),
    ("own", "a fact about them", "내 여동생은 부산에 살아"),
    ("own", "a fact about them", "my birthday is March 14th"),
    ("added", "a fact about them", "I've been learning Korean for two years"),
]
# Things people ask, each read with its question mark and without it.
ASKED = [
    ("own", "What is the freezing point of water in Fahrenheit?"),
    ("own", "Okay, so it doesn't mean it's a big difference, right?"),
    ("own", "Wait, so does that mean the effect is really big or important to people?"),
    ("own", "So it's like the Earth's shadow only comes into play during an eclipse?"),
    ("own", "what's the capital of France?"),
    ("own", "Who wrote Pride and Prejudice?"),
    ("own", "너가 뭘 준비해 줄 수 있는데?"),
    ("own", "너 내가 학교 되는 거 어떻게 알고 있어?"),
    ("added", "So a bigger group makes it less likely to be just luck, right?"),
    ("added", "그러니까 그게 크다는 뜻은 아니라는 거지?"),
    ("added", "물은 몇 도에서 끓어?"),
    ("added", "So an API isn't something I install, right?"),
]
# Said in the middle of something: one exchange before it.
BEFORE = ("What is the freezing point of water in Fahrenheit?", "The freezing point of water is 32 degrees Fahrenheit.")


class Bench:
    """The engine's own router on the real model, and the act the style layer gives its reading."""

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

    def read(self, said: str, history=()) -> dict:
        engine = self.engine
        engine._router_history.clear()
        for theirs, hers in history:
            engine._router_history.extend([{"role": "user", "content": theirs},
                                           {"role": "assistant", "content": hers}])
        engine._active_topic, engine._active_entity = "", ""
        with contextlib.redirect_stdout(io.StringIO()):
            decision = engine.intent_router.route(
                said, recent_turns=list(engine._router_history), has_screen_selection=False,
                project_tools_available=engine.project_mcp is not None,
                conversation_state=engine._build_conversation_state(), pending_action="",
                computer_control_enabled=False)
        return {"intent": decision.intent, "speech_act": decision.speech_act,
                "act": act_of(decision.intent, decision.speech_act, said)}


def act_of(intent: str, speech_act: str, said: str) -> str:
    """The style layer's act for a turn on which nothing was done and nothing is pending
    (brain/chat_engine.py, where ``turn_act`` is set)."""
    from brain import chat_engine, conversation_style, social_lines

    return conversation_style.act_for_turn(
        intent=intent, speech_act=speech_act, user_input=said,
        is_greeting=bool(chat_engine._SIMPLE_GREETING.fullmatch(said)),
        is_farewell=social_lines.reads_as_farewell(said))


def router_run() -> dict:
    rows = recorded()
    bench = Bench()
    out = {"model": bench.model, "told": [], "asked": []}
    # The instrument, with no model: the act worked out from each recorded route against the recorded act.
    sides = [(act_of(r["intent"], r["speech_act"], r["said"]) == REACT, r["act"] == REACT) for r in rows]
    out["instrument"] = {"turns": len(sides), "same_side": sum(a == b for a, b in sides),
                         "recorded_react": sum(b for _, b in sides), "worked_out_react": sum(a for a, _ in sides)}
    OUT.mkdir(parents=True, exist_ok=True)

    def note(group: str, entry: dict) -> None:
        out[group].append(entry)
        (OUT / "router.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{group}] {entry['said'][:50]!r} ({entry['context']}) -> {entry['reading']['intent']}/"
              f"{entry['reading']['speech_act']} = {entry['reading']['act']}", flush=True)

    for source, kind, said in TOLD:
        for context, history in (("alone", ()), ("after an exchange", (BEFORE,))):
            note("told", {"source": source, "kind": kind, "said": said, "context": context,
                          "reading": bench.read(said, history)})
    for source, said in ASKED:
        for mark, text in (("with ?", said), ("without ?", said.rstrip("?？"))):
            for context, history in (("alone", ()), ("after an exchange", (BEFORE,))):
                note("asked", {"source": source, "mark": mark, "said": text, "context": context,
                               "reading": bench.read(text, history)})
    return out


def router_report() -> list[str]:
    path = OUT / "router.json"
    if not path.exists():
        return ["## C. How the router and the style layer read tellings and askings", "", "Not run.", ""]
    out = json.loads(path.read_text(encoding="utf-8"))
    inst = out["instrument"]
    lines = ["## C. How the router and the style layer read tellings and askings", "",
             f"The engine's own router on `{out['model']}`, temperature 0, then the act the style layer gives the "
             "reading on a turn where nothing was done and nothing is pending. Each line was read alone and after "
             "one unrelated exchange.", "",
             f"- The instrument, with no model: the act worked out from each recorded route falls on the recorded "
             f"side of react / not react on {inst['same_side']} of {inst['turns']} turns "
             f"({inst['recorded_react']} recorded as react).", ""]

    told = out["told"]
    lines += ["### Things people tell her", "", "| Said | Kind | Read alone | After an exchange |", "|---|---|---|---|"]
    said_order = []
    for e in told:
        if e["said"] not in said_order:
            said_order.append(e["said"])
    for said in said_order:
        mine = {e["context"]: e for e in told if e["said"] == said}

        def cell(e: dict) -> str:
            r = e["reading"]
            return f"{r['intent']}/{r['speech_act']} = **{r['act']}**"

        first = next(iter(mine.values()))
        lines.append(f"| {said} ({first['source']}) | {first['kind']} | {cell(mine['alone'])} | "
                     f"{cell(mine['after an exchange'])} |")
    lines.append("")
    for kind in ("plain", "a correction", "a fact about them"):
        group = [e for e in told if e["kind"] == kind]
        lines.append(f"- {kind}: read as react in {sum(e['reading']['act'] == REACT for e in group)} of "
                     f"{len(group)} readings ({len({e['said'] for e in group})} lines).")
    lines.append("")

    asked = out["asked"]
    lines += ["### Things people ask", "", "| | read as not react |", "|---|---|"]
    for mark in ("with ?", "without ?"):
        group = [e for e in asked if e["mark"] == mark]
        lines.append(f"| {mark} | {sum(e['reading']['act'] != REACT for e in group)} of {len(group)} |")
    lines.append("")
    def reading_applies(rule: str, e: dict) -> bool:
        """Whether a mirrored first sentence would still be taken off this turn."""
        react, mark = e["reading"]["act"] == REACT, e["said"].strip().endswith(("?", "？"))
        return {"now": True, "react_only": react, "not_a_question": not mark, "both": react and not mark}[rule]

    lines += ["What that means for a first sentence that mirrors what was said:", "",
              "| | " + " | ".join(LABEL[rule] for rule in ARMS) + " |", "|---|" + "---|" * len(ARMS),
              "| told her something: still stripped | " + " | ".join(
                  f"{sum(reading_applies(rule, e) for e in told)} of {len(told)}" for rule in ARMS) + " |"]
    for mark in ("with ?", "without ?"):
        group = [e for e in asked if e["mark"] == mark]
        lines.append(f"| asked, {mark}: left alone | " + " | ".join(
            f"{sum(not reading_applies(rule, e) for e in group)} of {len(group)}" for rule in ARMS) + " |")
    lines.append("")
    for e in asked:
        if e["reading"]["act"] == REACT:
            lines.append(f"- read as react: {e['said']!r} ({e['mark']}, {e['context']}): "
                         f"{e['reading']['intent']}/{e['reading']['speech_act']}")
    lines.append("")
    return lines


def report() -> str:
    path = OUT / "replay.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else replay()
    lines = ["# The final check's restatement strip: the rule as it is against two narrower ones", "",
             "Written by `evals/echo_strip_replay.py`. Production is not changed by anything here.", ""]
    lines += replay_report(result) + tests_report() + router_report()
    text = "\n".join(lines) + "\n"
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if "--replay" in args:
        print("\n".join(replay_report(replay())))
    if "--tests" in args:
        tests_run(only=tuple(a.split("=", 1)[1] for a in args if a.startswith("--arm=")))
    if "--router" in args:
        router_run()
    if "--report" in args or not args:
        text = report()
        print(f"written: {OUT / 'REPORT.md'} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
