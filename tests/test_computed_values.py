"""A number the calculator worked out is verified, whatever unit she says it in.

Measured on 2026-10-09 (runtime/evals/sl/tw1-27b-s1, sl_ko_dividend), two
turns running. The router read the question as a calculation, the planner's
calculator worked it out, and the turn's evidence held the result:

    한 주당 1000원이라고 하면, 주식 10주 있으면 1만원 받는 거지?
    (calculator)  total dividend received: 10000
    draft         총 배당금은 10,000원입니다.
    said          아직 확인해 보지 않아서, 추측으로 말씀드리지는 않겠습니다.

    [Grounding Guard] Removed a value nothing had verified: 10000.
    [Response Guard] The value it worked out was taken out as unverified; not put back.

``grounded_values`` reads values by their unit -- a price, a weight -- and a
calculator's result has none. The unit is in the question, the answer says
both, and "10,000원" was a price no evidence could ever hold. The same in
English for "$12" on a fifteen percent tip. Any model, either language; the
record had 17 turns with a verified calculation and these were the only two
whose answer put a unit on the result.

It was older than it looked. Until 2026-10-07 the last check
(``result_restored``) put the sentence back, and the turn came out right by
accident; the check was then taught not to restore what grounding had taken
out (tests/test_result_restored.py), which is right, and this was underneath.

So her wording of a number the turn's calculation worked out is read as
evidence beside it (``grounded_values.computed_as_said``). Only a result,
and only the exact number: an operand, a number in a label, a rounded
figure and a figure nothing computed are all checked as before.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from brain import evidence as turn_evidence
from brain import grounded_values, guard_lines
from brain.calculation_planner import CalculationPlan
from core import turn_trace
from tests.turn_harness import ScriptedClient, build_engine
from tools.calculator import CalculationStep

HEADER = (
    "Verified calculation. Every number below was computed exactly by a "
    "calculator tool, one step at a time -- never estimated or recomputed "
    "mentally. Use these exact numbers; do not recompute, round differently, "
    "or alter them.\n"
)
DIVIDEND_ASKED = "아 근데 한 주당 1000원이라고 하면, 주식 10주 있으면 1만원 받는 거지?"
DIVIDEND_AGAIN = "아니 진짜로 궁금한데, 10주 있으면 몇 번이나 받아요?"
DIVIDEND_CALCULATION = HEADER + "total dividend received: 10000"
DIVIDEND_DRAFT = "총 배당금은 10,000원입니다."
DIVIDEND_DRAFT_AGAIN = "총 10,000원을 받습니다."

TIP_ASKED = "What is a 15 percent tip on an 80 dollar bill?"
TIP_CALCULATION = HEADER + "tip: 12"
TIP_DRAFT = "A 15 percent tip on an 80 dollar bill is $12."
INVENTED = "The service fee there is $4.50."


class ComputedAsSaidTests(unittest.TestCase):
    """Which of the reply's values a calculation vouches for."""

    def said(self, reply: str, computed: str) -> str:
        return grounded_values.computed_as_said(reply, computed)

    def test_the_result_said_with_the_unit_of_the_question(self):
        self.assertEqual(self.said(DIVIDEND_DRAFT, DIVIDEND_CALCULATION), "10,000원")
        self.assertEqual(self.said(DIVIDEND_DRAFT_AGAIN, HEADER + "total dividends: 10000"), "10,000원")
        self.assertEqual(self.said(TIP_DRAFT, TIP_CALCULATION), "$12")

    def test_it_is_the_number_that_is_compared_not_how_it_is_written(self):
        self.assertEqual(self.said("That comes to $12.00.", TIP_CALCULATION), "$12.00")
        self.assertEqual(self.said("Each person pays $33.33.", HEADER + "each share: 33.33"), "$33.33")
        self.assertEqual(self.said("That comes to 10,000 won.", DIVIDEND_CALCULATION), "10,000 won")

    def test_a_measured_unit_as_well_as_money(self):
        self.assertEqual(
            self.said("The shipment weighs 10000 grams.", HEADER + "total weight: 10000"),
            "10000 grams",
        )

    def test_every_step_of_a_plan_is_a_result(self):
        plan = HEADER + (
            "share A: 260\nshare B: 260\nshare C: 130\n"
            "Combined total of the amounts above (computed exactly, not estimated): 650"
        )
        self.assertEqual(
            self.said("A and B each pay 260 dollars, C pays 130 dollars, 650 dollars in all.", plan),
            "260 dollars 130 dollars 650 dollars",
        )

    def test_the_deterministic_domains_write_theirs_after_an_equals_sign(self):
        arithmetic = "Verified calculation. Use it exactly.\n15 / 100 * 80 = 12"
        self.assertEqual(self.said("That comes to $12.", arithmetic), "$12")
        conversion = "Verified unit conversion. Use it exactly.\n1 pound = 16 ounces"
        self.assertEqual(self.said("That is $16.", conversion), "$16")

    def test_an_operand_is_not_a_result(self):
        arithmetic = "Verified calculation. Use it exactly.\n15 / 100 * 80 = 12"
        for wrong in ("That comes to $15.", "That comes to $80.", "That comes to $100."):
            with self.subTest(reply=wrong):
                self.assertEqual(self.said(wrong, arithmetic), "")
        self.assertEqual(self.said("That is $1.", "Use it exactly.\n1 pound = 16 ounces"), "")

    def test_a_number_in_a_label_is_not_a_result(self):
        steeping = HEADER + "Coffee needed for 500g water at 1:4 ratio: 125"
        self.assertEqual(self.said("That costs $500.", steeping), "")
        self.assertEqual(self.said("That costs $4.", steeping), "")
        self.assertEqual(self.said("That costs $125.", steeping), "$125")

    def test_a_rounded_figure_is_a_different_number(self):
        self.assertEqual(self.said("About $33 each.", HEADER + "each share: 33.33"), "")
        self.assertEqual(self.said("About $33.30 each.", HEADER + "each share: 33.33"), "")

    def test_a_figure_nothing_computed_is_no_part_of_it(self):
        self.assertEqual(self.said(f"The tip is $12. {INVENTED}", TIP_CALCULATION), "$12")
        self.assertEqual(self.said(INVENTED, TIP_CALCULATION), "")

    def test_nothing_computed_nothing_vouched_for(self):
        self.assertEqual(self.said(TIP_DRAFT, ""), "")
        self.assertEqual(self.said(TIP_DRAFT, HEADER.strip()), "")
        self.assertEqual(self.said("", TIP_CALCULATION), "")

    def test_a_reply_without_a_unit_has_nothing_to_vouch_for(self):
        # It was never checked: a plain number is not a claim.
        self.assertEqual(self.said("A 15 percent tip on 80 is 12.", TIP_CALCULATION), "")


