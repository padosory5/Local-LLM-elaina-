"""The title of a page is not the name of a thing.

Every page title in tests/candidate_title_matrix.json reached the candidate
list in a real session, and most were said out loud as the pick:

    "...Serve with rice for a rich, spicy meal. We Really Cooking Now
     Butterbean'S Cafe GIF is the one I'd start with."
    Selected: Gaming Mice Under                          (four times)
    Selected: Today's Weather in Seoul - Hourly Forecast and Conditions

Before this: 0 of 18 refused. The matrix also holds real products, places,
and films and dramas whose names are sentences, because the rules are only
worth having if those survive.

One class the words cannot decide, and it is tested here separately. "Realize
Cooking Doesn't Really Burn Off" is a headline shaped exactly like a film
title. What decided it was the question: "how to make it really tasty" asks
for a method, and nothing found for a method is put forward as the pick.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brain import candidate_fit
from scripts.candidate_title_check import verdict

MATRIX = json.loads(
    Path(__file__).with_name("candidate_title_matrix.json").read_text(
        encoding="utf-8",
    )
)


class TheMatrixTests(unittest.TestCase):

    def test_every_title_is_read_the_way_it_should_be(self):
        for case in MATRIX["cases"]:
            with self.subTest(name=case["name"]):
                got, reason = verdict(case)
                self.assertEqual(got, case["expect"], reason or case.get("why", ""))

    def test_both_halves_are_measured(self):
        wanted = [case["expect"] for case in MATRIX["cases"]]

        self.assertGreaterEqual(wanted.count("page"), 10)
        self.assertGreaterEqual(wanted.count("thing"), 10)
        # And the films: the half a strict rule would throw away.
        self.assertTrue(any(
            case["expect"] == "thing" and case["shape"] == "any"
            for case in MATRIX["cases"]
        ))


class AMediaAddressIsAMediaPageTests(unittest.TestCase):

    def test_a_gif_address_decides_it_whatever_the_title(self):
        self.assertTrue(candidate_fit.reads_as_a_page(
            "Butterbean's Cafe",
            "https://tenor.com/view/we-really-cooking-now-gif-26542123",
        ))

    def test_an_ordinary_address_decides_nothing(self):
        self.assertEqual(candidate_fit.reads_as_a_page(
            "Razer Orochi V2", "https://www.razer.com/gaming-mice/razer-orochi-v2",
        ), "")


class AskingForAMethodTests(unittest.TestCase):

    def test_how_to_questions_in_both_languages(self):
        for said in (
            "search how to make it really tasty, not the obvious stuff",
            "how do I make kimchi stew",
            "what's the best way to cook rice",
            "give me a recipe for bulgogi",
            "김치찌개 만드는 법 알려줘",
            "맛있게 만드는법을 좀 검색해서 알려줘 그런 뻔한거 말고",
            "된장찌개는 어떻게 만들어",
            "떡볶이 레시피 알려줘",
        ):
            with self.subTest(said=said):
                self.assertTrue(candidate_fit.asks_for_a_method(said))

    def test_asking_which_thing_is_not_a_method(self):
        for said in (
            "recommend a good gaming mouse",
            "which hotel should I stay at in Seoul",
            "what's a good kimchi stew restaurant near me",
            "how much is the Razer Orochi V2",
            "노트북 추천해줘",
            "강남에 맛있는 김치찌개 집 알려줘",
        ):
            with self.subTest(said=said):
                self.assertFalse(candidate_fit.asks_for_a_method(said))


class NothingIsPickedForAMethodTests(unittest.TestCase):
    """Through the engine: the two places that turn results into a pick."""

    def setUp(self):
        import contextlib
        import io

        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def _report(self, said: str) -> str:
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._report_what_was_found(
                "Here is what I found.",
                candidates=("Razer Orochi V2",), searched=True, about="",
                said=said,
            )

    def test_a_method_answer_is_not_given_a_pick(self):
        # The measured reply: a method, then "... is the one I'd start with".
        self.assertEqual(
            self._report("search how to make it really tasty, not the obvious stuff"),
            "Here is what I found.",
        )
        self.assertEqual(
            self._report("맛있게 만드는법을 좀 검색해서 알려줘 그런 뻔한거 말고"),
            "Here is what I found.",
        )

    def test_asking_which_thing_still_gets_one(self):
        # The guard exists for this: a search found things and the answer
        # buried them. That must keep working.
        self.assertTrue(
            self._report("recommend a good gaming mouse").endswith(
                "Razer Orochi V2 is the one I'd start with.",
            ),
        )

    def test_candidate_ranking_stands_aside_for_a_method(self):
        import contextlib
        import io

        self.engine.task_sessions.note_recommendation_turn(
            "find me a quiet mechanical keyboard under $100",
            subject="mechanical keyboard",
        )
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            result = self.engine._research_for_recommendation(
                "mechanical keyboard lubing tips",
                said="how do I lube the switches on it",
            )

        self.assertIsNone(result)
        self.assertIn("asks how to do something", log.getvalue())


class ThePickIsSayableTests(unittest.TestCase):
    """The name put forward as the pick is one they can read, in their language.

    Measured in the paired Korean baseline:

        You:     그거 배터리 얼마나 가?
        Elaina:  ... 직접 확인해 보시기 바랍니다. Беспроводные/проводные
                 наушники Nothing Headphone (a) черный... is the one I'd
                 start with.

    A Russian storefront listing, cut off by the page, in an English
    sentence, inside a Korean reply. The listing is now refused as a page
    (the matrix above); these hold the sentence itself.
    """

    BASE = "헤드폰은 가격대가 다양합니다."

    def setUp(self):
        import contextlib
        import io

        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def _report(self, candidates, said: str, language: str) -> str:
        import contextlib
        import io

        self.engine._turn_language = language
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._report_what_was_found(
                self.BASE, candidates=candidates, searched=True, about="",
                said=said,
            )

    def test_in_korean_the_pick_is_said_in_korean(self):
        reply = self._report(("Sony WH-CH520",), "싸고 괜찮은 헤드폰 추천해줘", "ko")

        self.assertEqual(reply, f"{self.BASE} Sony WH-CH520부터 보시는 걸 추천합니다.")

    def test_in_english_it_is_unchanged(self):
        reply = self._report(("Sony WH-CH520",), "recommend cheap headphones", "en")

        self.assertTrue(reply.endswith("Sony WH-CH520 is the one I'd start with."))

    def test_a_page_the_card_layer_refused_is_not_said(self):
        # Measured in parity3: the card layer dropped this as "several of
        # them, not one", and the guard still appended it as the pick.
        import contextlib
        import io

        self.engine._turn_language = "ko"
        with contextlib.redirect_stdout(io.StringIO()):
            reply = self.engine._report_what_was_found(
                self.BASE,
                candidates=("Sennheiser — Headphones, Microphones, Wireless Systems",),
                searched=True, about="headphones", said="10만원 이하로",
            )

        self.assertEqual(reply, self.BASE)

    def test_a_name_in_a_script_neither_language_uses_is_not_said(self):
        for language in ("ko", "en"):
            with self.subTest(language=language):
                reply = self._report(
                    ("Беспроводные наушники Nothing Headphone (a) черный",),
                    "recommend cheap headphones", language,
                )
                self.assertEqual(reply, self.BASE)


if __name__ == "__main__":
    unittest.main()
