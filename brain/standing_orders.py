"""Two files the person owns, that outlive every restart.

Nine dogfooding sessions produced a long tail of failures that are not
really bugs in the ordinary sense. She mishears one word the same way
every week. She does not know which university is *my* university until
somebody says so. She keeps having to be told the same thing.

Each of those was fixed with code -- a reader, a guard, a lifecycle -- and
each fix took a session to find and a session to validate. That is the
right way to fix a class of problem. It is a terrible way to fix *one
person's* long tail, because the tail is different for every person and
none of it can be tested in advance.

So: the person states the rule once, and it holds.

    runtime/data/directives.yaml   what to do differently, always
    runtime/data/about_me.yaml     what she knows about the person

Both are plain text, both are hers to edit in Notepad, and Elaina can
edit them when asked ("always make opennaver.com open naver.com").

Why this is not a list of past mistakes read back to the model
--------------------------------------------------------------

The obvious shape for this is a prose file of "things you got wrong
before, try not to repeat them", handed to the model each turn. This
project has nine sessions of evidence that qwen3:8b does not honour
prose instructions reliably -- the whole reason its guards are code and
not prompt wording. A growing list of don'ts would also grow the prompt,
and routing already costs 9-12 seconds.

So a directive is **executed, not read**. Each one is a typed rule that
deterministic code applies at a named boundary, and the file's prose is
for the person, not for the model. Two kinds, both grounded in what
actually went wrong:

``say``    a transcript repair, applied before anything reads the turn.
           "opennaver.com" -> "open naver.com". This is the one the
           project deliberately did not build in general -- a homophone
           table would break more than it fixed -- and is safe here for
           one reason: the person wrote it.

``note``   free prose, added to the answering context. Capped and few,
           because this is the weak mechanism and it should stay small.

``about_me`` is not a third kind. It is the handful of facts that must
never be missed -- always in context, never retrieved. The memory system
holds far more and searches it; this holds the few things that being
wrong about is unforgivable, and it is a complement to that rather than
a replacement for it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.paths import DATA_DIRECTORY, ensure_runtime_directories

DIRECTIVES_PATH = DATA_DIRECTORY / "directives.yaml"
ABOUT_ME_PATH = DATA_DIRECTORY / "about_me.yaml"

# Prose costs prompt and buys the least, so it is bounded. Rules are not
# bounded: they cost nothing at answer time because nothing reads them,
# code applies them.
MAX_NOTES = 12
MAX_FACTS = 40

_DIRECTIVES_HEADER = """\
# Elaina's standing instructions.
#
# You can edit this file by hand, or just tell her: "always make
# opennaver.com open naver.com". She writes it here and it holds from
# then on, including after a restart.
#
#   say:   when you say the first thing, she hears the second.
#          Applied before anything else reads the turn.
#   notes: anything else, in your own words. Kept short on purpose --
#          these are advice to the model rather than a rule, so they are
#          the weakest thing in this file.
"""

_ABOUT_ME_HEADER = """\
# What Elaina knows about you, in your words.
#
# Tell her "remember that ..." and it appears here. She reads all of it
# every session, so keep it to things that matter -- the memory system
# holds the rest and searches it.
"""


@dataclass(frozen=True)
class SaidAs:
    """One transcript repair the person asked for."""

    heard: str
    means: str

    def applies_to(self, text: str) -> bool:
        return bool(self.heard) and bool(
            re.search(re.escape(self.heard), str(text or ""), re.IGNORECASE)
        )

    def apply(self, text: str) -> str:
        return re.sub(
            re.escape(self.heard), self.means, str(text or ""),
            flags=re.IGNORECASE,
        )


@dataclass
class StandingOrders:
    """The person's own rules and facts, loaded once and written on change."""

    say: list[SaidAs] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    directives_path: Path = DIRECTIVES_PATH
    about_me_path: Path = ABOUT_ME_PATH

    # ------------------------------------------------------------- load

    @classmethod
    def load(
        cls, *, directives_path: Path | None = None,
        about_me_path: Path | None = None,
    ) -> "StandingOrders":
        """Read both files. A missing or broken one is simply empty.

        Never raises. These are hand-editable files, so a stray tab in one
        of them must cost a directive, not a startup.
        """
        directives_path = directives_path or DIRECTIVES_PATH
        about_me_path = about_me_path or ABOUT_ME_PATH
        orders = cls(
            directives_path=Path(directives_path),
            about_me_path=Path(about_me_path),
        )
        data = _read(orders.directives_path)
        for entry in data.get("say") or ():
            heard = str((entry or {}).get("heard", "")).strip()
            means = str((entry or {}).get("means", "")).strip()
            if heard and means and heard.casefold() != means.casefold():
                orders.say.append(SaidAs(heard=heard, means=means))
        orders.notes = [
            " ".join(str(note).split())
            for note in (data.get("notes") or ())
            if str(note).strip()
        ][:MAX_NOTES]

        about = _read(orders.about_me_path)
        orders.facts = [
            " ".join(str(fact).split())
            for fact in (about.get("facts") or ())
            if str(fact).strip()
        ][:MAX_FACTS]
        return orders

    # ------------------------------------------------------------ apply

    def heard_as(self, transcript: str) -> tuple[str, str]:
        """The turn with the person's own repairs applied, and which fired.

        Returns the text unchanged and an empty note when no rule matches,
        so the caller can stay silent rather than announcing nothing.
        """
        said = str(transcript or "")
        if not said.strip():
            return said, ""
        applied: list[str] = []
        for rule in self.say:
            if rule.applies_to(said):
                said = rule.apply(said)
                applied.append(f"{rule.heard!r} -> {rule.means!r}")
        return said, "; ".join(applied)

    def context_text(self) -> str:
        """What the answering prompt should carry, or nothing."""
        lines: list[str] = []
        if self.facts:
            lines.append("What you know about them:")
            lines.extend(f"- {fact}" for fact in self.facts)
        if self.notes:
            lines.append("Standing instructions from them:")
            lines.extend(f"- {note}" for note in self.notes)
        return "\n".join(lines)

    def log_block(self) -> str:
        return (
            f"[Standing Orders] {len(self.say)} repair(s), "
            f"{len(self.notes)} note(s), {len(self.facts)} fact(s)."
        )

    # ------------------------------------------------------------ write

    def remember_repair(self, heard: str, means: str) -> bool:
        """Record "when I say X I mean Y", replacing any rule for the same X."""
        heard = " ".join(str(heard or "").split())
        means = " ".join(str(means or "").split())
        if not heard or not means or heard.casefold() == means.casefold():
            return False
        self.say = [
            rule for rule in self.say
            if rule.heard.casefold() != heard.casefold()
        ]
        self.say.append(SaidAs(heard=heard, means=means))
        self._write_directives()
        return True

    def remember_note(self, note: str) -> bool:
        note = " ".join(str(note or "").split())
        if not note or note in self.notes:
            return False
        self.notes = (self.notes + [note])[-MAX_NOTES:]
        self._write_directives()
        return True

    def remember_fact(self, fact: str) -> bool:
        """Record something about the person, so a restart does not lose it."""
        fact = " ".join(str(fact or "").split())
        if not fact:
            return False
        # The same thing said twice is one fact, and a restated one is the
        # newer wording of it.
        lowered = fact.casefold()
        self.facts = [
            held for held in self.facts if held.casefold() != lowered
        ]
        self.facts = (self.facts + [fact])[-MAX_FACTS:]
        self._write_about_me()
        return True

    def forget(self, phrase: str) -> tuple[int, int]:
        """Drop every rule and fact mentioning this, and say how many."""
        phrase = " ".join(str(phrase or "").split()).casefold()
        if not phrase:
            return 0, 0
        rules = len(self.say) + len(self.notes)
        self.say = [
            rule for rule in self.say
            if phrase not in f"{rule.heard} {rule.means}".casefold()
        ]
        self.notes = [note for note in self.notes if phrase not in note.casefold()]
        facts = len(self.facts)
        self.facts = [fact for fact in self.facts if phrase not in fact.casefold()]
        dropped_rules = rules - len(self.say) - len(self.notes)
        dropped_facts = facts - len(self.facts)
        if dropped_rules:
            self._write_directives()
        if dropped_facts:
            self._write_about_me()
        return dropped_rules, dropped_facts

    def _write_directives(self) -> None:
        _write(
            self.directives_path, _DIRECTIVES_HEADER,
            {
                "say": [
                    {"heard": rule.heard, "means": rule.means}
                    for rule in self.say
                ],
                "notes": list(self.notes),
            },
        )

    def _write_about_me(self) -> None:
        _write(
            self.about_me_path, _ABOUT_ME_HEADER,
            {"facts": list(self.facts), "updated": _today()},
        )


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _read(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        print(f"[Standing Orders] {Path(path).name} could not be read: {error}")
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, header: str, data: dict) -> None:
    try:
        ensure_runtime_directories()
        body = yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False,
        )
        Path(path).write_text(f"{header}\n{body}", encoding="utf-8")
    except OSError as error:
        print(f"[Standing Orders] could not save {Path(path).name}: {error}")


