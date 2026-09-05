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
from dataclasses import dataclass, field, replace

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


def strip_surface_context(label: str) -> tuple[str, str]:
    """Split a spoken element label from the words locating the page.

    Returns ``(element, context)``. Repeated because people do say "click
    about on this page here", and each pass removes one phrase.
    """
    said = " ".join(str(label or "").split())
    context: list[str] = []
    while said:
        stripped = _SURFACE_CONTEXT.sub("", said)
        if stripped == said:
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
