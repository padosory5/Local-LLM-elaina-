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


# Korean attaches its particles directly to a noun -- 유튜브*에서*, 노래*나* --
# so a \b after a Korean word never matches: both sides are word
# characters. Korean alternatives are therefore listed without boundaries,
# and only the English ones keep them.
_SPOTIFY_CUE = re.compile(r"\bspotify\b|스포티파이", re.I)

# Somewhere else was named outright, so this is not her media app's
# request to answer -- "play it on youtube" means youtube.
_OTHER_MEDIA_SURFACE = re.compile(
    r"\b(?:youtube|netflix|disney|browser|chrome|whale|edge|firefox|"
    r"soundcloud|apple\s+music|spotify\s+web|vlc|twitch|melon|genie|bugs)\b"
    r"|유튜브|넷플릭스|멜론|지니",
    re.I,
)
# The words that make an app-less request a *music* request. Without
# one of these, "play chess" would be read as a song title -- so a
# bare play request is left to the router rather than assumed to be
# music.
_MEDIA_NOUN = re.compile(
    r"\b(?:song|songs|music|track|tracks|album|albums|playlist|tune|tunes)\b"
    r"|노래|음악|앨범|곡",
    re.I,
)
_NON_MUSIC_SUBJECT = re.compile(
    r"\b(?:chess|game|games|video|videos|movie|movies|film|films|"
    r"episode|episodes|show|shows|animation|sport|sports)\b",
    re.IGNORECASE,
)
# "Click Play in Spotify" asks for a button by name; the word "play" is the
# control, not the verb. Without this the subject came out as "in Spotify"
# and the media guard then refused a perfectly ordinary click.
_PLAY_AS_A_CONTROL = re.compile(
    r"\b(?:click|press|tap|hit|push|select)\s+(?:the\s+)?"
    r"(?:play|pause|resume|next|previous)\b",
    re.I,
)
_TRAILING_POLITENESS = re.compile(
    r"(?:\s+(?:for\s+me|please|now|right\s+now))+[.!?]*$", re.I,
)
_PLAY_REQUEST = re.compile(
    r"\b(?:play|put\s+on|listen\s+to|start)\s+(?P<subject>.+)$",
    re.I,
)
_COMPOUND_REQUEST = re.compile(
    r"\b(?:search(?:\s+for)?|find|look\s+up|lookup)\s+"
    r"(?P<subject>.+?)(?=\s*,?\s*(?:and|then)\s+"
    r"(?:open|play|put\s+on|start)\b)",
    re.I,
)
# Who the play verb belongs to. An instruction is addressed to her and has
# no subject of its own ("play Attention"), or names her ("can you play
# Attention"). A clause with any other subject is a statement about
# someone -- "I want to listen to music later", "when should I start
# applying" -- and reading those as requests is how a question about a
# 2027 internship reached ui_action with a standing Spotify preference and
# a ten-word "title", without the router ever being consulted.
#
# Read as the nearest preceding subject rather than as the presence of a
# word: "I'd like you to play Attention" contains both pronouns, and the
# one that governs the verb is the last one before it.
_SUBJECT_PRONOUN = re.compile(r"\b(i|we|they|he|she|it|you)\b", re.I)
_ADDRESSEE = frozenset({"you"})

# A title is a phrase; what follows a sentence boundary is another
# request. "?" and "!" always end one. A full stop is trusted only when at
# least two words follow it, because "Mr. Brightside" is one title and
# "Bang Bang. What is the weather?" is two sentences.
_SENTENCE_BREAK = re.compile(r"[?!]\s+\S|\.\s+\S+\s+\S")


def _addressed_to_her(text: str, verb_start: int) -> bool:
    """Whether the verb at ``verb_start`` is an instruction to her."""
    subjects = _SUBJECT_PRONOUN.findall(text[:verb_start])
    if not subjects:
        return True
    return subjects[-1].casefold() in _ADDRESSEE


def _first_sentence(subject: str) -> str:
    """The subject, cut where the sentence it sits in ends."""
    match = _SENTENCE_BREAK.search(subject)
    return subject if match is None else subject[:match.start()]


