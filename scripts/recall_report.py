"""A7's instrument: does a turn's personal content survive its route?

There is a real memory subsystem here -- SQLite, FAISS, embeddings,
extraction, consolidation, ranking -- and both ends of it sat behind the
same gate:

    storing     route.intent == "conversation" and route.memory_candidate
    retrieval   route.intent == "conversation" and route.memory_relevant

``conversation`` is one of **twenty-four** intents. The turns most likely
to contain a durable fact about someone are the turns where they are
asking for something, and those are never routed as conversation:

    "I'm allergic to shellfish, find me somewhere for dinner"
        -> web_search -> the allergy is never stored

    "what's a good restaurant near my school?"
        -> web_search -> nothing is recalled, and the gap is filled from
           the model. That is where an invented university comes from.

This report runs `tests/recall_matrix.json` through
:mod:`brain.memory_gate` and, with ``--routed``, through the real router as
well, so the two gates can be compared on the same sentences. The
``--routed`` pass needs Ollama; the default pass is free and offline.

The negatives matter as much as the positives, in both directions. A mood
is not a profile -- storing "I'm tired" as a fact about someone is how a
memory becomes a caricature -- and "my screen" is not "my school".

    .venv/Scripts/python.exe scripts/recall_report.py
    .venv/Scripts/python.exe scripts/recall_report.py --routed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8")

from brain import memory_gate  # noqa: E402

MATRIX_PATH = PROJECT_ROOT / "tests" / "recall_matrix.json"

# What each kind expects from the two gate questions and the two
# instructions, in order: store, recall, forget.
EXPECTED = {
    "store":      (True,  None,  False),
    "recall":     (None,  True,  False),
    "transient":  (False, None,  False),
    "machine":    (None,  False, False),
    "forget":     (None,  None,  True),
    "not_forget": (None,  None,  False),
    # A question that names something personal: recall against it,
    # never store it as though it were an answer.
    "asking":     (False, True,  False),
}


def examine(case: dict) -> dict:
    text = case["text"]
    store = memory_gate.carries_something_to_remember(text)
    recall = memory_gate.needs_what_we_know(text)
    forget = memory_gate.asks_to_forget(text)

    want_store, want_recall, want_forget = EXPECTED[case["kind"]]
    faults = []
    if want_store is not None and store != want_store:
        faults.append(f"store={store} wanted {want_store}")
    if want_recall is not None and recall != want_recall:
        faults.append(f"recall={recall} wanted {want_recall}")
    if want_forget is not None and forget != want_forget:
        faults.append(f"forget={forget} wanted {want_forget}")

    # What the old gate would have done: everything that is not routed as
    # conversation is dropped in both directions, whatever it says.
    routed_conversation = case.get("likely_intent") == "conversation"

    return {
        "id": case["id"],
        "kind": case["kind"],
        "text": text,
        "store": store,
        "recall": recall,
        "forget": forget,
        "old_gate_would_reach_memory": routed_conversation,
        "faults": faults,
        "ok": not faults,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--routed", action="store_true",
        help="also run the real router, to confirm the intents this assumes",
    )
    parser.add_argument("--json", dest="out", default="")
    args = parser.parse_args()

    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    rows = [examine(case) for case in matrix["cases"]]

    if args.routed:
        from brain.intent_router import IntentRouter  # noqa: E402  (slow import)
        router = IntentRouter()
        for row, case in zip(rows, matrix["cases"]):
            try:
                decision = router.route(case["text"])
                row["intent"] = getattr(decision, "intent", "?")
                row["old_gate_would_reach_memory"] = row["intent"] == "conversation"
            except Exception as error:
                row["intent"] = f"({type(error).__name__})"

    print("=" * 74)
    print("A7 -- RECALL GATE")
    print("=" * 74)

    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)

    for kind in ("store", "recall", "asking", "transient", "machine",
                 "forget", "not_forget"):
        group = by_kind.get(kind, [])
        if not group:
            continue
        clean = sum(1 for row in group if row["ok"])
        print(f"\n### {kind}  ({clean}/{len(group)})")
        for row in group:
            mark = "ok  " if row["ok"] else "FAIL"
            intent = f"  [{row['intent']}]" if "intent" in row else ""
            print(f"  [{mark}] {row['id']}{intent}")
            print(f"         {row['text']}")
            for fault in row["faults"]:
                print(f"         >> {fault}")

    clean = sum(1 for row in rows if row["ok"])
    # What the old gate reached: only turns routed as conversation, and
    # only then if the model's boolean also agreed.
    reachable = sum(1 for row in rows if row["old_gate_would_reach_memory"])
    needed = sum(1 for row in rows if row["kind"] in ("store", "recall"))
    reached_and_needed = sum(
        1 for row in rows
        if row["kind"] in ("store", "recall") and row["old_gate_would_reach_memory"]
    )

    print("\n" + "=" * 74)
    print(f"{clean}/{len(rows)} cases correct")
    print(f"  turns the intent gate let through at all:  {reachable}/{len(rows)}")
    print(f"  turns that needed memory and were routed")
    print(f"  somewhere the old gate could see:          {reached_and_needed}/{needed}")
    print("=" * 74)

    if args.out:
        Path(args.out).write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(f"\nWritten to {args.out}")
    return 0 if clean == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
