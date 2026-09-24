"""A reaction still too long after the re-say is cut, not kept.

Measured on the paired baseline: English too_verbose was 6 of 72 turns and
Korean's 2, and the log each time read "The re-said version still reads as
too_verbose; kept the original" -- the re-say was asked to shorten it and
came back as long. On a reaction, a receipt, a greeting or a goodbye the
length is register, not content (``_LENGTH_IS_STYLE``), so the cut loses
nothing a person would miss:

    That sounds tough. Take some time to breathe and let it settle.
    You've got this.

An answer is never cut here: its sentences can carry the value that was
asked for.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from brain import conversation_style as style
from brain.conversation_style import (
    ANSWER,
    RECEIPT,
    REACT,
    TOO_VERBOSE,
    RoboticTells,
    cut_to_length,
)
from tests.turn_harness import build_engine

ROUGH = ("That sounds tough. Take some time to breathe and let it settle. "
         "You've got this.")


def _too_long(text: str, act: str) -> bool:
    return any(
        finding.failure == TOO_VERBOSE
        for finding in RoboticTells.inspect(text, act=act)
    )


class TheMeasuredRepliesTests(unittest.TestCase):

    CASES = (
        (REACT,
         "That sounds tough. Take some time to breathe and let it settle. "
         "You've got this.",
         "That sounds tough. Take some time to breathe and let it settle."),
        # The sentence that says nothing goes first.
        (REACT,
         "I see. Sounds like you've already seen that one. What else are you "
         "interested in?",
         "Sounds like you've already seen that one. What else are you "
         "interested in?"),
        # Two sentences, but 21 words where a receipt allows 20.
        (RECEIPT,
         "That sounds like a day that could use a break. Let me know if you'd "
         "like help organizing the next steps.",
         "That sounds like a day that could use a break."),
        (REACT,
         "오늘 하루 종일 회의만 하셨군요. 힘드셨겠습니다. 조금씩 쉬어가시는 게 좋습니다.",
         "오늘 하루 종일 회의만 하셨군요. 힘드셨겠습니다."),
    )

    def test_each_is_cut_to_its_acts_length(self):
        for act, said, cut in self.CASES:
            with self.subTest(said=said):
                self.assertTrue(_too_long(said, act))
                self.assertEqual(cut_to_length(said, act), cut)
                self.assertFalse(_too_long(cut, act))


class WhatIsNotCutTests(unittest.TestCase):

    def test_an_answer_keeps_every_sentence(self):
        said = ("The Sony WH-CH720N is about $99. It lasts 35 hours. It folds. "
                "It has noise cancelling. The Anker Q20i is cheaper.")

        self.assertEqual(cut_to_length(said, ANSWER), said)

    def test_a_reply_within_its_length_is_returned_as_it_was(self):
        said = "That sounds tough. Get some rest tonight."

        self.assertIs(cut_to_length(said, REACT), said)

    def test_never_below_one_sentence(self):
        said = ("That sounds like the kind of long and draining day that would "
                "leave anyone wanting to lie down for a while.")

        self.assertEqual(cut_to_length(said, RECEIPT), said)

    def test_the_acts_it_applies_to_are_the_fact_free_ones(self):
        self.assertEqual(
            set(style._LENGTH_IS_STYLE),
            {style.GREET, style.RECEIPT, style.REACT, style.CLOSE},
        )


class TheEngineCutsTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def _voice(self, draft: str, **resay) -> str:
        with mock.patch.object(self.engine, "_resay", **resay), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.engine._say_it_in_her_voice(
                draft, act=REACT, user_input="today was kind of rough",
                model="test", keep_alive=None, max_words=60,
            )

    def test_length_alone_costs_no_model_call(self):
        spoken = self._voice(ROUGH, side_effect=AssertionError("re-said"))

        self.assertEqual(
            spoken,
            "That sounds tough. Take some time to breathe and let it settle.",
        )

    def test_a_resay_that_is_still_long_is_cut(self):
        # Service phrasing as well, so the re-say is asked for; it comes
        # back without the phrasing and still three sentences long.
        draft = ("I'm here if you need anything. That sounds tough. Take some "
                 "time to breathe. You've got this.")

        spoken = self._voice(
            draft,
            return_value="That sounds tough. Take some time to breathe. "
                         "You've got this.",
        )

        self.assertEqual(spoken, "That sounds tough. Take some time to breathe.")


if __name__ == "__main__":
    unittest.main()
