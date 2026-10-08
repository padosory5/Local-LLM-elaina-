"""The "sources disagree" line, replayed: the rule as it is against a narrower one.

    .venv/Scripts/python.exe -m evals.disagreement_replay
    .venv/Scripts/python.exe -m evals.disagreement_replay --live d1
    .venv/Scripts/python.exe -m evals.disagreement_replay --live-report d1

The ``grounded_values`` stage ends a reply with "Sources disagree on that one
-- another says X" when ``attribute_values.conflicting_claims`` finds that the
sources give two values for something the reply states. Reviewed over every
recorded turn (2026-10-07): 33 such lines, and in 28 of them X is in no
source. Two causes, both in how values are compared:

* any two different values of one kind anywhere in the evidence count as a
  disagreement, so a page listing 256GB, 512GB, 1TB and 2TB disagrees with
  itself four ways;
* the other value is written by putting its number into the reply's wording,
  so a source's 2TB is said as "2GB".

Every recorded turn that searched is put through the stage again, on an
engine with its hands tied (tests/turn_harness.py), from what the trace holds:
the reply as it reached the stage, the turn's evidence, what was asked, what
she had said the turn before. Twice -- with the rule as it was (``before``),
and with the rule the replay was run to test:

    a disagreement needs the same unit, and two values one source lists
    together are options, not a conflict.

That rule was ``candidate`` here, swapped in in memory, when the replay was
first run; it is ``attribute_values.conflicting_claims`` itself since
2026-10-07, and ``candidate`` is kept as the record of what was tested (the
report says whether the two still agree on every turn).

The first pass is also the check on the replay itself: it has to give back
what the trace recorded.

``--live`` asks the real backend the price question the line was recorded on,
flag off, a fresh backend each time and the rules turn about. The rule is
swapped inside the backend process by a ``sitecustomize`` module this script
writes under ``runtime/`` and names on that process's PYTHONPATH -- production
is not changed for it -- which also writes down every check the stage made
and what each rule found. ``--rules production`` swaps nothing. Each live turn is then put through the stage
again offline under both rules, so the two are compared on the same reply and
the same evidence as well as across arms.
"""

from __future__ import annotations

import contextlib
import glob
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import attribute_values as av  # noqa: E402
from brain import evidence as turn_evidence  # noqa: E402
from brain import guard_lines  # noqa: E402

ROOT = PROJECT_ROOT / "runtime" / "evals"
OUT = ROOT / "disagreement"
CUT = re.compile(r"\.\.\.\[\d+ chars cut\]\.\.\.")
FAMILIES = ("sl", "tmr", "reask", "search_order")
STAGE = "grounded_values"

# Spellings of one unit. Everything else in attribute_values' tables is a
# unit of its own: GB is not TB, and Hz is not kHz.
SAME_UNIT = {
    "watt": "w", "watts": "w",
    "megapixel": "mp", "megapixels": "mp",
    "cd/m2": "nits",
    "hours": "hour", "hr": "hour", "hrs": "hour",
    "minutes": "minute", "min": "minute", "mins": "minute",
    "kgs": "kg", "grams": "g", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "inches": "inch", "in": "inch", "metre": "m", "metres": "m",
    "percent": "%", "mile": "mi", "miles": "mi", "litre": "l", "litres": "l",
}


def unit_of(claim) -> str:
    unit = str(claim.unit or "").casefold()
    return SAME_UNIT.get(unit, unit)


def before(reply: str, evidence: str) -> tuple:
    """``attribute_values.conflicting_claims`` as it was until 2026-10-07."""
    disputed = av.conflicts(evidence)
    if not disputed:
        return ()
    noted = []
    for claim in av.claims(reply):
        values = disputed.get(claim.kind)
        if not values or claim.value not in values:
            continue
        others = sorted(values - {claim.value})
        if others:
            noted.append((claim, claim.text.replace(claim.value, others[0], 1)))
    return tuple(noted)


