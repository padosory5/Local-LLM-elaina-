"""What the person says about what they did on the computer.

Most of this file is what must *not* be read as a command: "repeat", "do
it again", "remember", "오늘", "아까" turn up in ordinary turns all the time
-- "can you repeat that?" asks her to say her last sentence again, not to
redo five clicks -- and every dogfood arc turn, in both languages, is run
through the reader here and must come out untouched.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from brain.activity_commands import read, read_consent, time_window


class RepeatingTests(unittest.TestCase):

    def test_the_last_n_actions(self):
        for said, count in (
            ("repeat my last 5 actions", 5),
            ("Repeat the previous 5 actions that I did", 5),
            ("can you redo my last three steps?", 3),
            ("do the last few things I did again", 3),
            ("마지막 5개 다시 해줘", 5),
            ("방금 한 동작 세 개 그대로 해줘", 3),
            ("최근 두 개 다시 해줘", 2),
        ):
            with self.subTest(said=said):
                command = read(said)
                self.assertEqual((command.kind, command.count), ("replay_last", count))

    def test_what_i_just_did(self):
        for said in ("repeat what I just did", "do that again the way I did it",
                     "방금 한 거 다시 해줘", "아까 한 거 그대로 해줘"):
            with self.subTest(said=said):
                command = read(said)
                self.assertEqual((command.kind, command.count), ("replay_last", 0))


class AfterStartingThePcTests(unittest.TestCase):

    def test_asking(self):
        for said in ("what did I do after I turned on my PC?",
                     "Do you remember what I did as soon as I turned on my computer?",
                     "컴퓨터 켜자마자 뭐 했지?", "부팅하고 나서 뭐 했는지 기억나?"):
            with self.subTest(said=said):
                self.assertEqual(read(said).kind, "recall_boot")

    def test_asking_her_to_do_it(self):
        for said in (
            "Do you remember what I did as soon as I turned on my PC? I want "
            "you to do those actions",
            "do what I did after I turned on my PC",
            "부팅하고 나서 한 거 다시 해줘",
        ):
            with self.subTest(said=said):
                self.assertEqual(read(said).kind, "replay_boot")

    def test_every_time(self):
        for said in ("do those every time I turn on my PC",
                     "whenever I boot up my computer, open those",
                     "make that my startup routine",
                     "켤 때마다 이렇게 해줘", "부팅할 때마다 그거 열어줘"):
            with self.subTest(said=said):
                self.assertEqual(read(said).kind, "save_startup")

    def test_not_any_more(self):
        for said in ("stop doing that at startup", "forget my startup routine",
                     "부팅할 때 그거 하지 마"):
            with self.subTest(said=said):
                self.assertEqual(read(said).kind, "forget_startup")


class ConsentTests(unittest.TestCase):

    def test_yes(self):
        for said in ("yes", "yeah, can you do that?", "go ahead", "do it",
                     "sure", "응 해줘", "그래", "네", "해줘"):
            with self.subTest(said=said):
                self.assertEqual(read_consent(said), "yes")

    def test_no(self):
        for said in ("no", "not now", "never mind", "아니", "하지 마", "괜찮아", "나중에"):
            with self.subTest(said=said):
                self.assertEqual(read_consent(said), "no")

    def test_something_else(self):
        for said in ("what's the weather tomorrow?", "오늘 날씨 어때?",
                     "tell me about the second one", ""):
            with self.subTest(said=said):
                self.assertEqual(read_consent(said), "")


class WhatIDidTests(unittest.TestCase):

    def test_asking(self):
        for said, when, kinds in (
            ("what did I do this morning?", "this_morning", ()),
            ("which websites did I visit today?", "today", ("page",)),
            ("오늘 뭐 했지?", "today", ()),
            ("아까 본 사이트 뭐였지?", "earlier", ("page",)),
            ("지난 30분 동안 뭐 했지?", "minutes:30", ()),
        ):
            with self.subTest(said=said):
                command = read(said)
                self.assertEqual((command.kind, command.when, command.kinds),
                                 ("recall", when, kinds))

    def test_forgetting_pausing_resuming(self):
        self.assertEqual(read("forget what I did today").when, "today")
        self.assertTrue(read("delete my activity log").everything)
        self.assertEqual(read("오늘 한 거 지워줘").kind, "forget_activity")
        self.assertEqual(read("pause the activity log").kind, "pause")
        self.assertEqual(read("활동 기록 꺼 줘").kind, "pause")
        self.assertEqual(read("resume the activity log").kind, "resume")

    def test_time_windows(self):
        now = datetime(2026, 9, 11, 15, 30).timestamp()
        day = datetime(2026, 9, 11).timestamp()
        self.assertEqual(time_window("today", now)[:2], (day, None))
        self.assertEqual(time_window("어제", now)[:2], (day - 86400, day))
        self.assertEqual(time_window("in the last 2 hours", now)[2], "hours:2")


class WhatMustNotBeACommandTests(unittest.TestCase):

    def test_ordinary_sentences(self):
        for said in (
            "can you repeat that?", "say that again", "repeat after me",
            "repeat the last thing you said", "do it again", "다시 해줘",
            "한 번 더 말해줘", "record this", "stop recording",
            "record a video for me", "what did I do wrong?",
            "what did I say earlier?", "remember that I like pizza",
            "이거 기억해줘", "화면 녹화 프로그램 추천해줘", "stop it",
            "오늘 뭐 먹지?", "아까 말한 거 기억나?", "아까 본 사이트 다시 열어줘",
            "내가 뭐 잘못했어?", "turn on my PC fan", "컴퓨터 켜는 법 알려줘",
        ):
            with self.subTest(said=said):
                self.assertIsNone(read(said))

    def test_every_dogfood_turn(self):
        from scripts.live_dogfood_conversation import ARCS

        for arc, spec in ARCS.items():
            for said, _ in spec["turns"]:
                with self.subTest(arc=arc, said=said):
                    self.assertIsNone(read(said))


if __name__ == "__main__":
    unittest.main()
