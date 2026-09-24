"""What the person did on the machine, kept locally: the activity log.

The memory store holds what the person *said*. This holds what they *did*:
which button they clicked in which app, which page they opened, which
setting they switched, which app they moved to -- written by the activity
recorder (tools/screen_control/activity_recorder.py) and read back when
they ask "what did I do this morning?" or "which site was I on earlier?".

What it deliberately is not:

* **not a keylogger.** Typing is recorded as "typed in <field>"; the text
  itself only inside a recording the person started, never from a password
  field (config ``activity.typed_text``);
* **not kept forever.** Rows older than ``activity.retention_days`` are
  dropped at start-up and hourly;
* **not shared.** A local SQLite file under the runtime directory; this
  package makes no network calls (tests/test_memory_gate.py checks every
  file in it);
* **not hard to forget.** "Forget what I did today" deletes the rows --
  for a record of someone's behaviour, forgetting has to mean gone.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from core.paths import DATABASE_DIRECTORY, ensure_runtime_directories

ACTIVITY_PATH = DATABASE_DIRECTORY / "activity.db"

# What a row can be. The recorder writes these and nothing else.
CLICKS = ("click", "double_click", "right_click", "middle_click")
KINDS = CLICKS + (
    "type",        # typed into a field (text only when allowed)
    "key",         # a shortcut or a named key: Ctrl+C, Enter, F5
    "scroll",      # inside a recording only
    "switch",      # moved to another window
    "page",        # a browser page opened
    "view",        # another app's window title changed (a folder, a track)
    "fullscreen",  # a full-screen app (usually a game): one row, from-until
    "private",     # a private browsing window: that it was used, nothing more
    "start",       # where a recording began
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    app TEXT NOT NULL DEFAULT '',
    window TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL DEFAULT '',
    target_type TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    recording TEXT NOT NULL DEFAULT ''
)
"""

_PRUNE_EVERY_SECONDS = 3600.0


@dataclass
class Activity:
    """One thing the person did.

    ``detail`` carries what a replay would need and a sentence does not:
    the control's automation id and class, its rectangle, the point within
    it, the window handle, the wheel delta. ``recording`` is the id of the
    recording it was captured in, if any.
    """

    at: float
    kind: str
    app: str = ""
    window: str = ""
    target: str = ""
    target_type: str = ""
    url: str = ""
    text: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    recording: str = ""
    id: int = 0

    _FIELDS = ("at", "kind", "app", "window", "target", "target_type",
               "url", "text", "detail")

    def as_step(self) -> dict[str, Any]:
        """As a recording step: every field that says something."""
        step: dict[str, Any] = {}
        for name in self._FIELDS:
            value = getattr(self, name)
            if value or name in ("at", "kind"):
                step[name] = round(value, 3) if name == "at" else value
        return step

    @classmethod
    def from_step(cls, data: dict[str, Any]) -> "Activity":
        known = {name: data[name] for name in cls._FIELDS if name in data}
        known["at"] = float(known.get("at", 0.0) or 0.0)
        known["kind"] = str(known.get("kind", "") or "")
        known["detail"] = dict(known.get("detail") or {})
        return cls(**known)


class ActivityLog:
    """The local store. Safe to write from the recorder's thread while the
    conversation thread reads."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        retention_days: float = 14.0,
        clock=time.time,
    ) -> None:
        if path is None:
            ensure_runtime_directories()
            path = ACTIVITY_PATH
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = float(retention_days)
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        with self._lock:
            self._db.execute(_SCHEMA)
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS activity_at ON activity(at)"
            )
            self._db.commit()
        self._pruned_at = 0.0
        self.prune()

    # -------------------------------------------------------------- write

    def add(self, activity: Activity) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO activity (at, kind, app, window, target, "
                "target_type, url, text, detail, recording) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    float(activity.at), activity.kind, activity.app,
                    activity.window, activity.target, activity.target_type,
                    activity.url, activity.text,
                    json.dumps(activity.detail or {}, ensure_ascii=False),
                    activity.recording,
                ),
            )
            self._db.commit()
            row_id = int(cursor.lastrowid or 0)
        if self._clock() - self._pruned_at > _PRUNE_EVERY_SECONDS:
            self.prune()
        return row_id

    def extend(self, row_id: int, **detail: Any) -> None:
        """Merge more into a row's detail -- a full-screen session's end."""
        with self._lock:
            row = self._db.execute(
                "SELECT detail FROM activity WHERE id = ?", (int(row_id),),
            ).fetchone()
            if row is None:
                return
            merged = {**_load(row[0]), **detail}
            self._db.execute(
                "UPDATE activity SET detail = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False), int(row_id)),
            )
            self._db.commit()

    def forget(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        everything: bool = False,
    ) -> int:
        """Delete rows in a time window, or all of them. Returns how many."""
        if not everything and since is None and until is None:
            return 0
        clauses, values = _window(since, until)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            cursor = self._db.execute(f"DELETE FROM activity{where}", values)
            self._db.commit()
            return int(cursor.rowcount or 0)

    def prune(self) -> int:
        """Drop what is older than the retention window (0 keeps all)."""
        self._pruned_at = self._clock()
        if self.retention_days <= 0:
            return 0
        cutoff = self._clock() - self.retention_days * 86400.0
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM activity WHERE at < ?", (cutoff,),
            )
            self._db.commit()
            return int(cursor.rowcount or 0)

    # --------------------------------------------------------------- read

    def recent(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        kinds: Iterable[str] = (),
        containing: str = "",
        limit: int = 200,
    ) -> list[Activity]:
        """Rows in a window, oldest first -- the latest ``limit`` of them."""
        clauses, values = _window(since, until)
        kinds = tuple(kinds)
        if kinds:
            clauses.append(f"kind IN ({', '.join('?' * len(kinds))})")
            values.extend(kinds)
        needle = str(containing or "").strip()
        if needle:
            clauses.append(
                "(lower(target) LIKE ? OR lower(window) LIKE ? "
                "OR lower(url) LIKE ? OR lower(text) LIKE ?)"
            )
            values.extend([f"%{needle.casefold()}%"] * 4)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, at, kind, app, window, target, target_type, url, "
                f"text, detail, recording FROM activity{where} "
                "ORDER BY at DESC, id DESC LIMIT ?",
                (*values, max(1, int(limit))),
            ).fetchall()
        return [
            Activity(
                id=row[0], at=row[1], kind=row[2], app=row[3], window=row[4],
                target=row[5], target_type=row[6], url=row[7], text=row[8],
                detail=_load(row[9]), recording=row[10],
            )
            for row in reversed(rows)
        ]

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute(
                "SELECT COUNT(*) FROM activity",
            ).fetchone()[0])

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass


def _window(since: float | None, until: float | None) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    if since is not None:
        clauses.append("at >= ?")
        values.append(float(since))
    if until is not None:
        clauses.append("at < ?")
        values.append(float(until))
    return clauses, values


def _load(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
