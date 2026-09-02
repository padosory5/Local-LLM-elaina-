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
from datetime import datetime
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


def read_place(text: str) -> str:
    """The place a time question names, as this module knows it."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    for match in _PLACE_AFTER.finditer(text):
        phrase = match.group(1) or match.group(2) or ""
        words = phrase.split()
        # Longest first: "new york" before "new".
        for size in range(min(_LONGEST, len(words)), 0, -1):
            candidate = " ".join(words[:size]).casefold().strip(",.;:!?")
            if candidate in _ZONES:
                return candidate
    for name in _ALSO_KNOWN:
        # Korean names carry no preposition and no capitalisation.
        if not name.isascii() and name in text:
            return name
    return ""


def clock_in(place: str) -> tuple[str, datetime] | None:
    """The zone and the current time there, or nothing if unknown."""
    zone = _ZONES.get(str(place or "").casefold().strip())
    if zone is None:
        return None
    try:
        return zone, datetime.now(ZoneInfo(zone))
    except Exception:
        return None


def describe(place: str) -> str:
    """One line stating the time and date there, computed not guessed."""
    found = clock_in(place)
    if found is None:
        return ""
    zone, moment = found
    return (
        f"In {place.title()} ({zone}) it is now "
        f"{moment.strftime('%I:%M %p on %A, %B %d, %Y')} "
        f"({moment.strftime('%Z')})."
    )
