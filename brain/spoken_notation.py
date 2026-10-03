"""Notation said aloud: what a person reading the screen would say.

Part of speech realization (brain/realize.py). The screen keeps
``f'(a)(x − a)``; the voice is given "f prime of a times x minus a". The
two used to be one string, and the speech rules ran on it -- which is how
``(x - a)`` was *shown* as ``(x, a)`` (docs/PHASE3_PLAN.md §1.2).

What this is, and what it is not:

* A closed operator grammar -- the arithmetic and comparison operators,
  powers, roots, factorials, primes, function application, integrals and
  sums, ranges, degrees and percentages -- in English and Korean. Each
  operator is read from the tokens beside it, so a hyphen inside a word,
  "and/or", "24/7", a date and an exclamation stay prose.
* Not a pronunciation table. Words the voice already says correctly are
  left alone; the handful of unit and abbreviation readings below are the
  ones whose written form is not what is said ("km/h", "e.g.").
* Deterministic, no model call. Anything it cannot read -- an unknown LaTeX
  command -- is said as a reference to the screen rather than guessed at.
"""

from __future__ import annotations

import re

LANGUAGES = ("en", "ko")

# ----------------------------------------------------------------- readings

_OPERATORS = {
    "en": {
        "+": "plus", "−": "minus", "-": "minus", "×": "times", "·": "times",
        "*": "times", "÷": "divided by", "/": "over", "=": "equals",
        "≈": "is approximately", "≠": "is not equal to",
        "≤": "is less than or equal to", "≥": "is greater than or equal to",
        "<": "is less than", ">": "is greater than", "±": "plus or minus",
        "range": "to",
    },
    "ko": {
        "+": "더하기", "−": "빼기", "-": "빼기", "×": "곱하기", "·": "곱하기",
        "*": "곱하기", "÷": "나누기", "/": "나누기", "=": "는",
        "≈": "는 약", "≠": "낫 이퀄", "≤": "는 작거나 같다", "≥": "는 크거나 같다",
        "<": "는 작다", ">": "는 크다", "±": "플러스 마이너스",
        "range": "에서",
    },
}

_WORDS = {
    "en": {
        "sqrt": "the square root of", "pi": "pi", "infinity": "infinity",
        "sum": "the sum of", "integral": "the integral", "of": "of",
        "from": "from", "to": "to", "factorial": "factorial",
        "prime": "prime", "double prime": "double prime",
        "and so on": "and so on", "degrees": "degrees", "percent": "percent",
        "all": "all", "d by d": "d by d", "minus": "minus",
        "unknown": "the formula shown on screen",
    },
    "ko": {
        "sqrt": "루트", "pi": "파이", "infinity": "무한대", "sum": "시그마",
        "integral": "적분", "of": "", "from": "", "to": "부터",
        "factorial": "팩토리얼", "prime": "프라임", "double prime": "더블 프라임",
        "and so on": "등", "degrees": "도", "percent": "퍼센트", "all": "",
        "d by d": "디 디", "minus": "마이너스",
        "unknown": "화면에 보이는 식",
    },
}

_FUNCTIONS = {
    "en": {"sin": "sine", "cos": "cosine", "tan": "tangent", "log": "log",
           "ln": "natural log", "exp": "exp", "sqrt": "the square root",
           "arcsin": "arc sine", "arccos": "arc cosine", "arctan": "arc tangent",
           "max": "max", "min": "min", "lim": "the limit"},
    "ko": {"sin": "사인", "cos": "코사인", "tan": "탄젠트", "log": "로그",
           "ln": "자연로그", "exp": "exp", "sqrt": "루트",
           "arcsin": "아크사인", "arccos": "아크코사인", "arctan": "아크탄젠트",
           "max": "맥스", "min": "민", "lim": "리밋"},
}

_ORDINALS = {4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
             9: "ninth", 10: "tenth"}

_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁺⁻", "0123456789n+-")
_SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉ₙ", "0123456789n")

