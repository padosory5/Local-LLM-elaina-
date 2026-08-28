import json
import unittest

from brain.browser_action_planner import (
    ActionPlanResult as BrowserActionPlanResult,
    PendingConfirmation as BrowserPendingConfirmation,
)
from brain.desktop_action_planner import (
    ActionPlanResult as DesktopActionPlanResult,
    DesktopSurfaceContext,
    PendingConfirmation as DesktopPendingConfirmation,
)
from brain.task_planner import (
    ExtractedItem,
    TaskPlanner,
    TaskState,
    TaskStep,
    _classify_step_risk,
    _MAX_CONSECUTIVE_FAILURES,
)
from brain.task_discovery_policy import TaskDiscoveryPolicy
from brain.user_locale import UserLocale


class FakeClient:
    """Returns one queued JSON decision per .chat() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps(self._responses.pop(0))}}


class FakeDesktopExecutor:
    def __init__(self, *, act_results=None, resume_result=None):
        self._act_results = list(act_results or [])
        self._resume_result = resume_result
        self.act_calls = []
        self.resume_calls = []

    def act(self, goal):
        self.act_calls.append(goal)
        return self._act_results.pop(0)

    def resume_confirmed_click(self, *, window_title, control_name, window_snapshot=None):
        self.resume_calls.append((window_title, control_name, window_snapshot))
        return self._resume_result


class FakeBrowserExecutor:
    def __init__(self, *, act_results=None, resume_result=None):
        self._act_results = list(act_results or [])
        self._resume_result = resume_result
        self.act_calls = []
        self.act_kwargs = []
        self.resume_calls = []

    def act(self, goal, **kwargs):
        self.act_calls.append(goal)
        self.act_kwargs.append(kwargs)
        return self._act_results.pop(0)

    def resume_confirmed_action(self, **kwargs):
        self.resume_calls.append(kwargs)
        return self._resume_result


class FakeWebSearchExecutor:
    def __init__(self, *, act_results=None):
        self._act_results = list(act_results or [])
        self.act_calls = []

    def act(self, goal):
        self.act_calls.append(goal)
        return self._act_results.pop(0)


class FakeComputerControlMode:
    def __init__(self, enabled):
        self.enabled = enabled


class FakeExtractor:
    """Returns one queued item tuple per .extract() call, keyed by call order."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def extract(self, text, *, source_type="model_knowledge", source=""):
        self.calls.append((text, source_type, source))
        return self._results.pop(0) if self._results else ()


def _planner(
    responses,
    *,
    desktop_results=None,
    browser_results=None,
    desktop_resume=None,
    browser_resume=None,
    computer_control_mode=None,
    browser_control_enabled=True,
    web_search_enabled=True,
    web_search_action_planner=None,
    max_steps=8,
    task_extractor=None,
    preview_enabled=False,
    discovery_policy=None,
    user_locale=None,
):
    desktop = FakeDesktopExecutor(act_results=desktop_results, resume_result=desktop_resume)
    browser = FakeBrowserExecutor(act_results=browser_results, resume_result=browser_resume)
    planner = TaskPlanner(
        client=FakeClient(responses),
        model="qwen3:8b",
        keep_alive=-1,
        agent_registry=None,
        desktop_action_planner=desktop,
        browser_action_planner=browser,
        web_search_action_planner=web_search_action_planner,
        computer_control_mode=computer_control_mode,
        browser_control_enabled=browser_control_enabled,
        web_search_enabled=web_search_enabled,
        max_steps=max_steps,
        task_extractor=task_extractor,
        # Existing tests queue exact FakeClient responses for _plan_next
        # calls only; preview is tested explicitly below where it's on.
        preview_enabled=preview_enabled,
        discovery_policy=discovery_policy,
        user_locale=user_locale,
    )
    return planner, desktop, browser


