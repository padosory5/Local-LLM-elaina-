"""Defects the Phase 3 final rerun exposed, checked through whole turns.

Each was read out of the runs in runtime/evals/p3f by hand
(docs/COMMUNICATION_FINDINGS.md, "Phase 3, final"). The unit halves sit
beside their modules; these pin the wiring through ``ChatEngine.chat()``.
"""

import contextlib
import io
import unittest

from core import turn_trace
from tests.test_turn_trace import _traced
from tests.turn_harness import build_engine

COLD_BREW = "콜드브루는 실온에서 12시간 이상 우려내면 됩니다."
STEEP_ROUTE = {
    "intent": "calculation", "confidence": 1.0,
    "normalized_request": "How long should cold brew coffee be steeped?",
    "speech_act": "information_request", "is_follow_up": True,
    "information_freshness": "stable",
}


class _Turns(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({
                "콜드브루 만드는 법": {
                    "intent": "knowledge_question", "confidence": 1.0,
                    "normalized_request": "How do I make cold brew coffee?",
                    "speech_act": "information_request",
                    "information_freshness": "stable",
                },
                "얼마나 우려야": STEEP_ROUTE,
            })
        _traced(self.engine)
        self.engine.client.reply = COLD_BREW

    def tearDown(self):
        self.engine.close()

    def chat(self, said):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.chat(said)
        return turn_trace.last().as_record()


class AFollowUpWithoutArithmeticTests(_Turns):
    """"얼마나 우려야 돼?" after a cold-brew answer, routed as a calculation."""

    def test_nothing_is_verified_that_was_never_calculated(self):
        self.chat("콜드브루 만드는 법 알아?")
        record = self.chat("얼마나 우려야 돼?")

        evidence = record.get("evidence") or {}
        self.assertNotIn("calculation", evidence)
        self.assertFalse(any(
            str(call.get("caller", "")).endswith("calculation_planner:_ask")
            for call in record.get("model_calls") or ()
        ))

    def test_her_repeated_answer_is_asked_again_as_the_whole_question(self):
        asked = []

        def answered_on_its_own(question, **_turn_model):
            asked.append(question)
            return "콜드브루는 12시간에서 24시간 정도 우리면 됩니다."

        self.engine._answered_on_its_own = answered_on_its_own
        self.chat("콜드브루 만드는 법 알아?")
        self.chat("얼마나 우려야 돼?")

        self.assertEqual(asked, [STEEP_ROUTE["normalized_request"]])


class AClarificationKeepsItsContextTests(unittest.TestCase):
    """The FAST/DEEP baseline: "얼마나 우려야 돼?" after a cold-brew answer,
    routed as a clarification whose reading stayed the words said, was asked
    again without its history and answered about tumble-drying clothes."""

    def turn(self, route):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({
                "콜드브루 만드는 법": {
                    "intent": "knowledge_question", "confidence": 1.0,
                    "normalized_request": "How do I make cold brew coffee?",
                    "speech_act": "information_request",
                    "information_freshness": "stable",
                },
                "얼마나 우려야": route,
            })
        _traced(engine)
        engine.client.reply = COLD_BREW
        asked = []

        def answered_on_its_own(question, **_turn_model):
            asked.append(question)
            return "어떤 옷인지에 따라 달라집니다."

        engine._answered_on_its_own = answered_on_its_own
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                engine.chat("콜드브루 만드는 법 알아?")
                shown = engine.chat("얼마나 우려야 돼?")
        finally:
            engine.close()
        return asked, shown

    @staticmethod
    def _route(**fields):
        route = dict(STEEP_ROUTE)
        route.update(fields)
        return route

    def test_a_clarification_is_never_asked_again_without_its_history(self):
        for reading in ("얼마나 우려야 돼?", "How long should cold brew coffee be steeped?"):
            with self.subTest(reading=reading):
                asked, shown = self.turn(self._route(intent="clarification",
                                                     normalized_request=reading))
                self.assertEqual(asked, [])
                self.assertNotIn("옷", shown)

    def test_a_follow_up_left_as_the_words_said_keeps_its_context(self):
        asked, shown = self.turn(self._route(normalized_request="얼마나 우려야 돼?"))
        self.assertEqual(asked, [])
        self.assertNotIn("옷", shown)

    def test_a_question_that_stands_on_its_own_is_still_asked_again(self):
        # The stage's own purpose: a new value question answered with the
        # last answer is asked again, without the history it copied from.
        asked, _ = self.turn(self._route(is_follow_up=False,
                                         normalized_request="How long should cold brew steep?"))
        self.assertEqual(asked, ["How long should cold brew steep?"])


class TheReAskIsWrittenByTheWordsModelTests(unittest.TestCase):
    """Stage B: with the 8B deciding and the 27B writing, the re-ask of
    not_her_last_answer went to the 8B -- one answer of a 27B turn was the
    8B's. It is written by the turn's words model now."""

    def test_the_re_ask_uses_the_words_model(self):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({})
        engine.model, engine.conversation_model = "decision-model", "words-model"
        engine._turn_language = "en"
        before = "Water freezes at 32 degrees Fahrenheit."
        engine._things_she_has_said = lambda: (before, [])
        used = []

        class Client:
            def chat(self, **kwargs):
                used.append(kwargs.get("model"))
                return {"message": {"content": "Water boils at 212 degrees Fahrenheit."}}

        engine.client = Client()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                engine._not_her_last_answer("At what temperature does water boil?", before)
                engine._not_her_last_answer("At what temperature does water boil?", before,
                                            model="turn-model")
        finally:
            engine.close()
        self.assertEqual(used, ["words-model", "turn-model"])

    def test_a_whole_turn_re_asks_with_the_model_that_wrote_it(self):
        # The cold-brew value turn, standing on its own, whose draft repeats
        # the previous answer -- the shape that triggers the re-ask.
        steep = dict(STEEP_ROUTE, is_follow_up=False,
                     normalized_request="How long should cold brew steep?")
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({
                "콜드브루 만드는 법": {
                    "intent": "knowledge_question", "confidence": 1.0,
                    "normalized_request": "How do I make cold brew coffee?",
                    "speech_act": "information_request", "information_freshness": "stable",
                },
                "얼마나 우려야": steep,
            })
        _traced(engine)
        engine.conversation_model = "words-model"
        engine.client.reply = COLD_BREW
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                engine.chat("콜드브루 만드는 법 알아?")
                engine.chat("얼마나 우려야 돼?")
            record = turn_trace.last().as_record()
        finally:
            engine.close()
        re_asks = [c for c in record.get("model_calls") or ()
                   if str(c.get("caller", "")).endswith("_answered_on_its_own")]
        self.assertTrue(re_asks, "the repeated answer was not asked again")
        self.assertEqual({c.get("model") for c in re_asks}, {"words-model"})


class AVariableIsNotArithmeticTests(unittest.TestCase):
    """A question about a variable asks for an expression; the calculator
    only verifies numbers (FAST/DEEP baseline: "power rule applied: 5")."""

    def test_the_reader(self):
        from brain.spoken_notation import names_a_variable

        for text in (
            "Calculate the derivative of the function x squared plus 3x",
            "What's the derivative of x^2 + 3x?", "derivative of 3x", "d/dx of x²",
            "integral of f(x) from 0 to 1", "x 제곱 더하기 3x를 미분하면?", "x²+3x의 도함수",
            "sin x at 30 degrees", "solve 2y + 4 = 10",
        ):
            with self.subTest(text=text):
                self.assertTrue(names_a_variable(text))
        for text in (
            "split 650 among 100, 100, 50", "What is 15% of 80?",
            "Plan A costs $50 a month and plan B $70; what do I save over 3 months with A?",
            "I need 3 cups of flour, how many grams is that?", "a 15% tip on $80",
            "prices went up by a third, what is the new price of $90?",
            "I save $50 a month over a year, total?", "what is 12 x 3", "5 x 4 = ?",
            "divide 100 by 4", "약 3시간 걸리면 몇 분이야?",
        ):
            with self.subTest(text=text):
                self.assertFalse(names_a_variable(text))

    def test_a_calculation_turn_about_a_variable_files_nothing_as_verified(self):
        route = {"intent": "calculation", "confidence": 1.0,
                 "normalized_request": "Calculate the derivative of the function x squared plus 3x",
                 "speech_act": "information_request", "information_freshness": "stable"}
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({"derivative of x": route})
        _traced(engine)
        engine.client.reply = "The derivative is 2x + 3."
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                engine.chat("What's the derivative of x squared plus 3x?")
            record = turn_trace.last().as_record()
        finally:
            engine.close()
        self.assertNotIn("calculation", record.get("evidence") or {})
        self.assertFalse(any(
            str(call.get("caller", "")).endswith("calculation_planner:_ask")
            for call in record.get("model_calls") or ()
        ))


class ADamagedCopyIsOfTheirNumberTests(unittest.TestCase):
    """The value guard on the engine: a calculation in the turn's ledger
    does not make a correct steeping time a damaged copy of it."""

    def setUp(self):
        from brain import evidence as turn_evidence

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"
        self.engine._ledger().add(
            turn_evidence.CALCULATION,
            "Verified calculation.\nCoffee needed for 500g water at 1:4 ratio: 125",
            source="trusted tool result",
        )

    def tearDown(self):
        self.engine.close()

    def guarded(self, reply, said):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._enforce_grounded_values(
                reply, user_input=said, action_performed=False,
            )

    def test_the_steeping_time_stays(self):
        reply = "12시간에서 24시간 정도 우려내면 됩니다."
        self.assertEqual(self.guarded(reply, "얼마나 우려야 돼?"), reply)

    def test_their_number_damaged_still_goes(self):
        guarded = self.guarded("Your budget is 150.",
                               "My budget is 1500. Repeat that back to me.")
        self.assertNotIn("150", guarded)


if __name__ == "__main__":
    unittest.main()
