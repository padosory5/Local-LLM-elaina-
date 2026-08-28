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

import tempfile
import unittest
from pathlib import Path

from brain.deliberation import ClarificationGate, interpret
from brain.deliberation.profile import (
    ARTIST_FOR_TITLE,
    STATED,
    UserProfile,
)
from brain.skills import (
    MediaSurface,
    PlayCollectionSkill,
    PlayTrackSkill,
    skill_for,
)
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
        self.key_presses = []

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

    def press_key(self, target, *keys):
        self.key_presses.append(keys)
        return UIActionResult("typed", f"Pressed {'+'.join(keys)}.")

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


class _Played:
    """One verified thing she played, as session memory records it."""

    def __init__(self, subject):
        self.subject = subject


class RecordingMemory:
    def __init__(self, subject=None):
        self.records = []
        # What she last verifiably *played*, if anything. Launches and
        # focuses live in the same memory and must not be mistaken for it.
        self.subject = _Played(subject) if subject else None

    def recent(self, *, app="", family=""):
        if family and family != "activation":
            return ()
        return (self.subject,) if self.subject else ()

    def record(self, **fields):
        self.records.append(fields)

    def recent_context(self):
        return []

    def __init__subject__(self):  # pragma: no cover - documentation only
        pass

    def last_subject(self):
        return self.subject

    def last_action(self):
        return None


class SilentClient:
    def __init__(self):
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        raise AssertionError("The deterministic path must not ask the model.")


def _surface(observer, control, *, memory=None):
    return MediaSurface(
        observer=observer,
        control=control,
        computer_control=NeverOpens(),
        session_actions=memory,
        sleeper=lambda _seconds: None,
    )


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
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
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
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
        control = MediaControl(observer=observer)
        client = SilentClient()

        result = _planner(observer, control, client=client).act(
            "Play Bang Bang by IVE in Spotify."
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(client.calls, 0)

    def test_the_search_is_not_committed_with_enter(self):
        # Measured live, twice: Spotify renders results while you type, and
        # pressing Enter replaces that live list with a page whose top card
        # is a *link* to the track. Committing the search turned a working
        # double-click into a navigation, so this stays uncommitted.
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
        control = MediaControl(observer=observer)

        result = _planner(observer, control).act(
            "Play Bang Bang by IVE in Spotify."
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(control.key_presses, [])

    def test_a_control_that_merely_contains_search_is_not_the_search_box(self):
        # Measured live: "Spotify - 검색하기" is not Spotify's search. Clicking
        # it and typing sent the query somewhere nobody checked.
        decoys = WindowObservation(
            "observed",
            title="Spotify Premium",
            controls=(
                ControlInfo(
                    "Button", "Spotify - 검색하기",
                    is_actionable=True, element_id="d-e0",
                ),
                ControlInfo(
                    "Button", "검색하기", is_actionable=True, element_id="d-e1",
                ),
            ),
        )
        observer = MediaObserver([decoys, _results(), _results()])
        control = MediaControl(observer=observer)

        result = _planner(observer, control).act(
            "Play Bang Bang by IVE in Spotify."
        )

        self.assertEqual(result.status, "done")
        self.assertEqual(control.type_calls[0][0], "검색하기")

    def test_a_rows_own_play_control_is_preferred_over_the_title(self):
        # "After LIKE 재생하기" names the track it plays, so one click starts
        # exactly that -- no double-click, and nothing a link could open
        # instead. Observed live in Spotify's own results.
        rows = WindowObservation(
            "observed",
            title="Spotify Premium",
            controls=(
                ControlInfo("Hyperlink", "Bang Bang Radio", element_id="r-e0"),
                ControlInfo(
                    "Button", "Bang Bang Radio 재생하기",
                    is_actionable=True, element_id="r-e1",
                ),
                ControlInfo(
                    "Button", "Bang Bang 재생하기",
                    is_actionable=True, element_id="r-e2",
                ),
                ControlInfo("Hyperlink", "Bang Bang", element_id="r-e3"),
                ControlInfo("Hyperlink", "IVE", element_id="r-e4"),
            ),
        )
        observer = MediaObserver([_SEARCH_VIEW, rows])
        control = MediaControl(observer=observer)
        control.click_control = lambda target, name, *, confirmed=False, element_id="": (
            control.click_calls.append((name, element_id))
            or observer.set_now_playing("IVE - Bang Bang")
            or UIActionResult("clicked", f"Clicked {name}.", control_name=name)
        )

        result = _planner(observer, control).act(
            "Play Bang Bang by IVE in Spotify."
        )

        self.assertEqual(result.status, "done")
        # The station's own play button shares every word but is not it.
        self.assertEqual(control.click_calls, [("Bang Bang 재생하기", "r-e2")])
        self.assertEqual(control.double_click_calls, [])

    def test_a_request_that_names_no_track_asks_instead_of_typing(self):
        # The whole failure in one test: this used to be parsed as a title,
        # searched for, and typed into the app verbatim. SilentClient also
        # proves the model loop never got the chance to type it either.
        observer = MediaObserver([_SEARCH_VIEW, _results()])
        control = MediaControl(observer=observer)
        planner = _planner(observer, control)

        result = planner.act("Play something from my playlist in Spotify.")

        self.assertEqual(result.status, "needs_clarification")
        self.assertIn("Which song", result.summary)
        self.assertEqual(control.type_calls, [])
        self.assertEqual(control.click_calls, [])
        self.assertEqual(control.double_click_calls, [])

    def test_a_vague_request_asks_once_and_then_plays_the_answer(self):
        # Phase 3's acceptance test, in two turns: the question first, then
        # the answer folded back into the request that prompted it.
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
        control = MediaControl(observer=observer)
        planner = _planner(observer, control)

        asked = planner.act("Play some music in Spotify.")

        self.assertEqual(asked.status, "needs_clarification")
        self.assertIn("Which song", asked.summary)
        self.assertEqual(control.type_calls, [])
        self.assertIsNotNone(asked.clarification)

        gate = ClarificationGate()
        pending = gate.offer(
            goal=asked.clarification.goal,
            slot=asked.clarification.missing,
            question=asked.clarification.question,
            template=asked.clarification.template,
        )
        played = planner.act(pending.completed("Bang Bang by IVE"))

        self.assertEqual(played.status, "done")
        # The answer became the whole request: the artist narrows the
        # search, and only the exact title is activated.
        self.assertEqual(control.type_calls[0][1], "Bang Bang IVE")
        self.assertEqual(control.double_click_calls, [("Bang Bang", "s2-e2")])

    def test_a_vague_request_after_a_played_track_acts_and_says_so(self):
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
        control = MediaControl(observer=observer)
        planner = _planner(
            observer, control, memory=RecordingMemory(subject="Bang Bang"),
        )

        result = planner.act("Play some music in Spotify.")

        self.assertEqual(result.status, "done")
        # It played something, and it said what it assumed while doing it.
        self.assertIn("Bang Bang", result.summary)
        self.assertIn("say the word", result.summary)

    def test_a_radio_row_alone_is_never_activated(self):
        observer = MediaObserver([
            _SEARCH_VIEW, _results(exact_title=False),
            _results(exact_title=False), _results(exact_title=False),
        ])
        control = MediaControl(observer=observer)
        planner = _planner(observer, control)

        result = PlayTrackSkill().run(
            interpret("Play Bang Bang by IVE in Spotify."),
            _surface(observer, control),
        )

        self.assertTrue(result.handed_back)
        self.assertEqual(control.double_click_calls, [])
        self.assertEqual(control.click_calls, [])

    def test_a_same_titled_track_by_another_artist_is_not_played(self):
        observer = MediaObserver([
            _SEARCH_VIEW, _results(artist_near=False),
            _results(artist_near=False), _results(artist_near=False),
        ])
        control = MediaControl(observer=observer)
        planner = _planner(observer, control)

        result = PlayTrackSkill().run(
            interpret("Play Bang Bang by IVE in Spotify."),
            _surface(observer, control),
        )

        self.assertTrue(result.handed_back)
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
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results(), item_page])
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
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
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
        found = _surface(observer, control, memory=memory).window("Spotify")

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


