"""A new question answered with the answer to the last one: what the repair asks again.

    .venv/Scripts/python.exe -m evals.last_answer_repair --run
    .venv/Scripts/python.exe -m evals.last_answer_repair --report

``not_her_last_answer`` (brain/chat_engine.py) notices a reply that is her
previous answer over again and asks the question once more with no history
(``_answered_on_its_own``). What it asks is the router's reading of the turn,
not the words said, and for a reason that was measured: a bare follow-up
("얼마나 우려야 돼?") means nothing without the conversation.

Measured 2026-10-07, five live runs out of five: "물은 몇 도에서 끓어?"
straight after "What is the freezing point of water in Fahrenheit?" was
written by the router as *that* question ("freezing point of water in
Fahrenheit"). The draft gave the freezing point, the repair noticed, asked the
router's reading again and was given the freezing point again; the unit stage
then said it in Celsius: "물의 동결점은 섭씨 0도입니다."

The rule this was written to test, in the repair only (the router is not
touched):

    when the router's request for this turn is the request it wrote for the
    turn before, the repair asks the words the person said instead.

Three parts, on the real 8B with the context size production sends, and no
production change:

* the recorded turns: the repair call exactly as the trace holds it (which has
  to give back the recorded answer), and again with the person's words in
  place of the router's request; then what the engine's own tests would make
  of the second answer, and what the unit stage would show;
* follow-ups that only mean something through the router's reading, written
  for this check: whether the rule would leave them alone (it must), and what
  each wording gets when asked;
* new questions asked straight after a related one, written for this check:
  how often the router writes the previous request for them;
* the same question asked again in other words, written for this check. The
  router rightly writes the same request for these, so the rule's test is met
  on them too, and a bare "몇 도라고?" asked with no history is the failure
  the router's reading was put there to prevent.
"""

from __future__ import annotations

import contextlib
import glob
import io
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ROOT = PROJECT_ROOT / "runtime" / "evals"
OUT = ROOT / "last_answer"
REPAIR = "_answered_on_its_own"
HEADER = "CURRENT USER MESSAGE\n"

# Follow-ups that need the router's reading: (asked before, her reply, the follow-up).
FOLLOW_UPS = [
    ("콜드브루 커피는 어떻게 만들어?", "굵게 간 원두를 찬물에 넣고 냉장고에서 12시간 정도 우려낸 뒤 걸러내면 됩니다.",
     "얼마나 우려야 돼?"),
    ("What is the freezing point of water in Fahrenheit?", "The freezing point of water is 32 degrees Fahrenheit.",
     "섭씨로는 몇 도야?"),
    ("물은 몇 도에서 끓어?", "물은 섭씨 100도에서 끓습니다.", "화씨로는 몇 도야?"),
    ("김치찌개는 어떻게 끓여?", "돼지고기와 김치를 볶다가 물을 붓고 20분 정도 끓이면 됩니다.", "몇 분 끓여야 돼?"),
    ("서울에서 부산까지 KTX로 얼마나 걸려?", "KTX로 약 2시간 30분 걸립니다.", "요금은 얼마야?"),
]
# New questions asked straight after a related one: (asked before, her reply, the new question).
NEW_QUESTIONS = [
    ("What is the freezing point of water in Fahrenheit?", "The freezing point of water is 32 degrees Fahrenheit.",
     "물은 몇 도에서 끓어?"),
    ("물은 몇 도에서 얼어?", "물은 섭씨 0도에서 업니다.", "물은 몇 도에서 끓어?"),
    ("What is the freezing point of water in Fahrenheit?", "The freezing point of water is 32 degrees Fahrenheit.",
     "What is the boiling point of water in Fahrenheit?"),
    ("How tall is Mount Rainier?", "Mount Rainier is 14,410 feet tall.", "후지산은 높이가 몇 미터야?"),
    ("What's the capital of France?", "The capital of France is Paris.", "독일 수도는 어디야?"),
    ("How far is the Moon from Earth?", "The Moon is about 238,855 miles from Earth.", "태양까지는 몇 킬로야?"),
    ("How many continents are there?", "There are seven continents.", "바다는 몇 개야?"),
    ("Who wrote Pride and Prejudice?", "Jane Austen wrote Pride and Prejudice.", "햄릿은 누가 썼어?"),
    ("How many ounces are in a pound?", "There are 16 ounces in a pound.", "1킬로그램은 몇 그램이야?"),
]


