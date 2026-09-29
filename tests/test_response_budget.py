"""Phase 3D: how much room a reply gets, from what the reply is for.

A value keeps the old ceiling. An explanation gets room for what the thing
is for and one concrete case. A re-explanation, or an explicit request for
depth, gets room to take another route. Social acts keep their contracts.
"""

import unittest

from brain import response_budget as budget
from brain.response_policy import ResponseLimits


class KindTests(unittest.TestCase):

    def test_values_stay_short(self):
        for intent in ("time_question", "calculation"):
            self.assertEqual(budget.kind_for(intent=intent, detailed=False,
                                             value_response=False), budget.VALUE)
        self.assertEqual(budget.kind_for(intent="knowledge_question", detailed=False,
                                         value_response=True), budget.VALUE)

    def test_an_explanation_gets_room(self):
        self.assertEqual(budget.kind_for(intent="knowledge_question", detailed=False,
                                         value_response=False), budget.EXPLAIN)

    def test_depth_or_a_second_try_gets_more(self):
        self.assertEqual(budget.kind_for(intent="knowledge_question", detailed=True,
                                         value_response=False), budget.ELABORATE)
        self.assertEqual(budget.kind_for(intent="clarification", detailed=False,
                                         value_response=False), budget.ELABORATE)

    def test_a_recommendation_keeps_its_measured_length(self):
        self.assertEqual(budget.kind_for(intent="conversation", detailed=False,
                                         value_response=False, recommendation=True),
                         budget.VALUE)

    def test_ceilings_come_from_config(self):
        class Config:
            def get(self, section, key, default=None, required=False):
                return {"explain_max_words": 99}.get(key, default)

        self.assertEqual(budget.ceilings(Config())[budget.EXPLAIN][0], 99)
        self.assertEqual(budget.ceilings(None)[budget.VALUE], budget.DEFAULTS[budget.VALUE])

    def test_the_goal_is_said_in_the_reply_language(self):
        explain = budget.ResponseBudget(budget.EXPLAIN, 130, 6)
        self.assertIn("concrete case", explain.goal("en"))
        self.assertIn("구체적인", explain.goal("ko"))

    def test_the_prompt_carries_the_goal_and_the_ceiling(self):
        limits = ResponseLimits(max_words=130, max_sentences=6, goal="Explain it so it lands.")
        rules = limits.instruction(language="en")
        self.assertIn("at most 130 spoken words", rules)
        self.assertIn("Explain it so it lands.", rules)


class WholeTurnTests(unittest.TestCase):

    def _prompt(self, said, route):
        from core import turn_trace
        from tests.test_turn_trace import _traced
        from tests.turn_harness import build_engine

        engine = build_engine({said[:12]: route})
        _traced(engine)
        engine.chat(said)
        record = turn_trace.last().as_record()
        call = next(c for c in record["model_calls"]
                    if str(c.get("caller", "")).endswith(":collect_answer"))
        return record, str(call["messages"])

    def test_an_explanation_is_not_squeezed_into_two_sentences(self):
        record, prompt = self._prompt("Why does ice float on water?", {
            "intent": "knowledge_question", "confidence": 1.0,
            "normalized_request": "Why does ice float on water?",
            "speech_act": "information_request", "information_freshness": "stable",
        })
        self.assertEqual(record["context"]["limits"]["budget"], budget.EXPLAIN)
        self.assertIn("at most 6 complete sentences", prompt)
        self.assertNotIn("at most 2 complete sentences", prompt)

    def test_a_value_question_keeps_the_short_ceiling(self):
        record, prompt = self._prompt("What is 15% of 80?", {
            "intent": "knowledge_question", "confidence": 1.0,
            "normalized_request": "What is 15% of 80?",
            "speech_act": "information_request", "information_freshness": "stable",
        })
        self.assertEqual(record["context"]["limits"]["budget"], budget.VALUE)


if __name__ == "__main__":
    unittest.main()
