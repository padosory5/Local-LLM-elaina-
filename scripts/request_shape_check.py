"""Is this turn asking for something? Scored in both languages.

Rule 1: the instrument before the change. ``_REQUEST_SHAPE`` in
``brain/intent_router.py`` decides whether a turn the model wanted to look up
was actually a request; when it says "remark", the lookup is withheld and she
answers from what she already knows.

It was written for English. Korean keeps its question word where the answer
would go and arrives from speech-to-text without a question mark, so in a
real Korean session three plain questions were read as remarks and answered
from memory -- one with an invented viewer count.

    python scripts/request_shape_check.py

No model, no network: the test is a regular expression over the sentence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MATRIX = ROOT / "tests" / "request_shape_matrix.json"


def shape(said: str) -> str:
    """What the rule makes of this turn: "request" or "remark"."""
    from brain.intent_router import reads_as_request

    return "request" if reads_as_request(said) else "remark"


def report(cases: list[dict]) -> int:
    wrong = 0
    table: dict[tuple[str, str], list[bool]] = {}
    for case in cases:
        got = shape(case["said"])
        ok = got == case["expect"]
        table.setdefault((case["language"], case["expect"]), []).append(ok)
        if not ok:
            wrong += 1
            print(
                f"  wrong  {case['language']}  want {case['expect']:<7}  "
                f"got {got:<7}  {case['said']}"
            )
    print()
    for (language, expect), results in sorted(table.items()):
        right = sum(results)
        print(f"{language} must be {expect:<8} {right}/{len(results)}")
    total = len(cases)
    print(f"overall            {total - wrong}/{total}")
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