def candidate(reply: str, evidence: str) -> tuple:
    """``attribute_values.conflicting_claims``, narrower in two ways.

    A value is the number *and* its unit, so 1TB is not another reading of
    256GB. And an other value that some source lists beside hers is an
    option of the same thing -- a second storage size, a second camera --
    not another source's account of it.
    """
    found = av.sources(evidence)
    if len(found) <= 1:
        return ()
    stated: list[dict] = []
    for source in found:
        values: dict = {}
        for claim in av.claims(source, spoken=False):
            values.setdefault((claim.kind, unit_of(claim)), set()).add(claim.value)
        stated.append(values)
    noted = []
    for claim in av.claims(reply):
        key = (claim.kind, unit_of(claim))
        per_source = [values.get(key, set()) for values in stated]
        if not any(claim.value in values for values in per_source):
            continue
        others = set().union(*per_source) - {claim.value}
        others = {other for other in others
                  if not any(claim.value in values and other in values for values in per_source)}
        if others:
            noted.append((claim, claim.text.replace(claim.value, sorted(others)[0], 1)))
    return tuple(noted)


# ----------------------------------------------------------- the turns


def recorded_turns(under: Path | None = None) -> list[dict]:
    """Every recorded turn that searched, with what the stage was given.

    ``under``: one run's folder, instead of every run of the families above.
    """
    rows = []
    for path in sorted(glob.glob(str((under or ROOT) / "**" / "turn_trace" / "*.jsonl"), recursive=True)):
        parts = Path(path).relative_to(ROOT).parts
        if under is None and parts[0] not in FAMILIES:
            continue
        previous, said_before = "", []
        researches: list[str] = []
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            turn = json.loads(line)
            evidence = turn.get("evidence") or {}
            research = str(evidence.get("research") or "")
            context = turn.get("context") or {}
            display = str(turn.get("display") or "")
            if research:
                text = str((turn.get("draft") or {}).get("text") or "")
                given, left, ran, later = None, None, 0, []
                for step in sorted(turn.get("steps") or (), key=lambda s: s.get("i", 0)):
                    if step.get("name") == STAGE:
                        ran += 1
                        if given is None:
                            given = str(step.get("before")) if step.get("changed") else text
                            left = str(step.get("after")) if step.get("changed") else text
                    elif given is not None and step.get("changed"):
                        later.append(str(step.get("name")))
                    if step.get("changed"):
                        text = str(step.get("after") or "")
                ledger, clipped = [], 0
                for item in evidence.get("ledger") or ():
                    body = str(item.get("text") or "")
                    # The trace keeps the head and tail of each item (2,000
                    # characters, core/turn_trace.py). The whole of every
                    # search is kept beside it: this turn's, or an earlier
                    # one's when the item was carried.
                    if CUT.search(body) or len(body) >= turn_evidence._TRACE_TEXT_LIMIT:
                        whole = next((text for text in [research, *reversed(researches)]
                                      if text[:150] == body[:150]), "")
                        if whole:
                            body = whole
                        else:
                            clipped += 1
                    ledger.append({**item, "text": body})
                if not ledger:
                    ledger = [{"kind": turn_evidence.SEARCH, "text": research, "source": "", "carried": False}]
                rows.append({
                    "where": f"{parts[0]}/{parts[1]}/{parts[-3]} t{number}", "family": parts[0],
                    "said": str(context.get("user_input_resolved") or turn.get("user_input") or ""),
                    "language": str(context.get("language") or "en"),
                    "given": given, "left": left, "ran": ran, "display": display, "ledger": ledger,
                    "previous": previous, "said_before": tuple(said_before),
                    "action_performed": bool(context.get("action_performed")),
                    "trusted": bool(str(context.get("tool_result") or "").strip()),
                    "searched": "web_search" in (turn.get("timings") or {}),
                    "clipped": clipped, "later": tuple(later),
                })
                researches.append(research)
            if display.strip():
                said_before.append(display)
                previous = display
    return rows


# ------------------------------------------------------------ the stage


OFFER_STAGE = "redundant_permission"