# The question before, asked again: (asked before, her reply, the re-ask).
RE_ASKS = [
    ("What is the freezing point of water in Fahrenheit?", "The freezing point of water is 32 degrees Fahrenheit.",
     "몇 도라고?"),
    ("물은 몇 도에서 끓어?", "물은 섭씨 100도에서 끓습니다.", "몇 도라고 했지?"),
    ("서울에서 부산까지 몇 킬로야?", "서울에서 부산까지는 약 325킬로미터입니다.", "몇 킬로라고?"),
    ("에베레스트는 높이가 몇 미터야?", "에베레스트의 높이는 8,849미터입니다.", "다시 한 번, 몇 미터야?"),
    ("What is the freezing point of water in Fahrenheit?", "The freezing point of water is 32 degrees Fahrenheit.",
     "화씨로 물이 어는 온도가 몇 도야?"),
    ("한국 인구가 얼마야?", "한국 인구는 약 5,100만 명입니다.", "그래서 얼마라고?"),
]


def same_request(mine: str, before: str) -> bool:
    """The rule's test: the router wrote this turn's request as the last one's."""
    squash = lambda text: re.sub(r"[\W_]+", " ", str(text or "").casefold()).strip()  # noqa: E731
    return bool(squash(mine)) and squash(mine) == squash(before)


# ------------------------------------------------------------ the record


def recorded() -> list[dict]:
    """Every recorded turn on which the repair asked again, and how often it could have."""
    rows, total, same_other_words = [], 0, []
    files = sorted(glob.glob(str(ROOT / "**" / "turn_trace" / "*.jsonl"), recursive=True))
    files += sorted(glob.glob(str(PROJECT_ROOT / "runtime" / "turn_trace" / "*.jsonl")))
    for path in files:
        turns = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError:
                    turns.append({})
        where = "/".join(Path(path).relative_to(PROJECT_ROOT / "runtime").parts[1:-2])
        # The trace writes a system prompt out once and names it by its hash
        # after that (core/turn_trace.py); the repair's is one it has seen.
        systems = {}
        for turn in turns:
            for call in turn.get("model_calls") or ():
                for message in call.get("messages") or ():
                    if message.get("role") == "system" and message.get("content"):
                        systems[message.get("sha1")] = str(message["content"])
        for number, turn in enumerate(turns, 1):
            total += 1
            context = turn.get("context") or {}
            route = context.get("route") or {}
            before = turns[number - 2] if number > 1 else {}
            before_route = (before.get("context") or {}).get("route") or {}
            said, request = str(turn.get("user_input") or ""), str(route.get("normalized_request") or "")
            same = number > 1 and same_request(request, before_route.get("normalized_request"))
            if same and re.sub(r"\W+", "", said.casefold()) != re.sub(r"\W+", "", str(before.get("user_input") or "").casefold()):
                same_other_words.append(f"{where} t{number}")
            calls = [c for c in turn.get("model_calls") or () if str(c.get("caller", "")).endswith(REPAIR)]
            step = next((s for s in turn.get("steps") or () if s.get("name") == "not_her_last_answer"), None)
            if not calls or step is None:
                continue
            call = calls[-1]
            history = [(str(t.get("user_input") or ""), str(t.get("display") or "")) for t in turns[:number - 1]]
            rows.append({
                "where": f"{where} t{number}", "said": said, "request": request,
                "request_before": str(before_route.get("normalized_request") or ""), "same_request": same,
                "language": str(context.get("language") or ""),
                "said_before": str(before.get("user_input") or ""), "reply_before": str(before.get("display") or ""),
                "topic_before": str(before_route.get("topic") or ""), "entity_before": str(before_route.get("entity") or ""),
                "history": history, "draft": str((turn.get("draft") or {}).get("text") or ""),
                "kept": bool(step.get("changed")), "shown": str(turn.get("display") or ""),
                "messages": [{"role": m.get("role"),
                              "content": m.get("content") if m.get("content") is not None
                              else systems.get(m.get("sha1"))} for m in call.get("messages") or ()],
                "options": call.get("options") or {},
                "model": str(call.get("model") or ""), "think": call.get("think"),
                "again": str(call.get("output") or ""),
            })
    return [{"turns": total, "same_request_other_words": same_other_words}, rows]


