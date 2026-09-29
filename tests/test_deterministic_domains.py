"""Phase 3B: answers code computes are not routed by a model's guess, and R9.

* The clock, arithmetic and unit conversion are claimed by their own
  grammars (brain/domain_resolver.py). A time question never becomes a web
  search because a router called it live information -- in either mode.
* The clock layer reads every place named, compares two of them, and says
  the time in Korean with 오전/오후 computed, not translated.
* R9: the router's ``clarification`` is the person asking about her last
  answer. It keeps the conversation and is answered; Elaina needing to ask
  is ``needs_clarification``.
"""

import os
import unittest
from datetime import datetime
from unittest.mock import patch

from brain import domain_resolver, world_clock
from core import turn_trace


class ClaimsTests(unittest.TestCase):

    def test_the_clock_is_claimed(self):
        for said in (
            "What's the time?",
            "what time is it now",
            "Which day is it today?",
            "What's the date?",
            "Tell me the time in London and Tokyo.",
            "지금 몇 시예요?",
            "오늘 무슨 요일이야?",
            "도쿄는 지금 몇 시야?",
        ):
            with self.subTest(said=said):
                found = domain_resolver.claim(said)
                self.assertIsNotNone(found)
                self.assertEqual(found.domain, domain_resolver.CLOCK)
                self.assertEqual(found.intent, "time_question")

    def test_what_only_looks_like_the_clock_is_not(self):
        for said in (
            "What time does the bakery close?",
            "What time should I leave for the airport?",
            "It's time for bed.",
            "What time is it in Narnia?",
            "When did the Berlin wall fall?",
            "몇 시에 출발해야 해?",
        ):
            with self.subTest(said=said):
                self.assertIsNone(domain_resolver.claim(said))

    def test_arithmetic_is_claimed_and_computed(self):
        cases = {
            "what's 17 times 3": "51",
            "Calculate 250 divided by 4": "62.5",
            "What is 20% of 45?": "9",
            "9 - 13": "-4",
            "5 곱하기 6은 얼마야?": "30",
        }
        for said, value in cases.items():
            with self.subTest(said=said):
                found = domain_resolver.claim(said)
                self.assertEqual(found.domain, domain_resolver.ARITHMETIC)
                self.assertTrue(found.result.rstrip().endswith(f"= {value}"), found.result)

    def test_a_word_problem_is_left_to_the_planner(self):
        for said in (
            "If I buy 3 shirts at $12 each, how much do I spend?",
            "I ran 5 km and then 3 more",
            "what is 2020's best film",
        ):
            with self.subTest(said=said):
                self.assertIsNone(domain_resolver.claim(said))

    def test_unit_conversions_are_computed(self):
        cases = {
            "What is 0 °C in Fahrenheit?": "32 °F",
            "convert 10 kilograms to pounds": "22.0462 pounds",
            "How many teaspoons in a tablespoon?": "3 teaspoons",
            "화씨 212도는 섭씨로 몇 도야?": "100 °C",
        }
        for said, stated in cases.items():
            with self.subTest(said=said):
                found = domain_resolver.claim(said)
                self.assertEqual(found.domain, domain_resolver.CONVERSION)
                self.assertTrue(found.expression.endswith(stated), found.expression)

    def test_mismatched_dimensions_are_not_converted(self):
        self.assertIsNone(domain_resolver.claim("What is 5 kg in miles?"))

    def test_mode_defaults_and_overrides(self):
        with patch.dict(os.environ, {"ELAINA_DOMAINS": "off"}):
            self.assertEqual(domain_resolver.mode(), "off")
        with patch.dict(os.environ, {"ELAINA_DOMAINS": "nonsense"}):
            self.assertEqual(domain_resolver.mode(), "act")


