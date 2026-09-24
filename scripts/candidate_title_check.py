"""Is this the name of a thing, or the title of a page? Scored both ways.

Rule 1, the instrument first. Page titles have been recommended out loud as
though they were things -- a GIF title as a kimchi-stew tip, "Gaming Mice
Under" as a mouse, a weather forecast page as a pick. Every such title in
tests/candidate_title_matrix.json came out of a real session log.

The other half is what must survive: real products, places, and the films
and dramas whose names are sentences. A checker that only counted the pages
would score perfectly on a rule that rejected everything.

    python scripts/candidate_title_check.py

Scores ``candidate_fit.off_target``, the one place a result is judged not to
be a candidate, with no model and no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MATRIX = ROOT / "tests" / "candidate_title_matrix.json"


def verdict(case: dict) -> tuple[str, str]:
    """("page" or "thing", and the reason if it was rejected)."""
    from brain import candidate_fit

    reason = candidate_fit.off_target(
        case["name"], case.get("url", ""), "", case["shape"],
    )
    return ("page" if reason else "thing"), reason


def report(cases: list[dict]) -> int:
    wrong = 0
    counts: dict[str, list[bool]] = {"page": [], "thing": []}
    for case in cases:
        got, reason = verdict(case)
        ok = got == case["expect"]
        counts[case["expect"]].append(ok)
        if not ok:
            wrong += 1
            print(f"  wrong  want {case['expect']:<5} got {got:<5} "
                  f"[{case['shape']}] {case['name'][:60]}"
                  f"{'  (' + reason + ')' if reason else ''}")
    print()
    for expect, results in counts.items():
        print(f"must be a {expect:<5}  {sum(results)}/{len(results)}")
    print(f"overall          {len(cases) - wrong}/{len(cases)}")
    return wrong


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cases = json.loads(MATRIX.read_text(encoding="utf-8"))["cases"]
    return 1 if report(cases) else 0


if __name__ == "__main__":
    raise SystemExit(main())
