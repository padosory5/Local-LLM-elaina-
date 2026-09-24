"""The recorder turns the person's clicks and keys into things they did.

No screen is read here: a fake stands in for Win32 and UI Automation, and
events are fed in as the input watcher would queue them. What is checked is
what gets written -- and, as much, what never does: Elaina's own window,
a private window's contents, a game's keys, a password.
"""

from __future__ import annotations

import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from memory.activity_log import ActivityLog
from tools.screen_control.activity_recorder import (
    ActivityRecorder,
    ControlInfo,
    WindowInfo,
    pick_target,
)
from tools.screen_control.input_watcher import InputWatcher

SETTINGS = WindowInfo(1, "설정", "SystemSettings.exe", 100, (0, 0, 1000, 800))
CHROME = WindowInfo(2, "YouTube - Chrome", "chrome.exe", 200, (0, 0, 1600, 900))
ELAINA = WindowInfo(3, "Elaina Desktop", "electron.exe", 300, (0, 0, 500, 700))
GAME = WindowInfo(4, "Valorant", "VALORANT.exe", 400, (0, 0, 2560, 1440), fullscreen=True)
PRIVATE = WindowInfo(5, "New Incognito Tab - Chrome", "chrome.exe", 200, (0, 0, 1600, 900))


class FakeScreen:
    def __init__(self):
        self.windows = {w.handle: w for w in (SETTINGS, CHROME, ELAINA, GAME, PRIVATE)}
        self.front = CHROME.handle
        self.at: dict[tuple[int, int], tuple[int, ControlInfo | None]] = {}
        self.focused: ControlInfo | None = None
        self.values: dict[str, str] = {}
        self.toggles: dict[str, str] = {}
        self.urls: dict[int, str] = {}
        self.reads: list[str] = []

    def foreground(self):
        return self.windows.get(self.front)

    def window_at(self, x, y):
        return self.windows.get(self.at.get((x, y), (self.front, None))[0])

    def control_at(self, x, y):
        return self.at.get((x, y), (None, None))[1]

    def focused_control(self):
        return self.focused

    def read_value(self, control):
        self.reads.append(control.automation_id)
        return self.values.get(control.automation_id, "")

    def read_toggle(self, control):
        return self.toggles.get(control.automation_id)

    def browser_url(self, window):
        return self.urls.get(window.handle, "")


def click(x, y, at, what="left_down"):
    return SimpleNamespace(kind="mouse", what=what, x=x, y=y, extra=0, at=at)


def key(vk, at, *, up=False):
    return SimpleNamespace(kind="key", what=f"vk_0x{vk:02x}", x=0, y=0,
                           extra=0x101 if up else 0x100, at=at)


def wheel(x, y, delta, at):
    return SimpleNamespace(kind="mouse", what="wheel", x=x, y=y, extra=delta, at=at)


class RecorderTestCase(unittest.TestCase):

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="elaina-activity-"))
        self.now = 1_000_000.0
        self.log = ActivityLog(self.directory / "activity.db", retention_days=0,
                               clock=lambda: self.now)
        self.screen = FakeScreen()
        self.recorder = ActivityRecorder(
            watcher=SimpleNamespace(available=True), log=self.log,
            resolver=self.screen, clock=lambda: self.now, own_pids=(),
        )
        self.recorder._feed = deque()
        self.recorder.step(self.now)  # takes in the window already in front

    def tearDown(self):
        self.log.close()

    def feed(self, *events):
        self.recorder._feed.extend(events)

    def advance(self, seconds, *, steps=1):
        for _ in range(steps):
            self.now += seconds / steps
            self.recorder.step(self.now)

    def rows(self, kind=None):
        return [row for row in self.log.recent(limit=500)
                if kind is None or row.kind == kind]


