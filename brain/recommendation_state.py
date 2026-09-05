"""The recommendation the conversation is currently working on.

A recommendation is not a turn, it is a problem that stays open across
several of them. Measured live, treating each turn independently produced
this:

    "What should I eat for dinner?"          -> Korean BBQ
    "I have a sore throat, something easy."  -> soft ramen or bibimbap
    "Pull up some spots for me."             -> Korean BBQ again

The third turn routed correctly and searched with none of what the second
turn established, because nothing carried it. ``SemanticGoal`` is rebuilt
from scratch every turn and holds three fields; the rich structure that
*does* hold constraints, candidates and evidence -- ``TaskState`` -- only
comes into existence when the task planner runs, which a conversational
recommendation never does.

So this is that same structure, reachable from the conversational path,
and stored in the session store that already keeps short-lived grounded
context (``brain/task_session.py``). Field names mirror ``TaskState`` on
purpose: ``constraints`` is its ``preferences``, ``candidates`` its
``collected_items``, ``evidence`` its ``collected_information``.

Two rules do most of the work:

* a constraint is never anonymous -- each carries the ``Slot`` provenance
  the deliberation layer already uses, so "you said soft" and "I assumed
  soft" stay distinguishable;
* a superseded value may never reach a query. That single rule is what
  stops "actually, my throat hurts" from still searching for Korean BBQ.

Nothing here calls a model. Every constraint is read from the words.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from brain.deliberation.goal import (
    SOURCE_ASKED,
    SOURCE_RESEARCH,
    SOURCE_UTTERANCE,
    SOURCE_WORLD,
    Slot,
)

DEFAULT_TTL_SECONDS = 20 * 60

# What kind of thing a constraint is. The distinction that matters is not
# semantic tidiness but whether the value belongs in a search box:
# "soft" and "under 500,000 won" narrow a search, "sore throat" does not.
# Searching for "sore throat restaurants" is how a constraint meant to
# explain a preference turns into a query that finds throat clinics.
PREFERENCE = "preference"      # a thing asked for: "Korean BBQ"
ATTRIBUTE = "attribute"        # a quality asked for: "soft", "electric"
BUDGET = "budget"
AREA = "area"
DATES = "dates"
HOUSING_TYPE = "housing_type"
SITUATION = "situation"        # why: "sore throat"
EXCLUSION = "exclusion"        # "nothing spicy"

QUERY_SAFE = (ATTRIBUTE, PREFERENCE, HOUSING_TYPE, BUDGET, AREA, DATES)
CONTEXT_ONLY = (SITUATION, EXCLUSION)

# Said when the person is replacing what they asked for, not adding to it.
_REVISION = re.compile(
    r"\b(?:actually|instead|on second thought|second thoughts|"
    r"changed my mind|never\s?mind|scratch that|forget (?:that|it)|"
    r"rather than that|no wait)\b"
    r"|아니(?:요|다)?\s|그냥\s+말고",
    re.IGNORECASE,
)

# A situation is a fact about the person that shapes what suits them. Read
# narrowly and verbatim: the architecture's job is to carry "sore throat"
# into the reasoning, never to decide what a sore throat implies.
_SITUATIONS = (
    re.compile(
        r"\bmy\s+([a-z][a-z ]{1,20}?\s+(?:hurts?|aches?|is\s+sore|are\s+sore))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s*(?:'ve|’ve|\s+have|\s+got)\s+(?:a|an)\s+"
        r"((?:sore|bad|upset|stiff|broken|sprained|runny|blocked)\s+[a-z]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s*(?:'m|’m|\s+am)\s+"
        r"((?:really\s+|a bit\s+|quite\s+)?"
        r"(?:sick|ill|unwell|tired|exhausted|hungover|pregnant|"
        r"allergic\s+to\s+[a-z]+|on\s+a\s+[a-z]+\s+diet))",
        re.IGNORECASE,
    ),
)

# What was asked for, and in what shape. "something soft" is a quality;
# "Korean BBQ" is a thing. The difference decides whether a later "actually"
# retires it.
#
# The thing may sit inside an infinitive -- "I want to get a guitar" -- so
# the marker and its verb are skipped rather than captured. Measured live,
# capturing them made "I just want to talk about you, my rents and stuff"
# into preference="to talk about you", which then became the subject of an
# apartment search and the phrase she read back to the user.
#
# Two guards keep that from simply moving the problem: the thing may not
# begin with a preposition, so "to talk *about you*" contributes nothing
# at all; and a preposition also ends it, so "to get an internship *in*
# summer 2027" leaves "internship" -- a phrase that can go in a search box.
_WANTED = re.compile(
    r"\b(?:want|wanted|looking for|prefer|feel like|craving|"
    r"in the mood for|fancy|after|find(?:\s+me)?|recommend(?:\s+me)?)\s+"
    r"(?:to\s+[a-z]+\s+)?"
    r"(?:some\s+|a\s+|an\s+|the\s+)?"
    r"(?!(?:in|on|at|about|for|with|from|to|of|near|around|under)\b)"
    r"(something\s+[a-z][a-z ]{1,30}?|anything\s+[a-z][a-z ]{1,30}?"
    r"|[a-z][a-z' -]{1,30}?)"
    r"(?=[,.;!?]|$|\s+(?:and|but|or|because|since|so|that|which|"
    r"in|on|at|about|for|with|from|near|around|under|"
    r"to\s+(?:eat|buy|get|play|drink|wear|use)))",
    re.IGNORECASE,
)

# "something soft" is a constraint on its own, with no verb in front of it.
# Kept separate from _WANTED because that pattern must still cut "a guitar
# to play" at "to play", while "easy to eat" has to survive whole.
_SOMETHING = re.compile(
    r"\b(?:something|anything)\s+([a-z][a-z ]{1,30}?)"
    r"(?=[,.;!?]|$|\s+(?:and|but|or|because|since|so|that|which))",
    re.IGNORECASE,
)

# A recommendation about something that exists in the world and can be
# bought or visited -- as opposed to "what should I cook tonight". The
# market decides what is available and at what price, so these queries get
# the user's own locale even when no word in them is market-sensitive.
_PURCHASE = re.compile(
    r"\b(?:buy|buying|purchase|purchasing|get|getting|shop|shopping|"
    r"order|ordering|pick\s+up|price|prices|cost|costs|"
    r"store|stores|shop|shops|retailer|retailers|dealer|dealers|"
    r"rent|rental|renting|lease|leasing|apartment|apartments|housing)\b"
    r"|사고|구매|구입|가격|어디서\s*파",
    re.IGNORECASE,
)

# Things whose two obvious kinds lead to completely different candidate
# sets, so that one question is worth asking before searching. A knowledge
# table in the same spirit as the category patterns and the place list --
# not a mapping from a constraint to an answer. Anything unlisted still
# gets asked, just in general terms.
_VARIANTS: dict[str, tuple[str, str]] = {
    "guitar": ("electric", "acoustic"),
    "bass": ("electric", "upright"),
    "piano": ("digital", "acoustic"),
    "keyboard": ("digital piano", "synth"),
    "drums": ("electronic", "acoustic"),
    "headphones": ("over-ear", "in-ear"),
    "earphones": ("wireless", "wired"),
    "bike": ("road", "mountain"),
    "bicycle": ("road", "mountain"),
    "laptop": ("Windows", "Mac"),
    "camera": ("mirrorless", "DSLR"),
    "watch": ("smart", "analogue"),
    "car": ("new", "used"),
}

# The order questions are worth asking in. Type first: it splits the
# candidate set in two, and a budget for the wrong kind of thing is a
# wasted question.
TYPE = "type"
DIMENSIONS = (TYPE, BUDGET)

_HOUSING = re.compile(
    r"\b(?:rent|rental|renting|lease|leasing|housing|apartments?|"
    r"studios?|one[- ]bedrooms?|two[- ]bedrooms?)\b",
    re.IGNORECASE,
)

_HOUSING_TYPE = re.compile(
    r"\b(?:like\s+)?(?:a\s+|an\s+)?"
    r"(studio|one[- ]bedroom|two[- ]bedroom|shared room|private room)\b",
    re.IGNORECASE,
)

_NEAR_SCHOOL = re.compile(
    r"\b(?:near|close\s+to|around)\s+(?:my|the)\s+school\b",
    re.IGNORECASE,
)

_ACKNOWLEDGEMENT = re.compile(
    r"^(?:(?:y+a+h*|yeah|yep|yes|sure|ok(?:ay)?|alright|right|"
    r"go\s+ahead|do\s+that|please|fine|sounds?\s+good|got\s+it)\s*)+$",
    re.IGNORECASE,
)


def is_acknowledgement(text: str) -> bool:
    normalized = " ".join(
        re.sub(r"[,!.?]", " ", str(text or "")).split()
    ).strip()
    return bool(normalized and _ACKNOWLEDGEMENT.fullmatch(normalized))


def complains_about_missing_results(text: str) -> bool:
    return bool(re.search(
        # This asked for a "why" and matched one phrasing of six. Measured
        # live, "You're showing me nothing." missed it entirely, was routed
        # as a fresh computer_action, came back unsupported, and she read
        # out her capability list -- one turn after running the browser
        # action being complained about.
        r"\bwhy\b.{0,40}\b(?:show|showing|find|finding|search|results?)\b"
        r"|\byou\s+said\s+(?:got\s+it|you\s+would)\b"
        r"|\b(?:you(?:'?re| are)?|it|that|this)\s+"
        r"(?:just\s+|only\s+)?"
        r"(?:show(?:ed|ing|s)?|gave|given|found|display(?:ed|ing|s)?|"
        r"did\s?n[o']t\s+show)\s+"
        r"(?:me\s+)?(?:nothing|anything|no\s+\w+)\b"
        r"|\b(?:nothing|no\s+results?|no\s+images?)\s+"
        r"(?:is|are|was|were)?\s*"
        r"(?:showing|shown|there|here|coming\s+up|loaded)\b"
        r"|\bthere(?:'?s| is| are)\s+nothing\s+(?:there|here|showing)\b"
        r"|\bi\s+do\s?n[o']t\s+see\s+(?:anything|any\b|it\b|them\b)"
        # Contradicting the claim itself. She said "Got it, one of those
        # websites is open"; the answer was "No it's not." That is about
        # the thing she just did, and it was routed as a fresh unsupported
        # request and answered with her capability list.
        r"|^\s*no,?\s+it(?:'?s|\s+is|\s+was)?\s*n[o']?t\b"
        r"|\b(?:that'?s|it'?s)\s+not\s+"
        r"(?:open|opened|showing|there|up|working|loaded)\b"
        r"|\bit\s+did\s?n[o']t\s+(?:open|load|work|show)\b"
        # The same denial with the agent as the subject rather than the
        # thing. English lets you say either, and only one was read.
        # Measured live: "you didn't open it." one turn after a URL she
        # reported opening -- routed as a fresh unsupported request, and
        # answered "I can't do that one", while listing browser control as
        # something she has. The verb list is closed on purpose: this is
        # about an action, so a denial that names no action ("you didn't
        # understand me") is not one of these.
        r"|\byou\s+(?:did\s?n[o']t|have\s?n[o']t|never|did\s+not)\s+"
        r"(?:actually\s+|even\s+|really\s+)?"
        r"(?:open(?:ed)?|load(?:ed)?|show(?:ed|n)?|find|found|"
        r"search(?:ed)?|play(?:ed)?|run|ran|click(?:ed)?|read|do|done)\b"
        # The subject may be a noun for the thing rather than a pronoun,
        # or absent altogether -- speech drops it constantly. Measured
        # live: "the website is not opened on my browser." and a bare
        # "didn't open it." were both read as fresh requests, and each was
        # answered by repeating the success claim it was contradicting.
        r"|\b(?:the\s+(?:site|website|page|tab|link))"
        r"(?:\s+(?:is|was|does)\s?n[o']t|(?:'?s|\s+is|\s+was)?\s+not)\s+"
        r"(?:open|opened|showing|there|up|working|load(?:ed|ing)?)\b"
        r"|^\s*(?:no,?\s+)?did\s?n[o']?t\s+"
        r"(?:open|load|work|show|find)\b"
        # The other half of "it didn't work": it worked, and it went
        # somewhere else. "That's not it" is about the destination, and it
        # is the shape a person uses when a page did load.
        r"|\b(?:that'?s|it'?s|this\s+is)\s+not\s+"
        r"(?:it\b|the\s+(?:one|right|correct)\b)"
        r"|\bwrong\s+(?:site|website|page|address|url|link|tab)\b"
        r"|\bnothing\s+(?:opened|loaded|happened|came\s+up)\b"
        r"|아무것도\s*안\s*보여|안\s*보여|안\s*열렸",
        str(text or ""),
        re.IGNORECASE,
    ))


# "No X" is two different turns wearing the same words. It excludes X --
# "no Zillow", don't use that site -- or it corrects a name she just got
# wrong. Measured live:
#
#   Elaina: Got it, it up on Zelo is open.
#   User:   no Zillow.
#   Constraints: ... exclusion=Zillow [utterance]
#
# What separates them is whether she just said something that sounds like
# it. "Zelo" and "Zillow" are one misheard syllable apart; "spicy" and a
# restaurant name are not. Similarity against her own previous words, not
# a list of site names.
_BARE_NEGATION = re.compile(
    r"^\s*no[,.!]?\s+(?P<name>[A-Za-z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)?)"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_SOUNDS_THE_SAME = 0.55


def correction_pair(text: str, *, said_before: str = "") -> tuple[str, str]:
    """The name meant and the one she used, when this turn corrects it."""
    match = _BARE_NEGATION.match(str(text or ""))
    if match is None:
        return "", ""
    name = match.group("name").strip(" .,!?")
    before = str(said_before or "")
    if not name or not before:
        return "", ""
    import difflib

    lowered = name.casefold()
    best = ("", 0.0)
    for word in re.findall(r"[A-Za-z][\w'’.-]{2,}", before):
        other = word.casefold()
        if other == lowered:
            # She already used this exact name, so "no X" is rejecting it.
            return "", ""
        score = difflib.SequenceMatcher(None, lowered, other).ratio()
        if score > best[1]:
            best = (word, score)
    if best[1] >= _SOUNDS_THE_SAME:
        return name, best[0]
    return "", ""


def corrects_a_named_surface(text: str, *, said_before: str = "") -> str:
    """The name this turn is correcting to, if that is what it is doing."""
    return correction_pair(text, said_before=said_before)[0]


def references_conversation_anchor(text: str) -> bool:
    """Whether this request explicitly points at a known conversational fact."""
    return bool(_NEAR_SCHOOL.search(str(text or "")))

_THINKING_ABOUT = re.compile(
    r"\b(?:thinking (?:about|of)|considering|might get|planning to get)\s+"
    r"(?:getting\s+|buying\s+|purchasing\s+|picking up\s+)?"
    r"(?:a\s+|an\s+|some\s+)?"
    r"([a-z][a-z' -]{1,30}?)(?=[,.;!?]|$|\s+(?:and|but|or|because|for))",
    re.IGNORECASE,
)

# "Where can I buy a guitar in Seoul?" names the thing after a buying verb,
# which _WANTED deliberately cuts at ("a guitar to play"). Without this the
# thing fell back to the router's subject -- "guitar stores" -- and she
# asked "what kind of stores did you have in mind?".
_BUYING = re.compile(
    r"\b(?:buy|buying|purchase|purchasing|order|shop\s+for|get\s+hold\s+of)\s+"
    r"(?:a\s+|an\s+|some\s+|the\s+)?"
    r"([a-z][a-z' -]{1,30}?)"
    r"(?=[,.;!?]|$|\s+(?:in|at|near|from|for|and|or|under|around|online))",
    re.IGNORECASE,
)

_EXCLUSIONS = (
    re.compile(
        r"\b(?:no|without|avoid|nothing)\s+"
        r"((?:too\s+)?[a-z][a-z ]{1,24}?)"
        r"(?=[,.;!?]|$|\s+(?:and|but|or|please|though))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bnot\s+(too\s+[a-z]+|[a-z]+y)\b(?!\s+sure)",
        re.IGNORECASE,
    ),
)

# A bare amount. TaskDiscoveryPolicy._BUDGET wants a lead-in word ("under",
# "up to"); a reply that is only "About 500,000 won" has none.
_BARE_MONEY = re.compile(
    r"([$₩€£¥]\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s*(?:won|krw|usd|eur|gbp|jpy|"
    r"dollars?|euros?|pounds?|yen|원|만원))",
    re.IGNORECASE,
)

# A range is two numbers, and only two. Measured live: "the phone
# number is 206-221-7857" was read as budget=206-221, and that went on
# into a rental search as "studio apartments September 13th 206-221 in
# South Korea". The lookaround says a link of a longer chain is not a
# price -- which separates a phone number, a date and a serial from an
# amount without needing to know what any of those words mean.
_MONEY_RANGE = re.compile(
    r"(?<![\d.,–—-])"
    r"((?:[$₩€£¥]\s?)?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:-|\u2013|\u2014|to|through)\s*"
    r"(?:[$₩€£¥]\s?)?\d[\d,]*(?:\.\d+)?(?![\d,])\s*"
    r"(?:won|krw|usd|eur|gbp|jpy|dollars?|euros?|pounds?|yen|원|만원)?)"
    r"(?!\s*[–—-]\s*\d)",
    re.IGNORECASE,
)

# The closed classes -- determiners, pronouns, prepositions, conjunctions,
# auxiliaries and the handful of discourse particles speech puts in front
# of everything. Not a list of phrases to watch for: it is the complement
# of "content word", used to ask whether a turn says anything of its own
# beyond the constraint it states. English adds no new members to these
# classes, which is what makes the set safe to fix in code.
_FUNCTION_WORDS = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "my", "your", "our",
    "their", "his", "her", "its", "some", "any", "no", "all", "both", "each",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "us", "them",
    "myself", "yourself", "itself",
    "am", "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "have", "has", "had", "will", "would", "can", "could", "shall",
    "should", "may", "might", "must", "let", "let's",
    "in", "on", "at", "to", "of", "for", "with", "from", "by", "about",
    "into", "onto", "over", "under", "up", "down", "out", "off", "as",
    "than", "then", "there", "here", "near", "around", "like", "just",
    "and", "or", "but", "so", "if", "because", "when", "while", "though",
    "not", "very", "really", "too", "also", "still", "yet", "again",
    "okay", "ok", "yeah", "yes", "well", "oh", "ah", "um", "uh", "please",
    "thanks", "thank", "hey", "hi", "actually", "maybe", "kinda", "sort",
    "kind", "lot", "bit", "much", "many", "more", "most", "now", "today",
    "what", "which", "who", "whom", "whose", "how", "why", "where",
})

# Words that are grammar, not content, once the specifics are stripped out.
_EMPTY_SUBJECTS = frozenset({
    "some", "something", "anything", "one", "ones", "it", "them", "those",
    "these", "any", "few", "couple", "options", "option", "stuff", "things",
    "thing",
})

# Real words, and poor search terms. Beaten by the category's own noun when
# one is known -- "soft restaurants" finds more than "soft places".
_WEAK_SUBJECTS = frozenset({
    "place", "places", "spot", "spots", "somewhere", "shop", "shops",
    "store", "stores", "one", "ones",
})

_FILLER = re.compile(
    r"^(?:the|a|an|some|any|me|my|of|for|that|this)\b\s*", re.IGNORECASE,
)

# Trailing words that add emphasis, not meaning. "something mild too" is a
# constraint on mildness; "mild too" is not a phrase worth searching for.
_TRAILING_FILLER = re.compile(
    r"\s*\b(?:too|also|as well|please|though|then|now|instead)\b\s*$",
    re.IGNORECASE,
)

# "Pull up some spots for me" is a request wrapper around one content word.
# Stripped so the wrapper does not become the search query when the subject
# has been retired out from under it.
_REQUEST_WRAPPER = re.compile(
    r"\b(?:pull\s+up|show|find|get|look\s+(?:up|for)|search\s+for|search|"
    r"check|bring\s+up|give)\b|\b(?:me|us|some|a\s+few|couple|please|"
    r"for\s+me|now)\b",
    re.IGNORECASE,
)


_ASKS_FOR_PLACES = re.compile(
    r"\b(?:place|places|spot|spots|shop|shops|store|stores|"
    r"restaurant|restaurants|somewhere|where|nearby|near\s+me|around\s+here)\b"
    r"|근처|어디",
    re.IGNORECASE,
)


_WANTS_TO_SEE = re.compile(
    r"\b(?:show|pull\s+up|bring\s+up|find|look\s+(?:up|for)|search|"
    r"list|give)\b.{0,20}?\b(?:me|some|a\s+few|them|options?|ones?)\b"
    r"|\b(?:show|pull\s+up|find)\s+(?:me\s+)?(?:some|a\s+few)\b"
    r"|\bwhat\s+(?:are|were)\s+(?:my|the)\s+options\b"
    r"|보여|찾아|알아봐",
    re.IGNORECASE,
)


def wants_to_see_options(request: str) -> bool:
    """Whether the turn asks to be shown real options, not given advice.

    "Show me some" carries no subject, no verb the router recognises as a
    lookup, and no evidence requirement -- measured live it was classified
    as plain conversation and answered from nothing, three turns into a
    recommendation that had a type and a budget ready to search on.
    """
    return bool(
        _WANTS_TO_SEE.search(str(request or ""))
        or complains_about_missing_results(request)
    )


def asks_where(request: str) -> bool:
    """Whether the turn asks *where*, rather than *which*."""
    return bool(re.search(
        r"\bwhere\b|\bwhich\s+(?:shop|store|place)", str(request or ""),
        re.IGNORECASE,
    ))


def supplies_only_a_place(text: str) -> str:
    """The place, when the turn is a place and nothing else.

    Measured live, one turn after a search for packing peanuts had gone
    out as "packing peanuts Seattle" -- Seattle inherited from a question
    about clocks twenty minutes earlier:

        User:   In Korea though.
        Elaina: Cool, you're in Korea! What's new there?

    The open task had already recorded ``area=Korea``. Nothing re-ran it,
    because the turn read as a remark, so the correction was acknowledged
    and then dropped.

    A place is the one dimension with a silent fallback behind it, which
    is what makes correcting it urgent: say nothing and the market is
    assumed, so the only way to tell the difference between "they did not
    say" and "they said, and it was ignored" is to act on the saying.

    Empty when the turn carries anything besides the place -- then it is a
    request of its own and gets routed as one.
    """
    said = " ".join(str(text or "").split())
    if not said:
        return ""
    areas = [
        slot.value for slot in read_constraints(said, source="utterance")
        if slot.name == AREA
    ]
    if len(areas) != 1:
        return ""
    place = areas[0]
    residual = re.sub(re.escape(place), " ", said, flags=re.IGNORECASE)
    for word in re.findall(r"[A-Za-z']+", residual):
        if word.casefold() not in _FUNCTION_WORDS:
            return ""
    return place


def _asks_for_places(request: str) -> bool:
    """Whether the turn asked for somewhere to go, rather than for advice."""
    return bool(_ASKS_FOR_PLACES.search(str(request or "")))


def _content_of(request: str) -> str:
    """What a request is *about*, once the asking is stripped off it."""
    return " ".join(
        _REQUEST_WRAPPER.sub(" ", str(request or "")).split()
    ).strip(" ,.;:!?-")


# The general thing a category is about, used when the specific thing the
# person named has been retired ("actually, not Korean BBQ") and the turn
# itself only says "show me some places".
_DOMAIN_NOUNS = {
    "restaurant": "restaurants",
    "hotel": "hotels",
    "gpu": "graphics cards",
    "car": "cars",
    "flight": "flights",
    "secondhand": "second-hand listings",
    "realestate": "apartments",
}


def category_for(text: str) -> str:
    """The discovery category key this request belongs to, or nothing."""
    if _HOUSING.search(str(text or "")):
        return "realestate"
    try:
        from brain.task_discovery_policy import TaskDiscoveryPolicy

        category = TaskDiscoveryPolicy.category_for(str(text or ""))
    except Exception:
        return ""
    return category[0] if category else ""


def domain_for(text: str) -> str:
    """The category noun this request belongs to, or nothing."""
    return _DOMAIN_NOUNS.get(category_for(text), "")


def is_purchase(text: str) -> bool:
    """Whether the turn is about acquiring something real."""
    return bool(_PURCHASE.search(str(text or "")))


_SUBJECT_WORDS = 10

# Said before getting to the point, and carrying no subject of its own.
_LEADING_FILLER = re.compile(
    r"^(?:(?:like|so|well|okay|ok|and|but|also|now|then|um|uh|yeah|"
    r"actually|basically|i\s+mean|you\s+know)\b[,\s]*)+",
    re.IGNORECASE,
)


def _as_phrase(value: str) -> str:
    """The request inside an utterance, as something searchable.

    Speech arrives whole, and the part that says what is wanted is
    usually its last clause: "What if you have a car? Like, give me some
    good places to travel along Washington State." The clause before it is
    a condition, and treating it as the subject is how a question about
    places to travel became a search about cars.
    """
    said = " ".join(str(value or "").split())
    if not said or len(said.split()) <= _SUBJECT_WORDS:
        return said
    clause = [part for part in re.split(r"[.?!]+", said) if part.strip()]
    chosen = (clause[-1] if clause else said).strip()
    chosen = _LEADING_FILLER.sub("", chosen).strip()
    chosen = _REQUEST_WRAPPER.sub(" ", chosen)
    chosen = _LEADING_FILLER.sub("", " ".join(chosen.split())).strip(" ,.;:!?-")
    words = chosen.split()
    return " ".join(words[:_SUBJECT_WORDS])


# A multi-word capitalised name -- an organisation, an institution, a
# place. Single capitalised words are excluded: too many of them are just
# sentence openings, and a one-word name is usually already in the subject.
_NAMED_ENTITY = re.compile(
    r"\b([A-Z][A-Za-z0-9&'’-]*"
    r"(?:\s+(?:of|the|and|de|del|van|von)\s+[A-Z][A-Za-z0-9&'’-]*"
    r"|\s+[A-Z][A-Za-z0-9&'’-]*){1,4})\b"
)


def _with_named_entities(core: str, request: str) -> str:
    """Keep a name the request introduced that the subject does not have.

    The held subject is usually the better search term -- it survives
    revisions and carries what several turns established. What it cannot do
    is know about an organisation the person named for the first time in
    this turn, and dropping that turns a question about an office into a
    question about a form.
    """
    core = " ".join(str(core or "").split())
    request = " ".join(str(request or "").split())
    if not request:
        return core
    lowered = core.casefold()
    for match in _NAMED_ENTITY.finditer(request):
        name = match.group(1).strip()
        if name.casefold() in lowered:
            continue
        # Sentence-initial capitals are grammar, not names.
        if match.start() == 0:
            continue
        core = f"{core} {name}".strip() if core else name
        lowered = core.casefold()
    return core


def _clean(value: str) -> str:
    value = " ".join(str(value or "").split()).strip(" ,.;:!?-")
    while True:
        stripped = _TRAILING_FILLER.sub("", _FILLER.sub("", value)).strip()
        if stripped == value:
            return value
        value = stripped


def _slot(name: str, value: str, source: str) -> Slot | None:
    value = _clean(value)
    if not value or value.casefold() in _EMPTY_SUBJECTS:
        return None
    if name == PREFERENCE and value.casefold() in _WEAK_SUBJECTS:
        # "Find some places" names no new object. Keep the active task;
        # qualified objects such as "guitar shops" still carry meaning.
        return None
    return Slot(name=name, value=value, source=source)


def revises(text: str) -> bool:
    """Whether this turn replaces what was asked for rather than adding."""
    return bool(_REVISION.search(str(text or "")))


def read_constraints(
    text: str, *, source: str = SOURCE_UTTERANCE, said_before: str = "",
) -> tuple[Slot, ...]:
    """Every constraint this utterance states, read from the words alone.

    Deliberately incomplete rather than speculative: an unrecognised
    sentence contributes nothing instead of contributing a guess. What is
    missed shows up as a thinner query, which is recoverable; what is
    invented shows up as a confident wrong recommendation, which is not.
    """
    text = " ".join(str(text or "").split())
    if not text:
        return ()
    if corrects_a_named_surface(text, said_before=said_before):
        # "no Zillow" right after "Zelo is open" is fixing the name, not
        # banning the site. Reading it as an exclusion put Zillow on the
        # forbidden list of the very task it was meant to point at.
        return ()
    found: list[Slot] = []

    housing_type = _HOUSING_TYPE.search(text)
    if housing_type is not None:
        slot = _slot(HOUSING_TYPE, housing_type.group(1).casefold(), source)
        if slot is not None:
            found.append(slot)

    for pattern in _SITUATIONS:
        for match in pattern.finditer(text):
            slot = _slot(SITUATION, match.group(1), source)
            if slot is not None:
                found.append(slot)

    for pattern in (_SOMETHING, _WANTED, _THINKING_ABOUT, _BUYING):
        for match in pattern.finditer(text):
            raw = match.group(1)
            lowered = raw.casefold().strip()
            # "something soft" is a quality; "Korean BBQ" is a thing. Only
            # the latter is a preference a later "actually" retires.
            if pattern is _SOMETHING:
                slot = _slot(ATTRIBUTE, raw, source)
            elif lowered.startswith(("something ", "anything ")):
                slot = _slot(ATTRIBUTE, raw.split(" ", 1)[1], source)
            else:
                slot = _slot(PREFERENCE, raw, source)
            if slot is not None:
                found.append(slot)

    for pattern in _EXCLUSIONS:
        for match in pattern.finditer(text):
            slot = _slot(EXCLUSION, match.group(1), source)
            if slot is not None:
                found.append(slot)

    try:
        from brain.task_discovery_policy import TaskDiscoveryPolicy

        # The budget/area/date readers already exist and are already tested
        # against live phrasing; there is no reason for a second set.
        for name, value in TaskDiscoveryPolicy.extract_preferences(
            text,
        ).items():
            if name in {BUDGET, AREA, DATES}:
                if name == BUDGET:
                    bounded = re.search(
                        rf"\b(?:under|below|less than|up to)\s*{re.escape(value)}", text, re.I,
                    )
                    if bounded:
                        value = bounded.group(0)
                # This is a relationship to an anchor, not an area named
                # "school". The anchor is resolved from conversation context.
                if name == AREA and re.fullmatch(
                    r"(?:my\s+|the\s+)?school", value, re.IGNORECASE,
                ):
                    continue
                slot = _slot(name, value, source)
                if slot is not None:
                    found.append(slot)
    except Exception:
        pass

    money_range = _MONEY_RANGE.search(text)
    if money_range is not None:
        found = [slot for slot in found if slot.name != BUDGET]
        slot = _slot(BUDGET, money_range.group(1), source)
        if slot is not None:
            found.append(slot)
    elif not any(slot.name == BUDGET for slot in found):
        bare = _BARE_MONEY.search(text)
        if bare is not None:
            slot = _slot(BUDGET, bare.group(1), source)
            if slot is not None:
                found.append(slot)

    return _prune(found)


def _prune(found: list[Slot]) -> tuple[Slot, ...]:
    """Drop a reading that another reading already contains.

    Two patterns legitimately match "want something easy to eat" -- one
    keyed on "something", one on "want" -- and they cut the phrase in
    different places, leaving both "easy to eat" and "easy". Keeping the
    truncated one would put a weaker word in the query alongside the
    stronger one.
    """
    kept: list[Slot] = []
    for slot in found:
        longer = any(
            other is not slot
            and other.name == slot.name
            and other.value.casefold() != slot.value.casefold()
            and f" {other.value.casefold()} ".startswith(
                f" {slot.value.casefold()} ",
            )
            for other in found
        )
        if not longer:
            kept.append(slot)
    return tuple(kept)


def read_short_reply(text: str) -> tuple[Slot, ...]:
    """A bare answer to a question, taken as a constraint on the open problem.

    "Electric." is not a sentence any of the readers above can parse, and
    it is exactly how people answer "electric or acoustic?". Only used
    while a problem is already open, so a stray one-word utterance cannot
    invent a constraint out of nothing.
    """
    text = " ".join(str(text or "").split()).strip(" .!?")
    if (
        not text
        or len(text.split()) > 3
        or is_acknowledgement(text)
    ):
        return ()
    # "Show me some" is three words and an instruction, not an answer.
    # Without this it became an attribute and went into the search query --
    # and so did "Find some places", whose content word is only "places".
    content = _content_of(text)
    words = {
        word for word in re.findall(r"[a-z0-9가-힣]+", content.casefold())
    }
    if not words - _EMPTY_SUBJECTS - _WEAK_SUBJECTS:
        return ()
    # A quality, not a name: "Electric." reads badly mid-sentence and
    # duplicates "electric" from the router topic in a query.
    slot = _slot(ATTRIBUTE, text.casefold(), SOURCE_ASKED)
    return (slot,) if slot is not None else ()


# A reply that is only an amount, said in answer to a question about one.
_BARE_AMOUNT = re.compile(
    r"^\s*(?:around|about|maybe|roughly|up\s+to|under|max|like)?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?(?:\s*[kK])?)"
    r"\s*(?:or\s+so|ish|max|at\s+most)?\s*[.!]?\s*$",
)

# Said instead of the answer, because they already gave it. Re-asking the
# identical question after this is the worst available reply: it says the
# earlier answer was not heard *and* asks for it again. Measured live:
#
#     Elaina: What type of housing did you have in mind?
#     User:   same as I said.
#     Elaina: What type of housing did you have in mind?
_POINTS_BACK_AT_AN_ANSWER = re.compile(
    r"^\s*(?:the\s+)?same(?:\s+(?:as|thing|one))?"
    r"(?:\s+(?:as\s+)?(?:i|you)\s+said)?"
    r"(?:\s+(?:as\s+)?(?:before|earlier|last\s+time|previously))?"
    r"\s*[.!]?\s*$"
    r"|^\s*(?:like|as)\s+i\s+said(?:\s+(?:before|earlier))?\s*[.!]?\s*$"
    r"|^\s*아까(?:\s*말한)?(?:\s*거)?(?:랑)?\s*(?:같(?:은|이))?\s*[.!]?\s*$",
    re.IGNORECASE,
)


def points_at_an_earlier_answer(text: str) -> bool:
    """Whether this reply defers to something the person already said."""
    return bool(_POINTS_BACK_AT_AN_ANSWER.match(" ".join(str(text or "").split())))


def _option_named(text: str, options) -> str:
    """Which of a closed set of options this text names, if exactly one.

    A question that offers two answers can be answered in a sentence, and
    people do. Measured live, against "Electric or acoustic?":

        "Electric, I said electric."   -> not an answer
        "Electric, you ..."            -> not an answer
        "electric"                     -> accepted

    Three turns of the same question because the reply was four words
    long. When the question named the options, finding one of them in the
    reply is the whole job; the rest of the sentence is the person being
    understandably short with her.
    """
    said = str(text or "").casefold()
    words = set(re.findall(r"[a-z0-9가-힣'-]+", said))
    found = [
        option for option in (options or ())
        if set(re.findall(r"[a-z0-9가-힣'-]+", option.casefold())) <= words
    ]
    # Both named is a question, not an answer.
    return found[0] if len(found) == 1 else ""


def answer_for_dimension(
    dimension: str, text: str, *, options=(),
) -> Slot | None:
    """Return a plausible typed answer to one outstanding dimension.

    This is deliberately stricter than ``read_short_reply``. A clarification
    may consume a turn only when the words contain a value for the exact
    dimension being asked; acknowledgements and consent words are never
    recommendation values.
    """
    said = " ".join(str(text or "").split()).strip(" .!?")
    if not said or is_acknowledgement(said):
        return None
    constraints = read_constraints(said, source=SOURCE_ASKED)
    if dimension == HOUSING_TYPE:
        return next(
            (slot for slot in constraints if slot.name == HOUSING_TYPE), None,
        )
    if dimension == BUDGET:
        found = next(
            (slot for slot in constraints if slot.name == BUDGET), None,
        )
        if found is not None:
            return found
        # A bare number in answer to "what sort of budget are you
        # thinking?" is a budget. The constraint reader will not say so,
        # and must not: in open conversation "1500" is a number, and
        # reading loose digits as money is how half a phone number became
        # a rental budget. Here the question supplies what the words do
        # not. Measured live: "1500" was refused and the same question
        # asked again; "$1500" was accepted.
        amount = _BARE_AMOUNT.match(said)
        if amount is not None:
            return _slot(BUDGET, amount.group("amount"), SOURCE_ASKED)
        return None
    if dimension == TYPE:
        chosen = _option_named(said, options)
        if chosen:
            return _slot(ATTRIBUTE, chosen, SOURCE_ASKED)
        short = read_short_reply(said)
        return short[0] if short else None
    return next((slot for slot in constraints if slot.name == dimension), None)


def apply_dimension_answer(
    problem: RecommendationProblem,
    dimension: str,
    text: str,
    *,
    now: float | None = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> RecommendationProblem | None:
    """Resolve one owned clarification without reinterpreting the reply."""
    answer = answer_for_dimension(
        dimension, text, options=problem.type_options(),
    )
    if answer is None:
        return None
    now = now if now is not None else time.monotonic()
    return replace(
        problem,
        constraints=_merge(problem.constraints, (answer,)),
        turns=problem.turns + 1,
        expires_at=now + ttl,
    )


@dataclass(frozen=True)
class RecommendationProblem:
    """The open recommendation, and everything established about it.

    Mirrors the ``TaskState`` fields that matter here rather than inventing
    a second vocabulary for them: ``constraints`` is its ``preferences``,
    ``candidates`` its ``collected_items``, ``evidence`` its
    ``collected_information``, and ``verification_level`` is the same
    "discover" / "verify" judgement.
    """

    id: str = ""
    subject: str = ""
    domain: str = ""
    # The discovery category key ("restaurant", "hotel"), kept alongside the
    # noun so the locale layer can be asked in its own terms.
    category: str = ""
    # Whether this is about something real that can be bought or visited,
    # as opposed to advice. Decides whether the query gets the user's market.
    purchase: bool = False
    # Resolved once with the task, then consumed by query shaping and ranking.
    entity_type: str = ""
    # Resolved task context copied from the existing conversation focus.
    # Explicit task constraints still outrank these background facts.
    location: str = ""
    anchor: str = ""
    relationship: str = ""
    lookup_requested: bool = False
    authorization_source: str = ""
    # Dimensions already put to the person. One question each, ever: a
    # re-asked question reads as not having listened.
    asked: tuple[str, ...] = ()
    # "Use Google Maps for this one" -- held for as long as this problem is
    # open, not for one message. A clarifying question in the middle of the
    # task must not drop back to the saved default halfway through; a new
    # task starts a new problem and therefore drops it.
    source_override: str = ""
    constraints: tuple[Slot, ...] = ()
    superseded: tuple[Slot, ...] = ()
    candidates: tuple[Any, ...] = ()
    evidence: tuple[str, ...] = ()
    verification_level: str = "discover"
    previous_recommendation: str = ""
    turns: int = 0
    expires_at: float = 0.0

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) >= self.expires_at

    def values(self, *names: str) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for name in names:
            for slot in self.constraints:
                if slot.name != name:
                    continue
                key = slot.value.casefold()
                if key in seen:
                    continue
                seen.add(key)
                out.append(slot.value)
        return tuple(out)

    def _thing(self) -> str:
        """The noun this recommendation is about, as a single word."""
        if self.category == "realestate" or self.domain == "apartments":
            return "housing"
        for slot in self.constraints:
            if slot.name == PREFERENCE:
                return slot.value.split()[-1].casefold()
        words = [
            word for word in self.subject.split()
            if word.casefold() not in _WEAK_SUBJECTS
        ]
        return words[-1].casefold() if words else ""

    def type_options(self) -> tuple[str, ...]:
        """The two kinds this thing comes in, when they are known."""
        return _VARIANTS.get(self._thing(), ())

    def missing_dimension(self) -> str:
        """The one unresolved thing worth asking about, or nothing.

        At most one, and only when the answer would genuinely change which
        candidates come back. Everything else is left to a sensible default
        -- five questions before a suggestion is worse than a suggestion
        that turns out to be slightly off.
        """
        thing = self._thing()
        # "I need headphones" names no buying verb and is plainly about
        # buying headphones. A thing whose kinds are known is a thing you
        # acquire, so it counts on its own.
        if not thing or not (self.purchase or thing in _VARIANTS):
            # Advice ("what should I eat tonight") is low-stakes: suggest
            # something rather than interrogate.
            return ""
        # And the thing has to have a shape worth asking about. ``purchase``
        # alone is not enough, because _PURCHASE contains "get" -- the most
        # general verb in English. Measured live, that produced
        #
        #     "What kind of summer did you have in mind?"
        #     "What kind of permit did you have in mind?"
        #     "What kind of time did you have in mind?"
        #     "Got it. What sort of budget are you thinking?"
        #
        # for an internship timeline, an international driving permit and a
        # request for someone's contact details. Getting an internship is
        # an acquisition; it is not a purchase with a budget.
        #
        # This is the rule the docstring above already states -- ask only
        # when the answer would genuinely change which candidates come
        # back -- enforced rather than assumed. Either the thing has two
        # obvious kinds, or it belongs to a discovery category that has a
        # market. Otherwise a generic "what kind of X" is worse than
        # silence, because X is whatever word the sentence ended on.
        if thing not in _VARIANTS and not self.category and not self.domain:
            return ""
        if (
            thing == "housing"
            and HOUSING_TYPE not in self.asked
            and not self.values(HOUSING_TYPE)
        ):
            return HOUSING_TYPE
        if (
            thing != "housing"
            and TYPE not in self.asked
            and not self.values(ATTRIBUTE)
            # Never ask for something the person already said. Measured
            # live: "Find me an electric guitar under 500,000 won" was
            # answered "Electric or acoustic?", and then three more times,
            # because the word was in the subject and not in a constraint.
            and not _option_named(self.subject, _VARIANTS.get(thing, ()))
        ):
            return TYPE
        if (
            BUDGET not in self.asked
            and not self.values(BUDGET)
            and (self.values(ATTRIBUTE) or self.values(HOUSING_TYPE))
        ):
            # Only worth asking once the kind is settled -- a budget for
            # the wrong kind of thing decides nothing.
            return BUDGET
        return ""

    def question_for(self, dimension: str) -> str:
        """How to ask for that dimension, in one short sentence."""
        thing = self._thing()
        if dimension == TYPE:
            variants = _VARIANTS.get(thing)
            if variants:
                return f"{variants[0].capitalize()} or {variants[1]}?"
            return f"What kind of {thing or 'one'} did you have in mind?"
        if dimension == HOUSING_TYPE:
            return "What type of housing did you have in mind?"
        if dimension == BUDGET:
            return "What sort of budget are you thinking?"
        return ""

    @property
    def real_world(self) -> bool:
        """Whether the answer names things that exist in a market."""
        return bool(self.domain or self.category or self.purchase or self.entity_type)

    @property
    def retired_values(self) -> tuple[str, ...]:
        return tuple(slot.value for slot in self.superseded)

    def as_task_preferences(self) -> dict[str, str]:
        """The same constraints in TaskState's own ``preferences`` shape."""
        preferences: dict[str, str] = {}
        for slot in self.constraints:
            existing = preferences.get(slot.name)
            preferences[slot.name] = (
                f"{existing}, {slot.value}" if existing else slot.value
            )
        return preferences

    def strip_retired(self, text: str) -> str:
        """Remove anything the conversation has already moved on from.

        The router still sees the whole history, so on the turn after a
        revision its topic can come back as "Korean BBQ places" -- the very
        thing that was just retired. One rule covers every such route in:
        a superseded value may not appear in a query.
        """
        cleaned = " ".join(str(text or "").split())
        for retired in self.retired_values:
            cleaned = re.sub(
                re.escape(retired), " ", cleaned, flags=re.IGNORECASE,
            )
        return " ".join(cleaned.split()).strip(" ,.;:-")

    def search_query(self, fallback: str = "") -> str:
        """What to actually search for, given everything established.

        Qualities lead, the thing follows, and the numbers trail -- "soft
        mild restaurants near Gangnam" rather than the raw utterance or a
        bare subject. Situations stay out of it: they explain the request,
        and a search for "sore throat restaurants" finds clinics.
        """
        if self.category == "realestate" or self.domain == "apartments":
            housing_type = " ".join(self.values(HOUSING_TYPE))
            parts = [f"{housing_type} apartments".strip()]
            if self.anchor:
                parts.append(f"near {self.anchor}")
            elif self.relationship:
                parts.append(self.relationship)
            explicit_areas = self.values(AREA)
            if explicit_areas:
                parts.extend(explicit_areas)
            elif self.location:
                parts.append(f"in {self.location}")
            parts.extend(self.values(BUDGET))
            return " ".join(part for part in parts if part).strip()

        # Housing type belongs here too, and only the real-estate branch
        # above was using it. Measured live: a request for "a studio near
        # the University of Washington" was classified ``hotel`` rather
        # than ``apartments``, took this path instead, and searched
        # "accommodation University of Washington" -- the one word the
        # person had actually specified was the one word dropped. A
        # constraint they gave may not vanish because a classifier put the
        # task in a different bucket.
        head = " ".join(self.values(ATTRIBUTE, PREFERENCE, HOUSING_TYPE))
        core = self.strip_retired(self.subject) or self.domain
        # A name this turn introduced is not optional. Measured live: the
        # request was "find contact information for the University of
        # Washington regarding I-20 verification" and the query went out as
        # "I-20 form processing" -- the held subject outranked the entity
        # the person had just named, and the search was about the form
        # rather than about the office that issues it.
        core = _with_named_entities(core, fallback)
        if not core or core.casefold() in _EMPTY_SUBJECTS:
            # The turn's own words are the last resort, and only their
            # content half: "pull up some spots for me" contributes
            # "spots", never the asking.
            core = (
                _content_of(self.strip_retired(fallback)) or self.domain
            )
        if core.casefold() in _WEAK_SUBJECTS and self.domain:
            # "places" is a real word and a poor search term. Once the
            # category is known, its own noun is strictly better.
            core = self.domain
        elif self.domain and _asks_for_places(fallback):
            # They asked for somewhere to go, so say what kind of somewhere:
            # "easy to eat dinner" is advice, "easy to eat dinner
            # restaurants" is a list of places.
            if not set(self.domain.casefold().split()) & set(
                core.casefold().split()
            ):
                core = f"{core} {self.domain}".strip()
        tail_parts = []
        for value in self.values(AREA):
            tail_parts.append(value if " " in value else f"in {value}")
        for value in self.values(BUDGET):
            tail_parts.append(
                value if re.match(r"(?:under|below|less than|up to)\b", value, re.I)
                else f"around {value}"
            )
        tail_parts.extend(self.values(DATES))
        query = " ".join(
            part for part in (head, core, " ".join(tail_parts)) if part
        )
        # Case-insensitive: "Electric" from a one-word reply and "electric"
        # from the router's topic are the same word twice.
        seen: set[str] = set()
        words: list[str] = []
        for word in query.split():
            if word.casefold() in seen:
                continue
            seen.add(word.casefold())
            words.append(word)
        return " ".join(words).strip()

    def reasoning_context(self) -> str:
        """The full picture for the answering prompt, situations included."""
        parts = []
        if self.subject:
            parts.append(f"about: {self.subject}")
        for name in (ATTRIBUTE, PREFERENCE, BUDGET, AREA, DATES, SITUATION):
            values = self.values(name)
            if values:
                parts.append(f"{name}: {', '.join(values)}")
        excluded = self.values(EXCLUSION)
        if excluded:
            parts.append(f"avoid: {', '.join(excluded)}")
        if self.retired_values:
            parts.append(
                f"no longer wanted: {', '.join(self.retired_values)}"
            )
        return "; ".join(parts)

    def log_block(self) -> str:
        """Console only -- never the conversation UI."""
        def line(label: str, values) -> str:
            rendered = ", ".join(values) if values else "(none)"
            return f"  {label}: {rendered}"

        constraints = [
            f"{slot.name}={slot.value} [{slot.source}]"
            for slot in self.constraints
        ]
        return "\n".join([
            "[Recommendation Context]",
            "[Active Task]",
            f"  id: {self.id or '(none)'}",
            f"  Subject: {self.subject or '(none)'}",
            f"  Location: {self.location or '(none)'}",
            f"  Anchor: {self.anchor or '(none)'}",
            f"  Relationship: {self.relationship or '(none)'}",
            f"  Action requested: {str(self.lookup_requested).lower()}",
            f"  Authorization: {self.authorization_source or '(none)'}",
            line("Constraints", constraints),
            line("Superseded", self.retired_values),
            line("Candidates", [str(c)[:40] for c in self.candidates]),
            f"  Evidence: {len(self.evidence)} record(s)",
            f"  Verification: {self.verification_level}",
        ])