# Units read from a number. Only the ones whose written form is not what
# is said; "5 cups" needs nothing.
_UNITS = {
    "en": {
        "km/h": "kilometers per hour", "m/s": "meters per second",
        "mph": "miles per hour", "GHz": "gigahertz", "MHz": "megahertz",
        "kHz": "kilohertz", "Hz": "hertz", "km": "kilometers",
        "cm": "centimeters", "mm": "millimeters", "kg": "kilograms",
        "mg": "milligrams", "lb": "pounds", "lbs": "pounds", "oz": "ounces",
        "ft": "feet", "ml": "milliliters", "mL": "milliliters",
        "GB": "gigabytes", "MB": "megabytes", "TB": "terabytes",
    },
    "ko": {
        "km/h": "킬로미터 매시", "m/s": "미터 매초", "km": "킬로미터",
        "cm": "센티미터", "mm": "밀리미터", "kg": "킬로그램",
        "GHz": "기가헤르츠", "MHz": "메가헤르츠", "GB": "기가바이트",
        "MB": "메가바이트", "TB": "테라바이트", "ml": "밀리리터", "mL": "밀리리터",
    },
}

_ABBREVIATIONS = {
    "en": (
        (re.compile(r"\be\.g\.,?", re.IGNORECASE), "for example,"),
        (re.compile(r"\bi\.e\.,?", re.IGNORECASE), "that is,"),
        (re.compile(r"\betc\.", re.IGNORECASE), "and so on."),
        (re.compile(r"\bvs\.?(?=\s)", re.IGNORECASE), "versus"),
        (re.compile(r"\bapprox\.", re.IGNORECASE), "approximately"),
    ),
    "ko": (),
}

# ------------------------------------------------------------------ LaTeX

_LATEX_SIMPLE = {
    r"\cdot": "×", r"\times": "×", r"\div": "÷", r"\pm": "±", r"\le": "≤",
    r"\leq": "≤", r"\ge": "≥", r"\geq": "≥", r"\ne": "≠", r"\neq": "≠",
    r"\approx": "≈", r"\infty": "∞", r"\pi": "π", r"\cdots": "…",
    r"\ldots": "…", r"\dots": "…", r"\sum": "∑", r"\int": "∫",
    r"\left": "", r"\right": "", r"\,": " ", r"\;": " ", r"\quad": " ",
}
_LATEX_FUNCTIONS = ("sin", "cos", "tan", "log", "ln", "exp", "max", "min", "lim")


def _from_latex(span: str) -> tuple[str, bool]:
    """LaTeX to the same plain notation the model writes; False if unknown."""
    text = span
    # A sum or an integral with its bounds reads "from ... to ... of".
    text = re.sub(
        r"\\(sum|int|prod)_\{([^{}]*)\}\^\{([^{}]*)\}",
        lambda m: f" \\{m.group(1)}from {m.group(2)} to {m.group(3)} of ",
        text,
    )
    # A parenthesised order on a function is a derivative, not a power:
    # f^{(n)}(a) is the n-th derivative of f at a.
    text = re.sub(
        r"([A-Za-z])\^\{\(([^{}()]+)\)\}\(([^()]+)\)",
        r" the \2-th derivative of \1 at \3 ",
        text,
    )
    # Innermost braces first, so a fraction holding a power still resolves:
    # \frac{f^{(n)}(a)}{n!}.
    for _ in range(6):
        text = re.sub(r"\^\{([^{}]*)\}", r"^(\1)", text)
        text = re.sub(r"_\{([^{}]*)\}", lambda m: m.group(1).translate(
            str.maketrans("0123456789n", "₀₁₂₃₄₅₆₇₈₉ₙ")), text)
        text = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", text)
        text = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", text)
    text = re.sub(r"\\sumfrom ", " the sum from ", text)
    text = re.sub(r"\\intfrom ", " the integral from ", text)
    text = re.sub(r"\\prodfrom ", " the product from ", text)
    for name in _LATEX_FUNCTIONS:
        text = text.replace("\\" + name, name)
    for command in sorted(_LATEX_SIMPLE, key=len, reverse=True):
        text = text.replace(command, _LATEX_SIMPLE[command])
    known = not re.search(r"\\[A-Za-z]+", text)
    text = text.replace("{", "").replace("}", "")
    return " ".join(text.split()), known


