"""What an action assumes beforehand, and what would prove it afterwards.

Phase 2 of the deliberation layer. Both drivers already verified their work;
what neither of them stated was the *precondition*. That gap is not
theoretical -- it produced a bug that passed verification:

    the search box held "bang bang IVE"
    "After LIKE IVE" was typed without clearing
    the box read "bang bang IVEAfter LIKE IVE"
    the check asked "does it contain the requested text?" -- it did
    the action was reported as verified

An effect check that only asks whether its own text arrived cannot tell
replacement from appending. So a contract carries both halves: what must be
true before (and how to repair it when it is not), and what must be true
after -- where "after" means the field holds the requested text *instead of*
what was there, not in addition to it.

Deliberately pure: readings come in, verdicts go out. The drivers know how
to read a field and how to clear one; they differ in every detail of doing
so, and none of that belongs here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Check:
    """One question about the world, answered from a live reading.

    ``holds`` is deliberately three-valued. False means the world
    contradicts the claim; None means it could not be read, which is a
    weaker guarantee than success and must never be reported as one.
    """

    name: str
    holds: bool | None
    evidence: str

    @property
    def failed(self) -> bool:
        return self.holds is False


def field_is_empty(before_value: str | None) -> Check:
    """Precondition for typing: nothing is in the way of the new text."""
    if before_value is None:
        return Check(
            "field_is_empty",
            None,
            "The field exposes no readable value, so it cannot be checked.",
        )
    if not _normalized(before_value):
        return Check("field_is_empty", True, "The field was empty.")
    return Check(
        "field_is_empty",
        False,
        f"The field already held {before_value.strip()!r}.",
    )


def replacement_effect(
    expected: str,
    before_value: str | None,
    after_value: str | None,
    *,
    source: str = "The field",
    high_confidence: bool = True,
) -> Check:
    """Effect for typing: the field holds this text *instead of* the old.

    The middle case is the one that matters and the one a "contains"
    check gets wrong: the requested text is present, and so is everything
    that was there before it. That is an append, and an append is a
    failure however much of the requested text can be found in the result.
    """
    name = "field_holds_requested_text"
    if after_value is None:
        return Check(name, None, f"{source} exposes no readable value.")
    after = _normalized(after_value)
    expected_text = _normalized(expected)
    before = _normalized(before_value) if before_value is not None else ""

    if not expected_text:
        return Check(
            name,
            after == "",
            f"{source} is empty." if after == "" else f"{source} is not empty.",
        )
    if after == expected_text:
        return Check(name, True, f"{source} reads exactly the requested text.")
    if expected_text in after:
        if before and before != expected_text and before in after:
            return Check(
                name,
                False,
                (
                    f"{source} still holds what was there before, with the "
                    "requested text added onto it."
                ),
            )
        return Check(
            name,
            True,
            f"{source} contains all {len(expected)} requested characters.",
        )
    if high_confidence:
        return Check(
            name,
            False,
            f"{source} was readable and does not hold the requested text.",
        )
    return Check(name, None, f"{source} gave no conclusive postcondition.")


def blind_typing_effect(control_name: str) -> Check:
    """Effect for typing with no verifiable field: honestly unknowable.

    Chromium/CEF apps reveal a search box that never appears as a named,
    readable control. Typing there can be done and cannot be proved, and
    saying so is the whole point of this Check existing.
    """
    return Check(
        "field_holds_requested_text",
        None,
        (
            f"{control_name} opened a field with no readable value; the "
            "keystrokes went to whatever held keyboard focus."
        ),
    )