class Stage:
    """The stage itself, on an engine that can do nothing else."""

    def __init__(self) -> None:
        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        # As the recorded backends ran: Desktop Control Mode off.
        self.engine.computer_control_mode.set_enabled(False)
        self.checks: list[tuple[str, str]] = []

    def run(self, row: dict, rule) -> str:
        """What the stage leaves of ``row['given']`` under ``rule``."""
        engine = self.engine
        ledger = turn_evidence.EvidenceLedger()
        for item in row["ledger"]:
            ledger._items.append(turn_evidence.Evidence(
                kind=item.get("kind") or turn_evidence.SEARCH, text=str(item.get("text") or "").strip(),
                source=str(item.get("source") or ""), carried=bool(item.get("carried"))))
        engine._turn_evidence = ledger
        engine._turn_language = row["language"]
        engine._things_she_has_said = lambda: (row["previous"], row["said_before"][:-1])
        engine.capability_offer.clear()
        seen = self.checks

        def watched(reply, evidence):
            seen.append((reply, evidence))
            return rule(reply, evidence)

        original = av.conflicting_claims
        av.conflicting_claims = watched
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return engine._enforce_grounded_values(
                    row["given"], user_input=row["said"], action_performed=row["action_performed"],
                    trusted_result=row["trusted"], searched=row["searched"])
        finally:
            av.conflicting_claims = original


    def offer_stage(self, text: str, row: dict) -> str:
        """What ``redundant_permission``, further down the pipeline, leaves of
        ``text`` on a turn that searched: it takes a closing offer off the
        end, and can only reach one that *is* the end."""
        from types import SimpleNamespace

        self.engine.capability_offer.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._refuse_redundant_permission(
                text, decision=SimpleNamespace(acts=True), route=SimpleNamespace(action_requested=False),
                action_performed=row["action_performed"])


def line_openings() -> tuple:
    return tuple(guard_lines.say("sources_disagree", language).split("{other}")[0].strip()
                 for language in av.LANGUAGES)


def says_disagree(text: str) -> str:
    """The other value the line names, or ''."""
    # As the screen shows it, the line's "--" is a dash (brain/realize.py).
    text = str(text or "").replace(" \u2014 ", " -- ")
    for opening in line_openings():
        at = str(text or "").find(opening)
        if at >= 0:
            tail = text[at + len(opening):]
            return re.split(r"[.。]|\(으\)로", tail.strip(), maxsplit=1)[0].strip(" :-—")
    return ""


def squash(text: str) -> str:
    return re.sub(r"[\s,]", "", str(text or "")).casefold()


def replay() -> dict:
    rows = [row for row in recorded_turns() if row["given"] is not None]
    skipped = len(recorded_turns()) - len(rows)
    stage = Stage()
    for row in rows:
        stage.checks.clear()
        row["now"] = stage.run(row, before)
        row["checked"] = list(stage.checks)
        row["then"] = stage.run(row, av.conflicting_claims)
        row["offer_now"] = stage.offer_stage(row["now"], row)
        row["offer_then"] = stage.offer_stage(row["then"], row)
        row["evidence"] = row["checked"][0][1] if row["checked"] else ""
        row["found_now"] = before(row["given"], row["evidence"]) if row["checked"] else ()
        row["found_then"] = av.conflicting_claims(row["given"], row["evidence"]) if row["checked"] else ()
        row["as_tested"] = (not row["checked"]) or (
            [(c.text, o) for c, o in candidate(row["given"], row["evidence"])]
            == [(c.text, o) for c, o in row["found_then"]])
    with contextlib.redirect_stdout(io.StringIO()):
        stage.engine.close()
    return {"rows": rows, "skipped": skipped}