def _latex_spans(text: str, language: str) -> str:
    def replace(match: re.Match) -> str:
        plain, known = _from_latex(match.group(1) or match.group(2) or "")
        if not known:
            return f" {_WORDS[language]['unknown']} "
        return f" {plain} "

    def maybe(match: re.Match) -> str:
        body = match.group(1) or match.group(2) or ""
        # "$100-$200" is money. LaTeX carries a command, a power or a
        # subscript.
        if not re.search(r"\\[A-Za-z]|[\^_]", body):
            return match.group(0)
        return replace(match)

    return re.sub(r"\$\$(.+?)\$\$|\$([^$\n]+)\$", maybe, text, flags=re.DOTALL)


# ---------------------------------------------------------------- tokens

_TOKEN = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"|(?P<word>[A-Za-z]+)"
    r"|(?P<sup>[⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁺⁻]+)"
    r"|(?P<sub>[₀₁₂₃₄₅₆₇₈₉ₙ]+)"
    r"|(?P<space>\s+)"
    r"|(?P<ellipsis>\.\.\.|…|⋯)"
    r"|(?P<sym>.)",
    re.DOTALL,
)

# Operators that are notation wherever they appear.
_ALWAYS_MATH = set("×÷≈≠≤≥±√∑∫π∞^=·−")


class _Token:
    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str) -> None:
        self.kind = kind
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.kind}:{self.text!r}"


def _tokens(text: str) -> list[_Token]:
    return [_Token(match.lastgroup, match.group(0)) for match in _TOKEN.finditer(text)]


def _is_variable(token: _Token | None) -> bool:
    return token is not None and token.kind == "word" and len(token.text) == 1


def _is_function(token: _Token | None, language: str) -> bool:
    return token is not None and token.kind == "word" and token.text in _FUNCTIONS["en"]


def _is_operand(token: _Token | None) -> bool:
    if token is None:
        return False
    if token.kind in {"num", "sup", "sub"}:
        return True
    if _is_variable(token):
        return True
    return token.text in {")", "π", "∞", "!", "'"}


def _neighbours(tokens: list[_Token], index: int):
    """The nearest non-space token on each side, and whether space separates them."""
    left = index - 1
    while left >= 0 and tokens[left].kind == "space":
        left -= 1
    right = index + 1
    while right < len(tokens) and tokens[right].kind == "space":
        right += 1
    before = tokens[left] if left >= 0 else None
    after = tokens[right] if right < len(tokens) else None
    spaced_before = left != index - 1
    spaced_after = right != index + 1
    return before, after, spaced_before, spaced_after, left, right


def _math_nearby(tokens: list[_Token], index: int, reach: int = 6) -> bool:
    """Whether an unambiguous operator is within a few tokens: a span of notation."""
    low, high = max(0, index - reach), min(len(tokens), index + reach + 1)
    for position in range(low, high):
        token = tokens[position]
        if position != index and (
            token.text in _ALWAYS_MATH or token.kind in {"sup", "sub"}
            or token.text in "+"
        ):
            return True
    return False


# ------------------------------------------------------------------ reading


def _power(exponent: str, language: str) -> str:
    """A power, from its plain exponent text."""
    exponent = exponent.strip()
    if language == "ko":
        if exponent == "2":
            return "제곱"
        if exponent == "3":
            return "세제곱"
        return f"의 {exponent} 제곱"
    if exponent == "2":
        return "squared"
    if exponent == "3":
        return "cubed"
    if exponent.isdigit() and int(exponent) in _ORDINALS:
        return f"to the {_ORDINALS[int(exponent)]}"
    if exponent == "-1":
        return "to the minus 1"
    return f"to the power of {exponent}"


