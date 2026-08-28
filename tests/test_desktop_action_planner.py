import unittest
from unittest.mock import patch

from brain.deliberation import interpret
from brain.desktop_action_planner import (
    DesktopActionPlanner,
    DesktopSurfaceContext,
    _completion_contract,
)
from tools.computer_control.computer_control import ComputerActionResult
from tools.computer_control.windows_ui_control import UIActionResult
from tools.computer_control.windows_ui_observer import (
    ControlInfo,
    WindowInfo,
    WindowObservation,
)


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

    def set_now_playing(self, title):
        """Rename the window the way a media app does when it starts a track."""
        self.active = WindowInfo(
            title=title,
            app_name=self.active.app_name if self.active else "",
            is_active=True,
            handle=self.active.handle if self.active else None,
        )

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
    def __init__(
        self,
        *,
        typed_verified=True,
        clicked_verified=True,
        resolved_name_for_id="",
        on_activate=None,
    ):
        self.typed_verified = typed_verified
        self.clicked_verified = clicked_verified
        # Called with the activated control's name, so a test can simulate
        # the app renaming its window once the track really starts.
        self.on_activate = on_activate
        # Simulates what a real element_id lookup would resolve to (see
        # WindowsUIObserver.resolve_control_by_id) when a test's tool call
        # supplies only an id and no semantic `control` text.
        self.resolved_name_for_id = resolved_name_for_id
        self.type_calls = []
        self.click_calls = []

    def focus_window(self, target):
        return UIActionResult(
            "focused", "Focused the window.", verified=True,
        )

    def click_control(self, target, control, *, confirmed=False, element_id=""):
        self.click_calls.append((target, control, confirmed, element_id))
        resolved_name = control or (
            self.resolved_name_for_id if element_id else ""
        )
        if self.on_activate is not None:
            self.on_activate(resolved_name)
        return UIActionResult(
            "clicked",
            f"Clicked {resolved_name or control}.",
            window_title=(target.title if isinstance(target, WindowInfo) else str(target)),
            control_name=resolved_name,
            verified=self.clicked_verified,
        )

    def type_text(self, target, control, text, *, element_id=""):
        self.type_calls.append((target, control, text, element_id))
        return UIActionResult(
            "typed",
            f"Typed into {control}.",
            window_title=(target.title if isinstance(target, WindowInfo) else str(target)),
            control_name=control,
            verified=self.typed_verified,
        )

    def select_option(self, target, control, option, *, element_id=""):
        return UIActionResult(
            "selected", f"Selected {option}.", verified=True,
        )

    def scroll_control(self, target, control, direction, *, element_id=""):
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
        sleeper=lambda _seconds: None,
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
        observer = PlannerObserver(active=spotify, observations=[
            WindowObservation(
                "observed", title="Spotify",
                controls=(ControlInfo("Edit", "Search", element_id="s1-e0"),),
            ),
            WindowObservation(
                "observed", title="Spotify",
                controls=(ControlInfo("Hyperlink", "Dynamite", element_id="s2-e0"),),
            ),
        ])
        control = PlannerControl(
            typed_verified=True,
            clicked_verified=True,
            on_activate=lambda name: observer.set_now_playing(f"{name} - BTS"),
        )
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
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "play_media_item",
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

    def test_click_control_tool_call_forwards_element_id_to_control(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=94)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(clicked_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call(
                    "click_control", window="Spotify", element_id="scan1-e3",
                ),
            ]),
            _message(content="Done."),
        ], observer, control)

        planner.act("Click that.")

        self.assertEqual(control.click_calls[0][3], "scan1-e3")

    def test_goal_completion_contract_is_satisfied_by_an_id_based_activation_click(
        self,
    ):
        # Regression test for the _action_completion_terms/resolved_
        # control_name fix: an element_id-only click supplies no semantic
        # "control" text at all, so goal-completion matching must fall
        # back to the real resolved name (what resolve_control_by_id
        # would have returned), not the empty tool-call argument -- or a
        # fully correct, verified click would be reported as incomplete.
        spotify = WindowInfo("Spotify", is_active=True, handle=95)
        observer = PlannerObserver(active=spotify, observations=[
            WindowObservation(
                "observed", title="Spotify",
                controls=(ControlInfo("Edit", "Search", element_id="s1-e0"),),
            ),
            WindowObservation(
                "observed", title="Spotify",
                controls=(ControlInfo("Hyperlink", "Dynamite", element_id="scan1-e4"),),
            ),
        ])
        control = PlannerControl(
            typed_verified=True,
            clicked_verified=True,
            resolved_name_for_id="Dynamite",
            on_activate=lambda name: observer.set_now_playing(f"{name} - BTS"),
        )
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
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "play_media_item", window="Spotify", element_id="scan1-e4",
                ),
            ]),
            _message(content="Dynamite is playing."),
        ], observer, control)

        result = planner.act("Play Dynamite in Spotify for me.")

        self.assertEqual(result.status, "done")
        self.assertEqual(control.click_calls[0][1], "")
        self.assertEqual(control.click_calls[0][3], "scan1-e4")

    def test_compound_spotify_search_and_play_keeps_the_title_and_artist(self):
        goal = (
            "Search for Bang Bang from IVE and open that music in Spotify "
            "to play for me."
        )
        contract = _completion_contract(goal)
        self.assertEqual(contract.operation, "activation")
        self.assertEqual(contract.subject_terms, frozenset({"bang", "ive"}))
        self.assertTrue(contract.subject_requires_full_match)

        spotify = WindowInfo("Spotify", is_active=True, handle=93)
        observer = PlannerObserver(active=spotify, observations=[
            WindowObservation(
                "observed", title="Spotify",
                controls=(ControlInfo("Edit", "Search", element_id="s1-e0"),),
            ),
            WindowObservation(
                "observed", title="Spotify",
                controls=(
                    ControlInfo("Hyperlink", "Bang Bang", element_id="s2-e0"),
                    ControlInfo("Hyperlink", "IVE", element_id="s2-e1"),
                ),
            ),
        ])
        control = PlannerControl(
            typed_verified=True,
            clicked_verified=True,
            on_activate=lambda name: observer.set_now_playing(f"{name} - IVE"),
        )
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="Spotify", control="Search",
                    text="Bang Bang IVE",
                ),
            ]),
            _message(content="The matching result is visible."),
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "play_media_item", window="Spotify", control="Bang Bang",
                ),
            ]),
            _message(content="Bang Bang by IVE is playing."),
        ], observer, control)

        result = planner.act(goal)

        self.assertEqual(result.status, "done")
        self.assertEqual(control.type_calls[0][2], "Bang Bang IVE")
        self.assertEqual(control.click_calls[0][1], "Bang Bang")

    def test_compound_spotify_goal_rejects_a_partial_search_before_generic_play(self):
        spotify = WindowInfo("Spotify", is_active=True, handle=94)
        observer = PlannerObserver(active=spotify)
        control = PlannerControl(typed_verified=True, clicked_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="Spotify"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="Spotify", control="Search",
                    text="Bang Bang",
                ),
            ]),
            _message(content="A result is visible."),
            _message(tool_calls=[
                _tool_call("click_control", window="Spotify", control="Play"),
            ]),
            _message(content="A song is playing."),
            _message(content="A song is playing."),
        ], observer, control)

        result = planner.act(
            "Search for Bang Bang from IVE and open that music in Spotify to play for me."
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "goal_operation_incomplete")

    def test_the_request_itself_is_never_typed_into_a_field(self):
        # Phase 1's acceptance test. The model is handed a sentence and a
        # keyboard; before the Goal boundary existed, it typed the sentence.
        notepad = WindowInfo("메모장", is_active=True, handle=130)
        observer = PlannerObserver(active=notepad)
        control = PlannerControl(typed_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="메모장"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="메모장", control="텍스트 편집기",
                    text="Type 'see you at six' in 메모장",
                ),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="메모장", control="텍스트 편집기",
                    text="see you at six",
                ),
            ]),
            _message(content="Typed it."),
        ], observer, control)

        result = planner.act("Type 'see you at six' in 메모장")

        self.assertEqual(result.status, "done")
        # The refused attempt never reached the driver at all.
        self.assertEqual(
            [call[2] for call in control.type_calls], ["see you at six"],
        )

    def test_a_planner_accepts_a_request_already_read_into_slots(self):
        notepad = WindowInfo("메모장", is_active=True, handle=131)
        observer = PlannerObserver(active=notepad)
        control = PlannerControl(typed_verified=True)
        planner = _surface_planner([
            _message(tool_calls=[
                _tool_call("describe_window", window="메모장"),
            ]),
            _message(tool_calls=[
                _tool_call(
                    "type_text", window="메모장", control="텍스트 편집기",
                    text="see you at six",
                ),
            ]),
            _message(content="Typed it."),
        ], observer, control)

        result = planner.act(interpret("Type 'see you at six' in 메모장"))

        self.assertEqual(result.status, "done")
        self.assertEqual(control.type_calls[0][2], "see you at six")

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

        # Clicking Home is preparation, not an activation: it is allowed to
        # happen (refusing every click is what once blocked Spotify's own
        # Search), but it can never satisfy a playback goal.
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "goal_operation_incomplete")
        self.assertEqual([call[1] for call in control.click_calls], ["Home"])

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
            def click_control(self, target, control, *, confirmed=False, element_id=""):
                self.click_calls.append((target, control, confirmed))
                if len(self.click_calls) == 1:
                    return UIActionResult(
                        "not_found", "The result was still loading.",
                        verified=False,
                    )
                observer.set_now_playing("Dynamite - BTS")
                return UIActionResult(
                    "clicked", "Clicked Dynamite.", verified=True,
                )

        control = RetryControl()
        click = _message(tool_calls=[
            _tool_call("play_media_item", window="Spotify", control="Dynamite"),
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
            element_id="scan1-e0",
        )

        self.assertEqual(result.status, "done")
        self.assertIs(control.click_calls[0][0], github)
        self.assertTrue(control.click_calls[0][2])
        self.assertEqual(control.click_calls[0][3], "scan1-e0")

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
            element_id="scan1-e0",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "unverified_outcome")
        self.assertNotIn("complete", result.summary.casefold())

    def test_confirmed_click_without_a_frozen_element_id_never_replays_by_name(self):
        checkout = WindowInfo(
            "Checkout", is_active=True, handle=79, process_id=90,
        )
        control = PlannerControl(clicked_verified=True)
        planner = _surface_planner([], PlannerObserver(active=checkout), control)

        result = planner.resume_confirmed_click(
            window_title=checkout.title,
            control_name="Submit Order",
            window_snapshot=checkout,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "missing_element_id")
        self.assertEqual(control.click_calls, [])

    def test_name_only_committing_click_is_reobserved_before_confirmation(self):
        class CommittingControl(PlannerControl):
            def click_control(self, target, control, *, confirmed=False, element_id=""):
                self.click_calls.append((target, control, confirmed, element_id))
                return UIActionResult(
                    "confirmation_required",
                    "Clicking 'Submit Order' needs confirmation first.",
                    window_title=(
                        target.title if isinstance(target, WindowInfo) else str(target)
                    ),
                    control_name="Submit Order",
                )

        checkout = WindowInfo(
            "Checkout", is_active=True, handle=80, process_id=91,
        )
        planner = _surface_planner(
            [], PlannerObserver(active=checkout), CommittingControl(),
        )

        execution = planner._run_tool_call(
            "click_control",
            {"window": "Checkout", "control": "Submit Order"},
            surface=DesktopSurfaceContext.from_window_info(checkout),
        )

        self.assertEqual(execution.status, "needs_reobservation")
        self.assertIsNone(execution.pending)
        self.assertIn("exact control id", execution.message)

    def test_confirmation_preserves_the_exact_element_id_for_resume(self):
        class CommittingControl(PlannerControl):
            def click_control(self, target, control, *, confirmed=False, element_id=""):
                self.click_calls.append((target, control, confirmed, element_id))
                if not confirmed:
                    return UIActionResult(
                        "confirmation_required",
                        "Clicking 'Submit Order' needs confirmation first.",
                        window_title=(
                            target.title if isinstance(target, WindowInfo) else str(target)
                        ),
                        control_name="Submit Order",
                    )
                return UIActionResult(
                    "clicked", "Clicked Submit Order.",
                    window_title=(
                        target.title if isinstance(target, WindowInfo) else str(target)
                    ),
                    control_name="Submit Order", verified=True,
                )

        checkout = WindowInfo(
            "Checkout", is_active=True, handle=81, process_id=92,
        )
        control = CommittingControl()
        planner = _surface_planner([], PlannerObserver(active=checkout), control)
        first = planner._run_tool_call(
            "click_control",
            {"window": "Checkout", "element_id": "scan9-e3"},
            surface=DesktopSurfaceContext.from_window_info(checkout),
        )

        self.assertIsNotNone(first.pending)
        self.assertEqual(first.pending.element_id, "scan9-e3")
        resumed = planner.resume_confirmed_click(
            window_title=first.pending.window_title,
            control_name=first.pending.control_name,
            window_snapshot=first.pending.window_snapshot,
            element_id=first.pending.element_id,
        )

        self.assertEqual(resumed.status, "done")
        self.assertEqual(control.click_calls[-1][3], "scan9-e3")

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


class DesktopActionPlannerToolSchemaTests(unittest.TestCase):
    def test_control_action_tools_expose_element_id_and_do_not_require_control(self):
        from brain.desktop_action_planner import _TOOLS

        by_name = {tool["function"]["name"]: tool["function"] for tool in _TOOLS}
        for name in (
            "click_control", "type_text", "click_then_type",
            "select_option", "scroll_control",
        ):
            params = by_name[name]["parameters"]
            self.assertIn("element_id", params["properties"], name)
            self.assertIn("control", params["properties"], name)
            self.assertNotIn("control", params["required"], name)
            self.assertIn("window", params["required"], name)


if __name__ == "__main__":
    unittest.main()
