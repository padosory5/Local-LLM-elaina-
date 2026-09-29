"""Display text and speech, scored without a model.

    .venv/Scripts/python.exe -m evals.modality_pairs

For each pair in evals/modality_pairs.json, three questions about whatever
turns a reply into screen text and into speech:

* **preserved** -- is the text shown exactly the notation that was written?
* **speakable** -- is the text handed to the voice free of characters it
  cannot say?
* **equivalent** -- does the spoken text say every listed token, in order?

Since Phase 3A they are two realizations of one reply (brain/realize.py):
``current_display`` is what the pipeline shows when no stage rewrites the
reply -- the invariants and display repair -- and ``current_speech`` is what
the audio boundary hands the voice for that screen text. Phase 2's numbers,
from the single-string path, are in docs/COMMUNICATION_FINDINGS.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import realize  # noqa: E402
from evals.checks import unspeakable  # noqa: E402

PAIRS = Path(__file__).resolve().parent / "modality_pairs.json"


def pairs() -> list[dict]:
    return json.loads(PAIRS.read_text(encoding="utf-8"))["pairs"]


def current_display(text: str) -> str:
    """The screen text the pipeline makes of a reply, when no stage rewrites it."""
    return realize.display(text)


def current_speech(text: str, language: str = "en") -> str:
    """What the audio boundary hands the voice for that screen text."""
    return realize.speech(current_display(text), language)


def spoken_in_order(speech: str, tokens) -> tuple[bool, str]:
    """Whether every token is said, in order; the first one that is not."""
    said = " ".join(str(speech or "").casefold().replace("-", " ").split())
    position = 0
    for token in tokens:
        best = None
        for alternative in str(token).split("|"):
            wanted = " ".join(alternative.casefold().replace("-", " ").split())
            match = re.compile(rf"(?<!\w){re.escape(wanted)}(?!\w)").search(said, position)
            if match and (best is None or match.start() < best.start()):
                best = match
        if best is None:
            return False, str(token)
        position = best.end()
    return True, ""


# Notation is scored inside a sentence, the way a reply carries it. A reply
# that *begins* with notation has a separate problem, measured separately:
# the final structural repair capitalises the first letter of every reply,
# so "f(x) = ..." is shown as "F(x) = ..." and "n! = ..." as "N! = ...".
_SENTENCE = {"en": "So it is {} in this case.", "ko": "정리하면 {} 입니다."}


def score(display=current_display, speech=current_speech) -> list[dict]:
    rows = []
    for pair in pairs():
        language = pair.get("language", "en")
        written = _SENTENCE.get(language, _SENTENCE["en"]).format(pair["display"])
        shown = display(written)
        spoken = speech(written, language)
        equivalent, missing = spoken_in_order(spoken, pair["spoken"])
        opening = display(pair["display"])
        rows.append({
            "id": pair["id"],
            "language": language,
            "display": pair["display"],
            "shown": shown,
            "spoken": spoken,
            "preserved": pair["display"] in shown,
            "speakable": not unspeakable(spoken),
            "equivalent": equivalent,
            "missing": missing,
            # The same notation as the first thing in a reply.
            "preserved_at_start": opening.startswith(pair["display"]),
        })
    return rows


def summary(rows: list[dict]) -> dict:
    total = len(rows)
    return {
        "pairs": total,
        "preserved": sum(row["preserved"] for row in rows),
        "preserved_at_start": sum(row["preserved_at_start"] for row in rows),
        "speakable": sum(row["speakable"] for row in rows),
        "equivalent": sum(row["equivalent"] for row in rows),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = score()
    for row in rows:
        marks = "".join(
            "✓" if row[key] else "✗" for key in ("preserved", "speakable", "equivalent")
        )
        print(f"{marks}  {row['id']:<22} {row['display']!r:<32} -> screen {row['shown']!r}")
        if not row["equivalent"]:
            print(f"{'':27}voice {row['spoken']!r}; first missing: {row['missing']!r}")
    totals = summary(rows)
    print(
        f"\n{totals['pairs']} pairs: preserved {totals['preserved']} "
        f"(at the start of a reply {totals['preserved_at_start']}), "
        f"speakable {totals['speakable']}, equivalent {totals['equivalent']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
