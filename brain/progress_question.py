"""Asking how it is going is not asking for something new.

B-46, from session 2:

    User:   Why is it taking so long?
    Elaina: What would you like me to do next?

Nothing was running -- the lookup she had offered was never started,
because the "Yeah" that accepted it took a fast path (B-45). So the honest
answer was "nothing is running", and instead the question was read as a
fresh request for work.

The information needed to answer is already held: whether a turn is in
flight, whether an offer is parked and unanswered, or neither. This is
only the reading of the question; the engine supplies the state.
"""

from __future__ import annotations

import re

_ASKS_ABOUT_PROGRESS = re.compile(
    r"\bwhy\s+(?:is|it'?s)\b[^?]{0,30}\btaking\b"
    r"|\b(?:are|r)\s+you\s+(?:still\s+)?(?:doing|working\s+on|looking|"
    r"searching|on)\s+(?:it|that|this)\b"
    r"|\bis\s+it\s+(?:done|ready|finished|working)\b"
    r"|\bwhat'?s\s+(?:happening|going\s+on|taking\s+so\s+long)\b"
    r"|\bdid\s+you\s+(?:find|get)\s+(?:anything|it|them)\s*(?:yet)?\b"
    r"|\bany\s+(?:luck|progress)\b"
    r"|\bhow'?s\s+(?:it|that)\s+going\b"
    r"|아직(?:이야|이니|인가)|어떻게\s*돼\s*가",
    re.IGNORECASE,
)


def asks_about_progress(text: str) -> bool:
    """Whether this turn asks how the work in hand is going."""
    return bool(_ASKS_ABOUT_PROGRESS.search(str(text or "")))
