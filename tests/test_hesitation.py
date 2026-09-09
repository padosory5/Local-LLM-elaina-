"""A noise while they think is not a question.

From a real Korean session, three turns into a conversation about the
user's university:

    said : 엄
    read : Confirm if computer engineering at Washington University in
           Seattle is associated with Bill Gates
    said : 그냥 어이가 없어서 한 표현이야

One syllable of hesitation came back from the router as a full repeat of
the previous question, and the user spent their next turn explaining that
they had not asked anything.

The router cannot do better than that on its own. A filler names no
subject, so the only thing in the prompt a subjectless turn can attach to
is the previous subject -- which is exactly what happened. It belongs to
the same closed-class family as the greeting, the acknowledgement and the
cancellation: a turn there is nothing to classify, answered without a
model call.

Not folded into the acknowledgement path, because they are different acts.
"ok" agrees with something and is answered with closure; "um" agrees with
nothing and is answered by getting out of the way.
"""

from __future__ import annotations

import unittest

from brain.chat_engine import _HESITATION


class WhatCountsAsAFillerTests(unittest.TestCase):

    FILLERS = (
        "엄", "음", "흠", "으음", "어어", "아아", "저기", "그",
        "um", "uhh", "hmm", "ah", "Um...", "erm", "oh",
    )
    NOT_FILLERS = (
        # Acknowledgements. A different act, answered differently.
        "ok", "네", "알겠어", "그래",
        # A bare "어" is 반말 for "yeah" far more often than it is a
        # filler, so it takes the normal path. Lengthened -- "어어" -- it
        # is unambiguous and is in the list above.
        "어", "아", "응",
        # Real words that start with the same syllable.
        "엄마", "어디야", "음식", "아니", "그런데",
        # A filler with a sentence behind it is the sentence.
        "hmm what about it", "어제 뭐했어", "음 그러면 다른거 알려줘",
    )

    def test_every_filler_is_recognised(self):
        for said in self.FILLERS:
            with self.subTest(said=said):
                self.assertTrue(
                    _HESITATION.fullmatch(said), f"{said!r} is a filler",
                )

    def test_nothing_else_is(self):
        for said in self.NOT_FILLERS:
            with self.subTest(said=said):
                self.assertIsNone(
                    _HESITATION.fullmatch(said),
                    f"{said!r} is not a filler",
                )


class TheEngineAnswersWithoutRoutingTests(unittest.TestCase):

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def test_a_korean_filler_gets_a_short_korean_line(self):
        self.engine._turn_language = "ko"

        routing = self.engine._route_turn("엄", timings={})

        self.assertEqual(routing.route.intent, "conversation")
        self.assertEqual(routing.route.reason, "A filler with nothing outstanding.")
        self.assertEqual(routing.locked_response, "네, 듣고 있습니다.")

    def test_an_english_filler_gets_a_short_english_line(self):
        self.engine._turn_language = "en"

        routing = self.engine._route_turn("um", timings={})

        self.assertEqual(routing.locked_response, "I'm listening.")

    def test_it_does_not_repeat_the_previous_question(self):
        # The measured failure: the filler came back carrying the whole
        # previous subject.
        self.engine._turn_language = "ko"
        self.engine.task_sessions.note_recommendation_turn(
            "빌게이츠가 후원하는 컴퓨터공학과에 대해 알려줘",
            subject="Washington University in Seattle",
        )

        routing = self.engine._route_turn("엄", timings={})

        self.assertNotIn("Bill Gates", routing.route.normalized_request)
        self.assertNotIn("Washington", routing.route.normalized_request)
        self.assertEqual(routing.route.normalized_request, "엄")


if __name__ == "__main__":
    unittest.main()
