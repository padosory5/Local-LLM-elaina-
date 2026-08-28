"""Deterministic reading of what a native media request actually named.

Two different questions, and conflating them is what produced the worst
failure this system has had. "Play Bang Bang by IVE" names a track: the
title is the thing to activate and the artist is disambiguating context,
never text to append to a clickable label. "Play any songs from my liked
list" names *no track at all* -- and the earlier parser, able to imagine
only one shape of request, read it as title="any songs", artist="my liked
list", searched for that, and typed the whole sentence into Spotify's
search box on top of the previous query.

So the parser answers three ways, not two: this is not a media request;
this is a request for one named track; or this is a media request that
named nothing to act on -- which has to go back to the person who made it,
not to the keyboard.
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


@dataclass(frozen=True)
class MediaRequest:
    """What a play request named, or that it named nothing."""

    # "none"    -- not a media request this parser handles
    # "track"   -- one specific track was named
    # "unclear" -- a media request that named no specific track
    kind: str
    target: MediaTarget | None = None
    # The collection the person referred to ("liked songs", "playlist"),
    # when they referred to one. Kept for the question, and for the
    # collection skill that will eventually handle it.
    collection: str = ""
    subject: str = ""

    @property
    def question(self) -> str:
        """What to ask when the request named nothing to act on.

        Honest about the current limit: she can start one named track, so
        the question asks for one rather than promising a shuffle she
        cannot yet perform.
        """
        if self.collection:
            whole = _COLLECTION_PHRASES.get(
                self.collection, f"your whole {self.collection}"
            )
            return (
                f"I can only start one specific song for now, not {whole}. "
                "Which song do you want?"
            )
        return "Which song would you like me to play?"


# How to name a collection inside the question, so it reads like a sentence
# rather than a slot being filled.
_COLLECTION_PHRASES = {
    "liked songs": "all your liked songs",
    "saved songs": "all your saved songs",
    "favourites": "all your favourites",
    "favorites": "all your favorites",
    "playlist": "a whole playlist",
    "library": "your whole library",
    "queue": "your whole queue",
}

# Collections she has a procedure for. Everything else named a place
# without naming which one -- "my playlist" is not a playlist -- and gets a
# question rather than a guess.
PLAYABLE_COLLECTIONS = frozenset({
    "liked songs", "saved songs", "favourites", "favorites",
})


def collection_phrase(collection: str) -> str:
    """How to name a collection inside a sentence about not playing it."""
    label = " ".join(str(collection or "").split()).casefold()
    if not label:
        return "that"
    return _COLLECTION_PHRASES.get(label, f"your whole {label}")


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

# A place inside the app, not a performer. "Play X from my liked songs"
# splits on the same word as "Play X by IVE", so without this the library
# itself becomes the artist -- which is exactly what happened live.
_COLLECTION_CUES = (
    ("liked", "liked songs"),
    ("좋아요", "liked songs"),
    ("saved", "saved songs"),
    ("favourite", "favourites"),
    ("favorite", "favorites"),
    ("playlist", "playlist"),
    ("플레이리스트", "playlist"),
    ("재생목록", "playlist"),
    ("library", "library"),
    ("라이브러리", "library"),
    ("queue", "queue"),
)

# Stripped before deciding whether anything was named. Articles are here
# too, so "a song" reads as unnamed while "A Sky Full of Stars" does not.
_QUANTIFIERS = (
    "a couple of", "a bunch of", "a few", "any", "some", "random",
    "a", "an", "the", "my", "아무", "아무거나",
)
# Words that describe a *kind* of thing to play rather than one of them.
_CATEGORY_WORDS = frozenset({
    "song", "songs", "music", "track", "tracks", "tune", "tunes", "audio",
    "sound", "sounds", "stuff", "thing", "things", "something", "anything",
    "whatever", "playlist", "playlists", "list", "album", "albums", "mix",
    "노래", "음악", "아무거나",
})
_GENRE_WORDS = frozenset({
    "kpop", "k", "pop", "jpop", "hiphop", "hip", "hop", "rap", "rock",
    "jazz", "lofi", "lo", "fi", "edm", "ballad", "ballads", "classical",
    "indie", "metal", "house", "techno", "rnb", "soul", "acoustic",
    "케이팝", "팝", "힙합", "재즈", "발라드", "클래식",
})


def _named_collection(text: str) -> str:
    lowered = " ".join(str(text or "").split()).casefold()
    for cue, label in _COLLECTION_CUES:
        if cue in lowered:
            return label
    return ""


def _names_no_track(title: str) -> bool:
    """True when a 'title' is a quantity or a category, not a song."""
    text = " ".join(str(title or "").split()).casefold().strip(" \"'")
    if not text:
        return True
    for quantifier in _QUANTIFIERS:
        if text == quantifier:
            return True
        if text.startswith(f"{quantifier} "):
            text = text[len(quantifier):].strip()
            break
    tokens = re.findall(r"[^\W_]+", text)
    if not tokens:
        return True
    return all(
        token in _CATEGORY_WORDS or token in _GENRE_WORDS for token in tokens
    )


def classify_spotify_media_request(goal: str) -> MediaRequest:
    """Read a Spotify play request without inventing what it did not say."""
    text = " ".join(str(goal or "").split()).strip()
    if not text or not _SPOTIFY_CUE.search(text):
        return MediaRequest("none")

    match = _COMPOUND_REQUEST.search(text) or _PLAY_REQUEST.search(text)
    if match is None:
        return MediaRequest("none")
    subject = _TRAILING_POLITENESS.sub("", match.group("subject")).strip(" ,.!?")
    if not subject:
        return MediaRequest("none")

    pieces = _ARTIST_SEPARATOR.split(subject, maxsplit=1)
    title = pieces[0].strip(" \"'")
    artist = pieces[1].strip(" \"'") if len(pieces) == 2 else ""
    collection = _named_collection(artist) or _named_collection(subject)
    if artist and _named_collection(artist):
        # "from my liked songs" says where to look, not who performed it.
        artist = ""

    if _names_no_track(title):
        return MediaRequest("unclear", collection=collection, subject=subject)
    return MediaRequest(
        "track",
        target=MediaTarget(application="Spotify", title=title, artist=artist),
        collection=collection,
        subject=subject,
    )


def parse_spotify_media_target(goal: str) -> MediaTarget | None:
    """Return a Spotify track target when a concrete title was requested."""
    return classify_spotify_media_request(goal).target
