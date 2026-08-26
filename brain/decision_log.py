"""One structured log line for an information-acquisition decision.

Answers "why did Elaina use search instead of browser?" / "why did she
decide to verify this?" from the console alone, without dumping the
model's own reasoning -- just the final decision fields, matching this
codebase's existing debug-print conventions (e.g. chat_engine.py's
"[Router Source] freshness=... external=... verify=..." and
task_planner.py's "[Task Planner] status=..."). A turn that never reaches
an information-acquisition decision point (plain conversation, local
knowledge) simply never prints this line -- that absence is itself the
"answered directly, no acquisition needed" case, so no separate value is
needed to represent it.
"""

from __future__ import annotations

from typing import Any, Iterable


def log_information_need(
    *,
    intent: str = "",
    freshness: str = "",
    verification: bool | str | None = None,
    effort: str = "",
    capabilities: Iterable[Any] = (),
    sources: Iterable[Any] = (),
    confidence: float | None = None,
) -> None:
    capability_text = ",".join(str(item) for item in capabilities) or "(none)"
    source_text = ",".join(str(item) for item in sources) or "(none)"
    confidence_text = "(none)" if confidence is None else f"{confidence:.2f}"
    print(
        "[Information Need] "
        f"intent={intent or '(none)'} "
        f"freshness={freshness or '(none)'} "
        f"verification={verification if verification is not None else '(none)'} "
        f"effort={effort or '(none)'} "
        f"capabilities=[{capability_text}] "
        f"sources=[{source_text}] "
        f"confidence={confidence_text}"
    )
