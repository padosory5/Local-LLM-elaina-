import unittest
from unittest.mock import patch

from brain.desktop_action_planner import (
    DesktopActionPlanner,
    DesktopSurfaceContext,
)
from tools.computer_control import ComputerActionResult
from tools.windows_ui_control import UIActionResult
from tools.windows_ui_observer import ControlInfo, WindowInfo, WindowObservation


def _tool_call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


def _message(*, content="", tool_calls=None):
    return {"message": {"content": content, "tool_calls": tool_calls}}


class FakeClient:
    """Returns one queued response per .chat() call, ignoring arguments."""

    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, **kwargs):
        return self._responses.pop(0)


class FakeObserver:
    def __init__(self, windows_by_call=None):
        # A list of tuples-of-WindowInfo, one entry consumed per find_window
        # call, so a test can simulate a window appearing after N polls.
        self._windows_by_call = list(windows_by_call or [])
        self.find_window_calls = []

    def find_window(self, hint):
        self.find_window_calls.append(hint)
        if not self._windows_by_call:
            return None
        windows = self._windows_by_call.pop(0)
        return windows[0] if windows else None

    @staticmethod
    def _safe_text(window):
        return window.title


class FakeComputerControl:
    def __init__(self, open_app_result):
        self.open_app_result = open_app_result
        self.open_app_calls = []

    def open_app(self, target):
        self.open_app_calls.append(target)
        return self.open_app_result


class DesktopActionPlannerOpenAppTests(unittest.TestCase):
    def test_open_app_waits_for_the_window_and_reports_its_real_title(self):
        opened = ComputerActionResult(
            "opened", "Notepad", "Notepad", "Opened Notepad.",
            operation="open_app",
        )
        observer = FakeObserver(windows_by_call=[
            (),
            (WindowInfo(title="제목 없음 - 메모장"),),
        ])
        planner = DesktopActionPlanner(
            client=FakeClient([
                _message(tool_calls=[_tool_call("open_app", app="Notepad")]),
                _message(content="Notepad is open now."),
            ]),
            model="qwen3:8b",
            keep_alive=-1,
            observer=observer,
            control=object(),
            computer_control=FakeComputerControl(opened),
        )

        with patch("brain.desktop_action_planner.time.sleep"):
            result = planner.act("open Notepad")

        self.assertEqual(result.status, "done")
        self.assertIn("제목 없음 - 메모장", result.steps_taken[0])

    def test_open_app_reports_failure_without_waiting(self):
        not_found = ComputerActionResult(
            "not_found", "Nonexistent", "", "I couldn't find Nonexistent.",
            operation="open_app",
        )
        observer = FakeObserver()
        planner = DesktopActionPlanner(
            client=FakeClient([
                _message(tool_calls=[_tool_call("open_app", app="Nonexistent")]),
                _message(content="I couldn't open that app."),
            ]),
            model="qwen3:8b",
            keep_alive=-1,
            observer=observer,
            control=object(),
            computer_control=FakeComputerControl(not_found),
        )

        with patch("brain.desktop_action_planner.time.sleep") as sleep:
            planner.act("open Nonexistent")

        sleep.assert_not_called()
        # One read-only preflight checks whether the app is already open; a
        # catalog launch failure must not enter the appearance polling loop.
        self.assertEqual(observer.find_window_calls, ["Nonexistent"])

    def test_open_app_ambiguity_keeps_the_specific_did_you_mean_question(self):
        # Regression: the catalog's fuzzy match for a likely STT mishearing
        # ("battle nest" -> "Battle.net Launcher") returns status=ambiguous
        # with a real, specific question -- but the planner's generic
        # ambiguous-status message ("I found multiple matching controls")
        # was overwriting it, even though this ambiguity was never about a
        # UI control at all.
        ambiguous = ComputerActionResult(
            "ambiguous", "battle nest", "", "Did you mean Battle.net Launcher?",
            operation="open_app", candidates=("Battle.net Launcher",),
        )
        observer = FakeObserver()
        planner = DesktopActionPlanner(
            client=FakeClient([
                _message(tool_calls=[_tool_call("open_app", app="battle nest")]),
            ]),
            model="qwen3:8b",
            keep_alive=-1,
            observer=observer,
            control=object(),
            computer_control=FakeComputerControl(ambiguous),
        )

        result = planner.act("open battle nest")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.summary, "Did you mean Battle.net Launcher?")

    def test_open_app_reports_when_the_window_never_appears(self):
        opened = ComputerActionResult(
            "opened", "SlowApp", "SlowApp", "Opened SlowApp.",
            operation="open_app",
        )
        observer = FakeObserver(windows_by_call=[])
        planner = DesktopActionPlanner(
            client=FakeClient([
                _message(tool_calls=[_tool_call("open_app", app="SlowApp")]),
                _message(content="SlowApp is opening."),
            ]),
            model="qwen3:8b",
            keep_alive=-1,
            observer=observer,
            control=object(),
            computer_control=FakeComputerControl(opened),
        )

        with patch("brain.desktop_action_planner.time.sleep") as sleep:
            planner.act("open SlowApp")

        self.assertGreater(sleep.call_count, 0)
        self.assertGreater(len(observer.find_window_calls), 1)