class TheGuardOnTheEngineTests(unittest.TestCase):
    """``_enforce_grounded_values`` with a calculation in the turn's ledger."""

    def engine(self, calculation: str = "", *, kind: str = turn_evidence.CALCULATION,
               source: str = "trusted tool result", language: str = "en"):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({})
        self.addCleanup(engine.close)
        # With the browser to hand the stage parks an offer to go and check
        # instead of saying what it left out.
        engine.computer_control_mode.set_enabled(False)
        engine._turn_language = language
        if calculation:
            engine._ledger().add(kind, calculation, source=source)
        return engine

    def guarded(self, engine, reply: str, said: str) -> tuple[str, str]:
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            result = engine._enforce_grounded_values(
                reply, user_input=said, action_performed=False,
            )
        return result, log.getvalue()

    # -- what changed

    def test_the_recorded_answers_stay(self):
        for asked, draft in ((DIVIDEND_ASKED, DIVIDEND_DRAFT), (DIVIDEND_AGAIN, DIVIDEND_DRAFT_AGAIN)):
            with self.subTest(asked=asked):
                engine = self.engine(DIVIDEND_CALCULATION, language="ko")
                reply, log = self.guarded(engine, draft, asked)
                self.assertEqual(reply, draft)
                self.assertNotIn("Removed", log)

    def test_the_same_in_dollars(self):
        for draft in (TIP_DRAFT, "That comes to $12.00."):
            with self.subTest(draft=draft):
                reply, log = self.guarded(self.engine(TIP_CALCULATION), draft, TIP_ASKED)
                self.assertEqual(reply, draft)
                self.assertNotIn("Removed", log)

    def test_a_deterministic_domain_s_result_stays(self):
        engine = self.engine("Verified calculation. Use it exactly.\n15 / 100 * 80 = 12")
        reply, _ = self.guarded(engine, "That comes to $12.", "what's 15% of 80")
        self.assertEqual(reply, "That comes to $12.")

    def test_a_measured_result_stays(self):
        engine = self.engine(HEADER + "total weight: 10000")
        draft = "The shipment weighs 10000 grams."
        reply, _ = self.guarded(engine, draft, "How much do 4 boxes of 2500 grams weigh?")
        self.assertEqual(reply, draft)

    def test_a_figure_beside_the_result_still_goes_and_the_result_stays(self):
        reply, log = self.guarded(
            self.engine(TIP_CALCULATION), f"The tip is $12. {INVENTED}", TIP_ASKED,
        )
        self.assertIn("Removed a value nothing had verified: 450.", log)
        self.assertTrue(reply.startswith("The tip is $12."), reply)
        self.assertNotIn("$4.50", reply)
        self.assertIn(guard_lines.say("unverified_figure_unsearched", "en"), reply)

    # -- what did not

    def test_a_figure_nothing_computed_still_goes(self):
        reply, log = self.guarded(self.engine(TIP_CALCULATION), INVENTED, TIP_ASKED)
        self.assertIn("Removed a value nothing had verified: 450.", log)
        self.assertEqual(reply, guard_lines.say("unverified_unsearched", "en"))

    def test_the_result_said_wrong_still_goes(self):
        reply, log = self.guarded(
            self.engine(TIP_CALCULATION), "A 15 percent tip on an 80 dollar bill is $15.", TIP_ASKED,
        )
        self.assertIn("Removed a value nothing had verified: 15.", log)
        self.assertNotIn("$15", reply)

    def test_a_rounded_result_still_goes(self):
        engine = self.engine(HEADER + "each share: 33.33")
        reply, log = self.guarded(engine, "Each person pays about $33.", "What is 100 dollars split three ways?")
        self.assertIn("Removed a value nothing had verified: 33.", log)
        self.assertNotIn("$33", reply)

    def test_a_bare_number_anywhere_else_vouches_for_nothing(self):
        # Only a calculation's result. A count on a page is not a price.
        engine = self.engine(
            "[1] iPhone 18 Pro\nSource: store.example.test\nSnippet: 1299 units in stock: 1299",
            kind=turn_evidence.SEARCH, source="iPhone 18 Pro price",
        )
        reply, log = self.guarded(engine, "It costs $1,299.", "How much does the iPhone 18 Pro cost?")
        self.assertIn("Removed a value nothing had verified: 1299.", log)
        self.assertNotIn("$1,299", reply)

    def test_a_reply_without_a_unit_is_as_it_was(self):
        draft = "A 15 percent tip on an 80 dollar bill is 12."
        reply, log = self.guarded(self.engine(TIP_CALCULATION), draft, TIP_ASKED)
        self.assertEqual(reply, draft)
        self.assertNotIn("Removed", log)


