"""The unchecked-name guard reads Korean names too.

``unverified_entities`` found a name by its capital letters, and only woke
up on English words -- "recommend", "try", "visit". Korean has no capitals
and the trigger had three Korean nouns, so across every Korean turn of the
paired measurements -- 30 of 432 replies quoting a name -- it checked
nothing. Measured, with no search behind any of it:

    캐나다 드라마 '더 블랙 블레이드'를 보십시오.
    2026년 최신 드라마로는 태국 드라마 '패미의 선택'을 추천합니다.
    현지 식당 추천이라면 '서울 콩고리'나 '해태 해산물'이 인기 있습니다.

while the same English replies were retracted. Part of Korean's lead in the
parity measurements was this: not a better answer, a less-checked one.

A Korean name is written in quotation marks, which is what this reads. The
rest mirrors the English rules: a name the search returned, or the person
said, passes; a landform is not a business; discussing a title without
recommending it is not a claim.
"""

from __future__ import annotations

import unittest

from brain import grounded_values, guard_lines
from brain.grounded_values import sends_somewhere, unverified_entities


class KoreanNamesAreCheckedTests(unittest.TestCase):

    def test_the_measured_replies_are_caught(self):
        for reply, request, name in (
            ("캐나다 드라마 '더 블랙 블레이드'를 보십시오. 악역이 주인공을 "
             "구원하려는 갈등이 매력적입니다.", "음 별로네", "더 블랙 블레이드"),
            ("2026년 최신 드라마로는 태국 드라마 '패미의 선택'을 추천합니다.",
             "요즘 볼만한 드라마 있어?", "패미의 선택"),
            ("현지 식당 추천이라면 '서울 콩고리'나 '해태 해산물'이 인기 있습니다.",
             "드라마는 됐고, 저녁 뭐 먹지?", "서울 콩고리"),
        ):
            with self.subTest(name=name):
                self.assertIn(
                    name, unverified_entities(reply, evidence="", request=request),
                )

    def test_the_same_recommendation_is_caught_in_either_language(self):
        english = unverified_entities(
            "I recommend The Black Blade.", request="hmm",
        )
        korean = unverified_entities(
            "'더 블랙 블레이드'를 추천합니다.", request="음",
        )

        self.assertEqual(len(english), 1)
        self.assertEqual(len(korean), 1)


class WhatStillPassesTests(unittest.TestCase):

    def test_a_name_the_search_returned(self):
        self.assertEqual(
            unverified_entities(
                "'오징어 게임'을 추천합니다.",
                evidence="넷플릭스 '오징어 게임' 시즌 3 공개",
                request="요즘 볼만한 드라마 있어?",
            ),
            (),
        )

    def test_a_name_the_person_said(self):
        self.assertEqual(
            unverified_entities(
                "'오징어 게임'과 비슷한 '지금 우리 학교는'을 추천합니다.",
                request="오징어 게임 같은 거 추천해줘",
            ),
            ("지금 우리 학교는",),
        )

    def test_a_landform_is_not_a_business(self):
        self.assertEqual(
            unverified_entities("'한라산'에 가 보십시오.", request="제주도 어디 가?"),
            (),
        )

    def test_quoted_speech_is_not_a_name(self):
        self.assertEqual(
            unverified_entities(
                "'이 뜨거워졌어요'라는 표현을 추천합니다.", request="",
            ),
            (),
        )

    def test_a_quoted_question_is_speech_in_any_register(self):
        # Simulated learners: retracted as a title, "실제로 찾아볼까요?" said.
        self.assertEqual(
            unverified_entities(
                '예를 들어, "오늘 날씨 어때?"라고 묻는다면, 훈련 데이터를 기반으로 '
                "답합니다. 실제로 찾아볼까요?",
                request="매번 그거 다 찾아보는 거야?",
            ),
            (),
        )

    def test_discussing_a_title_is_not_recommending_it(self):
        # The English rule: naming a dish, or talking about a film, is not
        # sending anyone anywhere.
        self.assertEqual(
            unverified_entities("'오징어 게임'은 재미있었습니다.", request=""), (),
        )

    def test_naming_something_counts_as_naming(self):
        self.assertTrue(
            grounded_values.names_something_specific("'오징어 게임'을 추천합니다."),
        )

    def test_the_spelled_out_term_they_asked_about(self):
        # Simulated learners, both pilots: "a weather app" made this a reply
        # that sends somewhere, the expansion read as a business, and the
        # definition was replaced with "I don't want to send you somewhere
        # I haven't checked".
        reply = (
            "An API, or Application Programming Interface, is a set of rules "
            "that lets different software applications talk to each other. "
            "For example, when you use a weather app, it uses an API to get "
            "the current weather data from a weather service."
        )
        self.assertEqual(
            unverified_entities(
                reply, request="What's an API? My coworkers keep saying it.",
            ),
            (),
        )
        # Initials nobody said ground nothing.
        self.assertEqual(
            unverified_entities(
                "Check out local music stores in Seoul like Melody House or "
                "Guitar Center Korea.",
                request="where can I buy a guitar in Seoul",
            ),
            ("Melody House", "Guitar Center Korea"),
        )


class WhichOfferIsSaidTests(unittest.TestCase):

    def test_places_and_things(self):
        for text, somewhere in (
            ("You could check out local music stores like Melody House.", True),
            ("현지 식당 추천이라면 '서울 콩고리'가 인기 있습니다.", True),
            ("Not into The WONDERfools? Start with the first episode.", False),
            ("캐나다 드라마 '더 블랙 블레이드'를 보십시오.", False),
        ):
            with self.subTest(text=text):
                self.assertEqual(sends_somewhere(text), somewhere)


