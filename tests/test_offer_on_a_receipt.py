"""An offer on an act that allows none, through the engine.

The style layer's own tests prove the rule; this proves it is reached, and
that the one thing that must survive it does. Measured live on the
contamination matrix, answering a plain "아니야":

    Elaina: 영화 추천을 도와드릴 수 있습니다. 어떤 장르를 좋아하시나요?
    [Style] receipt: duplicate_offer(1 offer(s) where the receipt act allows 0)
    [Style] The re-said version still reads as duplicate_offer; kept the original.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace

from brain import conversation_style
from tests.turn_harness import build_engine


OFFERED = "영화 추천을 도와드릴 수 있습니다. 어떤 장르를 좋아하시나요?"


class TheOfferComesOutTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine.client.calls.clear()

    def tearDown(self):
        self.engine.close()

    def _said(self, draft: str) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._say_it_in_her_voice(
                draft,
                act=conversation_style.RECEIPT,
                user_input="아니야",
                model="qwen3:8b",
                keep_alive="5m",
                max_words=20,
            )

    def test_the_offer_is_taken_out_without_asking_the_model(self):
        # No model call: the scripted client would answer "Sure." and the
        # assertion below would fail on it.
        said = self._said(OFFERED)

        self.assertEqual(said, "어떤 장르를 좋아하시나요?")
        # The whole point of doing it here: no second call, and so no
        # chance of the re-say coming back offering again.
        self.assertEqual(self.engine.client.calls, [])

    def test_a_parked_offer_is_left_for_the_re_say_to_judge(self):
        # The gate is holding this exact sentence. Taking it out here would
        # leave a question nobody was asked, so the deterministic path
        # stands down and the ordinary re-say decides, as it did before.
        self.engine.capability_offer.peek = lambda: SimpleNamespace(
            offer_text="영화 추천을 도와드릴 수 있습니다.",
        )

        self._said(OFFERED)

        self.assertTrue(self.engine.client.calls)


if __name__ == "__main__":
    unittest.main()
