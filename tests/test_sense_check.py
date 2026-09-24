"""A doubtful spoken transcript whose words do not fit together is asked about.

The transcriber can be sure of words nobody said: "벨뷰에서 시애틀 다운타운까지
버스 있어?" in noise came back "해수에서 슬프 당선까지 버스있어", scored no lower
than a correct "간단한 걸로". The resident model reads the sentence; code
decides when it is asked, what she says, and that a failure loses nothing.
The judge itself is scripted here -- what it catches was measured against
the real model (brain/sense_check.py).
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import unittest

from brain import guard_lines, sense_check
from brain.chat_engine import ChatEngine
from tests.turn_harness import build_engine

GARBLED = "해수에서 슬프 당선까지 버스있어"


class TheGateTests(unittest.TestCase):

    def test_a_typed_turn_is_never_read(self):
        self.assertFalse(sense_check.worth_reading(GARBLED, 0.0))

    def test_a_clearly_heard_one_is_never_read(self):
        self.assertFalse(sense_check.worth_reading(GARBLED, 0.93))

    def test_a_doubtful_one_is(self):
        self.assertTrue(sense_check.worth_reading(GARBLED, 0.53))

    def test_a_one_word_reply_is_never_read(self):
        # A real "네" scored 0.41.
        self.assertFalse(sense_check.worth_reading("네", 0.41))


class TheVerdictTests(unittest.TestCase):

    def test_only_a_clean_garbled_counts(self):
        self.assertTrue(sense_check.reads_as_garbled('{"reads_as":"garbled"}'))
        self.assertTrue(sense_check.reads_as_garbled('{"reads_as": " Garbled "}'))
        for content in ('{"reads_as":"sense"}', "Sure.", "", None, "[]",
                        '{"reads_as": 3}', '{"garbled": true}'):
            with self.subTest(content=content):
                self.assertFalse(sense_check.reads_as_garbled(content))

    def test_the_judge_sees_her_line_and_the_transcript_only(self):
        text = sense_check.message(GARBLED, her_last_line="뭐 드시고 싶으십니까?")

        self.assertEqual(
            text, f"Her last line: 뭐 드시고 싶으십니까?\nTranscript: {GARBLED}",
        )

    def test_what_she_repeats_back_stays_short(self):
        self.assertEqual(sense_check.quoted(GARBLED + "?"), GARBLED)
        long = "뉴욕 9일에 10분 중하면서 메뉴품까지 10분이 얼마야 그리고 또 뭐가 있더라"
        quoted = sense_check.quoted(long)
        self.assertLessEqual(len(quoted), 43)
        self.assertTrue(quoted.endswith("..."))


class JudgeClient:
    """Answers the sense check with a set verdict; everything else goes to
    the scripted model underneath."""

    def __init__(self, inner, verdict):
        self.inner = inner
        self.verdict = verdict
        self.judged: list[str] = []

    def chat(self, **kwargs):
        messages = kwargs.get("messages") or []
        if messages and messages[0].get("content") == sense_check.PROMPT:
            self.judged.append(messages[-1]["content"])
            if isinstance(self.verdict, Exception):
                raise self.verdict
            return {"message": {"content": json.dumps({"reads_as": self.verdict})}}
        return self.inner.chat(**kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _asks_again(routing) -> bool:
    line = str(routing.locked_response or "")
    return "다시 한번 말씀해" in line or "say it again" in line


class TheEngineAsksTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"
        self.judge = JudgeClient(self.engine.client, "garbled")
        self.engine.client = self.judge

    def tearDown(self):
        self.engine.close()

    def _route(self, said: str, *, average: float, unclear: bool = False):
        self.engine._heard_unclearly = unclear
        self.engine._spoken_word_average = average
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._route_turn(said, timings={})

    def test_confident_garbage_is_repeated_back_and_asked_about(self):
        routing = self._route(GARBLED, average=0.53)

        self.assertEqual(
            routing.locked_response,
            "\"해수에서 슬프 당선까지 버스있어\"라고 들었는데, 제가 잘못 들은 것 "
            "같습니다. 다시 한번 말씀해 주시겠습니까?",
        )

    def test_in_english_too(self):
        self.engine._turn_language = "en"

        routing = self._route("the bland paid the wheel of wet", average=0.60)

        self.assertEqual(
            routing.locked_response,
            "I heard \"the bland paid the wheel of wet\", and I don't think I "
            "caught it right. Could you say it again?",
        )

    def test_a_sentence_that_makes_sense_is_answered(self):
        self.judge.verdict = "sense"

        routing = self._route("간단한 걸로 해줘", average=0.53)

        self.assertEqual(len(self.judge.judged), 1)
        self.assertFalse(_asks_again(routing))

    def test_typed_turns_are_never_judged(self):
        routing = self._route(GARBLED, average=0.0)

        self.assertEqual(self.judge.judged, [])
        self.assertFalse(_asks_again(routing))

    def test_clearly_heard_turns_are_never_judged(self):
        self._route(GARBLED, average=0.93)

        self.assertEqual(self.judge.judged, [])

    def test_never_twice_in_a_row(self):
        self._route(GARBLED, average=0.53)

        again = self._route(GARBLED, average=0.53)

        self.assertFalse(_asks_again(again))

    def test_never_right_after_the_unclear_check_asked(self):
        first = self._route("뉴욕 주일의 스포츠함에서 매네틴까지 11 얼마야?",
                            average=0.49, unclear=True)
        self.assertEqual(first.locked_response, guard_lines.say("didnt_catch", "ko"))

        again = self._route(GARBLED, average=0.53)

        self.assertFalse(_asks_again(again))

    def test_a_turn_in_between_lets_her_ask_again(self):
        self._route(GARBLED, average=0.53)
        self.judge.verdict = "sense"
        self._route("오늘 날씨 어때?", average=0.70)
        self.judge.verdict = "garbled"

        routing = self._route(GARBLED, average=0.53)

        self.assertTrue(_asks_again(routing))

    def test_a_failed_judge_answers_the_turn(self):
        self.judge.verdict = RuntimeError("the model is not loaded")

        routing = self._route(GARBLED, average=0.53)

        self.assertFalse(_asks_again(routing))

    def test_a_slip_the_repair_settles_is_not_asked_about(self):
        # 빈천공항 is assumed as 인천공항 and said; that reading stands.
        self._route("빈천공항에서 미국까지 가는데 몇 시간 걸려?", average=0.60)

        self.assertEqual(self.judge.judged, [])

    def test_the_voice_loop_can_pass_it(self):
        self.assertIn("spoken_word_average",
                      inspect.signature(ChatEngine.chat).parameters)


class TheTranscriberReportsItsAverageTests(unittest.TestCase):

    def test_the_average_of_the_words(self):
        from tests.test_unclear_transcript import _stt

        stt = _stt([
            ("ko", 0.9, GARBLED, [0.46, 0.50, 0.60, 0.56]),
            ("ko", 0.9, "네", []),
        ])
        with contextlib.redirect_stdout(io.StringIO()):
            stt._run_transcription("clip.wav")
            self.assertAlmostEqual(stt.last_word_average, 0.53, places=2)

            stt._run_transcription("clip.wav")
        self.assertEqual(stt.last_word_average, 0.0)


if __name__ == "__main__":
    unittest.main()
