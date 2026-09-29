"""What this turn actually had in hand, and where each piece came from.

One ledger per turn. Every capability that hands the answer a fact writes it
here -- the clock, a computed result, a search, something recalled from an
earlier turn on purpose -- and the ledger is also what the turn record shows
as the turn's evidence (core/turn_trace.py). So what a guard checks a reply
against and what the record says the turn knew are one object, not two
accounts that can drift apart.

Why it exists, measured in Phase 2 (docs/PHASE3_PLAN.md §1.4): the grounded
value guard checked replies against ``ChatEngine._last_research_evidence``,
which any search set and nothing ever cleared, and never against the clock.
A time question asked after an earlier search had its correct answer
deleted as unverified -- the clock reading was in the turn record and not in
what the guard read.

Scope is the turn. Evidence from an earlier turn is in the ledger only when
this turn decided to reuse it, and it is marked ``carried``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# The kinds a reply's values can be checked against. Deterministic kinds are
# computed by code, never read off a page or out of a model.
CLOCK = "clock"
CALCULATION = "calculation"
CONVERSION = "conversion"
SEARCH = "search"
RECALL = "recall"
GROUNDED = "grounded"
TOOL_RESULT = "tool_result"

KINDS = (CLOCK, CALCULATION, CONVERSION, SEARCH, RECALL, GROUNDED, TOOL_RESULT)
DETERMINISTIC = frozenset({CLOCK, CALCULATION, CONVERSION})

_TRACE_TEXT_LIMIT = 4000


@dataclass(frozen=True)
class Evidence:
    kind: str
    text: str
    source: str = ""
    # From an earlier turn, reused because this turn chose to.
    carried: bool = False


class EvidenceLedger:
    """The evidence of one turn, in the order it arrived."""

    def __init__(self) -> None:
        self._items: list[Evidence] = []

    def add(self, kind: str, text, *, source: str = "",
            carried: bool = False) -> Evidence | None:
        body = str(text or "").strip()
        if not body:
            return None
        if kind not in KINDS:
            raise ValueError(f"unknown evidence kind {kind!r}")
        item = Evidence(kind=kind, text=body, source=str(source or ""),
                        carried=bool(carried))
        self._items.append(item)
        self._record()
        return item

    def items(self, *kinds: str) -> tuple[Evidence, ...]:
        if not kinds:
            return tuple(self._items)
        return tuple(item for item in self._items if item.kind in kinds)

    def text(self, *kinds: str) -> str:
        return "\n".join(item.text for item in self.items(*kinds))

    def has(self, *kinds: str) -> bool:
        return bool(self.items(*kinds))

    @property
    def looked_something_up(self) -> bool:
        """Whether anything outside the conversation stands behind this turn."""
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def as_list(self) -> list[dict]:
        out = []
        for item in self._items:
            entry = asdict(item)
            entry["text"] = entry["text"][:_TRACE_TEXT_LIMIT]
            out.append(entry)
        return out

    def _record(self) -> None:
        try:
            from core import turn_trace

            turn_trace.note_evidence(ledger=self.as_list())
        except Exception:
            pass
