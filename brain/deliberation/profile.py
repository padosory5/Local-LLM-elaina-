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
# Said, but softly -- "I usually use X", as against "always use X". Worth
# remembering and not yet worth acting on unasked: at 1.5 it sits below the
# threshold, so one mention is kept and a second one (3.0) makes it real.
# That is exactly what "persistent, but lower confidence than always" means.
SUGGESTED = "suggested"

_WEIGHT_BY_SOURCE = {STATED: 2.0, SUGGESTED: 1.5, OBSERVED: 1.0}
# One play is not a taste. Something said outright clears this on its own.
_MINIMUM_STANDING = 2.0
# A correction should change behaviour, not be averaged into it.
_CORRECTION_DECAY = 0.5
# Kinds this profile knows about, so a typo cannot invent a new one.
ARTIST_FOR_TITLE = "artist_for_title"
FAVOURITE_TRACK = "favourite_track"
# What this person wants used, and what they want, in a given situation.
# Deliberately three kinds rather than one: "search restaurants on Naver
# Maps" and "when my throat hurts I get porridge from Bonjuk" are different
# claims -- one is how to look, the other is what to find -- and both can
# be true at once.
SOURCE_FOR = "source_for"        # which site or surface to search
TOOL_FOR = "tool_for"            # which capability or app to use
FAVOURITE_FOR = "favourite_for"  # which actual thing they go back to
_KINDS = frozenset({
    ARTIST_FOR_TITLE, FAVOURITE_TRACK, SOURCE_FOR, TOOL_FOR, FAVOURITE_FOR,
})

# Context rides in the key, after this separator: "restaurant" is the bare
# domain and "restaurant|sore throat" is the same domain in a situation.
# Kept as a key convention rather than a new field so nothing already
# written to disk has to be migrated, and so the lookup stays two lines.
CONTEXT_SEPARATOR = "|"


def context_key(domain: str, context: str = "") -> str:
    """The lookup key for a domain, optionally in a situation."""
    domain = " ".join(str(domain or "").split()).strip().casefold()
    context = " ".join(str(context or "").split()).strip().casefold()
    return f"{domain}{CONTEXT_SEPARATOR}{context}" if context else domain

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
        if self.source == SUGGESTED:
            return "you've said you usually prefer it"
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

    def forget_value(self, value: str) -> tuple[str, ...]:
        """Drop everything believed about one named thing.

        "Stop using Naver Maps by default" names the thing, not the slot,
        which is how people actually say it. Returns the keys dropped so
        the caller can say what it just stopped doing.
        """
        value = " ".join(str(value or "").split()).strip().casefold()
        if not value:
            return ()
        dropped = tuple(
            entry["key"]
            for entry in self._entries.values()
            if str(entry["value"]).casefold() == value
        )
        if not dropped:
            return ()
        self._entries = {
            entry_id: entry
            for entry_id, entry in self._entries.items()
            if str(entry["value"]).casefold() != value
        }
        self._save()
        return dropped

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

    def preferred_in(
        self, kind: str, domain: str, context: str = "",
    ) -> Preference | None:
        """What they want here, preferring the more specific answer.

        "When my throat hurts" beats a plain restaurant default, because it
        was said about exactly this situation. Falling back to the bare
        domain is what makes a general default a default rather than a rule
        that only fires in the one case it was learned in.
        """
        if context:
            specific = self.preferred(kind, key=context_key(domain, context))
            if specific is not None:
                return specific
        return self.preferred(kind, key=context_key(domain))

    def known_in(self, kind: str, domain: str) -> tuple[Preference, ...]:
        """Everything known for a domain, in any situation."""
        domain = " ".join(str(domain or "").split()).strip().casefold()
        return tuple(
            preference for preference in self.known()
            if preference.kind == kind
            and (
                preference.key == domain
                or preference.key.startswith(f"{domain}{CONTEXT_SEPARATOR}")
            )
        )

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
