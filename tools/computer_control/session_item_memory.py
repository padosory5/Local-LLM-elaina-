"""Local, per-session memory of filesystem items Elaina just created.

Lets a referential delete request ("delete the folder we just made") resolve
its target from real, locally-recorded state instead of the router's model
inventing an unspoken name -- the same principle already used for
active_desktop_surface. This never lets the model choose a delete target; it
only lets a deictic reference bind to something Elaina herself just created.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

_MAX_ITEMS_PER_KIND = 5


@dataclass(frozen=True)
class SessionCreatedItem:
    name: str
    location: str
    kind: str  # "file" or "folder"
    created_at: float


class SessionItemMemory:
    """Remember the most recently created files/folders this session."""

    def __init__(self) -> None:
        self._items: list[SessionCreatedItem] = []

    def record(self, *, name: str, location: str, kind: str) -> None:
        name = str(name).strip()
        kind = str(kind).strip()
        if not name or not kind:
            return
        self._items.append(SessionCreatedItem(
            name=name,
            location=str(location).strip(),
            kind=kind,
            created_at=time.time(),
        ))
        by_kind: dict[str, list[SessionCreatedItem]] = {}
        for item in self._items:
            by_kind.setdefault(item.kind, []).append(item)
        trimmed = [
            item
            for kind_items in by_kind.values()
            for item in kind_items[-_MAX_ITEMS_PER_KIND:]
        ]
        trimmed.sort(key=lambda item: item.created_at)
        self._items = trimmed

    def recent(self, kind: str) -> tuple[SessionCreatedItem, ...]:
        wanted = str(kind).strip()
        return tuple(item for item in self._items if item.kind == wanted)

    def recent_context(self) -> tuple[dict[str, str], ...]:
        """Plain-dict snapshot, matching active_desktop_surface's shape."""
        return tuple(
            {"name": item.name, "location": item.location, "kind": item.kind}
            for item in self._items
        )