class PlayCollectionTests(unittest.TestCase):
    """Starting a place the person named, rather than aiming at an item."""

    LIBRARY = WindowObservation(
        "observed",
        title="Spotify Premium",
        controls=(
            ControlInfo("Button", "홈", is_actionable=True, element_id="c-e0"),
            ControlInfo(
                "Group", "좋아요 표시한 곡", is_actionable=True, element_id="c-e1",
            ),
        ),
    )
    LIKED_PAGE = WindowObservation(
        "observed",
        title="Spotify Premium",
        controls=(
            ControlInfo("Text", "좋아요 표시한 곡", element_id="c-e2"),
            ControlInfo(
                "Button", "재생하기", is_actionable=True, element_id="c-e3",
            ),
        ),
    )

    def _control(self, observer, *, plays=True):
        control = MediaControl(observer=observer)

        def click(target, name, *, confirmed=False, element_id=""):
            control.click_calls.append((name, element_id))
            if plays and name == "재생하기":
                observer.set_now_playing("IVE - After LIKE")
            return UIActionResult("clicked", f"Clicked {name}.", control_name=name)

        control.click_control = click
        return control

    def test_a_named_collection_is_opened_and_started(self):
        observer = MediaObserver([self.LIBRARY, self.LIKED_PAGE])
        control = self._control(observer)

        result = PlayCollectionSkill().run(
            interpret("Play any songs from my liked list in Spotify"),
            _surface(observer, control),
        )

        self.assertEqual(result.status, "done")
        self.assertIn("liked songs", result.summary)
        # It names what actually started, which is the only thing it can
        # honestly claim: the collection chooses the track, not her.
        self.assertIn("After LIKE", result.summary)
        self.assertEqual(
            control.click_calls, [("좋아요 표시한 곡", "c-e1"), ("재생하기", "c-e3")],
        )
        # Nothing was searched for: a collection needs no aiming at all.
        self.assertEqual(control.type_calls, [])

    def test_a_collection_that_starts_nothing_is_not_claimed_as_playing(self):
        observer = MediaObserver([self.LIBRARY, self.LIKED_PAGE])
        control = self._control(observer, plays=False)

        result = PlayCollectionSkill().run(
            interpret("Play any songs from my liked list in Spotify"),
            _surface(observer, control),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "playback_unverified")
        self.assertNotIn("Playing", result.summary)

    def test_a_missing_collection_is_reported_rather_than_guessed_at(self):
        elsewhere = WindowObservation(
            "observed",
            title="Spotify Premium",
            controls=(
                ControlInfo("Button", "홈", is_actionable=True, element_id="x-e0"),
            ),
        )
        observer = MediaObserver([elsewhere, elsewhere, elsewhere])
        control = self._control(observer)

        result = PlayCollectionSkill().run(
            interpret("Play any songs from my liked list in Spotify"),
            _surface(observer, control),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "collection_not_found")
        self.assertEqual(control.click_calls, [])

    def test_a_play_control_is_not_pressed_on_a_page_that_is_not_the_collection(self):
        # The page must name the collection, or a Play control on it could
        # belong to anything -- the same rule that keeps a results list
        # from being started by its own generic Play.
        somewhere_else = WindowObservation(
            "observed",
            title="Spotify Premium",
            controls=(
                ControlInfo("Text", "추천", element_id="y-e0"),
                ControlInfo(
                    "Button", "재생하기", is_actionable=True, element_id="y-e1",
                ),
            ),
        )
        observer = MediaObserver(
            [self.LIBRARY, somewhere_else, somewhere_else, somewhere_else],
        )
        control = self._control(observer)

        result = PlayCollectionSkill().run(
            interpret("Play any songs from my liked list in Spotify"),
            _surface(observer, control),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "collection_not_playable")
        self.assertEqual(control.click_calls, [("좋아요 표시한 곡", "c-e1")])

    def test_the_planner_runs_the_collection_skill_end_to_end(self):
        observer = MediaObserver([self.LIBRARY, self.LIKED_PAGE])
        control = self._control(observer)

        result = _planner(observer, control).act(
            "Play any songs from my liked list in Spotify."
        )

        self.assertEqual(result.status, "done")
        self.assertIn("liked songs", result.summary)