def report() -> str:
    result = replay()
    rows = result["rows"]
    faithful = [r for r in rows if r["now"] == r["left"]]
    lines = ["# The \"sources disagree\" line, replayed", "",
             f"{len(rows)} recorded turns that searched, each put through the `grounded_values` stage again "
             f"from its trace ({result['skipped']} more searched and never reached the stage).", "",
             "## The replay against the record", "",
             f"With the rule as it was, the stage gives back what the trace recorded in {len(faithful)} of "
             f"{len(rows)} turns.", "",
             f"The rule in production and the rule that was tested find the same thing in "
             f"{sum(r['as_tested'] for r in rows)} of {len(rows)} turns.", ""]
    lines += [f"Evidence items the trace had clipped and no recorded search could make whole: "
              f"{sum(r['clipped'] for r in rows)}, in {sum(bool(r['clipped']) for r in rows)} turns.", ""]
    unfaithful = [r for r in rows if r["now"] != r["left"]]
    for r in unfaithful:
        lines += [f"- **{r['where']}** — recorded: {' '.join(r['left'].split())[-150:]!r}; "
                  f"replayed: {' '.join(r['now'].split())[-150:]!r}"]
    said_now = [r for r in rows if says_disagree(r["now"]) and not says_disagree(r["given"])]
    said_then = [r for r in rows if says_disagree(r["then"]) and not says_disagree(r["given"])]
    recorded = [r for r in rows if says_disagree(r["left"]) and not says_disagree(r["given"])]

    def absent(r, text):
        return squash(says_disagree(text)) not in squash(r["evidence"])

    lines += ["", "## The line", "",
              "| | recorded | rule as it was | rule now |", "|---|---|---|---|",
              f"| replies given the line | {len(recorded)} | {len(said_now)} | {len(said_then)} |",
              f"| ...naming a value that is in no source as said | {sum(absent(r, r['left']) for r in recorded)} | "
              f"{sum(absent(r, r['now']) for r in said_now)} | {sum(absent(r, r['then']) for r in said_then)} |",
              f"| ...naming a value some source does state | {sum(not absent(r, r['left']) for r in recorded)} | "
              f"{sum(not absent(r, r['now']) for r in said_now)} | {sum(not absent(r, r['then']) for r in said_then)} |",
              "", "The other values named, rule as it was: "
              + ", ".join(f"{value} ×{count}" for value, count in
                          Counter(says_disagree(r["now"]) for r in said_now).most_common()) + ".", ""]
    found_now = [r for r in rows if r["found_now"]]
    found_then = [r for r in rows if r["found_then"]]
    lines += [f"The check alone, on what the stage handed it: a disagreement found in {len(found_now)} turns as "
              f"it was, {len(found_then)} now. (It is not said when she said it the turn before.)", ""]
    changed = [r for r in rows if r["then"] != r["now"]]
    only_line, other = [], []
    for r in changed:
        # The line exactly as the stage words it, wherever in the reply it sits.
        r["line"] = (guard_lines.say("sources_disagree", r["language"]).format(other=r["found_now"][0][1])
                     if r["found_now"] else "")
        without = " ".join(r["now"].replace(r["line"], " ").split()) if r["line"] and r["line"] in r["now"] else None
        (only_line if without is not None and " ".join(r["then"].split()) == without else other).append(r)
    lines += ["## Every reply that changes", "",
              f"{len(changed)} replies change. In {len(only_line)} the only difference is that the line is gone; "
              f"in {len(other)} something else differs.", ""]
    def written(r):
        found = r["found_now"][0] if r["found_now"] else None
        return sorted({f"{c.value}{c.unit}" for source in av.sources(r["evidence"])
                       for c in av.claims(source, spoken=False) if found and c.kind == found[0].kind})

    if other:
        lines += ["### Something else differs", ""]
        for r in other:
            lines += [f"**{r['where']}** — asked: {r['said'][:100]}",
                      f"- as it was: {' '.join(r['now'].split())}",
                      f"- now: {' '.join(r['then'].split())}", ""]
    lines += ["### Only the line is gone", "",
              "| # | turn | asked | she said | that kind, in the evidence | the line taken out |",
              "|---|---|---|---|---|---|"]
    for number, r in enumerate(only_line, 1):
        lines.append(f"| {number} | {r['where']} | {r['said'][:44]} | {r['found_now'][0][0].text} | "
                     f"{', '.join(written(r))} | {r['line']} |")
    lines += ["", "The replies in full, as the stage left them and as it leaves them now:", ""]
    for number, r in enumerate(only_line, 1):
        lines += [f"{number}. **{r['where']}**", f"   - as it was: {' '.join(r['now'].split())}",
                  f"   - now: {' '.join(r['then'].split())}"]
    lines.append("")
    # Further down the pipeline. The line goes on the end of the reply, and the
    # stage that takes a closing offer off the end cannot then reach it.
    agree = sum((r["offer_now"] != r["now"]) == (OFFER_STAGE in r["later"]) for r in rows)
    freed = [r for r in changed if r["offer_then"] != r["then"] and r["offer_now"] == r["now"]]
    lines += ["## What a later stage then does", "",
              f"`{OFFER_STAGE}` runs after this stage and takes a closing offer off the end of a reply. It can "
              "only reach one that is the end, and the line was being put after it.", "",
              f"- Run on its own on each replayed reply, it agrees with what the trace recorded of it in {agree} "
              f"of {len(rows)} turns.",
              f"- Of the {len(changed)} replies that change, it would then also remove a closing sentence in "
              f"{len(freed)}, which it could not reach before.", ""]
    for r in freed:
        gone = r["then"][len(r["offer_then"].rstrip()):].strip() if r["then"].startswith(r["offer_then"].rstrip()) else "(reworded)"
        lines.append(f"  - {r['where']}: {gone}")
    lines += ["", "## Disagreements still reported", ""]
    if said_then:
        for r in said_then:
            claim, named = r["found_then"][0]
            lines += [f"**{r['where']}** — asked: {r['said'][:100]}",
                      f"- she said {claim.text!r}; the line names {named!r}",
                      f"- reply: {' '.join(r['then'].split())}",
                      "- what each source states of that kind:"]
            for index, source in enumerate(av.sources(r["evidence"]), 1):
                stated = sorted({f"{c.value}{c.unit}" for c in av.claims(source, spoken=False) if c.kind == claim.kind})
                if stated:
                    lines.append(f"  - source {index} ({source.splitlines()[0][:60]}): {', '.join(stated)}")
            lines.append("")
    else:
        lines.append("None in the recorded turns.")
    lines += ["", "## Where the rule now reports one and the rule as it was did not", ""]
    new = [r for r in rows if r["found_then"] and not r["found_now"]]
    lines.append("None." if not new else "\n".join(f"- {r['where']}" for r in new))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- live