def _is_fraction(tokens: list[_Token], index: int, before, after, left, right) -> bool:
    """A number over a number reads as a fraction, not a date or "24/7"."""
    if not (before and after and before.kind == "num" and after.kind == "num"):
        return False
    # Part of a date or a longer run: 9/25/2026, 1/2/3.
    if (left - 1 >= 0 and tokens[left - 1].text == "/") or (
        right + 1 < len(tokens) and tokens[right + 1].text == "/"
    ):
        return False
    if _math_nearby(tokens, index):
        return True
    try:
        top, bottom = float(before.text.replace(",", "")), float(after.text.replace(",", ""))
    except ValueError:
        return False
    return 0 < top < bottom <= 16


def _korean_fraction(numerator: str, denominator: str) -> str:
    return f"{denominator}분의 {numerator}"


def verbalize(text: str, language: str = "en") -> str:
    """The text with its notation said in words; prose passes through."""
    said = str(text or "")
    if not said.strip():
        return said
    language = "ko" if str(language).lower().startswith("ko") else "en"
    words = _WORDS[language]
    ops = _OPERATORS[language]

    said = _latex_spans(said, language)
    for pattern, reading in _ABBREVIATIONS[language]:
        said = pattern.sub(reading, said)
    # Degrees and percentages after a number.
    said = re.sub(
        r"(\d)\s*°\s*([CF])\b",
        lambda m: (f"{m.group(1)} degrees {'Celsius' if m.group(2) == 'C' else 'Fahrenheit'}"
                   if language == "en" else
                   f"{'섭씨' if m.group(2) == 'C' else '화씨'} {m.group(1)}도"),
        said,
    )
    said = re.sub(r"(\d)\s*°", rf"\1 {words['degrees']}", said)
    said = re.sub(r"(\d)\s*%", rf"\1 {words['percent']}", said)
    # Units after a number, longest first so "km/h" is not read as "km".
    for unit in sorted(_UNITS[language], key=len, reverse=True):
        # After a digit, including a power's: "3 × 10⁸ m/s".
        said = re.sub(
            rf"([\d⁰¹²³⁴⁵⁶⁷⁸⁹])\s*{re.escape(unit)}(?![A-Za-z/])",
            rf"\1 {_UNITS[language][unit]}", said,
        )
    # d/dx: the derivative operator, read as written.
    said = re.sub(
        r"\bd/d([a-z])\b",
        lambda m: f"{words['d by d']} {m.group(1)} {words['of']}".rstrip(), said,
    )

    tokens = _tokens(said)
    out: list[str] = []
    # Parentheses opened by function application, which are said as "of"
    # and closed silently.
    index = 0
    while index < len(tokens):
        token = tokens[index]
        text_ = token.text
        before, after, spaced_before, spaced_after, left, right = _neighbours(tokens, index)

        if token.kind == "sup":
            plain = text_.translate(_SUPERSCRIPT)
            if before is not None and before.text == ")" and language == "en":
                out.append(f" {words['all']}")
            out.append(f" {_power(plain, language)}")
            index += 1
            continue
        if token.kind == "sub":
            if before is not None and before.text == "∫":
                index += 1
                continue  # read with the integral
            out.append(f" {text_.translate(_SUBSCRIPT)}")
            index += 1
            continue
        if token.kind == "word" and after is not None and after.text == "(" and not spaced_after and (
            _is_variable(token) or _is_function(token, language)
        ):
            name = _FUNCTIONS[language].get(text_, text_) if len(text_) > 1 else text_
            out.append(f"{name} {words['of']}".rstrip() + " ")
            # Drop the opening parenthesis; its partner is dropped as usual.
            index = right + 1
            continue
        primes = 0
        while text_ == "'" and index + primes < len(tokens) and tokens[index + primes].text == "'":
            primes += 1
        following = tokens[index + primes] if text_ == "'" and index + primes < len(tokens) else None
        # A prime, not an apostrophe: "f'(a)", "f' = 2x", "y''". "I'm" and
        # "don't" have a letter straight after it.
        if text_ == "'" and _is_variable(before) and not spaced_before and not (
            following is not None and following.kind in {"word", "num"}
        ):
            out.append(f" {words['double prime' if primes >= 2 else 'prime']}")
            index += primes
            if index < len(tokens) and tokens[index].text == "(":
                out.append(f" {words['of']} " if words["of"] else " ")
                index += 1
            continue
        if text_ == "(":
            # Juxtaposition: ")(" and "2(" multiply.
            if before is not None and before.text in {")"} and not spaced_before:
                out.append(f" {ops['×']} ")
            index += 1
            continue
        if text_ == ")":
            index += 1
            continue
        if text_ == "^":
            exponent = ""
            cursor = right
            if after is not None and after.text == "(":
                depth, cursor = 0, right
                parts = []
                while cursor < len(tokens):
                    part = tokens[cursor].text
                    if part == "(":
                        depth += 1
                        if depth == 1:
                            cursor += 1
                            continue
                    elif part == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    parts.append(part)
                    cursor += 1
                exponent = verbalize("".join(parts), language).strip()
            elif after is not None:
                exponent = after.text
            out.append(f" {_power(exponent, language)}")
            index = cursor + 1
            continue
        if text_ == "√":
            out.append(f" {words['sqrt']} ")
            index += 1
            continue
        if text_ in {"∑", "Σ"}:
            out.append(f" {words['sum']} ")
            index += 1
            continue
        if text_ == "∫":
            lower = upper = ""
            cursor = index + 1
            if cursor < len(tokens) and tokens[cursor].kind == "sub":
                lower = tokens[cursor].text.translate(_SUBSCRIPT)
                cursor += 1
            if cursor < len(tokens) and tokens[cursor].kind == "sup":
                upper = tokens[cursor].text.translate(_SUPERSCRIPT)
                cursor += 1
            if language == "ko":
                bounds = f" {lower}부터 {upper}까지" if lower or upper else ""
                out.append(f"{bounds} {words['integral']} ")
            else:
                bounds = f" {words['from']} {lower} {words['to']} {upper}" if lower or upper else ""
                out.append(f" {words['integral']}{bounds} {words['of']} ")
            index = cursor
            continue
        if text_ == "π":
            out.append(f" {words['pi']} ")
            index += 1
            continue
        if text_ == "∞":
            out.append(f" {words['infinity']} ")
            index += 1
            continue
        if token.kind == "ellipsis" and _math_nearby(tokens, index):
            # "+ ..." is "and so on", not "plus and so on".
            while out and not out[-1].strip():
                out.pop()
            if out and out[-1].strip() in {ops["+"], ops["-"]}:
                out.pop()
            out.append(f" {words['and so on']} ")
            index += 1
            continue
        if text_ == "!" and _is_operand(before) and before.text != "!" and not spaced_before and (
            (before.kind == "word" and before.text.islower() and len(before.text) == 1)
            or before.text == ")"
            or (after is not None and (after.text in set("+−-×÷/=)·*") or after.text == "!"))
            or _math_nearby(tokens, index, reach=3)
        ):
            out.append(f" {words['factorial']}")
            index += 1
            continue
        if text_ in {"-", "–", "—", "−"}:
            numbers = before is not None and after is not None and before.kind == after.kind == "num"
            chained = (
                (left - 1 >= 0 and tokens[left - 1].text in {"-", "–"})
                or (right + 1 < len(tokens) and tokens[right + 1].text in {"-", "–"})
            )
            if text_ == "—" or chained:
                out.append(text_)  # prose dash or a date: speech prose rules
                index += 1
                continue
            if numbers and not spaced_before and not spaced_after and text_ != "−" and not _math_nearby(tokens, index):
                out.append(f" {ops['range']} " if language == "en" else f"{ops['range']} ")
                index += 1
                continue
            binary = _is_operand(before) and (_is_operand(after) or (after is not None and after.text in {"(", "√"}))
            math_sides = binary and (
                text_ == "−"
                or (spaced_before and spaced_after)
                or _is_variable(before) or _is_variable(after)
                or (before.kind in {"sup", "num"} or before.text in {")", "!"})
                and not (before.kind == "num" and after.kind == "num" and not _math_nearby(tokens, index))
            )
            unary = (
                not _is_operand(before)
                and after is not None and (after.kind == "num" or _is_variable(after))
                and not spaced_after
                and (
                    # -5 in a sentence is a negative number.
                    (after.kind == "num" and spaced_before
                     and (before is None or before.kind == "word"
                          or before.text in set("=(+×÷≈≤≥<>,:")))
                    or (
                        (before is None or before.text in set("=(+×÷≈≤≥<>,") or text_ == "−")
                        and (_math_nearby(tokens, index) or text_ == "−")
                    )
                )
            )
            if math_sides:
                out.append(f" {ops['-']} ")
            elif unary:
                out.append(f" {words['minus']} ")
            else:
                out.append(text_)
            index += 1
            continue
        if text_ == "/":
            if _is_fraction(tokens, index, before, after, left, right):
                if language == "ko":
                    # Said denominator first: "8분의 5".
                    while out and not out[-1].strip():
                        out.pop()
                    if out and out[-1].strip() == before.text:
                        out.pop()
                    out.append(f" {_korean_fraction(before.text, after.text)} ")
                    index = right + 1
                    continue
                out.append(f" {ops['/']} ")
                index += 1
                continue
            both_words = (
                before is not None and after is not None
                and before.kind == "word" and after.kind == "word"
                and len(before.text) > 1 and len(after.text) > 1
            )
            if _is_operand(before) and (_is_operand(after) or (after is not None and after.text in {"(", "√"})) and not both_words and not (
                before.kind == "num" and after is not None and after.kind == "num"
            ):
                out.append(f" {ops['/']} ")
            else:
                out.append(text_)
            index += 1
            continue
        if text_ in {"*"} and spaced_before == spaced_after and _is_operand(before) and _is_operand(after):
            out.append(f" {ops['*']} ")
            index += 1
            continue
        if text_ in {"<", ">"}:
            if _is_operand(before) and _is_operand(after):
                out.append(f" {ops[text_]} ")
            else:
                out.append(text_)
            index += 1
            continue
        if text_ in ops and text_ not in {"-", "/", "*", "<", ">", "range"}:
            out.append(f" {ops[text_]} ")
            index += 1
            continue
        if token.kind == "num" and after is not None and not spaced_after and (
            after.text == "π" or (_is_variable(after) and _math_nearby(tokens, index))
        ):
            # Implicit multiplication: "2πr", "2x". Said with a space.
            out.append(f"{text_} ")
            index += 1
            continue
        if text_ == "π" or (token.kind == "word" and text_ == "pi"):
            out.append(text_)
            index += 1
            continue
        out.append(text_)
        index += 1

    result = "".join(out)
    result = re.sub(r"[ \t]{2,}", " ", result)
    if language == "ko":
        # 의 is a particle: it attaches to the word before it.
        result = re.sub(r"\s+의(?=\s)", "의", result)
    result = re.sub(r" +([,.;:!?])", r"\1", result)
    return result.strip()