class TaskPlannerTests(unittest.TestCase):
    def test_executes_a_single_step_task_to_completion(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capability": "browser_control",
                    "sub_goal": "Search Google for hotels in Guam.",
                    "rationale": "Start the research.",
                },
                {"done": True, "summary": "Found several hotels in Guam."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Opened search results."),
            ],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Found several hotels in Guam.")
        self.assertEqual(browser.act_calls, ["Search Google for hotels in Guam."])
        self.assertEqual(result.task_state.step_count, 1)

    def test_executes_multiple_steps_and_folds_collected_information(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels in Guam."},
                {"capability": "browser_control", "sub_goal": "Open the first result."},
                {"done": True, "summary": "Shortlist ready."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Search results shown."),
                BrowserActionPlanResult("done", "Opened Ocean View Hotel."),
            ],
        )

        result = planner.run("Find a hotel in Guam and make a shortlist.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(result.task_state.completed_steps), 2)
        self.assertEqual(
            result.task_state.collected_information,
            ["Search results shown.", "Opened Ocean View Hotel."],
        )

    def test_max_steps_bound_stops_the_loop(self):
        # Distinct sub_goals each round -- otherwise repeat-detection (see
        # below) would catch this before the step budget itself does,
        # which is a different mechanism this test isn't exercising.
        responses = [
            {"capability": "browser_control", "sub_goal": f"Keep looking, attempt {i}."}
            for i in range(3)
        ]
        planner, _, browser = _planner(
            responses=responses,
            browser_results=[
                BrowserActionPlanResult("done", "Still looking.") for _ in range(3)
            ],
            max_steps=3,
        )

        result = planner.run("An open-ended goal.")

        self.assertEqual(result.status, "stopped")
        self.assertEqual(result.task_state.step_count, 3)

    def test_capability_unavailable_short_circuits_before_executing(self):
        planner, desktop, _ = _planner(
            responses=[
                {"capability": "ui_control", "sub_goal": "Open Notepad."},
            ],
            desktop_results=[DesktopActionPlanResult("done", "Opened Notepad.")],
            computer_control_mode=FakeComputerControlMode(enabled=False),
        )

        result = planner.run("Write something in Notepad.")

        self.assertEqual(result.status, "capability_unavailable")
        self.assertEqual(desktop.act_calls, [])

    def test_needs_confirmation_returns_pending_details_and_pauses(self):
        pending = BrowserPendingConfirmation(
            tab_index=1, element_id="scan1-e2", element_label="Buy now",
            url="https://example.com", action="click", scan_id="scan1",
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Click Buy now."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "needs_confirmation", "Clicking 'Buy now' needs confirmation.",
                    pending=pending,
                ),
            ],
        )

        result = planner.run("Buy the item.")

        self.assertEqual(result.status, "needs_confirmation")
        self.assertEqual(result.task_state.status, "needs_confirmation")
        self.assertEqual(result.pending_capability, "browser_control")
        self.assertIsNotNone(result.pending_step)
        self.assertEqual(result.pending_prepared.operation, "browser_action")
        self.assertEqual(result.pending_prepared.target, "scan1-e2")
        self.assertEqual(result.pending_prepared.tab_index, 1)

    def test_resume_continues_after_confirmation_and_completes(self):
        pending = BrowserPendingConfirmation(
            tab_index=1, element_id="scan1-e2", element_label="Buy now",
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Click Buy now."},
                {"done": True, "summary": "Purchased."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "needs_confirmation", "needs confirmation", pending=pending,
                ),
            ],
            browser_resume=BrowserActionPlanResult("done", "Clicked Buy now."),
        )
        first = planner.run("Buy the item.")
        self.assertEqual(first.status, "needs_confirmation")

        result = planner.resume(
            first.task_state,
            approved_action=first.pending_prepared,
            step=first.pending_step,
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(browser.resume_calls[0]["element_id"], "scan1-e2")

    def test_single_failed_step_replans_and_can_still_finish(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"capability": "browser_control", "sub_goal": "Try a different site."},
                {"done": True, "summary": "Found a hotel after retrying."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "failed", "Could not load the page.",
                    failure_code="direct_target_not_found",
                ),
                BrowserActionPlanResult("done", "Opened search results."),
            ],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Found a hotel after retrying.")
        self.assertEqual(browser.act_calls, [
            "Search for hotels.", "Try a different site.",
        ])
        self.assertIn("browser_control", result.task_state.errors[0])
        self.assertEqual(result.task_state.consecutive_failures, 0)

    def test_physical_takeover_stops_the_whole_task_without_replanning(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Open Booking.com."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "failed", "You moved the mouse.",
                    failure_code="user_took_over",
                ),
            ],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "stopped")
        self.assertEqual(result.summary, "You took control, so I stopped.")
        self.assertEqual(browser.act_calls, ["Open Booking.com."])

    def test_consecutive_failures_past_the_budget_stop_the_task(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"capability": "browser_control", "sub_goal": "Try again."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "failed", "Could not load the page.", failure_code="x",
                ),
                BrowserActionPlanResult(
                    "failed", "Still could not load the page.", failure_code="x",
                ),
            ],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.summary, "Still could not load the page.")
        self.assertEqual(len(browser.act_calls), _MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(
            result.task_state.consecutive_failures, _MAX_CONSECUTIVE_FAILURES,
        )

    def test_a_success_between_failures_resets_the_budget(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "a"},
                {"capability": "browser_control", "sub_goal": "b"},
                {"capability": "browser_control", "sub_goal": "c"},
                {"done": True, "summary": "Done anyway."},
            ],
            browser_results=[
                BrowserActionPlanResult("failed", "f1", failure_code="x"),
                BrowserActionPlanResult("done", "s1"),
                BrowserActionPlanResult("failed", "f2", failure_code="x"),
            ],
        )

        result = planner.run("An open-ended goal.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(browser.act_calls), 3)

    def test_resume_failure_also_replans_and_can_still_finish(self):
        pending = BrowserPendingConfirmation(
            tab_index=1, element_id="scan1-e2", element_label="Buy now",
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Click Buy now."},
                {"capability": "browser_control", "sub_goal": "Try clicking again."},
                {"done": True, "summary": "Purchased on retry."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "needs_confirmation", "needs confirmation", pending=pending,
                ),
                BrowserActionPlanResult("done", "Clicked Buy now on retry."),
            ],
            browser_resume=BrowserActionPlanResult(
                "failed", "Click did not register.", failure_code="unverified_outcome",
            ),
        )
        first = planner.run("Buy the item.")
        self.assertEqual(first.status, "needs_confirmation")

        result = planner.resume(
            first.task_state,
            approved_action=first.pending_prepared,
            step=first.pending_step,
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Purchased on retry.")
        self.assertEqual(browser.resume_calls[0]["element_id"], "scan1-e2")
        self.assertEqual(
            browser.act_calls, ["Click Buy now.", "Try clicking again."],
        )

    def test_invalid_capability_in_decision_fails_safely(self):
        planner, desktop, browser = _planner(
            responses=[
                {"capability": "vision", "sub_goal": "Look at the screen."},
            ],
        )

        result = planner.run("Look at my screen and tell me what's there.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(desktop.act_calls, [])
        self.assertEqual(browser.act_calls, [])

    def test_planner_call_failure_fails_safely(self):
        planner, _, _ = _planner(responses=[])
        # No queued responses at all -- FakeClient.chat() will raise
        # IndexError, exercising the planner's own exception handling.

        result = planner.run("Do something.")

        self.assertEqual(result.status, "failed")

    def test_extractor_defaults_to_off_and_never_populates_collected_items(self):
        # task_extractor is opt-in (None by default) so a caller that
        # doesn't need 4D-3 never pays for the extra classification call.
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Found hotels."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "done",
                    "Ocean View Resort ($180/night, 4.5 stars), Guam Beach "
                    "Hotel ($120/night, 4.0 stars).",
                ),
            ],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.task_state.collected_items, [])

    def test_extractor_populates_collected_items_when_provided(self):
        extractor = FakeExtractor([
            (
                ExtractedItem(name="Ocean View Resort", attributes={"price": "$180/night"}),
                ExtractedItem(name="Guam Beach Hotel", attributes={"price": "$120/night"}),
            ),
        ])
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Found hotels."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "done",
                    "Ocean View Resort ($180/night, 4.5 stars), Guam Beach "
                    "Hotel ($120/night, 4.0 stars).",
                ),
            ],
            task_extractor=extractor,
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(result.task_state.collected_items), 2)
        self.assertEqual(result.task_state.collected_items[0].name, "Ocean View Resort")
        self.assertEqual(len(extractor.calls), 1)

        # The second (final "done") planning call must have seen the
        # structured items, not just the raw prose.
        final_prompt = planner.client.calls[-1]["messages"][0]["content"]
        self.assertIn("Ocean View Resort", final_prompt)
        self.assertIn("$180/night", final_prompt)

    def test_extractor_failure_does_not_stop_the_task(self):
        class RaisingExtractor:
            def extract(self, text):
                raise RuntimeError("extraction backend unavailable")

        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Found hotels."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "A, B, C ($1, $2, $3)."),
            ],
            task_extractor=RaisingExtractor(),
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.task_state.collected_items, [])


