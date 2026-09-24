"""Does she sound as good in Korean as in English? Measured on the same talk.

The earlier comparison -- Korean 73%, English 80% -- scored two different
conversations: "korean_day" was about dramas and dinner, "evening" about
sleep and headphones. The gap mixed the language with the content. The
paired arcs in live_dogfood_conversation.py fix that: every conversation
exists in both languages, turn for turn, with the same acts.

This pools every turn of every run by *language* and compares the two:

    python scripts/language_parity_report.py --tag parity0

Transcripts are read from runtime/<tag>_<arc>_<n>.json, as written by
scripts/dogfood_session.py. The score is conversation_quality_report's,
which is the same rule set the running system checks before it speaks.

"The same percentage" is a statistical claim, so it is made as one: a
two-proportion z-test on the pooled rates. A gap the test cannot tell from
noise is reported as such, not as parity or as a difference.
"""

from __future__ import annotations

import argparse
import glob
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Which language each arc is in. Pairs share content; that is the point.
ARC_LANGUAGE = {
    "korean_day": "ko", "english_day": "en",
    "evening": "en", "korean_evening": "ko",
}
PAIRS = (("korean_day", "english_day"), ("korean_evening", "evening"))


def _z_test(clean_a: int, total_a: int, clean_b: int, total_b: int) -> tuple[float, float]:
    """Two-proportion z and its two-sided p."""
    if not total_a or not total_b:
        return 0.0, 1.0
    p_a, p_b = clean_a / total_a, clean_b / total_b
    pooled = (clean_a + clean_b) / (total_a + total_b)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / total_a + 1 / total_b))
    if spread == 0:
        return 0.0, 1.0
    z = (p_a - p_b) / spread
    p = math.erfc(abs(z) / math.sqrt(2))
    return z, p


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    from scripts.conversation_quality_report import score

    per_language: dict[str, list[int]] = {"ko": [0, 0], "en": [0, 0]}
    per_arc: dict[str, list[int]] = {}
    classes: dict[str, Counter] = {"ko": Counter(), "en": Counter()}
    for arc, language in ARC_LANGUAGE.items():
        for path in sorted(glob.glob(str(ROOT / "runtime" / f"{args.tag}_{arc}_*.json"))):
            result = score(Path(path))
            clean, total = result["clean_turns"], result["total_turns"]
            per_language[language][0] += clean
            per_language[language][1] += total
            per_arc.setdefault(arc, [0, 0])
            per_arc[arc][0] += clean
            per_arc[arc][1] += total
            classes[language].update(result.get("counts", {}))

    print(f"{'arc':<16} {'lang':<5} clean")
    for arc, (clean, total) in per_arc.items():
        print(f"{arc:<16} {ARC_LANGUAGE[arc]:<5} {clean}/{total} "
              f"({clean / total:.0%})" if total else f"{arc:<16} no runs")
    print()
    for first, second in PAIRS:
        a, b = per_arc.get(first, [0, 0]), per_arc.get(second, [0, 0])
        if a[1] and b[1]:
            print(f"same content: {first} {a[0] / a[1]:.0%}  vs  "
                  f"{second} {b[0] / b[1]:.0%}")
    print()
    ko, en = per_language["ko"], per_language["en"]
    for language, (clean, total) in (("Korean", ko), ("English", en)):
        if total:
            print(f"{language:<8} {clean}/{total}  ({clean / total:.0%})")
    if ko[1] and en[1]:
        z, p = _z_test(ko[0], ko[1], en[0], en[1])
        gap = 100 * (ko[0] / ko[1] - en[0] / en[1])
        print(f"gap {gap:+.0f} points   z={z:+.2f}   p={p:.2f}   "
              f"{'distinguishable' if p < 0.05 else 'not distinguishable from noise'}")
    print()
    print("failures by class:")
    for name in sorted(set(classes["ko"]) | set(classes["en"])):
        print(f"  {name:<24} ko {classes['ko'][name]:>3}   en {classes['en'][name]:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
