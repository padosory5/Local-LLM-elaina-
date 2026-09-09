"""What a task actually got done, said out loud.

:mod:`brain.task_outcome` already answers *which of the terminal states this
run reached*, from the run's own status and its last step, with a code table
and a drift test behind it. Twenty-two scenarios exercise it and all
twenty-two are green.

**Nothing outside the tests ever called it.**

``ChatEngine._handle_task_action`` returns ``task_result.summary``, and that
summary is whatever the model wrote in its final planning decision. Measured
across the execution matrix at the start of A5, four scenarios out of
twenty-two:

    goal   : Play my liked songs.
    step   : ui_control -> failed, "Pressed play; nothing started."
             failure_code=playback_unverified
    plan   : {"done": true, "summary": "Done."}
    outcome: retryable_failure          <- correct, and read by no one
    heard  : "Done."                    <- what the person is told

The honest sentence existed. It was on the step, it was one field away, and
the model's word was taken instead. ``task_outcome``'s own docstring names
this exact failure -- *"a caller could not tell 'it ran but did not work'
from 'it could not run'"* -- and it was still true at the only place it
mattered, because the module that fixed it was wired to nothing.

So this module is the wiring, and it carries the rule that makes the wiring
worth having:

    **On a run that did not succeed, the model's summary is not evidence.**
    The steps are.

The second half is progress. A run that opened Spotify and was then
cancelled reported "You took control, so I stopped." -- true, and it leaves
the person not knowing whether anything happened to their machine. What got
done is in ``task_state.completed_steps``; saying so is the difference
between stopping and disappearing.

Fragments pass through :func:`conversation_style.speakable_fragment` before
they are spoken, so a step record -- which is written for a log -- cannot be
read out as a sentence. A1 built that boundary after an accessibility tree
went out as speech; this is the same boundary, on the same kind of text.
"""

from __future__ import annotations

from dataclasses import dataclass

from brain import conversation_style, guard_lines, task_outcome

LANGUAGES = ("en", "ko")

# How many completed steps a person will listen to before it stops being a
# sentence. The same bound A1 settled on for spoken lists, for the same
# reason: three is where an enumeration stops parsing as speech.
SPOKEN_STEP_LIMIT = 2

# The phrases this module uses to introduce completed work. Exported so the
# A5 report can check the *other* direction -- a sentence claiming progress
# on a run that made none. Nothing produces that today; a reporting layer
# measured in only the direction it is known to fail is measured once.
SPOKEN_PROGRESS_MARKERS = ("This much is done", "여기까지는 되어 있습니다")


@dataclass(frozen=True)
class Progress:
    """What a run actually achieved, whatever its outcome."""

    #: Speakable summaries of the steps that got somewhere, in order.
    reached: tuple[str, ...] = ()
    #: Every step that ran, successful or not.
    attempted: int = 0
    #: Named things the run collected, for goals that gather rather than act.
    found: int = 0

    @property
    def any(self) -> bool:
        return bool(self.reached) or self.found > 0

    def spoken(self, language: str = "en") -> str:
        """"Spotify is open, and Liked Songs is open", or nothing.

        Step summaries are whole sentences and arrive with their own full
        stops, so joining them the way a list is joined produced
        "Spotify is open., Liked Songs is open." -- which a voice reads
        with a stop in the middle of a clause. They are trimmed to
        fragments here rather than in ``speakable_list``: that function is
        A1's and its other callers pass fragments already.
        """
        if not self.reached:
            return ""
        trimmed = [
            fragment.strip().rstrip(".!") for fragment in self.reached
        ]
        kept = [fragment for fragment in trimmed if fragment][-SPOKEN_STEP_LIMIT:]
        if not kept:
            return ""
        if len(kept) == 1:
            return kept[0]
        joiner = ", 그리고 " if language == "ko" else ", and "
        return joiner.join((", ".join(kept[:-1]), kept[-1]))

    def log_line(self) -> str:
        return (
            f"[Progress] {len(self.reached)} of {self.attempted} steps reached "
            f"their state; {self.found} item(s) collected"
        )


def reached_something(step_result) -> bool:
    """Whether this step got anywhere, as opposed to merely running.

    ``status == "done"`` is the bar, not ``verified``. Verification is
    about whether *success* was observed, and this is a different
    question: a step that opened an app and was never checked still
    changed the person's machine, and not telling them is the fault this
    exists to fix. A step that failed changed nothing worth claiming.
    """
    return (
        getattr(step_result, "status", "") == "done"
        and bool(str(getattr(step_result, "summary", "") or "").strip())
    )


def read(task_state) -> Progress:
    """What this run got done, read off the steps rather than the summary."""
    steps = list(getattr(task_state, "completed_steps", ()) or ())
    reached = []
    for step in steps:
        if not reached_something(step):
            continue
        sayable = conversation_style.speakable_fragment(step.summary)
        if sayable:
            reached.append(sayable)
    return Progress(
        reached=tuple(reached),
        attempted=len(steps),
        found=len(list(getattr(task_state, "collected_items", ()) or ())),
    )


def _last_trouble(task_state) -> str:
    """What the last step that failed actually said.

    The sentence the model's "Done." was covering. Taken from the end
    backwards because a run can fail, recover, and fail again, and the one
    that ended it is the one worth reporting.
    """
    for step in reversed(list(getattr(task_state, "completed_steps", ()) or ())):
        if getattr(step, "status", "") == "failed":
            # A cancellation is reported by its own frame, in the language
            # of the turn -- its step summary is a placeholder, not a
            # finding about the task.
            if getattr(step, "failure_code", "") in task_outcome.CANCELLED_CODES:
                return ""
            sayable = conversation_style.speakable_fragment(step.summary)
            if sayable:
                return sayable
    return ""


def report(
    outcome,
    task_state,
    *,
    language: str = "en",
    model_summary: str = "",
) -> str:
    """The sentence the person hears when a task ends.

    A successful run keeps the model's summary: it is the answer, and it
    is the thing the whole task was for. Every other outcome is built from
    the steps, because the model's summary on a failed run is the summary
    of a run the model believed had finished.
    """
    progress = read(task_state)
    if getattr(outcome, "succeeded", False):
        return str(model_summary or "").strip()

    name = getattr(outcome, "outcome", "")
    parts: list[str] = []

    if name == task_outcome.CANCELLED:
        parts.append(guard_lines.say("task_cancelled", language))
    elif name == task_outcome.NEEDS_USER_INPUT:
        # The question itself is the report. Anything added to it turns an
        # answerable question into a status update with a question in it.
        return str(model_summary or "").strip() or _last_trouble(task_state)
    else:
        trouble = _last_trouble(task_state)
        parts.append(trouble or guard_lines.say("task_incomplete", language))

    said = progress.spoken(language)
    if said:
        parts.append(guard_lines.say("task_progress", language).format(done=said))
    elif name == task_outcome.CANCELLED:
        # Stopping before anything happened is worth saying plainly. "I
        # stopped" alone leaves the person checking their own machine.
        parts.append(guard_lines.say("task_nothing_done", language))

    return " ".join(part for part in parts if part).strip()
