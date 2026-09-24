"""Read the turn records back: one turn in full, or every stage in aggregate.

core/turn_trace.py writes one record per turn to runtime/turn_trace/. This
answers the questions those records exist for, without re-running anything:

    # the last turn, draft to speech, with every stage that changed it
    .venv/Scripts/python.exe scripts/turn_trace_report.py last

    # the last five turns, one line each
    .venv/Scripts/python.exe scripts/turn_trace_report.py last 5

    # one turn by id (a prefix is enough), or by what was said
    .venv/Scripts/python.exe scripts/turn_trace_report.py turn 3f2a
    .venv/Scripts/python.exe scripts/turn_trace_report.py find "taylor"

    # every stage: how often it ran, how often it changed the reply
    .venv/Scripts/python.exe scripts/turn_trace_report.py stages --days 7

    # every model call: who made it, how long it took, tokens per second
    .venv/Scripts/python.exe scripts/turn_trace_report.py calls --days 7

    # the failure bank: turns a stage rewrote, one line per finding
    .venv/Scripts/python.exe scripts/turn_trace_report.py bank

``--dir`` reads another runtime (for example a verification run started
with ELAINA_RUNTIME_ROOT); ``--days`` widens the window from today.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import RUNTIME_ROOT  # noqa: E402


# ------------------------------------------------------------------ loading


def load(directory: Path, days: int) -> list[dict]:
    """Every record in the window, oldest first."""
    if not directory.exists():
        return []
    since = (datetime.now() - timedelta(days=max(1, days) - 1)).date()
    records: list[dict] = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < since:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    records.sort(key=lambda record: record.get("started_at", ""))
    return records


# ------------------------------------------------------------------ one turn


def _indent(text, prefix: str = "    ", width: int = 100) -> str:
    said = str(text or "").strip() or "(empty)"
    lines = []
    for paragraph in said.splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, width=width) or [""])
    return "\n".join(prefix + line for line in lines)


def _route(record: dict) -> dict:
    route = (record.get("context") or {}).get("route")
    return route if isinstance(route, dict) else {}


def _seconds(value) -> str:
    return f"{value:.2f}s" if isinstance(value, (int, float)) else "-"


def show_turn(record: dict) -> None:
    context = record.get("context") or {}
    route = _route(record)
    limits = context.get("limits") or {}
    state = context.get("state") or {}
    chain = "chain ok" if record.get("chain_ok") else "CHAIN BROKEN"
    print(
        f"Turn {record.get('turn_id')}  {record.get('started_at')}  "
        f"session {record.get('session_id')}  "
        f"{_seconds(record.get('elapsed'))}  {chain}  "
        f"outcome={record.get('outcome')}"
    )
    print(f"Said:   {record.get('user_input')!r}")
    resolved = context.get("user_input_resolved")
    if resolved and resolved != record.get("user_input"):
        print(f"Read as: {resolved!r}")
    if route:
        print(
            f"Route:  {route.get('intent')} ({route.get('confidence')})  "
            f"normalized={route.get('normalized_request')!r}  "
            f"act={context.get('act')}  "
            f"limits={limits.get('max_words')}w/{limits.get('max_sentences')}s"
            f"{' detailed' if limits.get('detailed') else ''}  "
            f"lang={context.get('language')}"
        )
    if state:
        print(f"State:  {json.dumps(state, ensure_ascii=False)}")
    evidence = record.get("evidence") or {}
    if evidence:
        parts = []
        if evidence.get("search_queries"):
            parts.append(f"queries={evidence['search_queries']}")
        for key in ("research", "clock", "calculation"):
            if evidence.get(key):
                parts.append(f"{key}={len(str(evidence[key]))} chars")
        print(f"Evidence: {'  '.join(parts)}")
        if evidence.get("clock"):
            print(_indent(evidence["clock"]))

    draft = record.get("draft") or {}
    print()
    print(f"Draft ({draft.get('source', '?')}"
          f"{', ' + draft['model'] if draft.get('model') else ''}):")
    print(_indent(draft.get("text")))

    steps = record.get("steps") or []
    changed = [step for step in steps if step.get("changed")]
    print()
    print(f"Stages: {len(steps)} ran, {len(changed)} changed the reply")
    for step in steps:
        if not step.get("changed"):
            continue
        marker = "GAP " if step.get("gap") else ""
        calls = step.get("model_calls")
        call_note = f"  model calls {calls}" if calls else ""
        print(f"  [{step.get('i'):>2}] {marker}{step.get('name')}  "
              f"+{_seconds(step.get('dt'))}{call_note}")
        print("       before:")
        print(_indent(step.get("before"), prefix="         "))
        print("       after:")
        print(_indent(step.get("after"), prefix="         "))
        for reason in step.get("log") or ():
            print(f"       why: {reason}")
    order = [step.get("name") for step in steps if not step.get("gap")]
    if order:
        print("  order: " + ", ".join(order))

    print()
    print("Displayed:")
    print(_indent(record.get("display")))
    for entry in record.get("speech") or ():
        chunks = entry.get("chunks") or []
        print(f"Spoken ({len(chunks)} chunk{'s' if len(chunks) != 1 else ''}):")
        print(_indent(entry.get("spoken")))

    calls = record.get("model_calls") or []
    if calls:
        total = sum(call.get("latency", 0) for call in calls)
        print()
        print(f"Model calls: {len(calls)}, {total:.2f}s")
        for call in calls:
            speed = call.get("tokens_per_second")
            print(
                f"  #{call.get('i'):<2} {call.get('caller', ''):<52} "
                f"{call.get('model', ''):<14} {_seconds(call.get('latency')):>7} "
                f"ttft {_seconds(call.get('ttft')):>6}  "
                f"{(str(speed) + ' tok/s') if speed else '':>11}"
                f"{'  ERROR ' + call['error'] if call.get('error') else ''}"
            )
    timings = record.get("timings") or {}
    if timings:
        shown = "  ".join(
            f"{name}={value:.2f}s" for name, value in timings.items()
            if isinstance(value, (int, float)) and value
        )
        print()
        print(f"Timings: {shown}")
    if record.get("error"):
        print(f"Error: {record['error']}")


def one_line(record: dict) -> str:
    steps = record.get("steps") or []
    changed = [step["name"] for step in steps if step.get("changed")]
    return (
        f"{record.get('turn_id')}  {str(record.get('started_at', ''))[11:19]}  "
        f"{_route(record).get('intent', '?'):<18} "
        f"{_seconds(record.get('elapsed')):>7}  "
        f"{'' if record.get('chain_ok') else 'BROKEN '}"
        f"{str(record.get('user_input', ''))[:50]!r}"
        f"{'  changed: ' + ', '.join(changed) if changed else ''}"
    )


# -------------------------------------------------------------- aggregates


def show_stages(records: list[dict]) -> None:
    stats: dict[str, dict] = {}
    for record in records:
        for step in record.get("steps") or ():
            if step.get("gap"):
                continue
            entry = stats.setdefault(step["name"], {
                "runs": 0, "changed": 0, "positions": [], "dt": [],
                "calls": 0, "turns": set(),
            })
            entry["runs"] += 1
            entry["changed"] += 1 if step.get("changed") else 0
            entry["positions"].append(step.get("i", 0))
            entry["dt"].append(step.get("dt", 0.0) or 0.0)
            entry["calls"] += len(step.get("model_calls") or ())
            entry["turns"].add(record.get("turn_id"))
    turns = len(records)
    broken = sum(1 for record in records if not record.get("chain_ok"))
    print(f"{turns} turns, {broken} with a broken chain\n")
    print(f"{'stage':<26}{'turns':>7}{'runs':>7}{'changed':>9}{'rate':>8}"
          f"{'p50 dt':>9}{'max dt':>9}{'model calls':>13}")
    ordered = sorted(stats.items(), key=lambda item: median(item[1]["positions"]))
    for name, entry in ordered:
        rate = entry["changed"] / entry["runs"] if entry["runs"] else 0
        print(
            f"{name:<26}{len(entry['turns']):>7}{entry['runs']:>7}"
            f"{entry['changed']:>9}{rate:>8.1%}"
            f"{median(entry['dt']):>8.2f}s{max(entry['dt']):>8.2f}s"
            f"{entry['calls']:>13}"
        )


def show_calls(records: list[dict]) -> None:
    by_caller: dict[str, list[dict]] = {}
    for record in records:
        for call in record.get("model_calls") or ():
            by_caller.setdefault(call.get("caller", "?"), []).append(call)
    print(f"{'caller':<56}{'calls':>6}{'p50 s':>8}{'max s':>8}"
          f"{'p50 ttft':>10}{'tok/s':>8}{'errors':>8}")
    ordered = sorted(
        by_caller.items(),
        key=lambda item: -sum(call.get("latency", 0) for call in item[1]),
    )
    for caller, calls in ordered:
        latencies = [call.get("latency", 0) for call in calls]
        ttfts = [call["ttft"] for call in calls if "ttft" in call]
        speeds = [call["tokens_per_second"] for call in calls
                  if call.get("tokens_per_second")]
        errors = sum(1 for call in calls if call.get("error"))
        print(
            f"{caller[:55]:<56}{len(calls):>6}{median(latencies):>8.2f}"
            f"{max(latencies):>8.2f}"
            f"{(f'{median(ttfts):.2f}' if ttfts else '-'):>10}"
            f"{(f'{median(speeds):.0f}' if speeds else '-'):>8}{errors:>8}"
        )


def _where_they_differ(before: str, after: str, context: int = 25) -> tuple[str, str]:
    """The two texts from just before the first difference to the last.

    A guard usually changes a clause at the end of a long sentence; showing
    the first sixty characters of both sides showed the same sixty twice.
    """
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    end = 0
    while (end < min(len(before), len(after)) - start
           and before[-1 - end] == after[-1 - end]):
        end += 1
    left = max(0, start - context)
    lead = "..." if left else ""

    def cut(text: str) -> str:
        stop = len(text) - end + min(end, context)
        tail = "..." if stop < len(text) else ""
        return lead + text[left:stop] + tail

    return cut(before), cut(after)


def show_bank(directory: Path, days: int) -> None:
    for finding in load(directory, days):
        print(
            f"{finding.get('turn_id')}  {str(finding.get('started_at', ''))[11:19]}  "
            f"{finding.get('intent', '?'):<18} {str(finding.get('user_input', ''))[:60]!r}"
        )
        for step in finding.get("findings") or ():
            before, after = _where_they_differ(
                str(step.get("before", "")), str(step.get("after", "")),
            )
            print(f"    [{step.get('i'):>2}] {step.get('name')}:")
            print(f"         - {before!r}")
            print(f"         + {after!r}")
            for reason in step.get("log") or ():
                print(f"         why: {reason}")


# ----------------------------------------------------------------------- cli


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=(
        "last", "turn", "find", "stages", "calls", "bank",
    ))
    parser.add_argument("argument", nargs="?", default="")
    parser.add_argument("--dir", default="",
                        help="a runtime directory other than this one's")
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()

    runtime = Path(args.dir) if args.dir else Path(RUNTIME_ROOT)
    traces = runtime / "turn_trace"
    if args.command == "bank":
        show_bank(runtime / "failure_bank", args.days)
        return 0

    records = load(traces, args.days)
    if not records:
        print(f"No turn records in {traces} for the last {args.days} day(s).")
        return 1

    if args.command == "last":
        count = int(args.argument or 1)
        if count == 1:
            show_turn(records[-1])
        else:
            for record in records[-count:]:
                print(one_line(record))
    elif args.command == "turn":
        wanted = [r for r in records if str(r.get("turn_id", "")).startswith(args.argument)]
        if not wanted:
            print(f"No turn starting with {args.argument!r}.")
            return 1
        for record in wanted:
            show_turn(record)
            print("\n" + "=" * 100 + "\n")
    elif args.command == "find":
        needle = args.argument.casefold()
        wanted = [r for r in records if needle in str(r.get("user_input", "")).casefold()]
        for record in wanted:
            print(one_line(record))
        if len(wanted) == 1:
            print()
            show_turn(wanted[0])
    elif args.command == "stages":
        show_stages(records)
    elif args.command == "calls":
        show_calls(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