class PlannerObserver:
    def __init__(self, *, active=None, observations=None):
        self.active = active
        self.observations = list(observations or [])
        self.describe_calls = []

    def get_active_window(self):
        return self.active

    def list_windows(self):
        return (self.active,) if self.active else ()

    def find_window(self, target):
        if isinstance(target, WindowInfo):
            return target
        if self.active and str(target).casefold() in self.active.title.casefold():
            return self.active
        return None

    def describe_window(self, target):
        self.describe_calls.append(target)
        if self.observations:
            return self.observations.pop(0)
        return WindowObservation(
            "observed",
            title=self.active.title if self.active else str(target),
            controls=(ControlInfo("Button", "Settings", is_actionable=True),),
        )


class PlannerControl:
    def __init__(self, *, typed_verified=True, clicked_verified=True):
        self.typed_verified = typed_verified
        self.clicked_verified = clicked_verified
        self.type_calls = []
        self.click_calls = []

    def focus_window(self, target):
        return UIActionResult(
            "focused", "Focused the window.", verified=True,
        )

    def click_control(self, target, control, *, confirmed=False):
        self.click_calls.append((target, control, confirmed))
        return UIActionResult(
            "clicked",
            f"Clicked {control}.",
            window_title=(target.title if isinstance(target, WindowInfo) else str(target)),
            control_name=control,
            verified=self.clicked_verified,
        )

    def type_text(self, target, control, text):
        self.type_calls.append((target, control, text))
        return UIActionResult(
            "typed",
            f"Typed into {control}.",
            window_title=(target.title if isinstance(target, WindowInfo) else str(target)),
            control_name=control,
            verified=self.typed_verified,
        )

    def select_option(self, target, control, option):
        return UIActionResult(
            "selected", f"Selected {option}.", verified=True,
        )

    def scroll_control(self, target, control, direction):
        return UIActionResult(
            "scrolled", f"Scrolled {direction}.", verified=True,
        )


class NeverOpenComputerControl:
    def __init__(self):
        self.open_app_calls = []

    def open_app(self, target):
        self.open_app_calls.append(target)
        raise AssertionError("A locked surface must never open another app.")


def _surface_planner(responses, observer, control, computer_control=None):
    return DesktopActionPlanner(
        client=FakeClient(responses),
        model="qwen3:8b",
        keep_alive=-1,
        observer=observer,
        control=control,
        computer_control=computer_control or NeverOpenComputerControl(),
    )


