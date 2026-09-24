"""The whole exchange through the engine: what I did, do it again, every time.

A replay is never done on the turn that asks for it: she lists the steps
and asks, and only a yes on the next turn does anything. The runner is
replaced by a fake here -- the real one is tested in test_replay.py -- so
nothing in this file can touch the machine.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from brain import guard_lines
from brain.replay_runner import ReplayOutcome
from memory.activity_log import Activity, ActivityLog
from tests.turn_harness import build_engine, machine_actions


class FakeRecorder:
    running = True
    paused = False

    def __init__(self, activities):
        self.recent = list(activities)

    def recent_activities(self):
        return list(self.recent)

    def stop(self):
        pass


class FakeRunner:
    def __init__(self, *, stop_after=None):
        self.ran = []
        self.stop_after = stop_after

    def run(self, steps):
        self.ran.append(list(steps))
        if self.stop_after is None:
            return ReplayOutcome(done=list(steps))
        return ReplayOutcome(done=list(steps[: self.stop_after]),
                             failed=steps[self.stop_after], reason="it was not there")


def settings_run(at):
    return [
        Activity(at=at, kind="switch", app="SearchHost.exe", window="검색"),
        Activity(at=at + 1, kind="switch", app="ApplicationFrameHost.exe", window="설정"),
        Activity(at=at + 2, kind="click", app="ApplicationFrameHost.exe", window="설정",
                 target="시스템"),
        Activity(at=at + 3, kind="click", app="ApplicationFrameHost.exe", window="설정",
                 target="디스플레이"),
    ]


class ActivityTurnTestCase(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"
        self.directory = Path(tempfile.mkdtemp(prefix="elaina-activity-turns-"))
        self.log = ActivityLog(self.directory / "activity.db", retention_days=0)
        self.engine.activity_log = self.log
        self.recorder = FakeRecorder(settings_run(time.time() - 60))
        self.engine.activity_recorder = self.recorder
        self.runner = FakeRunner()
        self.engine._replay_runner = lambda: self.runner

    def tearDown(self):
        self.log.close()
        self.engine.close()

    def say(self, text):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._route_turn(text, timings={}).locked_response or ""


class RepeatingTests(ActivityTurnTestCase):

    def test_she_lists_them_and_asks_first(self):
        answer = self.say("마지막 3개 다시 해줘")

        self.assertEqual(
            answer,
            "최근 동작 3개: 1) 설정 열기, 2) 설정에서 “시스템” 클릭, "
            "3) 설정에서 “디스플레이” 클릭. 지금 그대로 해 드릴까요?",
        )
        self.assertEqual(self.runner.ran, [])
        self.assertEqual(machine_actions(self.engine), [])

    def test_a_yes_does_them(self):
        self.say("repeat my last 3 actions")

        answer = self.say("응 해줘")

        self.assertEqual(len(self.runner.ran), 1)
        self.assertEqual([s.target for s in self.runner.ran[0]], ["", "시스템", "디스플레이"])
        self.assertTrue(answer.startswith("다 했습니다: 1) 설정 열기"))

    def test_in_english_too(self):
        self.engine._turn_language = "en"
        asked = self.say("repeat my last 2 actions")
        done = self.say("yeah, can you do that?")

        self.assertEqual(asked, "Your last 2 actions: 1) click “시스템” in 설정, "
                                "2) click “디스플레이” in 설정. Shall I do them now?")
        self.assertTrue(done.startswith("Done: "))

    def test_a_no_does_nothing(self):
        self.say("마지막 3개 다시 해줘")

        self.assertEqual(self.say("아니"), guard_lines.say("replay_declined", "ko"))
        self.assertEqual(self.runner.ran, [])

    def test_talking_about_something_else_lets_it_lapse(self):
        self.say("마지막 3개 다시 해줘")
        self.say("오늘 날씨 어때?")

        self.say("응")

        self.assertEqual(self.runner.ran, [])

    def test_where_it_stopped_is_said(self):
        self.runner.stop_after = 1
        self.say("마지막 3개 다시 해줘")

        answer = self.say("응")

        self.assertEqual(answer, "3단계 중 1단계까지 했고, 이 단계에서 멈췄습니다: "
                                 "설정에서 “시스템” 클릭.")

    def test_desktop_control_is_on_only_for_the_run(self):
        self.engine.computer_control_mode.set_enabled(False)
        seen = []
        self.runner.run = lambda steps: (
            seen.append(self.engine.computer_control_mode.enabled)
            or ReplayOutcome(done=list(steps))
        )
        self.say("마지막 3개 다시 해줘")

        self.say("응")

        self.assertEqual(seen, [True])
        self.assertFalse(self.engine.computer_control_mode.enabled)

    def test_nothing_to_repeat(self):
        self.recorder.recent = []

        self.assertEqual(self.say("방금 한 거 다시 해줘"), guard_lines.say("replay_nothing", "ko"))


class AfterStartingThePcTests(ActivityTurnTestCase):

    def setUp(self):
        super().setUp()
        booted = time.time() - 3600
        for activity in settings_run(booted + 60):
            self.log.add(activity)
        self.boot = mock.patch("psutil.boot_time", return_value=booted)
        self.boot.start()

    def tearDown(self):
        self.boot.stop()
        super().tearDown()

    def test_what_i_did_first(self):
        answer = self.say("컴퓨터 켜자마자 뭐 했지?")

        self.assertIn("컴퓨터를 켜신 뒤 처음 하신 일은 이렇습니다: 1) 설정 열기", answer)
        self.assertTrue(answer.endswith("지금 그대로 해 드릴까요?"))

    def test_every_time_i_start(self):
        self.say("컴퓨터 켜자마자 뭐 했지?")

        saved = self.say("켤 때마다 이렇게 해줘")

        self.assertTrue(saved.startswith("알겠습니다. 앞으로 제가 켜질 때마다"))
        self.assertEqual([step["action"] for step in self.engine.routines.plan("startup")],
                         ["open_app", "click", "click"])

    def test_at_start_up_she_offers_and_waits(self):
        self.say("컴퓨터 켜자마자 뭐 했지?")
        self.say("켤 때마다 이렇게 해줘")
        self.engine._pending_replay = None

        with contextlib.redirect_stdout(io.StringIO()):
            offer = self.engine.offer_startup_routine()
        self.assertTrue(offer.startswith("다시 오셨군요. 평소처럼 해 드릴까요? 1) 설정 열기"))
        self.assertEqual(self.runner.ran, [])

        self.say("응")
        self.assertEqual(len(self.runner.ran), 1)

    def test_not_any_more(self):
        self.say("컴퓨터 켜자마자 뭐 했지?")
        self.say("켤 때마다 이렇게 해줘")

        self.say("부팅할 때 그거 하지 마")

        self.assertEqual(self.engine.routines.plan("startup"), [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.engine.offer_startup_routine(), "")


class WhatIDidTests(ActivityTurnTestCase):

    def setUp(self):
        super().setUp()
        now = time.time()
        self.log.add(Activity(at=now - 120, kind="page", app="chrome.exe",
                              target="CPT application - UW", url="https://uw.edu/cpt"))
        self.log.add(Activity(at=now - 60, kind="click", app="SystemSettings.exe",
                              target="Bluetooth", target_type="Button",
                              detail={"toggled": "on", "class_name": "ToggleSwitch"}))
        self.log.add(Activity(at=now - 30, kind="switch", app="SearchHost.exe", window="검색"))

    def test_which_sites(self):
        self.engine._turn_language = "en"
        answer = self.say("which websites did I visit today?")

        self.assertTrue(answer.startswith("Here's what you did today: "))
        self.assertIn("opened “CPT application - UW” (uw.edu/cpt)", answer)

    def test_in_korean_without_the_shell(self):
        answer = self.say("오늘 뭐 했지?")

        self.assertIn("설정에서 “Bluetooth” 켬", answer)
        self.assertNotIn("검색", answer.split(":", 1)[1])

    def test_forgetting_today(self):
        self.engine._turn_language = "en"
        answer = self.say("forget what I did today")

        self.assertEqual(answer, "Done. I deleted 3 records of what you did today.")
        self.assertEqual(self.log.count(), 0)
        self.assertEqual(self.recorder.recent, [])


class HeardAtLowConfidenceTests(ActivityTurnTestCase):

    def test_her_own_command_is_not_asked_again(self):
        # Live: "Stop Recording" heard exactly right at an average of 0.40,
        # and asked about twice.
        self.engine._heard_unclearly = True

        answer = self.say("방금 한 거 다시 해줘")

        self.assertNotIn("다시 한번 말씀해", answer)
        self.assertIn("지금 그대로 해 드릴까요?", answer)


if __name__ == "__main__":
    unittest.main()
