"""Noticing that the person moved.

The machine already knows: the clock changed zone when they landed, and
the regional setting changes when they get round to it. Nothing read
either, so after a move to the United States she was still pricing things
in won and searching Korean sites, and the only sign was four clock tests
failing.

Deliberately only a *question*. Where someone shops is not settled by
which timezone their laptop is in -- they may be travelling, or keep
buying from home -- so this says what changed and asks, and the answer
they give is what changes the setting.

Both halves are stored under ``runtime/``, which is git-ignored: where
someone lives is theirs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

LANGUAGES = ("en", "ko")


@dataclass(frozen=True)
class Place:
    """What the machine says about where it is."""

    country: str = ""
    currency: str = ""
    timezone: str = ""
    offset_hours: float = 0.0

    @classmethod
    def from_stored(cls, payload) -> "Place | None":
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                country=str(payload.get("country") or ""),
                currency=str(payload.get("currency") or ""),
                timezone=str(payload.get("timezone") or ""),
                offset_hours=float(payload.get("offset_hours") or 0.0),
            )
        except (TypeError, ValueError):
            return None


def read(path: Path) -> Place | None:
    """Where she last thought they were, or None the first time."""
    try:
        return Place.from_stored(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def write(path: Path, place: Place) -> None:
    """Remember it for the next start-up. A failure here is not worth a
    turn: she simply asks again next time."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(place), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError:
        pass


def moved(before: Place | None, now: Place) -> bool:
    """Whether the clock moved zone. The regional setting is not the test:
    it is the thing that usually has *not* caught up -- measured, the clock
    was Pacific and Windows still said 한국."""
    if before is None:
        return False
    if not now.timezone or not before.timezone:
        return False
    if now.timezone == before.timezone:
        return False
    return abs(now.offset_hours - before.offset_hours) >= 1.0


def hours_between(before: Place, now: Place) -> int:
    return int(round(now.offset_hours - before.offset_hours))


def _utc(offset_hours: float) -> str:
    """UTC+09:00 -- stable, and the same in both languages. Windows reports
    zone *names* in the system language ("태평양 일광 절약 시간"), which is
    fine for telling two zones apart and wrong to say out loud."""
    sign = "+" if offset_hours >= 0 else "-"
    minutes = int(round(abs(offset_hours) * 60))
    return f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def sentence(before: Place, now: Place, language: str = "en") -> str:
    """What changed, and the question that follows from it.

    Names the consequence rather than the setting: prices and shops are
    what the person will notice, ``user.country`` is not.
    """
    moved_by = hours_between(before, now)
    was, here = _utc(before.offset_hours), _utc(now.offset_hours)
    if language == "ko":
        return (
            f"시계가 {was}에서 {here}로 {abs(moved_by)}시간 바뀌었습니다. "
            f"이사하셨나요? 추천과 가격은 아직 {before.country} 기준"
            f"({before.currency})으로 찾고 있습니다. 바꿔 드릴까요?"
        )
    direction = "ahead of" if moved_by > 0 else "behind"
    return (
        f"Your clock moved from {was} to {here}, {abs(moved_by)} hours "
        f"{direction} where it was. Did you move? I'm still looking things "
        f"up for {before.country} in {before.currency}, so say the word and "
        "I'll switch."
    )