class DesktopActionPlannerStabilizationTests(unittest.TestCase):
    def test_current_page_scope_rejects_opening_windows_settings(self):
        github = WindowInfo(
            title="sample/repository - Google Chrome",
            app_name="Chrome_WidgetWin_1",
            is_active=True,
            handle=44,
            process_id=55,
            class_name="Chrome_WidgetWin_1",
        )
        observer = PlannerObserver(active=github)
        computer = NeverOpenComputerControl()
        planner = _surface_planner(
            [_message(tool_calls=[_tool_call("open_app", app="Settings")])],
            observer,
            PlannerControl(),
            computer,
        )

        result = planner.act(
            "Click Settings on this page.",
            surface_context=DesktopSurfaceContext.from_window_info(
                github, browser_page_cue=True,
            ),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "surface_violation")
        self.assertTrue(result.surface_context.lock_to_surface)
        self.assertEqual(computer.open_app_calls, [])

    def test_in_it_scope_rejects_opening_an_unrelated_app(self):
        github = WindowInfo(
            title="sample/repository - Google Chrome",
            app_name="Chrome_WidgetWin_1",
            is_active=True,
            handle=45,
            process_id=56,
            class_name="Chrome_WidgetWin_1",
        )
        observer = PlannerObserver(active=github)
        computer = NeverOpenComputerControl()
        planner = _surface_planner(
            [_message(tool_calls=[_tool_call("open_app", app="Settings")])],
            observer,
            PlannerControl(),
            computer,
        )

        result = planner.act(
            "Click Settings in it.",
            surface_context=DesktopSurfaceContext.from_window_info(
                github, browser_page_cue=True,
            ),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "surface_violation")
        self.assertTrue(result.surface_context.lock_to_surface)
        self.assertEqual(computer.open_app_calls, [])

    def test_verified_spotify_search_finishes_without_sentence_matching(self):
        spotify = WindowInfo(
            title="Spotify Premium",
            app_name="Chrome_WidgetWin_1",
            is_active=True,
            handle=81,
        )
        observation = WindowObservation(
            "observed",
            title=spotify.title,
            controls=(ControlInfo("Edit", "Search", is_actionable=True),),
        )
        observer = PlannerObserver(active=spotify, observations=[observation])
        control = PlannerControl(typed_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="Spotify", control="Search", text="BTS",
                ),
            ]),
            _message(content="BTS is in Spotify search."),
        ], observer, control)

        result = planner.act("Find BTS using Spotify's search.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.action_steps, 1)
        self.assertEqual(result.failure_code, "")
        self.assertEqual(control.type_calls[0][2], "BTS")

    def test_unverified_last_click_cannot_be_reported_as_complete(self):
        window = WindowInfo("GitHub - Chrome", is_active=True, handle=90)
        observer = PlannerObserver(active=window)
        control = PlannerControl(clicked_verified=None)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="GitHub"),
            ]),
            _message(tool_calls=[
                _tool_call("click_control", window="GitHub", control="Settings"),
            ]),
            _message(content="Settings opened."),
            _message(tool_calls=[
                _tool_call("describe_window", window="GitHub"),
            ]),
            _message(content="Settings opened."),
        ], observer, control)

        result = planner.act("Click Settings in GitHub.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "unverified_outcome")
        self.assertNotEqual(result.summary, "Settings opened.")
        self.assertEqual(len(observer.describe_calls), 2)

    def test_playback_goal_cannot_finish_after_verified_search_input(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=91)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(typed_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text",
                    window="Spotify",
                    control="Search",
                    text="Dynamite",
                ),
            ]),
            _message(content="Dynamite is in Spotify search."),
            _message(content="Dynamite is ready."),
        ], observer, control)

        result = planner.act("Play Dynamite in Spotify for me.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "goal_operation_incomplete")
        self.assertEqual(len(control.click_calls), 0)

    def test_playback_goal_can_continue_from_search_to_activation(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=92)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(typed_verified=True, clicked_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text",
                    window="Spotify",
                    control="Search",
                    text="Dynamite",
                ),
            ]),
            _message(content="Dynamite is in Spotify search."),
            _message(tool_calls=[
                _tool_call(
                    "click_control",
                    window="Spotify",
                    control="Dynamite",
                ),
            ]),
            _message(content="Dynamite is playing."),
        ], observer, control)

        result = planner.act("Play Dynamite in Spotify for me.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.action_steps, 2)
        self.assertEqual(control.click_calls[0][1], "Dynamite")

    def test_wrong_verified_click_cannot_complete_playback(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=95)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(clicked_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call("click_control", window="Spotify", control="Home"),
            ]),
            _message(content="Dynamite is playing."),
            _message(content="Dynamite is playing."),
        ], observer, control)

        result = planner.act("Play Dynamite in Spotify.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "goal_operation_incomplete")
        self.assertEqual(control.click_calls[0][1], "Home")

    def test_put_on_paraphrase_still_requires_activation(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=96)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(typed_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="Spotify", control="Search",
                    text="Dynamite",
                ),
            ]),
            _message(content="Dynamite is playing."),
            _message(content="Dynamite is playing."),
        ], observer, control)

        result = planner.act("Put on Dynamite in Spotify.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "goal_operation_incomplete")

    def test_original_goal_restores_operation_lost_by_router_paraphrase(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=98)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(typed_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="Spotify", control="Search",
                    text="Dynamite",
                ),
            ]),
            _message(content="Dynamite is playing."),
            _message(content="Dynamite is playing."),
        ], observer, control)

        result = planner.act(
            "Spotify\nOriginal user request: Put on Dynamite in Spotify."
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "goal_operation_incomplete")

    def test_wrong_search_value_cannot_complete_search_goal(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=97)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(typed_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="Spotify", control="Search",
                    text="Metallica",
                ),
            ]),
            _message(content="BTS is in search."),
            _message(content="BTS is in search."),
        ], observer, control)

        result = planner.act("Search for BTS in Spotify.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "goal_operation_incomplete")

    def test_partial_multiword_search_value_is_not_complete(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=99)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(typed_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="Spotify", control="Search",
                    text="BTS",
                ),
            ]),
            _message(content="BTS Dynamite is in search."),
            _message(content="BTS Dynamite is in search."),
        ], observer, control)

        result = planner.act("Search for BTS Dynamite in Spotify.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "goal_operation_incomplete")

    def test_unknown_click_verification_gets_one_changed_state_check(self):
        window = WindowInfo("GitHub - Chrome", is_active=True, handle=93)
        before = WindowObservation(
            "observed",
            title=window.title,
            controls=(ControlInfo("Button", "Settings", is_actionable=True),),
        )
        after = WindowObservation(
            "observed",
            title=window.title,
            controls=(ControlInfo("Heading", "Repository settings"),),
        )
        observer = PlannerObserver(active=window, observations=[before, after])
        control = PlannerControl(clicked_verified=None)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="GitHub"),
            ]),
            _message(tool_calls=[
                _tool_call("click_control", window="GitHub", control="Settings"),
            ]),
            _message(content="Settings opened."),
            _message(tool_calls=[
                _tool_call("describe_window", window="GitHub"),
            ]),
            _message(content="Repository settings opened."),
        ], observer, control)

        result = planner.act("Click Settings in GitHub.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(observer.describe_calls), 2)

    def test_window_list_cannot_verify_an_unverified_click(self):
        window = WindowInfo("GitHub - Chrome", is_active=True, handle=94)
        before = WindowObservation(
            "observed",
            title=window.title,
            controls=(ControlInfo("Button", "Settings", is_actionable=True),),
        )
        observer = PlannerObserver(active=window, observations=[before])
        control = PlannerControl(clicked_verified=None)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="GitHub"),
            ]),
            _message(tool_calls=[
                _tool_call("click_control", window="GitHub", control="Settings"),
            ]),
            _message(content="Settings opened."),
            # A top-level window list is a different observation format, but
            # it proves nothing about whether the scoped page changed.
            _message(tool_calls=[_tool_call("list_windows")]),
            _message(content="Settings opened."),
        ], observer, control)

        result = planner.act("Click Settings in GitHub.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "unverified_outcome")

    def test_repeated_observation_gets_one_recovery_then_stops_specifically(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=101)
        observer = PlannerObserver(active=spotify)
        repeated = _message(tool_calls=[
            _tool_call("describe_window", window="Spotify"),
        ])
        planner = _surface_planner(
            [repeated, repeated, repeated],
            observer,
            PlannerControl(),
        )

        result = planner.act("Search for BTS in Spotify.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "repeated_step")
        self.assertTrue(result.recovery_used)
        self.assertNotIn("too many steps", result.summary.casefold())
        self.assertEqual(len(observer.describe_calls), 1)

    def test_same_action_can_retry_after_the_scoped_ui_state_changes(self):
        window = WindowInfo("Spotify", is_active=True, handle=102)
        before = WindowObservation(
            "observed",
            title=window.title,
            controls=(ControlInfo("Button", "Dynamite", is_actionable=True),),
        )
        after = WindowObservation(
            "observed",
            title=window.title,
            controls=(ControlInfo("Button", "Dynamite", value="ready"),),
        )
        observer = PlannerObserver(active=window, observations=[before, after])

        class RetryControl(PlannerControl):
            def click_control(self, target, control, *, confirmed=False):
                self.click_calls.append((target, control, confirmed))
                if len(self.click_calls) == 1:
                    return UIActionResult(
                        "not_found", "The result was still loading.",
                        verified=False,
                    )
                return UIActionResult(
                    "clicked", "Clicked Dynamite.", verified=True,
                )

        control = RetryControl()
        click = _message(tool_calls=[
            _tool_call("click_control", window="Spotify", control="Dynamite"),
        ])
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            click,
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            click,
            _message(content="Dynamite is playing."),
        ], observer, control)

        result = planner.act("Play Dynamite in Spotify.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(control.click_calls), 2)

    def test_successful_toggle_is_not_repeated_after_state_change(self):
        window = WindowInfo("Spotify", is_active=True, handle=103)
        before = WindowObservation(
            "observed",
            title=window.title,
            controls=(ControlInfo("Button", "Mute", is_actionable=True),),
        )
        after = WindowObservation(
            "observed",
            title=window.title,
            controls=(ControlInfo("Button", "Unmute", is_actionable=True),),
        )
        observer = PlannerObserver(active=window, observations=[before, after])
        control = PlannerControl(clicked_verified=True)
        click = _message(tool_calls=[
            _tool_call("click_control", window="Spotify", control="Mute"),
        ])
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            click,
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            click,
            _message(content="Spotify is muted."),
        ], observer, control)

        result = planner.act("Click Mute in Spotify.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(control.click_calls), 1)

    def test_observation_may_repeat_after_an_action_to_verify_changed_state(self):
        notepad = WindowInfo("Untitled - Notepad", is_active=True, handle=8)
        before = WindowObservation(
            "observed",
            title=notepad.title,
            controls=(ControlInfo("Edit", "Text editor", value=""),),
        )
        after = WindowObservation(
            "observed",
            title=notepad.title,
            controls=(ControlInfo("Edit", "Text editor", value="grocery list"),),
        )
        observer = PlannerObserver(active=notepad, observations=[before, after])
        control = PlannerControl(typed_verified=None)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Notepad"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text",
                    window="Notepad",
                    control="Text editor",
                    text="grocery list",
                ),
            ]),
            _message(tool_calls=[
                _tool_call("describe_window", window="Notepad"),
            ]),
            _message(content="Entered the grocery list in Notepad."),
        ], observer, control)

        result = planner.act("Type grocery list in this Notepad window.")

        self.assertEqual(result.status, "done")
        self.assertEqual(len(observer.describe_calls), 2)

    def test_confirmed_click_uses_frozen_window_identity(self):
        github = WindowInfo(
            "GitHub - Chrome",
            is_active=True,
            handle=77,
            process_id=88,
            class_name="Chrome_WidgetWin_1",
        )
        control = PlannerControl(clicked_verified=True)
        planner = _surface_planner([], PlannerObserver(active=github), control)

        result = planner.resume_confirmed_click(
            window_title=github.title,
            control_name="Submit",
            window_snapshot=github,
        )

        self.assertEqual(result.status, "done")
        self.assertIs(control.click_calls[0][0], github)
        self.assertTrue(control.click_calls[0][2])

    def test_confirmed_click_with_unknown_verification_is_not_done(self):
        github = WindowInfo(
            "GitHub - Chrome",
            is_active=True,
            handle=78,
            process_id=89,
            class_name="Chrome_WidgetWin_1",
        )
        control = PlannerControl(clicked_verified=None)
        planner = _surface_planner([], PlannerObserver(active=github), control)

        result = planner.resume_confirmed_click(
            window_title=github.title,
            control_name="Submit",
            window_snapshot=github,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "unverified_outcome")
        self.assertNotIn("complete", result.summary.casefold())

    def test_final_model_failure_cannot_overturn_verified_local_success(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=31)
        observer = PlannerObserver(active=spotify)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("focus_window", window="Spotify"),
            ]),
            _message(content="I couldn't finish the Spotify request."),
        ], observer, PlannerControl())

        result = planner.act("Bring Spotify to the front.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.failure_code, "")
        self.assertNotIn("couldn't", result.summary.casefold())


if __name__ == "__main__":
    unittest.main()
