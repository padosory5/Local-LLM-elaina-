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


# "So open it." -- an instruction whose object is the thing just acted on
# and nothing else. Anchored at both ends on purpose: the moment the turn
# carries any content of its own, it is naming its own target and this
# must not fire. That is the rule the whole file rests on.
_CONTINUES_THE_LAST_ACTION = re.compile(
    r"^(?:so|then|ok(?:ay)?|well|yeah|yes|alright|right|now|and|but|"
    r"please)?[,. ]*"
    r"(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:just\s+|then\s+|please\s+|still\s+)*"
    r"(?:open|try|do|retry|redo|run|go\s+to|load)\s+"
    r"(?:it|that|this|them|those)"
    r"(?:\s+(?:again|now|properly|for\s+me|please|one\s+more\s+time))*"
    r"[.!? ]*$",
    re.IGNORECASE,
)

# A whole turn that is only a statement about spelling: "Only one S.",
# "with two Ls", "no, spelled with a K". Deliberately a closed grammar
# over letter counts rather than a list of phrasings -- it reads a number
# and a letter, and it means nothing at all without a target to apply it
# to.
_A_LETTER_COUNT = re.compile(
    r"^(?:(?:no|nope|nah)[,.!]?\s+)*"
    # The ways a person introduces a correction to a spelling. Measured
    # live: "I meant only one S." matched nothing, because the reader only
    # knew the bare form -- so the correction became a new conversational
    # subject called "only one S", the browser action was retired, and she
    # opened the misspelled address again.
    r"(?:so\s+)?(?:from\s+(?:my|the)\s+(?:previous|last)\s+\w+,?\s+)?"
    r"(?:actually[, ]+)?(?:i\s+(?:mean|meant)\s+)?"
    r"(?:i\s+(?:just\s+)?(?:want|wanted|need|needed)\s+)?"
    r"(?:(?:can|could|would)\s+(?:you|we|it)\s+"
    r"(?:just\s+|please\s+)?(?:only\s+)?"
    r"(?:have|make\s+it|use|do)\s+)?"
    r"(?:(?:it|that|there)(?:'s|s| is)?\s+)?"
    r"(?:should\s+(?:be|have)\s+)?"
    r"(?:spel(?:led|t)\s+)?"
    r"(?:with\s+)?(?:only\s+|just\s+)*"
    r"(?P<count>one|two|three|a|an|single|double|triple|no)\s+"
    r"(?P<letter>[A-Za-z])(?:'s|s)?"
    r"(?:\s+(?:only|though|there|in\s+(?:it|there)))*"
    r"[.!, ]*$",
    re.IGNORECASE,
)

# A turn may correct itself mid-sentence -- "There's three S's in there. I
# just want two S's in there." The operative clause is the last one that
# reads as a letter count; the ones before it are describing the problem.
_CLAUSE = re.compile(r"[.!?;]+|,\s+(?=i\b|just\b|so\b)", re.IGNORECASE)

_COUNTS = {
    "no": 0, "a": 1, "an": 1, "one": 1, "single": 1,
    "two": 2, "double": 2, "three": 3, "triple": 3,
}
_LETTER_DELTA = re.compile(
    r"^(?:actually[, ]+|please\s+|i\s+meant\s+)*"
    r"(?:(?P<verb>remove|add)\s+(?P<count>one|two|three)\s+"
    r"|(?P<amount>one|two|three)\s+(?P<direction>fewer|less|more)\s+)"
    r"(?P<letter>[a-z])(?:'s|s)?[.! ]*$", re.I,
)
_ACTION_DISPUTE = re.compile(
    r"^(?:(?:no|but|well|actually|though)[,! .]+)*"
    r"(?:(?:it|that|this|the\s+(?:website|site|page|browser|window))\s+)?"
    r"(?:(?:didn'?t|did\s+not)\s+(?:open|load|work)|"
    r"(?:isn'?t|is\s+not|wasn'?t|was\s+not)\s+(?:open(?:ed)?|loaded|it|right)|"
    r"failed|wrong\s+(?:page|address|site)|that'?s\s+(?:not\s+it|the\s+wrong\s+(?:page|address)))"
    r"(?:\s+(?:it|on\s+my\s+browser|though|again))*[.!? ]*$", re.I,
)


