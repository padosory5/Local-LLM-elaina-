"""An explanation's worked example is not a value she looked up.

``grounded_values`` takes out a sentence stating a number nothing this session
saw. On a question about a price that is the point of it. On an explanation
the numbers are made up on purpose:

    아 근데 내가 가진 주식 수만큼 받는 거지?
    -> ... [예를 들어, 주당 1,000원의 배당금이 있고 100주를 보유한다면
       100,000원을 받을 수 있습니다.] ... 찾아봤지만 구체적인 숫자는 확인되지
       않아서 빼고 말씀드렸습니다.

    Wait, that math still doesn't work. If I multiply 32 by 100, I get 3200,
    not 0.325. How do you actually get the original value back?
    -> ... [For example, if the integer is 32 and the scaling factor is 0.01,
       the original float value is 0.32.] ...

The second is the correction the person had asked for, twice, and never saw.

Measured over every recorded turn (2026-10-08): the stage took a sentence out
of 25 replies. The 12 on turns the router read as ``state`` were prices. The
13 on turns it read as ``explain`` were all worked examples -- 13 of the 1,032
explanation turns the stage ran on, and not one invented fact among them.

So on a turn read as explaining, the numbers are left as written and the log
says which. Two shapes are still taken out there: a price put on a place she
has named, and a value the person has just said is wrong. A turn read as
asking for a fact is checked exactly as before.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from brain import guard_lines
from brain.grounded_values import GroundedValueGuard
from tests.turn_harness import ScriptedClient, build_engine

DIVIDEND_ASKED = "아 근데 내가 가진 주식 수만큼 받는 거지?"
DIVIDEND_EXAMPLE = "예를 들어, 주당 1,000원의 배당금이 있고 100주를 보유한다면 100,000원을 받을 수 있습니다."
DIVIDEND_REPLY = f"주식 보유 수량에 따라 배당금은 주당 배당금 금액을 곱한 금액이 됩니다. {DIVIDEND_EXAMPLE}"
DIVIDEND_PAGES = "[1] 배당금이란\nSource: example.test\nSnippet: 배당금은 회사가 이익의 일부를 주주에게 나눠 주는 돈입니다."

SCALE_ASKED = (
    "Wait, that math still doesn't work. If I multiply 32 by 100, I get 3200, "
    "not 0.325. How do you actually get the original value back?"
)
SCALE_EXAMPLE = "For example, if the integer is 32 and the scaling factor is 0.01, the original float value is 0.32."
SCALE_REPLY = f"You multiply the integer by the scaling factor. {SCALE_EXAMPLE}"

PRICE_ASKED = "How much does the iPhone 18 Pro cost?"
PRICE_PAGES = "[1] iPhone 18 Pro\nSource: store.example.test\nSnippet: The iPhone 18 Pro starts at $1,199."
PRICE_REPLY = "The iPhone 18 Pro starts at $1,199. The 512GB model is $1,349."

LEFT = "An explanation's own example; left as written"


class TheShapesThatStayClaimsTests(unittest.TestCase):
    """``is_an_illustration``: what is still a claim inside an explanation."""

    def test_a_worked_example_is_an_illustration(self):
        for reply in (DIVIDEND_REPLY, SCALE_REPLY):
            with self.subTest(reply=reply):
                self.assertTrue(GroundedValueGuard.is_an_illustration(reply, disputed=False))

    def test_a_price_on_a_named_place_is_not(self):
        self.assertFalse(GroundedValueGuard.is_an_illustration(
            "서울에 있는 '김치찌개 전문점'에서 10,000원에 먹을 수 있습니다.", disputed=False,
        ))

    def test_a_value_the_person_disputed_is_not(self):
        self.assertFalse(GroundedValueGuard.is_an_illustration(SCALE_REPLY, disputed=True))


class TheStageTests(unittest.TestCase):
    """``_enforce_grounded_values`` told that the turn is an explanation."""

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.addCleanup(self.engine.close)
        # As the recorded backends ran: with the browser to hand the stage
        # parks an offer to go and check instead of saying what it left out.
        self.engine.computer_control_mode.set_enabled(False)

    def _guard(self, reply: str, asked: str, *, evidence: str = "", searched: bool = False,
               illustrating: bool, language: str = "en") -> tuple[str, str]:
        self.engine._turn_language = language
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            guarded = self.engine._enforce_grounded_values(
                reply, user_input=asked, action_performed=False, research_evidence=evidence,
                trusted_result=False, searched=searched, illustrating=illustrating,
            )
        return guarded, log.getvalue()

    # -- what changed

    def test_an_example_on_a_turn_that_searched_is_left_as_written(self):
        guarded, log = self._guard(
            DIVIDEND_REPLY, DIVIDEND_ASKED, evidence=DIVIDEND_PAGES, searched=True,
            illustrating=True, language="ko",
        )

        self.assertEqual(guarded, DIVIDEND_REPLY)
        self.assertIn(LEFT, log)
        self.assertIn("100000", log)

    def test_an_example_built_on_the_persons_own_number_is_left_as_written(self):
        # Nothing was looked up; the stage acted because their message held
        # numbers and the reply's were not among them.
        guarded, log = self._guard(SCALE_REPLY, SCALE_ASKED, illustrating=True)

        self.assertEqual(guarded, SCALE_REPLY)
        self.assertIn(LEFT, log)

    # -- what did not

    def test_the_same_reply_on_a_turn_asking_for_a_fact_loses_the_number(self):
        for reply, asked, evidence, searched, language, gone in (
            (DIVIDEND_REPLY, DIVIDEND_ASKED, DIVIDEND_PAGES, True, "ko", "100,000원"),
            (SCALE_REPLY, SCALE_ASKED, "", False, "en", "0.32"),
        ):
            with self.subTest(reply=reply):
                guarded, log = self._guard(
                    reply, asked, evidence=evidence, searched=searched,
                    illustrating=False, language=language,
                )
                self.assertNotIn(gone, guarded)
                self.assertNotIn(LEFT, log)

    def test_an_unverified_price_is_removed_from_an_answer_about_a_price(self):
        guarded, _log = self._guard(
            PRICE_REPLY, PRICE_ASKED, evidence=PRICE_PAGES, searched=True, illustrating=False,
        )

        self.assertIn("$1,199", guarded)
        self.assertNotIn("$1,349", guarded)
        self.assertIn(guard_lines.say("unverified_figure_searched", "en"), guarded)

    def test_a_price_on_a_named_place_is_removed_from_an_explanation_too(self):
        reply = "서울에 있는 '김치찌개 전문점'에서 10,000원에 먹을 수 있습니다. 김치찌개는 간단한 음식입니다."
        guarded, log = self._guard(reply, "김치찌개는 왜 인기가 많아?", illustrating=True, language="ko")

        self.assertNotIn("10,000원", guarded)
        self.assertNotIn(LEFT, log)

    def test_a_disputed_value_is_removed_from_an_explanation_too(self):
        guarded, log = self._guard(
            "The iPhone 18 Pro is $1,349 because storage costs more.",
            "are you sure? what's your source for that price", evidence=PRICE_PAGES, searched=True,
            illustrating=True,
        )

        self.assertNotIn("$1,349", guarded)
        self.assertNotIn(LEFT, log)


def _route(shape: str) -> dict:
    return {
        "intent": "web_search", "confidence": 0.99, "normalized_request": "how dividends are paid per share",
        "reason": "scripted", "search_query": "dividend per share", "topic": "dividends",
        "speech_act": "information_request", "answer_shape": shape, "information_freshness": "stable",
        "requires_external_evidence": True, "is_follow_up": False,
    }


class TheWholeTurnTests(unittest.TestCase):
    """The router's reading of the turn, as ``ChatEngine.chat()`` hands it to the stage."""

    def setUp(self):
        class Tripwire:
            def __init__(self, *args, **kwargs):
                pass

            def text(self, query, max_results=3, **kwargs):
                raise AssertionError(f"the real search backend was asked for {query!r}")

        patched = mock.patch("tools.web_search.DDGS", Tripwire)
        patched.start()
        self.addCleanup(patched.stop)

    def _turn(self, shape: str) -> tuple[str, str]:
        route = _route(shape)
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({DIVIDEND_ASKED: route})
        self.addCleanup(engine.close)
        engine.computer_control_mode.set_enabled(False)
        client = ScriptedClient(routes={DIVIDEND_ASKED: route}, reply=DIVIDEND_REPLY)
        engine.client = client
        for owner in (engine.intent_router, getattr(engine, "task_intent_gate", None)):
            if owner is not None and hasattr(owner, "client"):
                owner.client = client
        engine._web_search_enabled = True
        engine.web_search_tool.results = [{
            "title": "배당금이란", "url": "https://example.test/dividend",
            "summary": "배당금은 회사가 이익의 일부를 주주에게 나눠 주는 돈입니다.",
        }]
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            reply = engine.chat(DIVIDEND_ASKED)
        return reply, log.getvalue()

    def test_an_explanation_keeps_its_example(self):
        reply, log = self._turn("explain")

        self.assertIn("100,000원", reply)
        self.assertIn(LEFT, log)
        self.assertNotIn(guard_lines.say("unverified_figure_searched", "ko"), reply)

    def test_a_turn_asking_for_a_fact_is_checked_as_it_was(self):
        reply, log = self._turn("state")

        self.assertNotIn("100,000원", reply)
        self.assertNotIn(LEFT, log)


if __name__ == "__main__":
    unittest.main()
