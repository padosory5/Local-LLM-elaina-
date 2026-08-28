"""What she has learned about the person, and how sure she is of it.

Phase 5. Until now the only thing allowed to stand in for a value nobody
said was what she had played *this session*; close the app and she knows
nothing again. This remembers across sessions -- and, more usefully, it
remembers which of two identically named things the person actually means.

Three rules keep it honest, and they matter more than the storage:

* **A preference is evidence, not a fact.** Every entry records where it
  came from and how often it has been seen. One play is not a taste, so a
  single observation is never enough to act on; something said outright is.
* **It may never reinforce itself.** A value she filled in *from this
  profile* is not new evidence for this profile. Without that rule a
  single lucky guess becomes a certainty by being repeated back to itself.
* **Correcting is cheap.** Naming something explicitly outweighs what was
  observed, and halves the competing value's standing at the same time, so
  one correction changes behaviour rather than being averaged away.

Stored locally in ``runtime/data`` -- what someone listens to is theirs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from core.paths import DATA_DIRECTORY, ensure_runtime_directories

# What was said outright, versus what was noticed from what actually
# happened. Both are real evidence; only one of them was volunteered.
STATED = "stated"
OBSERVED = "observed"

_WEIGHT_BY_SOURCE = {STATED: 2.0, OBSERVED: 1.0}
# One play is not a taste. Something said outright clears this on its own.
_MINIMUM_STANDING = 2.0
# A correction should change behaviour, not be averaged into it.
_CORRECTION_DECAY = 0.5
# Kinds this profile knows about, so a typo cannot invent a new one.
ARTIST_FOR_TITLE = "artist_for_title"
FAVOURITE_TRACK = "favourite_track"
_KINDS = frozenset({ARTIST_FOR_TITLE, FAVOURITE_TRACK})

_PROFILE_PATH = DATA_DIRECTORY / "user_profile.json"


@dataclass(frozen=True)
class Preference:
    """One thing she has learned, and the grounds for it."""

    kind: str
    key: str
    value: str
    standing: float
    source: str
    updated_at: float

    @property
    def is_actionable(self) -> bool:
        """Whether this is established enough to act on unasked."""
        return self.source == STATED or self.standing >= _MINIMUM_STANDING

    def because(self) -> str:
        """Why she believes it, in words a person can argue with."""
        if self.source == STATED:
            return "you told me that one"
        if self.standing >= 4:
            return "it's the one you usually mean"
        return "it's the one you've played before"


class UserProfile:
    """Preferences learned from what was said and what actually happened."""

    def __init__(self, *, path: Path | None = None, clock=None) -> None:
        self._clock = clock or time.time
        self._path = Path(path) if path is not None else _PROFILE_PATH
        self._entries: dict[str, dict[str, float | str]] = {}
        self._load()

    # ------------------------------------------------------------------
    # learning

    def observe(
        self, kind: str, value: str, *, key: str = "", source: str = OBSERVED,
    ) -> Preference | None:
        """Record one piece of evidence about what this person means."""
        kind = str(kind or "").strip()
        value = " ".join(str(value or "").split()).strip()
        if kind not in _KINDS or not value:
            return None
        key = " ".join(str(key or "").split()).strip().casefold()
        source = source if source in _WEIGHT_BY_SOURCE else OBSERVED

        if source == STATED:
            # Being told outright settles a disagreement rather than joining
            # it: everything else known for this slot loses standing.
            for entry_id, entry in self._entries.items():
                if (
                    entry["kind"] == kind
                    and entry["key"] == key
                    and str(entry["value"]).casefold() != value.casefold()
                ):
                    entry["standing"] = float(entry["standing"]) * _CORRECTION_DECAY

        entry_id = self._id(kind, key, value)
        entry = self._entries.setdefault(
            entry_id,
            {
                "kind": kind,
                "key": key,
                "value": value,
                "standing": 0.0,
                "source": source,
                "updated_at": 0.0,
            },
        )
        entry["standing"] = float(entry["standing"]) + _WEIGHT_BY_SOURCE[source]
        entry["updated_at"] = self._clock()
        if source == STATED:
            entry["source"] = STATED
        self._save()
        return self._as_preference(entry)

    def forget(self, kind: str, *, key: str = "") -> None:
        """Drop what is known for one slot, when it is plainly wrong."""
        key = " ".join(str(key or "").split()).strip().casefold()
        self._entries = {
            entry_id: entry
            for entry_id, entry in self._entries.items()
            if not (entry["kind"] == kind and entry["key"] == key)
        }
        self._save()

    # ------------------------------------------------------------------
    # recalling

    def preferred(self, kind: str, *, key: str = "") -> Preference | None:
        """The best-established value for this slot, if any is."""
        key = " ".join(str(key or "").split()).strip().casefold()
        candidates = [
            self._as_preference(entry)
            for entry in self._entries.values()
            if entry["kind"] == kind and entry["key"] == key
        ]
        if not candidates:
            return None
        best = max(
            candidates, key=lambda item: (item.standing, item.updated_at),
        )
        return best if best.is_actionable else None

    def known(self) -> tuple[Preference, ...]:
        """Everything currently believed, strongest first."""
        return tuple(
            sorted(
                (self._as_preference(entry) for entry in self._entries.values()),
                key=lambda item: (-item.standing, item.kind, item.key),
            )
        )

    # ------------------------------------------------------------------
    # storage

    @staticmethod
    def _id(kind: str, key: str, value: str) -> str:
        return "|".join((kind, key, value.casefold()))

    def _as_preference(self, entry: dict) -> Preference:
        return Preference(
            kind=str(entry["kind"]),
            key=str(entry["key"]),
            value=str(entry["value"]),
            standing=float(entry["standing"]),
            source=str(entry["source"]),
            updated_at=float(entry["updated_at"]),
        )

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A missing or damaged profile means she knows nothing yet,
            # which is a fine state to be in and never a reason to fail.
            return
        if not isinstance(raw, dict):
            return
        for entry_id, entry in raw.get("preferences", {}).items():
            if not isinstance(entry, dict) or entry.get("kind") not in _KINDS:
                continue
            try:
                self._entries[str(entry_id)] = {
                    "kind": str(entry["kind"]),
                    "key": str(entry.get("key", "")),
                    "value": str(entry["value"]),
                    "standing": float(entry.get("standing", 0.0)),
                    "source": str(entry.get("source", OBSERVED)),
                    "updated_at": float(entry.get("updated_at", 0.0)),
                }
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> None:
        try:
            ensure_runtime_directories()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"preferences": self._entries}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            # Remembering is a convenience; failing to write one must never
            # take down the turn that was otherwise about to succeed.
            pass
