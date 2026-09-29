"""What the pipeline did to a turn that it should never do, read from the record.

    .venv/Scripts/python.exe -m evals.integrity runtime/evals/p2/p-8b-soft-1 ...

The corpus checks (evals/checks.py) ask whether a reply is right. These ask
whether the machinery around the reply behaved, over every turn a run
recorded, scored or not:

* **clock turns searched** -- a time question that went to a web search. The
  clock answers it; a search result put in its place is how the 27B read
  New York's sunrise as the time (docs/PHASE3_PLAN.md §1.3).
* **values deleted with evidence** -- a grounding stage removed a value that
  the turn's own evidence held. The deletion is of something true
  (§1.4).
* **display changed by speech shaping** -- a stage that exists to make text
  speakable changed what was shown (§1.2).
* **notation changed by a deterministic stage** -- an operator between two
  operands went missing between a stage's input and output, in a stage that
  does not call a model. Covers any future stage, not only the three named
  ones.

Each is a count of turns, with the turns themselves kept for reading.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain import response_stages  # noqa: E402

# The Phase 2 names of the stages whose job was speech. After Phase 3A they
# do not run on the display at all; the count must then be 0.
SPEECH_SHAPING_STAGES = frozenset({"speech_filter", "natural_dashes", "final_speech_filter"})
GROUNDING_STAGES = frozenset({"grounded_values"})

_VALUE = re.compile(r"\d+(?::\d{2})?(?:[.,]\d+)?")
# An operator with an operand on each side: "x - a", "5-3", "b² - 4ac",
# "x²/2!", "a = b". Commas and "to" are what speech shaping turned them into.
_OPERATION = re.compile(
    r"([\w²³!)]+)(\s*)([-−+×÷=^/*])(?=(\s*)([\w(√]+))"
)


def _is_math(left: str, space_before: str, op: str, space_after: str, right: str) -> bool:
    """An operation, not a hyphenated word, "and/or" or *emphasis*."""
    if op not in "-/*":
        return True
    if space_before and space_after:
        return True
    # Unspaced: every operand a number or a symbol. "12-month", "COVID-19"
    # and "*Title* Even" are words; "5-3", "x-a", "2*3" are not.
    def symbolic(operand: str) -> bool:
        bare = operand.strip("()²³!")
        return bare.replace(".", "").isdigit() or len(bare) <= 1
    return symbolic(left) and symbolic(right)


@dataclass
class Integrity:
    turns: int = 0
    clock_turns: int = 0
    clock_turns_searched: list = field(default_factory=list)
    values_deleted_with_evidence: list = field(default_factory=list)
    display_speech_shaped: list = field(default_factory=list)
    notation_changed: list = field(default_factory=list)

    def counts(self) -> dict:
        return {
            "turns": self.turns,
            "clock_turns": self.clock_turns,
            "clock_turns_searched": len(self.clock_turns_searched),
            "values_deleted_with_evidence": len(self.values_deleted_with_evidence),
            "display_speech_shaped": len(self.display_speech_shaped),
            "notation_changed": len(self.notation_changed),
        }


def _route(record: dict) -> dict:
    route = (record.get("context") or {}).get("route")
    return route if isinstance(route, dict) else {}


def _evidence_text(record: dict) -> str:
    evidence = record.get("evidence") or {}
    parts = [str(value) for key, value in evidence.items()
             if key != "ledger" and isinstance(value, str)]
    for item in evidence.get("ledger") or ():
        parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def is_clock_turn(record: dict) -> bool:
    evidence = record.get("evidence") or {}
    ledger_kinds = {item.get("kind") for item in evidence.get("ledger") or ()}
    return (
        _route(record).get("intent") == "time_question"
        or bool(evidence.get("clock"))
        or "clock" in ledger_kinds
    )


def searched(record: dict) -> bool:
    evidence = record.get("evidence") or {}
    ledger_kinds = {item.get("kind") for item in evidence.get("ledger") or ()}
    return bool(evidence.get("search_queries")) or "search" in ledger_kinds


def deleted_values(before: str, after: str) -> list[str]:
    kept = set(_VALUE.findall(after or ""))
    return [value for value in dict.fromkeys(_VALUE.findall(before or ""))
            if value not in kept]


def operations(text: str) -> list[str]:
    """Each operator with its neighbours, as written: 'x - a', '5-3'."""
    found = []
    for match in _OPERATION.finditer(str(text or "")):
        if _is_math(*match.groups()):
            left, _, op, _, right = match.groups()
            found.append(f"{left[-2:]}{op}{right[:2]}")
    return found


def _is_deterministic(name: str) -> bool:
    stage = response_stages.BY_NAME.get(name)
    return stage is None or not stage.model_call


def check_record(record: dict, result: Integrity) -> None:
    result.turns += 1
    label = f"{record.get('turn_id', '?')}: {str(record.get('user_input', ''))[:60]}"
    if is_clock_turn(record):
        result.clock_turns += 1
        if searched(record):
            result.clock_turns_searched.append(label)
    evidence = _evidence_text(record)
    for step in record.get("steps") or ():
        if not step.get("changed") or step.get("gap"):
            continue
        name = str(step.get("name", ""))
        before, after = step.get("before", ""), step.get("after", "")
        if name in GROUNDING_STAGES and evidence:
            held = [value for value in deleted_values(before, after) if value in evidence]
            if held:
                result.values_deleted_with_evidence.append(f"{label} -- {', '.join(held)}")
        if name in SPEECH_SHAPING_STAGES:
            result.display_speech_shaped.append(f"{label} -- {name}")
        if _is_deterministic(name):
            lost = [op for op in operations(before) if op not in operations(after)]
            if lost:
                result.notation_changed.append(f"{label} -- {name}: {', '.join(lost[:3])}")


def check_run(path: Path) -> Integrity:
    result = Integrity()
    for trace_file in sorted((Path(path) / "traces").glob("*.jsonl")):
        for line in trace_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            check_record(record, result)
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for path in sys.argv[1:]:
        result = check_run(Path(path))
        print(f"{path}: {result.counts()}")
        for name in ("clock_turns_searched", "values_deleted_with_evidence",
                     "display_speech_shaped", "notation_changed"):
            for line in getattr(result, name)[:6]:
                print(f"  {name}: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
