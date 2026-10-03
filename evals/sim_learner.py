"""Simulated learners: can Elaina carry a conversation that helps this person understand?

    .venv/Scripts/python.exe -m evals.sim_learner --run pilot-current --arm current
    .venv/Scripts/python.exe -m evals.sim_learner --judge pilot-current
    .venv/Scripts/python.exe -m evals.sim_learner --report pilot-current
    .venv/Scripts/python.exe -m evals.sim_learner --export-calibration pilot-current --count 5

docs/SIMULATED_LEARNER_EVAL.md. Every earlier evaluation scored single
replies against a rubric, and its "conversations" were scripted: the person
said the same next line whatever Elaina had said, so nothing could test
whether she adapts. Here the person reacts.

* **The learner** (evals/learners/learners_v0.json) is a person with hidden
  knowledge, a misconception and the ideas they need to grasp. A different
  model plays them (the 27B by default; Elaina runs on the 8B) and decides,
  turn by turn, what made sense, what did not, and what to say next. Elaina
  -- the real backend, over its websocket -- is told nothing about them.
* **The judge** (the rubric judge's model, a third model) reads the finished
  transcript with the person's hidden profile, never the learner's own
  claims, and says which ideas were actually conveyed in words this person
  could follow, and how each of Elaina's turns behaved: pitched at this
  person, a changed approach after confusion, a question only when needed,
  natural speech.
* **Calibration**: transcripts for the user to rate. Until a person agrees
  with the learner and the judge on a sample, the numbers are provisional.

The objective is the conversation's: understanding reached, in how many
turns, naturally. Single-reply properties (concrete_first and the rest) are
signals, not the target.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Silent: every dialogue starts a backend, and a backend that spoke would
# spend the paid voice on every turn. evals/run.py forces it off again.
os.environ.setdefault("ELAINA_TTS", "off")

from brain import explain_contract as ec  # noqa: E402
from evals.judge import DEFAULT_JUDGE  # noqa: E402
from evals.phase2 import M8, M27, NO_STT  # noqa: E402

PROFILES = PROJECT_ROOT / "evals" / "learners" / "learners_v0.json"
ROOT = PROJECT_ROOT / "runtime" / "evals" / "sl"
URL = "ws://127.0.0.1:8765"
REPLY_TIMEOUT = 240.0
DONE = ("continue", "understood", "gave_up")
# v1: after pilot 1 (the learner may not supply the answer; unknown words).
# v2: after pilot 2's ratings -- "Most people just ask simple questions like
# I don't understand this part or whats blahblahblah" -- one plain question
# at a time and no recaps; and the closing line is said to Elaina, so no
# conversation ends on a question she never answered.
SIMULATOR = "v2"
ARMS = {
    "current": {**NO_STT, "ELAINA_DOMAINS": "act", "ELAINA_MODEL": M8, ec.EXPERIMENT_ENV: "off",
                "ELAINA_TURN_MOVE": "off"},
    "revised": {**NO_STT, "ELAINA_DOMAINS": "act", "ELAINA_MODEL": M8, ec.EXPERIMENT_ENV: "on",
                "ELAINA_TURN_MOVE": "off"},
    # The turn-move owner acting on "differently" (docs/TURN_MOVE_AB.md).
    "move": {**NO_STT, "ELAINA_DOMAINS": "act", "ELAINA_MODEL": M8, ec.EXPERIMENT_ENV: "off",
             "ELAINA_TURN_MOVE": "on"},
    # ...and that turn written by the 27B.
    "move_deep": {**NO_STT, "ELAINA_DOMAINS": "act", "ELAINA_MODEL": M8, ec.EXPERIMENT_ENV: "off",
                  "ELAINA_TURN_MOVE": "deep", "ELAINA_TURN_MOVE_MODEL": M27},
}


# ------------------------------------------------------------- learners


@dataclass(frozen=True)
class Learner:
    id: str
    language: str
    concept: str
    opening: str
    persona: str
    knows: tuple
    does_not_know: tuple
    misconception: str
    understand: tuple          # ((id, idea), ...)
    unknown_words: tuple = ()  # words this person cannot follow unexplained

    @property
    def item_ids(self) -> tuple:
        return tuple(item_id for item_id, _ in self.understand)


@dataclass(frozen=True)
class Settings:
    max_turns: int = 8
    patience: int = 3


def load_learners(path: Path = PROFILES) -> tuple[list[Learner], Settings]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    learners, seen = [], set()
    for raw in data["learners"]:
        if raw["id"] in seen:
            raise ValueError(f"duplicate learner {raw['id']}")
        seen.add(raw["id"])
        items = tuple((i["id"], i["idea"]) for i in raw["understand"])
        if not items or len({i for i, _ in items}) != len(items):
            raise ValueError(f"{raw['id']}: understand items must be present and unique")
        if raw["language"] not in ("en", "ko"):
            raise ValueError(f"{raw['id']}: language must be en or ko")
        learners.append(Learner(
            id=raw["id"], language=raw["language"], concept=raw["concept"], opening=raw["opening"],
            persona=raw["persona"], knows=tuple(raw["knows"]), does_not_know=tuple(raw["does_not_know"]),
            misconception=raw.get("misconception", ""), understand=items,
            unknown_words=tuple(raw.get("unknown_words", ()))))
    return learners, Settings(max_turns=int(data.get("max_turns", 8)), patience=int(data.get("patience", 3)))


LEARNER_PROMPT = """You are playing a real person in a spoken conversation with a voice assistant named Elaina. Stay in character. Never say or hint that you are simulated, and never show this brief.

