"""Turning what the person did into steps, and doing them again.

The plan half is the one shaped by a live run: "open Settings, then System,
then Display" was recorded as seven rows through Windows Search, the Start
button and the Start menu, and repeating those literally would type into a
search box that is no longer there. The runner half is checked against a
fake desktop -- nothing here touches the machine.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from brain import replay_plan
from brain.replay_plan import ReplayStep
from brain.replay_runner import ReplayRunner
from memory.activity_log import Activity


def row(kind, app, window="", target="", **fields):
    return Activity(at=fields.pop("at", 100.0), kind=kind, app=app, window=window,
                    target=target, **fields)


# The live run, as the log had it.
OPENING_SETTINGS = [
    row("switch", "SearchHost.exe", "검색"),
    row("click", "explorer.exe", "", "시작", target_type="Button",
        detail={"toggled": "on"}),
    row("switch", "ApplicationFrameHost.exe", "설정"),
    row("click", "StartMenuExperienceHost.exe", "시작"),
    row("click", "ApplicationFrameHost.exe", "설정", "시스템"),
    row("click", "ApplicationFrameHost.exe", "설정", "디스플레이"),
]


class ThePlanTests(unittest.TestCase):

    def test_the_way_there_is_not_part_of_it(self):
        plan = replay_plan.build(OPENING_SETTINGS)

        self.assertEqual(
            [(s.action, s.target) for s in plan.steps],
            [("open_app", ""), ("click", "시스템"), ("click", "디스플레이")],
        )
        self.assertEqual(plan.steps[0].app_ko, "설정")

    def test_said_in_both_languages(self):
        plan = replay_plan.build(OPENING_SETTINGS)

        self.assertEqual(
            replay_plan.steps_text(plan.steps, "ko"),
            "1) 설정 열기, 2) 설정에서 “시스템” 클릭, 3) 설정에서 “디스플레이” 클릭",
        )
        self.assertEqual(
            replay_plan.steps_text(plan.steps, "en"),
            "1) open 설정, 2) click “시스템” in 설정, 3) click “디스플레이” in 설정",
        )

    def test_pages_typing_and_keys(self):
        plan = replay_plan.build([
            row("switch", "chrome.exe", "New Tab - Chrome"),
            row("page", "chrome.exe", "YouTube - Chrome", "YouTube",
                url="https://www.youtube.com/"),
            row("type", "chrome.exe", "YouTube - Chrome", "Search", text="lofi",
                detail={"ended_by": "Enter"}),
            row("key", "chrome.exe", "YouTube - Chrome", text="Ctrl+T"),
        ])

        self.assertEqual(
            [(s.action, s.target, s.text, s.submit) for s in plan.steps],
            [("focus_app", "", "", False),
             ("open_url", "https://www.youtube.com/", "", False),
             ("type", "Search", "lofi", True),
             ("press", "Ctrl+T", "", False)],
        )

    def test_what_cannot_be_repeated_is_said(self):
        plan = replay_plan.build([
            row("type", "chrome.exe", "Login", "Password", detail={"password": True}),
            row("type", "notepad.exe", "memo", "Text editor"),       # text not kept
            row("click", "game.exe", "Game", ""),                    # nameless
            row("right_click", "explorer.exe", "Downloads", "a.txt"),
        ])

        self.assertEqual(plan.steps, [])
        self.assertEqual(plan.skipped,
                         ["password", "typed_text_unknown", "unnamed_click", "right_click"])
        self.assertIn("비밀번호", replay_plan.skipped_text(plan.skipped, "ko"))

    def test_what_cannot_be_undone_is_marked(self):
        plan = replay_plan.build([
            row("click", "explorer.exe", "Downloads", "삭제"),
            row("type", "KakaoTalk.exe", "친구", "메시지 입력", text="곧 갈게",
                detail={"ended_by": "Enter"}),
            row("click", "explorer.exe", "Downloads", "보기"),
        ])

        self.assertEqual([s.risky for s in plan.steps], [True, True, False])
        self.assertIn("(되돌릴 수 없음)", replay_plan.step_text(plan.steps[0], "ko"))

    def test_passing_through_windows_is_not_going_to_them(self):
        plan = replay_plan.build([
            row("switch", "chrome.exe", "a"),
            row("switch", "Discord.exe", "b"),
            row("switch", "Spotify.exe", "c"),
            row("click", "Spotify.exe", "c", "재생"),
        ])

        self.assertEqual([(s.action, s.app) for s in plan.steps],
                         [("focus_app", "Spotify.exe"), ("click", "Spotify.exe")])

    def test_the_last_few(self):
        plan = replay_plan.build(OPENING_SETTINGS)

        self.assertEqual([s.target for s in replay_plan.last(plan, 2).steps],
                         ["시스템", "디스플레이"])

    def test_a_step_survives_being_saved(self):
        step = replay_plan.build(OPENING_SETTINGS).steps[1]

        self.assertEqual(ReplayStep.from_dict(step.as_dict()), step)


class FakeDesktop:
    def __init__(self, screen):
        self.screen = screen
        self.done = []

    def _ok(self, status):
        return SimpleNamespace(succeeded=status != "failed", status=status, message=status)

    def focus_window(self, window):
        self.done.append(("focus", window.title))
        return self._ok("focused")

    def click_control(self, window, name, *, confirmed=False):
        self.done.append(("click", window.title, name, confirmed))
        return self._ok("failed" if name in self.screen.missing else "clicked")

    def double_click_control(self, window, name, *, confirmed=False):
        self.done.append(("double_click", window.title, name, confirmed))
        return self._ok("clicked")

    def type_text(self, window, name, text, *, confirmed=False, submit=False):
        self.done.append(("type", window.title, name, text, submit))
        return self._ok("typed")

    def press_key(self, window, *keys):
        self.done.append(("press", window.title, keys))
        return self._ok("pressed")


class FakeScreen:
    def __init__(self, *windows):
        self.windows = list(windows)
        self.missing = set()
        self.opened = []

    def list_windows(self):
        return list(self.windows)

    def open_app(self, name):
        self.opened.append(name)
        self.windows.append(SimpleNamespace(title="설정", process_id=9))
        return SimpleNamespace(succeeded=True, message="opened")

    def open_url(self, url):
        return SimpleNamespace(succeeded=True, message=f"opened {url}")


PROCESSES = {1: "chrome.exe", 9: "ApplicationFrameHost.exe"}


def runner(screen, *, watcher=None):
    desktop = FakeDesktop(screen)
    return ReplayRunner(
        computer_control=screen, desktop_control=desktop, browser_service=screen,
        observer=screen, input_watcher=watcher, sleep=lambda _s: None,
        process_name=lambda pid: PROCESSES.get(pid, ""),
    ), desktop


class TheRunnerTests(unittest.TestCase):

    def test_opens_what_is_not_open_and_does_the_steps_in_order(self):
        screen = FakeScreen()
        run, desktop = runner(screen)

        outcome = run.run(replay_plan.build(OPENING_SETTINGS).steps)

        self.assertTrue(outcome.finished)
        # A store app is named by its window title -- 설정 on this system.
        self.assertEqual(screen.opened, ["설정"])
        self.assertEqual([d[0:3] for d in desktop.done if d[0] == "click"],
                         [("click", "설정", "시스템"), ("click", "설정", "디스플레이")])

    def test_an_open_app_is_brought_forward_not_opened_again(self):
        screen = FakeScreen(SimpleNamespace(title="YouTube - Chrome", process_id=1))
        run, desktop = runner(screen)
        steps = [ReplayStep("focus_app", "chrome.exe", "Chrome", "크롬", window="YouTube - Chrome"),
                 ReplayStep("press", "chrome.exe", "Chrome", "크롬", target="Ctrl+T")]

        outcome = run.run(steps)

        self.assertTrue(outcome.finished)
        self.assertEqual(screen.opened, [])
        self.assertEqual(desktop.done[-1], ("press", "YouTube - Chrome", ("ctrl", "t")))

    def test_it_stops_at_the_step_it_cannot_do(self):
        screen = FakeScreen(SimpleNamespace(title="설정", process_id=9))
        screen.missing = {"시스템"}
        run, _ = runner(screen)

        outcome = run.run(replay_plan.build(OPENING_SETTINGS).steps)

        self.assertFalse(outcome.finished)
        self.assertEqual((len(outcome.done), outcome.failed.target), (1, "시스템"))

    def test_touching_the_mouse_stops_it(self):
        screen = FakeScreen(SimpleNamespace(title="설정", process_id=9))
        touched = {"after": 1}

        class Watcher:
            available = True

            def mark(self):
                return 0.0

            def user_input_since(self, _mark):
                touched["after"] -= 1
                return touched["after"] < 0

        run, desktop = runner(screen, watcher=Watcher())

        outcome = run.run(replay_plan.build(OPENING_SETTINGS).steps)

        self.assertTrue(outcome.interrupted)
        self.assertEqual(len(outcome.done), 1)

    def test_a_step_that_cannot_be_undone_goes_through_only_as_confirmed(self):
        screen = FakeScreen(SimpleNamespace(title="Downloads", process_id=1))
        PROCESSES[1] = "explorer.exe"
        try:
            run, desktop = runner(screen)
            plan = replay_plan.build([row("click", "explorer.exe", "Downloads", "삭제")])
            run.run(plan.steps)
        finally:
            PROCESSES[1] = "chrome.exe"

        self.assertEqual(desktop.done[-1], ("click", "Downloads", "삭제", True))


if __name__ == "__main__":
    unittest.main()
