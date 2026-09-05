"""What a page-interaction turn asks for, and what became of it.

Direct navigation has one owner and a lifecycle. Page interaction had
neither, and the browser acceptance run showed what that costs.

Two failures, one shape. First, the element and the words locating the
page were not told apart:

    You said: Can you click calendar on this webpage?
    direct target: 'calendar on this webpage'
    failure: direct_target_not_found

No page has a control called "calendar on this webpage". Second, the
requested action did not survive the conversation about it:

    You said: click about on this page
    Elaina:   (an offer)
    You said: Yes.
    planner target: 'Yes.'
    You said: Can you try again?
    [Router] continue the last action -> browser_action
    planner target: still 'Yes.'

An acknowledgement is not a target. A retry repeats what was asked for,
not what was last said.

So a page interaction is a record with an identity of its own -- the
operation, the element, the page it was on, and what happened -- and the
conversation edits fields of it rather than replacing it wholesale. "Yes"
changes nothing. "Try again" repeats it. "No, calendar" changes the
element and leaves everything else alone.

Element from context
--------------------
A click command has three parts:

    ACTION    click
    ELEMENT   calendar
    CONTEXT   on this webpage

The context is a trailing prepositional phrase whose object is the
surface itself -- "on this page", "in this webpage", "on the current
screen", "in here". It says *where to look*, and a page never puts those
words in a link's own name.

This is a closed grammatical class, not a list of phrases: a preposition,
an optional determiner, and a noun that names the surface Elaina is
already working on. "Click Sign in on Google" keeps its whole label,
because Google is not a surface -- it is where the label lives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from brain.references import ORDINAL_INDEX, ORDINAL_WORD

# The surface Elaina is already looking at. A genuinely closed class: these
# are the words English has for "the thing on screen in front of you", and
# a page does not name its own controls after them.
_SURFACE_NOUNS = (
    "web ?page", "page", "screen", "window", "site", "website", "tab",
    "browser", "view", "display",
)
# Determiners that make a noun refer to the surface in hand rather than
# to some other one. "on this page" locates; "on a page" does not.
_DEICTIC = r"(?:this|that|the|the\s+current|current|your|our)"
_LOCATIVE_PREPOSITION = r"(?:on|in|at|within|inside|from)"

# A trailing phrase that locates the surface instead of naming an element.
# A deictic may name the surface by what is on it -- "on this GitHub
# page", "on the current results page". One word between the determiner
# and the noun, and only after a determiner: without that anchor, an
# ordinary label ending in a noun would start being eaten.
_SURFACE_CONTEXT = re.compile(
    r"\s+" + _LOCATIVE_PREPOSITION + r"\s+"
    r"(?:" + _DEICTIC + r"\s+(?:[\w-]+\s+)?)?"
    r"(?:" + "|".join(_SURFACE_NOUNS) + r")"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)
# "in here" / "over there" -- the same locative with an adverb for an
# object, which is how people say it out loud.
_SURFACE_ADVERB = re.compile(
    r"\s+(?:" + _LOCATIVE_PREPOSITION + r"\s+)?(?:right\s+)?(?:here|there)"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def strip_surface_context(
    label: str, *, adverbs: bool = True,
) -> tuple[str, str]:
    """Split a spoken element label from the words locating the page.

    Returns ``(element, context)``. Repeated because people do say "click
    about on this page here", and each pass removes one phrase.

    ``adverbs=False`` keeps a bare "there"/"here". Those are the only
    surface words that are also ordinary objects -- "go there" points
    somewhere, "in here" locates -- so a caller asking what a turn *refers
    to* wants the prepositional form only.
    """
    said = " ".join(str(label or "").split())
    context: list[str] = []
    while said:
        stripped = _SURFACE_CONTEXT.sub("", said)
        if stripped == said and adverbs:
            stripped = _SURFACE_ADVERB.sub("", said)
        if stripped == said or not stripped.strip():
            # Never strip the whole label away: "click here" means the
            # element is literally called Here, which is a real link.
            break
        context.insert(0, said[len(stripped):].strip(" .!?"))
        said = stripped.strip()
    return said.strip(" .!?"), " ".join(context).strip()


@dataclass(frozen=True)
class BrowserInteraction:
    """One requested action on a page, and what became of it.

    The target is what the person asked for. It is edited by corrections
    and by nothing else -- not by an acknowledgement, not by whatever the
    planner last had in a variable, not by the transcript of a later turn.
    """

    operation: str                  # click_element, type_text, scroll_to...
    target: str                     # the element as the person named it
    source: str = ""                # the utterance it came from
    context: str = ""               # the words that located the page
    tab_identity: str = ""          # which page this belongs to
    page_url: str = ""
    status: str = "requested"       # requested|clicked|not_found|ambiguous...
    resolved: str = ""              # the element actually matched
    candidates: tuple[str, ...] = ()    # what an ambiguous match found
    evidence: str = ""              # why we believe the status
    attempts: int = 0

    @property
    def satisfied(self) -> bool:
        """Whether the thing that was asked for actually happened.

        Deliberately not "the planner stopped". The acceptance run ended a
        run of clicks and page descriptions with ``status=done`` and a
        summary of the page, with no evidence that About had been clicked
        at all.
        """
        return self.status == "clicked" and bool(self.resolved)

    def retried(self) -> "BrowserInteraction":
        """The same request again -- same operation, same element, same page."""
        return replace(
            self, status="requested", resolved="", candidates=(),
            evidence="", attempts=self.attempts + 1,
        )

    def retargeted(self, element: str, *, source: str = "") -> "BrowserInteraction":
        """A correction: a new element, everything else untouched."""
        return replace(
            self, target=element, source=source or self.source,
            status="requested", resolved="", candidates=(), evidence="",
            attempts=self.attempts + 1,
        )

    def finished(self, status: str, *, resolved: str = "",
                 candidates: tuple[str, ...] = (),
                 evidence: str = "") -> "BrowserInteraction":
        return replace(
            self, status=status, resolved=resolved,
            candidates=tuple(candidates), evidence=evidence,
        )

    def describe(self) -> str:
        return (
            f"{self.operation} target={self.target!r} "
            f"status={self.status} resolved={self.resolved!r} "
            f"tab={self.tab_identity or 'unknown'} attempts={self.attempts}"
        )


# Choosing one of several matches. A closed class of selection
# expressions: a position, an end, the middle, or "you pick". Anything
# else is not a choice and must not be read as one.
_ORDINALS = "|".join(sorted(ORDINAL_INDEX, key=len, reverse=True))
_CHOOSES_A_POSITION = re.compile(
    rf"\b(?:the\s+)?(?P<ordinal>{_ORDINALS})\b(?!\s+(?:time|thing)\b)",
    re.IGNORECASE,
)
# "Number two", "#2" -- the same position said as a cardinal. People do
# both, and the ordinal table only knows the ordinal half.
_CARDINALS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8,
}
_CHOOSES_BY_NUMBER = re.compile(
    r"\b(?:number|no\.?|#)\s*(?P<count>\d+|"
    + "|".join(_CARDINALS) + r")\b",
    re.IGNORECASE,
)
_CHOOSES_THE_MIDDLE = re.compile(
    r"\b(?:middle|centre|center)\b", re.IGNORECASE,
)
_CHOOSES_THE_TOP = re.compile(r"\b(?:top|topmost|upper)\b", re.IGNORECASE)
_CHOOSES_THE_BOTTOM = re.compile(
    r"\b(?:bottom|bottommost|lower)\b", re.IGNORECASE,
)
# Leaving the choice to her is still a choice, and it is the one the
# acceptance run made: "click any of them".
_LEAVES_IT_TO_HER = re.compile(
    r"\b(?:any(?:\s+(?:of\s+)?(?:them|those|these|one))?"
    r"|either(?:\s+(?:of\s+)?(?:them|those|one))?"
    r"|whichever|whatever"
    r"|one\s+of\s+(?:them|those|these)"
    r"|you\s+(?:choose|pick|decide)"
    r"|does\s?n[o']?t\s+matter"
    r"|up\s+to\s+you)\b",
    re.IGNORECASE,
)


# How far apart two elements may sit and still be described as being next
# to each other. Generous enough for a padded navigation bar, tight enough
# that "next to" means something a person can see.
_ADJACENT_PIXELS = 320
# A neighbour has to be sayable. Four words is a menu item or a heading;
# more is a sentence, and "the one next to Apply for your I-20 before the
# deadline" helps nobody.
_NEIGHBOUR_WORDS = 4


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> int:
    """How much two 1-D spans share. Negative is the gap between them."""
    return min(first[1], second[1]) - max(first[0], second[0])


def _relation(target, other) -> tuple[str, int] | None:
    """How ``other`` sits relative to ``target``, and how far away.

    Only the four readings a person would actually use: beside it on the
    same line, or directly above or below it in the same column.
    """
    t_left, t_top, t_right, t_bottom = target
    o_left, o_top, o_right, o_bottom = other
    same_row = _overlaps((t_top, t_bottom), (o_top, o_bottom)) > 0
    same_column = _overlaps((t_left, t_right), (o_left, o_right)) > 0
    if same_row and not same_column:
        gap = o_left - t_right if o_left >= t_right else t_left - o_right
        if 0 <= gap <= _ADJACENT_PIXELS:
            return "next to", gap
        return None
    if same_column and not same_row:
        if o_bottom <= t_top:
            gap = t_top - o_bottom
            return ("under", gap) if gap <= _ADJACENT_PIXELS else None
        gap = o_top - t_bottom
        return ("above", gap) if gap <= _ADJACENT_PIXELS else None
    return None


def landmarks_for(targets, others) -> dict[str, tuple[str, str]]:
    """What each target sits next to, when that tells them apart.

    ``targets`` are ``(id, label, rect)``; ``others`` are ``(label, rect)``
    for everything else visible on the page.

    People locate things on a page by what is beside them -- "the one next
    to Services" -- and that is the only handle they have when the labels
    are identical. Measured in the session-14 run, where the alternative
    was offered instead:

        There are two about links on the page -- the first one in the page
        navigation and the second one in the page navigation.

    A landmark is kept only if it separates the candidates. Two links both
    "next to Services" is the same sentence again with more words in it.
    """
    found: dict[str, tuple[str, str]] = {}
    target_labels = {
        str(label or "").strip().casefold() for _id, label, _rect in targets
    }
    for element_id, _label, rect in targets:
        if not any(rect):
            continue
        best: tuple[int, str, str] | None = None
        for other_label, other_rect in others:
            name = " ".join(str(other_label or "").split())
            if not name or name == "(unlabeled)" or not any(other_rect):
                continue
            if name.casefold() in target_labels:
                continue
            if len(name.split()) > _NEIGHBOUR_WORDS:
                continue
            placed = _relation(rect, other_rect)
            if placed is None:
                continue
            relation, gap = placed
            # Beside beats above and below: a person reads a row first.
            rank = gap if relation == "next to" else gap + _ADJACENT_PIXELS
            if best is None or rank < best[0]:
                best = (rank, relation, name)
        if best is not None:
            found[element_id] = (best[1], best[2])
    # A landmark that every candidate shares distinguishes none of them.
    seen: dict[tuple[str, str], int] = {}
    for placed in found.values():
        seen[placed] = seen.get(placed, 0) + 1
    return {
        element_id: placed for element_id, placed in found.items()
        if seen[placed] == 1
    }


@dataclass(frozen=True)
class Candidate:
    """One of several page elements a request could have meant.

    The identity is the point. An ambiguity answered with a *label* sends
    the planner back to a page to find something it already found -- and
    when the labels are identical, as they were on the ISS site, that
    cannot possibly resolve to the right one.
    """

    element_id: str
    label: str
    order: int              # 1-based, in the page's own order
    where: str = ""         # "the page navigation", "the main content"
    href: str = ""
    # What this one sits beside, and how: ("next to", "Services").
    relation: str = ""
    near: str = ""

    def named(self) -> str:
        """How to refer to this one so a person can tell it apart.

        What it is beside first, because that is how people point at
        things on a page. Where it sits second. Its position last, which
        is true but tells you nothing about which one you were looking at.
        """
        if self.near:
            return f"the one {self.relation} {self.near}"
        position = ORDINAL_WORD.get(self.order - 1, f"number {self.order}")
        return f"the {position} one{f' in {self.where}' if self.where else ''}"


@dataclass(frozen=True)
class AmbiguousPageAction:
    """A page action that matched several things, waiting on a choice.

    Measured in the session-13 run, and this is the whole bug:

        You said: click about on this page
        Elaina:   I found more than one about item -- ABOUT, ABOUT.
                  Which one do you mean?
        You said: the first one
        [Reference] 'one of those' -> 'ABOUT'
        [Computer Control] open_search target=ABOUT
        -> https://www.google.com/search?q=ABOUT

    Choosing one of several candidates for a click is not a new command.
    It fills the unresolved slot of the action already standing, and that
    action then runs -- same operation, same page, one named element.
    """

    operation: str
    requested_label: str
    candidates: tuple[Candidate, ...]
    tab_index: int | None = None
    tab_identity: str = ""
    page_url: str = ""
    scan_id: str = ""
    goal: str = ""

    def choose(self, text: str) -> Candidate | None:
        """Which candidate this turn selects, or None if it selects none."""
        said = " ".join(str(text or "").split())
        if not said or not self.candidates:
            return None
        # Answering with the landmark she offered: "the one next to
        # Services". This is how the question was asked, so it has to be
        # how an answer can be given.
        for candidate in self.candidates:
            near = candidate.near.strip()
            if near and near.casefold() in said.casefold():
                if sum(
                    1 for other in self.candidates
                    if other.near.casefold() == near.casefold()
                ) == 1:
                    return candidate
        # Naming one outright, when the labels actually differ.
        for candidate in self.candidates:
            label = candidate.label.strip()
            if len(label) > 2 and label.casefold() in said.casefold():
                if sum(
                    1 for other in self.candidates
                    if other.label.casefold() == label.casefold()
                ) == 1:
                    return candidate
        if _CHOOSES_THE_MIDDLE.search(said):
            return self.candidates[(len(self.candidates) - 1) // 2]
        numbered = _CHOOSES_BY_NUMBER.search(said)
        if numbered is not None:
            spoken = numbered.group("count").casefold()
            count = _CARDINALS.get(spoken)
            if count is None:
                count = int(spoken) if spoken.isdigit() else 0
            if 1 <= count <= len(self.candidates):
                return self.candidates[count - 1]
            return None
        match = _CHOOSES_A_POSITION.search(said)
        if match is not None:
            index = ORDINAL_INDEX.get(match.group("ordinal").casefold())
            if index is not None and -len(self.candidates) <= index < len(
                self.candidates,
            ):
                return self.candidates[index]
            return None
        if _CHOOSES_THE_TOP.search(said):
            return self.candidates[0]
        if _CHOOSES_THE_BOTTOM.search(said):
            return self.candidates[-1]
        if _LEAVES_IT_TO_HER.search(said):
            # Her own order is her ranking, same as everywhere else.
            return self.candidates[0]
        return None

    def question(self) -> str:
        """Ask in a way that can actually be answered.

        "I found more than one about item -- ABOUT, ABOUT" gives a person
        nothing to choose with. When the labels do not distinguish the
        candidates, where they sit on the page does.
        """
        named = self.requested_label
        count = len(self.candidates)
        labels = [c.label.strip() for c in self.candidates]
        distinct = len({label.casefold() for label in labels if label})
        if distinct == count and distinct > 1:
            listed = ", ".join(labels[:4])
            return (
                f"I found more than one {named} item -- {listed}. "
                "Which one do you mean?"
            )
        described = [c.named() for c in self.candidates[:4]]
        if len(described) == 2:
            listed = f"{described[0]} and {described[1]}"
        else:
            listed = ", ".join(described[:-1]) + f", and {described[-1]}"
        spelled = {2: "two", 3: "three", 4: "four"}.get(count, str(count))
        return (
            f"There are {spelled} {named} links on the page -- {listed}. "
            "Which one do you mean?"
        )

    def still_answerable_for(self, label: str) -> bool:
        """Whether a later choice still refers to this same set.

        Measured in the session-14 run: one candidate was chosen and
        clicked, and the very next turn -- "Can you click the second
        one?" -- had nothing left to choose from, so a bare ordinal went
        to the router and became a Google search for ABOUT. Answering a
        question does not use up the answer to it.
        """
        asked = " ".join(str(label or "").split()).casefold()
        return not asked or asked == self.requested_label.casefold()