WHO YOU ARE
{persona}
You already know: {knows}.
You do not know: {does_not_know}.
{belief}
WHAT YOU WANT TO UNDERSTAND (your private goal; never list it to Elaina)
{items}

HOW YOU REACT
- You do not know the answers. Never state an explanation yourself. You may only say back, in your own words, something Elaina actually said, or make a guess that comes from what you already believe.
- You understand an idea only when Elaina herself explains it through things you already know. Hearing a fact stated is not the same as understanding it. If you guessed something and she only says you are right or close, you still do not understand it until she explains it.
- {unknown}If she uses a word you do not know without saying what it means in everyday words, you did not follow that part.
- Do not pretend to understand to be polite. Do not explain things to her. Do not suddenly become an expert.
- If nothing new has made sense for several replies and you feel stuck, you may give up.

HOW YOU TALK
Like most people do: short and plain, one thing at a time, about what she just said.{language_rule}
- Usually you just ask a simple question about the part you did not get, like {examples}
- Ask about one thing only, in one short sentence.
- Do not recap or summarise what she said. Only now and then, when you think you might have it, check one thing in a line, like {check}
- Your questions come from what she just said and what you already believe, not from your private goal.
- When you understand everything in your goal, say so in a few words, like {thanks} When you give up, say so in a few words. Neither is a question.