class ClockLayerTests(unittest.TestCase):

    def test_every_named_place_is_read_in_order(self):
        self.assertEqual(world_clock.read_places("the time in Paris versus Chicago"),
                         ["paris", "chicago"])
        self.assertEqual(world_clock.read_places("런던은 몇 시고 도쿄는 몇 시야"), ["런던", "도쿄"])

    def test_two_places_are_compared_with_each_other(self):
        line = world_clock.compare("london", "tokyo")
        self.assertRegex(line, r"^Tokyo is \d+ hours ahead of London\.$")

    def test_korean_hours_count_from_twelve_either_side_of_noon(self):
        say = lambda hour, minute=0: world_clock.spoken_time(
            datetime(2026, 1, 5, hour, minute), "ko", with_date=False)
        self.assertEqual(say(0, 5), "오전 12시 5분")
        self.assertEqual(say(11, 59), "오전 11시 59분")
        self.assertEqual(say(12), "오후 12시")
        self.assertEqual(say(13, 30), "오후 1시 30분")
        self.assertEqual(say(23), "오후 11시")

    def test_the_zone_label_is_never_the_os_translation(self):
        label = world_clock.zone_label(world_clock.local_now("local"))
        self.assertTrue(label == "" or label.isascii(), label)

    def test_the_prompt_carries_the_korean_time_for_a_korean_reply(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine.config.data.setdefault("time", {})["timezone"] = "Asia/Seoul"
        context = engine.build_time_context("지금 몇 시야?", language="ko")
        self.assertRegex(context, r"(오전|오후) \d{1,2}시")
        self.assertIn("UTC+0900", context)
        self.assertIn("only if the question asks", context)


def _traced(engine):
    """The recorder in front of the scripted model, wherever it is held."""
    from tests.test_turn_trace import _traced as traced

    traced(engine)
    if hasattr(engine.calculation_planner, "client"):
        engine.calculation_planner.client = engine.client
    return engine


def _router(intent, said, **fields):
    decision = {"intent": intent, "confidence": 1.0, "normalized_request": said,
                "speech_act": "information_request"}
    decision.update(fields)
    return decision


class ClockTurnTests(unittest.TestCase):
    """The 27B's routing of a time question, through a whole turn."""

    LIVE = _router("time_question", "What is the current time?",
                   information_freshness="live", requires_external_evidence=True,
                   topic="current_time", topic_shift=True)

    def _turn(self, domains):
        from tests.turn_harness import build_engine

        with patch.dict(os.environ, {"ELAINA_DOMAINS": domains}):
            engine = build_engine({"What time is it": self.LIVE})
            engine.client.reply = "It's a quarter past nine."
            engine.chat("What time is it?")
        return turn_trace.last().as_record()

    def _assert_clock_not_search(self, record):
        kinds = [item["kind"] for item in record["evidence"].get("ledger", ())]
        self.assertIn("clock", kinds)
        self.assertNotIn("search", kinds)
        self.assertEqual(record["context"]["route"]["intent"], "time_question")

    def test_acting_the_clock_is_read_and_nothing_is_searched(self):
        record = self._turn("act")
        self._assert_clock_not_search(record)
        self.assertEqual(record["context"]["domain_claim"]["mode"], "act")

    def test_in_shadow_the_router_decides_and_still_nothing_is_searched(self):
        record = self._turn("shadow")
        self._assert_clock_not_search(record)
        self.assertEqual(record["context"]["domain_claim"]["mode"], "shadow")


class CalculationTurnTests(unittest.TestCase):

    def test_a_claimed_sum_is_computed_without_the_planner(self):
        from tests.turn_harness import build_engine

        with patch.dict(os.environ, {"ELAINA_DOMAINS": "act"}):
            engine = _traced(build_engine())
            engine.client.reply = "That's 51."
            engine.chat("what's 17 times 3")
        record = turn_trace.last().as_record()
        ledger = record["evidence"].get("ledger", ())
        self.assertTrue(any(item["kind"] == "calculation" and "= 51" in item["text"]
                            for item in ledger), ledger)
        callers = [call.get("caller", "") for call in record["model_calls"]]
        self.assertFalse([c for c in callers if "calculation_planner" in c], callers)
        self.assertFalse([c for c in callers if c.endswith(":route")], callers)


class ClarificationTurnTests(unittest.TestCase):
    """R9. "I still don't get it" keeps the conversation and is answered."""

    def _conversation(self, first, first_route, second, second_route, answer):
        from tests.turn_harness import build_engine

        engine = _traced(build_engine({first[:18]: first_route, second[:10]: second_route}))
        engine.client.reply = answer
        engine.chat(first)
        engine.client.reply = "Here it is another way."
        engine.chat(second)
        record = turn_trace.last().as_record()
        call = next(c for c in record["model_calls"]
                    if str(c.get("caller", "")).endswith(":collect_answer"))
        messages = call["messages"]
        roles = [m.get("role") for m in messages] if isinstance(messages, list) else []
        text = str(messages)
        return record, roles, text

    def _assert_follow_up(self, record, roles, text, earlier_words):
        self.assertEqual(record["context"]["route"]["intent"], "clarification")
        self.assertIn("assistant", roles, "the earlier answer was not in the prompt")
        self.assertIn(earlier_words, text)
        self.assertNotIn("Ask one short clarifying question", text)
        self.assertIn("WHAT THIS TURN IS", text)

    def test_english_still_dont_get_it(self):
        record, roles, text = self._conversation(
            "What is a hash table?",
            _router("knowledge_question", "What is a hash table?",
                    information_freshness="stable", topic="hash table"),
            "I still don't get it.",
            # The worst case measured: a new subject and a topic shift.
            _router("clarification", "Please clarify the previous explanation.",
                    topic="unclear explanation", topic_shift=True, is_follow_up=True),
            "A hash table stores values under keys it can find instantly.",
        )
        self._assert_follow_up(record, roles, text, "stores values under keys")

    def test_english_another_way(self):
        record, roles, text = self._conversation(
            "What is compound interest?",
            _router("knowledge_question", "What is compound interest?",
                    information_freshness="stable", topic="compound interest"),
            "Can you explain that another way?",
            _router("clarification", "Explain compound interest another way.",
                    topic="compound interest", is_follow_up=True),
            "Compound interest is interest earned on earlier interest.",
        )
        self._assert_follow_up(record, roles, text, "interest earned on earlier interest")

    def test_korean_did_not_understand(self):
        record, roles, text = self._conversation(
            "블록체인이 뭐야?",
            _router("knowledge_question", "What is a blockchain?",
                    information_freshness="stable", topic="블록체인"),
            "잘 이해가 안 돼.",
            _router("clarification", "이전 설명을 이해하지 못함",
                    topic="이해 안 됨", topic_shift=True, is_follow_up=True),
            "블록체인은 여러 컴퓨터가 같은 장부를 나눠 가지는 방식입니다.",
        )
        self._assert_follow_up(record, roles, text, "같은 장부를 나눠 가지는")

    def test_korean_what_do_you_mean(self):
        record, roles, text = self._conversation(
            "광합성이 뭐야?",
            _router("knowledge_question", "What is photosynthesis?",
                    information_freshness="stable", topic="광합성"),
            "마지막 부분이 무슨 뜻이야?",
            _router("clarification", "마지막 부분의 의미를 묻는 질문",
                    topic="광합성", is_follow_up=True),
            "광합성은 빛으로 양분을 만드는 과정입니다. 이때 산소가 나옵니다.",
        )
        self._assert_follow_up(record, roles, text, "빛으로 양분을 만드는")

    def test_when_elaina_must_ask_it_is_a_different_decision(self):
        from brain.intent_router import (
            NEEDS_CLARIFICATION, IntentDecision, SemanticIntentRouter,
        )

        decision = IntentDecision(
            intent="web_search", confidence=0.2, normalized_request="do the thing",
            reason="unsure", action_requested=True,
        )
        result = SemanticIntentRouter._apply_confidence_clarification_policy(
            decision, medium_confidence_threshold=0.5, clarification_enabled=True,
            print_confidence_log=False,
        )
        self.assertEqual(result.intent, NEEDS_CLARIFICATION)


if __name__ == "__main__":
    unittest.main()
