"""Phase 3D, as corrected after the rerun: room and purpose, from the router.

How much room a reply gets (value, explain, elaborate) and what fills it
(a direct statement, an explanation grounded in a case, an example asked
for) are two decisions, both read from signals the router already gives:

* ``speech_act`` -- a thanks, a remark or an approval asks for nothing to be
  explained. The rerun measured the cost of forgetting it: "Thanks, that
  makes sense" was given the explanation budget, and the 8B explained hash
  tables again.
* ``answer_shape`` -- a fact, a value, a formula or a definition is stated;
  a why/how gets a concrete case; an example is given when one is asked
  for. The rerun measured this one too: "What's the quadratic formula?"
  came back with a worked example nobody asked for.
"""

import unittest

from brain import response_budget as budget
from brain.response_policy import ResponseLimits


def _budget(**kwargs):
    kwargs.setdefault("detailed", False)
    kwargs.setdefault("value_response", False)
    return budget.budget_for(**kwargs)


class RoomTests(unittest.TestCase):

    def test_values_stay_short(self):
        for intent in ("time_question", "calculation"):
            self.assertEqual(_budget(intent=intent).kind, budget.VALUE)
        self.assertEqual(_budget(intent="knowledge_question", value_response=True).kind,
                         budget.VALUE)

    def test_an_information_request_gets_room(self):
        self.assertEqual(_budget(intent="knowledge_question").kind, budget.EXPLAIN)
        self.assertEqual(_budget(intent="conversation",
                                 speech_act="information_request").kind, budget.EXPLAIN)

    def test_a_turn_that_asks_nothing_gets_no_explanation(self):
        for act in ("approval_response", "social", "statement", "correction"):
            with self.subTest(act=act):
                found = _budget(intent="conversation", speech_act=act)
                self.assertEqual(found.kind, budget.VALUE)
                self.assertEqual(found.goal("en"), "")

    def test_the_router_saying_an_explanation_is_wanted_counts(self):
        # "I still don't get it", filed by the 8B as a correction whose answer
        # is an explanation.
        self.assertEqual(_budget(intent="conversation", speech_act="correction",
                                 answer_shape="explain").kind, budget.EXPLAIN)
        # A remark whose shape is a plain statement asks for nothing.
        self.assertEqual(_budget(intent="conversation", speech_act="statement",
                                 answer_shape="state").kind, budget.VALUE)

    def test_depth_or_a_second_try_gets_more(self):
        self.assertEqual(_budget(intent="knowledge_question", detailed=True).kind,
                         budget.ELABORATE)
        self.assertEqual(_budget(intent="clarification").kind, budget.ELABORATE)

    def test_a_recommendation_keeps_its_measured_length_and_its_own_rules(self):
        found = _budget(intent="conversation", recommendation=True,
                        speech_act="advice")
        self.assertEqual(found.kind, budget.VALUE)
        self.assertEqual(found.goal("en"), "")

    def test_ceilings_come_from_config(self):
        class Config:
            def get(self, section, key, default=None, required=False):
                return {"explain_max_words": 99}.get(key, default)

        self.assertEqual(budget.ceilings(Config())[budget.EXPLAIN][0], 99)
        self.assertEqual(budget.ceilings(None)[budget.VALUE], budget.DEFAULTS[budget.VALUE])


