"""A clip heard too unclearly to answer is asked about -- once.

Measured with a harvested mishearing: noise that decoded into words nobody
said ("인산부한테 인산비날 가는 것이 있죠") was answered as if it made sense.
The transcriber knows when it is guessing; this is what the engine does with
that. The threshold itself lives in voice/transcription_policy and is
tested there; here the flag is set directly.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import unittest

from brain import guard_lines, near_miss
from brain.chat_engine import ChatEngine
from tests.turn_harness import build_engine

GARBLED = "인산부한테 인산비날 가는 것이 있죠"


class TheUnclearClipTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"

    def tearDown(self):
        self.engine.close()

    def _route(self, said: str, *, unclear: bool):
        self.engine._heard_unclearly = unclear
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._route_turn(said, timings={})

    def test_an_unclear_clip_is_asked_again(self):
        routing = self._route(GARBLED, unclear=True)

        self.assertEqual(routing.locked_response, guard_lines.say("didnt_catch", "ko"))

    def test_in_english_too(self):
        self.engine._turn_language = "en"

        routing = self._route("the bland paid the wheel of wet", unclear=True)

        self.assertEqual(routing.locked_response,
                         "Sorry, I didn't catch that. Could you say it again?")

    def test_never_twice_in_a_row(self):
        self._route(GARBLED, unclear=True)

        again = self._route(GARBLED, unclear=True)

        self.assertNotEqual(again.locked_response, guard_lines.say("didnt_catch", "ko"))

    def test_a_clear_turn_resets_it(self):
        self._route(GARBLED, unclear=True)
        self._route("오늘 날씨 어때?", unclear=False)

        routing = self._route(GARBLED, unclear=True)

        self.assertEqual(routing.locked_response, guard_lines.say("didnt_catch", "ko"))

    def test_typed_turns_are_never_asked(self):
        routing = self._route(GARBLED, unclear=False)

        self.assertNotEqual(routing.locked_response, guard_lines.say("didnt_catch", "ko"))

    def test_a_question_she_was_waiting_on_survives(self):
        slip = near_miss._slip("CBT 신청 서류?", "CBT", "CPT", near_miss.ACRONYM)
        self.engine._pending_slip = near_miss.PendingSlip(slip)

        self._route("응 음", unclear=True)

        self.assertIsNotNone(self.engine._pending_slip)

    def test_the_voice_loop_can_pass_it(self):
        self.assertIn("heard_unclearly", inspect.signature(ChatEngine.chat).parameters)


if __name__ == "__main__":
    unittest.main()
