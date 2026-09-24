"""A temperature in the unit the question was asked in.

Measured on the everyday-answers check, three runs in a row: "물은 몇 도에서
끓어?" answered "물의 끓는점은 212°F입니다" and "212도 화씨입니다". The fact
is right and the unit is not — 몇 도 from a Korean speaker means Celsius, and
the Fahrenheit came from the English turns before it.

The market and the unit are different questions. This machine now lives in
the United States (``user.country`` is ``US``), so English answers stay in
Fahrenheit; a Korean question is answered in Celsius regardless, because
that is the unit the person asking it thinks in.

Deterministic, because a conversion is arithmetic: the model is not asked to
do it again. Only fires when the reply gives Fahrenheit and the question did
not ask for Fahrenheit -- "화씨로 몇 도야?" is left exactly as it is.
"""

from __future__ import annotations

import re

LANGUAGES = ("ko",)

# 212°F, 212 F, 212 degrees Fahrenheit, 화씨 212도, 212도 화씨.
# ``\b`` is no use at the end of these: "212°F입니다" has no word boundary
# between F and 입, because both are word characters -- measured, it matched
# nothing at all in the one language this module is for.
#
# Korean carries the minus as a word: "영하 4°F" is -4°F, and -20°C is said
# "영하 20도" rather than "섭씨 -20도". Both directions are handled here, so
# the sign is never doubled or lost.
_FAHRENHEIT = (
    re.compile(r"(영하\s*)?(-?\d+(?:\.\d+)?)\s*°\s*F(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"(영하\s*)?(-?\d+(?:\.\d+)?)\s*(?:degrees?\s*)?Fahrenheit(?![A-Za-z])",
               re.IGNORECASE),
    re.compile(r"(영하\s*)?(-?\d+(?:\.\d+)?)\s*도\s*화씨"),
    re.compile(r"화씨\s*(영하\s*)?(-?\d+(?:\.\d+)?)\s*도"),
    re.compile(r"(영하\s*)?(-?\d+(?:\.\d+)?)\s*℉"),
)
# They asked in Fahrenheit, so they get Fahrenheit.
_ASKED_IN_FAHRENHEIT = re.compile(r"화씨|fahrenheit|°\s*F\b|℉", re.IGNORECASE)


def _said(value: float) -> str:
    """100도, not 100.0도; 37.8도 keeps the decimal that matters."""
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 0.05:
        return str(int(round(rounded)))
    return f"{rounded:.1f}"


def in_celsius(reply: str, said: str) -> str:
    """The reply with Fahrenheit temperatures said in Celsius instead.

    Unchanged when there is no Fahrenheit in it, or when the question asked
    for Fahrenheit. The number is converted here rather than regenerated:
    asking the model again for the same fact in another unit is a second
    chance to get the fact wrong.
    """
    text = str(reply or "")
    if not text or _ASKED_IN_FAHRENHEIT.search(str(said or "")):
        return text
    for pattern in _FAHRENHEIT:
        def swap(match: re.Match) -> str:
            fahrenheit = float(match.group(2))
            if match.group(1):
                fahrenheit = -abs(fahrenheit)
            degrees = (fahrenheit - 32.0) * 5.0 / 9.0
            if degrees < 0:
                return f"영하 {_said(abs(degrees))}도"
            return f"섭씨 {_said(degrees)}도"

        text = pattern.sub(swap, text)
    return text