def start(subject: str, *, domain: str = "", ttl: int = DEFAULT_TTL_SECONDS,
          now: float | None = None) -> RecommendationProblem:
    now = now if now is not None else time.monotonic()
    return RecommendationProblem(
        id=uuid.uuid4().hex[:12], subject=_clean(subject), domain=domain,
        expires_at=now + ttl,
    )


def _merge(
    existing: tuple[Slot, ...], incoming: tuple[Slot, ...],
) -> tuple[Slot, ...]:
    """Add what is new, and let a later value replace an earlier one.

    Single-valued dimensions (a budget, an area) are replaced rather than
    accumulated -- two budgets is not twice the information, it is a
    contradiction. Qualities accumulate, because "soft" and "mild" are
    both true at once.
    """
    single = {BUDGET, AREA, DATES}
    kept = list(existing)
    for slot in incoming:
        if slot.name in single:
            kept = [held for held in kept if held.name != slot.name]
            kept.append(slot)
            continue
        if any(
            held.name == slot.name
            and held.value.casefold() == slot.value.casefold()
            for held in kept
        ):
            continue
        kept.append(slot)
    return tuple(kept)


def _fits(
    offered: str,
    problem: RecommendationProblem,
    constraints: tuple[Slot, ...],
) -> bool:
    """Whether a proposed subject belongs to the problem already open.

    The router re-reads the whole history every turn and its topic drifts:
    two turns into a sore-throat dinner it offered "places to visit", which
    would have turned the query into a travel search. A subject that agrees
    with neither the category nor anything established is not adopted --
    what is already known is better than a fresh guess.
    """
    if not offered:
        return False
    if not problem.domain and not problem.subject:
        return True
    words = set(re.findall(r"[a-z0-9가-힣]+", offered.casefold()))
    known = set(re.findall(r"[a-z0-9가-힣]+", problem.domain.casefold()))
    known |= set(re.findall(r"[a-z0-9가-힣]+", problem.subject.casefold()))
    known |= {
        word
        for slot in constraints
        for word in re.findall(r"[a-z0-9가-힣]+", slot.value.casefold())
    }
    if not known:
        return True
    return bool(words & known)