class AMemoryInTheOtherLanguageIsStillEvidenceTests(unittest.TestCase):
    """Measured once memories began being written in the person's own
    language: the profile said "사용자는 워싱턴 대학교에서 컴퓨터공학을
    전공하고 있습니다", the answer to "Which school do I go to?" said
    "University of Washington", and this guard replaced a remembered fact
    with "I don't want to send you somewhere I haven't checked". One run in
    two -- the fact was there, its English name was not."""

    def setUp(self):
        import contextlib
        import io as streams

        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(streams.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "en"
        self.engine.memory_enabled = True
        self.engine.memory_manager = object()
        self.told = []
        self.engine._what_they_told_her = lambda: list(self.told)

    def tearDown(self):
        self.engine.memory_enabled = False
        self.engine.memory_manager = None
        self.engine.close()

    def guard(self, reply, said):
        import contextlib
        import io as streams

        with contextlib.redirect_stdout(streams.StringIO()):
            return self.engine._enforce_grounded_entities(
                reply, user_input=said, action_performed=False,
            )

    def test_the_english_name_of_a_korean_memory(self):
        self.told = ["사용자는 워싱턴 대학교에서 컴퓨터공학을 전공하고 있습니다."]
        reply = "You are attending the University of Washington, majoring in computer engineering."

        self.assertEqual(self.guard(reply, "Which school do I go to?"), reply)

    def test_and_the_korean_name_of_an_english_memory(self):
        self.told = ["The user attends the University of Washington."]
        reply = "워싱턴 대학교에 다니고 계십니다."

        self.assertEqual(self.guard(reply, "내가 어느 학교 다니지?"), reply)

    def test_a_place_nothing_said_is_still_retracted(self):
        self.told = ["The user is vegetarian."]

        guarded = self.guard("You could try Melody House or Guitar Center Korea in Seoul.",
                             "where can I buy a guitar?")

        self.assertNotIn("Melody House", guarded)
        self.assertNotIn("Guitar Center Korea", guarded)


class WhatTheySaidEarlierIsTheirsTests(unittest.TestCase):
    """Simulated learners, 2026-10-02: "What's an API?", then "so it's not
    an app?" -- and "API" in the answer was retracted as a place nothing
    had checked, because only this turn's words counted as theirs."""

    def setUp(self):
        import contextlib
        import io

        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "en"

    def tearDown(self):
        self.engine.close()

    def _guard(self, reply, said):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._enforce_grounded_entities(
                reply, user_input=said, action_performed=False, evidence="",
            )

    def test_a_term_from_an_earlier_turn(self):
        said = "Okay, so it's not something I install, right? It's just... how the apps talk to each other?"
        reply = ("An API is like a set of instructions that let apps talk to each other, not something "
                 "you install. For example, when you check the weather in an app, it uses an API to get "
                 "the data from a weather service.")
        self.engine._said_this_session = [said]
        self.assertNotEqual(self._guard(reply, said), reply, "the measured retraction")

        self.engine._said_this_session = ["What's an API? My coworkers keep saying it.", said]
        self.assertEqual(self._guard(reply, said), reply)

    def test_but_not_a_shop_nobody_named(self):
        said = "Where can I buy one?"
        self.engine._said_this_session = ["I want to learn guitar.", said]
        guarded = self._guard("Check out the stores like Melody House or Guitar Center Korea.", said)

        self.assertNotIn("Melody House", guarded)


class TheGuardInTheEngineTests(unittest.TestCase):
    """What she says once a name has been retracted, in both languages."""

    def setUp(self):
        import contextlib
        import io

        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def _guard(self, reply: str, said: str, language: str) -> str:
        import contextlib
        import io

        self.engine._turn_language = language
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._enforce_grounded_entities(
                reply, user_input=said, action_performed=False, evidence="",
            )

    def test_a_korean_drama_nothing_checked_is_retracted(self):
        guarded = self._guard(
            "그 드라마는 별로셨군요. 캐나다 드라마 '더 블랙 블레이드'를 보십시오.",
            "음 별로네", "ko",
        )

        self.assertNotIn("더 블랙 블레이드", guarded)
        self.assertEqual(
            guarded,
            "그 드라마는 별로셨군요. "
            + guard_lines.say("unchecked_name_offer", "ko"),
        )

    def test_a_drama_is_not_somewhere_in_english_either(self):
        guarded = self._guard(
            "Not into that one? Try The Black Blade.", "hmm not really", "en",
        )

        self.assertNotIn("The Black Blade", guarded)
        self.assertNotIn("somewhere", guarded)
        self.assertTrue(
            guarded.endswith(guard_lines.say("unchecked_name_offer", "en")),
        )

    def test_a_shop_keeps_the_place_line(self):
        guarded = self._guard(
            "You could check out local music stores like Melody House.",
            "where can I buy a guitar", "en",
        )

        self.assertIn(guard_lines.say("unchecked_place_offer", "en"), guarded)

    def test_the_offer_is_not_said_twice_in_a_row(self):
        first = self._guard(
            "그 드라마는 별로셨군요. 캐나다 드라마 '더 블랙 블레이드'를 보십시오.",
            "음 별로네", "ko",
        )
        self.engine.conversation.add("user", "음 별로네")
        self.engine.conversation.add("assistant", first)

        second = self._guard(
            "영화도 좋습니다. '아이언 하트'를 추천합니다.", "그럼 영화는?", "ko",
        )

        self.assertEqual(second, "영화도 좋습니다.")


if __name__ == "__main__":
    unittest.main()
