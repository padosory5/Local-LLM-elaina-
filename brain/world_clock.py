"""What time it is somewhere that is not here.

Measured live, in the first dogfooding session:

    User:   Tell me the time in Seattle right now.
    Elaina: The time in Seattle right now is 07:57 PM on Wednesday,
            September 02, 2026.
    User:   That's not the time in Seattle right now.
    Elaina: It's 3:45 PM in Seattle right now.

07:57 PM was the time in Korea, where the user was sitting. The second
answer was invented: nothing in the process knew what time it was in
Seattle, because the only clock the model was given read

    Today is Wednesday, September 02, 2026.
    The current local time is 07:57 PM.

-- one clock, unlabelled, with no offset and no way to convert. Asking an
8B model to do timezone arithmetic against an unlabelled local time is
asking it to guess, and it guessed twice.

So the arithmetic happens here instead. The bulk of the mapping is not
written down: ``zoneinfo`` ships the IANA database and its zone names
*are* city names, so ``Asia/Seoul`` and ``Europe/London`` resolve without
anyone maintaining them. The table below covers only what that database
does not name -- cities that share a zone with the one it is named after,
and the country and region words people actually say.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

# Places the IANA database has no entry of its own for, because they share
# a zone with the city it is named after. A knowledge table in the same
# spirit as the market/site table: what the world is like, not what to do
# about it. Anything already named by a zone is resolved without help.
_ALSO_KNOWN = {
    # United States
    "seattle": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "san diego": "America/Los_Angeles",
    "portland": "America/Los_Angeles",
    "las vegas": "America/Los_Angeles",
    "silicon valley": "America/Los_Angeles",
    "california": "America/Los_Angeles",
    "washington state": "America/Los_Angeles",
    "boston": "America/New_York",
    "philadelphia": "America/New_York",
    "atlanta": "America/New_York",
    "miami": "America/New_York",
    "orlando": "America/New_York",
    "washington": "America/New_York",
    "washington dc": "America/New_York",
    "dc": "America/New_York",
    "austin": "America/Chicago",
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "texas": "America/Chicago",
    "seoul": "Asia/Seoul",
    # Countries and regions, by the words people say
    "korea": "Asia/Seoul",
    "south korea": "Asia/Seoul",
    "busan": "Asia/Seoul",
    "incheon": "Asia/Seoul",
    "daegu": "Asia/Seoul",
    "japan": "Asia/Tokyo",
    "osaka": "Asia/Tokyo",
    "kyoto": "Asia/Tokyo",
    "china": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "taiwan": "Asia/Taipei",
    "vietnam": "Asia/Ho_Chi_Minh",
    "thailand": "Asia/Bangkok",
    "philippines": "Asia/Manila",
    "india": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "uk": "Europe/London",
    "england": "Europe/London",
    "britain": "Europe/London",
    "great britain": "Europe/London",
    "united kingdom": "Europe/London",
    "scotland": "Europe/London",
    "edinburgh": "Europe/London",
    "manchester": "Europe/London",
    "france": "Europe/Paris",
    "germany": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "frankfurt": "Europe/Berlin",
    "spain": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "italy": "Europe/Rome",
    "milan": "Europe/Rome",
    "netherlands": "Europe/Amsterdam",
    "switzerland": "Europe/Zurich",
    "sweden": "Europe/Stockholm",
    "australia": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "new zealand": "Pacific/Auckland",
    "canada": "America/Toronto",
    "montreal": "America/Toronto",
    "ottawa": "America/Toronto",
    "brazil": "America/Sao_Paulo",
    "mexico": "America/Mexico_City",
    "guam": "Pacific/Guam",
    "hawaii": "Pacific/Honolulu",
    "alaska": "America/Anchorage",
}

# Said in Korean, because the person using this speaks it.
_ALSO_KNOWN.update({
    "서울": "Asia/Seoul",
    "한국": "Asia/Seoul",
    "부산": "Asia/Seoul",
    "시애틀": "America/Los_Angeles",
    "뉴욕": "America/New_York",
    "도쿄": "Asia/Tokyo",
    "런던": "Europe/London",
    "파리": "Europe/Paris",
})


def _from_the_database() -> dict[str, str]:
    """Every zone whose own name says which city it is."""
    found: dict[str, str] = {}
    for zone in available_timezones():
        if "/" not in zone:
            continue
        city = zone.rsplit("/", 1)[1].replace("_", " ").casefold()
        # First writer wins, so a curated alias is never overwritten by a
        # same-named city in another region.
        found.setdefault(city, zone)
    return found


_ZONES: dict[str, str] = _from_the_database()
_ZONES.update(_ALSO_KNOWN)

_LONGEST = max(len(name.split()) for name in _ZONES)

# "the time in Seattle", "what time is it in New York". The place follows a
# locative preposition; scanning the whole sentence for any known city name
# would find one in "Nice weather" and in half the surnames people say.
_PLACE_AFTER = re.compile(
    r"\b(?:in|at|for|over\s+in|back\s+in)\s+"
    r"([A-Za-z][\w.'-]*(?:\s+[A-Za-z][\w.'-]*){0,3})"
    # "the current time of Seattle" -- said live, and "of" is the whole
    # signal. It is allowed only directly after a clock word, because
    # "University of Washington" is a school in Seattle and would
    # otherwise resolve to the other Washington, three zones away.
    r"|\b(?:time|date|clock|hour)\s+(?:of|over\s+at)\s+"
    r"([A-Za-z][\w.'-]*(?:\s+[A-Za-z][\w.'-]*){0,3})",
)

_ASKS_THE_TIME = re.compile(
    r"\b(?:what|tell|current|right\s+now)\b.{0,40}\b(?:time|clock|date|day)\b"
    r"|\b(?:time|date)\b.{0,30}\b(?:right\s+now|now|there|today)\b"
    r"|몇\s*시|날짜",
    re.IGNORECASE | re.DOTALL,
)


def asks_the_time(text: str) -> bool:
    return bool(_ASKS_THE_TIME.search(str(text or "")))


# A second place, once a first has been read: "in Seattle compared with
# Seoul", "Seattle and Tokyo", "London vs Paris". Only ever after a place
# has already been found, or in a sentence that asks the time -- the same
# reason the rule above needs a preposition.
_PLACE_AFTER_CONNECTOR = re.compile(
    r"\b(?:and|or|vs\.?|versus|with|to|than|compared\s+(?:with|to))\s+"
    r"([A-Za-z][\w.'-]*(?:\s+[A-Za-z][\w.'-]*){0,3})",
    re.IGNORECASE,
)


def _known(phrase: str) -> str:
    words = phrase.split()
    # Longest first: "new york" before "new".
    for size in range(min(_LONGEST, len(words)), 0, -1):
        candidate = " ".join(words[:size]).casefold().strip(",.;:!?")
        if candidate in _ZONES:
            return candidate
    return ""


def read_places(text: str) -> list[str]:
    """Every place a time question names, in the order it names them.

    "What time is it in Seattle compared with Seoul?" names two. Reading
    only the first is how Seoul's time was left to the model, which gave
    "13 hours ahead" in 3 of 3 runs (docs/PHASE3_PLAN.md §1.3).
    """
    text = " ".join(str(text or "").split())
    if not text:
        return []
    found: list[tuple[int, str]] = []
    for match in _PLACE_AFTER.finditer(text):
        group = 1 if match.group(1) else 2
        name = _known(match.group(group) or "")
        if name:
            found.append((match.start(group), name))
    if found or asks_the_time(text):
        for match in _PLACE_AFTER_CONNECTOR.finditer(text):
            name = _known(match.group(1) or "")
            if name:
                found.append((match.start(1), name))
    for name in _ALSO_KNOWN:
        # Korean names carry no preposition and no capitalisation.
        if not name.isascii():
            position = text.find(name)
            if position >= 0:
                found.append((position, name))
    ordered: list[str] = []
    seen_zones: set[str] = set()
    for _, name in sorted(found):
        zone = _ZONES.get(name)
        if name not in ordered and zone not in seen_zones:
            ordered.append(name)
            seen_zones.add(zone)
    return ordered


def read_place(text: str) -> str:
    """The first place a time question names, as this module knows it."""
    places = read_places(text)
    return places[0] if places else ""


def clock_in(place: str) -> tuple[str, datetime] | None:
    """The zone and the current time there, or nothing if unknown."""
    zone = _ZONES.get(str(place or "").casefold().strip())
    if zone is None:
        return None
    try:
        return zone, datetime.now(ZoneInfo(zone))
    except Exception:
        return None


def local_now(zone: str = "") -> datetime:
    """Now, on the clock to treat as local.

    ``zone`` is an IANA name; "local", or nothing at all, means the
    machine's own -- which is what "here" has always meant. config.yaml
    has advertised the alternative since the time section was written
    ("You can later use an IANA name such as Asia/Seoul"), and nothing
    read it; this is where it is read.

    An unknown name falls back to the machine rather than raising. A
    typo in the config should cost the intended zone, not the clock.
    """
    name = str(zone or "").strip()
    if name and name.casefold() != "local":
        try:
            return datetime.now(ZoneInfo(name))
        except Exception:
            pass
    return datetime.now().astimezone()


def describe(place: str, here: datetime | None = None) -> str:
    """One line stating the time and date there, computed not guessed.

    The gap between the two clocks is stated too. Measured live, the local
    time was right and the model was left to work out the relationship:

        "It's 1:20 AM in Seattle right now. The time there is 13 hours
         behind Korea Standard Time."

    Seattle is sixteen hours behind Korea on that date, not thirteen. The
    two clocks were both in the prompt and the difference between them was
    not, so the one number nobody had computed was the one that was wrong.

    ``here`` is the clock to measure that difference from. The caller
    passes the same moment it put in the prompt as the local time, so the
    two clocks and the gap between them can never disagree.
    """
    found = clock_in(place)
    if found is None:
        return ""
    zone, moment = found
    line = (
        f"In {_title(place)} ({zone}) it is now "
        f"{moment.strftime('%I:%M %p on %A, %B %d, %Y')} "
        f"({zone_label(moment)})."
    )
    gap = _hours_from_here(moment, here)
    if gap:
        line += f" That is {gap}."
    return line


def _title(place: str) -> str:
    return place.title() if place.isascii() else place


def compare(first: str, second: str) -> str:
    """The gap between two named places, stated so nobody has to work it out."""
    a, b = clock_in(first), clock_in(second)
    if a is None or b is None:
        return ""
    hours = (
        (b[1].utcoffset() or timedelta()) - (a[1].utcoffset() or timedelta())
    ).total_seconds() / 3600
    if abs(hours) < 0.5:
        return f"{_title(second)} and {_title(first)} are on the same time."
    whole = int(abs(hours)) if abs(hours) == int(abs(hours)) else round(abs(hours), 1)
    plural = "" if whole == 1 else "s"
    way = "ahead of" if hours > 0 else "behind"
    return f"{_title(second)} is {whole} hour{plural} {way} {_title(first)}."


# ------------------------------------------------------------- saying it

_KOREAN_DAYS = "월화수목금토일"


def spoken_time(moment: datetime, language: str = "en", *, with_date: bool = True) -> str:
    """The time as a person says it in that language, computed here.

    Korean was left to the model, which translated "12:11 AM" itself and
    said 오후 for the hour after midnight in 3 of 3 runs. 오전 is before
    noon, 오후 after, and both count hours from 12.
    """
    hour, minute = moment.hour, moment.minute
    twelve = hour % 12 or 12
    if str(language or "").lower().startswith("ko"):
        clock = f"{'오전' if hour < 12 else '오후'} {twelve}시"
        if minute:
            clock += f" {minute}분"
        if not with_date:
            return clock
        day = _KOREAN_DAYS[moment.weekday()]
        return f"{moment.year}년 {moment.month}월 {moment.day}일 {day}요일 {clock}"
    clock = f"{twelve}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
    if not with_date:
        return clock
    return f"{clock}, {moment.strftime('%A, %B')} {moment.day}, {moment.year}"


# Zones to recognise the machine's own clock as, when the OS will not name
# it: Windows gives %Z in the display language, so on a Korean install the
# Pacific zone is "태평양 일광 절약 시간". Matched by offset and daylight
# saving against the tz database, never typed in.
_LIKELY_HOMES = (
    "America/Los_Angeles", "America/Denver", "America/Phoenix",
    "America/Chicago", "America/New_York", "America/Anchorage",
    "Pacific/Honolulu", "Asia/Seoul", "Asia/Tokyo", "Asia/Shanghai",
    "Europe/London", "Europe/Paris", "Australia/Sydney", "UTC",
)


def zone_label(moment: datetime) -> str:
    """A short, ASCII name for the zone this moment is in: "PDT", "KST"."""
    label = moment.strftime("%Z") or ""
    if label.isascii() and label.replace("+", "").replace("-", "").isalnum() and len(label) <= 6:
        return label
    offset = moment.utcoffset()
    if offset is None:
        return ""
    import time as _time

    in_dst = bool(_time.localtime().tm_isdst)
    for name in _LIKELY_HOMES:
        try:
            there = moment.astimezone(ZoneInfo(name))
        except Exception:
            continue
        if there.utcoffset() == offset and bool(there.dst()) == in_dst:
            return there.strftime("%Z")
    return ""


def _hours_from_here(moment: datetime, here: datetime | None = None) -> str:
    """How far that clock is from the local one, in plain words."""
    if here is None:
        here = local_now()
    # %Z is localised by the OS -- on Korean Windows it comes back as
    # "대한민국 표준시", which reads oddly inside an English sentence and
    # is not what the abbreviation is for. zone_label names it properly.
    local_zone = zone_label(here) or "your local time"
    offset = (moment.utcoffset() or timedelta()) - (
        here.utcoffset() or timedelta()
    )
    hours = offset.total_seconds() / 3600
    if abs(hours) < 0.5:
        return f"the same time as {local_zone}"
    whole = int(abs(hours)) if abs(hours) == int(abs(hours)) else round(abs(hours), 1)
    plural = "" if whole == 1 else "s"
    way = "ahead of" if hours > 0 else "behind"
    return f"{whole} hour{plural} {way} {local_zone}"
