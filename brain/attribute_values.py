"""Measured attributes, so a spec can be checked the way a price already is.

:mod:`brain.grounded_values` stops Elaina quoting a price she never looked
up. It has done that since Phase 4, it is narrow on purpose, and its
narrowness is the reason it works -- most numbers in conversation are
perfectly fine to state from general knowledge.

But "checkable value" was defined as **money, phone numbers and email
addresses**, and nothing else. Measured against a real search result for a
monitor, with six invented replies:

    invents a price            caught
    invents a refresh rate     not caught
    invents a response time    not caught
    invents a rating           not caught
    invents battery life       not caught
    invents a weight           not caught

One in six. A fabricated 240Hz reads exactly like a retrieved one, and a
person shopping for a monitor is being told a number that decides the
purchase.

This module widens the vocabulary without widening the *behaviour*: it
produces canonical tokens in the same shape the money check already
consumes, so the existing decision, repair and bilingual honesty line all
apply unchanged. What it adds is what counts as a value.

Two tiers, because the risk is not symmetric
--------------------------------------------

A guard that strips content is dangerous in proportion to how ordinary its
vocabulary is. So the units are split by how they appear in speech:

``_SPEC_UNITS``
    Hz, ms, mAh, GB, dpi, Mbps, nits, stars. These essentially never occur
    in casual conversation. A number carrying one is a specification, and
    the unit alone is enough.

``_AMBIENT_UNITS``
    hours, kg, inches, percent. These are ordinary words -- "it steeps for
    twelve hours" is not a claim about a product -- so they count only when
    the same clause also names what is being measured ("battery life",
    "weighs", "screen"). Without that rule the cold-brew turn in the
    everyday dogfood arc would have had its answer stripped.

A bare number is never a claim, in either tier. That is inherited
deliberately: ``grounded_values`` already refuses to second-guess "three
hotels" or "2026", and the same restraint is what keeps this from becoming
a disclaimer generator -- which A1 spent real effort removing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LANGUAGES = ("en", "ko")


@dataclass(frozen=True)
class Claim:
    """One measured attribute stated in a piece of text."""

    kind: str      # "refresh_rate"
    value: str     # "240"
    unit: str      # "hz"
    text: str      # "240Hz", as written
    tier: str      # "spec" | "ambient"

    @property
    def token(self) -> str:
        """The canonical form two texts are compared on."""
        return f"{self.kind}:{self.value}"


# Canonical kind per unit, with the spellings each unit really appears in.
# Aliases matter more than completeness here: "27-inch", "27 inches" and
# '27"' are the same claim, and a guard that treats them as three misses
# the one case it exists for -- the value that *is* in the evidence.
_SPEC_UNITS: dict[str, tuple[str, ...]] = {
    "refresh_rate": ("hz", "khz"),
    "response_time": ("ms",),
    "capacity_mah": ("mah",),
    "energy_wh": ("wh",),
    "power_w": ("w", "watt", "watts"),
    "storage": ("gb", "tb", "mb"),
    "megapixels": ("mp", "megapixel", "megapixels"),
    "density": ("dpi", "ppi"),
    "bandwidth": ("mbps", "gbps", "kbps"),
    "frequency": ("ghz", "mhz"),
    "brightness": ("nits", "cd/m2"),
}

_AMBIENT_UNITS: dict[str, tuple[str, ...]] = {
    "duration_h": ("hour", "hours", "hr", "hrs"),
    "duration_min": ("minute", "minutes", "min", "mins"),
    "weight": ("kg", "kgs", "g", "grams", "lb", "lbs", "pound", "pounds", "oz"),
    "length": ("inch", "inches", "in", "cm", "mm", "m", "metre", "metres"),
    "percent": ("%", "percent"),
    "distance": ("km", "mi", "mile", "miles"),
    "volume": ("l", "litre", "litres", "ml"),
}

# What an ambient unit has to be measuring before it counts. Checked in the
# same clause, so "battery life is about 14 hours" is a claim and "let it
# steep for 14 hours" is not.
_MEASURED_THING = re.compile(
    r"\bbattery(?:\s+life)?\b|\bruntime\b|\bplayback\b|\bcharge[sd]?\b"
    r"|\bweigh[st]?\b|\bweight\b|\bheav(?:y|ier)\b"
    r"|\bscreen\b|\bdisplay\b|\bpanel\b|\bdimensions?\b|\bdiagonal\b"
    r"|\bsize[ds]?\b|\bwide\b|\btall\b|\bthick\b|\bdepth\b"
    r"|\bcapacity\b|\bbrightness\b|\brange\b|\bresolution\b",
    re.IGNORECASE,
)

_UNIT_KIND: dict[str, tuple[str, str]] = {}
for _kind, _units in _SPEC_UNITS.items():
    for _unit in _units:
        _UNIT_KIND[_unit] = (_kind, "spec")
for _kind, _units in _AMBIENT_UNITS.items():
    for _unit in _units:
        _UNIT_KIND[_unit] = (_kind, "ambient")

# Longest first, so "mah" is not read as "m" and "inches" not as "in".
_UNIT_PATTERN = "|".join(
    re.escape(unit) for unit in sorted(_UNIT_KIND, key=len, reverse=True)
)

# A number, then a unit, with an optional space or hyphen between them.
# The trailing boundary keeps "27 inch" from matching inside "27 inchoate",
# and the leading one keeps "240" out of "1240Hz".
_MEASUREMENT = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*[-‑]?\s*(" + _UNIT_PATTERN + r")(?![\w])",
    re.IGNORECASE,
)

# Ratings say themselves differently: "4.8 stars", "4.8/5", "rated 4.8".
# The "rated" form is first and swallows an optional "stars" after it, so
# "rated 4.8 stars" is reported as one claim reading the whole phrase
# rather than the truncated "rated 4.8". The flag is correct either way;
# the shorter fragment is just a worse thing to read in a log.
_RATING = re.compile(
    r"\brated\s+(\d(?:\.\d+)?)\s*(?:stars?\b|/\s*(?:5|10)\b)?"
    r"|(\d(?:\.\d+)?)\s*(?:stars?\b|/\s*(?:5|10)\b)",
    re.IGNORECASE,
)

# Screen resolutions and their shorthand -- 1080p, 4K, 1920x1080.
#
# The thousands separator is allowed on both sides deliberately. Without
# it "2,560x1440" matched only "560x1440", so a resolution written with a
# comma in one place and without in another read as two different values
# -- and the guard's expensive mistake is flagging a correct answer, not
# missing a wrong one. `_number` strips the comma before comparison, so
# both spellings land on the same token.
_RESOLUTION = re.compile(
    r"\b(\d{1,2},\d{3}|\d{3,4})\s*[xX×]\s*(\d{1,2},\d{3}|\d{3,4})\b"
    r"|\b(\d{3,4})p\b|\b([48])k\b",
    re.IGNORECASE,
)

_CLAUSE_SPLIT = re.compile(r"[.!?;,]\s*|\s+--+\s+|\s+—\s+")


def _clause_of(text: str, position: int) -> str:
    """The clause a match sits in, for the ambient-unit rule."""
    start = 0
    for found in _CLAUSE_SPLIT.finditer(text):
        if found.end() > position:
            break
        start = found.end()
    end = len(text)
    for found in _CLAUSE_SPLIT.finditer(text, position):
        end = found.start()
        break
    return text[start:end]


def _number(raw: str) -> str:
    """A comparable form of a written number: 1,080 and 1080 are one value."""
    cleaned = raw.replace(",", "").strip()
    if cleaned.endswith(".0"):
        cleaned = cleaned[:-2]
    return cleaned


def claims(text: str, *, spoken: bool = True) -> tuple[Claim, ...]:
    """Every measured attribute this text states.

    ``spoken`` says whether this text is something *Elaina said*, where the
    ambient-unit rule applies, or a retrieved document, where it must not.

    The distinction was found by the guard flagging a correct answer. The
    evidence read "27-inch QHD gaming monitor" and the reply said "a 27
    inch screen"; the reply's clause named a measured thing and the
    evidence's did not, so the evidence produced no length claim and the
    reply's matched nothing. Applying a rule written to protect replies
    against over-flagging to the evidence as well is how a guard ends up
    stripping the one value it should have confirmed.
    """
    said = str(text or "")
    if not said.strip():
        return ()
    found: list[Claim] = []

    for match in _MEASUREMENT.finditer(said):
        value, unit = match.group(1), match.group(2).casefold()
        kind, tier = _UNIT_KIND[unit]
        if spoken and tier == "ambient" and not _MEASURED_THING.search(
            _clause_of(said, match.start())
        ):
            # An ordinary sentence that happens to contain a unit. "Let it
            # steep for twelve hours" is not a claim about a product.
            continue
        found.append(Claim(kind, _number(value), unit, match.group(0), tier))

    for match in _RATING.finditer(said):
        value = match.group(1) or match.group(2)
        if value:
            found.append(
                Claim("rating", _number(value), "stars", match.group(0), "spec")
            )

    for match in _RESOLUTION.finditer(said):
        wide, tall, lines, k = match.groups()
        if wide and tall:
            value = f"{_number(wide)}x{_number(tall)}"
        elif lines:
            value = _number(lines)
        else:
            value = f"{k}k"
        found.append(Claim("resolution", value, "", match.group(0), "spec"))

    return tuple(found)


def tokens(text: str, *, spoken: bool = True) -> set[str]:
    """The canonical values in this text, for set comparison.

    The same shape ``grounded_values._digits`` produces, so widening the
    vocabulary needed no change to the decision or the repair around it.
    """
    return {claim.token for claim in claims(text, spoken=spoken)}


def unsupported(reply: str, evidence: str) -> tuple[Claim, ...]:
    """Attributes stated in the reply that the evidence does not carry.

    The evidence is read permissively and the reply strictly, which is the
    safe direction for both mistakes this can make: a measurement in a
    retrieved document is a fact whatever prose surrounds it, and a
    measurement in ordinary speech is usually not a claim at all.
    """
    known = tokens(evidence, spoken=False)
    return tuple(claim for claim in claims(reply) if claim.token not in known)


def contradicted(reply: str, evidence: str) -> tuple[Claim, ...]:
    """Attributes whose *kind* is in the evidence with a different value.

    A stronger fault than an unsupported one, and worth separating: the
    evidence was read, the attribute was found, and the number came out
    different. That is not a gap being filled from memory, it is a value
    being changed.
    """
    by_kind: dict[str, set[str]] = {}
    for claim in claims(evidence, spoken=False):
        by_kind.setdefault(claim.kind, set()).add(claim.value)
    return tuple(
        claim for claim in claims(reply)
        if claim.kind in by_kind and claim.value not in by_kind[claim.kind]
    )


def disagreements(*sources: str) -> dict[str, set[str]]:
    """Attribute kinds two sources state differently.

    Two sources disagreeing is a state, not a race -- the milestone plan's
    words. This reports it so a caller can say so rather than silently
    taking whichever was read last.
    """
    seen: dict[str, set[str]] = {}
    for source in sources:
        for claim in claims(source, spoken=False):
            seen.setdefault(claim.kind, set()).add(claim.value)
    return {kind: values for kind, values in seen.items() if len(values) > 1}


# Search evidence arrives as numbered blocks -- "[1] Title / Source: url /
# Snippet: ..." -- and that numbering is the only per-source structure the
# evidence string carries by the time it reaches the guard. Splitting on it
# recovers the sources without threading a new parameter through four
# layers, which is what made conflict detection shippable at all rather
# than a function nobody calls.
_SOURCE_BLOCK = re.compile(r"(?m)^\s*\[\d+\]\s")


def sources(evidence: str) -> tuple[str, ...]:
    """The separate retrieved sources inside one evidence string."""
    text = str(evidence or "")
    if not text.strip():
        return ()
    parts = [part.strip() for part in _SOURCE_BLOCK.split(text) if part.strip()]
    return tuple(parts)


def conflicts(evidence: str) -> dict[str, set[str]]:
    """Attributes the retrieved sources do not agree on."""
    found = sources(evidence)
    return disagreements(*found) if len(found) > 1 else {}


# The "Source: <host>" line each search result carries. Read rather than
# threaded through, for the same reason the numbered blocks are.
_SOURCE_LINE = re.compile(r"(?im)^\s*source:\s*(\S+)")


def source_of(claim: Claim, evidence: str) -> str:
    """Which retrieved source carried this attribute, or "".

    Internal attribution: this goes in the log, not in the reply. Knowing
    that 165Hz came from bestbuy.com and 180Hz from lg.com is what makes a
    disagreement diagnosable instead of merely reported, and it costs
    nothing because the evidence already names its sources.

    A6 does not put it in front of the user. "According to bestbuy.com" in
    every sentence is the disclaimer register A1 spent effort removing, and
    the question of when a source is worth naming aloud is a conversational
    one that has not been measured.
    """
    for block in sources(evidence):
        if claim.token in tokens(block, spoken=False):
            named = _SOURCE_LINE.search(block)
            return named.group(1) if named else ""
    return ""


def conflicting_claims(reply: str, evidence: str) -> tuple[tuple[Claim, str], ...]:
    """Claims the reply states that the sources disagree about.

    Only claims the reply actually makes. A source disagreement about
    something she never mentioned is not worth a sentence -- saying it
    anyway is how honesty turns into the disclaimer footer A1 removed.
    """
    disputed = conflicts(evidence)
    if not disputed:
        return ()
    noted: list[tuple[Claim, str]] = []
    for claim in claims(reply):
        values = disputed.get(claim.kind)
        if not values or claim.value not in values:
            continue
        others = sorted(values - {claim.value})
        if others:
            # Written the way she wrote hers, so "165Hz" is answered by
            # "180Hz" and "rated 4.8 stars" by "rated 4.6 stars" -- the
            # bare number would drop the unit that makes it legible.
            noted.append((claim, claim.text.replace(claim.value, others[0], 1)))
    return tuple(noted)
