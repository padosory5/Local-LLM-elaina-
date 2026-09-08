"""Who she is, in whichever language this turn is in.

One character, two renderings. The files are prose because prose is what a
person can edit, but they are not two independent documents: both carry the
same ``[SECTION]`` markers and the same number of rules under each, and
:func:`sections` exists so a test can prove it.

That test is the whole point of this module. Before it, ``personality_en.txt``
and ``personality_ko.txt`` had drifted into two different people -- Korean
described a close friend in 반말 with an examples section English did not
have, English had three sections Korean did not. Nobody decided that; it
happened one edit at a time, invisibly, because nothing compared them.
"""

from __future__ import annotations

import re
from pathlib import Path


# A section header. Deliberately ASCII in both files even though the prose
# under it is not: the marker is an identifier shared between two documents,
# and an identifier that needs translating cannot be compared.
_SECTION = re.compile(r"^\[([A-Z][A-Z ]*)\]\s*$")
_RULE = re.compile(r"^-\s+(.+)$")


class PersonalityLoader:

    def __init__(self):
        self.directory = Path(__file__).parent
        self._cache: dict[str, str] = {}

    def load(
        self,
        language: str,
    ) -> str:
        """The personality text for this language.

        Cached: this is now read per turn rather than once per process, and
        re-reading a file from disk to answer "hello" would be silly.
        """
        language = str(language or "").strip().lower()
        if language in self._cache:
            return self._cache[language]

        filename = f"personality_{language}.txt"

        path = self.directory / filename

        if not path.exists():
            raise FileNotFoundError(
                f"Personality file not found: {path}"
            )

        text = path.read_text(
            encoding="utf-8",
        ).strip()
        self._cache[language] = text
        return text

    def sections(self, language: str) -> dict[str, list[str]]:
        """The rules under each section marker, in order.

        A rule is a ``- `` bullet. Everything else -- the lead line, the
        example exchanges -- is content the parity test counts by line
        rather than by rule, which is why examples live under their own
        marker and carry no bullets.
        """
        found: dict[str, list[str]] = {}
        current = ""
        for line in self.load(language).splitlines():
            header = _SECTION.match(line.strip())
            if header:
                current = header.group(1)
                found.setdefault(current, [])
                continue
            if not current:
                continue
            rule = _RULE.match(line.strip())
            if rule:
                found[current].append(rule.group(1).strip())
        return found

    def example_count(self, language: str) -> int:
        """How many example exchanges the file demonstrates."""
        text = self.load(language)
        marker = "[EXAMPLES]"
        if marker not in text:
            return 0
        body = text.split(marker, 1)[1]
        return len([
            line for line in body.splitlines()
            if line.strip() and ":" in line
        ])
