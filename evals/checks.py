"""The deterministic half of scoring: what can be decided without a judge.

Each check reads one turn -- the displayed reply, the text handed to the
voice, the turn's record from core/turn_trace.py, whether cards were shown
-- and returns pass or fail with the detail that decided it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


# Characters and shapes a voice cannot be relied on to say. Exponent marks,
# super- and subscripts, roots, integrals, sums, comparison and
# approximation signs, a digit/digit fraction, and the factorial mark after
# a digit, a letter or a bracket. This is a measuring instrument, not a
# rewrite rule: it decides whether text is speakable, never how to say it.
UNSPEAKABLE = re.compile(
    r"[\^²³⁴⁵⁶⁷⁸⁹⁰¹₀₁₂₃₄₅₆₇₈₉√∛∫∑∏≈≠≤≥±×÷∞∂∇]"
    r"|(?<=\d)/(?=\d)"
    # Factorial: after a digit, a bracket, or a one-letter variable ("n!"),
    # never after a word -- "Yes!" is punctuation.
    r"|(?<=[\d)])!|(?<=\b[A-Za-z])!"
)

# A token of notation: anything that carries an operator or a raised or
# lowered figure. Used to ask whether the draft's mathematics reached the
# screen unchanged.
_NOTATION = re.compile(r"[^\s,;:]*[=^²³⁴⁵⁶⁷⁸⁹⁰¹₀₁₂₃√∫∑≈≠≤≥±×÷][^\s,;:]*")
# A spaced dash between two operands. The speech filter turns "1 - x" into
# "1, x" on screen, so these are counted in the draft and in the display.
_SPACED_DASH = re.compile(r"([\w)²³⁴]+)\s+[-−]\s+([\w(]+)")
_OPERAND_END = set("0123456789)²³⁴")


def _minus_signs(text: str) -> int:
    """Dashes that are minus signs: between short or numeric operands.

    'Paris - the capital' is a dash in prose and is not counted; '1 - x',
    '(x − a)' and '10 - 4' are.
    """
    count = 0
    for match in _SPACED_DASH.finditer(text or ""):
        left, right = match.group(1), match.group(2)
        short = len(left) <= 4 and len(right) <= 4
        numeric = left[-1] in _OPERAND_END and (
            right[0].isdigit() or right[0] == "(" or len(right) == 1
        )
        if short or numeric:
            count += 1
    return count
_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[\w']+", re.UNICODE)


@dataclass
class TurnResult:
    """What one turn produced, as a check needs to see it."""

    said: str
    display: str
    speech: str = ""
    draft: str = ""
    searched: bool = False
    cards: bool = False
    previous_reply: str = ""
    # When the turn began, as an aware local time. The ground truth of every
    # clock, offset and calendar check is computed from it, so a run can be
    # re-scored later and still be judged against its own moment.
    when: datetime | None = None
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------ correctness: clocks

_NUMBER_WORDS = {
    word: value for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}
_EN_TIME = re.compile(r"\b(\d{1,2}):(\d{2})(?:\s*([AaPp])\.?\s*[Mm]\.?\b)?")
_KO_TIME = re.compile(
    r"(오전|오후|새벽|아침|낮|저녁|밤)?\s*(\d{1,2})\s*시(?!간)\s*(?:(\d{1,2})\s*분|(반))?"
)
# "UTC-0700", "UTC−7", and the same said in words: "UTC minus seven hours",
# "your UTC offset is minus seven hours". The Phase 3 final rerun failed two
# right answers of the second kind as stating no offset at all.
_OFFSET = re.compile(
    r"(?:UTC|GMT)(?:\s+offset(?:\s+(?:is|of))?)?\s*"
    r"([+\-−–]|minus\b|plus\b|마이너스|플러스)\s*"
    r"(\d{1,2}|(?:" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\b)"
    r"(?::?(\d{2}))?",
    re.IGNORECASE,
)
_OFFSET_WORDS = re.compile(
    r"\b(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\s+hours?\s+(behind|ahead of)\s+"
    r"(?:UTC|GMT|Coordinated Universal Time|Greenwich)",
    re.IGNORECASE,
)
_HOURS = re.compile(
    r"\b(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")[\s-]*hours?\b|(\d{1,2})\s*시간",
    re.IGNORECASE,
)


def _number(word: str) -> float:
    word = str(word).casefold()
    return float(_NUMBER_WORDS[word]) if word in _NUMBER_WORDS else float(word)


def _zone_time(when: datetime, zone: str) -> datetime:
    if zone == "local":
        return when
    return when.astimezone(ZoneInfo(zone))


def stated_clock_times(text: str) -> list[tuple[int, int, bool]]:
    """(hour, minute, meridiem_known) for every clock time in the text.

    Hours are 0-23 when the text says AM/PM or 오전/오후; otherwise the
    written hour, which a check accepts as either half of the day.
    """
    found = []
    for match in _EN_TIME.finditer(text or ""):
        hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
        if hour > 23 or minute > 59:
            continue
        if meridiem:
            hour = hour % 12 + (12 if meridiem.lower() == "p" else 0)
        found.append((hour, minute, bool(meridiem)))
    for match in _KO_TIME.finditer(text or ""):
        part, hour = match.group(1), int(match.group(2))
        minute = 30 if match.group(4) else int(match.group(3) or 0)
        if hour > 24 or minute > 59:
            continue
        if part:
            afternoon = part in ("오후", "낮", "저녁", "밤") and not (part == "낮" and hour == 12)
            hour = hour % 12 + (12 if afternoon else 0)
        found.append((hour, minute, bool(part)))
    return found


def _same_clock(stated: tuple[int, int, bool], truth: datetime, minutes: int = 3) -> bool:
    hour, minute, known = stated
    target = truth.hour * 60 + truth.minute
    candidates = [hour * 60 + minute]
    if not known and hour < 13:
        candidates.append(((hour + 12) % 24) * 60 + minute)
    return any(min(abs(c - target), 1440 - abs(c - target)) <= minutes for c in candidates)


def stated_offsets(text: str) -> list[float]:
    """Every UTC offset the text states, in hours."""
    found = []
    for match in _OFFSET.finditer(text or ""):
        written = match.group(1).casefold()
        sign = -1 if written in ("-", "−", "–", "minus", "마이너스") else 1
        found.append(sign * (_number(match.group(2)) + int(match.group(3) or 0) / 60))
    for match in _OFFSET_WORDS.finditer(text or ""):
        hours = _number(match.group(1))
        found.append(-hours if match.group(2).lower() == "behind" else hours)
    return found


def stated_hour_counts(text: str) -> list[float]:
    return [_number(m.group(1) or m.group(2)) for m in _HOURS.finditer(text or "")]


# ---------------------------------------------------- correctness: calendar

_MONTHS = {
    name: number for number, names in enumerate([
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
        ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
        ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
        ("december", "dec"),
    ], 1) for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_EN_DATE = re.compile(
    rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b"
    rf"|\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\b",
    re.IGNORECASE,
)
_KO_DATE = re.compile(r"(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일(?!\s*(?:간|동안))")
_YEAR = re.compile(r"\b(20\d\d)\b|(20\d\d)\s*년")
_EN_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_KO_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")
_WEEKDAY = re.compile(
    r"\b(" + "|".join(_EN_WEEKDAYS) + r")\b|([월화수목금토일])요일", re.IGNORECASE,
)


def stated_dates(text: str) -> list[tuple[int | None, int]]:
    """(month or None, day) for every date the text states."""
    found = []
    for match in _EN_DATE.finditer(text or ""):
        if match.group(1):
            found.append((_MONTHS[match.group(1).lower()], int(match.group(2))))
        else:
            found.append((_MONTHS[match.group(4).lower()], int(match.group(3))))
    for match in _KO_DATE.finditer(text or ""):
        month = int(match.group(1)) if match.group(1) else None
        found.append((month, int(match.group(2))))
    return [(m, d) for m, d in found if 1 <= d <= 31 and (m is None or 1 <= m <= 12)]


def stated_years(text: str) -> list[int]:
    return [int(m.group(1) or m.group(2)) for m in _YEAR.finditer(text or "")]


def stated_weekdays(text: str) -> list[int]:
    found = []
    for match in _WEEKDAY.finditer(text or ""):
        if match.group(1):
            found.append(_EN_WEEKDAYS.index(match.group(1).lower()))
        else:
            found.append(_KO_WEEKDAYS.index(match.group(2)))
    return found


# --------------------------------------------------- correctness: quantities

_SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


def stated_quantities(text: str, unit: str) -> list[float]:
    """Every number the text states directly before the given unit."""
    pattern = re.compile(
        r"(?<![\w.])(\d[\d,]*(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")\s*"
        r"(thousand|million|billion)?\s*(?:" + unit + r")",
        re.IGNORECASE,
    )
    found = []
    for match in pattern.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        try:
            value = _number(raw)
        except ValueError:
            continue
        found.append(value * _SCALE.get((match.group(2) or "").lower(), 1.0))
    return found


def stated_ranges(text: str, unit: str) -> list[tuple[float, float]]:
    """Every "from X to Y <unit>" the text states, as (low, high).

    A range is one claim, not two: "ranging from 356,000 to 406,000
    kilometers" states where the moon is, and its far end is not a wrong
    distance. The Phase 3 final rerun failed that answer for its 406,000.
    """
    number = r"(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion)?"
    pattern = re.compile(
        r"(?<![\w.])" + number + r"\s*(?:(?:" + unit + r")\s*)?"
        r"(?:to|and|through|–|—|-|~)\s*" + number + r"\s*(?:" + unit + r")",
        re.IGNORECASE,
    )
    found = []
    for match in pattern.finditer(text or ""):
        ends = []
        for raw, scale in ((match.group(1), match.group(2)), (match.group(3), match.group(4))):
            try:
                ends.append(float(raw.replace(",", "")) * _SCALE.get((scale or "").lower(), 1.0))
            except ValueError:
                break
        if len(ends) == 2:
            found.append((min(ends), max(ends)))
    return found


def _correctness(name: str, value, turn: TurnResult) -> CheckResult:
    display = turn.display or ""
    if turn.when is None:
        if name in {"clock_times", "hour_difference", "utc_offset", "calendar"}:
            return CheckResult(name, False, "the turn's time is unknown")
    if name == "clock_times":
        stated = stated_clock_times(display)
        missing = []
        for zone in value:
            truth = _zone_time(turn.when, zone)
            if not any(_same_clock(s, truth) for s in stated):
                missing.append(f"{zone} {truth.strftime('%H:%M')}")
        shown = [f"{h:02d}:{m:02d}" for h, m, _ in stated]
        return CheckResult(name, not missing,
                           f"not stated: {missing}; stated {shown}" if missing else "")
    if name == "hour_difference":
        first, second = (_zone_time(turn.when, zone).utcoffset() for zone in value)
        truth = abs((second - first).total_seconds()) / 3600
        stated = stated_hour_counts(display)
        right = [h for h in stated if abs(h - truth) < 0.01]
        wrong = [h for h in stated if abs(h - truth) >= 0.01]
        passed = bool(right) and not wrong
        return CheckResult(name, passed, "" if passed else
                           f"true gap {truth:g} h; stated {[f'{h:g}' for h in stated]}")
    if name == "utc_offset":
        truth = _zone_time(turn.when, value.get("zone", "local")).utcoffset().total_seconds() / 3600
        stated = stated_offsets(display)
        wrong = [o for o in stated if abs(o - truth) >= 0.01]
        missing = value.get("required") and not stated
        passed = not wrong and not missing
        return CheckResult(name, passed, "" if passed else
                           f"true offset {truth:+g}; stated {[f'{o:+g}' for o in stated]}")
    if name == "calendar":
        today = turn.when
        problems = []
        dates = stated_dates(display)
        wrong_dates = [(m, d) for m, d in dates
                       if d != today.day or (m is not None and m != today.month)]
        if wrong_dates:
            problems.append(f"wrong date {wrong_dates}")
        if value.get("date") and not [1 for m, d in dates if d == today.day and m in (None, today.month)]:
            problems.append("today's date not stated")
        years = stated_years(display)
        if [y for y in years if y != today.year]:
            problems.append(f"wrong year {years}")
        if value.get("year") and today.year not in years:
            problems.append("the year not stated")
        weekdays = stated_weekdays(display)
        if [w for w in weekdays if w != today.weekday()]:
            problems.append("wrong weekday")
        return CheckResult(name, not problems, "; ".join(problems))
    if name == "quantity":
        problems = []
        for spec in value:
            found = stated_quantities(display, spec["unit"])
            tolerance = float(spec.get("tolerance", 0.0))
            slack = tolerance * spec["value"] + 1e-9
            around = {
                end for low, high in stated_ranges(display, spec["unit"])
                if low - slack <= spec["value"] <= high + slack
                for end in (low, high)
            }
            wrong = [q for q in found if abs(q - spec["value"]) > slack and q not in around]
            if wrong:
                problems.append(f"{wrong} where {spec['value']:g} is right")
            if spec.get("required") and not found:
                problems.append(f"no quantity in {spec['unit']!r}")
        return CheckResult(name, not problems, "; ".join(problems))
    return CheckResult(name, False, "unknown check")


@dataclass
class CheckResult:
    check: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"check": self.check, "passed": self.passed, "detail": self.detail}


def turn_time(started_at) -> datetime | None:
    """The moment a turn began, from its record, as an aware local time.

    Records store local wall-clock time; it is read in this machine's zone,
    which is the zone the run happened in.
    """
    try:
        return datetime.fromisoformat(str(started_at)).astimezone()
    except (TypeError, ValueError):
        return None


def stored_turn(entry: dict, previous_reply: str = "", started_at=None) -> TurnResult:
    """A TurnResult from a turn as evals/run.py stores it.

    Used both when a run scores its turns and when a report re-scores an
    older run against the current corpus -- one reading of the record, so
    the two cannot disagree about what a turn said.
    """
    trace = entry.get("trace") or {}
    return TurnResult(
        said=entry.get("said", ""),
        display=entry.get("display") or "",
        speech=" ".join(trace.get("speech") or ()),
        draft=trace.get("draft", ""),
        searched=bool(trace.get("searched")),
        cards=bool(trace.get("cards")),
        previous_reply=previous_reply,
        when=turn_time(started_at or trace.get("started_at")),
    )


def words(text: str) -> list[str]:
    return _WORD.findall(str(text or "").casefold())


def similarity(first: str, second: str) -> float:
    """Word-set overlap, 0 to 1."""
    a, b = set(words(first)), set(words(second))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def language_of(text: str) -> str:
    hangul = len(_HANGUL.findall(text or ""))
    latin = len(_LATIN.findall(text or ""))
    if hangul + latin == 0:
        return ""
    return "ko" if hangul >= latin else "en"


def unspeakable(text: str) -> list[str]:
    return sorted({match.group(0) for match in UNSPEAKABLE.finditer(text or "")})


def notation_lost(draft: str, display: str) -> list[str]:
    """Notation in the draft that the screen does not show as written."""
    lost = [
        token for token in dict.fromkeys(_NOTATION.findall(draft or ""))
        if token and token not in (display or "")
    ]
    drafted, shown = _minus_signs(draft), _minus_signs(display)
    if shown < drafted:
        lost.append(f"{drafted - shown} of {drafted} minus sign(s)")
    return lost


def _search(pattern: str, text: str):
    return re.search(pattern, text or "", re.IGNORECASE)


def contains(phrase: str, text: str) -> bool:
    """Whether the text contains the phrase as itself.

    A phrase that starts or ends with a digit is not found inside a longer
    number: "8 hours" is not in "12 to 18 hours", nor "8시간" in "18시간".
    The Phase 3 final rerun failed a right steeping time for it. Otherwise
    plain containment, so a Korean word keeps its particles ("런던" is in
    "런던은").
    """
    phrase = str(phrase or "").casefold()
    if not phrase:
        return False
    before = r"(?<![\d.,])" if phrase[0].isdigit() else ""
    after = r"(?!\d)" if phrase[-1].isdigit() else ""
    return re.search(before + re.escape(phrase) + after, str(text or "").casefold()) is not None


# The same mathematics written another way. A pattern is written in symbols
# ("2x + 3", "1/3"); a right answer may say it in words or in LaTeX, and the
# Phase 3 final rerun failed both kinds: "The derivative is 2x plus 3" and
# "$ \frac{1}{3} $". A pattern matching either reading counts. This reads the
# reply; nothing that is scored is rewritten.
_LATEX_FRACTION = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SPOKEN_OPERATORS = (
    (re.compile(r"\s+plus\s+", re.IGNORECASE), " + "),
    (re.compile(r"\s+minus\s+", re.IGNORECASE), " - "),
    (re.compile(r"\s+times\s+", re.IGNORECASE), " * "),
    (re.compile(r"\s+divided\s+by\s+", re.IGNORECASE), " / "),
)


def math_reading(text: str) -> str:
    """The text with LaTeX fractions and spoken operators as symbols."""
    reading = _LATEX_FRACTION.sub(r"\1/\2", str(text or "")).replace("$", " ")
    for spoken, symbol in _SPOKEN_OPERATORS:
        reading = spoken.sub(symbol, reading)
    return reading


def _readings(display: str) -> tuple[str, ...]:
    reading = math_reading(display)
    return (display,) if reading == display else (display, reading)


def run(checks: dict, turn: TurnResult) -> list[CheckResult]:
    results: list[CheckResult] = []
    display = turn.display or ""
    readings = _readings(display)
    for name, value in checks.items():
        if name == "must_match":
            missing = [p for p in value if not any(_search(p, r) for r in readings)]
            results.append(CheckResult(name, not missing, f"missing {missing}" if missing else ""))
        elif name == "must_match_any":
            hit = any(_search(p, r) for p in value for r in readings)
            results.append(CheckResult(name, hit, "" if hit else f"none of {value}"))
        elif name == "must_not_match":
            found = [m.group(0) for p in value
                     if (m := next(filter(None, (_search(p, r) for r in readings)), None))]
            results.append(CheckResult(name, not found, f"found {found}" if found else ""))
        elif name == "must_contain_any":
            hit = any(contains(p, display) for p in value)
            results.append(CheckResult(name, hit, "" if hit else f"none of {value}"))
        elif name == "must_not_contain":
            found = [p for p in value if contains(p, display)]
            results.append(CheckResult(name, not found, f"found {found}" if found else ""))
        elif name == "max_words":
            count = len(words(display))
            results.append(CheckResult(name, count <= value, f"{count} words"))
        elif name == "min_words":
            count = len(words(display))
            results.append(CheckResult(name, count >= value, f"{count} words"))
        elif name == "language":
            spoken_in = language_of(display)
            results.append(CheckResult(name, spoken_in == value, f"reply is {spoken_in or 'unknown'}"))
        elif name == "answers_not_asks":
            # Asking back instead of answering: "What part is unclear?
            # Point to the specific bit." -- the Phase 2 27B reply to "I
            # still don't get it". Read as a reply that opens with a
            # question and is too short to have explained anything, or one
            # made only of questions. Answering and then asking is fine.
            said = [s for s in re.split(r"(?<=[.!?。？])\s+", display.strip()) if s.strip()]
            asks_first = bool(said) and said[0].rstrip().endswith(("?", "？"))
            only_questions = bool(said) and all(s.rstrip().endswith(("?", "？")) for s in said)
            short = len(words(display)) < 20
            asked_back = only_questions or (asks_first and short)
            results.append(CheckResult(name, not asked_back, "asked back instead of answering" if asked_back else ""))
        elif name == "not_searched":
            results.append(CheckResult(name, not turn.searched, "searched" if turn.searched else ""))
        elif name == "no_cards":
            results.append(CheckResult(name, not turn.cards, "cards shown" if turn.cards else ""))
        elif name == "not_repeat_previous":
            score = similarity(display, turn.previous_reply)
            results.append(CheckResult(name, score < 0.5, f"overlap {score:.2f}"))
        elif name == "speakable":
            found = unspeakable(turn.speech)
            results.append(CheckResult(name, not found, f"unspeakable {found}" if found else ""))
        elif name == "display_math_intact":
            lost = notation_lost(turn.draft, display)
            results.append(CheckResult(name, not lost, f"lost {lost[:6]}" if lost else ""))
        elif name in {"clock_times", "hour_difference", "utc_offset", "calendar", "quantity"}:
            results.append(_correctness(name, value, turn))
        elif name == "quantity_required_any":
            specs = checks.get("quantity", [])
            stated = any(stated_quantities(display, spec["unit"]) for spec in specs)
            results.append(CheckResult(name, stated or not value,
                                       "" if stated else "no quantity stated"))
        else:
            results.append(CheckResult(name, False, "unknown check"))
    return results


# Which checks establish that a stated value is right, for the report.
CORRECTNESS = frozenset({
    "must_match", "must_match_any", "clock_times", "hour_difference",
    "utc_offset", "calendar", "quantity", "quantity_required_any",
})
