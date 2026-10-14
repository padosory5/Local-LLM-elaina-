"""Everyday English: can Elaina hold an ordinary conversation correctly and fluently?

    .venv/Scripts/python.exe -m evals.sim_everyday --run ev1 --seed 1
    .venv/Scripts/python.exe -m evals.sim_everyday --judge ev1
    .venv/Scripts/python.exe -m evals.sim_everyday --report ev1
    .venv/Scripts/python.exe -m evals.sim_everyday --export-calibration ev1 --count 6

The simulated learners (evals/sim_learner.py) measure one thing she does:
explain a concept until it lands. The user asked for "at least one language
correct and fluent" (2026-10-13), and most of what a person says to her is
not a request to be taught -- dinner, a bad day, a trip, three quick
questions in a row, a bit of small talk.

* **The person** (evals/everyday/people_v0.json) wants something ordinary,
  mentions facts about themselves when they matter, and brings up one twist:
  a change of plan, a correction, or a wish about how she should respond
  ("I don't want advice, I just needed to vent"). A different model plays
  them and reacts to what she actually says. Elaina -- the real backend over
  its websocket, one fresh backend per conversation, speech off -- is told
  nothing about them.
* **The judge** (the rubric judge's model) reads the finished transcript with
  the person's brief and tags every reply: did it answer what was just said,
  did it state anything wrong or get the person's own facts wrong, did it use
  what they said earlier when that mattered, was it the right length for
  speech, did anything get tacked on (an offer, an unneeded question, filler,
  an unrelated sentence), and did it sound like a person. Then whether each
  thing the person wanted was given.
* **The goodbye counts.** The person's last line is said to her and her reply
  is judged like any other: a goodbye that carries an offer is a defect.
* **Calibration**: transcripts for the user to rate. Until a person agrees
  with the judge on a sample, the numbers are provisional.

The objective is the conversation's: the person got what they came for, and
every reply along the way was right and sounded like her.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Silent: every conversation starts a backend, and a backend that spoke would
# spend the paid voice on every turn. evals/run.py forces it off again.
os.environ.setdefault("ELAINA_TTS", "off")

from evals.judge import DEFAULT_JUDGE  # noqa: E402
from evals.phase2 import M27  # noqa: E402
from evals.sim_learner import ARMS, Elaina, _pair  # noqa: E402

PROFILES = PROJECT_ROOT / "evals" / "everyday" / "people_v0.json"
ROOT = PROJECT_ROOT / "runtime" / "evals" / "everyday"
DONE = ("continue", "satisfied", "gave_up")
SIMULATOR = "v1"
# A reply this close to an earlier one in the same conversation is a repeat
# (the measure the simulated-learner reports use).
REPEAT_LIKENESS = 0.6
ANSWERED = ("yes", "partly", "no")
LENGTHS = ("right", "too long", "too short")
EXTRAS = ("none", "offer", "question", "filler", "unrelated")
REMEMBERED = ("yes", "no", "n/a")


# ------------------------------------------------------------- people


@dataclass(frozen=True)
class Person:
    id: str
    language: str
    situation: str
    opening: str
    persona: str
    facts: tuple
    twist: str
    wants: tuple            # ((id, need), ...)

    @property
    def want_ids(self) -> tuple:
        return tuple(want_id for want_id, _ in self.wants)


@dataclass(frozen=True)
class Settings:
    max_turns: int = 6
    patience: int = 3


def load_people(path: Path = PROFILES) -> tuple[list[Person], Settings]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    people, seen = [], set()
    for raw in data["people"]:
        if raw["id"] in seen:
            raise ValueError(f"duplicate person {raw['id']}")
        seen.add(raw["id"])
        wants = tuple((w["id"], w["need"]) for w in raw["wants"])
        if not wants or len({w for w, _ in wants}) != len(wants):
            raise ValueError(f"{raw['id']}: wants must be present and unique")
        if raw["language"] != "en":
            raise ValueError(f"{raw['id']}: this suite is English only")
        for key in ("situation", "opening", "persona", "twist"):
            if not str(raw.get(key) or "").strip():
                raise ValueError(f"{raw['id']}: {key} is empty")
        people.append(Person(
            id=raw["id"], language=raw["language"], situation=raw["situation"], opening=raw["opening"],
            persona=raw["persona"], facts=tuple(raw.get("facts") or ()), twist=raw["twist"], wants=wants))
    return people, Settings(max_turns=int(data.get("max_turns", 6)), patience=int(data.get("patience", 3)))


PERSON_PROMPT = """You are playing a real person talking out loud with a voice assistant named Elaina. Stay in character. Never say or hint that you are simulated, and never show this brief.

