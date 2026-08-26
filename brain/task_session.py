"""Short-lived structured context for conversational task follow-ups.

This is intentionally separate from long-term user memory.  A hotel price or
GPU listing is volatile, so it lives only for the active chat session and is
used solely to resolve references such as "which of those" or "the second
one".  It never authorises a browser action by itself.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any


_DEICTIC_REFERENCE = re.compile(
    r"\b(?:these|those|them|the\s+(?:first|second|third|last|best|cheapest)\s+one|"
    r"which\s+(?:one|of))\b",
    re.I,
)


@dataclass(frozen=True)
class TaskEvidenceContext:
    goal: str
    information: tuple[str, ...]
    items: tuple[Any, ...]
    expires_at: float


class TaskSessionStore:
    """Keep the most recent grounded shortlist for one conversational turn."""

    def __init__(self, *, ttl_seconds: int = 15 * 60) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._context: TaskEvidenceContext | None = None

    def remember(self, task_state: Any) -> None:
        raw_items = tuple(getattr(task_state, "collected_items", ()) or ())
        if not raw_items:
            return
        # Preserve the first evidence record per name; it is the one the
        # planner actually presented most recently, and prevents a follow-up
        # prompt from becoming bloated with duplicate extraction passes.
        unique: list[Any] = []
        names: set[str] = set()
        for item in raw_items:
            name = str(getattr(item, "name", "")).strip()
            key = name.casefold()
            if not name or key in names:
                continue
            names.add(key)
            unique.append(item)
        if not unique:
            return
        self._context = TaskEvidenceContext(
            goal=str(getattr(task_state, "goal", "")).strip(),
            information=tuple(
                str(value)
                for value in (getattr(task_state, "collected_information", ()) or ())
            )[-4:],
            items=tuple(unique[:8]),
            expires_at=time.monotonic() + self.ttl_seconds,
        )

    def clear(self) -> None:
        self._context = None

    def current(self) -> TaskEvidenceContext | None:
        context = self._context
        if context is not None and time.monotonic() >= context.expires_at:
            self._context = None
            return None
        return context

    def context_for_followup(self, request: str) -> TaskEvidenceContext | None:
        if not _DEICTIC_REFERENCE.search(str(request)):
            return None
        return self.current()

    def public_conversation_state(self) -> dict[str, list[str]]:
        """Names only: enough for routing, never full source prose."""
        context = self.current()
        if context is None:
            return {}
        return {
            "task_candidates": [
                str(getattr(item, "name", ""))
                for item in context.items
                if str(getattr(item, "name", "")).strip()
            ],
        }
