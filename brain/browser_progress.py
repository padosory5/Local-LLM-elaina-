"""Noticing that nothing is happening, and reading what was just said.

Two root causes behind five reported browser issues in session 2.

Going round in circles
----------------------

Three separate runs ended ``failure=model_round_budget_exhausted``, all
with the same trace:

    round=1  describe_page  observed
    round=2  click_element  clicked
    round=3  describe_page  observed
    round=4  click_element  clicked
    ...  six identical cycles, to round 12

Nothing in the loop asked whether a step had already been taken and
changed nothing. The whole budget went on re-clicking the same control,
the page text was never read, and the run that was meant to fetch a phone
number off an already-open page came back ``not_verified`` -- it never
got there.

``ProgressWatch`` is the missing question. It does not stop the planner;
it says "you have done this twice already, and here is what you have not
tried", which is the information the loop lacked.

Pointing at the turn before
---------------------------

    Elaina: You can sell second-handed stuff online in Korea through
            Karrot, Bunjang, Joonggonara, Hello Market, or Danawa Jangteo.
    User:   open one of those websites for me.
    [Computer Control] action=open_search target=one of those websites
    Elaina: Got it, one of those websites is open.

"One of those" is a reference into a list she had read out one turn
earlier. Resolving it needs her previous words, which is the same thing
B-03 and B-17 needed -- a turn is not always a self-contained request.

The rule that keeps this safe is the one this project keeps relearning:
a generic phrase may never override what the current turn actually says.
"Open Bunjang" names its own target and resolves to nothing here.
"""

from __future__ import annotations

import re

from brain.references import ORDINAL_INDEX

# How many times the same step may be taken before it stops being work.
# Two is deliberate: looking at a page again after clicking is ordinary
# and often necessary, and only the third identical step is a loop.
_REPEATS_ALLOWED = 2

# Everything the planner can do, so what has *not* been tried is nameable.
_TOOLS = (
    "search", "open_url", "list_tabs", "describe_page", "read_page_text",
    "click_element", "fill_field", "select_option", "scroll_to_element",
)


class ProgressWatch:
    """Whether the planner is repeating itself rather than getting on."""

    def __init__(self, repeats_allowed: int = _REPEATS_ALLOWED) -> None:
        self.repeats_allowed = max(1, int(repeats_allowed))
        self._counts: dict[tuple[str, str], int] = {}
        self._used: set[str] = set()

    def repeating(self, tool: str, target: str = "", status: str = "") -> bool:
        """Record this step, and say whether it is one too many.

        Keyed on tool *and* target: clicking four different links is four
        pieces of work, clicking the same link four times is one, done
        four times.
        """
        tool = str(tool or "").strip()
        key = (tool, " ".join(str(target or "").split()).casefold())
        self._used.add(tool)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key] > self.repeats_allowed

    def untried(self) -> tuple[str, ...]:
        """Tools this run has not used yet, in the order worth trying."""
        return tuple(tool for tool in _TOOLS if tool not in self._used)


# "one of those", "the second one", "that first site" -- a choice made
# from a list she read out rather than a name given fresh.
_PICKS_FROM_A_LIST = re.compile(
    # "Open the website." A bare definite points at the one thing the
    # conversation has just been about, and nothing resolved it -- so the
    # raw utterance went to the planner as its own target, it searched
    # blind, and landed on example.com:
    #
    #     User:   Yeah, use browser control and then open the website.
    #     Elaina: Clicked 'Example Domain Example Domain
    r"\b(?:the|that|their|its)\s+"
    r"(?:site|website|page|link|homepage)\b"
    r"|\b(?:one|any)\s+of\s+(?:those|these|them|the)\b"
    r"|\bthe\s+(?P<ordinal>%s)\s+(?:one|site|website|link|option)?\b"
    r"|\b(?:that|the)\s+(?:first|last)\s+(?:one|site|website|link)\b"
    % "|".join(sorted(ORDINAL_INDEX, key=len, reverse=True)),
    re.IGNORECASE,
)

# A proper name in what she said: the things a list is made of.
# A proper name does not end at its first lowercase word: "University of
# Washington" is one name, and stopping at "University" is how a bare
# definite reference resolved to something that was not a site at all.
_NAMED = re.compile(
    r"\b([A-Z][A-Za-z0-9&'’-]*"
    r"(?:\s+(?:of|the|and|de|del|van|von)\s+[A-Z][A-Za-z0-9&'’-]*"
    r"|\s+[A-Z][A-Za-z0-9&'’-]*){0,3})\b"
)

# Words that open a sentence or name a place rather than an option.
_NOT_AN_OPTION = frozenset({
    "you", "i", "it", "the", "a", "an", "in", "on", "at", "and", "or",
    "korea", "south", "north", "seattle", "washington", "sure", "yes",
    "no", "okay", "here", "there", "this", "that", "through", "online",
})


def _options_in(said_before: str) -> list[str]:
    """The named things her previous turn offered, in the order given."""
    found: list[str] = []
    for match in _NAMED.finditer(str(said_before or "")):
        name = match.group(1).strip()
        # A capitalised article opens a sentence; it is not part of the
        # name. "The University of Washington..." offers the university.
        name = re.sub(r"^(?:The|A|An)\s+", "", name).strip()
        words = [word.casefold() for word in name.split()]
        if not words or all(word in _NOT_AN_OPTION for word in words):
            continue
        if name not in found:
            found.append(name)
    return found


def resolve_named_choice(text: str, *, said_before: str = "") -> str:
    """Which of the things she just listed this turn is asking for.

    Empty when the turn names its own target -- an explicit request is
    never overridden by something said earlier -- and empty when her
    previous turn listed nothing to choose from.
    """
    said = " ".join(str(text or "").split())
    if not said or not said_before:
        return ""
    match = _PICKS_FROM_A_LIST.search(said)
    if match is None:
        return ""

    options = _options_in(said_before)
    if not options:
        return ""
    # A turn that already names one of them is not choosing blindly.
    for option in options:
        if option.casefold() in said.casefold():
            return ""

    ordinal = (match.groupdict().get("ordinal") or "").casefold()
    if ordinal:
        index = ORDINAL_INDEX.get(ordinal)
        if index is None or index >= len(options):
            return ""
        return options[index]
    if re.search(r"\bfirst\b", said, re.IGNORECASE):
        return options[0]
    if re.search(r"\blast\b", said, re.IGNORECASE):
        return options[-1]
    # "One of those" leaves the choice to her, so take the one she named
    # first -- the order she gave them in is her own ranking.
    return options[0]