class PurposeTests(unittest.TestCase):

    def test_a_statement_is_answered_directly(self):
        found = _budget(intent="knowledge_question", answer_shape="state")
        self.assertEqual(found.shape, budget.STATE)
        self.assertIn("No example or application unless they asked", found.goal("en"))

    def test_an_explanation_is_grounded_in_a_case(self):
        found = _budget(intent="knowledge_question", answer_shape="explain")
        self.assertIn("one concrete case", found.goal("en"))

    def test_an_explanation_says_what_it_solves_and_may_work_an_example(self):
        # Dropped when the goals were split by shape; the final rerun's
        # 27B explanations lost their concrete case without it.
        found = _budget(intent="knowledge_question", answer_shape="explain")
        self.assertIn("what problem it solves", found.goal("en"))
        self.assertIn("worked example", found.goal("en"))
        self.assertIn("why it happens", found.goal("en"))
        self.assertIn("어떤 문제를 푸는지", found.goal("ko"))

    def test_a_value_answer_is_not_invited_to_add_context(self):
        # Candidate after the FAST/DEEP baseline: "at most one short
        # sentence of context" read as an invitation to append one.
        found = _budget(intent="time_question")
        self.assertIn("only when the value would be misread", found.goal("en"))
        self.assertNotIn("sentence of context", found.goal("en"))
        self.assertIn("오해될 수 있을 때만", found.goal("ko"))

    def test_an_example_is_given_when_asked_for(self):
        found = _budget(intent="knowledge_question", answer_shape="example")
        self.assertIn("They asked for an example", found.goal("en"))

    def test_an_unknown_shape_adds_a_case_only_when_it_helps(self):
        found = _budget(intent="knowledge_question", answer_shape="")
        self.assertIn("only when it makes the answer clearer", found.goal("en"))

    def test_a_check_of_their_understanding_gets_a_verdict_first(self):
        # The user, rating the simulated learners: "is this correct?" is
        # answered yes / not quite first, then in easier words.
        for intent, shape in (("knowledge_question", "state"), ("knowledge_question", "explain"),
                              ("knowledge_question", "example"), ("knowledge_question", ""),
                              ("clarification", "")):
            with self.subTest(intent=intent, shape=shape):
                found = _budget(intent=intent, answer_shape=shape)
                self.assertIn("start by saying whether they have: yes, partly, or not quite",
                              found.goal("en"))
                self.assertIn("먼저 맞는지부터 말합니다", found.goal("ko"))
        # A value is not an explanation, and the contract says the same words.
        self.assertNotIn("understood it right", _budget(intent="time_question").goal("en"))
        from brain import explain_contract
        self.assertIn(budget.CHECKING_UNDERSTANDING[0],
                      explain_contract.requirements("en", max_words=130))
        self.assertIn(budget.CHECKING_UNDERSTANDING[1],
                      explain_contract.requirements("ko", max_words=130))

    def test_the_goal_is_said_in_the_reply_language(self):
        found = _budget(intent="knowledge_question", answer_shape="example")
        self.assertIn("예시", found.goal("ko"))

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

    @staticmethod
    def _route(said, **fields):
        route = {"intent": "knowledge_question", "confidence": 1.0,
                 "normalized_request": said, "speech_act": "information_request",
                 "information_freshness": "stable"}
        route.update(fields)
        return route

    def test_an_explanation_is_not_squeezed_into_two_sentences(self):
        record, prompt = self._prompt("Why does ice float on water?", self._route(
            "Why does ice float on water?", answer_shape="explain"))
        self.assertEqual(record["context"]["limits"]["budget"], budget.EXPLAIN)
        self.assertIn("at most 6 complete sentences", prompt)
        self.assertIn("one concrete case", prompt)

    def test_a_formula_is_stated_without_an_unasked_example(self):
        record, prompt = self._prompt("What's the quadratic formula?", self._route(
            "What is the quadratic formula?", answer_shape="state"))
        self.assertEqual(record["context"]["limits"]["shape"], budget.STATE)
        self.assertIn("No example or application unless they asked", prompt)
        self.assertNotIn("show one concrete case", prompt)

    def test_thanks_is_not_given_an_explanation_to_write(self):
        record, prompt = self._prompt("Thanks, that makes sense.", self._route(
            "Thanks, that makes sense.", intent="conversation",
            speech_act="approval_response"))
        self.assertEqual(record["context"]["limits"]["budget"], budget.VALUE)
        self.assertNotIn("Explain it so it lands", prompt)
        self.assertNotIn("concrete case", prompt)

    def test_a_value_question_keeps_the_short_ceiling(self):
        record, prompt = self._prompt("What is 15% of 80?", self._route(
            "What is 15% of 80?"))
        self.assertEqual(record["context"]["limits"]["budget"], budget.VALUE)


if __name__ == "__main__":
    unittest.main()
