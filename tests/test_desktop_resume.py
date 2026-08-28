import unittest

from brain.desktop_action_planner import (
    DesktopActionPlanner,
    DesktopSurfaceContext,
    PausedDesktopRun,
    _ACTION_FAMILY_BY_TOOL,
    _localised_control_terms,
)
from tools.computer_control.session_action_memory import SessionActionMemory
from tools.computer_control.windows_ui_control import UIActionResult


class _ScriptedClient:
    """Replays a fixed sequence of tool calls, recording what it was told."""

    def __init__(self, calls):
        self._calls = list(calls)
        self.prompts = []

    def chat(self, **kwargs):
        self.prompts.append(list(kwargs["messages"]))
        if not self._calls:
            return {"message": {"content": "Done.", "tool_calls": []}}
        name, arguments = self._calls.pop(0)
        return {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": name, "arguments": arguments}}
                ],
            }
        }


class _Control:
    """A UI control layer whose result for each call is scripted."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def _next(self, name):
        self.calls.append(name)
        if self.results:
            return self.results.pop(0)
        return UIActionResult("failed", "nothing scripted")

    def focus_window(self, target):
        return self._next("focus_window")

    def click_control(self, target, control, *, confirmed=False, element_id=""):
        return self._next("click_control")

    def type_text(self, target, control, text, *, confirmed=False, element_id=""):
        return self._next("type_text")

    def click_then_type(self, *args, **kwargs):
        return self._next("click_then_type")

    def select_option(self, *args, **kwargs):
        return self._next("select_option")

    def scroll_control(self, *args, **kwargs):
        return self._next("scroll_control")


class _Window:
    def __init__(self, title="Spotify Premium"):
        self.title = title
        self.app_name = "Spotify"
        self.is_active = True
        self.handle = 1
        self.process_id = 2
        self.class_name = "Chrome_WidgetWin_0"

    @property
    def identity(self):
        return f"hwnd:{self.handle}"


class _Observer:
    """Just enough observer for the planner to take a window snapshot."""

    available = True

    @staticmethod
    def list_windows():
        return (_Window(),)

    @staticmethod
    def find_window(target):
        return _Window()

    @staticmethod
    def _safe_text(window):
        return getattr(window, "title", "")


def _planner(client, control, *, session_actions=None):
    return DesktopActionPlanner(
        client=client,
        model="qwen3:8b",
        keep_alive=-1,
        observer=_Observer(),
        control=control,
        computer_control=object(),
        session_actions=session_actions,
    )


class PausedRunTests(unittest.TestCase):
    def test_progress_note_lists_verified_steps(self):
        paused = PausedDesktopRun(
            goal="play a song",
            steps_taken=("Opened Spotify.", "Typed 'Blinding Lights'."),
        )
        note = paused.progress_note
        self.assertIn("Opened Spotify.", note)
        self.assertIn("Typed 'Blinding Lights'.", note)
        self.assertIn("Do not repeat", note)

    def test_no_steps_means_no_note(self):
        self.assertEqual(PausedDesktopRun(goal="x").progress_note, "")


class InterruptionTests(unittest.TestCase):
    def test_a_takeover_stops_the_run_and_keeps_the_progress(self):
        client = _ScriptedClient([
            ("click_control", {"window": "Spotify", "control": "Play"}),
        ])
        control = _Control([
            UIActionResult(
                "user_took_over", "You moved the mouse, so I stopped.",
            ),
        ])
        result = _planner(client, control).act("play a song")
        self.assertEqual(result.status, "interrupted")
        self.assertIsNotNone(result.paused)
        self.assertEqual(result.paused.goal, "play a song")

    def test_an_interrupted_run_is_not_reported_as_done(self):
        client = _ScriptedClient([
            ("click_control", {"window": "Spotify", "control": "Play"}),
        ])
        control = _Control([UIActionResult("user_took_over", "stopped")])
        result = _planner(client, control).act("play a song")
        self.assertNotEqual(result.status, "done")


class ResumeTests(unittest.TestCase):
    def test_prior_steps_are_carried_into_the_resumed_run(self):
        client = _ScriptedClient([])
        planner = _planner(client, _Control([]))
        paused = PausedDesktopRun(
            goal="play a song", steps_taken=("Opened Spotify.",),
        )
        result = planner.act("play a song", prior_progress=paused)
        self.assertIn("Opened Spotify.", result.steps_taken)

    def test_the_model_is_told_what_is_already_done(self):
        client = _ScriptedClient([])
        planner = _planner(client, _Control([]))
        planner.act(
            "play a song",
            prior_progress=PausedDesktopRun(
                goal="play a song", steps_taken=("Typed 'Blinding Lights'.",),
            ),
        )
        user_turn = client.prompts[0][-1]["content"]
        self.assertIn("Typed 'Blinding Lights'.", user_turn)
        self.assertIn("Do not repeat", user_turn)

    def test_completed_families_survive_the_pause(self):
        # The completion contract is what refuses to accept a typed search
        # as playback. If the families were dropped on resume, a resumed run
        # could declare success without ever pressing play.
        client = _ScriptedClient([])
        planner = _planner(client, _Control([]))
        paused = PausedDesktopRun(
            goal="play Blinding Lights",
            steps_taken=("Typed 'Blinding Lights'.",),
            completed=(("text_input", frozenset({"blinding", "lights"})),),
        )
        result = planner.act("play Blinding Lights", prior_progress=paused)
        # Typing alone is preparation, so this must not come back done.
        self.assertNotEqual(result.status, "done")

    def test_a_fresh_run_carries_no_prior_steps(self):
        client = _ScriptedClient([])
        result = _planner(client, _Control([])).act("play a song")
        self.assertEqual(result.steps_taken, ())


class ActionMemoryTests(unittest.TestCase):
    def test_a_verified_action_is_remembered_for_later_follow_ups(self):
        memory = SessionActionMemory()
        client = _ScriptedClient([
            ("type_text", {
                "window": "Spotify Premium",
                "control": "Search",
                "text": "Blinding Lights",
            }),
        ])
        control = _Control([
            UIActionResult(
                "typed", "Typed it.", window_title="Spotify Premium",
                control_name="Search", verified=True,
            ),
        ])
        _planner(client, control, session_actions=memory).act(
            "search Spotify for Blinding Lights",
        )
        remembered = memory.last_subject()
        self.assertIsNotNone(remembered)
        self.assertEqual(remembered.subject, "Blinding Lights")

    def test_an_unverified_action_is_not_remembered(self):
        # Remembering it would let a later "stop it" act on a track that
        # was never actually started.
        memory = SessionActionMemory()
        client = _ScriptedClient([
            ("type_text", {
                "window": "Spotify", "control": "Search", "text": "Song A",
            }),
        ])
        control = _Control([
            UIActionResult(
                "typed", "Typed, unverified.", window_title="Spotify",
                control_name="Search", verified=False,
            ),
        ])
        _planner(client, control, session_actions=memory).act("search Spotify")
        self.assertIsNone(memory.last_subject())

    def test_memory_is_optional(self):
        client = _ScriptedClient([
            ("type_text", {
                "window": "Spotify", "control": "Search", "text": "Song A",
            }),
        ])
        control = _Control([
            UIActionResult(
                "typed", "ok", window_title="Spotify",
                control_name="Search", verified=True,
            ),
        ])
        # No session memory wired: must not raise.
        _planner(client, control).act("search Spotify")


class ToolSurfaceTests(unittest.TestCase):
    def test_press_key_is_not_evidence_of_completing_a_goal(self):
        # Pressing Enter submits a search; it is not proof the goal is done,
        # so it must not carry a completion family.
        self.assertNotIn("press_key", _ACTION_FAMILY_BY_TOOL)


if __name__ == "__main__":
    unittest.main()


class _NoModel:
    """A model that must never be consulted."""

    def chat(self, **kwargs):
        raise AssertionError("the model was consulted for a direct action")


class _TransportControl:
    """A control layer that only knows the app's real transport labels."""

    def __init__(self, present=("일시 정지하기",)):
        self.present = set(present)
        self.clicked = []
        self.focused = []

    def focus_window(self, target):
        self.focused.append(target)
        return UIActionResult("focused", f"Focused {target}.", window_title=str(target))

    def click_control(self, target, control, *, confirmed=False, element_id=""):
        if control in self.present:
            self.clicked.append(control)
            return UIActionResult(
                "clicked", f"Clicked {control}.",
                window_title=str(target), control_name=control, verified=True,
            )
        return UIActionResult("not_found", f"No control matches {control!r}.")