_ARTIST_SEPARATOR = re.compile(r"\s+(?:by|from)\s+", re.I)

# Korean puts the verb last and the app in a locative, so none of the
# patterns above can see it: "스포티파이에서 뱅뱅 틀어줘" is
# [in Spotify] [Bang Bang] [play-please]. Read on its own terms rather
# than translated into an English shape.
_KOREAN_PLAY_REQUEST = re.compile(
    r"^(?P<subject>.+?)\s*(?:을|를)?\s*(?:좀\s*)?"
    r"(?:틀어|재생\s*(?:해)?|들려|켜|플레이\s*(?:해)?)"
    r"(?:\s*(?:줘|주세요|줄래|주라|봐|라|다오))?\s*$"
)
# "스포티파이에서" / "스포티파이로" -- where to play it, not what to play.
_KOREAN_APP_LOCATIVE = re.compile(
    r"^\s*스포티파이\s*(?:에서|에|로|으로)?\s+|"
    r"\s*스포티파이\s*(?:에서|에|로|으로)?\s*$"
)
# Korean names the performer first, joined by the possessive: 아이브의 뱅뱅.
_KOREAN_POSSESSIVE = re.compile(r"^(?P<artist>.+?)의\s+(?P<title>.+)$")

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
    "a couple of", "a bunch of", "a few", "another", "any", "some", "random",
    "a", "an", "the", "my", "아무", "아무거나",
)
# Words that describe a *kind* of thing to play rather than one of them.
_CATEGORY_WORDS = frozenset({
    "song", "songs", "music", "track", "tracks", "tune", "tunes", "audio",
    "sound", "sounds", "stuff", "thing", "things", "something", "anything",
    "whatever", "playlist", "playlists", "list", "album", "albums", "mix",
    "노래", "노래나", "음악", "곡", "아무거나", "아무",
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


# The words a collection is made of. "My liked songs" is entirely these,
# so it names a place; "Bohemian Rhapsody from my liked songs" is not, so
# it names a track inside one.
_COLLECTION_WORDS = frozenset({
    "liked", "saved", "favourite", "favourites", "favorite", "favorites",
    "playlist", "playlists", "library", "queue", "list",
    # "좋아요 표시한 곡" is Spotify's own Korean name for Liked Songs; the
    # words between are part of the name, not a title inside it.
    "좋아요", "표시한", "표시된", "누른", "라이브러리", "재생목록",
    "플레이리스트", "내",
})


def _collection_only(subject: str) -> bool:
    """True when the whole subject is a place, with no item named in it."""
    text = " ".join(str(subject or "").split()).casefold().strip(" \"'")
    for quantifier in _QUANTIFIERS:
        if text.startswith(f"{quantifier} "):
            text = text[len(quantifier):].strip()
            break
    tokens = re.findall(r"[^\W_]+", text)
    return bool(tokens) and all(
        token in _COLLECTION_WORDS or token in _CATEGORY_WORDS
        for token in tokens
    )


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


def _without_application(subject: str, application: str) -> str:
    """Remove only the selected provider from the end of a media subject."""
    application = " ".join(str(application or "").split()).strip()
    if not application:
        return subject
    return re.sub(
        rf"\s+(?:in|inside|on|using|with)\s+(?:the\s+)?"
        rf"{re.escape(application)}\s*$",
        "",
        str(subject),
        flags=re.IGNORECASE,
    ).strip()


def _provider_is_explicit(text: str, application: str) -> bool:
    application = " ".join(str(application or "").split()).strip()
    if application.casefold() == "spotify" and _SPOTIFY_CUE.search(text):
        return True
    return bool(application and re.search(
        rf"(?<!\w){re.escape(application)}(?!\w)", text, re.IGNORECASE,
    ))


def classify_media_request(
    goal: str,
    *,
    application: str = "Spotify",
    preferred_provider: bool = False,
) -> MediaRequest:
    """Read a play request for the already-selected provider.

    Provider selection is deliberately outside this parser.  The parser only
    turns that decision into a typed target, so a saved ``TOOL_FOR`` value and
    an explicit one-task override reach the same execution path.
    """
    text = " ".join(str(goal or "").split()).strip()
    if not text:
        return MediaRequest("none")
    named_other = _OTHER_MEDIA_SURFACE.search(text)
    if named_other and not _provider_is_explicit(text, application):
        return MediaRequest("none")
    if _PLAY_AS_A_CONTROL.search(text):
        return MediaRequest("none")

    korean = _KOREAN_PLAY_REQUEST.match(text)
    match = (
        _COMPOUND_REQUEST.search(text)
        or _PLAY_REQUEST.search(text)
        or korean
    )
    if match is None:
        return MediaRequest("none")
    if match is not korean and not _addressed_to_her(text, match.start()):
        # Someone else's verb. Korean marks the request on the verb itself
        # and drops the subject, so the English subject test cannot read it
        # and is not applied to it.
        return MediaRequest("none")
    subject = _first_sentence(match.group("subject"))
    subject = _TRAILING_POLITENESS.sub("", subject).strip(" ,.!?")
    subject = _without_application(subject, application)
    if korean is not None and match is korean:
        # The app is where, not what.
        subject = _KOREAN_APP_LOCATIVE.sub("", subject).strip(" ,.!?")
    if not subject:
        return MediaRequest("none")

    # Naming the app is one way to mean music; saying "songs", naming a
    # collection, or naming an artist are the others. Measured live: the
    # app was previously required, so "play my liked songs" -- the request
    # this whole layer was built for -- typed as nothing at all.
    if not (
        _provider_is_explicit(text, application)
        or _MEDIA_NOUN.search(subject)
        or _named_collection(subject)
        or _ARTIST_SEPARATOR.search(subject)
        or _KOREAN_POSSESSIVE.match(subject)
        # A standing provider makes an otherwise app-less, title-shaped
        # request actionable ("Play Blinding Lights").  Keep the relaxation
        # narrow enough that a lower-case one-word activity such as "play
        # chess" is not silently turned into a song.
        or (
            preferred_provider
            and not _NON_MUSIC_SUBJECT.search(subject)
            and (
                len(re.findall(r"[^\W_]+", subject)) >= 2
                or subject[:1].isupper()
            )
        )
    ):
        return MediaRequest("none")

    possessive = _KOREAN_POSSESSIVE.match(subject)
    if possessive is not None and not _named_collection(subject):
        # 아이브의 뱅뱅 -- performer first, which is the opposite order to
        # "Bang Bang by IVE" and the reason a shared splitter cannot do it.
        title = possessive.group("title").strip(" \"'")
        artist = possessive.group("artist").strip(" \"'")
    else:
        pieces = _ARTIST_SEPARATOR.split(subject, maxsplit=1)
        title = pieces[0].strip(" \"'")
        artist = pieces[1].strip(" \"'") if len(pieces) == 2 else ""
    collection = _named_collection(artist) or _named_collection(subject)
    if artist and _named_collection(artist):
        # "from my liked songs" says where to look, not who performed it.
        artist = ""

    if _collection_only(subject):
        # "Play my liked songs" names a place and nothing inside it, so
        # there is no title here to be got wrong.
        return MediaRequest(
            "unclear",
            collection=collection or _named_collection(subject),
            subject=subject,
        )
    if _names_no_track(title):
        return MediaRequest("unclear", collection=collection, subject=subject)
    return MediaRequest(
        "track",
        target=MediaTarget(
            application=application or "Spotify", title=title, artist=artist,
        ),
        collection=collection,
        subject=subject,
    )


def classify_spotify_media_request(goal: str) -> MediaRequest:
    """Compatibility wrapper for existing direct Spotify callers."""
    return classify_media_request(goal)


def parse_spotify_media_target(goal: str) -> MediaTarget | None:
    """Return a Spotify track target when a concrete title was requested."""
    return classify_spotify_media_request(goal).target