def disputes_last_action(text: str) -> bool:
    return bool(_ACTION_DISPUTE.fullmatch(" ".join(str(text or "").split())))

# Something with a dot in it and no spaces: the shape of a web address.
_ADDRESS = re.compile(r"\b[\w-]+(?:\.[\w-]+)+\b")


def looks_like_an_address(text: str) -> bool:
    """Whether this is a web address rather than a sentence about one."""
    said = " ".join(str(text or "").split())
    return bool(said) and bool(_ADDRESS.fullmatch(said))


def continues_the_last_action(text: str) -> bool:
    """Whether the turn is "do that again" and nothing else.

    Measured live, after she reported opening a page that had not opened:

        User:   though it's not.
        Elaina: You're right -- it's not.
        User:   So open it.

    Every layer read that last turn as a fresh request, found no target in
    it, and the desktop planner burned its whole budget looking for a
    native window. The target was the address of the action before it.
    """
    return bool(_CONTINUES_THE_LAST_ACTION.match(" ".join(str(text or "").split())))


def respelled_address(goal: str, text: str) -> str:
    """The address in ``goal``, with the spelling the turn asks for.

    Measured live, one turn after opening ``isss.washington.edu``:

        User:   Only one S.

    It was routed as ordinary conversation, the answer repeated the turn
    back, and the guard that catches that apologised and asked for the
    whole address again. A correction to what was just done is not a new
    subject.

    Empty unless the goal holds exactly one address and exactly one run of
    that letter in it is the wrong length -- ambiguity is not guessed at.
    """
    said = " ".join(str(text or "").split())
    # An explicit replacement address owns the turn, even if another clause
    # also describes the old spelling. Never silently throw the new URL away.
    if _ADDRESS.search(said):
        return ""
    match = None
    for clause in reversed([part.strip() for part in _CLAUSE.split(said)]):
        if not clause:
            continue
        match = _A_LETTER_COUNT.match(clause) or _LETTER_DELTA.match(clause)
        if match is not None:
            break
    if match is None:
        return ""
    delta = "direction" in match.re.groupindex
    if delta:
        number = match.group("count") or match.group("amount")
        amount = _COUNTS[number.casefold()]
        amount *= -1 if (match.group("verb") or match.group("direction")).casefold() in {"remove", "fewer", "less"} else 1
        wanted = None
    else:
        wanted = _COUNTS.get(match.group("count").casefold())
    letter = match.group("letter").casefold()
    if wanted is None and not delta:
        return ""

    addresses = {found.group(0) for found in _ADDRESS.finditer(str(goal or ""))}
    if len(addresses) != 1:
        return ""
    address = addresses.pop()

    # A correction about spelling is about the site's own name, which in
    # an address is its first label. Everything after the first dot is the
    # registry talking -- washington.edu, .com, .co.kr -- and nobody
    # corrects the spelling of that.
    #
    # Measured live: "is.washington.edu" plus "I meant two S's" found the
    # lone s in "is" and the lone s in "washington", called it ambiguous,
    # and refused. The whole exchange then went four more turns and ended
    # with the planner trying to add the letter S to the page's contents.
    #
    # Restricting to the first label is also what makes the correction
    # work in both directions. The rule before this one preferred a
    # *repeated* run, which can only ever shorten one.
    head, dot, tail = address.partition(".")
    if not dot:
        return ""
    runs = [
        run for run in re.finditer(
            rf"{re.escape(letter)}+", head, re.IGNORECASE,
        )
    ]
    wrong = runs if delta else [run for run in runs if len(run.group(0)) != wanted]
    if len(wrong) != 1:
        # More than one run of that letter in the name itself, both the
        # wrong length: "assess.com" with "only one S" could mean either,
        # so it means neither.
        return ""
    run = wrong[0]
    if delta:
        wanted = len(run.group(0)) + amount
    if wanted < 0 or wanted > 63:
        return ""
    corrected = (
        head[:run.start()] + letter * wanted + head[run.end():] + dot + tail
    )
    if not corrected or corrected == address:
        return ""
    # Preserve an explicit URL's path, scheme and query. A sentence used as
    # an old goal still contributes only its one address.
    original = str(goal or "").strip()
    if original.startswith(("https://", "http://")) and not re.search(r"\s", original):
        return original.replace(address, corrected, 1)
    return corrected


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
