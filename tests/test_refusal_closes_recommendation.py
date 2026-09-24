"""A refusal of an open recommendation, through the engine.

Measured on tests/contamination_matrix.json, refusal_is_not_a_request: two
film turns, then a plain "아니야". The offer gate already answers a "no"
said to a *parked* offer -- 0.18s, straight from the declined bank -- but a
recommendation she made without parking anything had no such path, so the
same word went to the model with the film thread still in its history:

    You:     아니야
    Elaina:  어제 영화보다는 오늘 영화를 추천드리겠습니다.
    Elaina:  미니멀 라이프 추천드립니다. 스트리밍 플랫폼에서 확인해보십시오.

Four of eight runs answered a refusal with another recommendation. With the
refusal read as a decline it was eight of eight, five of them through this
path and three through the older offer one.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain import action_status
from tests.turn_harness import build_engine


class ARefusalClosesTheRecommendationTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
            self.engine.task_sessions.note_recommendation_turn(
                "오늘 볼 만한 영화 뭐 있어?", subject="entertainment",
            )
        self.engine.client.calls.clear()

    def tearDown(self):
        self.engine.close()

    def _say(self, text: str) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine.chat(text)

    def test_the_refusal_is_accepted_without_asking_the_model(self):
        reply = self._say("아니야")

        self.assertIn(reply, action_status._KO_PHASES["declined"])
        # Nothing was routed and nothing was generated: a refusal with
        # nothing parked has no classification to make, and the model is
        # what answered it with another film.
        self.assertEqual(self.engine.client.calls, [])

    def test_the_recommendation_does_not_survive_it(self):
        self._say("아니야")

        self.assertIsNone(self.engine.task_sessions.active_recommendation())

    def test_a_refusal_that_redirects_still_goes_to_the_router(self):
        # "아니야 다른 거" declines *and* asks, and answering it in one
        # clause would drop the asking half.
        self._say("아니야 다른 거")

        self.assertTrue(self.engine.client.calls)

    def test_nothing_open_means_nothing_to_decline(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine.task_sessions.clear_recommendation()
        self.engine.client.calls.clear()

        self._say("아니야")

        self.assertTrue(self.engine.client.calls)


if __name__ == "__main__":
    unittest.main()