WHO YOU ARE
{persona}
Right now: {situation}
About you (Elaina knows none of this until you say it): {facts}.

WHAT YOU WANT FROM THIS CONVERSATION (private; never list it to Elaina)
{wants}

ONE THING THAT WILL COME UP
{twist}

HOW YOU TALK
- Like a real person: short and casual, one thing at a time, reacting to what she just said.
- Mention a fact about yourself only when it matters for what you are talking about, the way people do, not all at once.
- If she gets something about you wrong, ignores something you told her, or answers something you did not ask, say so briefly.
- Do not explain things to her or do her job for her. Do not recap what she said.
- When you have what you wanted, say thanks in a few words, like "great, thanks!" If she keeps missing what you want for several replies, you may give up, in a few words.

After Elaina's latest reply, return one JSON object and nothing else:
{{"say": "what you say next", "got": ["ids of the things you wanted that you now have, earlier ones included"], "bothered_by": "anything in her latest reply that was wrong, odd, off-topic or annoying, or empty", "done": "continue" or "satisfied" or "gave_up"}}"""


def person_messages(person: Person, transcript: list[dict]) -> list[dict]:
    system = PERSON_PROMPT.format(
        persona=person.persona, situation=person.situation,
        facts="; ".join(person.facts) or "nothing in particular",
        wants="\n".join(f"- {i}: {need}" for i, need in person.wants), twist=person.twist)
    lines = []
    for turn in transcript:
        lines += [f"You: {turn['person']}", f"Elaina: {turn['elaina']}"]
    lines.append("That was Elaina's latest reply. Your turn.")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_person(text: str, person: Person, before: frozenset) -> dict | None:
    """The person's turn, or None when it cannot be read. What they got only
    accumulates: a thing once given stays given."""
    try:
        data = json.loads(str(text or "").strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("say") or "").strip():
        return None
    claimed = data.get("got") or []
    if not isinstance(claimed, list):
        claimed = []
    got = frozenset(before | {str(i) for i in claimed if str(i) in person.want_ids})
    done = str(data.get("done") or "continue").strip().lower()
    if done not in DONE:
        done = "continue"
    return {"say": str(data["say"]).strip(), "got": got,
            "bothered_by": str(data.get("bothered_by") or "").strip(), "done": done}


# ------------------------------------------------------------ the loop


def converse(person: Person, settings: Settings, reply_fn, person_fn) -> dict:
    """One conversation. ``reply_fn(said) -> (reply, seconds)`` is Elaina;
    ``person_fn(person, transcript, got) -> dict | None`` the person. Ends when
    the person is satisfied, gives up, stops making sense, or the turn limit is
    reached. Their last line is said to her and her answer is a reply like any
    other, marked ``closing``."""
    transcript, got = [], frozenset()
    said, outcome = person.opening, "max_turns"
    for turn in range(1, settings.max_turns + 1):
        reply, seconds = reply_fn(said)
        entry = {"turn": turn, "person": said, "elaina": reply, "seconds": seconds}
        transcript.append(entry)
        state = person_fn(person, transcript, got)
        if state is None:
            outcome = "simulator_error"
            break
        gained = sorted(state["got"] - got)
        got = state["got"]
        entry["after"] = {"gained": gained, "bothered_by": state["bothered_by"], "done": state["done"]}
        if state["done"] in ("satisfied", "gave_up"):
            outcome = state["done"]
            closing_reply, closing_seconds = reply_fn(state["say"])
            transcript.append({"turn": turn + 1, "person": state["say"], "elaina": closing_reply,
                               "seconds": closing_seconds, "closing": True})
            break
        said = state["say"]
    return {"person": person.id, "language": person.language, "outcome": outcome,
            "got": sorted(got), "of": list(person.want_ids), "turns": len(transcript),
            "transcript": transcript}


def _ollama_person(model: str, seed: int, temperature: float):
    import ollama

    def respond(person: Person, transcript: list[dict], got: frozenset) -> dict | None:
        for attempt in range(2):
            response = ollama.chat(model=model, messages=person_messages(person, transcript),
                                   stream=False, format="json", think=False,
                                   options={"temperature": temperature, "seed": seed + attempt,
                                            "num_predict": 300})
            state = parse_person(str((response.get("message") or {}).get("content") or ""), person, got)
            if state is not None:
                return state
        return None

    return respond


def run(name: str, arm: str, person_model: str, seed: int, only: list[str]) -> int:
    from evals import run as runner
    from evals.phase2 import unload_models

    if runner.port_open():
        print("Port 8765 is in use -- is Elaina already running? Close it first.")
        return 1
    people, settings = load_people()
    if only:
        people = [x for x in people if x.id in only]
    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    path = out / "dialogues.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(line)["person"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    meta = out / "meta.json"
    if not meta.exists():
        version = json.loads(PROFILES.read_text(encoding="utf-8")).get("version", "")
        meta.write_text(json.dumps({"arm": arm, "env": ARMS[arm], "person_model": person_model,
                                    "judge": DEFAULT_JUDGE, "seed": seed, "profiles": PROFILES.name,
                                    "profiles_version": version, "simulator": SIMULATOR,
                                    "started_at": datetime.now().isoformat(timespec="seconds")},
                                   indent=1), encoding="utf-8")
    respond = _ollama_person(person_model, seed, temperature=0.6)
    unload_models()
    for number, person in enumerate(people, 1):
        if person.id in done:
            continue
        runtime = out / "rt" / person.id
        elaina = Elaina(runtime, out / "logs" / f"{person.id}.log", ARMS[arm])
        started = time.monotonic()
        try:
            record = converse(person, settings, elaina.reply, respond)
        finally:
            elaina.close()
        record["traces"] = [runner.summarize(r) for r in runner.read_traces(runtime)]
        record["wall_seconds"] = round(time.monotonic() - started, 1)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[{number}/{len(people)}] {person.id}: {record['outcome']} after {record['turns']} "
              f"replies, got {len(record['got'])}/{len(record['of'])} ({record['wall_seconds']}s)", flush=True)
    return 0


# ------------------------------------------------------------- the judge

JUDGE_PROMPT = """You are evaluating a spoken conversation between a person and a voice assistant, Elaina. You know things about the person; Elaina knew only what the person said in the conversation.