class DirectMediaControlTests(unittest.TestCase):
    """A bare "stop it" must not depend on the model finding the button."""

    def setUp(self):
        self.memory = SessionActionMemory()
        self.memory.record(
            app="Spotify Premium", family="activation",
            subject="Weightless by Marconi Union",
            window_title="Spotify Premium", control_name="재생하기",
            window_handle=4242,
        )

    def _planner(self, control):
        return DesktopActionPlanner(
            client=_NoModel(), model="qwen3:8b", keep_alive=-1,
            observer=_Observer(), control=control, computer_control=object(),
            session_actions=self.memory,
        )

    def test_stop_it_clicks_pause_without_asking_the_model(self):
        control = _TransportControl()
        result = self._planner(control).act(
            "Stop it.", surface_context=DesktopSurfaceContext(),
        )
        self.assertEqual(result.status, "done")
        self.assertEqual(control.clicked, ["일시 정지하기"])

    def test_the_reply_names_the_remembered_track(self):
        result = self._planner(_TransportControl()).act(
            "Stop it.", surface_context=DesktopSurfaceContext(),
        )
        self.assertIn("Weightless by Marconi Union", result.summary)

    def test_a_named_target_goes_through_the_normal_planner(self):
        # "Pause the video in Chrome" is a real request with its own target;
        # the direct path must not hijack it. The planner catches the model
        # error internally, so the observable proof is that nothing was
        # clicked directly.
        control = _TransportControl()
        result = self._planner(control).act(
            "Pause the video playing in Chrome please",
            surface_context=DesktopSurfaceContext(),
        )
        self.assertEqual(control.clicked, [])
        self.assertNotEqual(result.status, "done")

    def test_nothing_remembered_means_no_direct_action(self):
        control = _TransportControl()
        planner = DesktopActionPlanner(
            client=_NoModel(), model="qwen3:8b", keep_alive=-1,
            observer=_Observer(), control=control,
            computer_control=object(), session_actions=SessionActionMemory(),
        )
        result = planner.act("Stop it.", surface_context=DesktopSurfaceContext())
        self.assertEqual(control.clicked, [])
        self.assertNotEqual(result.status, "done")

    def test_no_transport_control_present_falls_back_to_the_planner(self):
        # Nothing is playing, so there is no pause button to click. That is
        # the model's problem to explain, not something to fake.
        control = _TransportControl(present=())
        result = self._planner(control).act(
            "Stop it.", surface_context=DesktopSurfaceContext(),
        )
        self.assertEqual(control.clicked, [])
        self.assertNotEqual(result.status, "done")


class LocalisedControlTests(unittest.TestCase):
    def test_korean_transport_labels_map_to_english_terms(self):
        self.assertEqual(_localised_control_terms("일시 정지하기"), {"pause"})
        self.assertEqual(_localised_control_terms("재생하기"), {"play"})

    def test_playlist_names_are_not_mistaken_for_play_buttons(self):
        # "재생 목록" is "playlist". A library full of them would otherwise
        # look like a wall of play buttons.
        self.assertEqual(
            _localised_control_terms("2023년 최애곡 추천 재생 목록"), set(),
        )
        self.assertEqual(_localised_control_terms("지금 재생 중 바입니다."), set())