class TaskPlanPreviewTests(unittest.TestCase):
    """4D foundation: 'explain what she intends to do before execution',
    plus the capability check and preference extraction that upfront
    analysis makes possible."""

    def test_states_intent_before_the_first_step_executes(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll search for hotels in Guam.",
                },
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Found hotels."},
            ],
            browser_results=[BrowserActionPlanResult("done", "Some hotels.")],
            preview_enabled=True,
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(
            result.task_state.plan_preview, "I'll search for hotels in Guam.",
        )
        self.assertEqual(result.task_state.required_capabilities, ("browser_control",))
        # The preview call happened first, before any step was dispatched.
        self.assertEqual(browser.act_calls, ["Search for hotels."])

    def test_extracts_preferences_from_the_goal_and_they_reach_planning(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {"max_price": "$150/night"},
                    "plan_preview": "I'll find a hotel under $150 a night.",
                },
                {"done": True, "summary": "Nothing under $150 was found."},
            ],
            preview_enabled=True,
        )

        result = planner.run("Find a hotel in Guam under $150 a night.")

        self.assertEqual(result.task_state.preferences, {"max_price": "$150/night"})
        # The very next planning prompt must actually carry the constraint,
        # not just store it unused.
        planning_prompt = planner.client.calls[-1]["messages"][0]["content"]
        self.assertIn("$150/night", planning_prompt)

    def test_stops_before_any_step_when_a_needed_capability_is_unavailable(self):
        planner, desktop, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["email"],
                    "preferences": {},
                    "plan_preview": "I'll check your email for that.",
                },
            ],
            preview_enabled=True,
        )

        result = planner.run("Check my email and reply to anything urgent.")

        self.assertEqual(result.status, "capability_unavailable")
        self.assertIn("email", result.summary)
        self.assertEqual(desktop.act_calls, [])
        self.assertEqual(browser.act_calls, [])
        # Only the preview call happened -- no step-planning call at all.
        self.assertEqual(len(planner.client.calls), 1)

    def test_empty_capabilities_needed_is_also_treated_as_unavailable(self):
        # Found live: the prompt asks the model to *name* an unlisted
        # capability so it can be recognized as unavailable, but it doesn't
        # always follow that -- it can just return an empty list instead.
        # A goal reaching TaskPlanner already passed TaskIntentGate's
        # multi-step check, so it always needs some real capability; an
        # empty list must not be read as "proceed with nothing to do".
        planner, desktop, browser = _planner(
            responses=[
                {
                    "capabilities_needed": [],
                    "preferences": {},
                    "plan_preview": "Recall and summarize what you told me.",
                },
            ],
            preview_enabled=True,
        )

        result = planner.run("Recall what I told you about my sister last week.")

        self.assertEqual(result.status, "capability_unavailable")
        self.assertEqual(desktop.act_calls, [])
        self.assertEqual(browser.act_calls, [])
        self.assertEqual(len(planner.client.calls), 1)

    def test_preview_failure_does_not_block_the_task(self):
        class FirstCallFailsClient:
            def __init__(self, responses):
                self._responses = list(responses)
                self.calls = []

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise RuntimeError("preview backend unavailable")
                return {"message": {"content": json.dumps(self._responses.pop(0))}}

        desktop = FakeDesktopExecutor(act_results=[])
        browser = FakeBrowserExecutor(
            act_results=[BrowserActionPlanResult("done", "Found hotels.")],
        )
        planner = TaskPlanner(
            client=FirstCallFailsClient([
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Found hotels."},
            ]),
            model="qwen3:8b",
            keep_alive=-1,
            agent_registry=None,
            desktop_action_planner=desktop,
            browser_action_planner=browser,
            preview_enabled=True,
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.task_state.plan_preview, "")

    def test_disabled_by_default_matches_pre_4d_foundation_behavior(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Found hotels."},
            ],
            browser_results=[BrowserActionPlanResult("done", "Some hotels.")],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.task_state.plan_preview, "")
        self.assertEqual(len(planner.client.calls), 2)