# ------------------------------------------------------------- the model


class Bench:
    """The engine's own router and the repair call, on the real model."""

    def __init__(self) -> None:
        import ollama

        from core import model_context
        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine.computer_control_mode.set_enabled(False)
        self.client = model_context.ContextSizedClient(
            ollama.Client(host=self.engine.config.get("llm", "ollama", "base_url")),
            model_context.configured(self.engine.config))
        router = self.engine.intent_router
        router.client = self.client
        router.print_confidence_log = False
        self.model = router.model

    def read(self, question: str, history=(), topic: str = "", entity: str = "") -> dict:
        """The router's reading of ``question`` after ``history``."""
        engine = self.engine
        engine._router_history.clear()
        for said, reply in history:
            engine._router_history.extend([{"role": "user", "content": said},
                                           {"role": "assistant", "content": reply}])
        engine._active_topic, engine._active_entity = topic, entity
        with contextlib.redirect_stdout(io.StringIO()):
            decision = engine.intent_router.route(
                question, recent_turns=list(engine._router_history), has_screen_selection=False,
                project_tools_available=engine.project_mcp is not None,
                conversation_state=engine._build_conversation_state(), pending_action="",
                computer_control_enabled=False)
        return {"intent": decision.intent, "request": decision.normalized_request,
                "is_follow_up": bool(decision.is_follow_up), "topic": decision.topic, "entity": decision.entity}

    def ask_again(self, template: dict, question: str) -> str:
        """The repair call of ``template``, with ``question`` as the current message."""
        messages = [dict(message) for message in template["messages"]]
        if any(message.get("content") is None for message in messages):
            raise ValueError("the recorded repair prompt is not whole")
        body = str(messages[-1].get("content") or "")
        marker = HEADER + template["request"] + "\n"
        if body.count(marker) != 1:
            raise ValueError("the recorded repair prompt does not hold the request where expected")
        messages[-1]["content"] = body.replace(marker, HEADER + question + "\n")
        response = self.client.chat(model=template["model"], messages=messages, stream=False,
                                    options=dict(template["options"]), keep_alive=-1, think=False)
        return str((response.get("message") or {}).get("content") or "").strip()

    def as_the_engine_would(self, fresh: str, *, said: str, previous: str, language: str) -> dict:
        """What ``_not_her_last_answer`` and the unit stage make of a second answer."""
        from brain import korean_register, told_not_asked, units

        if language == "ko" and fresh:
            fresh = korean_register.to_formal(fresh)
        kept = bool(fresh and not told_not_asked.repeats(fresh, previous)
                    and not told_not_asked.answers_the_one_before(fresh, said, previous))
        shown = units.in_celsius(fresh, said) if language == "ko" else fresh
        return {"formal": fresh, "kept": kept, "shown": shown}


