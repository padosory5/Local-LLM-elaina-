"""A5's instrument: what does the person hear when a multi-step task ends?

`tests/test_execution_matrix.py` already asserts which terminal state each of
the scenarios reaches, and all of them pass. This asks the question one layer
further out, which nothing was asking: **what sentence comes out.**

The two are not the same question, and at the start of A5 they disagreed on
five of twenty-two scenarios:

    goal   : Play my liked songs.
    step   : ui_control -> failed, "Pressed play; nothing started."
    outcome: retryable_failure          <- asserted, green, read by no one
    heard  : "Done."

``ChatEngine._handle_task_action`` returned ``task_result.summary``, which is
whatever the model wrote in its final planning decision, and never called
``TaskRunResult.outcome()`` at all. A whole verified subsystem, wired to
nothing.

Three faults are counted, and each one is a sentence a person could be told:

* **claims_success** -- the run did not succeed and the sentence says it did.
  The worst of the three: the person stops watching.
* **silent_progress** -- the run got somewhere and the sentence does not say
  where, so they cannot tell what happened to their machine.
* **claims_progress** -- the sentence names work the steps do not support.
  Nothing produces this today; it is here because a reporting layer that can
  under-report can over-report, and the guard has to be measured in both
  directions or it is only measured in the easy one.

Run offline; the scenarios are scripted, like the matrix they come from.

    .venv/Scripts/python.exe scripts/task_report_check.py
    .venv/Scripts/python.exe scripts/task_report_check.py --language ko
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.stdout.reconfigure(encoding="utf-8")

from brain import task_progress  # noqa: E402
from tests.test_execution_matrix import MATRIX_PATH, _run  # noqa: E402

# Words that assert the goal was met. Checked only on runs that did not
# succeed, so an honest success saying "done" is untouched.
_SUCCESS_WORDS = ("done", "finished", "complete", "완료", "끝냈")


def _says_success(sentence: str) -> bool:
    """Whether this sentence claims the goal was met.

    **Instrument correction, recorded rather than quietly applied.** The
    first version searched the whole sentence, and condemned the single
    best report in the run:

        You took control, so I stopped. This much is done -- Spotify is open.

    That is exactly what a cancelled-with-progress run should say, and the
    word it was caught on is inside the progress frame's own wording. A
    checker that fails the behaviour it was built to reward is measuring
    its own vocabulary, so the progress clause is removed before the
    question is asked. Without this the run reads 21/22 and the one
    "failure" is correct behaviour.
    """
    said = sentence
    for marker in task_progress.SPOKEN_PROGRESS_MARKERS:
        head, sep, _ = said.partition(marker)
        if sep:
            said = head
    said = said.casefold()
    return any(word in said for word in _SUCCESS_WORDS)


def _mentions(sentence: str, fragment: str) -> bool:
    return fragment.strip(" .").casefold() in sentence.casefold()


def examine(case: dict, language: str) -> dict:
    # The planner prints its own trace; the report is what is under test.
    with contextlib.redirect_stdout(io.StringIO()):
        result = _run(case)
        outcome = result.outcome()
        progress = task_progress.read(result.task_state)
        heard = task_progress.report(
            outcome,
            result.task_state,
            language=language,
            model_summary=result.summary,
        )

    faults = []
    if not outcome.succeeded and _says_success(heard):
        faults.append("claims_success")
    if not outcome.succeeded and progress.reached and not any(
        _mentions(heard, fragment) for fragment in progress.reached
    ):
        faults.append("silent_progress")
    if not progress.reached and progress.found == 0:
        # Nothing was accomplished, so nothing may be claimed.
        if task_progress.SPOKEN_PROGRESS_MARKERS and any(
            marker in heard for marker in task_progress.SPOKEN_PROGRESS_MARKERS
        ):
            faults.append("claims_progress")

    return {
        "id": case["id"],
        "outcome": outcome.outcome,
        "expected": case.get("expected_outcome", ""),
        "reached": list(progress.reached),
        "heard": heard,
        "faults": faults,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", default="en", choices=("en", "ko"))
    parser.add_argument("--json", dest="out", default="")
    args = parser.parse_args()

    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    rows = [examine(case, args.language) for case in matrix["cases"]]

    print("=" * 74)
    print(f"A5 -- WHAT THE PERSON HEARS  (language: {args.language})")
    print("=" * 74)

    for row in rows:
        mark = "FAIL" if row["faults"] else "ok  "
        print(f"\n[{mark}] {row['id']}  ({row['outcome']})")
        print(f"       heard: {row['heard']!r}")
        if row["reached"]:
            print(f"       did:   {row['reached']}")
        for fault in row["faults"]:
            print(f"       >> {fault}")

    counts: dict[str, int] = {}
    for row in rows:
        for fault in row["faults"]:
            counts[fault] = counts.get(fault, 0) + 1
    clean = sum(1 for row in rows if not row["faults"])

    print("\n" + "=" * 74)
    print(f"{clean}/{len(rows)} scenarios reported honestly")
    for fault, count in sorted(counts.items(), key=lambda pair: -pair[1]):
        print(f"  {count:>3}  {fault}")
    print("=" * 74)

    if args.out:
        Path(args.out).write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(f"\nWritten to {args.out}")
    return 0 if clean == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