class ClicksTests(RecorderTestCase):

    def test_a_click_is_named_by_what_it_was_on(self):
        button = ControlInfo("Bluetooth 및 장치", "ListItem", automation_id="nav-bt",
                             rect=(10, 100, 200, 140))
        self.screen.at[(50, 120)] = (SETTINGS.handle, button)
        self.feed(click(50, 120, self.now))

        # A click waits half a second to see whether it becomes a double.
        self.advance(1.0, steps=4)

        [row] = self.rows("click")
        self.assertEqual((row.app, row.target, row.target_type),
                         ("SystemSettings.exe", "Bluetooth 및 장치", "ListItem"))
        self.assertEqual(row.detail["automation_id"], "nav-bt")
        self.assertEqual(row.detail["in_window"], [0.05, 0.15])

    def test_a_double_click_is_one_row(self):
        item = ControlInfo("report.pdf", "ListItem")
        self.screen.at[(300, 300)] = (CHROME.handle, item)
        self.feed(click(300, 300, 10.0), click(301, 300, 10.2))

        self.advance(0.6, steps=3)

        self.assertEqual([row.kind for row in self.rows() if row.target == "report.pdf"],
                         ["double_click"])

    def test_two_slow_clicks_are_two(self):
        item = ControlInfo("report.pdf", "ListItem")
        self.screen.at[(300, 300)] = (CHROME.handle, item)
        self.feed(click(300, 300, 10.0))
        self.advance(1.0, steps=4)
        self.feed(click(300, 300, 11.0))
        self.advance(1.0, steps=4)

        self.assertEqual(len(self.rows("click")), 2)

    def test_a_switched_setting_says_which_way(self):
        switch = ControlInfo("Bluetooth", "Button", automation_id="bt", toggle="off")
        self.screen.at[(600, 200)] = (SETTINGS.handle, switch)
        self.screen.toggles["bt"] = "on"
        self.feed(click(600, 200, self.now))

        self.advance(1.0, steps=5)

        [row] = self.rows("click")
        self.assertEqual(row.detail["toggled"], "on")

    def test_the_label_of_a_nameless_button_comes_from_inside_it(self):
        self.assertEqual(pick_target([("Text", "구독"), ("Button", ""), ("Pane", "")]),
                         (1, "구독"))
        self.assertEqual(pick_target([("Image", ""), ("Hyperlink", "lofi hip hop")]),
                         (1, "lofi hip hop"))


class WhatIsNeverRecordedTests(RecorderTestCase):

    def test_her_own_window(self):
        self.screen.at[(100, 100)] = (ELAINA.handle, ControlInfo("Send", "Button"))
        self.feed(click(100, 100, self.now))
        self.screen.front = ELAINA.handle

        self.advance(1.2, steps=4)

        self.assertFalse([row for row in self.rows() if row.app == "electron.exe"])

    def test_a_private_window_only_says_it_was_used(self):
        self.screen.front = PRIVATE.handle
        self.advance(0.6, steps=2)
        self.screen.at[(400, 400)] = (PRIVATE.handle, ControlInfo("a secret", "Hyperlink"))
        self.feed(click(400, 400, self.now))
        self.advance(0.6, steps=2)

        rows = [row for row in self.rows() if row.at > 1_000_000.0]
        self.assertEqual([row.kind for row in rows], ["private"])
        self.assertEqual((rows[0].window, rows[0].target, rows[0].url), ("", "", ""))

    def test_a_full_screen_game_is_one_row_with_its_end(self):
        self.screen.front = GAME.handle
        self.advance(0.6, steps=2)
        self.feed(*(key(0x57, self.now + i * 0.01) for i in range(20)))   # W W W ...
        self.advance(60.0, steps=10)
        self.screen.front = CHROME.handle
        self.advance(0.6, steps=2)

        [row] = self.rows("fullscreen")
        self.assertEqual(row.app, "VALORANT.exe")
        self.assertGreater(row.detail["until"], row.at + 59)
        self.assertFalse(self.rows("type"))

    def test_a_password_is_never_read(self):
        self.screen.focused = ControlInfo("Password", "Edit", automation_id="pw",
                                          is_password=True)
        self.screen.values["pw"] = "hunter2"
        self.recorder.typed_text = "always"
        self.recorder.start_recording()
        self.feed(*(key(0x41 + i, self.now) for i in range(6)))

        self.advance(2.0, steps=8)
        recording = self.recorder.stop_recording()

        self.assertNotIn("pw", self.screen.reads)
        [step] = [s for s in recording.steps if s.kind == "type"]
        self.assertEqual(step.text, "")
        self.assertTrue(step.detail["password"])
        self.assertNotIn("hunter2", str([row.text for row in self.rows()]))

    def test_keys_where_there_is_no_text_field_are_not_typing(self):
        self.screen.focused = ControlInfo("video", "Pane")
        self.feed(key(0x20, self.now), key(0x4B, self.now), key(0x4A, self.now))

        self.advance(2.0, steps=8)

        self.assertFalse(self.rows("type"))