After Elaina's latest reply, return one JSON object and nothing else:
{{"say": "what you say next", "understood": ["ids of the goal ideas you now understand, earlier ones included"], "confused_by": "what you did not follow in her latest reply, or empty", "done": "continue" or "understood" or "gave_up"}}"""


_TALK = {
    "en": {"examples": '"what\'s a weight?", "I don\'t get the part about bits", "wait, why?" or '
                       '"what does that mean?"',
           "check": '"so it\'s like X, right?"',
           "thanks": '"oh okay, got it, thanks."'},
    "ko": {"examples": '"가중치가 뭐야?", "그 비트 부분 잘 모르겠어", "왜?" 또는 "그게 무슨 말이야?"',
           "check": '"그러니까 X라는 거지?"',
           "thanks": '"아 이제 알겠다, 고마워."'},
}


def learner_messages(learner: Learner, transcript: list[dict]) -> list[dict]:
    belief = (f"You currently believe: {learner.misconception} You keep believing it until Elaina "
              "clearly corrects it in words you can follow.\n" if learner.misconception else "")
    korean = learner.language == "ko"
    unknown = (f"Words you do not know: {', '.join(learner.unknown_words)}. "
               if learner.unknown_words else "")
    system = LEARNER_PROMPT.format(
        unknown=unknown, persona=learner.persona, knows="; ".join(learner.knows),
        does_not_know="; ".join(learner.does_not_know), belief=belief,
        items="\n".join(f"- {i}: {idea}" for i, idea in learner.understand),
        language_rule=(" Speak Korean, in casual 반말, as your persona does." if korean else ""),
        **_TALK["ko" if korean else "en"])
    lines = []
    for turn in transcript:
        lines += [f"You: {turn['person']}", f"Elaina: {turn['elaina']}"]
    lines.append("That was Elaina's latest reply. Your turn.")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_learner(text: str, learner: Learner, before: frozenset) -> dict | None:
    """The learner's turn, or None when it cannot be read. Understanding only
    accumulates: an idea once understood stays understood."""
    try:
        data = json.loads(str(text or "").strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("say") or "").strip():
        return None
    claimed = data.get("understood") or []
    if not isinstance(claimed, list):
        claimed = []
    understood = frozenset(before | {str(i) for i in claimed if str(i) in learner.item_ids})
    done = str(data.get("done") or "continue").strip().lower()
    if done not in DONE:
        done = "continue"
    # Every idea claimed means understood -- unless the person said they give up.
    if understood == frozenset(learner.item_ids) and done != "gave_up":
        done = "understood"
    return {"say": str(data["say"]).strip(), "understood": understood,
            "confused_by": str(data.get("confused_by") or "").strip(), "done": done}


# ------------------------------------------------------------ the loop


def converse(learner: Learner, settings: Settings, reply_fn, learner_fn) -> dict:
    """One conversation. ``reply_fn(said) -> (reply, seconds)`` is Elaina;
    ``learner_fn(learner, transcript, understood) -> dict | None`` the person.
    Ends when the person understands, gives up, stops making sense, or the
    turn limit is reached."""
    transcript, understood, stalled = [], frozenset(), 0
    said, outcome, closing, closing_reply = learner.opening, "max_turns", "", ""
    for turn in range(1, settings.max_turns + 1):
        reply, seconds = reply_fn(said)
        entry = {"turn": turn, "person": said, "elaina": reply, "seconds": seconds}
        transcript.append(entry)
        state = learner_fn(learner, transcript, understood)
        if state is None:
            outcome = "simulator_error"
            break
        gained = sorted(state["understood"] - understood)
        understood = state["understood"]
        stalled = 0 if gained else stalled + 1
        entry["learner"] = {"gained": gained, "confused_by": state["confused_by"], "done": state["done"]}
        if state["done"] in ("understood", "gave_up"):
            # Said to her, as a person would. The pilots stopped here, and a
            # closing "...but does that change too?" went unanswered.
            outcome, closing = state["done"], state["say"]
            closing_reply, _seconds = reply_fn(closing)
            break
        said = state["say"]
    return {"learner": learner.id, "language": learner.language, "outcome": outcome,
            "understood": sorted(understood), "of": list(learner.item_ids),
            "turns": len(transcript), "closing": closing, "closing_reply": closing_reply,
            "transcript": transcript}


# ------------------------------------------------------- the real thing


def _ollama_learner(model: str, seed: int, temperature: float):
    import ollama

    def respond(learner: Learner, transcript: list[dict], understood: frozenset) -> dict | None:
        for attempt in range(2):
            response = ollama.chat(model=model, messages=learner_messages(learner, transcript),
                                   stream=False, format="json", think=False,
                                   options={"temperature": temperature, "seed": seed + attempt,
                                            "num_predict": 300})
            state = parse_learner(str((response.get("message") or {}).get("content") or ""),
                                  learner, understood)
            if state is not None:
                return state
        return None

    return respond


class Elaina:
    """The real backend, over its websocket: one fresh backend per conversation."""

    def __init__(self, runtime: Path, log: Path, env: dict):
        from evals import run as runner

        self._runner = runner
        self.process = runner.start_backend(runtime, log, env)
        from websockets.sync.client import connect

        self.connection = connect(URL, max_size=None)
        self.connection.send(json.dumps({"command": "set_input_mode", "mode": "text"}))
        self.connection.send(json.dumps({"command": "set_computer_control_mode", "enabled": False}))
        time.sleep(1.0)

    def reply(self, said: str) -> tuple[str, float]:
        started = time.monotonic()
        self.connection.send(json.dumps({"command": "send_text_message", "text": said}))
        deadline = started + REPLY_TIMEOUT
        while time.monotonic() < deadline:
            try:
                raw = self.connection.recv(timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if payload.get("event") == "assistant_finished":
                return str(payload.get("text", "")), round(time.monotonic() - started, 2)
        return "", round(time.monotonic() - started, 2)

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            self._runner.stop_backend(self.process)


def run(name: str, arm: str, learner_model: str, seed: int, only: list[str]) -> int:
    from evals import run as runner
    from evals.phase2 import unload_models

    if runner.port_open():
        print("Port 8765 is in use -- is Elaina already running? Close it first.")
        return 1
    learners, settings = load_learners()
    if only:
        learners = [x for x in learners if x.id in only]
    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    done = set()
    path = out / "dialogues.jsonl"
    if path.exists():
        done = {json.loads(line)["learner"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    meta = out / "meta.json"
    if not meta.exists():
        profiles_version = json.loads(PROFILES.read_text(encoding="utf-8")).get("version", "")
        meta.write_text(json.dumps({"arm": arm, "env": ARMS[arm], "learner_model": learner_model,
                                    "judge": DEFAULT_JUDGE, "seed": seed, "profiles": PROFILES.name,
                                    "profiles_version": profiles_version, "simulator": SIMULATOR,
                                    "contract_version": ec.CONTRACT_VERSION,
                                    "started_at": datetime.now().isoformat(timespec="seconds")},
                                   indent=1), encoding="utf-8")
    respond = _ollama_learner(learner_model, seed, temperature=0.6)
    unload_models()
    for number, learner in enumerate(learners, 1):
        if learner.id in done:
            continue
        runtime = out / "rt" / learner.id
        elaina = Elaina(runtime, out / "logs" / f"{learner.id}.log", ARMS[arm])
        started = time.monotonic()
        try:
            record = converse(learner, settings, elaina.reply, respond)
        finally:
            elaina.close()
        record["traces"] = [runner.summarize(r) for r in runner.read_traces(runtime)]
        record["wall_seconds"] = round(time.monotonic() - started, 1)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[{number}/{len(learners)}] {learner.id}: {record['outcome']} after {record['turns']} "
              f"replies, understood {len(record['understood'])}/{len(record['of'])} "
              f"({record['wall_seconds']}s)", flush=True)
    return 0


# ------------------------------------------------------------- the judge

JUDGE_PROMPT = """You are evaluating a spoken conversation between a person and a voice assistant, Elaina. You know things about the person that Elaina was never told.

