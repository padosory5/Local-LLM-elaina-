"""Phase 3C: a grounding stage reads this turn's evidence and records why.

The value guard used to read whatever the last search returned, from any
turn, and never the clock. After a search about a course numbered 207, a
time answer's "07" read as a damaged copy of "207" and the answer was
deleted. Now the guard reads the turn's ledger (brain/evidence.py) -- the
clock it actually read, this turn's search, and a previous search only when
this turn is a follow-up that carries it -- and every value it checks is a
finding in the turn record.
"""

import unittest

from brain import evidence
from brain.grounded_values import GroundedValueGuard
from core import turn_trace


class FindingsTests(unittest.TestCase):

    def test_each_value_says_what_stands_behind_it(self):
        found = GroundedValueGuard.findings(
            "The room is $189 a night and the host's number is 206-555-0142.",
            [("search:hotels", "Harbor Inn -- $189 per night"),
             ("their words", "how much is it?")],
        )
        by_value = {item["value"]: item for item in found}
        self.assertEqual(by_value["189"]["status"], "supported")
        self.assertEqual(by_value["189"]["source"], "search:hotels")
        self.assertEqual(by_value["2065550142"]["status"], "unsupported")

    def test_a_damaged_copy_is_conflicting_not_unsupported(self):
        found = GroundedValueGuard.findings(
            "Your budget is 150.", [("their words", "My budget is 1500.")],
        )
        self.assertIn({"value": "150", "status": "conflicting", "source": "their words"}, found)


def _time_route():
    return {"intent": "time_question", "confidence": 1.0,
            "normalized_request": "What time is it right now?",
            "speech_act": "information_request", "information_freshness": "unknown",
            "requires_external_evidence": False}


class WholeTurnTests(unittest.TestCase):

    def test_an_old_search_does_not_delete_the_time(self):
        """The Phase 2 failure, in every model's arm."""
        from tests.turn_harness import build_engine

        engine = build_engine({"what time it is": _time_route()})
        engine._last_research_evidence = (
            "UW Math 207: Taylor series, sequences and differential equations."
        )
        engine.client.reply = "It's 3:07 AM right now."
        shown = engine.chat("Can you tell me what time it is right now?")
        self.assertIn("3:07", shown)
        record = turn_trace.last().as_record()
        self.assertFalse([s for s in record["steps"]
                          if s["name"] == "grounded_values" and s.get("changed")])

    def test_every_checked_value_is_a_finding(self):
        from tests.turn_harness import build_engine

        engine = build_engine({"price of the Harbor": {
            "intent": "conversation", "confidence": 1.0,
            "normalized_request": "What was the price of the Harbor Inn?",
            "speech_act": "information_request", "is_follow_up": True,
        }})
        engine._last_research_evidence = "Harbor Inn -- $189 per night."
        engine.client.reply = "The Harbor Inn was $189 a night."
        shown = engine.chat("What was the price of the Harbor Inn again?")
        self.assertIn("$189", shown)
        findings = turn_trace.last().as_record()["findings"]
        self.assertTrue(any(f.get("stage") == "grounded_values"
                            and f.get("value") == "189"
                            and f.get("status") == "supported" for f in findings),
                        findings)

    def test_a_follow_up_carries_the_search_it_follows(self):
        from tests.turn_harness import build_engine

        engine = build_engine({"cheapest": {
            "intent": "conversation", "confidence": 1.0,
            "normalized_request": "Which of those hotels is cheapest?",
            "speech_act": "information_request", "is_follow_up": True,
        }})
        engine._last_research_evidence = "Harbor Inn $189; Bay Hotel $240."
        engine.client.reply = "The Harbor Inn, at $189."
        engine.chat("which is cheapest?")
        ledger = turn_trace.last().as_record()["evidence"].get("ledger", ())
        carried = [item for item in ledger if item["kind"] == evidence.RECALL]
        self.assertTrue(carried and carried[0]["carried"], ledger)


if __name__ == "__main__":
    unittest.main()
