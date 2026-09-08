"""Score a dogfood transcript against the conversation-quality failure classes.

The other half of ``live_dogfood_conversation.py``. That script produces
what she said; this one says how much of it sounded like an interface.

Every number here comes from :mod:`brain.conversation_style`, which is the
same module the running system consults before it speaks. That is
deliberate and it is the only way the numbers mean anything: a quality
metric that measures something the product does not enforce will drift
away from it, and then a green report and a robotic assistant can be true
at the same time.

Usage::

    .venv/Scripts/python.exe scripts/conversation_quality_report.py \
        runtime/a1_baseline_*.json

    # before/after on the same turns
    .venv/Scripts/python.exe scripts/conversation_quality_report.py \
        --before runtime/a1_baseline_everyday.json \
        --after  runtime/a1_after_everyday.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

from brain.conversation_style import (  # noqa: E402
    ANSWER,
    FAILURE_CLASSES,
    RoboticTells,
)


def _acts_from_harness() -> dict:
    """The declared act for every turn, so old transcripts score too."""
    try:
        from scripts.live_dogfood_conversation import ARCS
    except Exception:  # pragma: no cover - report must still run
        return {}
    return {
        (name, index): act
        for name, arc in ARCS.items()
        for index, (_, act) in enumerate(arc["turns"], start=1)
    }


def score(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    declared = _acts_from_harness()
    counts: Counter = Counter()
    turns: list[dict] = []
    previous = ""
    earlier: list[str] = []
    for record in data["turns"]:
        reply = str(record.get("reply") or "")
        act = record.get("act") or declared.get(
            (data.get("arc", ""), record.get("turn", 0)), ANSWER,
        )
        findings = RoboticTells.inspect(
            reply,
            act=act,
            user_input=str(record.get("user") or ""),
            previous_reply=previous,
            earlier_replies=tuple(earlier),
        )
        classes = sorted({finding.failure for finding in findings})
        for name in classes:
            counts[name] += 1
        turns.append({
            "turn": record.get("turn"),
            "act": act,
            "user": record.get("user"),
            "reply": reply,
            "classes": classes,
            "evidence": [f.evidence for f in findings],
        })
        if previous:
            earlier.append(previous)
        previous = reply
    clean = sum(1 for turn in turns if not turn["classes"])
    return {
        "arc": data.get("arc", path.stem),
        "path": str(path),
        "turns": turns,
        "counts": dict(counts),
        "total_turns": len(turns),
        "clean_turns": clean,
    }


def _print_report(result: dict, *, verbose: bool) -> None:
    total = result["total_turns"]
    clean = result["clean_turns"]
    rate = (clean / total * 100) if total else 0.0
    print(f"\n### {result['arc']}  ({result['path']})")
    print(f"    turns {total}   clean {clean}   "
          f"clean rate {rate:.0f}%")
    if result["counts"]:
        print("    failures by class:")
        for name, count in sorted(
            result["counts"].items(), key=lambda pair: (-pair[1], pair[0]),
        ):
            print(f"      {name:26s} {count}")
    if verbose:
        for turn in result["turns"]:
            if not turn["classes"]:
                continue
            print(f"\n    [{turn['turn']}] ({turn['act']}) "
                  f"USER: {turn['user']}")
            print(f"        ELAINA: {turn['reply']}")
            print(f"        -> {', '.join(turn['classes'])}")
            for evidence in turn["evidence"]:
                print(f"           · {evidence}")


def _totals(results: list[dict]) -> tuple[int, int, Counter]:
    counts: Counter = Counter()
    total = clean = 0
    for result in results:
        total += result["total_turns"]
        clean += result["clean_turns"]
        counts.update(result["counts"])
    return total, clean, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcripts", nargs="*", default=[])
    parser.add_argument("--before", action="append", default=[])
    parser.add_argument("--after", action="append", default=[])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.before or args.after:
        before = [score(Path(p)) for p in args.before]
        after = [score(Path(p)) for p in args.after]
        for label, group in (("BEFORE", before), ("AFTER", after)):
            print(f"\n{'=' * 68}\n{label}\n{'=' * 68}")
            for result in group:
                _print_report(result, verbose=args.verbose)
        b_total, b_clean, b_counts = _totals(before)
        a_total, a_clean, a_counts = _totals(after)
        print(f"\n{'=' * 68}\nBEFORE / AFTER\n{'=' * 68}")
        print(f"clean turns   {b_clean}/{b_total} "
              f"({b_clean / b_total * 100 if b_total else 0:.0f}%)"
              f"   ->   {a_clean}/{a_total} "
              f"({a_clean / a_total * 100 if a_total else 0:.0f}%)")
        print(f"\n{'failure class':28s}{'before':>8s}{'after':>8s}")
        for name in FAILURE_CLASSES:
            before_count, after_count = b_counts.get(name, 0), a_counts.get(name, 0)
            if before_count or after_count:
                print(f"{name:28s}{before_count:>8d}{after_count:>8d}")
        print(f"{'TOTAL':28s}{sum(b_counts.values()):>8d}"
              f"{sum(a_counts.values()):>8d}")
        return 0

    results = [score(Path(p)) for p in args.transcripts]
    for result in results:
        _print_report(result, verbose=args.verbose)
    total, clean, counts = _totals(results)
    print(f"\n{'=' * 68}")
    print(f"ALL ARCS: {clean}/{total} clean "
          f"({clean / total * 100 if total else 0:.0f}%), "
          f"{sum(counts.values())} finding(s)")
    for name, count in sorted(counts.items(), key=lambda p: (-p[1], p[0])):
        print(f"  {name:26s} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
