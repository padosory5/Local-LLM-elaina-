"""The deterministic "play exactly this track" path (Phase 4F.2).

Asking a small local model to aim at a Spotify search result is where this
system used to lose: it types the right query and then clicks "Bang Bang
Radio", a mix, or the nearest Play button -- each of which starts playing
*something*, which is why prompt wording never fixed it. These tests pin the
path that resolves the whole request from live state instead, and the two
properties that make it safe: the activation is a double-click on a row whose
name is exactly the title, and nothing is reported as playing until the app
itself says so.
"""

import unittest

from brain.desktop_action_planner import DesktopActionPlanner
from brain.media_target import MediaTarget
from tools.computer_control.windows_ui_control import UIActionResult
from tools.computer_control.windows_ui_observer import (
    ControlInfo,
    WindowInfo,
    WindowObservation,
)


_SPOTIFY = WindowInfo("Spotify Premium", is_active=True, handle=77)

_SEARCH_VIEW = WindowObservation(
    "observed",
    title="Spotify Premium",
    controls=(
        ControlInfo("Button", "Home", is_actionable=True, element_id="s1-e0"),
        ControlInfo("Edit", "Search", is_actionable=True, element_id="s1-e1"),
    ),
)


def _results(*, artist_near=True, exact_title=True):
    rows = [
        ControlInfo("Button", "Play", is_actionable=True, element_id="s2-e0"),
        ControlInfo("Hyperlink", "Bang Bang Radio", element_id="s2-e1"),
    ]
    if exact_title:
        rows.append(ControlInfo("Hyperlink", "Bang Bang", element_id="s2-e2"))
    if artist_near:
        rows.append(ControlInfo("Hyperlink", "IVE", element_id="s2-e3"))
    else:
        rows.append(ControlInfo("Hyperlink", "Jessie J", element_id="s2-e3"))
    return WindowObservation(
        "observed", title="Spotify Premium", controls=tuple(rows),
    )


class MediaObserver:
    """Enough live-window surface for the deterministic media path."""

    def __init__(self, observations, *, window=_SPOTIFY):
        self.window = window
        self._observations = list(observations)
        self.describe_calls = []

    def set_now_playing(self, title):
        self.window = WindowInfo(
            title=title, is_active=True, handle=self.window.handle,
        )

    def find_window(self, target):
        if isinstance(target, WindowInfo):
            return self.window if target.handle == self.window.handle else None
        if str(target).casefold() in self.window.title.casefold():
            return self.window
        return None

    def list_windows(self):
        return (self.window,)

    def describe_window(self, target):
        self.describe_calls.append(target)
        if self._observations:
            return self._observations.pop(0)
        return WindowObservation("observed", title=self.window.title)

    @staticmethod
    def _safe_text(window):
        return window.title


class MediaControl:
    """A driver that can double-click, and records how it was asked to act."""

    available = True

    def __init__(self, *, observer=None, plays=True, takeover_on_activate=False):
        self.observer = observer
        self.plays = plays
        self.takeover_on_activate = takeover_on_activate
        self.click_calls = []
        self.double_click_calls = []
        self.type_calls = []

    def focus_window(self, target):
        return UIActionResult("focused", "Focused Spotify.", verified=True)

    def type_text(self, target, control, text, *, confirmed=False, element_id=""):
        self.type_calls.append((control, text, element_id))
        return UIActionResult(
            "typed", f"Typed {text} into {control}.", control_name=control,
            verified=True,
        )

    def click_then_type(self, target, control, text, *, confirmed=False, element_id=""):
        self.type_calls.append((control, text, element_id))
        return UIActionResult(
            "typed", f"Clicked {control} and typed {text}.",
            control_name=control, verified=None,
        )

    def click_control(self, target, control, *, confirmed=False, element_id=""):
        self.click_calls.append((control, element_id))
        return UIActionResult(
            "clicked", f"Clicked {control}.", control_name=control,
            verified=True,
        )

    def double_click_control(self, target, control, *, confirmed=False, element_id=""):
        self.double_click_calls.append((control, element_id))
        if self.takeover_on_activate:
            return UIActionResult(
                "user_took_over", "You moved the mouse, so I stopped.",
            )
        if self.plays and self.observer is not None:
            self.observer.set_now_playing(f"{control} • IVE")
        return UIActionResult(
            "clicked", f"Double-clicked {control}.", control_name=control,
            verified=None,
        )


class NeverOpens:
    def open_app(self, target):
        raise AssertionError("Spotify was already open.")


class RecordingMemory:
    def __init__(self):
        self.records = []

    def record(self, **fields):
        self.records.append(fields)

    def recent_context(self):
        return []

    def last_subject(self):
        return None

    def last_action(self):
        return None


class SilentClient:
    def __init__(self):
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        raise AssertionError("The deterministic path must not ask the model.")


def _planner(observer, control, *, client=None, memory=None):
    return DesktopActionPlanner(
        client=client or SilentClient(),
        model="qwen3:8b",
        keep_alive=-1,
        observer=observer,
        control=control,
        computer_control=NeverOpens(),
        session_actions=memory,
        sleeper=lambda _seconds: None,
    )


