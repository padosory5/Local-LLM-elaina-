"""The shuffled engine order against a fixed one, Yahoo first and Yandex last.

    .venv/Scripts/python.exe -m evals.search_order --search so1
    .venv/Scripts/python.exe -m evals.search_order --turns so1
    .venv/Scripts/python.exe -m evals.search_order --report so1

``tools/web_search.py`` calls the ``ddgs`` library with its defaults, and the
library tries its engines in a new random order on every call. On this machine
two of them answer, and a Korean lookup that draws Yandex gets pages that do
not hold the answer (docs/TURN_MOVE_AB.md, 2026-10-04). This compares the two
orders with everything else as it is:

``shuffled``  the library's own order, as production searches today.
``ordered``   the same engines, the general ones in a fixed order
              (``ORDER``). Wikipedia and Grokipedia stay first, where the
              library's own order always has them.

Nothing in production is changed to run it. The order is set, and every engine
call written down, by a ``sitecustomize`` module this script writes under
``runtime/`` and names on the PYTHONPATH of the processes it starts. So what
is measured is which engine each result came from, not a guess from its host.

``--search``  The searches alone, through ``WebSearchTool`` as she makes them:
              the five forms of the iPhone question, and ordinary English and
              Korean searches. Each query under both orders, turn about.
``--turns``   Whole first turns through the real backend with the flag off:
              the router, both searches, the 8B, the stages. Korean and English
              iPhone questions and two ordinary English ones, under both
              orders, turn about.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ELAINA_TTS", "off")

from evals import sim_learner as sl  # noqa: E402
from evals.lookup_evidence import QUERIES, _host, models_named, names_18, sections  # noqa: E402
from evals.turn_move_shadow import _turns  # noqa: E402

ROOT = PROJECT_ROOT / "runtime" / "evals" / "search_order"
PATCH = ROOT / "patch"
ORDER_ENV = "ELAINA_EVAL_SEARCH_BACKEND"
LOG_ENV = "ELAINA_EVAL_SEARCH_LOG"
ORDER = "wikipedia,grokipedia,yahoo,duckduckgo,brave,google,startpage,mojeek,yandex"
ARMS = ("shuffled", "ordered")
# Production's order with Yahoo asked a second time when it comes back empty.
# Its misses are a results page the library cannot read, and are independent
# from one call to the next (docs/TURN_MOVE_AB.md, 2026-10-04).
TWICE = "wikipedia,grokipedia,yahoo,yahoo,brave,google,startpage,mojeek,yandex,duckduckgo"
# What each arm sends as the engine order. "" sends nothing: production as it is.
ORDERS = {"shuffled": "auto", "ordered": ORDER, "production": "", "once": "", "twice": TWICE,
          # Yahoo asked alone on up to three new clients, then production's order.
          "retry": "retry:3",
          # Production with its own Yahoo-only tries answered "nothing" at once
          # and without a request, so each search is the engine order alone:
          # what production did before tools/web_search.py asked Yahoo first.
          "noretry": "noretry"}
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)

# Ordinary searches: (query, what a result about it has to mention).
ORDINARY = {
    "en": [
        ("weather in Seattle today", ["seattle"]),
        ("NVIDIA stock price today", ["nvidia"]),
        ("Seattle Mariners score last night", ["mariners"]),
        ("latest macOS version 2026", ["macos"]),
        ("who is the CEO of Microsoft", ["microsoft"]),
        ("best ramen restaurants in Seattle", ["ramen"]),
        ("Python 3.14 release date", ["python"]),
        ("flights from Seattle to Seoul price", ["seoul"]),
        ("current population of South Korea", ["korea"]),
        ("cost of iPhone 18 Pro 2026", ["iphone"]),
        ("official source current weather in Portland as of 2026-10-04", ["portland"]),
        ("how long does it take to fly from Seattle to Incheon", ["incheon", "seattle"]),
    ],
    "ko": [
        ("오늘 서울 날씨 어때", ["서울", "seoul"]),
        ("삼성전자 주가", ["삼성전자", "samsung"]),
        ("서울 라멘 맛집 추천", ["라멘", "ramen"]),
        ("요즘 제일 최신 갤럭시 모델이 뭐야", ["갤럭시", "galaxy"]),
    ],
}
IPHONE_REPEATS = 10
ORDINARY_REPEATS = 4

# Whole first turns: (id, what is said, conversations per order).
TURNS = [
    ("ko_iphone", "요즘 제일 최신 아이폰 모델이 뭐야?", 10),
    ("en_iphone", "What's the newest iPhone model right now?", 10),
    ("en_weather", "What's the weather in Seattle today?", 5),
    ("en_price", "How much does the iPhone 18 Pro cost?", 5),
]

SITECUSTOMIZE = '''"""Eval only: written by evals/search_order.py, loaded through PYTHONPATH.