def run() -> dict:
    counts, rows = recorded()
    bench = Bench()
    print(f"model: {bench.model}; recorded turns on which the repair asked again: {len(rows)}", flush=True)
    out = {"model": bench.model, "counts": counts, "recorded": [], "follow_ups": [], "new_questions": [],
           "re_asks": []}
    OUT.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # 1. the recorded turns
    for row in rows:
        as_recorded = bench.ask_again(row, row["request"])
        in_her_words = bench.ask_again(row, row["said"])
        entry = {k: row[k] for k in ("where", "said", "request", "request_before", "same_request", "language",
                                     "said_before", "reply_before", "draft", "kept", "shown", "again")}
        entry["control"] = as_recorded
        entry["in_her_words"] = in_her_words
        entry["then"] = bench.as_the_engine_would(in_her_words, said=row["said"], previous=row["reply_before"],
                                                  language=row["language"])
        # the router, read again from the recorded conversation: is the instrument the recorded one?
        entry["router_again"] = bench.read(row["said"], row["history"][-3:], topic=row["topic_before"],
                                           entity=row["entity_before"])
        out["recorded"].append(entry)
        save()
        print(f"[recorded] {row['where']}: control {'=' if as_recorded == row['again'] else '!='} recorded | "
              f"in her words -> {in_her_words[:70]!r}", flush=True)

    template = rows[-1] if rows else None

    def pair(group: str, before: str, reply: str, question: str) -> None:
        first = bench.read(before)
        mine = bench.read(question, [(before, reply)], topic=first["topic"], entity=first["entity"])
        same = same_request(mine["request"], first["request"])
        from types import SimpleNamespace

        entry = {"said_before": before, "reply_before": reply, "said": question, "request_before": first["request"],
                 "request": mine["request"], "is_follow_up": mine["is_follow_up"], "intent": mine["intent"],
                 "same_request": same,
                 # brain/chat_engine.py: the stage is passed over on a turn that only means something
                 # with its history (a clarification, or a follow-up left as the words said).
                 "stage_skipped": bool(bench.engine._reply_rests_on_the_conversation(
                     SimpleNamespace(intent=mine["intent"], normalized_request=mine["request"],
                                     is_follow_up=mine["is_follow_up"]), question))}
        korean = any("가" <= ch <= "힣" for ch in question)
        if template is not None and korean and (group in ("follow_ups", "re_asks") or same):
            entry["by_request"] = bench.ask_again(template, mine["request"] or question)
            entry["by_words"] = bench.ask_again(template, question)
            for name in ("by_request", "by_words"):
                entry[name + "_kept"] = bench.as_the_engine_would(
                    entry[name], said=question, previous=reply, language="ko")["kept"]
        out[group].append(entry)
        save()
        print(f"[{group}] {question!r} -> request {mine['request']!r} | same as before: {same}", flush=True)

    for before, reply, question in FOLLOW_UPS:
        pair("follow_ups", before, reply, question)
    for before, reply, question in NEW_QUESTIONS:
        pair("new_questions", before, reply, question)
    for before, reply, question in RE_ASKS:
        pair("re_asks", before, reply, question)
    return out


# ------------------------------------------------------------ the report


