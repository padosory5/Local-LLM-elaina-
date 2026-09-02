"""What actually happened to a task, stated once in five words.

A tool returning "done" is not a goal being met, and the pipeline already
knew that -- it just never said so in one place. The sub-planners emit a
rich, honest vocabulary of failure codes, including a whole family that
means *the action ran and the expected state never appeared*:

    playback_unverified      the click landed; nothing started playing
    shuffle_not_observed     the control was pressed; the state did not change
    collection_not_observed  the view was opened; the collection is not there
    verification_failed      checked, and the expected state is absent
    unchanged_state          the action completed and changed nothing

Those were folded into a generic "failed" one layer up, next to
"spotify_not_found" and "source_scope_violation", so a caller could not tell
"it ran but did not work" from "it could not run" from "it must not run".
Worse, all three were treated identically by the retry budget: a scope
violation got two more attempts, exactly like a transient stall.

This module does not execute anything and does not change what the planners
report. It reads a status and a failure code and answers two questions:

    outcome   which of the five terminal states this is
    verified  whether success was actually observed, or merely reported

Deliberately a lookup rather than a model call or a heuristic. Every code
below was taken from the ones the code really emits, and
``tests/test_task_outcome.py`` fails if a new one appears without being
classified here -- so the table cannot silently fall behind the planners.
"""

from __future__ import annotations

from dataclasses import dataclass

# The five terminal states. Strings rather than an Enum to match the rest of
# the deliberation layer (interaction.py, capability_selection.py), which the
# logs and tests already read as plain values.
SUCCESS = "success"
RETRYABLE_FAILURE = "retryable_failure"
NEEDS_USER_INPUT = "needs_user_input"
CANCELLED = "cancelled"
TERMINAL_FAILURE = "terminal_failure"

OUTCOMES = (
    SUCCESS, RETRYABLE_FAILURE, NEEDS_USER_INPUT, CANCELLED, TERMINAL_FAILURE,
)

# How a success was established. A task may finish without anything having
# confirmed the end state -- saying so is more useful than a confidence that
# was never earned.
VERIFIED = "verified"                  # an observation confirmed the result
UNVERIFIED = "executed_but_unverified"  # it ran; nothing checked the outcome
NOT_APPLICABLE = ""                    # not a success


# ---------------------------------------------------------------- the codes
#
# Every entry is a code some planner really emits. Grouped by what should
# happen next, which is the only question the caller has.

# The user took the machine back, or said stop. Nothing further runs.
_CANCELLED = frozenset({
    "user_took_over",
})

# The task cannot proceed until a person answers something. Never a guess:
# "send this to John" with three Johns stops here.
_NEEDS_USER_INPUT = frozenset({
    "needs_clarification",
    "direct_target_ambiguous",
})

# The action ran and the expected state did not appear. Recoverable in
# principle -- a different approach may work -- but it must never be
# reported as success, which is the whole point of naming it separately.
_VERIFICATION_FAILED = frozenset({
    "playback_unverified",
    "unverified_outcome",
    "verification_failed",
    "unchanged_state",
    "shuffle_not_observed",
    "collection_not_observed",
    "goal_operation_incomplete",
})

# Plausibly transient: the same goal, attempted again or differently, could
# succeed. These are the only codes that may spend the retry budget.
_RETRYABLE = frozenset({
    "planner_unavailable",
    "web_search_failed",
    "surface_unavailable",
    "collection_open_failed",
    "collection_scroll_failed",
    "shuffle_failed",
    "model_reported_failure",
    "planner_reported_failure",
    # A tool raised rather than returning a result. Retryable because the
    # planner can legitimately recover by choosing a different capability or
    # sub-goal, and the exception text now travels with it so the retry has
    # something to go on.
    "tool_exception",
    # "this particular target was not found" -- not "the goal is
    # impossible". A different sub-goal can still reach it, and the planner
    # already recovers this way: a page that would not load is followed by
    # "try a different site", which succeeds. The already-retried case has
    # its own code, "repeated_not_found", and that one is terminal.
    "direct_result_not_found",
    "direct_target_not_found",
})

# Retrying cannot help. Three reasons, all terminal for different causes:
# a budget is spent, the thing genuinely is not there, or policy forbids it.
_TERMINAL = frozenset({
    # a bound was reached -- retrying is the loop this exists to stop
    "model_round_budget_exhausted",
    "observation_budget_exhausted",
    "action_budget_exhausted",
    "planner_stalled",
    "repeated_step",
    "repeated_not_found",
    # it is not there
    "spotify_not_found",
    "collection_not_found",
    "track_not_found_in_collection",
    "collection_not_playable",
    "collection_not_scrollable",
    "no_commit_control",
    # policy or a malformed target: doing it again does the same thing
    "source_scope_violation",
    "invalid_target",
    "invalid_confirmation_action",
    "missing_element_id",
    "missing_tab_identity",
    "wrong_media_target",
})