def update(
    problem: RecommendationProblem,
    text: str,
    *,
    subject: str = "",
    source: str = SOURCE_UTTERANCE,
    location: str = "",
    anchor: str = "",
    said_before: str = "",
    now: float | None = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> RecommendationProblem:
    """Fold this turn into the open problem.

    A revision ("actually...") retires what was asked for and keeps what
    the person is -- a sore throat does not stop being true because they
    changed their mind about barbecue.
    """
    now = now if now is not None else time.monotonic()
    incoming = read_constraints(text, source=source, said_before=said_before)
    if not incoming and problem.constraints:
        incoming = read_short_reply(text)

    constraints = problem.constraints
    superseded = problem.superseded
    previous = problem.previous_recommendation

    situational = any(slot.name in CONTEXT_ONLY for slot in incoming)
    if revises(text) or situational:
        # Only the things asked for are retired. Qualities, budgets and
        # areas survive: "actually something soft" narrows the problem, it
        # does not restart it.
        retiring = tuple(
            slot for slot in constraints if slot.name == PREFERENCE
        )
        if retiring:
            constraints = tuple(
                slot for slot in constraints if slot.name != PREFERENCE
            )
            superseded = superseded + retiring
            previous = ""

    constraints = _merge(constraints, incoming)
    # Stripped against what has *just* been retired, and applied to the
    # held subject as well as the incoming one. Using the old superseded
    # set cleared "Korean BBQ" from the new subject and then fell straight
    # back to the old subject, which was still "Korean BBQ".
    settled = replace(problem, superseded=superseded)
    held = settled.strip_retired(problem.subject)
    offered = (
        "" if complains_about_missing_results(text)
        else settled.strip_retired(_clean(subject))
    )
    resolved = offered if _fits(offered, problem, constraints) else held
    # The thing they actually named beats the router's topic for it.
    # Measured live: "I want a guitar" was topic'd "personal desire", and
    # the summary read "electric guitar personal desire around 500,000 won".
    dropped_a_clause = False
    named = next(
        (slot.value for slot in constraints if slot.name == PREFERENCE), "",
    )
    # A subject is a phrase, not a paragraph. Containing the named thing
    # was enough to keep a whole utterance as the subject, and speech
    # arrives in whole utterances: "Okay, thank you. Also, I want to get an
    # internship in summer 2027. When should I start applying?" stayed
    # entire. ``_thing()`` then took its last word, so she asked "What kind
    # of open did you have in mind?", and the same paragraph went into the
    # search box. When the turn named a thing, that thing is the subject.
    if named and (
        named.casefold() not in resolved.casefold()
        or len(resolved.split()) > 6
    ):
        resolved = named
    elif not named:
        # No thing was named, and the subject is still whatever the person
        # said in full. Measured live: "What if you have a car? Like, give
        # me like some good places to travel along Washington State."
        # became the subject entire, "car" supplied the category from a
        # *conditional clause*, and the search went out as "Washington
        # State cars Seattle" -- answered with van rental advice.
        #
        # A condition is not a subject. The request is in its last clause,
        # so that is what is kept once the asking is stripped off it.
        shortened = _as_phrase(resolved)
        if shortened and shortened != resolved:
            resolved = shortened
            dropped_a_clause = True
    # Read the category from what is being asked for before reading it from
    # the whole utterance. "What if you have a car? ... good places to
    # travel along Washington State" typed as the *car* domain, because the
    # condition is in the sentence too -- and the query went out as
    # "Washington State cars Seattle".
    # When the utterance was reduced to a phrase, the half that was
    # discarded is condition and filler, and it may not supply the
    # category either. "What if you have a car? ... good places to travel
    # along Washington State" typed as the car domain and searched
    # "Washington State cars Seattle".
    # Only when a clause was actually thrown away. Every subject is
    # shorter than its utterance, so "shorter" is not the signal -- the
    # signal is that part of the sentence was judged to be condition or
    # filler, and that part may not supply the category either.
    trimmed = dropped_a_clause
    from_text = "" if trimmed else domain_for(text)
    category_from_text = "" if trimmed else category_for(text)
    # When nothing was thrown away, the person's own sentence is read
    # before the paraphrase of it. Measured live: "a studio near the
    # University of Washington" arrived with the subject "accommodation",
    # which types as a hotel, so the whole request was handled as a
    # booking -- hotel surfaces, hotel candidates, and the word "studio"
    # dropped from the query. The utterance said "studio" outright.
    #
    # The order flips back when a clause *was* discarded, because then the
    # sentence contains something the request is not about: "what if you
    # have a car? ... places to travel along Washington State" typed as
    # the car domain and searched "Washington State cars Seattle".
    domain = (
        problem.domain
        or from_text
        or domain_for(resolved)
        or ("" if trimmed else domain_for(problem.subject))
    )
    category = (
        problem.category
        or category_from_text
        or category_for(resolved)
        or ("" if trimmed else category_for(problem.subject))
    )
    # A kind the request named is a stated attribute, not something still
    # to be asked about. Measured live: "an electric guitar under 500,000
    # won" put "electric" in the subject and nowhere else, so nothing
    # downstream could check a candidate against it -- and the answer that
    # came back was an article about electric-guitar songs.
    settled_thing = replace(problem, subject=resolved)._thing()
    stated = _option_named(
        f"{resolved} {text}", _VARIANTS.get(settled_thing, ()),
    )
    if stated and not any(slot.name == ATTRIBUTE for slot in constraints):
        variant = _slot(ATTRIBUTE, stated, SOURCE_UTTERANCE)
        if variant is not None:
            constraints = _merge(constraints, (variant,))

    return replace(
        problem,
        subject=resolved,
        domain=domain,
        category=category,
        purchase=problem.purchase or is_purchase(text),
        entity_type=(
            "rental_unit" if category == "realestate" else
            "place" if category in {"restaurant", "hotel"} else
            "product" if (problem.purchase or is_purchase(text)
                          or settled_thing in _VARIANTS) else problem.entity_type
        ),
        location=problem.location or _clean(location),
        anchor=problem.anchor or _clean(anchor),
        relationship=(
            problem.relationship
            or ("near school" if _NEAR_SCHOOL.search(text) else "")
        ),
        lookup_requested=(
            problem.lookup_requested or wants_to_see_options(text)
        ),
        authorization_source=(
            problem.authorization_source
            or ("explicit user request" if wants_to_see_options(text) else "")
        ),
        constraints=constraints,
        superseded=superseded,
        previous_recommendation=previous,
        turns=problem.turns + 1,
        expires_at=now + ttl,
    )



def starts_a_recommendation(text: str) -> bool:
    """Whether these words open a recommendation, whatever the router said.

    Measured live: "I want a guitar." was routed as plain conversation with
    recommendation_needed false and topic "personal desire", so no problem
    was opened, nothing was asked, and the two turns that followed had
    nothing to attach to. The words themselves are a better signal than the
    flag.
    """
    text = str(text or "")
    named = [
        slot for slot in read_constraints(text) if slot.name == PREFERENCE
    ]
    if not named:
        return False
    thing = named[0].value.split()[-1].casefold()
    return bool(
        thing in _VARIANTS or is_purchase(text) or category_for(text)
    )

def about_the_same_thing(
    problem: RecommendationProblem,
    text: str,
    *,
    subject: str = "",
    topic_shift: bool = False,
) -> bool:
    """Whether a new turn continues this problem or starts another.

    Decided on what the turn *introduces*, not on word overlap. Overlap
    was the first attempt and it failed live in both directions: "actually
    my throat hurts, something soft" shares no word with "Korean BBQ", so
    the revision started a fresh problem and the preference it was meant to
    retire was never superseded -- it just vanished, and the turn after it
    searched for travel destinations.

    Naming a different thing starts a new problem. Adding a quality, a
    situation or a budget refines the open one. Asking to see what is
    already being discussed continues it, whatever words it uses.
    """
    text = str(text or "")
    # A new named object wins even inside an 'actually' correction or an
    # options request. Revision grammar alone does not prove task identity.
    incoming = read_constraints(text)
    new_category = category_for(text)
    named = [slot.value for slot in incoming if slot.name == PREFERENCE]
    if named and (
        (new_category and problem.category and new_category != problem.category)
        or any(value.split()[-1].casefold() in _VARIANTS
               and value.split()[-1].casefold() != problem._thing() for value in named)
    ):
        return False
    # A revision is by definition about the problem it revises.
    if revises(text):
        return True
    if problem.lookup_requested and (
        wants_to_see_options(text)
        or complains_about_missing_results(text)
    ):
        return True

    bare = {
        word for word in re.findall(
            r"[a-z0-9가-힣]+", _content_of(text).casefold(),
        )
    } - _EMPTY_SUBJECTS - _WEAK_SUBJECTS
    if not bare and not incoming:
        # Nothing but the asking -- "find some places", "show me a few".
        # A turn that names no topic cannot be a shift to another one, and
        # the router says otherwise often enough to matter: measured live,
        # "Find some places." was flagged a topic shift three turns into a
        # sore-throat dinner and the search came back with travel
        # destinations from Harper's Bazaar.
        return True

    if topic_shift:
        return False

    known = {
        word for word in re.findall(
            r"[a-z0-9가-힣]+", problem.subject.casefold(),
        )
    }
    known |= {
        word
        for slot in tuple(problem.constraints) + tuple(problem.superseded)
        for word in re.findall(r"[a-z0-9가-힣]+", slot.value.casefold())
    }
    # Grammar is not evidence of being about the same thing. A budget of
    # "$1000 to $1500" put "to" into what this problem is known by, and
    # "shipping it *to* Seattle" then matched it -- so a turn about posting
    # a PC home continued an apartment search on the strength of one
    # preposition.
    known -= _FUNCTION_WORDS

    named = [slot for slot in incoming if slot.name == PREFERENCE]
    if named:
        # A new thing by name: the same problem only if it is the thing
        # already under discussion.
        return any(
            set(re.findall(r"[a-z0-9가-힣]+", slot.value.casefold())) & known
            for slot in named
        )
    if incoming:
        # Qualities, situations and budgets refine -- but only when the
        # constraint is essentially all the turn says.
        #
        # This was an unconditional "return True", and it is the single
        # line that kept a rental problem alive across the whole of the
        # first dogfooding session. "Okay, I searched it up and the phone
        # number is 206-221-7857. You are wrong." carries digits, so it
        # refined an apartment search; "I want to get an internship in
        # summer 2027" carries a date, so it did too. Four turns later the
        # query was "studio apartments September 13th 206-221 in South
        # Korea", built to answer a question about a phone number.
        #
        # What separates the two is what is left over. Take the turn's
        # content words away from the constraint's own words: a genuine
        # refinement has nothing left ("from $1000 to $1500", "just like a
        # studio"), while a sentence about something else still has its
        # subject in hand.
        stated = {
            word
            for slot in incoming
            for word in re.findall(r"[a-z0-9가-힣]+", slot.value.casefold())
        }
        residual = bare - stated - _FUNCTION_WORDS
        return not residual or bool(residual & known)

    # Nothing was stated at all. A bare follow-up ("show me some places")
    # continues; a whole unrelated sentence does not.
    content = _content_of(text)
    words = {word for word in re.findall(r"[a-z0-9가-힣]+", content.casefold())}
    words -= _EMPTY_SUBJECTS | _WEAK_SUBJECTS
    if not words:
        # Nothing but the asking: "show me some places", "pull up a few".
        return True
    if len(words) == 1:
        # One bare word. "Electric." answers the question just asked and
        # belongs to this problem; "guitar" names a different thing
        # entirely and starts another. What separates them is whether the
        # word is a thing rather than a quality.
        lone = next(iter(words))
        if lone not in _VARIANTS and not category_for(lone):
            return True
    subject_words = {
        word for word in re.findall(r"[a-z0-9가-힣]+", _clean(subject).casefold())
    }
    return bool((words | subject_words) & known)