class TaskCurrentApplicationTests(unittest.TestCase):
    def test_set_from_a_verified_ui_control_step(self):
        planner, desktop, _ = _planner(
            responses=[
                {"capability": "ui_control", "sub_goal": "Open Notepad."},
                {"done": True, "summary": "Opened Notepad."},
            ],
            desktop_results=[
                DesktopActionPlanResult(
                    "done", "Opened Notepad.",
                    surface_context=DesktopSurfaceContext(app_name="Notepad"),
                ),
            ],
            computer_control_mode=FakeComputerControlMode(enabled=True),
        )

        result = planner.run("Open Notepad.")

        self.assertEqual(result.task_state.current_application, "Notepad")

    def test_stays_empty_when_the_step_fails(self):
        planner, desktop, _ = _planner(
            responses=[
                {"capability": "ui_control", "sub_goal": "Open Notepad."},
            ],
            desktop_results=[
                DesktopActionPlanResult(
                    "failed", "Could not find Notepad.",
                    surface_context=DesktopSurfaceContext(app_name="Notepad"),
                    failure_code="not_found",
                ),
            ],
            computer_control_mode=FakeComputerControlMode(enabled=True),
        )

        result = planner.run("Open Notepad.")

        self.assertEqual(result.task_state.current_application, "")


class TaskRepeatDetectionTests(unittest.TestCase):
    """A step succeeding is not proof of progress -- a model can get stuck
    re-verifying an already-satisfied goal with every individual step
    reporting done. Prompt guidance alone did not reliably stop this in
    live testing, so it's enforced in code."""

    def test_third_identical_dispatch_forces_a_decision_instead_of_running(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Check price."},
                {"capability": "browser_control", "sub_goal": "Check price."},
                {"capability": "browser_control", "sub_goal": "Check price."},
                {"done": True, "summary": "Paradise Inn is under $150."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Checked once."),
                BrowserActionPlanResult("done", "Checked twice."),
            ],
        )

        result = planner.run("Find a hotel under $150.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Paradise Inn is under $150.")
        # Only two real dispatches -- the third identical proposal was
        # caught and replaced with a forced decision instead of running.
        self.assertEqual(browser.act_calls, ["Check price.", "Check price."])

    def test_forced_decision_that_still_repeats_stops_cleanly(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Check price."},
                {"capability": "browser_control", "sub_goal": "Check price."},
                {"capability": "browser_control", "sub_goal": "Check price."},
                # Even the forced "decide now" call ignores the
                # instruction and proposes yet another step.
                {"capability": "browser_control", "sub_goal": "Check price again."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Checked once."),
                BrowserActionPlanResult("done", "Checked twice."),
            ],
        )

        result = planner.run("Find a hotel under $150.")

        self.assertEqual(result.status, "stopped")
        self.assertEqual(browser.act_calls, ["Check price.", "Check price."])

    def test_repeat_detection_is_case_and_whitespace_insensitive(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Check   Price."},
                {"capability": "browser_control", "sub_goal": "check price."},
                {"capability": "browser_control", "sub_goal": "CHECK PRICE."},
                {"done": True, "summary": "Done."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Checked once."),
                BrowserActionPlanResult("done", "Checked twice."),
            ],
        )

        result = planner.run("Find a hotel under $150.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(browser.act_calls), 2)

    def test_distinct_sub_goals_never_trigger_repeat_detection(self):
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"capability": "browser_control", "sub_goal": "Check Guam Beach Hotel's price."},
                {"capability": "browser_control", "sub_goal": "Check Paradise Inn's price."},
                {"done": True, "summary": "Done."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Found hotels."),
                BrowserActionPlanResult("done", "$120/night."),
                BrowserActionPlanResult("done", "$95/night."),
            ],
        )

        result = planner.run("Find a hotel under $150.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(browser.act_calls), 3)

    def test_non_consecutive_revisit_forces_a_decision_instead_of_running(self):
        # Found live: verifying 5 hotels one by one, the model re-proposed
        # an already-completed target (not back-to-back -- three different
        # hotels came in between) and kept doing so until the step budget
        # ran out. _trailing_repeat_count alone can't see this because a
        # *different* step breaks its consecutive streak.
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Check Guam Beach Hotel's price."},
                {"capability": "browser_control", "sub_goal": "Check Paradise Inn's price."},
                # Revisits Guam Beach Hotel -- already done two steps ago,
                # not the immediately preceding step.
                {"capability": "browser_control", "sub_goal": "Check Guam Beach Hotel's price."},
                {"done": True, "summary": "Guam Beach Hotel is the best option."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "$120/night."),
                BrowserActionPlanResult("done", "$95/night."),
            ],
        )

        result = planner.run("Find a hotel under $150.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Guam Beach Hotel is the best option.")
        # Only the two distinct targets were actually dispatched -- the
        # revisit was caught and replaced with a forced decision.
        self.assertEqual(
            browser.act_calls,
            ["Check Guam Beach Hotel's price.", "Check Paradise Inn's price."],
        )

    def test_revisit_immediately_following_the_same_step_stays_a_trailing_repeat(self):
        # A revisit with NOTHING in between is the trailing-repeat case,
        # which intentionally tolerates _MAX_CONSECUTIVE_REPEATS immediate
        # repeats before intervening -- the non-consecutive check must not
        # make that case stricter than before.
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Check price."},
                {"capability": "browser_control", "sub_goal": "Check price."},
                {"capability": "browser_control", "sub_goal": "Check price."},
                {"done": True, "summary": "Done."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Checked once."),
                BrowserActionPlanResult("done", "Checked twice."),
            ],
        )

        result = planner.run("Find a hotel under $150.")

        self.assertEqual(result.status, "done")
        self.assertEqual(browser.act_calls, ["Check price.", "Check price."])