def report() -> str:
    out = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    rec = out["recorded"]
    lines = ["# A new question answered with the last answer: what the repair asks again", "",
             f"Written by `evals/last_answer_repair.py`, on `{out['model']}`, temperature 0, with the context size "
             "production sends. Production is not changed by anything here, and the router is not touched.", "",
             "## The record", "",
             f"- Turns on record: {out['counts']['turns']}. The repair asked again on {len(rec)}; its second answer "
             f"was kept on {sum(e['kept'] for e in rec)}.",
             f"- On {sum(e['same_request'] for e in rec)} of those {len(rec)} the router's request was the request "
             "it wrote for the turn before.",
             f"- Turns on which the router wrote the previous turn's request for different words, whether or not "
             f"the repair ran: {len(out['counts']['same_request_other_words'])}.", "",
             "## 1. The recorded turns", "",
             f"- The repair call replayed exactly as recorded gives the freezing point again (32, and not 100) "
             f"in {sum('32' in e['control'] and '100' not in e['control'] for e in rec)} of {len(rec)}, and the "
             f"recorded answer word for word in {sum(e['control'] == e['again'] for e in rec)}. Recorded: "
             + "; ".join(sorted({e["again"] for e in rec})) + ".",
             f"- Read again from the recorded conversation, the router writes the recorded request in "
             f"{sum(same_request(e['router_again']['request'], e['request']) for e in rec)} of {len(rec)}.",
             f"- Asked in the person's words instead: the answer states 100 in "
             f"{sum('100' in e['then']['shown'] for e in rec)} of {len(rec)}; the engine's own test would keep it "
             f"in {sum(e['then']['kept'] for e in rec)} of {len(rec)}.", "",
             "| Turn | Said | Router's request | Asked again as recorded | Asked in her words | Kept | Shown |",
             "|---|---|---|---|---|---|---|"]
    for e in rec:
        lines.append(f"| {e['where']} | {e['said']} | {e['request']} | {e['control']} | {e['in_her_words']} | "
                     f"{'yes' if e['then']['kept'] else 'no'} | {e['then']['shown']} |")
    lines += ["", "Shown today on each of them: " + "; ".join(sorted({e["shown"] for e in rec})) + ".", ""]

    ups = out["follow_ups"]
    lines += ["## 2. Follow-ups that need the router's reading (written for this check)", "",
              f"The rule would change what the repair asks on {sum(e['same_request'] for e in ups)} of {len(ups)}. "
              "Where it changes nothing the repair asks exactly what it asks today. Both wordings were asked "
              "anyway, to show what the router's reading is for.", "",
              "| Before | Follow-up | Router's request | Same as the request before | Asked by the request | "
              "Asked by the bare words |", "|---|---|---|---|---|---|"]
    for e in ups:
        lines.append(f"| {e['said_before']} | {e['said']} | {e['request']} | {'yes' if e['same_request'] else 'no'} "
                     f"| {e.get('by_request', '')} | {e.get('by_words', '')} |")
    lines.append("")

    new = out["new_questions"]
    wrote_previous = [e for e in new if e["same_request"]]
    lines += ["## 3. New questions straight after a related one (written for this check)", "",
              f"The router wrote the previous turn's request for {len(wrote_previous)} of {len(new)}.", "",
              "| Before | New question | Router's request | Same as the request before | Asked by the request | "
              "Asked by her words |", "|---|---|---|---|---|---|"]
    for e in new:
        lines.append(f"| {e['said_before']} | {e['said']} | {e['request']} | {'yes' if e['same_request'] else 'no'} "
                     f"| {e.get('by_request', '')} | {e.get('by_words', '')} |")
    lines.append("")

    again = out.get("re_asks") or []
    met = [e for e in again if e["same_request"] and not e["stage_skipped"]]
    replaced = [e for e in met if e.get("by_words_kept")]
    lines += ["## 4. The same question asked again in other words (written for this check)", "",
              "The router is right to write the same request here, so the rule's test is met on a turn it was not "
              "meant for. The repair only runs when the draft repeated her previous answer, which is what a "
              "re-ask gets. Today it then asks the request again, is given the same answer, and keeps the "
              "draft.", "",
              f"- Re-asks: {len(again)}. The router wrote the request of the turn before for "
              f"{sum(e['same_request'] for e in again)}; the engine would pass the stage over on "
              f"{sum(e['stage_skipped'] for e in again)}.",
              f"- Of the {len(met)} on which the rule would change what is asked, the answer to the bare words "
              f"would be kept in place of the draft on **{len(replaced)}**.", "",
              "| Before | Re-ask | Router's request | Same as before | Stage passed over | Asked by the request "
              "(kept?) | Asked by her words (kept?) |", "|---|---|---|---|---|---|---|"]
    for e in again:
        lines.append(f"| {e['said_before']} | {e['said']} | {e['request']} | {'yes' if e['same_request'] else 'no'} "
                     f"| {'yes' if e['stage_skipped'] else 'no'} | {e.get('by_request', '')} "
                     f"({'kept' if e.get('by_request_kept') else 'not kept'}) | {e.get('by_words', '')} "
                     f"({'kept' if e.get('by_words_kept') else 'not kept'}) |")
    lines.append("")
    text = "\n".join(lines) + "\n"
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if "--run" in args:
        started = time.monotonic()
        run()
        print(f"done in {time.monotonic() - started:.0f}s", flush=True)
    if "--report" in args or "--run" in args or not args:
        print(report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