THE PERSON
{persona}
Situation: {situation}
Facts about them: {facts}.
WHAT THEY WANTED
{wants}

THE CONVERSATION (numbers are Elaina's replies)
{conversation}

Judge each of Elaina's numbered replies from the transcript only:
- "answered": does it respond to what the person had just said -- their question, request, correction or feeling? "yes", "partly" or "no".
- "wrong": does it state anything false, give a wrong number, or get wrong something the person said about themselves? true or false. Say what was wrong in "wrong_what", else "".
- "remembered": if something the person said earlier mattered for this reply, did she use or respect it? "yes", "no", or "n/a" when nothing earlier mattered.
- "length": for what was said, is it "right", "too long" or "too short" for a spoken reply?
- "extra": anything tacked on that the person did not need: "none", "offer" (an offer to look something up or do something), "question" (a question she did not need to ask), "filler" (stock phrases, or restating what they said), or "unrelated" (a sentence about something else).
- "natural": does it sound like a person talking -- not a lecture, a list or a script? "yes" or "no".

Then, for each thing the person wanted, say whether Elaina gave it to them, judged against this person's facts.

Return one JSON object and nothing else:
{{"wants": [{{"id": "...", "met": "yes" or "no", "turn": the first reply number that met it, or 0}}],
 "turns": [{{"turn": 1, "answered": "yes", "wrong": false, "wrong_what": "", "remembered": "n/a", "length": "right", "extra": "none", "natural": "yes"}}],
 "overall": {{"fluent": "yes" or "partly" or "no", "why": "one sentence"}}}}"""


def judge_messages(person: Person, record: dict) -> list[dict]:
    conversation = "\n".join(
        f"[{t['turn']}] Person{' (closing)' if t.get('closing') else ''}: {t['person']}\n"
        f"[{t['turn']}] Elaina: {t['elaina']}" for t in record["transcript"])
    prompt = JUDGE_PROMPT.format(
        persona=person.persona, situation=person.situation,
        facts="; ".join(person.facts) or "nothing in particular",
        wants="\n".join(f"- {i}: {need}" for i, need in person.wants), conversation=conversation)
    return [{"role": "user", "content": prompt}]


def _one_of(value, allowed: tuple, default: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in allowed else default


def _true(value) -> bool:
    return value is True or str(value).strip().lower() == "true"


def parse_judgement(text: str, person: Person, record: dict) -> dict | None:
    try:
        data = json.loads(str(text or "").strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    wants = {str(w.get("id")): w for w in data.get("wants") or () if isinstance(w, dict)}
    turns = {}
    for t in data.get("turns") or ():
        if isinstance(t, dict):
            try:
                turns[int(t.get("turn") or 0)] = t
            except (TypeError, ValueError):
                continue
    expected = {t["turn"] for t in record["transcript"]}
    if set(wants) != set(person.want_ids) or not expected <= set(turns):
        return None
    return {
        "wants": {k: str(v.get("met")).strip().lower() == "yes" for k, v in wants.items()},
        "want_turns": {k: int(v.get("turn") or 0) if str(v.get("turn") or "0").isdigit() else 0
                       for k, v in wants.items()},
        "turns": {n: {"answered": _one_of(t.get("answered"), ANSWERED, "no"),
                      "wrong": _true(t.get("wrong")),
                      "wrong_what": str(t.get("wrong_what") or "") if _true(t.get("wrong")) else "",
                      "remembered": _one_of(t.get("remembered"), REMEMBERED, "n/a"),
                      "length": _one_of(t.get("length"), LENGTHS, "right"),
                      "extra": _one_of(t.get("extra"), EXTRAS, "none"),
                      "natural": _one_of(t.get("natural"), ("yes", "no"), "no")}
                  for n, t in turns.items() if n in expected},
        "overall": {k: str((data.get("overall") or {}).get(k, "")) for k in ("fluent", "why")},
    }


def judge(name: str) -> int:
    import ollama

    from evals.phase2 import unload_models

    people = {x.id: x for x in load_people()[0]}
    out = ROOT / name
    records = [json.loads(line) for line in (out / "dialogues.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    unload_models()
    judged = []
    for record in records:
        person = people[record["person"]]
        verdict = None
        for attempt in range(3):
            response = ollama.chat(model=DEFAULT_JUDGE, messages=judge_messages(person, record),
                                   stream=False, format="json", think=False,
                                   options={"temperature": 0, "seed": attempt, "num_predict": 2000,
                                            "num_ctx": 8192})
            verdict = parse_judgement(str((response.get("message") or {}).get("content") or ""),
                                      person, record)
            if verdict is not None:
                break
        judged.append({"person": record["person"], "judge": DEFAULT_JUDGE, "verdict": verdict})
        print(f"judged {record['person']}: {'ok' if verdict else 'unreadable'}", flush=True)
    (out / "judged.jsonl").write_text("".join(json.dumps(j, ensure_ascii=False) + "\n" for j in judged),
                                      encoding="utf-8")
    return 0


# ------------------------------------------------------------ the report


def repeats(record: dict) -> int:
    """Replies at least REPEAT_LIKENESS like an earlier reply in the same conversation."""
    replies = [str(t.get("elaina") or "") for t in record["transcript"]]
    return sum(1 for i, reply in enumerate(replies)
               if reply and any(SequenceMatcher(None, reply, earlier).ratio() >= REPEAT_LIKENESS
                                for earlier in replies[:i] if earlier))


def stage_offers(record: dict) -> int:
    """Replies to which a reply stage appended an offer."""
    return sum(1 for trace in record.get("traces") or ()
               for step in trace.get("changed") or () if step.get("name") == "append_recommendation")


def measures(records: list[dict], judged: dict) -> dict:
    """The conversation-level and reply-level numbers for one run."""
    n = len(records)
    satisfied = [r for r in records if r["outcome"] == "satisfied"]
    got_it = [r for r in satisfied if (judged.get(r["person"]) or {}).get("wants")
              and all(judged[r["person"]]["wants"].values())]
    tags = [t for r in records for t in ((judged.get(r["person"]) or {}).get("turns") or {}).values()]
    wants = [met for r in records for met in ((judged.get(r["person"]) or {}).get("wants") or {}).values()]
    remembered = [t["remembered"] for t in tags if t["remembered"] in ("yes", "no")]
    seconds = sorted(t["seconds"] for r in records for t in r["transcript"])

    def count(values, value):
        return (sum(1 for v in values if v == value), len(values))

    return {
        "conversations": n,
        "person_satisfied": (len(satisfied), n),
        "got_what_they_came_for": (len(got_it), n),
        "wants_met": (sum(1 for met in wants if met), len(wants)),
        "gave_up": count([r["outcome"] for r in records], "gave_up"),
        "simulator_errors": count([r["outcome"] for r in records], "simulator_error"),
        "unjudged": (sum(1 for r in records if not judged.get(r["person"])), n),
        "answered": count([t["answered"] for t in tags], "yes"),
        "not_answered": count([t["answered"] for t in tags], "no"),
        "wrong": count([t["wrong"] for t in tags], True),
        "remembered": count(remembered, "yes"),
        "length_right": count([t["length"] for t in tags], "right"),
        "too_long": count([t["length"] for t in tags], "too long"),
        "nothing_extra": count([t["extra"] for t in tags], "none"),
        "extras": {kind: sum(1 for t in tags if t["extra"] == kind) for kind in EXTRAS if kind != "none"},
        "natural": count([t["natural"] for t in tags], "yes"),
        "repeats": (sum(repeats(r) for r in records), sum(len(r["transcript"]) for r in records)),
        "stage_offers": (sum(stage_offers(r) for r in records), sum(len(r["transcript"]) for r in records)),
        "reply_seconds_p50": seconds[len(seconds) // 2] if seconds else None,
        "reply_seconds_p95": seconds[min(len(seconds) - 1, int(0.95 * (len(seconds) - 1) + 0.5))] if seconds else None,
    }


def _load(name: str) -> tuple[list[dict], dict, dict]:
    out = ROOT / name
    records = [json.loads(line) for line in (out / "dialogues.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    judged = {}
    if (out / "judged.jsonl").exists():
        for line in (out / "judged.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                verdict = item.get("verdict")
                if verdict:
                    verdict["turns"] = {int(k): v for k, v in verdict["turns"].items()}
                judged[item["person"]] = verdict
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    return records, judged, meta


ROWS = (
    ("got_what_they_came_for", "the person was satisfied and the judge finds every want met"),
    ("person_satisfied", "the person says they are satisfied"),
    ("wants_met", "wants met (judge)"),
    ("gave_up", "the person gave up"),
    ("answered", "replies that answer what was just said"),
    ("not_answered", "replies that do not"),
    ("wrong", "replies stating something wrong"),
    ("remembered", "replies using what they said earlier, where it mattered"),
    ("length_right", "replies the right length to say aloud"),
    ("too_long", "replies too long"),
    ("nothing_extra", "replies with nothing tacked on"),
    ("natural", "replies that sound like a person"),
    ("repeats", "replies repeating an earlier one"),
    ("stage_offers", "offers appended by a reply stage"),
    ("simulator_errors", "simulator errors"),
    ("unjudged", "conversations the judge could not read"),
    ("reply_seconds_p50", "reply seconds, p50 (includes model swaps)"),
    ("reply_seconds_p95", "reply seconds, p95"),
)


def report(names: list[str]) -> str:
    lines = ["# Everyday English", "",
             f"*Generated by `evals/sim_everyday.py` on {datetime.now():%Y-%m-%d %H:%M}. Provisional "
             "until the user's ratings agree with the judge.*", ""]
    for name in names:
        records, judged, meta = _load(name)
        m = measures(records, judged)
        lines += [f"## {name}: arm `{meta['arm']}`, person `{meta['person_model']}`, seed {meta['seed']}", "",
                  "| measure | value |", "|---|---|"]
        for key, label in ROWS:
            value = m[key]
            lines.append(f"| {label} | {_pair(value) if isinstance(value, tuple) else value} |")
        lines.append(f"| tacked on, by kind | {', '.join(f'{k} {v}' for k, v in m['extras'].items())} |")
        lines += ["", "| person | outcome | got | replies | judge: fluent | wrong | judge: why |",
                  "|---|---|---|---|---|---|---|"]
        for r in records:
            v = judged.get(r["person"]) or {}
            wrongs = "; ".join(t["wrong_what"] for t in (v.get("turns") or {}).values() if t.get("wrong_what"))
            lines.append(f"| `{r['person']}` | {r['outcome']} | {len(r['got'])}/{len(r['of'])} | {r['turns']} "
                         f"| {(v.get('overall') or {}).get('fluent', '-')} | {wrongs or '-'} "
                         f"| {(v.get('overall') or {}).get('why', '-')} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def export_calibration(name: str, count: int, seed: int = 20261014, ids: list | None = None) -> int:
    """Transcripts for the user to rate, without the person's or the judge's view."""
    records, _judged, _meta = _load(name)
    people = {x.id: x for x in load_people()[0]}
    if ids:
        chosen = [r for i in ids for r in records if r["person"] == i]
    else:
        chosen = list(records)
        random.Random(seed).shuffle(chosen)
        chosen = chosen[:count]
    out = ROOT / name
    lines = [f"# Rate these conversations ({len(chosen)})", "",
             "For each: (1) did Elaina give this person what they wanted? yes / partly / no. (2) Was it "
             "fluent, natural English, the way you would want her to talk? yes / partly / no. (3) Was "
             "anything she said wrong, odd or annoying? Write it in a few words. Put the answers in "
             "calibration.json.", ""]
    ratings = []
    for number, record in enumerate(chosen, 1):
        person = people[record["person"]]
        lines += [f"## Conversation {number}", "",
                  f"*Who the person is (Elaina did not know this):* {person.persona} {person.situation} "
                  f"About them: {'; '.join(person.facts)}.", "",
                  "*What they wanted:* " + " / ".join(need for _, need in person.wants), ""]
        for turn in record["transcript"]:
            lines += [f"**Person:** {turn['person']}", "", f"**Elaina:** {turn['elaina']}", ""]
        ratings.append({"conversation": number, "person": record["person"],
                        "got_what_they_wanted": "", "fluent": "", "wrong_or_odd": ""})
    (out / "calibration.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "calibration.json").write_text(json.dumps({"ratings": ratings}, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    return len(chosen)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", default="")
    parser.add_argument("--arm", default="current", choices=sorted(ARMS))
    parser.add_argument("--person-model", default=M27)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--judge", default="")
    parser.add_argument("--report", nargs="*", default=[])
    parser.add_argument("--export-calibration", default="")
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--ids", nargs="*", default=[])
    args = parser.parse_args()
    if args.run:
        return run(args.run, args.arm, args.person_model, args.seed, args.only)
    if args.judge:
        return judge(args.judge)
    if args.report:
        print(report(args.report))
        return 0
    if args.export_calibration:
        print(f"Wrote {export_calibration(args.export_calibration, args.count, ids=args.ids)} "
              "conversations to rate")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