class TaskPromptTruncationTests(unittest.TestCase):
    """4D-4: found live -- a browser step's own final answer echoed a raw
    describe_page element dump instead of a short synthesis, and feeding
    that verbatim into every later planning prompt bloated it enough that
    the model's own JSON response got truncated mid-string. The task
    planner's own prompt size must never depend on an upstream planner
    being well-behaved."""

    def test_a_very_long_step_summary_is_truncated_in_the_prompt(self):
        huge_summary = "Dusit Thani Guam Resort " + ("x" * 2000)
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Done."},
            ],
            browser_results=[BrowserActionPlanResult("done", huge_summary)],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        final_prompt = planner.client.calls[-1]["messages"][0]["content"]
        # The full 2000-char run must not survive intact anywhere in the
        # prompt -- each of the (separate) history/info occurrences is
        # truncated on its own.
        self.assertNotIn("x" * 2000, final_prompt)
        self.assertIn("[truncated]", final_prompt)
        # The start of the real content must still survive -- only the
        # tail is cut, not the whole thing replaced.
        self.assertIn("Dusit Thani Guam Resort", final_prompt)

    def test_the_final_spoken_summary_is_also_capped(self):
        # Same failure mode, a different exposure: the *final* "done"
        # summary is spoken aloud (TTS), not just fed into another
        # prompt. Found live: it inherited a raw element dump as its own
        # final answer and read the whole thing out loud.
        huge_summary = "The cheapest hotel is Paradise Inn. " + ("y" * 3000)
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": huge_summary},
            ],
            browser_results=[BrowserActionPlanResult("done", "Found hotels.")],
        )

        result = planner.run("Find a hotel in Guam.")

        self.assertEqual(result.status, "done")
        self.assertLess(len(result.summary), len(huge_summary))
        self.assertIn("Paradise Inn", result.summary)