QUESTION = "How much does the iPhone 18 Pro cost?"
PRICE = re.compile(r"\$\s?1,?199\b")
LIVE_TURNS = 10
RULES = ("before", "production")
# What each arm of a live run put where the stage looks for its check. "as_it_is"
# is how the run of 2026-10-07 named production, which was then the rule before.
RULE_OF = {"as_it_is": before, "before": before, "candidate": candidate, "production": av.conflicting_claims}
ARM_NAMES = {"as_it_is": "rule as it was", "before": "rule as it was", "candidate": "candidate",
             "production": "production now"}
RULE_ENV = "ELAINA_EVAL_DISAGREEMENT_RULE"
LOG_ENV = "ELAINA_EVAL_DISAGREEMENT_LOG"
FILE_ENV = "ELAINA_EVAL_DISAGREEMENT_FILE"

SITECUSTOMIZE = '''"""Eval only: written by evals/disagreement_replay.py, loaded through PYTHONPATH.

Once the engine is imported, puts the named rule where the grounded_values
stage looks for its disagreement check, and writes down each check: what the
rule in use found, and what the rule as it is would have. Production never
names this folder.
"""
import json
import os
import sys
import threading
import time

RULE_ENV = "ELAINA_EVAL_DISAGREEMENT_RULE"
LOG_ENV = "ELAINA_EVAL_DISAGREEMENT_LOG"
FILE_ENV = "ELAINA_EVAL_DISAGREEMENT_FILE"


def _keep(record):
    path = os.environ.get(LOG_ENV, "")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\\n")


def _install():
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        engine = sys.modules.get("brain.chat_engine")
        values = sys.modules.get("brain.attribute_values")
        if values is not None and engine is not None and hasattr(engine, "ChatEngine"):
            break
        time.sleep(0.05)
    else:
        _keep({"kind": "not installed"})
        return
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("disagreement_candidate", os.environ[FILE_ENV])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        name = os.environ.get(RULE_ENV, "")
        # "production" and "as_it_is" swap nothing; the others put a rule in its place.
        rule = {"candidate": module.candidate, "before": module.before}.get(name, values.conflicting_claims)

        def watched(reply, evidence):
            found = rule(reply, evidence)
            _keep({"kind": "check", "rule": name, "at": time.time(),
                   "found": [[claim.text, other] for claim, other in found],
                   "before": [[claim.text, other] for claim, other in module.before(reply, evidence)]})
            return found

        values.conflicting_claims = watched
        _keep({"kind": "installed", "rule": name, "at": time.time()})
    except Exception as failure:  # an eval hook never takes a process down
        _keep({"kind": "not installed", "error": f"{type(failure).__name__}: {failure}"})


if os.environ.get(RULE_ENV):
    threading.Thread(target=_install, daemon=True).start()
'''


