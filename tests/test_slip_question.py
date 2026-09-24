"""Through the engine: "CBT" after a conversation about CPT is asked about.

The person's example, as they described it: they had been telling Elaina
about CPT and said "CBT" by accident. She should notice what they have been
talking about, ask "are you actually talking about CPT?", and go on with the
right one. Each answer does something different:

    yes / 응 / "CPT"      -> the turn is answered as if they had said CPT
    no / 아니 / "CBT"     -> the turn is answered as said, and never asked again
    anything else         -> a new turn; the question is dropped
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain import guard_lines, near_miss
from tests.turn_harness import build_engine


class TheSlipQuestionTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"
        self.engine.conversation.add("user", "UW 유학생인데 CPT 신청하려고 해")
        self.engine.conversation.add(
            "assistant",
            "CPT는 F-1 학생이 전공 과정의 일부로 교외에서 일할 수 있게 해 줍니다.",
        )

    def tearDown(self):
        self.engine.close()

    def _route(self, said: str):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._route_turn(said, timings={})

    def test_the_slip_is_asked_about(self):
        routing = self._route("CBT 신청하려면 서류 뭐가 필요해?")

        self.assertEqual(
            routing.locked_response,
            guard_lines.say("slip_question", "ko").format(
                heard="CBT", meant="CPT", quote="라고",
            ),
        )
        self.assertIsNotNone(self.engine._pending_slip)

    def test_yes_answers_the_turn_they_meant(self):
        self._route("CBT 신청하려면 서류 뭐가 필요해?")

        routing = self._route("응")

        self.assertEqual(routing.user_input, "CPT 신청하려면 서류 뭐가 필요해?")
        self.assertNotEqual(
            routing.locked_response,
            guard_lines.say("slip_question", "ko").format(
                heard="CBT", meant="CPT", quote="라고",
            ),
        )
        self.assertIsNone(self.engine._pending_slip)

    def test_the_corrected_question_is_looked_up(self):
        # The person asked for the right information to be searched. In the
        # English demo "yes" was answered from memory, and wrongly.
        from brain import capability_selection

        self._route("CBT 신청하려면 서류 뭐가 필요해?")

        routing = self._route("응")

        self.assertTrue(self.engine._search_the_correction)
        self.assertNotIn(
            routing.capability.capability,
            {capability_selection.DIRECT_ANSWER, capability_selection.UI_CONTROL},
        )

    def test_no_answers_the_turn_as_said_and_settles_the_pair(self):
        self._route("CBT 신청하려면 서류 뭐가 필요해?")

        routing = self._route("아니, CBT 맞아")

        self.assertEqual(routing.user_input, "CBT 신청하려면 서류 뭐가 필요해?")
        self.assertIn(frozenset(("CBT", "CPT")), self.engine._distinct_terms)
        # And it is not asked about again.
        again = self._route("CBT 비용은 얼마야?")
        self.assertIsNone(self.engine._pending_slip)
        self.assertNotIn("말씀이신가요", again.locked_response or "")

    def test_a_confirmed_slip_is_corrected_and_said_next_time(self):
        # Measured in the live demo: after "CBT? -- 응", the same slip spelled
        # out in Hangul was skipped, because CBT was now in the conversation.
        self._route("CBT 신청하려면 서류 뭐가 필요해?")
        self._route("응")

        routing = self._route("씨비티는 얼마나 걸려?")

        self.assertEqual(routing.user_input, "CPT는 얼마나 걸려?")
        self.assertEqual(
            self.engine._slip_assumed,
            guard_lines.say("slip_assumed", "ko").format(meant="CPT"),
        )
        self.assertIsNone(self.engine._pending_slip)

    def test_no_after_an_assumption_undoes_it(self):
        self._route("CBT 신청하려면 서류 뭐가 필요해?")
        self._route("응")
        self._route("씨비티는 얼마나 걸려?")

        routing = self._route("아니 CBT 맞아, 인지행동치료 말하는 거야")

        self.assertEqual(routing.user_input, "아니 CBT 맞아, 인지행동치료 말하는 거야")
        self.assertEqual(self.engine._known_slips, {})
        self.assertIn(frozenset(("CBT", "CPT")), self.engine._distinct_terms)

    def test_something_else_is_a_new_turn(self):
        self._route("CBT 신청하려면 서류 뭐가 필요해?")

        routing = self._route("내일 날씨 어때?")

        self.assertEqual(routing.user_input, "내일 날씨 어때?")
        self.assertIsNone(self.engine._pending_slip)

    def test_in_english_too(self):
        self.engine._turn_language = "en"
        self.engine.conversation.add("user", "I want to apply for CPT at UW.")

        routing = self._route("what documents do I need for CBT?")

        self.assertEqual(routing.locked_response, "You said CBT -- did you mean CPT?")


class TheMisheardNameTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"
        self.engine.conversation.add("user", "베인브리지 섬에 카지노 있어?")
        self.engine.conversation.add("assistant", "베인브리지 섬에는 카지노가 없습니다.")

    def tearDown(self):
        self.engine.close()

    def test_taken_as_the_held_name_and_said(self):
        with contextlib.redirect_stdout(io.StringIO()):
            routing = self.engine._route_turn(
                "배인브리지에서 시애틀까지 얼마나 걸려?", timings={},
            )

        self.assertEqual(routing.user_input, "베인브리지에서 시애틀까지 얼마나 걸려?")
        self.assertEqual(
            self.engine._slip_assumed,
            guard_lines.say("slip_assumed", "ko").format(meant="베인브리지"),
        )
        self.assertIsNone(self.engine._pending_slip)


class TheListeningTermsTests(unittest.TestCase):

    def test_the_voice_loop_gets_what_the_conversation_holds(self):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({})
        try:
            engine.conversation.add("user", "UW 유학생인데 CPT 신청하려고 해")
            engine.conversation.add("assistant", "CPT와 OPT는 다릅니다.")

            terms = engine.listening_terms()
        finally:
            engine.close()

        self.assertIn("CPT", terms)
        self.assertIn("OPT", terms)
        self.assertTrue(near_miss.held_terms(["CPT"]))


if __name__ == "__main__":
    unittest.main()