class TaskWebSearchCapabilityTests(unittest.TestCase):
    """web_search as a third TaskPlanner capability: Information
    Acquisition layer -- graduated effort, not a competing tool choice."""

    def test_web_search_step_dispatches_and_completes(self):
        web_search = FakeWebSearchExecutor(
            act_results=[BrowserActionPlanResult("done", "Found hotels in Guam.")],
        )
        planner, _, _ = _planner(
            responses=[
                {"capability": "web_search", "sub_goal": "Find hotels in Guam."},
                {"done": True, "summary": "Found hotels in Guam."},
            ],
            web_search_action_planner=web_search,
        )

        result = planner.run("Find hotels in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(web_search.act_calls, ["Find hotels in Guam."])

    def test_web_search_not_offered_when_no_planner_is_wired(self):
        planner, _, _ = _planner(responses=[])

        self.assertNotIn("web_search", planner.executors)

    def test_preview_verification_level_reaches_the_planning_prompt(self):
        web_search = FakeWebSearchExecutor(
            act_results=[BrowserActionPlanResult("done", "Checked the current price.")],
        )
        planner, _, _ = _planner(
            responses=[
                {
                    "capabilities_needed": ["web_search", "browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll check the current price.",
                    "verification_level": "verify",
                },
                {"capability": "web_search", "sub_goal": "Check the current price."},
                {"done": True, "summary": "The current price is $120/night."},
            ],
            web_search_action_planner=web_search,
            preview_enabled=True,
        )

        result = planner.run("Check the hotel's actual current price.")

        self.assertEqual(result.task_state.verification_level, "verify")
        planning_prompt = planner.client.calls[-1]["messages"][0]["content"]
        self.assertIn("Verification level for this goal: verify", planning_prompt)

    def test_preview_defaults_to_discover_when_unspecified(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll look into it.",
                    # No verification_level key at all -- must not error,
                    # must default to "discover".
                },
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Found hotels."},
            ],
            browser_results=[BrowserActionPlanResult("done", "Found hotels.")],
            preview_enabled=True,
        )

        result = planner.run("Find hotels in Guam.")

        self.assertEqual(result.task_state.verification_level, "discover")

    def test_invalid_verification_level_value_is_ignored(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll look into it.",
                    "verification_level": "not_a_real_level",
                },
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"done": True, "summary": "Found hotels."},
            ],
            browser_results=[BrowserActionPlanResult("done", "Found hotels.")],
            preview_enabled=True,
        )

        result = planner.run("Find hotels in Guam.")

        self.assertEqual(result.task_state.verification_level, "discover")

    def test_extracted_items_carry_source_type_matching_the_producing_capability(self):
        extractor = FakeExtractor([
            (ExtractedItem(name="Ocean View Resort", attributes={"price": "$180/night"}),),
            (ExtractedItem(name="Guam Beach Hotel", attributes={"price": "$120/night"}),),
        ])
        web_search = FakeWebSearchExecutor(
            act_results=[BrowserActionPlanResult("done", "Ocean View Resort $180/night.")],
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "web_search", "sub_goal": "Find hotels in Guam."},
                {"capability": "browser_control", "sub_goal": "Confirm Guam Beach Hotel's price."},
                {"done": True, "summary": "Done."},
            ],
            browser_results=[BrowserActionPlanResult("done", "Guam Beach Hotel $120/night.")],
            web_search_action_planner=web_search,
            task_extractor=extractor,
        )

        planner.run("Find hotels in Guam and confirm one price.")

        self.assertEqual(len(extractor.calls), 2)
        self.assertEqual(extractor.calls[0][1], "web_search_snippet")
        self.assertEqual(extractor.calls[1][1], "browser_observed")

    def test_web_search_precondition_blocks_when_disabled(self):
        web_search = FakeWebSearchExecutor(act_results=[])
        planner, _, _ = _planner(
            responses=[
                {"capability": "web_search", "sub_goal": "Find hotels in Guam."},
            ],
            web_search_action_planner=web_search,
            web_search_enabled=False,
        )

        result = planner.run("Find hotels in Guam.")

        self.assertEqual(result.status, "capability_unavailable")
        self.assertEqual(web_search.act_calls, [])

    def test_discover_mode_caps_browser_control_to_one_selective_check(self):
        # Found live: even with verification_level="discover" telling it to
        # prefer web_search, the model kept opening a browser page for
        # every single discovered hotel -- exactly the "unnecessarily slow
        # and expensive" pattern this layer exists to avoid. Structurally
        # capped to one selective confirmation once web_search is wired as
        # the actual alternative.
        web_search = FakeWebSearchExecutor(
            act_results=[BrowserActionPlanResult("done", "Found several hotels.")],
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "web_search", "sub_goal": "Find hotels in Guam."},
                {"capability": "browser_control", "sub_goal": "Confirm the top pick's price."},
                # A second browser_control proposal -- must be intercepted
                # before dispatch, not actually run.
                {"capability": "browser_control", "sub_goal": "Confirm a second hotel's price."},
                {"done": True, "summary": "Ocean View Resort is a good pick."},
            ],
            browser_results=[BrowserActionPlanResult("done", "Ocean View Resort $180/night.")],
            web_search_action_planner=web_search,
        )

        result = planner.run("Find hotels in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(web_search.act_calls, ["Find hotels in Guam."])
        self.assertEqual(browser.act_calls, ["Confirm the top pick's price."])

    def test_verify_mode_is_not_capped_by_the_discover_mode_browser_limit(self):
        web_search = FakeWebSearchExecutor(
            act_results=[BrowserActionPlanResult("done", "Found several hotels.")],
        )
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["web_search", "browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll check current prices.",
                    "verification_level": "verify",
                },
                {"capability": "web_search", "sub_goal": "Find hotels in Guam."},
                {"capability": "browser_control", "sub_goal": "Confirm hotel A's price."},
                {"capability": "browser_control", "sub_goal": "Confirm hotel B's price."},
                {"done": True, "summary": "Both prices confirmed."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Hotel A $150/night."),
                BrowserActionPlanResult("done", "Hotel B $170/night."),
            ],
            web_search_action_planner=web_search,
            preview_enabled=True,
        )

        result = planner.run("Check the actual current prices of two hotels in Guam.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(browser.act_calls), 2)

    def test_discover_mode_cap_does_not_apply_without_web_search_wired(self):
        # "discover" is also TaskState's inert default when no preview ran
        # at all -- a pure browser-only task (no web_search capability)
        # must keep working exactly as before this layer existed.
        planner, _, browser = _planner(
            responses=[
                {"capability": "browser_control", "sub_goal": "Search for hotels."},
                {"capability": "browser_control", "sub_goal": "Open the first result."},
                {"done": True, "summary": "Shortlist ready."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Search results shown."),
                BrowserActionPlanResult("done", "Ocean View Resort $180/night."),
            ],
        )

        result = planner.run("Find a hotel in Guam and make me a shortlist.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(browser.act_calls), 2)


class TaskRiskClassificationTests(unittest.TestCase):
    """4D-5: every dispatched step is classified by risk before it runs.
    Reuses the same element-grounded is_committing_element/
    is_committing_control checks the tier-2 planners already enforce for
    real, applied one level up against the sub_goal's own wording -- a
    proactive, visible signal for the plan, not a second enforcement
    point (that stays the tier-2 planner's real, DOM/window-grounded
    check, unchanged)."""

    def test_reading_and_browsing_sub_goals_classify_as_safe(self):
        self.assertEqual(
            _classify_step_risk("browser_control", "Search for hotels in Guam."),
            "safe",
        )
        self.assertEqual(
            _classify_step_risk(
                "browser_control", "Compare the prices of these three hotels.",
            ),
            "safe",
        )
        self.assertEqual(
            _classify_step_risk("ui_control", "Open Notepad and read its contents."),
            "safe",
        )

    def test_web_search_is_always_safe_by_construction(self):
        self.assertEqual(
            _classify_step_risk("web_search", "Buy the cheapest flight to Guam."),
            "safe",
        )

    def test_committing_sub_goals_classify_as_consequential(self):
        self.assertEqual(
            _classify_step_risk("browser_control", "Book the best hotel."), "consequential",
        )
        self.assertEqual(
            _classify_step_risk("browser_control", "Send this message to the seller."),
            "consequential",
        )
        self.assertEqual(
            _classify_step_risk("ui_control", "Delete the old report file."),
            "consequential",
        )

    def test_payment_sub_goals_classify_as_payment_not_merely_consequential(self):
        self.assertEqual(
            _classify_step_risk("browser_control", "Buy the cheapest flight to Guam."),
            "payment",
        )
        self.assertEqual(
            _classify_step_risk("browser_control", "Complete the purchase for this order."),
            "payment",
        )

    def test_payment_check_only_applies_to_browser_control(self):
        # ui_control has no payment-specific tier (windows_ui_control.py
        # doesn't distinguish one) -- "buy"/"purchase" there still lands
        # on the general committing tier, not "payment".
        self.assertEqual(
            _classify_step_risk("ui_control", "Buy the cheapest flight to Guam."),
            "consequential",
        )

    def test_dispatched_steps_carry_their_risk_level(self):
        web_search = FakeWebSearchExecutor(
            act_results=[BrowserActionPlanResult("done", "Found hotels.")],
        )
        planner, _, browser = _planner(
            responses=[
                {"capability": "web_search", "sub_goal": "Find hotels in Guam."},
                {"capability": "browser_control", "sub_goal": "Book the best hotel."},
            ],
            browser_results=[
                BrowserActionPlanResult(
                    "needs_confirmation", "Confirm booking the best hotel?",
                    pending=BrowserPendingConfirmation(
                        tab_index=0, element_id="e0", element_label="Book",
                        url="https://hotels.example",
                    ),
                ),
            ],
            web_search_action_planner=web_search,
        )

        result = planner.run("Find hotels in Guam and book the best one.")

        self.assertEqual(result.status, "needs_confirmation")
        risk_by_step = [s.step.risk_level for s in result.task_state.completed_steps]
        self.assertEqual(risk_by_step, ["safe", "consequential"])
        self.assertEqual(result.pending_step.risk_level, "consequential")


class DeterministicDiscoveryPolicyTests(unittest.TestCase):
    """The production policy must pause before expensive browser work.

    These are deliberately planner-level tests rather than a prompt snapshot:
    a task that returns an offer must not consume a model decision or dispatch
    a search/browser step, and a preference-only reply must resume the exact
    same TaskState with its filters intact.
    """

    def test_hotel_shortlist_pauses_before_any_model_or_browser_work(self):
        planner, desktop, browser = _planner(
            responses=[],
            computer_control_mode=FakeComputerControlMode(True),
            discovery_policy=TaskDiscoveryPolicy(),
        )

        result = planner.run("Give me a shortlist of hotels in Seoul.")

        self.assertEqual(result.status, "needs_strategy_choice")
        self.assertEqual(result.task_state.step_count, 0)
        self.assertEqual(planner.client.calls, [])
        self.assertEqual(desktop.act_calls, [])
        self.assertEqual(browser.act_calls, [])
        self.assertIn("booking listings", result.summary)

    def test_preference_reply_keeps_the_task_and_requires_live_verification(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll compare live hotel listings.",
                    # The local model may call this casual discovery, but an
                    # accepted live-research choice is authoritative.
                    "verification_level": "discover",
                    "specialized_source_offer": "",
                },
                {
                    "capability": "browser_control",
                    "sub_goal": "Search the fixed search engine and read observed hotel listings.",
                },
                {
                    "done": True,
                    "summary": "Hotel shortlist ready.",
                },
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Observed three hotel listings."),
            ],
            computer_control_mode=FakeComputerControlMode(True),
            preview_enabled=True,
            discovery_policy=TaskDiscoveryPolicy(),
        )

        offered = planner.run("Give me a shortlist of hotels in Seoul.")
        result = planner.continue_with_strategy(
            offered.task_state,
            accepted=True,
            preference_update=(
                "Under ₩200,000 near Hongdae, 2026-09-10 to 2026-09-13."
            ),
        )

        self.assertEqual(result.status, "done")
        self.assertTrue(result.task_state.specialized_source_accepted)
        self.assertEqual(result.task_state.verification_level, "verify")
        self.assertEqual(result.task_state.preferences["budget"], "₩200,000")
        self.assertEqual(result.task_state.preferences["area"], "Hongdae")
        self.assertEqual(
            result.task_state.preferences["dates"],
            "2026-09-10 to 2026-09-13",
        )
        self.assertIn("Under ₩200,000 near Hongdae", result.task_state.preferences["additional_preferences"])
        self.assertEqual(len(browser.act_calls), 1)
        # A planner-generated sub-goal cannot turn its own source wording
        # into a direct third-party navigation authority.
        self.assertEqual(browser.act_kwargs, [{"allow_direct_navigation": False}])

    def test_accepting_live_hotel_research_without_dates_asks_for_dates(self):
        planner, _, browser = _planner(
            responses=[],
            computer_control_mode=FakeComputerControlMode(True),
            discovery_policy=TaskDiscoveryPolicy(),
        )
        offered = planner.run("Book me a hotel in Guam.")

        result = planner.continue_with_strategy(
            offered.task_state, accepted=True,
        )

        self.assertEqual(result.status, "needs_strategy_choice")
        self.assertIn("check-in", result.summary)
        self.assertEqual(browser.act_calls, [])

    def test_used_gpu_research_passes_a_secondhand_host_scope_to_browser(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll check Korean second-hand listings.",
                    "verification_level": "verify",
                    "specialized_source_offer": "",
                },
                {
                    "capability": "browser_control",
                    "sub_goal": "Find the requested RTX 5080 listings.",
                },
                {"done": True, "summary": "Found used RTX 5080 listings."},
            ],
            browser_results=[
                BrowserActionPlanResult("done", "Observed used RTX 5080 listings."),
            ],
            computer_control_mode=FakeComputerControlMode(True),
            preview_enabled=True,
            discovery_policy=TaskDiscoveryPolicy(),
            user_locale=UserLocale(country="KR", city="Seoul"),
        )

        offered = planner.run("Find the cheapest second-hand RTX 5080 in Korea.")
        result = planner.continue_with_strategy(offered.task_state, accepted=True)

        self.assertEqual(result.status, "done")
        self.assertEqual(offered.task_state.discovery_category, "secondhand")
        self.assertEqual(
            browser.act_kwargs[0]["allowed_hosts"],
            ("daangn.com", "bunjang.co.kr", "joongna.com"),
        )
        self.assertNotIn("danawa.com", browser.act_kwargs[0]["allowed_hosts"])

    def test_control_off_offers_a_truthful_overview_and_never_runs_browser(self):
        web_search = FakeWebSearchExecutor(
            act_results=[BrowserActionPlanResult("done", "Found overview results.")],
        )
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["web_search"],
                    "preferences": {},
                    "plan_preview": "I'll use a quick overview.",
                    "verification_level": "discover",
                    "specialized_source_offer": "",
                },
                {"capability": "web_search", "sub_goal": "Find a quick hotel overview."},
                {"done": True, "summary": "Quick overview ready."},
            ],
            computer_control_mode=FakeComputerControlMode(False),
            web_search_action_planner=web_search,
            preview_enabled=True,
            discovery_policy=TaskDiscoveryPolicy(),
        )

        offered = planner.run("Best restaurants to go in Seoul.")
        result = planner.continue_with_strategy(offered.task_state, accepted=True)

        self.assertEqual(offered.status, "needs_strategy_choice")
        self.assertIn("Desktop Control Mode is off", offered.summary)
        self.assertEqual(result.status, "done")
        self.assertFalse(result.task_state.specialized_source_accepted)
        self.assertEqual(browser.act_calls, [])
        self.assertEqual(web_search.act_calls, ["Find a quick hotel overview."])

    def test_followup_candidates_bypass_a_second_source_choice(self):
        candidate = ExtractedItem(
            "Hotel Cappuccino", {"price": "₩150,000"},
            source_type="browser_observed",
        )
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll check the existing candidate.",
                    "verification_level": "discover",
                    "specialized_source_offer": "",
                },
                {"done": True, "summary": "Hotel Cappuccino is the cheaper one."},
            ],
            computer_control_mode=FakeComputerControlMode(True),
            preview_enabled=True,
            discovery_policy=TaskDiscoveryPolicy(),
        )

        result = planner.run(
            "Which of those hotels is cheaper?",
            initial_information=("Hotel Cappuccino costs ₩150,000.",),
            initial_items=(candidate,),
        )

        self.assertEqual(result.status, "done")
        self.assertTrue(result.task_state.is_follow_up)
        self.assertEqual(result.task_state.verification_level, "verify")
        self.assertEqual(browser.act_calls, [])
        planning_prompt = planner.client.calls[-1]["messages"][0]["content"]
        self.assertIn("Hotel Cappuccino", planning_prompt)