# --------------------------------------------------------------- spoken
#
# The forms a person actually uses to state one of these. Deliberately
# explicit: a standing rule is a commitment, so it is made on purpose and
# never inferred from an ordinary correction. "Only one S" fixes this
# address; "always open naver.com when I say opennaver.com" fixes every
# future one.

_ALWAYS = r"(?:always|from\s+now\s+on|whenever|every\s+time)"

_A_REPAIR = (
    # "always make opennaver.com open naver.com"
    re.compile(
        rf"^\s*(?:{_ALWAYS})[,\s]+(?:you\s+should\s+)?"
        r"(?:make|treat|read|take)\s+"
        r"(?P<heard>.+?)\s+"
        r"(?:as|to\s+be|to\s+open|to\s+mean|open|mean)\s+"
        r"(?P<means>.+?)\s*[.!]?\s*$",
        re.IGNORECASE,
    ),
    # "when I say X I mean Y" / "if I say X, open Y"
    re.compile(
        rf"^\s*(?:{_ALWAYS}[,\s]+)?(?:when|if|whenever)\s+i\s+say\s+"
        r"(?P<heard>.+?)[,\s]+"
        r"(?:i\s+)?(?:mean|means|meant|open|go\s+to)\s+"
        r"(?P<means>.+?)\s*[.!]?\s*$",
        re.IGNORECASE,
    ),
)