# Every code this module knows. The drift test reads this.
KNOWN_FAILURE_CODES = (
    _CANCELLED | _NEEDS_USER_INPUT | _VERIFICATION_FAILED
    | _RETRYABLE | _TERMINAL
)

# Statuses the planners use, mapped where they are unambiguous. "failed"
# is absent on purpose: what it means depends entirely on the code.
_BY_STATUS = {
    "done": SUCCESS,
    "needs_confirmation": NEEDS_USER_INPUT,
    "needs_clarification": NEEDS_USER_INPUT,
    "needs_strategy_choice": NEEDS_USER_INPUT,
    "capability_unavailable": TERMINAL_FAILURE,
    "interrupted": CANCELLED,
}


@dataclass(frozen=True)
class TaskOutcome:
    """One answer to "what happened", with the reason it says so."""

    outcome: str = SUCCESS
    verification: str = NOT_APPLICABLE
    failure_code: str = ""
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome == SUCCESS

    @property
    def verified(self) -> bool:
        """Whether an observation actually confirmed the end state."""
        return self.verification == VERIFIED

    @property
    def may_retry(self) -> bool:
        return self.outcome == RETRYABLE_FAILURE

    @property
    def is_verification_failure(self) -> bool:
        """The action ran and the expected state never appeared."""
        return self.failure_code in _VERIFICATION_FAILED

    def log_line(self) -> str:
        detail = f" ({self.failure_code})" if self.failure_code else ""
        verification = f" [{self.verification}]" if self.verification else ""
        return f"{self.outcome}{verification}{detail}: {self.reason}"


def classify(
    status: str,
    failure_code: str = "",
    *,
    observed: bool = False,
) -> TaskOutcome:
    """Read a planner's status and code into one of the five outcomes.

    ``observed`` says whether something actually confirmed the end state --
    the verified foreground application for a desktop step, a page read back
    after the action for a browser one. It only refines a success; it can
    never turn a failure into one.
    """
    status = str(status or "").strip()
    code = str(failure_code or "").strip()

    # A cancellation outranks everything, including a status that still says
    # the step "failed": the person stopped it, and that is not a defect.
    if code in _CANCELLED:
        return TaskOutcome(
            CANCELLED, NOT_APPLICABLE, code,
            "the user took it back, so nothing further runs",
        )

    if status == "done":
        # A code alongside a "done" status means something checked and was
        # not satisfied. Success is not available to it.
        if code in _VERIFICATION_FAILED:
            return TaskOutcome(
                RETRYABLE_FAILURE, NOT_APPLICABLE, code,
                "the step ran and the expected state did not appear",
            )
        return TaskOutcome(
            SUCCESS,
            VERIFIED if observed else UNVERIFIED,
            "",
            "the expected end state was observed" if observed
            else "the step reported success; nothing observed the end state",
        )

    if code in _NEEDS_USER_INPUT:
        return TaskOutcome(
            NEEDS_USER_INPUT, NOT_APPLICABLE, code,
            "the task cannot continue until the person answers",
        )
    if code in _VERIFICATION_FAILED:
        return TaskOutcome(
            RETRYABLE_FAILURE, NOT_APPLICABLE, code,
            "the step ran and the expected state did not appear",
        )
    if code in _TERMINAL:
        return TaskOutcome(
            TERMINAL_FAILURE, NOT_APPLICABLE, code,
            "retrying this cannot change the result",
        )
    if code in _RETRYABLE:
        return TaskOutcome(
            RETRYABLE_FAILURE, NOT_APPLICABLE, code,
            "the failure is plausibly transient",
        )

    mapped = _BY_STATUS.get(status)
    if mapped is not None:
        return TaskOutcome(mapped, NOT_APPLICABLE, code, f"status {status!r}")

    # Fails safe, and deliberately toward stopping. An unrecognised failure
    # is treated as retryable rather than terminal only when the planner
    # itself said "failed" -- anything stranger stops, because inventing
    # another attempt on a state nobody modelled is how a loop starts.
    if status == "failed":
        return TaskOutcome(
            RETRYABLE_FAILURE, NOT_APPLICABLE, code,
            "an unclassified failure; one more attempt is allowed",
        )
    return TaskOutcome(
        TERMINAL_FAILURE, NOT_APPLICABLE, code,
        f"unrecognised status {status!r}; stopping rather than guessing",
    )