def live(name: str, rules: tuple = (), turns: int = 0) -> int:
    import os
    import subprocess
    import time
    from datetime import datetime

    os.environ.setdefault("ELAINA_TTS", "off")
    from evals import run as runner
    from evals import sim_learner as sl
    from evals.phase2 import unload_models

    if runner.port_open():
        print("Port 8765 is in use -- is Elaina already running? Close it first.")
        return 1
    out = OUT / name
    patch = out / "patch"
    patch.mkdir(parents=True, exist_ok=True)
    (patch / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    (out / "logs").mkdir(exist_ok=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()
    (out / "meta.json").write_text(json.dumps({
        "arm": "current", "env": sl.ARMS["current"], "question": QUESTION, "git_head": head, "rules": list(rules or RULES),
        "uncommitted": [line[3:] for line in dirty],
        "started_at": datetime.now().isoformat(timespec="seconds")}, indent=1, ensure_ascii=False), encoding="utf-8")
    path = out / "dialogues.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    rules = tuple(rules) or RULES
    plan = [(f"{rule}-{number}", rule) for number in range(1, (turns or LIVE_TURNS) + 1)
            for rule in (rules if number % 2 else rules[::-1])]
    unload_models()
    python_path = os.pathsep.join(filter(None, [str(patch), os.environ.get("PYTHONPATH", "")]))
    for number, (ident, rule) in enumerate(plan, 1):
        if ident in done:
            continue
        env = {**sl.ARMS["current"], "PYTHONPATH": python_path, RULE_ENV: rule,
               LOG_ENV: str(out / "logs" / f"{ident}.rule.jsonl"), FILE_ENV: str(Path(__file__).resolve())}
        backend = sl.Elaina(out / "rt" / ident, out / "logs" / f"{ident}.log", env)
        started = time.monotonic()
        try:
            reply, seconds = backend.reply(QUESTION)
        finally:
            backend.close()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": ident, "rule": rule, "reply": reply, "seconds": seconds},
                                    ensure_ascii=False) + "\n")
        print(f"[{number}/{len(plan)}] {ident}: {time.monotonic() - started:.0f}s", flush=True)
    return 0


