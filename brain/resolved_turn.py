"""Immutable interpretation passed from routing to execution and answering.

Readers may derive a tool subgoal, but cannot replace this turn's subject,
constraints, or query with a fresh reading of the transcript or session stores.
Existing Goal/Slot/RecommendationProblem objects remain the task vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from brain.recommendation_state import RecommendationProblem


@dataclass(frozen=True)
class ResolvedTurn:
    raw_transcript: str
    normalized_transcript: str
    intent: str
    subject: str = ""
    machine_target: str = ""
    search_query: str = ""
    task: RecommendationProblem | None = None
    correction_target: str = ""
    confidence: float = 0.0
    provenance: str = "current_turn"

    @property
    def constraints(self):
        return self.task.constraints if self.task else ()

    @property
    def entity_type(self):
        return self.task.entity_type if self.task else ""


def command_was_fused(transcript: str, route) -> bool:
    """Require an OPEN interpretation and evidence of verb/host segmentation.

    A separately spoken 'open openai.com' supplies no such evidence. Neither
    does a lowercase bare address; an outage must not turn that into ai.com.
    """
    from brain.browser_navigation import host_of, unfused
    if route.computer_operation != "open_url":
        return False
    raw = str(transcript).strip().rstrip(".!?")
    target = route.computer_url or route.action_target
    split = unfused(target)
    if not split or " " in raw or host_of(raw) != host_of(target):
        return False
    camel_boundary = bool(re.match(r"(?:https?://)?open[A-Z]", raw))
    parsed_split = bool(re.fullmatch(
        rf"open\s+(?:https?://)?(?:www\.)?{re.escape(split)}/?",
        route.normalized_request.strip(), re.I,
    ))
    return camel_boundary or parsed_split
