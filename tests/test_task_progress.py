"""A5: what a task got done, and the rule that stops the model narrating it.

The one that matters is
``TheModelsSummaryIsNotEvidenceOnAFailedRunTests`` -- everything else here
is the machinery that makes it true.
"""

import unittest

from brain import guard_lines, task_outcome, task_progress
from brain.task_planner import TaskRunResult, TaskState, TaskStep, TaskStepResult


def _step(status, summary, code="", capability="ui_control"):
    return TaskStepResult(
        TaskStep(capability=capability, sub_goal=summary),
        status,
        summary=summary,
        failure_code=code,
    )


def _state(*steps, goal="do the thing"):
    state = TaskState(goal=goal)
    state.completed_steps.extend(steps)
    return state


class ReadingWhatActuallyHappenedTests(unittest.TestCase):

    def test_a_finished_step_counts_as_progress(self):
        progress = task_progress.read(_state(_step("done", "Spotify is open.")))
        self.assertEqual(progress.reached, ("Spotify is open.",))
        self.assertTrue(progress.any)

    def test_a_failed_step_is_not_progress(self):
        progress = task_progress.read(
            _state(_step("failed", "Could not load.", "model_reported_failure"))
        )
        self.assertEqual(progress.reached, ())
        self.assertFalse(progress.any)

    def test_a_step_with_nothing_to_say_is_not_progress(self):
        self.assertEqual(task_progress.read(_state(_step("done", "  "))).reached, ())

    def test_an_empty_run_reports_nothing(self):
        progress = task_progress.read(_state())
        self.assertFalse(progress.any)
        self.assertEqual(progress.attempted, 0)

    def test_a_log_record_is_not_read_out_as_a_sentence(self):
        """A1's boundary, on the same kind of text it was built for.

        Step summaries are written for a log. One of them went out as
        speech once already -- "Completed: Window: ChatGPT Button: 최소화
        [id=6e663719-e0]" -- and this is the same class of string.
        """
        progress = task_progress.read(_state(
            _step("done", "Window: ChatGPT Button: 최소화 [id=6e663719-e0]"),
        ))
        self.assertEqual(progress.reached, ())


class TheModelsSummaryIsNotEvidenceOnAFailedRunTests(unittest.TestCase):
    """The phase, in one class.

    Measured across the execution matrix: four scenarios reported "Done."
    to the person on a run whose last step had failed verification,
    because the planner spoke the model's final planning summary and
    nothing consulted the outcome the pipeline had already computed.
    """

    def test_a_verification_failure_says_what_the_step_said(self):
        state = _state(
            _step("failed", "Pressed play; nothing started.", "playback_unverified"),
        )
        outcome = task_outcome.classify("failed", "playback_unverified")
        heard = task_progress.report(outcome, state, model_summary="Done.")
        self.assertEqual(heard, "Pressed play; nothing started.")
        self.assertNotIn("done", heard.casefold())

    def test_a_real_success_keeps_the_models_answer(self):
        """It is the answer. The whole task was for it."""
        state = _state(_step("done", "Rooms available on the 18th."))
        outcome = task_outcome.classify("done", observed=True)
        self.assertEqual(
            task_progress.report(
                outcome, state, model_summary="The Peninsula has rooms.",
            ),
            "The Peninsula has rooms.",
        )

    def test_a_question_is_reported_as_the_question(self):
        """Adding a status line to it makes it harder to answer."""
        state = _state(_step("failed", "There are three Johns.", "needs_clarification"))
        outcome = task_outcome.classify("failed", "needs_clarification")
        self.assertEqual(
            task_progress.report(
                outcome, state, model_summary="Which John did you mean?",
            ),
            "Which John did you mean?",
        )