THE PERSON
{persona}
They knew: {knows}.
They did not know: {does_not_know}.
{belief}
WHAT THEY NEEDED TO UNDERSTAND
{items}

THE CONVERSATION (numbers are Elaina's replies)
{conversation}

Judge from the transcript only, against this person's knowledge.
- An idea counts as conveyed only if Elaina herself explained it in a way this person could follow: no word they did not know left unexplained where the idea depends on it. If the person said the idea and Elaina only agreed ("right", "you're getting close"), she did not convey it.
- "fits_this_person": the reply is pitched at this person -- not over their head, not talking down to them.
- "after_confusion": if the person's message just before this reply showed confusion, did Elaina change her approach (a different starting point, an example, explaining the word they lacked) or repeat the same explanation? "n/a" if they were not confused.
- "question": did Elaina ask the person something? "needed" only if she could not reasonably work out what they meant or lacked; "unneeded" if she could have; "none" if she asked nothing.
- "natural": sounds like a person talking, not a lecture, a list or a script.

Return one JSON object and nothing else:
{{"items": [{{"id": "...", "conveyed": "yes" or "no", "turn": the first reply number that conveyed it, or 0}}],
 "turns": [{{"turn": 1, "fits_this_person": "yes" or "no", "after_confusion": "changed approach" or "repeated" or "n/a", "question": "none" or "needed" or "unneeded", "natural": "yes" or "no"}}],
 "overall": {{"helped_understand": "yes" or "partly" or "no", "natural_conversation": "yes" or "no", "why": "one sentence"}}}}"""


def judge_messages(learner: Learner, record: dict) -> list[dict]:
    belief = f"They believed: {learner.misconception}\n" if learner.misconception else ""
    conversation = "\n".join(f"[{t['turn']}] Person: {t['person']}\n[{t['turn']}] Elaina: {t['elaina']}"
                             for t in record["transcript"])
    if record.get("closing"):
        conversation += f"\nPerson (closing): {record['closing']}"
        if record.get("closing_reply"):
            conversation += f"\nElaina (closing): {record['closing_reply']}"
    prompt = JUDGE_PROMPT.format(
        persona=learner.persona, knows="; ".join(learner.knows),
        does_not_know="; ".join(learner.does_not_know), belief=belief,
        items="\n".join(f"- {i}: {idea}" for i, idea in learner.understand), conversation=conversation)
    return [{"role": "user", "content": prompt}]


def parse_judgement(text: str, learner: Learner, record: dict) -> dict | None:
    try:
        data = json.loads(str(text or "").strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    items = {str(i.get("id")): i for i in data.get("items") or () if isinstance(i, dict)}
    turns = {int(t.get("turn") or 0): t for t in data.get("turns") or () if isinstance(t, dict)}
    expected = {t["turn"] for t in record["transcript"]}
    if set(items) != set(learner.item_ids) or not expected <= set(turns):
        return None
    # Every reply judged; anything more dropped. The judge sometimes scores
    # her answer to the closing line as one more turn (pilot 5, five seeds
    # running), which is not one of the numbered replies.
    turns = {n: t for n, t in turns.items() if n in expected}
    return {"items": {k: str(v.get("conveyed")).lower() == "yes" for k, v in items.items()},
            "item_turns": {k: int(v.get("turn") or 0) for k, v in items.items()},
            "turns": {n: {k: str(t.get(k, "")).lower() for k in
                          ("fits_this_person", "after_confusion", "question", "natural")}
                      for n, t in turns.items()},
            "overall": {k: str((data.get("overall") or {}).get(k, "")) for k in
                        ("helped_understand", "natural_conversation", "why")}}


def judge(name: str) -> int:
    import ollama

    from evals.phase2 import unload_models

    learners = {x.id: x for x in load_learners()[0]}
    out = ROOT / name
    records = [json.loads(line) for line in (out / "dialogues.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    unload_models()
    judged = []
    for record in records:
        learner = learners[record["learner"]]
        verdict = None
        for attempt in range(3):
            response = ollama.chat(model=DEFAULT_JUDGE, messages=judge_messages(learner, record),
                                   stream=False, format="json", think=False,
                                   options={"temperature": 0, "seed": attempt, "num_predict": 1500})
            verdict = parse_judgement(str((response.get("message") or {}).get("content") or ""),
                                      learner, record)
            if verdict is not None:
                break
        judged.append({"learner": record["learner"], "judge": DEFAULT_JUDGE, "verdict": verdict})
        print(f"judged {record['learner']}: {'ok' if verdict else 'unreadable'}", flush=True)
    (out / "judged.jsonl").write_text("".join(json.dumps(j, ensure_ascii=False, default=list) + "\n"
                                              for j in judged), encoding="utf-8")
    return 0


# ------------------------------------------------------------- the report


_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+|\n+")
_TERMS = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d+")
_COMMON = frozenset({"the", "and", "but", "you", "your", "that", "this", "with", "for", "are", "was",
                     "can", "its", "it's", "not", "have", "has", "from", "they", "what", "when"})
# Content words of the last sentence that the draft also has: below this,
# the ending says something the model did not.
DRAFTED_SHARE = 0.6


def _terms(text: str) -> list[str]:
    """Content words: Korean by their first two syllables, so a register
    change (있어요 / 있습니다) is the same word; English whole."""
    found = []
    for word in _TERMS.findall(str(text or "")):
        if "가" <= word[0] <= "힣":
            found.append(word[:2])
        elif word.casefold() not in _COMMON:
            found.append(word.casefold())
    return found


def ending_not_drafted(trace: dict) -> bool | None:
    """Whether the reply they saw ends on something the model did not say
    (a turn record from evals/run.summarize). None when there was no draft.

    The user, rating pilot 2: "she keep on saying weird and unrelated stuffs
    on the end of the sentence". Read by hand from the traces: guard lines
    appended ("I don't want to send you somewhere I haven't checked"), a
    replacement answer, a re-say that added "지금까지 그런 생각이셨다면, 그런
    식으로 답변해드리겠습니다". The same sentence in another register, or two
    of its sentences joined, is not a new ending.
    """
    draft = str(trace.get("draft") or "")
    if not draft.strip():
        return None
    changed = trace.get("changed") or []
    final = str(changed[-1].get("after", "") if changed else draft)
    sentences = [x for x in _SENTENCE_END.split(final.strip()) if _terms(x)]
    if not sentences:
        return None
    last, drafted = _terms(sentences[-1]), set(_terms(draft))
    return sum(1 for word in last if word in drafted) < DRAFTED_SHARE * len(last)


def measures(records: list[dict], judged: dict) -> dict:
    """The conversation-level numbers for one run."""
    n = len(records)
    learner_ok = [r for r in records if r["outcome"] == "understood"]
    verified = [r for r in learner_ok if (judged.get(r["learner"]) or {}).get("items")
                and all(judged[r["learner"]]["items"].values())]
    turns_tags = [t for r in records for t in ((judged.get(r["learner"]) or {}).get("turns") or {}).values()]
    after = [t["after_confusion"] for t in turns_tags if t.get("after_confusion") in ("changed approach", "repeated")]
    seconds = sorted(t["seconds"] for r in records for t in r["transcript"])
    endings = [e for r in records for e in (ending_not_drafted(t) for t in r.get("traces") or ())
               if e is not None]

    def rate(values, value):
        return (sum(1 for v in values if v == value), len(values))

    return {
        "conversations": n,
        "learner_understood": (len(learner_ok), n),
        "verified_understood": (len(verified), n),
        "gave_up": rate([r["outcome"] for r in records], "gave_up"),
        "simulator_errors": rate([r["outcome"] for r in records], "simulator_error"),
        "turns_to_understanding": median(r["turns"] for r in verified) if verified else None,
        "fits_this_person": rate([t.get("fits_this_person") for t in turns_tags], "yes"),
        "changed_after_confusion": rate(after, "changed approach"),
        "unneeded_questions": rate([t.get("question") for t in turns_tags], "unneeded"),
        "natural_turns": rate([t.get("natural") for t in turns_tags], "yes"),
        "ending_not_drafted": rate(endings, True),
        "reply_seconds_p50": seconds[len(seconds) // 2] if seconds else None,
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
                judged[item["learner"]] = verdict
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    return records, judged, meta


def _pair(pair) -> str:
    return f"{pair[0]}/{pair[1]}" + (f" ({pair[0] / pair[1]:.0%})" if pair[1] else "")


def report(names: list[str]) -> str:
    lines = ["# Simulated learners", "",
             f"*Generated by `evals/sim_learner.py` on {datetime.now():%Y-%m-%d %H:%M}. Provisional "
             "until a person's ratings agree with the learner and the judge "
             "(docs/SIMULATED_LEARNER_EVAL.md).*", ""]
    for name in names:
        records, judged, meta = _load(name)
        m = measures(records, judged)
        lines += [f"## {name}: arm `{meta['arm']}`, learner `{meta['learner_model']}`", "",
                  "| measure | value |", "|---|---|"]
        for key, label in (("learner_understood", "the learner says it understood"),
                           ("verified_understood", "...and the judge finds every idea conveyed"),
                           ("turns_to_understanding", "Elaina's replies to understanding (median, verified)"),
                           ("gave_up", "the learner gave up"),
                           ("fits_this_person", "replies pitched at this person"),
                           ("changed_after_confusion", "changed approach after confusion"),
                           ("unneeded_questions", "replies with a question she did not need"),
                           ("natural_turns", "replies that sound natural"),
                           ("ending_not_drafted", "replies ending on a sentence the model did not write"),
                           ("simulator_errors", "simulator errors"),
                           ("reply_seconds_p50", "reply seconds, p50 (includes model swaps)")):
            value = m[key]
            lines.append(f"| {label} | {_pair(value) if isinstance(value, tuple) else value} |")
        lines += ["", "| learner | outcome | understood | replies | judge: helped | judge: why |",
                  "|---|---|---|---|---|---|"]
        for r in records:
            v = judged.get(r["learner"]) or {}
            overall = v.get("overall") or {}
            lines.append(f"| `{r['learner']}` | {r['outcome']} | {len(r['understood'])}/{len(r['of'])} "
                         f"| {r['turns']} | {overall.get('helped_understand', '-')} "
                         f"| {overall.get('why', '-')} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def export_calibration(name: str, count: int, seed: int = 20261002, ids: list | None = None) -> int:
    """Transcripts for the user to rate, without the learner's or the judge's view."""
    records, _judged, _meta = _load(name)
    learners = {x.id: x for x in load_learners()[0]}
    if ids:
        chosen = [r for i in ids for r in records if r["learner"] == i]
    else:
        chosen = list(records)
        random.Random(seed).shuffle(chosen)
        chosen = chosen[:count]
    out = ROOT / name
    lines = [f"# Rate these conversations ({len(chosen)})", "",
             "For each: (1) did Elaina help this person understand? yes / partly / no. (2) Was it a "
             "natural conversation? yes / no. (3) Does the person sound like a real person who is "
             "confused about this? yes / no. Write the answers in calibration.json.", ""]
    ratings = []
    for number, record in enumerate(chosen, 1):
        learner = learners[record["learner"]]
        lines += [f"## Conversation {number}", "",
                  f"*Who the person is (Elaina did not know this):* {learner.persona} Knows: "
                  f"{'; '.join(learner.knows)}. Doesn't know: {'; '.join(learner.does_not_know)}."
                  + (f" Believed: {learner.misconception}" if learner.misconception else ""),
                  "", "*What they needed to understand:* "
                  + " / ".join(idea for _, idea in learner.understand), ""]
        for turn in record["transcript"]:
            lines += [f"**Person:** {turn['person']}", "", f"**Elaina:** {turn['elaina']}", ""]
        if record.get("closing"):
            lines += [f"**Person:** {record['closing']}", ""]
            if record.get("closing_reply"):
                lines += [f"**Elaina:** {record['closing_reply']}", ""]
        ratings.append({"conversation": number, "learner": record["learner"],
                        "helped_understand": "", "natural": "", "person_realistic": "", "notes": ""})
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
    parser.add_argument("--learner-model", default=M27)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--judge", default="")
    parser.add_argument("--report", nargs="*", default=[])
    parser.add_argument("--export-calibration", default="")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--ids", nargs="*", default=[])
    args = parser.parse_args()
    if args.run:
        return run(args.run, args.arm, args.learner_model, args.seed, args.only)
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
