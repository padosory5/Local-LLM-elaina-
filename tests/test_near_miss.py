"""A word that is almost what the conversation is about -- and what is not.

The person had been asking about CPT and said "CBT". The transcriber heard
"배인브리지" for the "베인브리지" the conversation had been using. Both are
near-misses of a term the conversation holds; the first is asked about, the
second is taken as the held term and said out loud.

Most of this file is what must not fire. The last attempt at this rewrote
ordinary Korean words toward the person's own ("시청자 -> 시청한"), so every
turn of every paired dogfood arc, in both languages, is run through it here
and must come out untouched.
"""

from __future__ import annotations

import unittest

from brain import near_miss
from brain.near_miss import ACRONYM, HEARD, MEANT, NAME, find, held_terms

CPT_TALK = (
    "I'm an international student at UW and I want to apply for CPT.",
    "CPT lets F-1 students work off campus as part of their curriculum.",
)
CPT_TALK_KO = (
    "UW 유학생인데 CPT 신청하려고 해",
    "CPT는 F-1 학생이 전공 과정의 일부로 교외에서 일할 수 있게 해 줍니다.",
)
ISLAND_KO = (
    "베인브리지 섬에 카지노 있어?",
    "베인브리지 섬에는 카지노가 없습니다.",
)


def _find(said: str, messages, *, theirs=(), **kwargs):
    return find(
        said, held_terms(messages, said_by_them=theirs),
        seen=" ".join(messages), **kwargs,
    )


class TheSlipsAreFoundTests(unittest.TestCase):

    def test_cbt_for_cpt(self):
        slip = _find("What documents do I need for CBT?", CPT_TALK)

        self.assertEqual((slip.heard, slip.meant, slip.kind), ("CBT", "CPT", ACRONYM))
        self.assertTrue(slip.asks)
        self.assertEqual(slip.corrected, "What documents do I need for CPT?")

    def test_typed_in_lower_case(self):
        slip = _find("cbt 신청 기간이 언제야?", CPT_TALK_KO)

        self.assertEqual((slip.heard, slip.meant), ("cbt", "CPT"))
        self.assertEqual(slip.corrected, "CPT 신청 기간이 언제야?")

    def test_spelled_out_in_hangul_by_the_transcriber(self):
        slip = _find("씨비티 신청하려면 뭐가 필요해?", CPT_TALK_KO)

        self.assertEqual((slip.heard, slip.meant, slip.kind), ("씨비티", "CPT", ACRONYM))
        self.assertEqual(slip.corrected, "CPT 신청하려면 뭐가 필요해?")

    def test_a_misheard_korean_name(self):
        slip = _find("배인브리지에서 시애틀까지 얼마나 걸려?", ISLAND_KO)

        self.assertEqual((slip.heard, slip.meant, slip.kind), ("배인브리지", "베인브리지", NAME))
        self.assertFalse(slip.asks)
        self.assertEqual(slip.corrected, "베인브리지에서 시애틀까지 얼마나 걸려?")

    def test_a_name_they_said_once_when_her_answer_used_english(self):
        # The live demo: 베인브리지 appeared once, because she answered in
        # English words, and 배인브리지 on the next turn went unrepaired.
        asked = "베인브리지 섬에 카지노 있어?"
        talk = (asked, "Bainbridge Island에 카지노는 없습니다.")

        slip = _find("배인브리지에서 시애틀까지 페리로 얼마나 걸려?", talk,
                     theirs=(asked,))

        self.assertEqual((slip.heard, slip.meant), ("배인브리지", "베인브리지"))

    def test_a_spelled_acronym_with_its_particle(self):
        slip = _find("씨비티는 얼마나 걸려?", CPT_TALK_KO)

        self.assertEqual((slip.heard, slip.meant), ("씨비티", "CPT"))
        self.assertEqual(slip.corrected, "CPT는 얼마나 걸려?")

    def test_a_misspelled_english_name(self):
        slip = _find(
            "How long is the ferry from Seattle to Bainbrige Island?",
            ("Is there a casino on Bainbridge Island?",
             "Bainbridge Island has no casino of its own."),
        )

        self.assertEqual((slip.heard, slip.meant, slip.kind), ("Bainbrige", "Bainbridge", NAME))


