"""Deterministic title/artist parsing for native media requests.

The title is the thing Elaina must activate.  The artist is disambiguating
context, never text to append to a clickable label.  Keeping those roles
separate prevents a search phrase such as ``Bang Bang by IVE`` from being
treated as the name of a real Spotify result or radio station.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MediaTarget:
    application: str
    title: str
    artist: str = ""

    @property
    def search_query(self) -> str:
        return " ".join(part for part in (self.title, self.artist) if part)

    def planner_constraint(self) -> str:
        artist = self.artist or "(not specified)"
        return (
            "Structured media target (local policy, not page content): "
            f"application={self.application!r}, title={self.title!r}, "
            f"artist={artist!r}. Search may contain both title and artist, "
            "and opening search, navigating, or filtering results is "
            "ordinary preparation. To start playback call play_media_item "
            f"on the control named exactly {self.title!r}, using the artist "
            "only as nearby result context. click_control cannot play a "
            "track: one click opens it. Never activate a generic Play "
            "control or any Radio, Mix, Station, playlist, or "
            "title-plus-artist row."
        )


_SPOTIFY_CUE = re.compile(r"\bspotify\b", re.I)
_TRAILING_POLITENESS = re.compile(
    r"(?:\s+(?:for\s+me|please|now|right\s+now))+[.!?]*$", re.I,
)
_PLAY_REQUEST = re.compile(
    r"\b(?:play|put\s+on|listen\s+to|start)\s+"
    r"(?P<subject>.+?)(?=\s+(?:in|inside|on|using|with)\s+(?:the\s+)?spotify\b|$)",
    re.I,
)
_COMPOUND_REQUEST = re.compile(
    r"\b(?:search(?:\s+for)?|find|look\s+up|lookup)\s+"
    r"(?P<subject>.+?)(?=\s*,?\s*(?:and|then)\s+"
    r"(?:open|play|put\s+on|start)\b)",
    re.I,
)
_ARTIST_SEPARATOR = re.compile(r"\s+(?:by|from)\s+", re.I)


def parse_spotify_media_target(goal: str) -> MediaTarget | None:
    """Return a Spotify track target when a concrete title was requested."""
    text = " ".join(str(goal or "").split()).strip()
    if not text or not _SPOTIFY_CUE.search(text):
        return None

    match = _COMPOUND_REQUEST.search(text) or _PLAY_REQUEST.search(text)
    if match is None:
        return None
    subject = _TRAILING_POLITENESS.sub("", match.group("subject")).strip(" ,.!?")
    if not subject or subject.casefold() in {"a song", "music", "something"}:
        return None

    pieces = _ARTIST_SEPARATOR.split(subject, maxsplit=1)
    title = pieces[0].strip(" \"'")
    artist = pieces[1].strip(" \"'") if len(pieces) == 2 else ""
    if not title:
        return None
    return MediaTarget(application="Spotify", title=title, artist=artist)