# ---------------------------------------------------------- notation spans
#
# Where the text is mathematics rather than prose. The value validator
# (brain/grounded_values.py) reads claims -- a price, a phone number, a
# measured attribute -- and a number inside an expression is none of those:
# it is an operand. Measured in the Phase 3 rerun, "$ 1 + x + \frac{x^2}{2}
# ... $" was read as a one-dollar price, found in no evidence, and the
# sentence explaining a Taylor series was deleted.

_DELIMITED = re.compile(r"\$\$(.+?)\$\$|\$([^$\n]+)\$", re.DOTALL)
# Inside a $...$ pair: a LaTeX command, a power, a subscript, an equation, or
# a single-letter variable. "$100-$200" has none of them: that is money.
_MATH_INSIDE = re.compile(r"\\[A-Za-z]|[\^_=]|(?<![A-Za-z])[A-Za-z](?![A-Za-z])")
_OPERAND = r"(?:(?<![A-Za-z])[A-Za-z](?![A-Za-z])|\d+(?:\.\d+)?|[(√π∞])[\w²³⁴⁵⁶⁷⁸⁹ⁿ₀₁₂₃₄₅₆₇₈₉!'′().√π∞^]*"
_EXPRESSION = re.compile(
    _OPERAND + r"(?:[ \t]*[+\-−×÷*/=^≈≤≥<>±][ \t]*" + _OPERAND + r")+"
)
_VARIABLE = re.compile(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])")


