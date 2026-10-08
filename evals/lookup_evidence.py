"""Is the answer missing from what a Korean lookup retrieves, or there and misread?

    .venv/Scripts/python.exe -m evals.lookup_evidence --recorded
    .venv/Scripts/python.exe -m evals.lookup_evidence --search s1
    .venv/Scripts/python.exe -m evals.lookup_evidence --read r1
    .venv/Scripts/python.exe -m evals.lookup_evidence --report s1 r1

The regression runs (docs/TURN_MOVE_AB.md, 2026-10-03) left one thing open:
on the first turn of "what's the newest iPhone model?", the evidence named the
iPhone 18 in 7 of 8 English runs and 3 of 8 Korean ones. Nothing here changes
what she does. Three measurements:

``--recorded``  The first turns already on record (runtime/evals/tmr): what
                each searched, what each search brought back, and what the 8B
                wrote from it.
``--search``    The same searches made again, through the tool she uses
                (tools/web_search.py), several times each; and once through
                each engine the library can pick, since it picks at random.
                The query forms are a 2 x 2 -- English or Korean, the sentence
                as spoken or a keyword query -- plus the dated official-source
                query.
``--read``      The recorded drafting prompts, whole again, drafted by the 8B
                and by the 27B: the Korean ones as they were, the Korean one
                with each English lookup's evidence in its place, and the
                English ones as they were. Drafts only; no stage runs.

"Names the iPhone 18" is a search of titles and snippets for "iPhone 18" or
"아이폰 18". It says the answer is within reach, not that a page states it
well; the report prints the pages and the drafts for reading.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ELAINA_TTS", "off")

from core import model_context  # noqa: E402
from evals.phase2 import M8, M27  # noqa: E402

TMR = PROJECT_ROOT / "runtime" / "evals" / "tmr"
ROOT = PROJECT_ROOT / "runtime" / "evals" / "lookup"

# "iPhone 18", "아이폰 18", and the forms a Korean page uses without the
# word in front: "18 프로", "18 시리즈" (and "18 Pro", "18 series").
EIGHTEEN = re.compile(r"(?:iphone|아이폰)\s*18|18\s*(?:pro\b|프로|시리즈|series)", re.IGNORECASE)
MODEL_NAMED = re.compile(r"(?:iphone|아이폰)\s*(\d{1,2})(?!\d)", re.IGNORECASE)
HANGUL = re.compile(r"[가-힣]")
SECTION = re.compile(r"(?m)^SEARCH (\d+): (.*)$")
RESULT = re.compile(r"(?ms)^\[(\d+)\] (.*?)\nSource: (.*?)\nSnippet: (.*?)(?=^\[\d+\] |\Z)")
CUT = re.compile(r"\n\.\.\.\[(\d+) chars cut\]\.\.\.\n")
EVIDENCE_BLOCK = re.compile(r"(?s)CURRENT RETRIEVED EVIDENCE\n.*?(?=\nCURRENTLY AVAILABLE AI AGENTS)")

# The 2 x 2, and the verification query. The sentences and the router's
# queries are the recorded ones; the Korean keyword query is the router's
# English one put into Korean, which production never searches.
QUERIES = {
    "en sentence": "What's the newest iPhone model right now",
    "en keywords": "latest iPhone model 2026",
    "ko sentence": "요즘 제일 최신 아이폰 모델이 뭐야",
    "ko keywords": "최신 아이폰 모델 2026",
    "official": "official source latest iPhone model 2026 as of 2026-10-03",
}
REPEATS = 5
DRAFTS = 2


def names_18(text: str) -> bool:
    return bool(EIGHTEEN.search(str(text or "")))


def models_named(text: str) -> list[int]:
    return sorted({int(n) for n in MODEL_NAMED.findall(str(text or ""))})


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url or "")).split("/")[0]


def form_of(query: str) -> str:
    if query.casefold().startswith("official source"):
        return "official"
    return "ko sentence" if HANGUL.search(query) else "en keywords"


def sections(research: str) -> list[dict]:
    """The evidence as recorded, back into searches and their results."""
    found = list(SECTION.finditer(research))
    out = []
    for index, match in enumerate(found):
        body = research[match.end():found[index + 1].start() if index + 1 < len(found) else len(research)]
        results = [{"title": title.strip(), "url": url.strip(), "summary": snippet.strip()}
                   for _n, title, url, snippet in RESULT.findall(body.strip() + "\n")]
        out.append({"query": match.group(2).strip(), "results": results})
    return out


def _drafting(turn: dict) -> dict:
    return next((call for call in turn.get("model_calls") or ()
                 if "VOICE RESPONSE REQUIREMENTS" in str((call.get("messages") or [{}])[-1].get("content", ""))),
                {})


def first_turns() -> list[dict]:
    rows = []
    for path in sorted(glob.glob(str(TMR / "*" / "rt" / "new_search_*" / "turn_trace" / "*.jsonl"))):
        with open(path, encoding="utf-8") as handle:
            turn = json.loads(handle.readline())
        research = str((turn.get("evidence") or {}).get("research") or "")
        if not research:
            continue
        case = Path(path).parents[1].name
        rows.append({
            "run": Path(path).parents[3].name, "case": case,
            "language": "ko" if case.endswith("_ko") else "en",
            "said": turn.get("user_input", ""), "research": research, "searches": sections(research),
            "draft": str((turn.get("draft") or {}).get("text") or ""),
            "reply": str(turn.get("display") or ""), "call": _drafting(turn),
        })
    return rows


# ------------------------------------------------------------ recorded


def recorded() -> str:
    rows = first_turns()
    lines = ["## Recorded first turns", "",
             f"{len(rows)} first turns of the iPhone cases, from every regression run "
             "(`runtime/evals/tmr`). The 8B wrote all of them.", ""]
    by_form = defaultdict(list)
    for row in rows:
        for search in row["searches"]:
            by_form[(row["language"], form_of(search["query"]))].append(
                any(names_18(r["title"] + " " + r["summary"]) for r in search["results"]))
    lines += ["| the turn | the search | result sets | name the iPhone 18 |", "|---|---|---|---|"]
    for (language, form), hits in sorted(by_form.items()):
        lines.append(f"| {language} | {form} | {len(hits)} | {sum(hits)} |")
    lines += ["", "| the turn | first turns | evidence names the 18 | of those, the draft names the 18 "
              "| evidence does not | of those, the draft names the 18 |", "|---|---|---|---|---|---|"]
    for language in ("en", "ko"):
        mine = [r for r in rows if r["language"] == language]
        present = [r for r in mine if names_18(r["research"])]
        absent = [r for r in mine if not names_18(r["research"])]
        lines.append(f"| {language} | {len(mine)} | {len(present)} | "
                     f"{sum(18 in models_named(r['draft']) for r in present)} | {len(absent)} | "
                     f"{sum(18 in models_named(r['draft']) for r in absent)} |")
    lines += ["", "| run | turn | each search: results naming the 18 / results | the draft names |", "|---|---|---|---|"]
    for row in rows:
        searches = "; ".join(
            f"{form_of(s['query'])} {sum(names_18(r['title'] + ' ' + r['summary']) for r in s['results'])}"
            f"/{len(s['results'])}" for s in row["searches"])
        named = ", ".join(map(str, models_named(row["draft"]))) or "none"
        lines.append(f"| {row['run']} | {row['language']} {row['case'][11:-3]} | {searches} | {named} |")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------- search


def search(name: str) -> int:
    from ddgs import DDGS
    from ddgs.engines import ENGINES

    from tools.web_search import WebSearchTool

    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    path = out / "searches.jsonl"
    tool = WebSearchTool()

    def keep(record: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"{record['how']:12s} {record['form']:12s} {len(record['results'])} result(s)"
              + (f"  {record['error']}" if record.get("error") else ""), flush=True)

    for repeat in range(1, REPEATS + 1):
        for form, query in QUERIES.items():
            record = {"how": "as she does", "repeat": repeat, "form": form, "query": query, "results": []}
            try:
                record["results"] = tool.search_web_structured(query, 5)
            except Exception as error:
                record["error"] = f"{type(error).__name__}: {error}"[:200]
            keep(record)
            time.sleep(1.0)
    engines = [key for key in ENGINES["text"] if key not in ("wikipedia", "grokipedia")]
    for engine in engines:
        for form, query in QUERIES.items():
            record = {"how": engine, "repeat": 1, "form": form, "query": query, "results": []}
            try:
                found = DDGS(timeout=10).text(query, max_results=5, backend=engine)
                record["results"] = [{"title": r.get("title", ""), "url": r.get("href", ""),
                                      "summary": r.get("body", "")} for r in found]
            except Exception as error:
                record["error"] = f"{type(error).__name__}: {error}"[:200]
            keep(record)
            time.sleep(1.0)
    return 0


def _searches(name: str) -> list[dict]:
    path = ROOT / name / "searches.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def search_report(name: str) -> str:
    rows = _searches(name)

    def hit(record: dict) -> bool:
        return any(names_18(r["title"] + " " + r["summary"]) for r in record["results"])

    def apple(record: dict) -> bool:
        return any("apple.com" in r["url"] and names_18(r["title"] + " " + r["summary"]) for r in record["results"])

    lines = ["## The searches made again", "",
             f"`tools/web_search.py`, 5 results asked for, {REPEATS} times each "
             "(`as she does`), and once through each engine the library can pick.", "",
             "| the search | query | came back | name the iPhone 18 | ...on apple.com |", "|---|---|---|---|---|"]
    mine = [r for r in rows if r["how"] == "as she does"]
    for form, query in QUERIES.items():
        group = [r for r in mine if r["form"] == form]
        back = [r for r in group if r["results"]]
        lines.append(f"| {form} | `{query}` | {len(back)} of {len(group)} | {sum(map(hit, back))} | "
                     f"{sum(map(apple, back))} |")
    engines = sorted({r["how"] for r in rows} - {"as she does"})
    lines += ["", "Each engine, once: does its result set name the iPhone 18? (`-` came back empty or failed)", "",
              "| engine | " + " | ".join(QUERIES) + " |", "|---|" + "---|" * len(QUERIES)]
    for engine in engines:
        cells = []
        for form in QUERIES:
            record = next((r for r in rows if r["how"] == engine and r["form"] == form), None)
            cells.append("-" if record is None or not record["results"] else ("yes" if hit(record) else "no"))
        lines.append(f"| {engine} | " + " | ".join(cells) + " |")
    lines += ["", "What came back, as she searches (first repeat of each):", ""]
    for form in QUERIES:
        record = next((r for r in mine if r["form"] == form and r["results"]), None)
        if record is None:
            continue
        lines.append(f"- **{form}**")
        lines += [f"  - {'**18** ' if names_18(r['title'] + ' ' + r['summary']) else ''}{r['title'][:90]} "
                  f"({_host(r['url'])})" for r in record["results"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- read


def whole(final: str, references: list[str]) -> str:
    """A recorded prompt with its clipped middle put back, or ''.

    The trace keeps the first two thirds and the last third of a long message
    (core/turn_trace.py). The evidence is in the head; what was cut is the
    list of abilities, which every prompt carries word for word. So it is
    taken from a prompt that was short enough to be kept whole, and both
    ends have to meet exactly or the prompt is not used.
    """
    cut = CUT.search(final)
    if cut is None:
        return final
    head, tail, missing = final[:cut.start()], final[cut.end():], int(cut.group(1))
    for reference in references:
        at = reference.find(head[-200:])
        if at < 0:
            continue
        start = at + 200
        if reference[start + missing:start + missing + 150] == tail[:150]:
            return head + reference[start:start + missing] + tail
    return ""


def prompts() -> tuple[list[dict], int]:
    """(the first-turn prompts whole again, how many could not be)."""
    references = []
    for path in glob.glob(str(TMR / "*" / "rt" / "*" / "turn_trace" / "*.jsonl")):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            final = str((_drafting(json.loads(line)).get("messages") or [{}])[-1].get("content", "")) if line.strip() else ""
            if final and CUT.search(final) is None:
                references.append(final)
    rows, lost = [], 0
    for row in first_turns():
        messages = [dict(m) for m in row["call"].get("messages") or ()]
        final = whole(str(messages[-1].get("content", "")), references) if messages else ""
        if not final or EVIDENCE_BLOCK.search(final) is None:
            lost += 1
            continue
        messages[-1]["content"] = final
        rows.append({**row, "messages": messages, "options": row["call"].get("options") or {},
                     "keep_alive": row["call"].get("keep_alive")})
    return rows, lost


def read(name: str) -> int:
    import ollama

    from evals.phase2 import unload_models

    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    path = out / "drafts.jsonl"
    rows, lost = prompts()
    print(f"{len(rows)} first-turn prompts whole again; {lost} could not be", flush=True)
    korean = [r for r in rows if r["language"] == "ko"]
    english = [r for r in rows if r["language"] == "en"]
    base = next(r for r in korean if len(r["searches"]) == 2)
    jobs = [("as recorded", r, r["messages"]) for r in korean + english]
    for donor in english:
        block = EVIDENCE_BLOCK.search(donor["messages"][-1]["content"]).group(0)
        swapped = [dict(m) for m in base["messages"]]
        swapped[-1]["content"] = EVIDENCE_BLOCK.sub(lambda _m: block, swapped[-1]["content"], count=1)
        jobs.append(("ko question, en evidence", donor, swapped))
    done = set()
    if path.exists():
        done = {(d["model"], d["condition"], d["run"], d["case"], d["draft_number"])
                for d in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    unload_models()
    for model in (M8, M27):
        for condition, row, messages in jobs:
            for number in range(1, DRAFTS + 1):
                if (model, condition, row["run"], row["case"], number) in done:
                    continue
                started = time.monotonic()
                # The window every production call asks for (core/model_context.py). Without
                # it Ollama runs at 4,096 tokens and drops the start of a Korean prompt.
                response = ollama.chat(model=model, messages=messages, stream=False, think=False,
                                       options={**row["options"], "num_ctx": model_context.configured()},
                                       keep_alive="10m")
                text = str(response["message"]["content"])
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "model": model, "condition": condition, "run": row["run"], "case": row["case"],
                        "language": row["language"], "draft_number": number,
                        "evidence_names_18": names_18(row["research"]), "text": text,
                        "prompt_tokens": response.get("prompt_eval_count"),
                        "seconds": round(time.monotonic() - started, 1)}, ensure_ascii=False) + "\n")
                print(f"{'27B' if model == M27 else '8B '} {condition:26s} {row['run']:5s} {row['case'][11:]:12s} "
                      f"#{number} names {models_named(text) or '-'}", flush=True)
    unload_models()
    return 0


def read_report(name: str) -> str:
    drafts = [json.loads(line) for line in (ROOT / name / "drafts.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    lines = ["## The same evidence, read by each model", "",
             "The recorded drafting prompts, whole, with the recorded options; "
             f"{DRAFTS} drafts each. Drafts only.", "",
             "| prompt | evidence names the 18 | model | drafts | name the 18 | name only other models | name none |",
             "|---|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for d in drafts:
        label = (f"{d['language']} turn, as recorded" if d["condition"] == "as recorded" else d["condition"])
        groups[(label, d["evidence_names_18"], "27B" if d["model"] == M27 else "8B")].append(d)
    for (label, present, model), group in sorted(groups.items(), key=lambda item: (item[0][0], not item[0][1], item[0][2])):
        named = [models_named(d["text"]) for d in group]
        lines.append(f"| {label} | {'yes' if present else 'no'} | {model} | {len(group)} | "
                     f"{sum(18 in n for n in named)} | {sum(bool(n) and 18 not in n for n in named)} | "
                     f"{sum(not n for n in named)} |")
    agree = Counter()
    pairs = defaultdict(dict)
    for d in drafts:
        if d["draft_number"] == 1:
            pairs[(d["condition"], d["run"], d["case"])]["27B" if d["model"] == M27 else "8B"] = d
    for key, pair in pairs.items():
        if len(pair) == 2:
            same = (18 in models_named(pair["8B"]["text"])) == (18 in models_named(pair["27B"]["text"]))
            agree[(pair["8B"]["evidence_names_18"], same)] += 1
    lines += ["", "On the same prompt, do the two models agree on whether the 18 is the answer? (first draft of each)", "",
              "| evidence names the 18 | agree | disagree |", "|---|---|---|"]
    for present in (True, False):
        lines.append(f"| {'yes' if present else 'no'} | {agree[(present, True)]} | {agree[(present, False)]} |")
    lines += ["", "Every first draft:", ""]
    for (condition, run, case), pair in sorted(pairs.items()):
        any_one = next(iter(pair.values()))
        lines.append(f"**{condition} / {run} {case[11:]}** (evidence names the 18: "
                     f"{'yes' if any_one['evidence_names_18'] else 'no'})")
        for model in ("8B", "27B"):
            if model in pair:
                lines.append(f"- {model}: " + " ".join(pair[model]["text"].split())[:420])
        lines.append("")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ variants
#
# What a Korean lookup would have to search for its answer not to hang on one
# search. Measured (docs/TURN_MOVE_AB.md, 2026-10-04): the reply names the
# newest model in 17 of 17 Korean turns where the official-source search was led
# by Yahoo, and in 1 of 23 where it was not.

VARIANT_QUERIES = {
    "ko": QUERIES["ko sentence"],
    "en": QUERIES["en keywords"],
    "official": "official source latest iPhone model 2026 as of {date}",
}
# The searches a Korean first turn makes, in the order the evidence lists them.
VARIANTS = {
    "as now: sentence + official": ("ko", "official"),
    "sentence + router's query": ("ko", "en"),
    "sentence + router's query + official": ("ko", "en", "official"),
    "an English turn's: router's query + official": ("en", "official"),
}
ENGINES_COMPARED = ("yahoo", "yandex")


def collect_sets(name: str) -> int:
    """One result set per query per engine. Yahoo on a client that reads as a
    desktop: its page for a phone is one the library cannot read."""
    from datetime import date

    from ddgs.engines import ENGINES

    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    sets = {"date": today, "sets": {}}
    for key, template in VARIANT_QUERIES.items():
        query = template.format(date=today)
        for engine_name in ENGINES_COMPARED:
            found = []
            for _attempt in range(10):
                engine = ENGINES["text"][engine_name](timeout=10)
                try:
                    found = engine.search(query, region="us-en") or []
                except Exception:
                    found = []
                if found:
                    break
                time.sleep(0.5)
            results = [{"title": getattr(r, "title", ""), "url": getattr(r, "href", ""),
                        "summary": getattr(r, "body", "")} for r in found[:5]]
            sets["sets"][f"{key}|{engine_name}"] = {"query": query, "results": results}
            print(f"{key:9s} {engine_name:7s} {len(results)} results | names the 18: "
                  f"{sum(names_18(r['title'] + ' ' + r['summary']) for r in results)}", flush=True)
            time.sleep(1.0)
    (out / "sets.json").write_text(json.dumps(sets, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def _formatted(results: list[dict]) -> str:
    """As tools/web_search.py words a result set."""
    return "\n\n".join(
        f"[{index}] {r['title']}\nSource: {r['url'] or '(source URL unavailable)'}\n"
        f"Snippet: {r['summary'] or '(no snippet available)'}" for index, r in enumerate(results, 1))


def variant_blocks(name: str) -> list[dict]:
    from itertools import product

    sets = json.loads((ROOT / name / "sets.json").read_text(encoding="utf-8"))
    blocks = []
    for label, searches in VARIANTS.items():
        for engines in product(ENGINES_COMPARED, repeat=len(searches)):
            parts = []
            for index, (key, engine) in enumerate(zip(searches, engines), 1):
                chosen = sets["sets"][f"{key}|{engine}"]
                parts.append(f"SEARCH {index}: {chosen['query']}\n{_formatted(chosen['results'])}")
            evidence = "\n\n".join(parts)
            blocks.append({"variant": label, "searches": list(searches), "engines": list(engines),
                           "evidence": evidence,
                           "block": f"CURRENT RETRIEVED EVIDENCE\nAS-OF DATE: {sets['date']}\n{evidence}\n"})
    return blocks


def variants_run(name: str) -> int:
    import ollama

    out = ROOT / name
    path = out / "variant_drafts.jsonl"
    rows, _lost = prompts()
    base = next(r for r in rows if r["language"] == "ko" and len(r["searches"]) == 2)
    done = set()
    if path.exists():
        done = {(d["variant"], tuple(d["engines"]), d["draft_number"])
                for d in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    for block in variant_blocks(name):
        messages = [dict(m) for m in base["messages"]]
        messages[-1]["content"] = EVIDENCE_BLOCK.sub(lambda _m: block["block"].rstrip("\n"),
                                                    messages[-1]["content"], count=1)
        for number in range(1, DRAFTS + 1):
            if (block["variant"], tuple(block["engines"]), number) in done:
                continue
            response = ollama.chat(model=M8, messages=messages, stream=False, think=False,
                                   options={**base["options"], "num_ctx": model_context.configured()},
                                   keep_alive="10m")
            text = str(response["message"]["content"])
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "variant": block["variant"], "searches": block["searches"], "engines": block["engines"],
                    "draft_number": number, "text": text, "evidence_names_18": names_18(block["evidence"]),
                    "prompt_tokens": response.get("prompt_eval_count"),
                    "done_reason": response.get("done_reason")}, ensure_ascii=False) + "\n")
            print(f"{block['variant'][:34]:34s} {'+'.join(block['engines']):22s} #{number} "
                  f"names {models_named(text) or '-'} | {response.get('prompt_eval_count')} tokens", flush=True)
    return 0


def variants_report(name: str, yahoo: tuple = (0.57, 0.92)) -> str:
    """``yahoo``: how often Yahoo leads a search, as production is and with a
    new client tried when it comes back empty."""
    from itertools import product

    drafts = [json.loads(line) for line in (ROOT / name / "variant_drafts.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    sets = json.loads((ROOT / name / "sets.json").read_text(encoding="utf-8"))
    lines = ["## What a Korean lookup would have to search", "",
             "The recorded Korean prompt, whole, with the evidence each set of searches would bring, by which "
             f"engine answered each. The 8B, {DRAFTS} drafts each, at the production context size. Drafts only.", "",
             "The result sets used:", "", "| search | engine | results naming the iPhone 18 |", "|---|---|---|"]
    for key, chosen in sets["sets"].items():
        query, engine = key.split("|")
        lines.append(f"| {query} | {engine} | "
                     f"{sum(names_18(r['title'] + ' ' + r['summary']) for r in chosen['results'])} of "
                     f"{len(chosen['results'])} |")
    lines += ["", "| the searches | each led by | drafts naming the iPhone 18 | prompt tokens |", "|---|---|---|---|"]
    right = {}
    for label, searches in VARIANTS.items():
        for engines in product(ENGINES_COMPARED, repeat=len(searches)):
            group = [d for d in drafts if d["variant"] == label and tuple(d["engines"]) == engines]
            hits = sum(18 in models_named(d["text"]) for d in group)
            right[(label, engines)] = hits / len(group) if group else 0.0
            tokens = max((d["prompt_tokens"] or 0) for d in group) if group else 0
            lines.append(f"| {label} | {', '.join(f'{s} {e}' for s, e in zip(searches, engines))} | "
                         f"{hits} of {len(group)} | {tokens} |")
    lines += ["", "Put together with how often Yahoo leads a search (each search on its own):", "",
              "| the searches | " + " | ".join(f"Yahoo leads {int(p * 100)}%" for p in yahoo) + " |",
              "|---|" + "---|" * len(yahoo)]
    for label, searches in VARIANTS.items():
        cells = []
        for p in yahoo:
            expected = 0.0
            for engines in product(ENGINES_COMPARED, repeat=len(searches)):
                weight = 1.0
                for engine in engines:
                    weight *= p if engine == "yahoo" else 1 - p
                expected += weight * right[(label, engines)]
            cells.append(f"{expected * 100:.0f}% right")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "Every first draft:", ""]
    for d in drafts:
        if d["draft_number"] == 1:
            lines.append(f"- **{d['variant']}** / {', '.join(f'{s} {e}' for s, e in zip(d['searches'], d['engines']))}: "
                         + " ".join(d["text"].split())[:260])
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recorded", action="store_true")
    parser.add_argument("--search", default="")
    parser.add_argument("--read", default="")
    parser.add_argument("--report", nargs=2, metavar=("SEARCHES", "DRAFTS"))
    parser.add_argument("--sets", default="", help="fetch one result set per query per engine")
    parser.add_argument("--variants", default="", help="draft from each set of searches a Korean turn could make")
    parser.add_argument("--variants-report", default="")
    args = parser.parse_args()
    if args.recorded:
        print(recorded())
        return 0
    if args.sets:
        return collect_sets(args.sets)
    if args.variants:
        return variants_run(args.variants)
    if args.variants_report:
        text = variants_report(args.variants_report)
        (ROOT / args.variants_report / "VARIANTS.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    if args.search:
        return search(args.search)
    if args.read:
        return read(args.read)
    if args.report:
        text = ("# Korean and English lookups: what is retrieved, and how it is read\n\n"
                + recorded() + "\n" + search_report(args.report[0]) + "\n" + read_report(args.report[1]))
        (ROOT / args.report[0] / "REPORT.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
