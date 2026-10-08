"""The last check puts back what was lost, not what was taken out.

``result_restored`` is the last word on a value question: when the finished
reply no longer states any number the draft worked out, the draft's own
result sentence goes back in its place. It was built for a result a rewrite
dropped ("what's 2+2" -> "That's the result of 2 plus 2.").

Reviewed over every recorded turn (evals/restoration_replay.py, 2026-10-08):
it had put a sentence back nine times, and four of those were sentences
``grounded_values`` had just removed as unverified:

    How much does it cost?
    -> The newest iPhone models are the iPhone 18 Pro and iPhone 18 Pro Max,
       with prices starting at $1,099 for the 128GB version and going up to
       $1,499 for the 1TB model.

of a phone that starts at $1,199, the whole reply replaced by it and the
"couldn't confirm the specific number" line gone. The check could not tell a
number lost from a number removed on purpose.

The other five were its job, and must stay: ``final_check`` had removed "The
freezing point of water is 32 degrees Fahrenheit." as a restatement of the
question, every time it was asked, and the check put it back. (That removal
was a fault of its own and is gone -- tests/test_echo_after_a_question.py --
so here a later stage is made to lose the answer instead.)

So the grounding steps say which numbers they took out, and a sentence
holding one of them is not put back. The cost is known: the grounding stage
drops whole sentences, so a verified price in the same sentence as an
unverified one goes with it, and stays out.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from brain import guard_lines
from brain.response_policy import AnswerCompletionGuard
from tests.turn_harness import ScriptedClient, build_engine

PRICE_ASKED = "How much does the iPhone 18 Pro cost?"
PRICE_ROUTE = {
    "intent": "web_search", "confidence": 0.99, "normalized_request": "iPhone 18 Pro price",
    "reason": "scripted", "search_query": "iPhone 18 Pro price", "topic": "product",
    "entity": "iPhone 18 Pro", "speech_act": "information_request", "answer_shape": "state",
    "information_freshness": "live", "requires_external_evidence": True, "is_follow_up": False,
}
# What the search finds: the Pro's price, and nothing about the Pro Max.
PAGE = {"title": "iPhone 18 Pro pricing", "url": "https://store.example.test/iphone-18-pro",
        "summary": "The iPhone 18 Pro starts at $1,199."}
BOTH_PRICES = "The iPhone 18 Pro and Pro Max start at $1,199 and $1,299 respectively."
DRAFT = f"{BOTH_PRICES} Carrier deals can reduce the price significantly."

FREEZING_ASKED = "What is the freezing point of water in Fahrenheit?"
FREEZING_ROUTE = {
    "intent": "knowledge_question", "confidence": 0.99, "normalized_request": FREEZING_ASKED,
    "reason": "scripted", "speech_act": "information_request", "answer_shape": "state",
    "information_freshness": "stable", "is_follow_up": False,
}
THE_ANSWER = "The freezing point of water is 32 degrees Fahrenheit."


class TheResultSentenceTests(unittest.TestCase):
    """``the_result_sentence`` told what the grounding stage took out."""

    def _sentence(self, taken_out=frozenset()) -> str:
        return AnswerCompletionGuard.the_result_sentence(
            DRAFT, question=PRICE_ASKED, taken_out=taken_out,
        )

    def test_told_nothing_it_is_as_it_was(self):
        self.assertEqual(self._sentence(), BOTH_PRICES)
        self.assertEqual(
            AnswerCompletionGuard.the_result_sentence(DRAFT, question=PRICE_ASKED),
            BOTH_PRICES,
        )

    def test_a_sentence_holding_a_number_that_was_taken_out_stays_out(self):
        self.assertEqual(self._sentence({"1299"}), "")
        # One of them is enough, verified neighbour or not.
        self.assertEqual(self._sentence({"1199", "1299"}), "")

    def test_a_number_taken_out_of_another_sentence_does_not_matter(self):
        self.assertEqual(self._sentence({"314"}), BOTH_PRICES)

    def test_what_a_stage_took_out_is_what_is_no_longer_there(self):
        took = AnswerCompletionGuard.numbers_taken_out(
            DRAFT, "Carrier deals can reduce the price significantly.",
        )
        # The model number went with the sentence too.
        self.assertEqual(took, {"18", "1199", "1299"})
        self.assertEqual(AnswerCompletionGuard.numbers_taken_out(DRAFT, DRAFT), set())
        # A number still said somewhere was not taken out.
        self.assertEqual(
            AnswerCompletionGuard.numbers_taken_out(
                "It is $1,199. Or $1,199 at Walmart, $1,299 for the Max.", "It is $1,199.",
            ),
            {"1299"},
        )


class TheWholeTurnTests(unittest.TestCase):
    """The check as ``ChatEngine.chat()`` reaches it."""

    def setUp(self):
        class Tripwire:
            def __init__(self, *args, **kwargs):
                pass

            def text(self, query, max_results=3, **kwargs):
                raise AssertionError(f"the real search backend was asked for {query!r}")

        patched = mock.patch("tools.web_search.DDGS", Tripwire)
        patched.start()
        self.addCleanup(patched.stop)

    def _engine(self, asked: str, route: dict, draft: str, *, pages=()):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({asked: route})
        self.addCleanup(engine.close)
        # As the recorded backends ran: with the browser to hand the grounding
        # stage parks an offer to go and check instead of saying what it left out.
        engine.computer_control_mode.set_enabled(False)
        client = ScriptedClient(routes={asked: route}, reply=draft)
        engine.client = client
        for owner in (engine.intent_router, getattr(engine, "task_intent_gate", None)):
            if owner is not None and hasattr(owner, "client"):
                owner.client = client
        if pages:
            engine._web_search_enabled = True
            engine.web_search_tool.results = [dict(page) for page in pages]
        return engine

    def _say(self, engine, asked: str) -> tuple[str, str]:
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            reply = engine.chat(asked)
        return reply, log.getvalue()

    # -- what changed

    def test_a_price_taken_out_as_unverified_is_not_put_back(self):
        engine = self._engine(PRICE_ASKED, PRICE_ROUTE, DRAFT, pages=[PAGE])

        reply, log = self._say(engine, PRICE_ASKED)

        self.assertIn("Removed a value nothing had verified: 1299", log)
        self.assertNotIn("$1,299", reply)
        self.assertNotIn("saying it:", log)
        self.assertIn("taken out as unverified; not put back", log)
        self.assertIn(guard_lines.say("unverified_figure_searched", "en"), reply)

    # -- what did not

    def test_an_answer_another_stage_removed_is_put_back(self):
        # Nothing was looked up and nothing was taken out as unverified: the
        # answer was simply lost on the way, which is what the check is for.
        rest = "That's when it turns from liquid to ice."
        engine = self._engine(FREEZING_ASKED, FREEZING_ROUTE, f"{THE_ANSWER} {rest}")
        engine._their_name_not_hers = lambda said, reply: rest

        reply, log = self._say(engine, FREEZING_ASKED)

        self.assertNotIn("not put back", log)
        self.assertIn("saying it:", log)
        self.assertEqual(reply, THE_ANSWER)

    def test_a_verified_price_lost_later_is_put_back_whatever_else_was_taken_out(self):
        # Grounding takes the unverified sentence out; a later stage then
        # loses the verified one. Only the first was removed on purpose.
        verified = "The iPhone 18 Pro starts at $1,199."
        engine = self._engine(
            PRICE_ASKED, PRICE_ROUTE,
            f"{verified} Trade-ins can bring it down to $314.", pages=[PAGE],
        )
        engine._their_name_not_hers = lambda said, reply: "Trade-ins are worth a look."

        reply, log = self._say(engine, PRICE_ASKED)

        self.assertIn("Removed a value nothing had verified: 314", log)
        self.assertIn("saying it:", log)
        self.assertEqual(reply, verified)


if __name__ == "__main__":
    unittest.main()