class TheWholeTurnTests(unittest.TestCase):
    """A calculation-routed turn as ``ChatEngine.chat()`` runs it."""

    def setUp(self):
        class Tripwire:
            def __init__(self, *args, **kwargs):
                pass

            def text(self, query, max_results=3, **kwargs):
                raise AssertionError(f"the real search backend was asked for {query!r}")

        patched = mock.patch("tools.web_search.DDGS", Tripwire)
        patched.start()
        self.addCleanup(patched.stop)

    def _turn(self, asked: str, request: str, draft: str, *, steps=()) -> tuple[str, str, dict]:
        route = {
            "intent": "calculation", "confidence": 0.99, "normalized_request": request,
            "reason": "scripted", "topic": "finance", "speech_act": "information_request",
            "answer_shape": "state", "is_follow_up": False,
        }
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({asked: route})
        self.addCleanup(engine.close)
        engine.computer_control_mode.set_enabled(False)
        client = ScriptedClient(routes={asked: route}, reply=draft)
        engine.client = client
        for owner in (engine.intent_router, getattr(engine, "task_intent_gate", None)):
            if owner is not None and hasattr(owner, "client"):
                owner.client = client
        if steps:
            # The planner asks the model for tool calls; here it has already
            # worked the problem out, as it had on the recorded turn.
            engine.calculation_planner.plan = lambda request: CalculationPlan(steps=tuple(
                CalculationStep(label=label, expression="scripted", value=float(value))
                for label, value in steps
            ))
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            reply = engine.chat(asked)
        return reply, log.getvalue(), turn_trace.last().as_record()

    def _findings(self, record: dict) -> list[tuple[str, str]]:
        return [
            (finding["value"], finding["status"])
            for finding in record.get("findings") or ()
            if finding.get("stage") == "grounded_values"
        ]

    # -- what changed

    def test_the_recorded_turn_says_what_it_worked_out(self):
        reply, log, record = self._turn(
            DIVIDEND_ASKED,
            "Calculate the total dividend received for 10 shares with a dividend rate of 1000 per share",
            DIVIDEND_DRAFT, steps=[("total dividend received", 10000)],
        )

        self.assertEqual(reply, DIVIDEND_DRAFT)
        self.assertNotIn("Removed a value", log)
        self.assertNotIn("not put back", log)
        self.assertIn(("10000", "supported"), self._findings(record))
        self.assertEqual(
            [item["kind"] for item in record["evidence"]["ledger"] if item["kind"] == "calculation"],
            ["calculation"],
        )

    def test_the_same_in_dollars(self):
        reply, log, record = self._turn(TIP_ASKED, "15 percent of 80", TIP_DRAFT, steps=[("tip", 12)])

        self.assertEqual(reply, TIP_DRAFT)
        self.assertNotIn("Removed a value", log)
        self.assertIn(("12", "supported"), self._findings(record))

    def test_a_result_the_arithmetic_domain_computed_itself(self):
        # No planner: "what's 15% of 80" is claimed and computed by
        # brain/domain_resolver.py, which writes "15 / 100 * 80 = 12".
        reply, log, record = self._turn("what's 15% of 80", "15 / 100 * 80", "That comes to $12.")

        self.assertIn("= 12", str(record["evidence"].get("calculation", "")))
        self.assertEqual(reply, "That comes to $12.")
        self.assertNotIn("Removed a value", log)

    # -- what did not

    def test_a_figure_nothing_computed_is_still_taken_out_and_not_put_back(self):
        reply, log, _ = self._turn(TIP_ASKED, "15 percent of 80", INVENTED, steps=[("tip", 12)])

        self.assertIn("Removed a value nothing had verified: 450.", log)
        self.assertIn("taken out as unverified; not put back", log)
        self.assertNotIn("$4.50", reply)


if __name__ == "__main__":
    unittest.main()
