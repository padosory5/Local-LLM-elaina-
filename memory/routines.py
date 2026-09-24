"""Named recordings of what the person did, kept as files they own.

"Record this" ... "stop recording" captures a run of clicks, pages, typing
and shortcuts (tools/screen_control/activity_recorder.py) and saves it here
under a name, so that "what did I record?", "what's in morning setup?" and,
from Milestone B, "do morning setup" can find it again.

One YAML file per recording in ``runtime/data/routines/``, like the
standing orders: readable, editable, and deletable by hand. Forgetting one
by voice moves its file into ``forgotten/`` rather than deleting it -- a
recording is something the person made, and "forget X" said about the wrong
X should be recoverable.

Each step keeps what a replay needs and a sentence does not (the control's
automation id, rectangle, the point within it, the window it was in). A
password field's text is never among it: the recorder does not read one.
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from core.paths import DATA_DIRECTORY, ensure_runtime_directories
from memory.activity_log import Activity

ROUTINES_DIRECTORY = DATA_DIRECTORY / "routines"

_HEADER = (
    "# A recording Elaina made of what you did on the computer.\n"
    "# You can rename it (the name field), edit the steps, or delete this\n"
    "# file. Typed text is kept only for fields that were not passwords."
)


@dataclass
class Routine:
    name: str
    steps: list[Activity] = field(default_factory=list)
    recorded_at: float = 0.0
    said: str = ""
    path: Path | None = None
    # Steps already turned into things she can do again
    # (brain/replay_plan.ReplayStep.as_dict) -- the start-up routine.
    plan: list[dict] = field(default_factory=list)


def name_key(text: str) -> str:
    """How names are compared: case and spacing do not matter."""
    return re.sub(r"[\s\"'“”‘’`.,!?]+", "", str(text or "").casefold())


class RoutineBook:
    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else ROUTINES_DIRECTORY

    @classmethod
    def load(cls, directory: Path | str | None = None) -> "RoutineBook":
        return cls(directory)

    # --------------------------------------------------------------- read

    def all(self) -> list[Routine]:
        if not self.directory.is_dir():
            return []
        routines = []
        for path in sorted(self.directory.glob("*.yaml")):
            routine = _read(path)
            if routine is not None:
                routines.append(routine)
        routines.sort(key=lambda routine: routine.recorded_at)
        return routines

    def names(self) -> tuple[str, ...]:
        return tuple(routine.name for routine in self.all())

    def named(self, name: str) -> Routine | None:
        key = name_key(name)
        if not key:
            return None
        for routine in self.all():
            if name_key(routine.name) == key:
                return routine
        return None

    def find(self, text: str) -> Routine | None:
        """The recording whose name the sentence mentions; the longest wins,
        so "morning setup 2" is not read as "morning setup"."""
        said = name_key(text)
        best: Routine | None = None
        for routine in self.all():
            key = name_key(routine.name)
            if len(key) >= 2 and key in said:
                if best is None or len(key) > len(name_key(best.name)):
                    best = routine
        return best

    def last(self) -> Routine | None:
        routines = self.all()
        return routines[-1] if routines else None

    # -------------------------------------------------------------- write

    def save(self, routine: Routine) -> tuple[Path, bool]:
        """Write it; a recording with the same name is replaced (the person
        recorded it again). Returns the path and whether one was replaced."""
        if self.directory == ROUTINES_DIRECTORY:
            ensure_runtime_directories()
        self.directory.mkdir(parents=True, exist_ok=True)
        existing = self.named(routine.name)
        path = existing.path if existing and existing.path else self._new_path(routine.name)
        body = {
            "name": routine.name,
            "recorded_at": datetime.fromtimestamp(
                routine.recorded_at or time.time(),
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "said": routine.said,
            "steps": [step.as_step() for step in routine.steps],
        }
        if routine.plan:
            body["plan"] = list(routine.plan)
        text = yaml.safe_dump(
            body, allow_unicode=True, sort_keys=False, default_flow_style=False,
        )
        path.write_text(f"{_HEADER}\n{text}", encoding="utf-8")
        routine.path = path
        return path, existing is not None

    def save_plan(self, name: str, plan: list[dict], *, said: str = "") -> Path:
        """Keep a list of things to do again under a name (the start-up
        routine is "startup")."""
        path, _ = self.save(Routine(
            name=name, recorded_at=time.time(), said=said, plan=list(plan),
        ))
        return path

    def plan(self, name: str) -> list[dict]:
        routine = self.named(name)
        return list(routine.plan) if routine is not None else []

    def forget(self, name: str) -> Routine | None:
        """Move a recording aside into ``forgotten/``. Returns it, or None."""
        routine = self.named(name)
        if routine is None or routine.path is None:
            return None
        aside = self.directory / "forgotten"
        aside.mkdir(parents=True, exist_ok=True)
        target = aside / routine.path.name
        if target.exists():
            target = aside / f"{routine.path.stem}-{int(time.time())}.yaml"
        shutil.move(str(routine.path), str(target))
        return routine

    def _new_path(self, name: str) -> Path:
        slug = re.sub(r"[^\w\-]+", "-", str(name).strip(), flags=re.UNICODE)
        slug = slug.strip("-")[:60] or "recording"
        path = self.directory / f"{slug}.yaml"
        number = 2
        while path.exists():
            path = self.directory / f"{slug}-{number}.yaml"
            number += 1
        return path


def _read(path: Path) -> Routine | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict) or not str(data.get("name") or "").strip():
        return None
    try:
        recorded_at = datetime.strptime(
            str(data.get("recorded_at") or ""), "%Y-%m-%d %H:%M:%S",
        ).timestamp()
    except ValueError:
        recorded_at = path.stat().st_mtime
    steps = [
        Activity.from_step(step) for step in data.get("steps") or []
        if isinstance(step, dict)
    ]
    return Routine(
        name=str(data["name"]).strip(), steps=steps, recorded_at=recorded_at,
        said=str(data.get("said") or ""), path=path,
        plan=[step for step in data.get("plan") or [] if isinstance(step, dict)],
    )