class LearningFromWhatHappenedTests(unittest.TestCase):
    """A verified play teaches her; a guess of her own never does."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.profile = UserProfile(
            path=Path(self._directory.name) / "profile.json",
        )

    def _planner_with_profile(self, observer, control):
        return DesktopActionPlanner(
            client=SilentClient(),
            model="qwen3:8b",
            keep_alive=-1,
            observer=observer,
            control=control,
            computer_control=NeverOpens(),
            session_actions=None,
            sleeper=lambda _seconds: None,
            profile=self.profile,
        )

    def test_playing_a_named_track_teaches_which_artist_was_meant(self):
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
        control = MediaControl(observer=observer)

        result = self._planner_with_profile(observer, control).act(
            "Play Bang Bang by IVE in Spotify."
        )

        self.assertEqual(result.status, "done")
        known = self.profile.preferred(ARTIST_FOR_TITLE, key="Bang Bang")
        self.assertEqual(known.value, "IVE")

    def test_a_value_she_filled_in_herself_is_not_evidence_for_itself(self):
        # Without this rule one lucky guess becomes a certainty by being
        # repeated back into the profile that produced it.
        self.profile.observe(
            ARTIST_FOR_TITLE, "IVE", key="Bang Bang", source=STATED,
        )
        before = self.profile.preferred(ARTIST_FOR_TITLE, key="Bang Bang")
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
        control = MediaControl(observer=observer)

        result = self._planner_with_profile(observer, control).act(
            "Play Bang Bang in Spotify."
        )

        self.assertEqual(result.status, "done")
        # It played IVE's, said so, and learned nothing new from doing it.
        self.assertIn("by IVE", result.summary)
        after = self.profile.preferred(ARTIST_FOR_TITLE, key="Bang Bang")
        self.assertEqual(after.standing, before.standing)

    def test_a_failed_run_teaches_nothing(self):
        observer = MediaObserver([_SEARCH_VIEW, _results(), _results()])
        control = MediaControl(observer=observer, plays=False)

        result = self._planner_with_profile(observer, control).act(
            "Play Bang Bang by IVE in Spotify."
        )

        self.assertNotEqual(result.status, "done")
        self.assertEqual(self.profile.known(), ())


class SkillRegistryTests(unittest.TestCase):
    def test_each_request_finds_the_procedure_that_serves_it(self):
        self.assertEqual(
            skill_for(interpret("Play Bang Bang by IVE in Spotify.")).name,
            "play_track",
        )
        self.assertEqual(
            skill_for(
                interpret("Play any songs from my liked list in Spotify")
            ).name,
            "play_collection",
        )

    def test_a_request_she_has_no_procedure_for_finds_nothing(self):
        self.assertIsNone(
            skill_for(interpret("Play something from my playlist in Spotify"))
        )
        self.assertIsNone(skill_for(interpret("Pause the music in Spotify")))


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
