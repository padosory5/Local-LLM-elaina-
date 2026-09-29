"""Requests whose answer is computed, recognised before the model router.

Phase 3B (docs/PHASE3_PLAN.md §2, 3B). The clock, arithmetic and unit
conversion have exact answers that code computes. Routing them through a
language model's reading of "freshness" is how a better router made them
worse: the 27B correctly called "what time is it" live information, the
pipeline sent live information to a web search, and the answer was New
York's sunrise read off a snippet -- on 14 of 26 time turns in each 27B arm.

This module claims a turn only when the *whole utterance* is such a request,
parsed with each domain's own grammar:

* the clock -- the local time, date, day or year, or the time in one or
  more places ``world_clock`` knows;
* arithmetic -- an expression ``tools.calculator`` can evaluate;
* unit conversion -- a quantity between two units of the same dimension.

"What time does the museum open", "time to leave", a word problem, a place
the clock does not know: none of these parse, and the router decides them
as before. A claim returns the ``IntentDecision`` the router would have
produced, so everything after routing -- topic, consent, capability
selection -- runs as on a routed turn (the ``tier0`` contract).

Mode (config ``routing.deterministic_domains``, env ``ELAINA_DOMAINS``):
``shadow`` computes and logs the claim beside the router's decision and
changes nothing; ``act`` routes by it; ``off`` does neither.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from brain import world_clock

CLOCK = "clock"
ARITHMETIC = "arithmetic"
CONVERSION = "conversion"

MODES = ("act", "shadow", "off")


def mode(config=None) -> str:
    """How the claims are used this run."""
    chosen = os.environ.get("ELAINA_DOMAINS", "").strip().lower()
    if not chosen and config is not None:
        try:
            chosen = str(config.get(
                "routing", "deterministic_domains", default="act", required=False,
            ) or "").strip().lower()
        except Exception:
            chosen = ""
    return chosen if chosen in MODES else "act"


@dataclass(frozen=True)
class DomainClaim:
    domain: str
    intent: str
    said: str
    places: tuple[str, ...] = ()
    expression: str = ""
    result: str = ""       # the computed answer, as a trusted result text
    reason: str = ""

    def decision(self):
        """The routing decision this claim stands for."""
        from brain.intent_router import IntentDecision

        return IntentDecision(
            intent=self.intent,
            confidence=1.0,
            # The self-contained reading, as the router gives one: for
            # arithmetic the expression itself ("2+2" for "what's 2+2"),
            # which the last-resort evaluator reads; otherwise their words.
            normalized_request=(
                self.expression if self.domain == ARITHMETIC and self.expression
                else self.said
            ),
            reason=self.reason,
            topic="time" if self.domain == CLOCK else "calculation",
            speech_act="information_request",
            information_freshness="unknown" if self.domain == CLOCK else "stable",
            requires_external_evidence=False,
            topic_shift=True,
            # A time, a sum, a conversion: stated, never illustrated.
            answer_shape="state",
        )

    def as_trusted_result_text(self) -> str:
        """The computed answer, in the shape a calculation plan hands over."""
        return self.result

    def as_dict(self) -> dict:
        return {"domain": self.domain, "intent": self.intent,
                "places": list(self.places), "expression": self.expression,
                "result": self.result}


# ------------------------------------------------------------------- clock

_CLOCK_VOCABULARY = frozenset("""
what what's whats is it the time date day year month today today's todays now
right current currently tell me please can could you in at for and or vs
versus compared with to of there here my local utc gmt offset timezone zone
hey elaina so okay ok um do know exact exactly again quick which o'clock
clock it's its a an then
""".split())
_CLOCK_HEADS = frozenset("time date day year month clock offset timezone zone".split())

# Korean: the whole utterance, once any place and its particle is removed,
# is one of these, or several joined by 고.
_KOREAN_LEAD = r"(?:(?:지금|현재|오늘|올해|이번\s*달|그럼|그러면|혹시|근데|거기|거긴|저기|여기)\s*)*"
_KOREAN_CLOCK = re.compile(
    rf"^{_KOREAN_LEAD}(?:"
    r"몇\s*시\s*(?:몇\s*분)?(?:야|예요|에요|입니까|인가요|지|냐|일까)?"
    r"|시간\s*(?:이|은)?\s*(?:몇\s*시(?:야|예요)?|어떻게\s*돼(?:요)?|알려\s*(?:줘|주세요|줄래))?"
    r"|며칠\s*(?:이야|이에요|인가요|이지)?"
    r"|날짜\s*(?:가|는)?\s*(?:뭐야|며칠이야|어떻게\s*돼(?:요)?|알려\s*(?:줘|주세요|줄래))?"
    r"|무슨\s*요일\s*(?:이야|이에요|인가요)?"
    r"|몇\s*년\s*(?:이야|이에요|인가요)?"
    r"|몇\s*월\s*며칠\s*(?:이야|이에요)?"
    r")\s*[?.!]*$"
)
_KOREAN_PARTICLE = r"(?:은|는|이|가|의|에서|에선|과|와|하고|랑|이랑|쪽은|은요|는요)?"


def _clock_claim(said: str) -> DomainClaim | None:
    places = tuple(world_clock.read_places(said))
    if re.search(r"[가-힣]", said):
        rest = said
        for place in places:
            rest = re.sub(rf"{re.escape(place)}{_KOREAN_PARTICLE}", " ", rest)
        parts = [part.strip() for part in re.split(r"(?<=[가-힣])고\s+|,\s*", rest) if part.strip()]
        if parts and all(_KOREAN_CLOCK.match(part) for part in parts):
            return DomainClaim(CLOCK, "time_question", said, places=places,
                               reason="A clock question: answered from the clock.")
        return None
    text = said.casefold()
    for place in sorted(places, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(place)}\b", " ", text)
    words = re.findall(r"[a-z][a-z'’]*|\d+", text.replace("’", "'"))
    if not words or not (set(words) & _CLOCK_HEADS):
        return None
    if not set(words) <= _CLOCK_VOCABULARY:
        return None
    # A question, not a remark about time: "what", "tell", "which", or a
    # question mark.
    if not ({"what", "what's", "whats", "tell", "which", "current"} & set(words)) and "?" not in said:
        return None
    return DomainClaim(CLOCK, "time_question", said, places=places,
                       reason="A clock question: answered from the clock.")


# -------------------------------------------------------------- arithmetic

_LEAD_IN = re.compile(
    r"^\s*(?:hey\s+elaina[,\s]*)?(?:what(?:'s|’s| is)|whats|calculate|compute|"
    r"how\s+much\s+is|can\s+you\s+(?:calculate|compute|work\s+out|tell\s+me)|"
    r"tell\s+me|work\s+out|solve)\s+",
    re.IGNORECASE,
)
_TAIL = re.compile(r"\s*(?:=|equals?|is)?\s*[?.!]*\s*(?:please)?\s*[?.!]*\s*$", re.IGNORECASE)
_WORD_OPERATORS = (
    (r"\bmultiplied\s+by\b", "*"), (r"\bdivided\s+by\b", "/"),
    (r"\bto\s+the\s+power\s+of\b", "**"), (r"\bplus\b", "+"),
    (r"\bminus\b", "-"), (r"\btimes\b", "*"), (r"\bover\b", "/"),
    (r"(?<=\d)\s*[x×]\s*(?=\d)", "*"), (r"÷", "/"), (r"−", "-"),
    (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"), (r"\^", "**"),
)
_KOREAN_OPERATORS = (
    ("더하기", "+"), ("빼기", "-"), ("곱하기", "*"), ("나누기", "/"),
)
_KOREAN_TAIL = re.compile(r"\s*(?:은|는|이|가)?\s*(?:얼마(?:야|예요|에요|인가요|지)?|뭐야|몇이야)?\s*[?.!]*$")
_EXPRESSION = re.compile(r"^[\d\s.+\-*/()]+$")
_PERCENT_OF = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE,
)
_KOREAN_PERCENT_OF = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*의\s*(\d+(?:\.\d+)?)\s*(?:%|퍼센트)\s*$")


def _number(value: float) -> str:
    rounded = round(value, 6)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.6f}".rstrip("0").rstrip(".")


def _arithmetic_claim(said: str) -> DomainClaim | None:
    from tools.calculator import CalculationError, evaluate_expression

    korean = bool(re.search(r"[가-힣]", said))
    body = said.strip()
    if korean:
        body = _KOREAN_TAIL.sub("", body)
        for word, symbol in _KOREAN_OPERATORS:
            body = body.replace(word, f" {symbol} ")
        percent = _KOREAN_PERCENT_OF.match(body)
        if percent:
            body = f"{percent.group(2)} / 100 * {percent.group(1)}"
    else:
        body = _LEAD_IN.sub("", body)
        body = _TAIL.sub("", body)
        percent = _PERCENT_OF.match(body)
        if percent:
            body = f"{percent.group(1)} / 100 * {percent.group(2)}"
        for pattern, symbol in _WORD_OPERATORS:
            body = re.sub(pattern, f" {symbol} ", body, flags=re.IGNORECASE)
    body = re.sub(r"(?<=\d),(?=\d{3}\b)", "", body)
    body = " ".join(body.split())
    if not body or not _EXPRESSION.match(body):
        return None
    numbers = re.findall(r"\d+(?:\.\d+)?", body)
    if len(numbers) < 2 or not re.search(r"(?<=[\d)\s])[+\-*/](?=[\s\d(*])", body):
        return None
    try:
        value = evaluate_expression(body)
    except (CalculationError, ValueError, TypeError):
        return None
    result = (
        "Verified calculation. The number below was computed exactly by a "
        "calculator tool -- never estimated. Use it exactly; do not "
        "recompute or round it differently.\n"
        f"{body} = {_number(value)}"
    )
    return DomainClaim(ARITHMETIC, "calculation", said, expression=body,
                       result=result, reason="Arithmetic: computed exactly.")


# -------------------------------------------------------------- conversion

# Factors to one base unit per dimension. Physical definitions, not phrasing.
_UNITS: dict[str, tuple[str, float]] = {}


def _define(dimension: str, factor: float, *names: str) -> None:
    for name in names:
        _UNITS[name] = (dimension, factor)


_define("length", 1000.0, "km", "kilometer", "kilometers", "kilometre", "kilometres", "킬로미터", "키로")
_define("length", 1.0, "m", "meter", "meters", "metre", "metres", "미터")
_define("length", 0.01, "cm", "centimeter", "centimeters", "centimetre", "centimetres", "센티미터", "센티")
_define("length", 0.001, "mm", "millimeter", "millimeters", "millimetre", "millimetres", "밀리미터")
_define("length", 1609.344, "mi", "mile", "miles", "마일")
_define("length", 0.9144, "yd", "yard", "yards", "야드")
_define("length", 0.3048, "ft", "foot", "feet", "피트")
_define("length", 0.0254, "in", "inch", "inches", "인치")
_define("mass", 1.0, "kg", "kilogram", "kilograms", "kilo", "kilos", "킬로그램")
_define("mass", 0.001, "g", "gram", "grams", "그램")
_define("mass", 0.000001, "mg", "milligram", "milligrams", "밀리그램")
_define("mass", 0.45359237, "lb", "lbs", "pound", "pounds", "파운드")
_define("mass", 0.028349523125, "oz", "ounce", "ounces", "온스")
_define("volume", 1.0, "l", "liter", "liters", "litre", "litres", "리터")
_define("volume", 0.001, "ml", "milliliter", "milliliters", "millilitre", "millilitres", "밀리리터")
_define("volume", 3.785411784, "gal", "gallon", "gallons", "갤런")
_define("volume", 0.946352946, "qt", "quart", "quarts")
_define("volume", 0.473176473, "pt", "pint", "pints")
_define("volume", 0.2365882365, "cup", "cups", "컵")
_define("volume", 0.0295735295625, "fl oz", "fluid ounce", "fluid ounces")
_define("volume", 0.01478676478125, "tbsp", "tablespoon", "tablespoons", "큰술")
_define("volume", 0.00492892159375, "tsp", "teaspoon", "teaspoons", "작은술")
_define("speed", 0.44704, "mph", "miles per hour")
_define("speed", 1000.0 / 3600.0, "km/h", "kph", "kilometers per hour", "kilometres per hour")
_define("speed", 1.0, "m/s", "meters per second", "metres per second")

_TEMPERATURES = {
    "c": "C", "°c": "C", "celsius": "C", "centigrade": "C", "degrees celsius": "C", "섭씨": "C",
    "f": "F", "°f": "F", "fahrenheit": "F", "degrees fahrenheit": "F", "화씨": "F",
    "k": "K", "kelvin": "K", "kelvins": "K",
}

_UNIT_NAMES = sorted(list(_UNITS) + list(_TEMPERATURES), key=len, reverse=True)
_UNIT = "(" + "|".join(re.escape(name) for name in _UNIT_NAMES) + ")"
_NUMBER = r"(-?\d+(?:,\d{3})*(?:\.\d+)?)"
_CONVERT_EN = (
    # "100 °C in Fahrenheit", "convert 5 miles to km", "what's 2 cups in ml"
    re.compile(rf"^{_NUMBER}\s*(?:degrees?\s+)?{_UNIT}\s+(?:in|to|into|as)\s+(?:degrees?\s+)?{_UNIT}$", re.I),
    # "how many ounces in a cup", "how many feet are in a mile"
    re.compile(rf"^how\s+many\s+{_UNIT}\s+(?:are\s+)?(?:there\s+)?(?:in|per)\s+(?:a|an|one|{_NUMBER})?\s*{_UNIT}$", re.I),
    # "5 km is how many miles"
    re.compile(rf"^{_NUMBER}\s*{_UNIT}\s+(?:is|are|equals?)\s+how\s+many\s+{_UNIT}$", re.I),
)
_CONVERT_KO = (
    # "섭씨 100도는 화씨로 몇 도야"
    re.compile(r"^(섭씨|화씨)\s*" + _NUMBER + r"\s*도\s*(?:는|은)?\s*(섭씨|화씨)\s*(?:로|으로)?\s*몇\s*도"),
    # "5킬로미터는 몇 마일이야"
    re.compile(rf"^{_NUMBER}\s*{_UNIT}\s*(?:는|은|이|가)?\s*몇\s*{_UNIT}"),
)


def _temperature(value: float, source: str, target: str) -> float:
    celsius = {"C": value, "F": (value - 32) * 5 / 9, "K": value - 273.15}[source]
    return {"C": celsius, "F": celsius * 9 / 5 + 32, "K": celsius + 273.15}[target]


def _unit_key(name: str) -> str:
    return name.casefold().strip()


def _convert(amount: float, source: str, target: str) -> tuple[float, str, str] | None:
    source, target = _unit_key(source), _unit_key(target)
    if source in _TEMPERATURES and target in _TEMPERATURES:
        a, b = _TEMPERATURES[source], _TEMPERATURES[target]
        if a == b:
            return None
        return _temperature(amount, a, b), f"°{a}" if a != "K" else "K", f"°{b}" if b != "K" else "K"
    if source not in _UNITS or target not in _UNITS:
        return None
    (dimension_a, factor_a), (dimension_b, factor_b) = _UNITS[source], _UNITS[target]
    # An ounce beside a volume is a fluid ounce.
    if dimension_a != dimension_b:
        if source in {"oz", "ounce", "ounces", "온스"} and dimension_b == "volume":
            dimension_a, factor_a, source = "volume", _UNITS["fl oz"][1], "fl oz"
        elif target in {"oz", "ounce", "ounces", "온스"} and dimension_a == "volume":
            dimension_b, factor_b, target = "volume", _UNITS["fl oz"][1], "fl oz"
        else:
            return None
    return amount * factor_a / factor_b, source, target


def _conversion_claim(said: str) -> DomainClaim | None:
    text = said.strip()
    korean = bool(re.search(r"[가-힣]", text))
    matched = None
    if korean:
        text = re.sub(r"\s*(?:야|이야|예요|에요|인가요|지)?\s*[?.!]*$", "", text)
        first = _CONVERT_KO[0].match(text)
        if first:
            source, amount, target = first.group(1), first.group(2), first.group(3)
            matched = (amount, source, target)
        else:
            second = _CONVERT_KO[1].match(text)
            if second:
                matched = (second.group(1), second.group(2), second.group(3))
    else:
        text = _LEAD_IN.sub("", text)
        text = re.sub(r"^\s*convert\s+", "", text, flags=re.I)
        text = re.sub(r"\s*[?.!]+\s*$", "", text)
        for index, pattern in enumerate(_CONVERT_EN):
            match = pattern.match(text)
            if not match:
                continue
            if index == 1:
                target, count, source = match.group(1), match.group(2), match.group(3)
                matched = (count or "1", source, target)
            else:
                matched = (match.group(1), match.group(2), match.group(3))
            break
    if matched is None:
        return None
    amount_text, source, target = matched
    try:
        amount = float(amount_text.replace(",", ""))
    except ValueError:
        return None
    converted = _convert(amount, source, target)
    if converted is None:
        return None
    value, source_label, target_label = converted
    if source_label.startswith("°"):
        stated = f"{_number(amount)} {source_label} = {round(value, 1):g} {target_label}"
    else:
        stated = f"{_number(amount)} {source_label} = {_number(round(value, 4))} {target_label}"
    result = (
        "Verified unit conversion, computed exactly from the unit "
        "definitions -- never estimated. Use it exactly.\n" + stated
    )
    return DomainClaim(CONVERSION, "calculation", said, expression=stated,
                       result=result, reason="A unit conversion: computed exactly.")


# ------------------------------------------------------------------ claim


def claim(said: str) -> DomainClaim | None:
    """The domain this whole utterance belongs to, or None."""
    text = " ".join(str(said or "").split())
    if not text or len(text) > 160:
        return None
    for reader in (_clock_claim, _conversion_claim, _arithmetic_claim):
        found = reader(text)
        if found is not None:
            return found
    return None


def claims_clock(said: str) -> bool:
    """Whether this is a question the clock answers, whatever else is said of it."""
    found = claim(said)
    return found is not None and found.domain == CLOCK