class WhatIsNotASlipTests(unittest.TestCase):

    def test_the_held_term_itself_or_its_hangul_spelling(self):
        for said in ("CPT 신청 기간이 언제야?", "씨피티 신청 기간이 언제야?"):
            with self.subTest(said=said):
                self.assertIsNone(_find(said, CPT_TALK_KO))

    def test_another_acronym_whose_letters_do_not_rhyme(self):
        # OPT is a real, different thing; CPU differs by T and U.
        for said in ("What about OPT?", "Is my CPU fast enough?"):
            with self.subTest(said=said):
                self.assertIsNone(_find(said, CPT_TALK))

    def test_a_word_already_in_the_conversation(self):
        talk = CPT_TALK + ("I was also reading about CBT for anxiety.",)

        self.assertIsNone(_find("So how does CBT work?", talk))

    def test_a_pair_the_person_already_settled(self):
        self.assertIsNone(_find(
            "What documents do I need for CBT?", CPT_TALK,
            distinct=[frozenset(("CBT", "CPT"))],
        ))

    def test_two_letter_codes_are_left_alone(self):
        self.assertIsNone(_find("I studied in the UK before", CPT_TALK))

    def test_ordinary_korean_words_near_a_held_one(self):
        # The failed experiment's pairs: 시청자/시청한, 고추장/가장.
        talk = ("드라마 시청한 사람 많아?", "그 드라마를 시청한 사람이 많습니다.")
        for said in ("시청자 반응은 어때?", "고추장 넣으면 맛있어?"):
            with self.subTest(said=said):
                self.assertIsNone(_find(said, talk))

    def test_a_lower_case_english_word_is_not_a_name(self):
        talk = ("We could go to Paris in spring.", "Paris is lovely in April.")

        self.assertIsNone(_find("are there parks nearby?", talk))


class EveryDogfoodTurnIsLeftAloneTests(unittest.TestCase):
    """Every turn of every arc, both languages, against what came before."""

    def test_no_turn_is_read_as_a_slip(self):
        from scripts.live_dogfood_conversation import ARCS

        for arc, spec in ARCS.items():
            turns = [said for said, _ in spec["turns"]]
            for index, said in enumerate(turns):
                with self.subTest(arc=arc, said=said):
                    # All of them are the person's own words, so the
                    # single-mention rule for long Korean words is in play.
                    self.assertIsNone(
                        _find(said, turns[:index], theirs=turns[:index]),
                    )


class KnownSlipTests(unittest.TestCase):

    def test_a_confirmed_slip_is_found_however_it_is_written(self):
        known = {"CBT": "CPT"}
        for said, heard in (("CBT 기간은?", "CBT"), ("cbt 기간은?", "cbt"),
                            ("씨비티 기간은?", "씨비티")):
            with self.subTest(said=said):
                slip = near_miss.known_slip(said, known)
                self.assertEqual((slip.heard, slip.meant), (heard, "CPT"))
                self.assertFalse(slip.asks)

    def test_the_key_is_the_letters(self):
        for heard in ("CBT", "cbt", "씨비티"):
            slip = near_miss._slip(f"{heard}?", heard, "CPT", ACRONYM)
            with self.subTest(heard=heard):
                self.assertEqual(near_miss.slip_key(slip), "CBT")
                self.assertTrue(near_miss.names_the_heard("아니 CBT 맞아", slip))

    def test_nothing_known_nothing_found(self):
        self.assertIsNone(near_miss.known_slip("CBT 기간은?", {}))


class ReadingTheAnswerTests(unittest.TestCase):

    SLIP = near_miss._slip("What do I need for CBT?", "CBT", "CPT", ACRONYM)

    def test_answers(self):
        for reply, meaning in (
            ("yes", MEANT), ("yeah, CPT", MEANT), ("응", MEANT), ("네 맞아요", MEANT),
            ("CPT", MEANT), ("아니 CPT 맞아", MEANT), ("no I meant CPT", MEANT),
            ("no", HEARD), ("아니요", HEARD), ("no, CBT", HEARD), ("CBT 맞아", HEARD),
            ("what's the weather tomorrow?", ""), ("내일 날씨 어때?", ""),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(near_miss.read_answer(reply, self.SLIP), meaning)

    def test_the_korean_quote_particle(self):
        self.assertEqual(near_miss.quoted_particle("CBT"), "라고")
        self.assertEqual(near_miss.quoted_particle("씨비티"), "라고")
        self.assertEqual(near_miss.quoted_particle("공항"), "이라고")


if __name__ == "__main__":
    unittest.main()