class DeterministicPlayTests(unittest.TestCase):
    def test_exact_track_is_searched_then_double_clicked_and_verified(self):
        observer = MediaObserver([_SEARCH_VIEW, _results()])
        control = MediaControl(observer=observer)
        memory = RecordingMemory()
        planner = _planner(observer, control, memory=memory)

        result = planner.act("Play Bang Bang by IVE in Spotify.")

        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "Playing Bang Bang by IVE in Spotify.")
        # The artist narrows the search, and only the bare title is activated.
        self.assertEqual(control.type_calls[0][1], "Bang Bang IVE")
        self.assertEqual(control.double_click_calls, [("Bang Bang", "s2-e2")])
        # A single click would have opened the album instead of playing it.
        self.assertEqual(control.click_calls, [])
        self.assertEqual(memory.records[-1]["family"], "activation")
        self.assertEqual(memory.records[-1]["subject"], "Bang Bang")

    def test_the_model_is_never_consulted_for_a_named_track(self):
        observer = MediaObserver([_SEARCH_VIEW, _results()])
        control = MediaControl(observer=observer)
        client = SilentClient()

        result = _planner(observer, control, client=client).act(
            "Play Bang Bang by IVE in Spotify."
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(client.calls, 0)

    def test_a_radio_row_alone_is_never_activated(self):
        observer = MediaObserver([
            _SEARCH_VIEW, _results(exact_title=False),
            _results(exact_title=False), _results(exact_title=False),
        ])
        control = MediaControl(observer=observer)
        planner = _planner(observer, control)

        handed_back = planner._try_direct_media_play(
            "Play Bang Bang by IVE in Spotify.",
            MediaTarget("Spotify", "Bang Bang", "IVE"),
            planner._effective_surface("Play Bang Bang by IVE in Spotify.", None),
        )

        self.assertIsNone(handed_back)
        self.assertEqual(control.double_click_calls, [])
        self.assertEqual(control.click_calls, [])

    def test_a_same_titled_track_by_another_artist_is_not_played(self):
        observer = MediaObserver([
            _SEARCH_VIEW, _results(artist_near=False),
            _results(artist_near=False), _results(artist_near=False),
        ])
        control = MediaControl(observer=observer)
        planner = _planner(observer, control)

        handed_back = planner._try_direct_media_play(
            "Play Bang Bang by IVE in Spotify.",
            MediaTarget("Spotify", "Bang Bang", "IVE"),
            planner._effective_surface("Play Bang Bang by IVE in Spotify.", None),
        )

        self.assertIsNone(handed_back)
        self.assertEqual(control.double_click_calls, [])

    def test_playback_that_cannot_be_proved_is_not_claimed(self):
        observer = MediaObserver([
            _SEARCH_VIEW, _results(), _results(), _results(),
        ])
        control = MediaControl(observer=observer, plays=False)
        planner = _planner(observer, control)

        result = planner.act("Play Bang Bang by IVE in Spotify.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "playback_unverified")
        self.assertNotIn("Playing", result.summary)
        self.assertEqual(len(control.double_click_calls), 1)
        # A results list still shows "Bang Bang Radio", so the generic Play
        # on it is exactly the control that must not be pressed.
        self.assertEqual(control.click_calls, [])

    def test_an_item_that_opened_instead_of_playing_is_played_from_its_page(self):
        # Measured live: the title in a results row is a link, so activating
        # it can navigate rather than start playback. On the item's own page
        # -- exact title present, no near-miss rows -- Play means this item.
        item_page = WindowObservation(
            "observed",
            title="Spotify Premium",
            controls=(
                ControlInfo("Hyperlink", "Bang Bang", element_id="s3-e0"),
                ControlInfo("Hyperlink", "IVE", element_id="s3-e1"),
                ControlInfo("Button", "Play", is_actionable=True, element_id="s3-e2"),
            ),
        )
        observer = MediaObserver([_SEARCH_VIEW, _results(), item_page])
        control = MediaControl(observer=observer, plays=False)
        control.click_control = lambda target, name, *, confirmed=False, element_id="": (
            control.click_calls.append((name, element_id))
            or observer.set_now_playing("Bang Bang - IVE")
            or UIActionResult("clicked", f"Clicked {name}.", control_name=name)
        )
        planner = _planner(observer, control)

        result = planner.act("Play Bang Bang by IVE in Spotify.")

        self.assertEqual(result.status, "done")
        self.assertEqual(control.double_click_calls, [("Bang Bang", "s2-e2")])
        self.assertEqual(control.click_calls, [("Play", "s3-e2")])

    def test_a_generic_play_on_a_results_list_is_never_pressed(self):
        # The same fallback, refused: "Bang Bang Radio" is still on screen,
        # so this is a results list and Play would start the radio.
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
        control = MediaControl(observer=observer, plays=False)
        planner = _planner(observer, control)

        result = planner.act("Play Bang Bang by IVE in Spotify.")

        self.assertEqual(result.status, "failed")
        self.assertEqual(control.click_calls, [])

    def test_taking_the_mouse_back_stops_the_run_and_keeps_progress(self):
        observer = MediaObserver([_SEARCH_VIEW, _results()])
        control = MediaControl(observer=observer, takeover_on_activate=True)
        planner = _planner(observer, control)

        result = planner.act("Play Bang Bang by IVE in Spotify.")

        self.assertEqual(result.status, "interrupted")
        self.assertEqual(result.failure_code, "user_took_over")
        self.assertIsNotNone(result.paused)
        self.assertTrue(result.paused.steps_taken)

    def test_an_app_that_renamed_itself_while_playing_is_still_found(self):
        observer = MediaObserver([_SEARCH_VIEW, _results()])
        observer.set_now_playing("Weightless • Marconi Union")
        control = MediaControl(observer=observer)
        memory = RecordingMemory()
        memory.recent_context = lambda: [
            {"handle": 77, "app": "Spotify Premium", "action": "played"},
        ]
        planner = _planner(observer, control, memory=memory)

        found = planner._media_window("Spotify")

        self.assertIsNotNone(found)
        self.assertEqual(found.handle, 77)


class TransportControlTests(unittest.TestCase):
    """Duplicate transport buttons must not make "stop it" unanswerable."""

    def test_three_identically_named_pause_buttons_are_still_clickable(self):
        # Measured live: a playing Spotify shows "일시 정지하기" three times --
        # main bar, mini player, now-playing view -- so name matching refuses
        # the whole request, and the deictic follow-up used to die there.
        duplicated = WindowObservation(
            "observed",
            title="IVE - BANG BANG",
            controls=(
                ControlInfo("Button", "일시 정지하기", is_actionable=True, element_id="t-e0"),
                ControlInfo("Button", "일시 정지하기", is_actionable=True, element_id="t-e1"),
                ControlInfo("Button", "일시 정지하기", is_actionable=True, element_id="t-e2"),
            ),
        )
        observer = MediaObserver([duplicated])
        control = MediaControl(observer=observer)
        control.click_control = lambda target, name, *, confirmed=False, element_id="": (
            control.click_calls.append((name, element_id))
            or (
                UIActionResult(
                    "ambiguous",
                    "More than one equally suitable control matches it.",
                )
                if not element_id
                else UIActionResult("clicked", f"Clicked {name}.", control_name=name)
            )
        )
        planner = _planner(observer, control)

        result = planner._click_transport_label("IVE - BANG BANG", "일시 정지하기")

        self.assertEqual(result.status, "clicked")
        # The retry addresses one exact control by scan id, never by the
        # name that could not tell the three of them apart.
        self.assertEqual(control.click_calls[-1], ("일시 정지하기", "t-e0"))

    def test_an_ordinary_failure_is_not_retried_by_id(self):
        observer = MediaObserver([_SEARCH_VIEW])
        control = MediaControl(observer=observer)
        control.click_control = lambda target, name, *, confirmed=False, element_id="": (
            control.click_calls.append((name, element_id))
            or UIActionResult("not_found", "No control matches that name.")
        )
        planner = _planner(observer, control)

        result = planner._click_transport_label("Spotify Premium", "Pause")

        self.assertEqual(result.status, "not_found")
        self.assertEqual(len(control.click_calls), 1)


class PlaybackEvidenceTests(unittest.TestCase):
    def test_a_title_that_was_already_showing_is_not_proof_by_itself(self):
        # Spotify keeps the track in its title while it is *loaded*. If the
        # window said "Bang Bang" before this run touched anything, only a
        # control offering to pause shows that playback actually started.
        paused_view = WindowObservation(
            "observed",
            title="Bang Bang - IVE",
            controls=(
                ControlInfo("Button", "재생하기", is_actionable=True, element_id="p-e0"),
            ),
        )
        playing_view = WindowObservation(
            "observed",
            title="Bang Bang - IVE",
            controls=(
                ControlInfo("Button", "일시 정지하기", is_actionable=True, element_id="p-e0"),
            ),
        )
        observer = MediaObserver([paused_view, playing_view])
        observer.set_now_playing("Bang Bang - IVE")
        planner = _planner(observer, MediaControl(observer=observer))
        target = MediaTarget("Spotify", "Bang Bang", "IVE")

        stale, _ = planner._playback_evidence(
            observer.window, target, baseline="Bang Bang - IVE",
        )
        self.assertFalse(stale)

        real, evidence = planner._playback_evidence(
            observer.window, target, baseline="Bang Bang - IVE",
        )
        self.assertTrue(real)
        self.assertIn("pause", evidence.casefold())

    def test_the_apps_own_window_title_is_the_proof(self):
        observer = MediaObserver([])
        planner = _planner(observer, MediaControl(observer=observer))
        target = MediaTarget("Spotify", "Bang Bang", "IVE")

        playing, evidence = planner._playback_evidence(_SPOTIFY, target)
        self.assertFalse(playing)

        observer.set_now_playing("Bang Bang • IVE")
        playing, evidence = planner._playback_evidence(_SPOTIFY, target)

        self.assertTrue(playing)
        self.assertIn("Bang Bang", evidence)


if __name__ == "__main__":
    unittest.main()