def live_report(name: str) -> str:
    out = OUT / name
    rows = {Path(r["where"].split("/")[-1].split(" ")[0]).name: r for r in recorded_turns(out)}
    stage = Stage()
    turns = []
    for line in (out / "dialogues.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        turn = json.loads(line)
        row = rows.get(turn["id"])
        log_path = out / "logs" / f"{turn['id']}.rule.jsonl"
        log = [json.loads(entry) for entry in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
        turn["installed"] = any(e["kind"] == "installed" and e.get("rule") == turn["rule"] for e in log)
        turn["checks"] = [e for e in log if e["kind"] == "check"]
        turn["row"] = row
        if row is not None and row["given"] is not None:
            turn["now"] = stage.run(row, before)
            turn["then"] = stage.run(row, av.conflicting_claims)
            turn["replayed"] = stage.run(row, RULE_OF[turn["rule"]]) == row["left"]
        turns.append(turn)
    with contextlib.redirect_stdout(io.StringIO()):
        stage.engine.close()
    arms = tuple(dict.fromkeys(t["rule"] for t in turns))
    lines = [f"# The \"sources disagree\" line, live: {name}", "",
             f"`{QUESTION}` asked of the real backend, flag off, a fresh backend each time, the rules turn "
             f"about. {len(turns)} conversations.", "",
             "| | " + " | ".join(ARM_NAMES[arm] for arm in arms) + " |", "|---|" + "---|" * len(arms)]

    def cell(rule, test):
        group = [t for t in turns if t["rule"] == rule]
        return f"{sum(bool(test(t)) for t in group)} of {len(group)}"

    def both(label, test):
        lines.append(f"| {label} | " + " | ".join(cell(arm, test) for arm in arms) + " |")

    both("the rule in use was the arm's, and it was asked", lambda t: t["installed"] and t["checks"]
         and all(c["rule"] == t["rule"] for c in t["checks"]))
    both("searched", lambda t: t["row"] is not None)
    both("reply carries the line", lambda t: says_disagree(t["reply"]))
    both("reply states the price ($1,199)", lambda t: PRICE.search(t["reply"]))
    both("the stage changed the reply", lambda t: t["row"] is not None and t["row"]["given"] != t["row"]["left"])
    both(f"a later stage then took a closing offer off the end ({OFFER_STAGE})",
         lambda t: t["row"] is not None and OFFER_STAGE in t["row"]["later"])
    both("reply ends with an offer to help", lambda t: re.search(r"(?i)(let me know if|would you like|want me to)[^.?!]*[.?!]\s*$", t["reply"]))
    both("...in some way other than adding the line",
         lambda t: t["row"] is not None and t["row"]["given"] != t["row"]["left"]
         and not (says_disagree(t["row"]["left"]) and t["row"]["left"].startswith(t["row"]["given"].rstrip())))
    both("the stage, replayed offline under the arm's rule, gives the live reply", lambda t: t.get("replayed"))
    paired = [t for t in turns if "now" in t]
    lines += ["", "The same replies and evidence under each rule (every live turn, replayed offline):", "",
              "| | rule as it was | rule now |", "|---|---|---|",
              f"| the line is added | {sum(bool(says_disagree(t['now'])) for t in paired)} of {len(paired)} | "
              f"{sum(bool(says_disagree(t['then'])) for t in paired)} of {len(paired)} |",
              f"| the reply differs in anything but the line | | "
              f"{sum(' '.join(t['then'].split()) != ' '.join(re.sub(_line_pattern(), ' ', t['now']).split()) for t in paired)}"
              f" of {len(paired)} |", "", "## Every reply", ""]
    for rule in arms:
        lines.append(f"**{ARM_NAMES[rule]}**")
        for t in [x for x in turns if x["rule"] == rule]:
            last = t["checks"][-1] if t["checks"] else {}
            found = last.get("before", last.get("as_it_is")) or []
            note = f" [the rule as it was finds: {found[0][0]} against {found[0][1]}]" if found else ""
            lines.append(f"- {t['id']}: {' '.join(t['reply'].split())}{note}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _line_pattern() -> str:
    """The line in either language, whatever value it names."""
    parts = []
    for language in av.LANGUAGES:
        before, _, after = guard_lines.say("sources_disagree", language).partition("{other}")
        parts.append(re.escape(before) + r".*?" + re.escape(after))
    return "|".join(parts)


def main() -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", default="")
    parser.add_argument("--live-report", default="")
    parser.add_argument("--rules", default="", help="comma-separated arms of a live run: " + ", ".join(RULE_OF))
    parser.add_argument("--turns", type=int, default=0, help="live conversations per arm")
    args = parser.parse_args()
    if args.live:
        return live(args.live, tuple(r for r in args.rules.split(",") if r), args.turns)
    if args.live_report:
        text = live_report(args.live_report)
        (OUT / args.live_report / "LIVE.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    text = report()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
