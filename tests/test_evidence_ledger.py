"""Phase 3.0: one account of what a turn knew, and checks read from records.

The ledger (brain/evidence.py) is what a turn had in hand and is also what
the turn record shows as its evidence. The integrity checks
(evals/integrity.py) count what the pipeline did that it should never do.
Neither changes a reply; both are what the Phase 3 changes are measured by.
"""

import unittest

from brain import evidence
from core import turn_trace
from evals import integrity


class LedgerTests(unittest.TestCase):

    def test_kinds_text_and_order(self):
        ledger = evidence.EvidenceLedger()
        ledger.add(evidence.CLOCK, "The current local time is 03:07 AM.", source="clock")
        ledger.add(evidence.SEARCH, "Snippet: ...", source="a query")

        self.assertEqual([item.kind for item in ledger.items()], ["clock", "search"])
        self.assertEqual(ledger.text(evidence.CLOCK), "The current local time is 03:07 AM.")
        self.assertTrue(ledger.has(evidence.SEARCH))
        self.assertFalse(ledger.has(evidence.RECALL))

    def test_nothing_is_not_evidence(self):
        ledger = evidence.EvidenceLedger()
        self.assertIsNone(ledger.add(evidence.SEARCH, "   "))
        self.assertFalse(ledger)
        self.assertFalse(ledger.looked_something_up)

    def test_an_unknown_kind_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            evidence.EvidenceLedger().add("rumour", "something")

    def test_carried_evidence_is_marked(self):
        ledger = evidence.EvidenceLedger()
        item = ledger.add(evidence.RECALL, "the hotels found earlier", carried=True)
        self.assertTrue(item.carried)

    def test_the_turn_record_shows_the_ledger(self):
        saved = turn_trace.PERSIST
        turn_trace.PERSIST = False
        try:
            trace = turn_trace.begin("what time is it?")
            evidence.EvidenceLedger().add(evidence.CLOCK, "03:07 AM", source="clock")
            turn_trace.note_finding(stage="grounded_values", value="03:07", status="supported")
            turn_trace.finish(trace)
            record = trace.as_record()
        finally:
            turn_trace.PERSIST = saved
        self.assertEqual(record["evidence"]["ledger"][0]["kind"], "clock")
        self.assertEqual(record["findings"][0]["status"], "supported")


class IntegrityTests(unittest.TestCase):

    def test_operations_are_math_and_hyphens_are_words(self):
        self.assertEqual(integrity.operations("(x - a)"), ["x-a"])
        self.assertIn("2-4a", integrity.operations("b^2 - 4ac"))
        self.assertEqual(integrity.operations("a well-known and/or fact"), [])
        self.assertEqual(integrity.operations("(x, a)"), [])

    def test_a_searched_clock_turn(self):
        result = integrity.Integrity()
        integrity.check_record({
            "turn_id": "t", "user_input": "What time is it?",
            "context": {"route": {"intent": "time_question"}},
            "evidence": {"search_queries": ["current time"]},
        }, result)
        self.assertEqual(result.counts()["clock_turns"], 1)
        self.assertEqual(result.counts()["clock_turns_searched"], 1)

    def test_a_deleted_value_the_evidence_held(self):
        result = integrity.Integrity()
        integrity.check_record({
            "turn_id": "t", "user_input": "time?",
            "evidence": {"clock": "The current local time is 03:07 AM."},
            "steps": [{"name": "grounded_values", "changed": True,
                       "before": "It is 3:07 AM.",
                       "after": "I haven't actually checked that."}],
        }, result)
        self.assertEqual(result.counts()["values_deleted_with_evidence"], 1)

    def test_speech_shaping_on_the_display_and_broken_notation(self):
        result = integrity.Integrity()
        integrity.check_record({
            "turn_id": "t", "user_input": "Taylor series?",
            "steps": [{"name": "speech_filter", "changed": True,
                       "before": "f(a) + f'(a)(x - a)", "after": "f(a) + f'(a)(x, a)"}],
        }, result)
        counts = result.counts()
        self.assertEqual(counts["display_speech_shaped"], 1)
        self.assertEqual(counts["notation_changed"], 1)

    def test_a_model_rewrite_is_not_counted_as_broken_notation(self):
        # A stage that asks the model again writes a new reply; losing an
        # operator there is the model's wording, not a transform.
        result = integrity.Integrity()
        integrity.check_record({
            "turn_id": "t", "user_input": "x?",
            "steps": [{"name": "her_voice", "changed": True,
                       "before": "x - a", "after": "the distance from a"}],
        }, result)
        self.assertEqual(result.counts()["notation_changed"], 0)


class WholeTurnLedgerTests(unittest.TestCase):

    def test_a_time_turn_records_the_clock_it_read(self):
        from tests.turn_harness import build_engine

        engine = build_engine({
            "What time is it": {
                "intent": "time_question", "confidence": 1.0,
                "normalized_request": "What time is it?",
                "speech_act": "information_request",
                # As the 8B routed it in Phase 2. The 27B's "live" routing
                # is Phase 3B's to fix, and tested there.
                "information_freshness": "unknown",
                "requires_external_evidence": False,
            },
        })
        engine.client.reply = "It's a little after three."
        engine.chat("What time is it?")
        record = turn_trace.last().as_record()
        kinds = [item["kind"] for item in record["evidence"].get("ledger", ())]
        self.assertIn("clock", kinds)

    def test_each_turn_starts_with_an_empty_ledger(self):
        from tests.turn_harness import build_engine

        engine = build_engine({
            "What time is it": {
                "intent": "time_question", "confidence": 1.0,
                "normalized_request": "What time is it?",
                "speech_act": "information_request",
                # As the 8B routed it in Phase 2. The 27B's "live" routing
                # is Phase 3B's to fix, and tested there.
                "information_freshness": "unknown",
                "requires_external_evidence": False,
            },
        })
        engine.chat("What time is it?")
        engine.chat("thanks")
        record = turn_trace.last().as_record()
        kinds = [item["kind"] for item in record["evidence"].get("ledger", ())]
        self.assertNotIn("clock", kinds)


if __name__ == "__main__":
    unittest.main()