Sets the order the search library tries its engines in, when one is named, and
writes down every engine it asks and what each returned. Production never
names this folder.
"""
import json
import os
import threading
import time

ORDER_ENV = "ELAINA_EVAL_SEARCH_BACKEND"
LOG_ENV = "ELAINA_EVAL_SEARCH_LOG"

if os.environ.get(ORDER_ENV) is not None or os.environ.get(LOG_ENV):
    try:
        import ddgs
        import ddgs.exceptions
        from ddgs.base import BaseSearchEngine
        from ddgs.engines import ENGINES

        _lock = threading.Lock()

        def _keep(record):
            path = os.environ.get(LOG_ENV, "")
            if not path:
                return
            with _lock, open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\\n")

        def _href(result):
            if isinstance(result, dict):
                return str(result.get("href") or result.get("url") or "")
            return str(getattr(result, "href", "") or getattr(result, "url", "") or "")

        _text = ddgs.DDGS.text

        def text(self, query, **kwargs):
            order = os.environ.get(ORDER_ENV, "").strip()
            started, found, error = time.monotonic(), None, ""
            try:
                if order.startswith("retry"):
                    # Yahoo alone, on a new client each time, before the order
                    # production names. An empty answer from it belongs to the
                    # client that asked: the same one asked again stays empty.
                    for _attempt in range(int(order.partition(":")[2] or 2)):
                        try:
                            found = _text(ddgs.DDGS(timeout=10), query, **{**kwargs, "backend": "yahoo"})
                            return found
                        except ddgs.exceptions.DDGSException:
                            continue
                elif order == "noretry":
                    if kwargs.get("backend") == "yahoo":
                        raise ddgs.exceptions.DDGSException("No results found.")
                elif order:
                    kwargs["backend"] = order
                found = _text(self, query, **kwargs)
                return found
            except Exception as failure:
                error = f"{type(failure).__name__}: {failure}"
                raise
            finally:
                _keep({"kind": "search", "query": query, "order": order or "auto",
                       "urls": [_href(r) for r in found or ()],
                       "seconds": round(time.monotonic() - started, 2), "error": error[:200],
                       "at": time.time()})

        ddgs.DDGS.text = text

        def _watched(inner):
            def search(self, query, *args, **kwargs):
                started, found, error = time.monotonic(), None, ""
                try:
                    found = inner(self, query, *args, **kwargs)
                    return found
                except Exception as failure:
                    error = f"{type(failure).__name__}: {failure}"
                    raise
                finally:
                    _keep({"kind": "engine", "engine": str(getattr(self, "name", type(self).__name__)),
                           "query": query, "urls": [_href(r) for r in found or ()],
                           "seconds": round(time.monotonic() - started, 2), "error": error[:200],
                           "at": time.time()})
            return search

        BaseSearchEngine.search = _watched(BaseSearchEngine.search)
        for _engine in set(ENGINES["text"].values()):
            if "search" in vars(_engine):
                _engine.search = _watched(_engine.search)
    except Exception as failure:  # an eval hook never takes a process down
        print(f"[search_order] could not install: {type(failure).__name__}: {failure}")
'''


def write_patch() -> Path:
    PATCH.mkdir(parents=True, exist_ok=True)
    (PATCH / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    return PATCH


def _set_order(arm: str) -> None:
    os.environ[ORDER_ENV] = ORDERS[arm]


# -------------------------------------------------------------- reading


def _log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def responsible(search: dict, engines: list[dict]) -> dict:
    """For one finished search: which engines answered, and whose results it kept.

    The engine calls are the ones for the same query made while the search
    ran. A kept result belongs to every engine that returned its address.
    """
    mine = [e for e in engines if e["query"] == search["query"]
            and search["at"] - search["seconds"] - 1.0 <= e["at"] <= search["at"] + 1.0]
    answered = [e["engine"] for e in mine if e["urls"]]
    kept = Counter()
    for url in search["urls"]:
        for engine in mine:
            if url in engine["urls"]:
                kept[engine["engine"]] += 1
    lead = kept.most_common(1)[0][0] if kept else ""
    return {"asked": [e["engine"] for e in mine], "answered": answered, "kept": dict(kept), "lead": lead}


def _general(lead: str) -> str:
    return lead if lead not in ("wikipedia", "grokipedia") else lead


# --------------------------------------------------------------- search


def _load_patch() -> None:
    spec = importlib.util.spec_from_file_location("search_order_patch", write_patch() / "sitecustomize.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def search(name: str, arms: tuple = ARMS) -> int:
    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    log = out / "engines.jsonl"
    os.environ[LOG_ENV] = str(log)
    os.environ[ORDER_ENV] = ""
    _load_patch()
    from tools.web_search import WebSearchTool

    tool = WebSearchTool()
    path = out / "searches.jsonl"
    plan = []
    for repeat in range(1, IPHONE_REPEATS + 1):
        for form, query in QUERIES.items():
            plan.append(("iphone", form, query, [], repeat))
        if repeat <= ORDINARY_REPEATS:
            for language, group in ORDINARY.items():
                for query, about in group:
                    plan.append((f"ordinary {language}", query, query, about, repeat))
    for number, (group, label, query, about, repeat) in enumerate(plan, 1):
        # Turn about, so neither order always goes first on a query.
        for arm in (arms if (repeat + number) % 2 else arms[::-1]):
            _set_order(arm)
            started = time.monotonic()
            record = {"arm": arm, "group": group, "label": label, "query": query, "about": about,
                      "repeat": repeat, "results": [], "error": ""}
            try:
                record["results"] = tool.search_web_structured(query, 5)
            except Exception as error:
                record["error"] = f"{type(error).__name__}: {error}"[:200]
            record["seconds"] = round(time.monotonic() - started, 2)
            record["at"] = time.time()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            time.sleep(0.8)
        if number % 20 == 0:
            print(f"{number}/{len(plan)} queries", flush=True)
    return 0


def search_report(name: str) -> str:
    out = ROOT / name
    rows = _log(out / "searches.jsonl")
    log = _log(out / "engines.jsonl")
    finished = [r for r in log if r["kind"] == "search"]
    engines = [r for r in log if r["kind"] == "engine"]
    ARMS = tuple(dict.fromkeys(r["arm"] for r in rows))
    for row in rows:
        match = next((s for s in finished if s["query"] == row["query"] and abs(s["at"] - row["at"]) < 2.0), None)
        row["who"] = responsible(match, engines) if match else {"asked": [], "answered": [], "kept": {}, "lead": ""}

    def text(result: dict) -> str:
        return f"{result['title']} {result['summary']}"

    lines = ["## The searches alone", "",
             f"Through `WebSearchTool`, 5 results asked for. Each query under both orders, turn about. "
             f"`ordered` is `{ORDER}`.", "",
             "### The iPhone question", "",
             f"{IPHONE_REPEATS} searches per form per order.", "",
             "| form | order | came back | name the iPhone 18 | top result names it | "
             "led by Yahoo | led by Yandex |", "|---|---|---|---|---|---|---|"]
    iphone = [r for r in rows if r["group"] == "iphone"]
    for form in QUERIES:
        for arm in ARMS:
            group = [r for r in iphone if r["label"] == form and r["arm"] == arm]
            back = [r for r in group if r["results"]]
            lines.append(
                f"| {form} | {arm} | {len(back)} of {len(group)} | "
                f"{sum(any(names_18(text(x)) for x in r['results']) for r in back)} | "
                f"{sum(names_18(text(r['results'][0])) for r in back)} | "
                f"{sum(r['who']['lead'] == 'yahoo' for r in back)} | "
                f"{sum(r['who']['lead'] == 'yandex' for r in back)} |")
    lines += ["", "| order | searches | name the iPhone 18 | top result names it |", "|---|---|---|---|"]
    for arm in ARMS:
        group = [r for r in iphone if r["arm"] == arm]
        lines.append(f"| {arm} | {len(group)} | "
                     f"{sum(any(names_18(text(x)) for x in r['results']) for r in group)} | "
                     f"{sum(bool(r['results']) and names_18(text(r['results'][0])) for r in group)} |")
    lines += ["", "By the engine that led the result set (both orders together):", "",
              "| led by | result sets | name the iPhone 18 | do not |", "|---|---|---|---|"]
    by_lead = defaultdict(list)
    for r in iphone:
        if r["results"]:
            by_lead[r["who"]["lead"] or "(unmatched)"].append(any(names_18(text(x)) for x in r["results"]))
    for lead, hits in sorted(by_lead.items(), key=lambda item: -len(item[1])):
        lines.append(f"| {lead} | {len(hits)} | {sum(hits)} | {len(hits) - sum(hits)} |")

    lines += ["", "### Ordinary searches", "",
              f"{ORDINARY_REPEATS} searches per query per order. \"About it\" is a result whose title or "
              "snippet mentions the query's subject. \"Russian\" is a result with a Cyrillic title.", "",
              "| searches | order | came back | results | about it | Russian | median seconds | slowest | "
              "led by Yahoo | led by Yandex |", "|---|---|---|---|---|---|---|---|---|---|"]
    for group_name in ("ordinary en", "ordinary ko"):
        for arm in ARMS:
            group = [r for r in rows if r["group"] == group_name and r["arm"] == arm]
            back = [r for r in group if r["results"]]
            results = [(r, x) for r in back for x in r["results"]]
            about = sum(any(term in text(x).casefold() for term in r["about"]) for r, x in results)
            russian = sum(bool(CYRILLIC.search(x["title"])) for _r, x in results)
            seconds = [r["seconds"] for r in group]
            lines.append(
                f"| {group_name} | {arm} | {len(back)} of {len(group)} | {len(results)} | {about} | {russian} | "
                f"{statistics.median(seconds):.1f} | {max(seconds):.1f} | "
                f"{sum(r['who']['lead'] == 'yahoo' for r in back)} | "
                f"{sum(r['who']['lead'] == 'yandex' for r in back)} |")
    lines += ["", "Each ordinary query: results about it / results, and who led, per order.", "",
              "| query | shuffled | led by | ordered | led by |", "|---|---|---|---|---|"]
    for group_name in ("ordinary en", "ordinary ko"):
        for query in dict.fromkeys(r["query"] for r in rows if r["group"] == group_name):
            cells = []
            for arm in ARMS:
                group = [r for r in rows if r["query"] == query and r["arm"] == arm]
                results = [(r, x) for r in group for x in r["results"]]
                about = sum(any(term in text(x).casefold() for term in r["about"]) for r, x in results)
                leads = Counter(r["who"]["lead"] or "-" for r in group if r["results"])
                cells += [f"{about}/{len(results)}", ", ".join(f"{k} {v}" for k, v in leads.most_common()) or "nothing back"]
            lines.append(f"| {query} | " + " | ".join(cells) + " |")
    lines += ["", "Which engines answered at all, when asked (every search above):", "",
              "| engine | asked | returned results | median seconds |", "|---|---|---|---|"]
    asked = defaultdict(list)
    for e in engines:
        asked[e["engine"]].append(e)
    for engine, calls in sorted(asked.items(), key=lambda item: -len(item[1])):
        lines.append(f"| {engine} | {len(calls)} | {sum(bool(c['urls']) for c in calls)} | "
                     f"{statistics.median(c['seconds'] for c in calls):.1f} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- turns


def turns(name: str, arms: tuple = ARMS) -> int:
    from evals import run as runner
    from evals.phase2 import unload_models

    if runner.port_open():
        print("Port 8765 is in use -- is Elaina already running? Close it first.")
        return 1
    out = ROOT / name / "turns"
    out.mkdir(parents=True, exist_ok=True)
    patch = write_patch()
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()
    (out / "meta.json").write_text(json.dumps({
        "arm": "current", "env": sl.ARMS["current"], "order": ORDER, "git_head": head,
        "uncommitted": [line[3:] for line in dirty],
        "started_at": datetime.now().isoformat(timespec="seconds")}, indent=1, ensure_ascii=False), encoding="utf-8")
    path = out / "dialogues.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    plan = []
    for round_number in range(1, max(n for _c, _s, n in TURNS) + 1):
        for case, said, repeats in TURNS:
            if round_number <= repeats:
                for arm in (arms if round_number % 2 else arms[::-1]):
                    plan.append((f"{case}-{arm}-{round_number}", case, said, arm))
    unload_models()
    python_path = os.pathsep.join(filter(None, [str(patch), os.environ.get("PYTHONPATH", "")]))
    for number, (ident, case, said, arm) in enumerate(plan, 1):
        if ident in done:
            continue
        runtime = out / "rt" / ident
        env = {**sl.ARMS["current"], "PYTHONPATH": python_path,
               LOG_ENV: str(out / "logs" / f"{ident}.engines.jsonl"),
               ORDER_ENV: ORDERS[arm]}
        (out / "logs").mkdir(parents=True, exist_ok=True)
        backend = sl.Elaina(runtime, out / "logs" / f"{ident}.log", env)
        started = time.monotonic()
        try:
            reply, seconds = backend.reply(said)
        finally:
            backend.close()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": ident, "case": case, "arm": arm, "said": said, "reply": reply,
                                     "seconds": seconds}, ensure_ascii=False) + "\n")
        print(f"[{number}/{len(plan)}] {ident}: {time.monotonic() - started:.0f}s", flush=True)
    return 0


def turn_rows(name: str) -> list[dict]:
    out = ROOT / name / "turns"
    rows = []
    for line in (out / "dialogues.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        paths = glob.glob(str(out / "rt" / row["id"] / "turn_trace" / "*.jsonl"))
        turn = _turns(paths[0])[0] if paths else {}
        research = str((turn.get("evidence") or {}).get("research") or "")
        searches = sections(research)
        log = _log(out / "logs" / f"{row['id']}.engines.jsonl")
        engines = [r for r in log if r["kind"] == "engine"]
        # A search is every call to the library for one query: production asks
        # Yahoo alone, on new connections, before the engine order. What it
        # returned is the last call that came back with results; what it took
        # is from the first call's start to that one's end.
        calls: dict[str, list] = {}
        for record in log:
            if record["kind"] == "search":
                calls.setdefault(record["query"], []).append(record)
        finished = []
        for query, group in calls.items():
            final = next((c for c in reversed(group) if c["urls"]), group[-1])
            started = group[0]["at"] - group[0]["seconds"]
            finished.append({**final, "seconds": round(final["at"] - started, 2), "library_calls": len(group),
                             "yahoo_requests": sum(1 for e in engines
                                                   if e["engine"] == "yahoo" and e["query"] == query)})
        who = [responsible(s, engines) for s in finished]
        route = (turn.get("context") or {}).get("route") or {}
        rows.append({
            **row, "searches": searches, "research": research, "searched": bool(research),
            "draft": str((turn.get("draft") or {}).get("text") or ""), "display": str(turn.get("display") or ""),
            "intent": route.get("intent", ""), "router_query": route.get("search_query", ""),
            "leads": [w["lead"] for w in who], "finished": finished, "who": who,
            "search_seconds": [s["seconds"] for s in finished],
            "changed": [s.get("name") for s in turn.get("steps") or () if s.get("changed")],
        })
    return rows


def turns_report(name: str) -> str:
    rows = turn_rows(name)
    ARMS = tuple(dict.fromkeys(r["arm"] for r in rows))

    def top(row: dict) -> bool:
        first = row["searches"][0]["results"][:1] if row["searches"] else []
        return bool(first) and names_18(first[0]["title"] + " " + first[0]["summary"])

    lines = ["## Whole first turns", "",
             "The real backend, flag off: the router, the searches, the 8B, the stages. One fresh backend per "
             "conversation, the two orders turn about.", "",
             "### The iPhone question", "",
             "| turn | order | conversations | searched | evidence names the iPhone 18 | top result names it | "
             "the 8B's draft names it | the reply names it |", "|---|---|---|---|---|---|---|---|"]
    for case in ("ko_iphone", "en_iphone"):
        for arm in ARMS:
            group = [r for r in rows if r["case"] == case and r["arm"] == arm]
            lines.append(
                f"| {case[:2]} | {arm} | {len(group)} | {sum(r['searched'] for r in group)} | "
                f"{sum(names_18(r['research']) for r in group)} | {sum(top(r) for r in group)} | "
                f"{sum(18 in models_named(r['draft']) for r in group)} | "
                f"{sum(18 in models_named(r['display']) for r in group)} |")
    lines += ["", "Who led each search of those turns, and whether its results name the iPhone 18:", "",
              "| turn | order | led by | searches | name the iPhone 18 | do not |", "|---|---|---|---|---|---|"]
    for case in ("ko_iphone", "en_iphone"):
        for arm in ARMS:
            tally = defaultdict(list)
            for r in rows:
                if r["case"] != case or r["arm"] != arm:
                    continue
                for search_, who in zip(r["finished"], r["who"]):
                    section = next((s for s in r["searches"] if s["query"] == search_["query"]), None)
                    hit = bool(section) and any(names_18(x["title"] + " " + x["summary"]) for x in section["results"])
                    tally[who["lead"] or "(nothing kept)"].append(hit)
            for lead, hits in sorted(tally.items(), key=lambda item: -len(item[1])):
                lines.append(f"| {case[:2]} | {arm} | {lead} | {len(hits)} | {sum(hits)} | {len(hits) - sum(hits)} |")
    missing = [r for r in rows if r["case"] in ("ko_iphone", "en_iphone") and r["searched"]
               and not names_18(r["research"])]
    by = Counter()
    for r in missing:
        leads = {lead for lead in r["leads"] if lead}
        by["only Yandex led" if leads == {"yandex"} else
           "Yandex and another" if "yandex" in leads else
           ", ".join(sorted(leads)) or "nothing kept"] += 1
    lines += ["", f"Turns whose evidence does not name the iPhone 18: {len(missing)}. Who led their searches: "
              + ("; ".join(f"{k} {v}" for k, v in by.most_common()) or "-") + ".", "",
              "### Ordinary English turns", "",
              "| turn | order | conversations | searched | median search seconds | led by |", "|---|---|---|---|---|---|"]
    for case in ("en_weather", "en_price"):
        for arm in ARMS:
            group = [r for r in rows if r["case"] == case and r["arm"] == arm]
            seconds = [s for r in group for s in r["search_seconds"]]
            leads = Counter(lead or "-" for r in group for lead in r["leads"])
            lines.append(f"| {case} | {arm} | {len(group)} | {sum(r['searched'] for r in group)} | "
                         f"{statistics.median(seconds) if seconds else 0:.1f} | "
                         f"{', '.join(f'{k} {v}' for k, v in leads.most_common()) or '-'} |")
    lines += ["", "### Every reply", ""]
    for case, _said, _n in TURNS:
        lines.append(f"**{case}**")
        for arm in ARMS:
            for r in [x for x in rows if x["case"] == case and x["arm"] == arm]:
                mark = ""
                if case.endswith("iphone"):
                    mark = f"[evidence {'18' if names_18(r['research']) else '--'}; led by {'+'.join(x or '-' for x in r['leads']) or '-'}] "
                else:
                    mark = f"[led by {'+'.join(x or '-' for x in r['leads']) or 'no search'}] "
                lines.append(f"- {arm} {r['id'].rsplit('-', 1)[1]}: {mark}" + " ".join(r["display"].split())[:300])
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--search", default="")
    parser.add_argument("--turns", default="")
    parser.add_argument("--production", action="store_true",
                        help="with --turns: one arm, production as it is, the engine calls written down")
    parser.add_argument("--arms", default="", help="comma-separated arms: " + ", ".join(ORDERS))
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    arms = tuple(a for a in args.arms.split(",") if a) or (("production",) if args.production else ARMS)
    if args.search:
        return search(args.search, arms)
    if args.turns:
        return turns(args.turns, arms)
    if args.report:
        parts = [f"# Search engine order: shuffled against Yahoo first ({args.report})\n"]
        if (ROOT / args.report / "searches.jsonl").exists():
            parts.append(search_report(args.report))
        if (ROOT / args.report / "turns" / "dialogues.jsonl").exists():
            parts.append(turns_report(args.report))
        text = "\n".join(parts)
        (ROOT / args.report / "REPORT.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