_A_FACT = re.compile(
    r"^\s*(?:please\s+)?remember\s+(?:that\s+)?(?P<fact>.+?)\s*[.!]?\s*$",
    re.IGNORECASE,
)

_A_NOTE = re.compile(
    rf"^\s*(?:{_ALWAYS})[,\s]+(?P<note>.+?)\s*[.!]?\s*$",
    re.IGNORECASE,
)

_FORGET = re.compile(
    r"^\s*(?:please\s+)?(?:forget|stop|drop|remove|delete)\s+"
    r"(?:about\s+|the\s+rule\s+(?:about\s+)?|that\s+)?"
    r"(?P<phrase>.+?)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def read_instruction(text: str) -> tuple[str, str, str]:
    """What standing instruction this turn gives, if any.

    Returns ``(kind, first, second)`` where kind is one of ``repair``,
    ``fact``, ``note``, ``forget`` -- or three empty strings.
    """
    said = " ".join(str(text or "").split())
    if not said:
        return "", "", ""

    for pattern in _A_REPAIR:
        match = pattern.match(said)
        if match:
            heard = match.group("heard").strip(" \"'`")
            means = match.group("means").strip(" \"'`")
            if heard and means and heard.casefold() != means.casefold():
                return "repair", heard, means

    fact = _A_FACT.match(said)
    if fact:
        return "fact", fact.group("fact").strip(), ""

    forget = _FORGET.match(said)
    if forget:
        phrase = forget.group("phrase").strip()
        # "stop it" and "forget it" are about the moment, not the file.
        if phrase.casefold() not in {
            "it", "that", "this", "everything", "all of it",
        }:
            return "forget", phrase, ""

    note = _A_NOTE.match(said)
    if note:
        return "note", note.group("note").strip(), ""
    return "", "", ""
