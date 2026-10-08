"""The first draft is sent back only when a value is owed and missing.

``completion_retry`` asks the model again when a value question came back
without its value, and keeps the second draft if it holds a number the first
did not. Reviewed over every recorded turn (evals/completion_retry_replay.py,
2026-10-07): asked twenty times, and not once for a requested number that was
missing and then supplied.

Nine were price answers that had stated the price and ended on an offer:

    The iPhone 18 Pro starts at $1,199 with 256GB storage. You can find it
    on Apple's website or at Walmart. Would you like help checking
    availability?

and the second draft added "The 512GB model is $1,349, and the 2TB version is
$1,599", which no source gave. Eleven were turns that asked for no number,
switched on by their wording:

    So it's just about the chance of it being a fluke, not how much it
    actually helps, right?
    -> The total is $120. That includes $80 for the item and $40 for
       shipping.

The rule, at the one place the first draft is judged: the calculation route
is as it was; a turn that is only worded like a value question is not sent
back when the router read it as asking for an explanation, nor for ending on
a question once it has stated a number.

The unit tests prove the rule. The whole turns prove it is reached, and that
nothing else was moved: a value question answered without its value is still
asked again.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain.response_policy import AnswerCompletionGuard
from tests.turn_harness import ScriptedClient, build_engine

PRICE_ASKED = "How much does the iPhone 18 Pro cost?"
PRICE_AND_AN_OFFER = (
    "The iPhone 18 Pro starts at $1,199 with 256GB storage. You can find it "
    "on Apple’s website or at Walmart. Would you like help checking "
    "availability?"
)
FLUKE_ASKED = (
    "So it's just about the chance of it being a fluke, not how much it "
    "actually helps, right?"
)
FLUKE_ANSWERED = "You're right, it's about whether it was a fluke or not."
INVENTED = "The total is $120. That includes $80 for the item and $40 for shipping."
FREEZING_ASKED = "What is the freezing point of water in Fahrenheit?"
NO_VALUE = (
    "This is the temperature at which water turns into ice under standard "
    "atmospheric conditions."
)
# What marks the retry's own prompt (brain/chat_engine.py).
RETRY = "deferred or stopped"


class AClosingQuestionTests(unittest.TestCase):
    """``needs_retry`` with ``closing_question_is_fine``."""

    def _sent_back(self, reply: str, asked: str = PRICE_ASKED, **said) -> bool:
        return AnswerCompletionGuard.needs_retry(
            reply, calculation=True, question=asked, **said,
        )

    def test_without_the_caller_saying_so_nothing_has_changed(self):
        self.assertTrue(self._sent_back(PRICE_AND_AN_OFFER))

    def test_a_reply_that_stated_the_number_is_not_sent_back_for_it(self):
        self.assertFalse(
            self._sent_back(PRICE_AND_AN_OFFER, closing_question_is_fine=True),
        )

    def test_a_question_instead_of_an_answer_still_is(self):
        for reply in (
            "Which model do you mean?",
            # The question's own number is not an answer to it.
            "Do you mean the iPhone 18 Pro?",
        ):
            with self.subTest(reply=reply):
                self.assertTrue(
                    self._sent_back(reply, closing_question_is_fine=True),
                )

    def test_an_offer_to_do_it_still_is(self):
        self.assertTrue(self._sent_back(
            "Let me calculate that for you. Want me to do the math?",
            asked="what's 15% of 80", closing_question_is_fine=True,
        ))

    def test_a_reply_cut_off_mid_sentence_still_is(self):
        self.assertTrue(self._sent_back(
            "The iPhone 18 Pro starts at $1,199 and the Pro Max starts at",
            closing_question_is_fine=True,
        ))


class TheFirstDraftTests(unittest.TestCase):
    """``first_draft_needs_retry``: the decision the engine asks for."""

    def _sent_back(self, reply: str, asked: str, *, calculation: bool = False,
                   shape: str = "state", value_question: bool = True) -> bool:
        return AnswerCompletionGuard.first_draft_needs_retry(
            reply, value_question=value_question, calculation=calculation,
            answer_shape=shape, question=asked,
        )

    # -- what changed

    def test_a_price_that_ends_on_an_offer_is_kept(self):
        self.assertFalse(self._sent_back(PRICE_AND_AN_OFFER, PRICE_ASKED))

    def test_a_turn_read_as_asking_for_an_explanation_is_kept(self):
        for shape in ("explain", "example", "Explain"):
            with self.subTest(shape=shape):
                self.assertFalse(
                    self._sent_back(FLUKE_ANSWERED, FLUKE_ASKED, shape=shape),
                )

    # -- what did not

    def test_a_value_question_without_its_value_is_sent_back(self):
        for shape in ("state", ""):
            with self.subTest(shape=shape):
                self.assertTrue(
                    self._sent_back(NO_VALUE, FREEZING_ASKED, shape=shape),
                )

    def test_a_value_question_with_its_value_is_kept(self):
        self.assertFalse(self._sent_back(
            "Water freezes at 32 degrees Fahrenheit.", FREEZING_ASKED,
        ))

    def test_the_calculation_route_is_as_it_was(self):
        asked = "what's 2+2"
        for reply, shape in (
            # Whatever the router made of its shape.
            ("That’s the result of 2 plus 2.", "explain"),
            ("That’s the result.", "state"),
            ("Let me calculate that for you. Want me to do the math?", "state"),
            # A closing question still sends a calculation back.
            ("2 + 2 equals 4. Want another one?", "state"),
        ):
            with self.subTest(reply=reply, shape=shape):
                was = AnswerCompletionGuard.needs_retry(
                    reply, calculation=True, question=asked,
                )
                self.assertTrue(was)
                self.assertEqual(
                    self._sent_back(reply, asked, calculation=True, shape=shape),
                    was,
                )
        self.assertFalse(self._sent_back(
            "2 + 2 equals 4.", asked, calculation=True,
        ))

    def test_a_turn_that_asked_for_no_value_is_never_sent_back(self):
        for calculation in (False, True):
            with self.subTest(calculation=calculation):
                self.assertFalse(self._sent_back(
                    "", "tell me a joke", calculation=calculation,
                    value_question=False,
                ))


def _route(intent: str, asked: str, shape: str, **more) -> dict:
    return {
        "intent": intent, "confidence": 0.99, "normalized_request": asked,
        "reason": "scripted", "speech_act": "information_request",
        "answer_shape": shape, "information_freshness": "stable",
        "is_follow_up": False, **more,
    }


class TheWholeTurnTests(unittest.TestCase):
    """The decision as ``ChatEngine.chat()`` reaches it."""

    def _turn(self, asked: str, route: dict, draft: str, second: str):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({asked: route})
        self.addCleanup(engine.close)
        client = ScriptedClient(
            routes={asked: route}, reply=draft, replies={RETRY: second},
        )
        engine.client = client
        for owner in (engine.intent_router,
                      getattr(engine, "task_intent_gate", None)):
            if owner is not None and hasattr(owner, "client"):
                owner.client = client
        with contextlib.redirect_stdout(io.StringIO()):
            reply = engine.chat(asked)
        asked_again = any(RETRY in call for call in client.calls)
        return reply, asked_again

    # -- what changed

    def test_an_answer_that_ends_on_an_offer_is_not_asked_for_again(self):
        # Not a unit conversion: the engine computes those itself, on the
        # calculation route, which is as it was.
        asked = "How tall is Mount Rainier?"
        reply, asked_again = self._turn(
            asked, _route("knowledge_question", asked, "state"),
            draft="Mount Rainier is 14,410 feet tall. Would you like to know how that compares with Mount Hood?",
            second="Mount Rainier is 14,410 feet tall, and Mount Hood is 11,249 feet.",
        )

        self.assertFalse(asked_again)
        self.assertIn("14,410", reply)
        self.assertNotIn("11,249", reply)

    def test_an_explanation_turn_is_not_asked_for_a_number(self):
        reply, asked_again = self._turn(
            FLUKE_ASKED,
            _route("conversation", FLUKE_ASKED, "explain", is_follow_up=True),
            draft=FLUKE_ANSWERED, second=INVENTED,
        )

        self.assertFalse(asked_again)
        self.assertIn("fluke", reply)
        self.assertNotIn("120", reply)

    # -- what did not

    def test_a_value_question_without_its_value_is_asked_again(self):
        reply, asked_again = self._turn(
            FREEZING_ASKED, _route("knowledge_question", FREEZING_ASKED, "state"),
            draft=NO_VALUE, second="Water freezes at 32 degrees Fahrenheit.",
        )

        self.assertTrue(asked_again)
        self.assertIn("32", reply)

    def test_a_calculation_without_its_result_is_asked_again(self):
        # Whatever shape the router gave it: the route is what decides.
        asked = "what's 2+2"
        reply, asked_again = self._turn(
            asked,
            {**_route("calculation", "2 + 2", "explain"), "topic": "mathematics"},
            draft="That’s the result of 2 plus 2.", second="2 + 2 equals 4.",
        )

        self.assertTrue(asked_again)
        self.assertIn("4", reply)


if __name__ == "__main__":
    unittest.main()
