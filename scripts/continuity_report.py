"""Score conversational continuity across the benchmark conversations.

Five measures, taken from the same conversations the suite asserts:

    reference resolution   an ordinal against a real result set
    correction accuracy    a correction replacing the stale subject
    goal continuity        the subject surviving a follow-up that names nothing
    stale-context errors   an old topic reaching past a clear topic change
    ambiguity handling     a reference that must ask rather than pick

The last one is the one with a hard target: an ambiguous reference resolving
to *something* is worse than not resolving at all, because it turns into an
action on the wrong object.

    .venv/Scripts/python.exe scripts/continuity_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.console_style import status_label  # noqa: E402
from tests.test_continuity_matrix import MATRIX_PATH, _run  # noqa: E402


def main() -> int:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    conversations = matrix["conversations"]

    checks: dict[str, list[bool]] = {
        "reference resolution": [],
        "correction accuracy": [],
        "goal continuity": [],
        "stale-context errors": [],
        "ambiguity handling": [],
    }
    failures: list[str] = []

    turns = 0
    for conversation in conversations:
        seen = _run(conversation)
        print("=" * 72)
        print(f"{conversation['id']} ({conversation['kind']})")
        for turn, state in zip(conversation["turns"], seen):
            turns += 1
            marks = []

            if "resolves_to" in turn:
                ok = (
                    state["reference"].resolved
                    and state["reference"].value == turn["resolves_to"]
                )
                checks["reference resolution"].append(ok)
                marks.append(f"ref->{state['reference'].value or '(none)'}")
                if not ok:
                    failures.append(f"{conversation['id']}: {turn['said']!r}")

            if turn.get("unresolved"):
                ok = not state["reference"].resolved
                checks["ambiguity handling"].append(ok)
                marks.append("asked" if ok else "PICKED ONE")
                if not ok:
                    failures.append(f"{conversation['id']}: {turn['said']!r}")

            if "superseded" in turn:
                ok = turn["superseded"] in state["superseded"]
                checks["correction accuracy"].append(ok)
                marks.append(f"retired={turn['superseded']!r}" if ok else "NOT RETIRED")
                if not ok:
                    failures.append(f"{conversation['id']}: {turn['said']!r}")

            if "subject" in turn:
                ok = state["subject"] == turn["subject"]
                checks["goal continuity"].append(ok)
                marks.append(f"subject={state['subject']!r}")
                if not ok:
                    failures.append(
                        f"{conversation['id']}: {turn['said']!r} -> "
                        f"{state['subject']!r} (wanted {turn['subject']!r})"
                    )

            if "not_subject" in turn:
                ok = state["subject"] != turn["not_subject"]
                checks["stale-context errors"].append(ok)
                marks.append(
                    f"subject={state['subject']!r}" if ok else "REACHED BACK"
                )
                if not ok:
                    failures.append(f"{conversation['id']}: {turn['said']!r}")

            detail = ("  " + " | ".join(marks)) if marks else ""
            print(f"  {turn['said']!r}{detail}")
        if conversation.get("note"):
            print(f"  note: {conversation['note']}")

    print("\n" + "=" * 72)
    print(f"{len(conversations)} conversations, {turns} turns.\n")
    total_ok = total = 0
    for name, results in checks.items():
        if not results:
            continue
        passed, count = sum(results), len(results)
        total_ok += passed
        total += count
        print(f"[{status_label(passed == count)}] {name:22} {passed}/{count}")
    if total:
        print(f"\nOverall: {total_ok}/{total} ({total_ok / total * 100:.1f}%)")
    if failures:
        print("\nFailures:")
        for line in failures:
            print(f"  {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
