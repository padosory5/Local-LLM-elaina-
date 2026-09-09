"""A6's instrument: for each attribute Elaina states, is it in the evidence?

`brain/grounded_values.py` has stopped her quoting an invented **price**
since Phase 4. It defines a checkable value as money, a phone number or an
email address, and nothing else. Measured against a real search result with
six invented replies, it caught one:

    invents a price            caught
    invents a refresh rate     not caught
    invents a response time    not caught
    invents a rating           not caught
    invents battery life       not caught
    invents a weight           not caught

A fabricated 240Hz reads exactly like a retrieved one, and it is the number
that decides the purchase.

Half of `tests/attribute_matrix.json` is negatives, and that is the more
important half. This guard **removes text from replies**, so its failure
mode is not "misses a spec" but "deletes a true sentence". The cases named
`not_a_claim` are ordinary speech carrying ordinary units -- one of them,
"let it steep for fourteen hours", is a real turn from the everyday dogfood
arc -- and a run that catches every invention while eating those is a worse
product than no guard at all.

Run offline; the evidence is fixed text.

    .venv/Scripts/python.exe scripts/attribute_grounding_report.py
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

from brain import attribute_values  # noqa: E402
from brain.grounded_values import GroundedValueGuard  # noqa: E402

MATRIX_PATH = PROJECT_ROOT / "tests" / "attribute_matrix.json"


def examine(case: dict) -> dict:
    evidence = case["evidence"]
    reply = case["reply"]

    if case["kind"] == "conflict":
        found = attribute_values.disagreements(
            evidence, case.get("second_source", ""),
        )
        expected = set(case.get("expect_conflict", ()))
        return {
            "id": case["id"],
            "kind": case["kind"],
            "reply": reply,
            "found": sorted(found),
            "expected": sorted(expected),
            "ok": set(found) == expected,
        }

    unsupported = attribute_values.unsupported(reply, evidence)
    found = [claim.text for claim in unsupported]
    expected = list(case.get("expect", ()))

    # Normalised comparison: the matrix names the fragment as a person
    # would write it, not as the regex happened to capture it.
    def _key(text: str) -> str:
        return "".join(text.split()).casefold().rstrip(".")

    ok = {_key(item) for item in found} == {_key(item) for item in expected}

    # The money guard must still work: A6 widens the vocabulary and may not
    # narrow it.
    money = sorted(GroundedValueGuard.unsupported_values(reply, evidence))

    return {
        "id": case["id"],
        "kind": case["kind"],
        "reply": reply,
        "found": found,
        "expected": expected,
        "money": money,
        "ok": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="out", default="")
    args = parser.parse_args()

    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    rows = [examine(case) for case in matrix["cases"]]

    print("=" * 74)
    print("A6 -- ATTRIBUTE GROUNDING")
    print("=" * 74)

    by_kind: dict[str, list[dict]] = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)

    for kind in ("unsupported", "contradicted", "grounded", "not_a_claim",
                 "conflict"):
        group = by_kind.get(kind, [])
        if not group:
            continue
        clean = sum(1 for row in group if row["ok"])
        print(f"\n### {kind}  ({clean}/{len(group)})")
        for row in group:
            mark = "ok  " if row["ok"] else "FAIL"
            print(f"  [{mark}] {row['id']}")
            print(f"         {row['reply']}")
            if not row["ok"]:
                print(f"         found:    {row['found']}")
                print(f"         expected: {row['expected']}")

    clean = sum(1 for row in rows if row["ok"])
    caught = sum(
        1 for row in rows
        if row["kind"] in ("unsupported", "contradicted") and row["ok"]
    )
    catchable = sum(
        1 for row in rows if row["kind"] in ("unsupported", "contradicted")
    )
    survived = sum(
        1 for row in rows
        if row["kind"] in ("grounded", "not_a_claim") and row["ok"]
    )
    protectable = sum(
        1 for row in rows if row["kind"] in ("grounded", "not_a_claim")
    )

    print("\n" + "=" * 74)
    print(f"{clean}/{len(rows)} cases correct")
    print(f"  invented attributes caught:   {caught}/{catchable}")
    print(f"  true sentences left alone:    {survived}/{protectable}")
    print("=" * 74)

    if args.out:
        Path(args.out).write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(f"\nWritten to {args.out}")
    return 0 if clean == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
