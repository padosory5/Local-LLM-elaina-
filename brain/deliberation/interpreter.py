"""Utterance in, Goal out -- deterministically, and able to say "unknown".

Nothing here asks a model anything. Everything it can recognise, it
recognises from the words themselves, and what it cannot recognise it
leaves as an empty slot rather than filling with something plausible. An
empty slot is what later phases turn into a question; a plausible guess is
what put a whole sentence into Spotify's search box.
"""

from __future__ import annotations

import re

from brain.deliberation.goal import SOURCE_PROFILE, SOURCE_UTTERANCE, Goal, Slot
from brain.task_discovery_policy import TaskDiscoveryPolicy
from brain.media_target import (
    PLAYABLE_COLLECTIONS,
    classify_media_request,
)

# ChatEngine may append the original wording under the router's paraphrase.
# Both lines are the same request, so both are read.
_ORIGINAL_PREFIX = re.compile(
    r"^original\s+user\s+request\s*:\s*", re.IGNORECASE,
)

# A quoted string is the strongest statement that something is a value and
# not an instruction: "type 'see you at six' in Notepad".
_QUOTED = re.compile(r"[\"'‘“]([^\"'’”]{2,})[\"'’”]")

_TEXT_REQUEST = re.compile(
    r"\b(?:type|write|enter|input|paste)\s+(?P<value>.+?)"
    r"(?=\s+(?:in|into|inside|on|to)\b|$)",
    re.IGNORECASE,
)
# Takes the whole phrase, then removes only a trailing *place to act*. An
# earlier version cut at the first "in", which turned "hotels in Guam" into
# "hotels" -- the destination is part of what was asked for, the browser is
# not.
_SEARCH_REQUEST = re.compile(
    r"\b(?:search(?:\s+for)?|look\s+up|lookup|find)\s+(?P<value>.+)$",
    re.IGNORECASE,
)
# A value cannot begin with the word that introduces where it goes.
_DESTINATION_FIRST = re.compile(
    r"^(?:in|into|inside|on|onto|to|at|for)\b", re.IGNORECASE,
)
_INDIRECT_OBJECT = re.compile(r"^(?:me|us|for\s+me|for\s+us)\s+", re.IGNORECASE)
_SURFACE_TAIL = re.compile(
    r"\s+(?:in|inside|on|using|with)\s+(?:the\s+)?(?P<surface>\S+"
    r"(?:\s+(?:browser|app|window|page))?)\s*$",
    re.IGNORECASE,
)
_SURFACE_WORDS = frozenset({
    "browser", "app", "window", "page", "screen", "chrome", "whale", "edge",
    "firefox", "safari", "spotify", "notepad", "discord", "youtube",
    "google", "naver", "브라우저", "스포티파이", "메모장",
})
_OPEN_REQUEST = re.compile(
    r"\b(?:open|launch|start)\s+(?:the\s+)?(?P<value>.+?)"
    r"(?=\s+(?:and|then)\b|$)",
    re.IGNORECASE,
)
# Looking for something to choose among is a different request from
# looking a fact up, and it has preconditions a search does not: live hotel
# prices mean nothing without dates.
_BOOKING_VERB = re.compile(r"\b(?:book|reserve|reservation)\b", re.IGNORECASE)
_RESEARCH_SUBJECT = re.compile(
    r"\b(?:book|reserve|find|search(?:\s+for)?|look\s+up|lookup|compare|"
    r"recommend|show)\s+(?P<value>.+)$",
    re.IGNORECASE,
)

_TRAILING_NOISE = re.compile(
    r"(?:\s+(?:for\s+me|please|now|right\s+now))+[.!?]*$", re.IGNORECASE,
)

_SHUFFLE_COLLECTION = re.compile(
    r"\b(?:shuffle|random(?:ly)?\s+play)\b|셔플", re.IGNORECASE,
)
_FIND_IN_COLLECTION = re.compile(
    r"\b(?:find|look\s+for)\s+(?P<title>.+?)(?:\s+(?:in|from|on)\s+.+)?$"
    r"|(?P<korean_title>.+?)(?:을|를)?\s*(?:찾아|찾아줘)",
    re.IGNORECASE,
)


def _explicit_collection(text: str) -> str:
    lowered = str(text).casefold()
    if any(term in lowered for term in ("liked", "좋아요", "saved", "favourite", "favorite")):
        return "liked songs"
    return ""


def semantic_text(utterance: str) -> str:
    """The request as one line, with the router's bookkeeping removed."""
    lines = []
    for raw in str(utterance or "").splitlines():
        line = _ORIGINAL_PREFIX.sub("", raw.strip()).strip()
        if line:
            lines.append(line)
    return " ".join(lines) if lines else " ".join(str(utterance or "").split())


def _cleaned(value: str) -> str:
    text = _TRAILING_NOISE.sub("", str(value or "")).strip(" ,.!?\"'")
    return _INDIRECT_OBJECT.sub("", text).strip()


def _without_surface(value: str) -> str:
    """Drop a trailing "in the browser" / "on booking.com", keep "in Guam"."""
    match = _SURFACE_TAIL.search(value)
    if match is None:
        return value
    surface = match.group("surface").casefold().strip(" .,")
    words = surface.split()
    if (
        "." in surface
        or surface in _SURFACE_WORDS
        or (words and words[-1] in _SURFACE_WORDS)
    ):
        return value[:match.start()].strip()
    return value


