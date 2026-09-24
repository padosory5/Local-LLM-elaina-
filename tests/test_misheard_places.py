"""Misheard place names, from the app's own transcriber.

Measured live, asked cold:

    said:   인천공항에서 미국 시애틀까지 가는데 몇 시간 걸려?
    heard:  빈천공항에서 미국 CLT까지 가는데 몇 시간 걸려?

and she searched for "Binche Airport". The near-miss guard only compared
words with what the conversation held, and it held nothing. The positives
below are real mishearings of the app's own faster-whisper model --
travel questions spoken by the Windows Korean voice, clean and with noise
(the harness in the session notes) -- not invented ones.

A Korean word is compared with the well-known places only when the
sentence uses it as a place, which is what keeps "지지도" as approval
rating and "시리즈" as a series.
"""

from __future__ import annotations

import unittest

from brain import known_names, near_miss
from brain.near_miss import ACRONYM, NAME

PLACES = known_names.korean_names()


def _assume_all(said: str, messages=(), theirs=()):
    """Every name correction, applied in order, as the engine applies them."""
    held = near_miss.held_terms(messages, said_by_them=theirs)
    text, corrections = said, []
    for _ in range(3):
        slip = near_miss.find(text, held, seen=" ".join(messages),
                              known=PLACES, only="assume")
        if slip is None:
            break
        corrections.append((slip.heard, slip.meant))
        text = slip.corrected
    return text, corrections


class TheHarvestedMishearingsTests(unittest.TestCase):

    def test_each_is_corrected_to_the_place_meant(self):
        for heard, meant in (
            ("빈천공항에서 미국 CLT까지 가는데 몇 시간 걸려?",       # the live one
             "인천공항에서 미국 CLT까지 가는데 몇 시간 걸려?"),
            ("인천공항에서 미국 시의틀까지 가는 데 몇 시간 걸려.",   # clean audio
             "인천공항에서 미국 시애틀까지 가는 데 몇 시간 걸려."),
            ("시리틀에서 뱅코버까지 차로 몇 시간이야",
             "시애틀에서 밴쿠버까지 차로 몇 시간이야"),
            ("김포공항에서 지지도까지 비행기로 얼마나 걸려.",
             "김포공항에서 제주도까지 비행기로 얼마나 걸려."),
            ("로스앤젤리스의 공항에서 한인타운까지 어떻게 가?",
             "로스앤젤레스의 공항에서 한인타운까지 어떻게 가?"),
            ("벨뷰에서 시에틀 다운타운까지 버스 있어.",
             "벨뷰에서 시애틀 다운타운까지 버스 있어."),
            ("빈천 공항에서 서울까지 버스 있어?",                   # one name, two words
             "인천공항에서 서울까지 버스 있어?"),
        ):
            with self.subTest(heard=heard):
                self.assertEqual(_assume_all(heard)[0], meant)

    def test_both_names_in_one_sentence(self):
        text, corrections = _assume_all("시리틀에서 뱅코버까지 차로 몇 시간이야")

        self.assertEqual(corrections, [("시리틀", "시애틀"), ("뱅코버", "밴쿠버")])


class WhatStaysTests(unittest.TestCase):

    def test_real_words_and_real_places_are_left_alone(self):
        for said in (
            "대통령 지지도가 올랐어",                     # approval rating
            "이 드라마 시리즈까지 다 봤어",               # a series
            "인천공항에서 미국 시리즈까지 가는 명약시간 걸려",  # too far to say
            "인천공항에서 뭐 먹을까?",
            "시애틀에서 밴쿠버까지 차로 몇 시간이야?",
            "김포공항에서 제주도까지 비행기로 얼마나 걸려?",
        ):
            with self.subTest(said=said):
                self.assertEqual(_assume_all(said), (said, []))

    def test_every_dogfood_turn_with_the_places_switched_on(self):
        from scripts.live_dogfood_conversation import ARCS

        for arc, spec in ARCS.items():
            turns = [said for said, _ in spec["turns"]]
            for index, said in enumerate(turns):
                with self.subTest(arc=arc, said=said):
                    self.assertEqual(
                        _assume_all(said, turns[:index], theirs=turns[:index]),
                        (said, []),
                    )


class TheCodeThatSoundsLikeAPlaceTests(unittest.TestCase):
    """CLT is Charlotte -- unless Seattle is where this person is."""

    SAID = "인천공항에서 미국 CLT까지 가는데 몇 시간 걸려?"

    def test_asked_when_seattle_is_theirs(self):
        messages = ("나 시애틀에 있는 UW 다니는 유학생이야",)
        held = near_miss.held_terms(messages, said_by_them=messages)

        slip = near_miss.find(self.SAID, held, seen=messages[0],
                              known=PLACES, only="ask")

        self.assertEqual((slip.heard, slip.meant, slip.kind), ("CLT", "시애틀", ACRONYM))
        self.assertEqual(slip.corrected, "인천공항에서 미국 시애틀까지 가는데 몇 시간 걸려?")

    def test_charlotte_otherwise(self):
        self.assertIsNone(
            near_miss.find(self.SAID, (), known=PLACES, only="ask"),
        )

    def test_the_reading(self):
        self.assertEqual(near_miss.read_aloud("CLT"), "씨엘티")


class TheLiveSentenceThroughTheEngineTests(unittest.TestCase):

    def setUp(self):
        import contextlib
        import io

        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"

    def tearDown(self):
        self.engine.close()

    def _route(self, said: str):
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._route_turn(said, timings={})

    def test_cold_the_airport_is_corrected_and_said(self):
        routing = self._route("빈천공항에서 미국 CLT까지 가는데 몇 시간 걸려?")

        self.assertEqual(routing.user_input, "인천공항에서 미국 CLT까지 가는데 몇 시간 걸려?")
        self.assertEqual(self.engine._slip_assumed, "인천공항 말씀으로 이해했습니다.")

    def test_knowing_seattle_she_also_asks_about_clt(self):
        self.engine.conversation.add("user", "나 시애틀에 있는 UW 다니는 유학생이야")
        self.engine.conversation.add("assistant", "그렇군요. 반갑습니다.")

        routing = self._route("빈천공항에서 미국 CLT까지 가는데 몇 시간 걸려?")

        self.assertEqual(
            routing.locked_response,
            "인천공항 말씀으로 이해했습니다. "
            "방금 CLT라고 하셨는데, 혹시 시애틀 말씀이신가요?",
        )
        answered = self._route("응")
        self.assertEqual(answered.user_input, "인천공항에서 미국 시애틀까지 가는데 몇 시간 걸려?")


if __name__ == "__main__":
    unittest.main()