def notation_spans(text: str) -> list[tuple[int, int]]:
    """The (start, end) spans of this text that are mathematical notation.

    Two shapes, by the grammar of notation rather than by what any reply
    said: a delimited $...$ span with mathematics inside it, and an
    expression whose operators join operands and which has a variable or is
    an equation. "5 + 3 dollars" and "$100-$200" are prose.
    """
    text = str(text or "")
    spans: list[tuple[int, int]] = []
    for match in _DELIMITED.finditer(text):
        body = match.group(1) or match.group(2) or ""
        if _MATH_INSIDE.search(body):
            spans.append(match.span())
    for match in _EXPRESSION.finditer(text):
        piece = match.group(0)
        if _VARIABLE.search(piece) or "=" in piece or "^" in piece:
            spans.append(match.span())
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# The same closed vocabulary the reading above speaks with, read the other
# way: the words that make a letter next to them a variable. Arithmetic
# operators, powers and functions only -- the comparison readings ("is less
# than", "is approximately") are ordinary words in a sentence.
_SPOKEN_MATH = {
    word
    for language in LANGUAGES
    for symbol in ("+", "−", "×", "÷", "/")
    for word in _OPERATORS[language][symbol].split()
    if word != "by"
} | {"squared", "cubed", "제곱", "세제곱"} | set(_FUNCTIONS["en"])
_SYMBOL_MATH = set("+-−×÷*/^=()²³")
# Single letters that are words: next to a word they are the article and
# the pronoun ("up by a third", "a few times"), never a variable.
_LETTER_WORDS = {"a", "A", "I"}


