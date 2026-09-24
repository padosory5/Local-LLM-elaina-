"""A search runs in the language the person was speaking.

Measured over a real Korean session, 4 of 5 searches went out in a language
the turn was not (`scripts/query_language_check.py runtime/koreanSession.log`).
The router writes in English -- the whole prompt is English -- and the query
is built from what the router wrote.

That is wrong twice. The pages that answer "서울에서 제일 유명한 냉면집"
are Korean ones, and it went out as "seoul most famous cold noodles
restaurant". And a translated proper noun is a different thing: 워싱턴
대학교 came back as "Washington University in Seattle", which is a real
university, in St. Louis, and not the one the person attends.

Asking the router nicely was tried first, because it is the cheap fix and
would have been the right one if it worked. Pooled over three runs against
qwen3:8b it moved 9/30 to 9/30 -- this project's standing result for prompt
wording against a confirmed behaviour. So the person's own words are used
instead: they are the one string guaranteed to be in the right language and
to carry the proper nouns in the form they used.

Half this file is about what the rule refuses to do, and that half is the
harder one. A turn that leans on the one before it does not carry its
subject, and the subject the conversation holds is stored in English by the
router -- so there is nothing in Korean to put back, and searching those
words alone would be worse than the English query rather than better.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brain import search_language
from scripts.query_language_check import score

MATRIX = json.loads(
    (Path(__file__).with_name("query_language_matrix.json")).read_text(
        encoding="utf-8",
    )
)


class TheMatrixTests(unittest.TestCase):

    def test_every_case_lands_where_it_should(self):
        for case in MATRIX["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    score(case), case["expect"],
                    case.get("why", case["id"]),
                )

    def test_it_measures_both_outcomes_and_both_languages(self):
        wanted = [case["expect"] for case in MATRIX["cases"]]
        languages = {case["language"] for case in MATRIX["cases"]}

        self.assertGreaterEqual(wanted.count("rewritten"), 5)
        self.assertGreaterEqual(wanted.count("kept"), 5)
        self.assertEqual(languages, {"en", "ko"})


class TheWordsThatGoIntoTheBoxTests(unittest.TestCase):

    def test_the_request_verb_comes_off_the_end(self):
        # Korean is head-final, so "알려줘" sits at the end and carries
        # none of what is being asked about.
        self.assertEqual(
            search_language.as_query("서울에서 제일 유명한 냉면집 알려줘"),
            "서울에서 제일 유명한 냉면집",
        )
        self.assertEqual(
            search_language.as_query("삼성전자 주가 좀 찾아줘"),
            "삼성전자 주가",
        )

    def test_nothing_else_is_touched(self):
        # No noun-phrase extraction. Korean needs a morphological analyser
        # for that and there is not one here -- and a question asked whole
        # is a perfectly good search.
        said = "워싱턴 대학교는 어떤 과가 유명해?"

        self.assertEqual(search_language.as_query(said), said.rstrip("?"))

    def test_a_proper_noun_survives_in_the_form_the_person_used(self):
        query, _ = search_language.in_the_turns_language(
            "famous majors at Washington University",
            said="워싱턴 대학교는 어떤 과가 유명해?",
            language="ko",
        )

        self.assertIn("워싱턴 대학교", query)
        self.assertNotIn("Washington University", query)


class WhatItRefusesToDoTests(unittest.TestCase):
    """The half that keeps the fix from being a regression."""

    def test_a_turn_leaning_on_the_last_one_keeps_the_english_query(self):
        original = "famous things about University of Washington"

        query, why = search_language.in_the_turns_language(
            original, said="그런 뻔한거 말고 뭐가 유명한지 알려줘", language="ko",
        )

        self.assertEqual(query, original)
        self.assertIn("leans on the one before it", why)

    def test_it_says_so_rather_than_going_quiet(self):
        # The turns this cannot help are the ones worth counting.
        _, why = search_language.in_the_turns_language(
            "best gaming monitor comparison",
            said="그중에 어떤게 제일 좋아?", language="ko",
        )

        self.assertTrue(why)

    def test_a_query_already_in_the_right_language_is_left_alone(self):
        original = "삼성전자 주가"

        query, why = search_language.in_the_turns_language(
            original, said="삼성전자 주가 알려줘", language="ko",
        )

        self.assertEqual(query, original)
        self.assertEqual(why, "")

    def test_a_turn_in_the_other_script_has_nothing_to_put_back(self):
        # "ok" inside a Korean conversation. The language decision is
        # sticky and correct; the words are not Korean.
        original = "latest GPT model version"

        query, _ = search_language.in_the_turns_language(
            original, said="ok", language="ko",
        )

        self.assertEqual(query, original)

    def test_english_turns_are_untouched(self):
        for said, query in (
            ("what is famous about the University of Washington?",
             "University of Washington famous aspects"),
            ("and what about the second one?", "Seoul hotels comparison"),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    search_language.in_the_turns_language(
                        query, said=said, language="en",
                    )[0],
                    query,
                )


class ThroughTheEngineTests(unittest.TestCase):

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def test_a_korean_turn_searches_in_korean(self):
        from types import SimpleNamespace

        self.engine._turn_language = "ko"
        route = SimpleNamespace(
            search_query="famous majors at Washington University",
            normalized_request="What are the famous majors at Washington University",
            topic="Washington University", is_follow_up=False,
        )

        query = self.engine._resolved_search_query(
            route, SimpleNamespace(subject="Washington University"),
            said="워싱턴 대학교는 어떤 과가 유명해?",
        )

        self.assertIn("워싱턴 대학교", query)

    def test_an_english_turn_is_unchanged(self):
        from types import SimpleNamespace

        self.engine._turn_language = "en"
        route = SimpleNamespace(
            search_query="University of Washington famous majors",
            normalized_request="What are the famous majors at UW",
            topic="University of Washington", is_follow_up=False,
        )

        query = self.engine._resolved_search_query(
            route, SimpleNamespace(subject="University of Washington"),
            said="what majors is UW famous for?",
        )

        self.assertEqual(query, "University of Washington famous majors")


if __name__ == "__main__":
    unittest.main()