class StoppingIsNotDisappearingTests(unittest.TestCase):

    def test_a_cancelled_run_says_what_it_already_did(self):
        state = _state(
            _step("done", "Spotify is open."),
            _step("failed", "cancelled", "user_took_over"),
        )
        outcome = task_outcome.classify("stopped", "user_took_over")
        heard = task_progress.report(outcome, state)
        self.assertIn("You took control", heard)
        self.assertIn("Spotify is open", heard)

    def test_a_cancelled_run_that_did_nothing_says_so(self):
        """"I stopped" alone leaves them checking their own machine."""
        state = _state(_step("failed", "cancelled", "user_took_over"))
        outcome = task_outcome.classify("stopped", "user_took_over")
        heard = task_progress.report(outcome, state)
        self.assertIn("Nothing changed", heard)

    def test_the_cancellation_placeholder_is_never_read_out(self):
        state = _state(_step("failed", "cancelled", "user_took_over"))
        self.assertEqual(task_progress._last_trouble(state), "")

    def test_two_steps_read_as_a_sentence_not_a_list(self):
        """Summaries arrive with their own full stops.

        Joined the way a list is joined, they produced "Spotify is open.,
        Liked Songs is open." -- which a voice reads with a stop in the
        middle of a clause.
        """
        state = _state(
            _step("done", "Spotify is open."),
            _step("done", "Liked Songs is open."),
            _step("failed", "No further progress.", "planner_stalled"),
        )
        heard = task_progress.report(
            task_outcome.with_progress(
                task_outcome.classify("failed", "planner_stalled"), True,
            ),
            state,
        )
        self.assertIn("Spotify is open, and Liked Songs is open.", heard)
        self.assertNotIn("open.,", heard)


class PartialIsItsOwnAnswerTests(unittest.TestCase):
    """A5's sixth terminal state."""

    def test_a_run_that_got_somewhere_is_partial_not_a_flat_failure(self):
        outcome = task_outcome.with_progress(
            task_outcome.classify("failed", "source_scope_violation"), True,
        )
        self.assertEqual(outcome.outcome, task_outcome.PARTIAL)

    def test_a_run_that_got_nowhere_keeps_its_failure(self):
        outcome = task_outcome.with_progress(
            task_outcome.classify("failed", "source_scope_violation"), False,
        )
        self.assertEqual(outcome.outcome, task_outcome.TERMINAL_FAILURE)

    def test_a_cancellation_keeps_its_reason(self):
        """Why it ended is the more useful half; PARTIAL would erase it."""
        outcome = task_outcome.with_progress(
            task_outcome.classify("stopped", "user_took_over"), True,
        )
        self.assertEqual(outcome.outcome, task_outcome.CANCELLED)

    def test_a_question_keeps_its_reason(self):
        outcome = task_outcome.with_progress(
            task_outcome.classify("failed", "needs_clarification"), True,
        )
        self.assertEqual(outcome.outcome, task_outcome.NEEDS_USER_INPUT)

    def test_a_success_is_never_softened_into_partial(self):
        outcome = task_outcome.with_progress(
            task_outcome.classify("done", observed=True), True,
        )
        self.assertEqual(outcome.outcome, task_outcome.SUCCESS)

    def test_partial_is_a_declared_outcome(self):
        self.assertIn(task_outcome.PARTIAL, task_outcome.OUTCOMES)

    def test_the_run_result_reaches_it_end_to_end(self):
        state = _state(
            _step("done", "The reviews page is open.", capability="browser_control"),
            _step(
                "failed", "That host is outside the allowed sources.",
                "source_scope_violation", capability="browser_control",
            ),
        )
        result = TaskRunResult("failed", "All set.", state)
        self.assertEqual(result.outcome().outcome, task_outcome.PARTIAL)


class BothLanguagesTests(unittest.TestCase):
    """Rule 4. The frames are bilingual; the step summaries are not.

    That limit is declared rather than papered over -- see the module
    docstring. The sub-planners write their summaries in English, so a
    Korean report carries an English clause naming what happened. Dropping
    it would be a Korean sentence that says less.
    """

    def test_every_task_frame_exists_in_both_languages(self):
        for name in (
            "task_incomplete", "task_progress",
            "task_cancelled", "task_nothing_done",
        ):
            for language in ("en", "ko"):
                with self.subTest(line=name, language=language):
                    self.assertTrue(guard_lines.say(name, language).strip())

    def test_a_korean_run_gets_korean_frames(self):
        state = _state(
            _step("done", "Spotify is open."),
            _step("failed", "cancelled", "user_took_over"),
        )
        outcome = task_outcome.classify("stopped", "user_took_over")
        heard = task_progress.report(outcome, state, language="ko")
        self.assertIn("직접 조작하셔서 멈췄습니다", heard)
        self.assertIn("여기까지는 되어 있습니다", heard)

    def test_the_korean_list_joins_in_korean(self):
        progress = task_progress.Progress(reached=("A is open.", "B is open."))
        self.assertIn("그리고", progress.spoken("ko"))
        self.assertIn(", and ", progress.spoken("en"))

    def test_the_module_declares_its_languages(self):
        self.assertEqual(task_progress.LANGUAGES, ("en", "ko"))


if __name__ == "__main__":
    unittest.main()