def names_a_variable(text: str) -> bool:
    """Whether the text uses a letter as a mathematical variable.

    Read with this module's own tokens and vocabulary: a single letter
    written against a number ("3x"), next to an operator or a power
    ("x^2", "x²", "f(x)", "x + 1"), or next to a word this module says
    operators and powers with ("x squared plus 3x", "sin x", "x 제곱").
    "Plan A costs $50", "I need 3 cups" and "a 15% tip" use letters as
    words, not variables.

    A question about a variable asks for an expression -- a derivative, a
    formula -- not for a number, which is what brain/calculation_planner.py
    needs to know before it calls a numbers-only calculator.
    """
    tokens = _tokens(str(text or ""))
    for index, token in enumerate(tokens):
        if not _is_variable(token):
            continue
        glued_before = index > 0 and tokens[index - 1].kind == "num"
        before, after, *_ = _neighbours(tokens, index)
        for neighbour in (before, after):
            if neighbour is None:
                continue
            if neighbour.kind == "sup" or neighbour.text in _SYMBOL_MATH:
                return True
            if (neighbour.kind == "word" and token.text not in _LETTER_WORDS
                    and neighbour.text.casefold() in _SPOKEN_MATH):
                return True
        if glued_before:
            return True
        # Korean follows the letter with a Hangul word, which the tokens
        # read one syllable at a time.
        rest = "".join(t.text for t in tokens[index + 1:index + 8]).lstrip()
        if token.text not in _LETTER_WORDS and any(
            rest.startswith(word) for word in _SPOKEN_MATH if not word.isascii()
        ):
            return True
    return False


def prose(text: str) -> str:
    """The text with its notation blanked out, same length, prose kept."""
    text = str(text or "")
    spans = notation_spans(text)
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            if chars[index] not in "\n":
                chars[index] = " "
    return "".join(chars)