def interpret(utterance: str, *, media_application: str = "") -> Goal:
    """Read a desktop request into slots, without inventing any."""
    text = semantic_text(utterance)
    if not text:
        return Goal(kind="unknown", utterance="")

    def said(name: str, value: str) -> dict[str, Slot]:
        return {name: Slot(name, value, SOURCE_UTTERANCE)}

    # Collection navigation is a real, observable desktop task in its own
    # right.  It must not be mistaken for a request to play a track named
    # "shuffle" or handed to the general planner without its collection.
    collection = _explicit_collection(text)
    if collection and _SHUFFLE_COLLECTION.search(text):
        return Goal(
            kind="shuffle_collection",
            utterance=text,
            slots=said("collection", collection),
        )
    if collection and ("scroll" in text.casefold() or "스크롤" in text):
        match = _FIND_IN_COLLECTION.search(text)
        title = ""
        if match is not None:
            title = (match.group("title") or match.group("korean_title") or "").strip(" ,.?!\"'")
        slots = said("collection", collection)
        if title:
            slots.update(said("title", title))
        return Goal(kind="find_in_collection", utterance=text, slots=slots)

    media = classify_media_request(
        text,
        application=media_application or "Spotify",
        preferred_provider=bool(media_application),
    )
    if media.kind == "track" and media.target is not None:
        slots = {
            "title": Slot("title", media.target.title, SOURCE_UTTERANCE),
            "query": Slot("query", media.target.search_query, SOURCE_UTTERANCE),
            "provider": Slot(
                "provider",
                media.target.application,
                SOURCE_UTTERANCE if media.target.application.casefold() in text.casefold()
                else SOURCE_PROFILE,
            ),
        }
        if media.target.artist:
            slots["artist"] = Slot(
                "artist", media.target.artist, SOURCE_UTTERANCE,
            )
        return Goal(kind="play_track", utterance=text, slots=slots)
    if media.kind == "unclear":
        if media.collection in PLAYABLE_COLLECTIONS:
            # They named a place rather than an item, and it is a place she
            # has a procedure for. Nothing is missing: "play my liked songs"
            # is a complete request for a different skill, not a vague one.
            return Goal(
                kind="play_collection",
                utterance=text,
                slots={
                    **said("collection", media.collection),
                    "provider": Slot(
                        "provider", media_application or "Spotify",
                        SOURCE_UTTERANCE
                        if (media_application or "Spotify").casefold() in text.casefold()
                        else SOURCE_PROFILE,
                    ),
                },
            )
        # Otherwise deliberately slotless: the request named a kind of thing
        # to play, never one of them, and the gate turns that into a question.
        slots = {
            "provider": Slot(
                "provider", media_application or "Spotify",
                SOURCE_UTTERANCE
                if (media_application or "Spotify").casefold() in text.casefold()
                else SOURCE_PROFILE,
            ),
        }
        if media.collection:
            slots.update(said("collection", media.collection))
        return Goal(kind="play_unnamed", utterance=text, slots=slots)

    category = TaskDiscoveryPolicy.category_for(text)
    booking = bool(_BOOKING_VERB.search(text))
    if category is not None and (
        booking or TaskDiscoveryPolicy.needs_discovery_conversation(text)
    ):
        # A request to choose among live options. Its preconditions come
        # from the same policy the task planner uses, so a hotel needs
        # dates here for exactly the reason it needs them there.
        name, _source_kind, _hint = category
        slots = said("category", name)
        subject = _RESEARCH_SUBJECT.search(text)
        if subject is not None:
            value = _cleaned(_without_surface(subject.group("value")))
            if value:
                slots.update(said("subject", value))
                # Also the value she would type into a search field, so the
                # typed-value boundary still has something to check against.
                slots.update(said("query", value))
        for key, value in TaskDiscoveryPolicy.extract_preferences(text).items():
            if key not in {"dates", "budget", "area"} or not value:
                continue
            # "in Guam on 2026-09-01" leaves the preposition attached to the
            # area; it is a slot value, so it should read as one.
            value = re.sub(
                r"\s+(?:on|in|at|for|from|to)$", "", value.strip(),
                flags=re.IGNORECASE,
            )
            if value:
                slots.update(said(key, value))
        return Goal(
            kind="booking" if booking else "research",
            utterance=text,
            slots=slots,
        )

    quoted = _QUOTED.search(text)
    typing = _TEXT_REQUEST.search(text)
    if typing is not None or (quoted is not None and _has_typing_verb(text)):
        value = _cleaned(
            quoted.group(1) if quoted is not None else typing.group("value")
        )
        if value and not _DESTINATION_FIRST.match(value):
            return Goal(
                kind="text_input", utterance=text, slots=said("text", value),
            )
        # "Type in Notepad" names where, not what: the words after the verb
        # are the destination. The kind is known and the value is not, which
        # is exactly what the gate turns into a question -- filling the slot
        # with "in Notepad" would have typed that into the document.
        return Goal(kind="text_input", utterance=text)

    searching = _SEARCH_REQUEST.search(text)
    if searching is not None:
        value = _cleaned(
            quoted.group(1) if quoted
            else _without_surface(searching.group("value"))
        )
        if value:
            return Goal(
                kind="search", utterance=text, slots=said("query", value),
            )

    opening = _OPEN_REQUEST.search(text)
    if opening is not None:
        value = _cleaned(opening.group("value"))
        if value:
            return Goal(
                kind="open_app", utterance=text, slots=said("app", value),
            )

    # Understood as a request, but nothing in it is a value to enter. That
    # is a fact worth carrying: it means nothing may be typed.
    return Goal(kind="generic", utterance=text)


def _has_typing_verb(text: str) -> bool:
    return bool(
        re.search(r"\b(?:type|write|enter|input|paste)\b", text, re.IGNORECASE)
    )