class TypingTests(RecorderTestCase):

    def setUp(self):
        super().setUp()
        self.screen.focused = ControlInfo("검색", "Edit", automation_id="search")
        self.screen.values["search"] = "로파이 음악"

    def test_the_log_on_disk_never_has_what_was_typed(self):
        self.feed(*(key(0x46, self.now) for _ in range(5)))

        self.advance(2.0, steps=8)

        [row] = self.rows("type")
        self.assertEqual((row.target, row.text, row.detail["keys"]), ("검색", "", 5))

    def test_memory_has_it_for_repeating(self):
        self.feed(*(key(0x46, self.now) for _ in range(5)))

        self.advance(2.0, steps=8)

        [step] = [a for a in self.recorder.recent_activities() if a.kind == "type"]
        self.assertEqual(step.text, "로파이 음악")

    def test_never_means_never(self):
        self.recorder.typed_text = "never"
        self.feed(*(key(0x46, self.now) for _ in range(5)))

        self.advance(2.0, steps=8)

        self.assertEqual(self.screen.reads, [])
        [step] = [a for a in self.recorder.recent_activities() if a.kind == "type"]
        self.assertEqual(step.text, "")

    def test_enter_ends_it_as_submitted(self):
        self.recorder.start_recording()
        self.feed(key(0x46, self.now), key(0x0D, self.now + 0.1))

        self.advance(0.3, steps=2)
        recording = self.recorder.stop_recording()

        [step] = [s for s in recording.steps if s.kind == "type"]
        self.assertEqual(step.detail["ended_by"], "Enter")

    def test_a_shortcut_is_named(self):
        self.feed(key(0x11, self.now), key(0x54, self.now), key(0x11, self.now, up=True))

        self.advance(0.2)

        [row] = self.rows("key")
        self.assertEqual((row.text, row.app), ("Ctrl+T", "chrome.exe"))


class WindowsAndPagesTests(RecorderTestCase):

    def test_moving_to_another_app_is_recorded(self):
        self.screen.front = SETTINGS.handle
        self.advance(0.6, steps=2)

        self.assertEqual(self.rows("switch")[-1].app, "SystemSettings.exe")

    def test_a_page_is_recorded_once_with_its_address(self):
        self.screen.urls[CHROME.handle] = "https://www.youtube.com/watch?v=abc"
        self.screen.windows[CHROME.handle] = WindowInfo(
            2, "lofi hip hop radio - YouTube - Chrome", "chrome.exe", 200, CHROME.rect)
        self.advance(2.0, steps=6)
        # The same page retitling itself is not a new page.
        self.screen.windows[CHROME.handle] = WindowInfo(
            2, "(1) lofi hip hop radio - YouTube - Chrome", "chrome.exe", 200, CHROME.rect)
        self.advance(2.0, steps=6)

        pages = [row for row in self.rows("page") if row.url.endswith("v=abc")]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].target, "lofi hip hop radio - YouTube")


class PauseAndRecordingTests(RecorderTestCase):

    def test_pausing_stops_the_log_not_a_recording(self):
        self.recorder.paused = True
        self.recorder.start_recording()
        self.screen.at[(50, 50)] = (CHROME.handle, ControlInfo("구독", "Button"))
        self.feed(click(50, 50, self.now))

        self.advance(0.6, steps=3)
        recording = self.recorder.stop_recording()

        self.assertFalse(self.rows("click"))
        self.assertIn("구독", [s.target for s in recording.steps])

    def test_scrolls_are_kept_only_in_recordings(self):
        self.feed(wheel(500, 500, -120, self.now))
        self.advance(1.5, steps=3)
        self.recorder.start_recording()
        self.feed(wheel(500, 500, -120, self.now), wheel(500, 500, -120, self.now))
        self.advance(1.5, steps=3)
        recording = self.recorder.stop_recording()

        self.assertFalse(self.rows("scroll"))
        [step] = [s for s in recording.steps if s.kind == "scroll"]
        self.assertEqual(step.detail["delta"], -240)

    def test_stopping_keeps_the_click_it_was_still_holding(self):
        self.recorder.start_recording()
        self.screen.at[(70, 70)] = (CHROME.handle, ControlInfo("검색", "Button"))
        self.feed(click(70, 70, self.now))
        self.advance(0.05)

        recording = self.recorder.stop_recording()

        self.assertIn("click", [s.kind for s in recording.steps])

    def test_a_recording_begins_where_the_person_is(self):
        self.recorder.start_recording()
        self.advance(0.05)
        recording = self.recorder.stop_recording()

        self.assertEqual(recording.steps[0].kind, "start")
        self.assertEqual(recording.steps[0].app, "chrome.exe")


class TheWatcherFeedTests(unittest.TestCase):

    def test_clicks_and_keys_are_queued_but_moves_are_not(self):
        watcher = InputWatcher(clock=lambda: 5.0)
        feed = watcher.activity_feed()

        watcher._record_real(mouse=True, what="move", x=1, y=1)
        watcher._record_real(mouse=True, what="left_down", x=1, y=1)
        watcher._record_real(mouse=False, what="vk_0x41", extra=0x100)

        self.assertEqual([event.what for event in feed], ["left_down", "vk_0x41"])
        self.assertEqual(feed[-1].extra, 0x100)

    def test_nothing_is_queued_until_asked_for(self):
        watcher = InputWatcher(clock=lambda: 5.0)
        watcher._record_real(mouse=True, what="left_down", x=1, y=1)

        self.assertIsNone(watcher._activity)


if __name__ == "__main__":
    unittest.main()
