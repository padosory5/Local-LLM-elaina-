"""Cheap pre-router gate detecting a compound, multi-capability goal.

Mirrors this codebase's existing pattern of a small deterministic regex
layered in front of an LLM call (see intent_router.py's
_DEICTIC_SURFACE_REFERENCE / _IMPLICIT_BROWSER_ACTION) rather than folding
this judgment into SemanticIntentRouter's already-large single call. A 21st
value on that one call would risk the same prompt-crowding failure already
found and fixed at the computer_action/web_search boundary earlier this
project -- multi-step detection is a different kind of judgment than picking
one of ~20 single-shot labels.

The regex only decides whether to *ask* a small, cheap LLM call; it does not
decide is_multistep on its own. A false positive here just costs one small
classification call that then correctly says "not multistep" -- a false
negative would silently drop a real compound goal, which is worse -- so this
stays deliberately permissive rather than perfectly precise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_UI_CONTROL_VERBS = re.compile(
    r"\b(?:open|launch|start|run|play|pause|resume|skip|click|type|write|"
    r"select|scroll|focus|close)\b",
    flags=re.IGNORECASE,
)
_BROWSER_CONTROL_VERBS = re.compile(
    r"\b(?:search|google|look\s*up|find|browse|shortlist|compare)\b",
    flags=re.IGNORECASE,
)
_CONJUNCTION = re.compile(
    r"\band\s+then\b|,\s*then\b|\band\s+also\b|\band\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskIntentDecision:
    is_multistep: bool
    confidence: float = 0.0
    reason: str = ""


class TaskIntentGate:
    """Cheaply decide whether a turn deserves the goal-level task planner."""

    def __init__(self, *, client: Any, model: str, keep_alive: Any = -1) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive

    def check(
        self,
        user_input: str,
        *,
        conversation_state: dict[str, Any] | None = None,
    ) -> TaskIntentDecision:
        if not self._looks_multistep(user_input):
            return TaskIntentDecision(is_multistep=False)
        return self._classify(user_input)

    @staticmethod
    def _looks_multistep(text: str) -> bool:
        match = _CONJUNCTION.search(text)
        if not match:
            return False
        before, after = text[: match.start()], text[match.end() :]
        before_capabilities = {
            "ui_control" if _UI_CONTROL_VERBS.search(before) else None,
            "browser_control" if _BROWSER_CONTROL_VERBS.search(before) else None,
        } - {None}
        after_capabilities = {
            "ui_control" if _UI_CONTROL_VERBS.search(after) else None,
            "browser_control" if _BROWSER_CONTROL_VERBS.search(after) else None,
        } - {None}
        if not before_capabilities or not after_capabilities:
            return False
        return bool(before_capabilities - after_capabilities) or bool(
            after_capabilities - before_capabilities
        )

    def _classify(self, user_input: str) -> TaskIntentDecision:
        prompt = (
            "Decide whether this request genuinely needs more than one "
            "different capability in sequence (for example: opening a "
            "native app AND separately searching/reading a webpage; or "
            "controlling a browser AND separately controlling a different "
            "native app) to be a real multi-step task -- not just one "
            "action described with an 'and' in it, and not two actions "
            "that both use the same single capability (e.g. 'open Spotify "
            "and play a song' is one capability, not multistep). Return "
            "JSON only: is_multistep_task (bool), confidence (0-1), "
            "reason (short string).\n\n"
            f"Request: {user_input}"
        )
        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 100},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            payload = json.loads(str(self._value(message, "content", "")))
            is_multistep = bool(payload.get("is_multistep_task", False))
            confidence = max(0.0, min(float(payload.get("confidence", 0)), 1.0))
            reason = str(payload.get("reason", "")).strip()
            return TaskIntentDecision(
                is_multistep=is_multistep, confidence=confidence, reason=reason,
            )
        except Exception as error:
            print(
                "[Task Intent Gate] Classification failed safely: "
                f"{type(error).__name__}: {error}"
            )
            return TaskIntentDecision(is_multistep=False)

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