class TaskStrategyOfferTests(unittest.TestCase):
    """The strategy-offer checkpoint: before the first step of a goal that
    could benefit from a specific specialized website, _preview() may
    offer to check one directly instead of just picking a capability and
    going. A distinct, earlier decision from risk classification above --
    "how much effort to spend", not "is this action risky"."""

    def test_preview_offer_returns_needs_strategy_choice_before_any_step(self):
        planner, desktop, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll look into hotels in Seoul.",
                    "verification_level": "discover",
                    "specialized_source_offer": (
                        "I could check a hotel booking site directly for "
                        "better filtering by price and location -- want "
                        "me to, or is a quick overview enough?"
                    ),
                },
            ],
            preview_enabled=True,
        )

        result = planner.run("Give me a shortlist of hotels in Seoul.")

        self.assertEqual(result.status, "needs_strategy_choice")
        self.assertEqual(
            result.summary,
            "I could check a hotel booking site directly for better "
            "filtering by price and location -- want me to, or is a "
            "quick overview enough?",
        )
        self.assertEqual(result.task_state.step_count, 0)
        self.assertEqual(desktop.act_calls, [])
        self.assertEqual(browser.act_calls, [])
        self.assertEqual(result.task_state.specialized_source_offer, result.summary)
        self.assertFalse(result.task_state.specialized_source_accepted)

    def test_offer_suppressed_when_browser_control_disabled(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["web_search"],
                    "preferences": {},
                    "plan_preview": "I'll look into hotels in Seoul.",
                    "verification_level": "discover",
                    "specialized_source_offer": "I could check a booking site...",
                },
                {"capability": "web_search", "sub_goal": "Find hotels in Seoul."},
                {"done": True, "summary": "Found hotels."},
            ],
            web_search_action_planner=FakeWebSearchExecutor(
                act_results=[BrowserActionPlanResult("done", "Found hotels.")],
            ),
            browser_control_enabled=False,
            preview_enabled=True,
        )

        result = planner.run("Give me a shortlist of hotels in Seoul.")

        self.assertEqual(result.status, "done")
        self.assertEqual(browser.act_calls, [])

    def test_continue_with_strategy_accepted_steers_the_next_prompt(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll look into hotels in Seoul.",
                    "verification_level": "discover",
                    "specialized_source_offer": "I could check a booking site...",
                },
                {"capability": "browser_control", "sub_goal": "Check a booking site."},
            ],
            browser_results=[BrowserActionPlanResult("done", "Checked.")],
            preview_enabled=True,
        )
        preview_result = planner.run("Give me a shortlist of hotels in Seoul.")

        planner.continue_with_strategy(preview_result.task_state, accepted=True)

        self.assertTrue(preview_result.task_state.specialized_source_accepted)
        self.assertEqual(preview_result.task_state.verification_level, "verify")
        planning_prompt = planner.client.calls[-1]["messages"][0]["content"]
        self.assertIn(
            "The user accepted checking a specialized website directly",
            planning_prompt,
        )

    def test_continue_with_strategy_declined_leaves_the_prompt_unchanged(self):
        planner, _, browser = _planner(
            responses=[
                {
                    "capabilities_needed": ["web_search", "browser_control"],
                    "preferences": {},
                    "plan_preview": "I'll look into hotels in Seoul.",
                    "verification_level": "discover",
                    "specialized_source_offer": "I could check a booking site...",
                },
                {"capability": "web_search", "sub_goal": "Find hotels in Seoul."},
            ],
            web_search_action_planner=FakeWebSearchExecutor(
                act_results=[BrowserActionPlanResult("done", "Found hotels.")],
            ),
            preview_enabled=True,
        )
        preview_result = planner.run("Give me a shortlist of hotels in Seoul.")

        planner.continue_with_strategy(preview_result.task_state, accepted=False)

        self.assertFalse(preview_result.task_state.specialized_source_accepted)
        self.assertEqual(preview_result.task_state.specialized_source_offer, "")
        self.assertEqual(preview_result.task_state.verification_level, "discover")
        planning_prompt = planner.client.calls[-1]["messages"][0]["content"]
        self.assertNotIn(
            "The user accepted checking a specialized website directly",
            planning_prompt,
        )


if __name__ == "__main__":
    unittest.main()
