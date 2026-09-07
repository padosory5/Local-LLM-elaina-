# -*- coding: utf-8 -*-
"""What she names as the answer has to be one of the results.

The invariant: if a reply says "I found", "I recommend", or names a
live-search result, the named thing must be a structured candidate
currently in the ResultSet. No product out of the model's memory may be
presented as though the search returned it.

Two guards already existed and both stepped aside the moment the search
found anything at all::

    if not text or candidates:
        return text

So they answered "was anything found" and never "is what she named one of
them". Measured live, in one eight-turn conversation, with cards on screen
beside every one of these:

    cards:  5K2K OLED, GX9 39
    Elaina: "The LG 45GX950A-B is the best fit... 165Hz refresh rate..."

    cards:  Seoul DDJ STAY, Hotel Inspiroom Jongro, Sofitel Ambassador
    Elaina: "L'Escape offers luxury..., while the JW Marriott Dongdaemun
             feels more intimate"

    cards:  (none)
    Elaina: "The Asus ROG Swift PG279Q ... with a 144Hz refresh rate"

Every named thing was absent from the results, and the refresh rates were
invented alongside them.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain import grounded_values as gv
from brain import result_state as rs
from tests.turn_harness import build_engine


MONITORS = ("5K2K OLED", "GX9 39")
HOTELS = (
    "Seoul DDJ STAY",
    "Hotel Inspiroom Jongro",
    "Sofitel Ambassador Seoul Hotel",
)


class ANameOffTheResultSetIsCaughtTests(unittest.TestCase):

    def test_a_model_from_memory_beside_different_cards(self):
        said = (
            "The LG 45GX950A-B is the best fit, it's a 5K2K OLED curved "
            "gaming monitor with 165Hz refresh rate."
        )

        self.assertIn(
            "LG 45GX950A-B",
            gv.names_outside_the_results(said, candidates=MONITORS),
        )

    def test_hotels_from_memory_beside_different_cards(self):
        said = (
            "L'Escape offers luxury and is near shopping, while the "
            "JW Marriott Dongdaemun feels more intimate."
        )

        self.assertIn(
            "JW Marriott Dongdaemun",
            gv.names_outside_the_results(said, candidates=HOTELS),
        )

    def test_a_brand_and_model_is_seen_at_all(self):
        # _PROPER_NAME's multi-word branch needs every word capitalised, and
        # a model number is not. "LG 45GX950A-B" reached the old guards as
        # the single word "LG" and was skipped for being one word.
        self.assertIn("LG 45GX950A-B", gv._brand_models("The LG 45GX950A-B is good."))


class ARealResultIsLeftAloneTests(unittest.TestCase):
    """The regression-sensitive half: this must not eat honest answers."""

    def test_a_candidate_named_in_full(self):
        said = (
            "Hotel Inspiroom Jongro and Sofitel Ambassador Seoul Hotel "
            "are both solid."
        )

        self.assertEqual(
            gv.names_outside_the_results(said, candidates=HOTELS), (),
        )

    def test_a_candidate_named_in_short(self):
        # A reply may shorten a name it is naming.
        said = "The Sofitel Ambassador is my pick, it's well reviewed."

        self.assertEqual(
            gv.names_outside_the_results(said, candidates=HOTELS), (),
        )

    def test_a_model_that_really_was_found(self):
        said = "The LG 45GX950A-B is a great pick."

        self.assertEqual(
            gv.names_outside_the_results(
                said, candidates=("LG 45GX950A-B curved 5K2K",),
            ),
            (),
        )

    def test_a_name_the_person_used_is_theirs(self):
        said = "The JW Marriott Dongdaemun is a good choice."

        self.assertEqual(
            gv.names_outside_the_results(
                said, candidates=HOTELS,
                request="what do you think of the JW Marriott Dongdaemun?",
            ),
            (),
        )

    def test_nothing_is_checked_when_nothing_was_found(self):
        # That case belongs to the older guards, which handle an empty
        # result set. This one only speaks when there is a set to be in.
        self.assertEqual(
            gv.names_outside_the_results("The LG 45GX950A-B is good.",
                                         candidates=()),
            (),
        )


class TheReplyIsRewrittenTests(unittest.TestCase):

    def _engine(self):
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            engine = build_engine(routes={})
        return engine, quiet

    def _held(self):
        return tuple(
            rs.Candidate(name=name, url=f"https://example.com/{index}")
            for index, name in enumerate(HOTELS)
        )

    def test_the_sentence_naming_an_absent_thing_is_removed(self):
        engine, quiet = self._engine()
        said = (
            "L'Escape offers luxury and is near shopping. "
            "Hotel Inspiroom Jongro is quieter."
        )

        with contextlib.redirect_stdout(quiet):
            rewritten = engine._enforce_named_candidates(
                said, candidates=self._held(), searched=True,
            )

        self.assertNotIn("Marriott", rewritten)
        self.assertIn("Hotel Inspiroom Jongro", rewritten)

    def test_when_every_name_was_invented_it_says_what_it_found(self):
        engine, quiet = self._engine()
        said = "L'Escape and the JW Marriott Dongdaemun are both excellent."

        with contextlib.redirect_stdout(quiet):
            rewritten = engine._enforce_named_candidates(
                said, candidates=self._held(), searched=True,
            )

        self.assertNotIn("JW Marriott", rewritten)
        self.assertIn("Hotel Inspiroom Jongro", rewritten)

    def test_an_honest_reply_is_untouched(self):
        engine, quiet = self._engine()
        said = "Hotel Inspiroom Jongro is the one I'd start with."

        with contextlib.redirect_stdout(quiet):
            rewritten = engine._enforce_named_candidates(
                said, candidates=self._held(), searched=True,
            )

        self.assertEqual(rewritten, said)

    def test_a_turn_that_did_not_search_is_untouched(self):
        engine, quiet = self._engine()
        said = "The JW Marriott Dongdaemun is a lovely hotel."

        with contextlib.redirect_stdout(quiet):
            rewritten = engine._enforce_named_candidates(
                said, candidates=self._held(), searched=False,
            )

        self.assertEqual(rewritten, said)

    def test_it_never_invents_a_replacement(self):
        # Whatever comes back must be made of the candidates in hand or of
        # nothing at all -- never of a third name.
        engine, quiet = self._engine()
        said = "The Lotte Hotel World is the best of them."

        with contextlib.redirect_stdout(quiet):
            rewritten = engine._enforce_named_candidates(
                said, candidates=self._held(), searched=True,
            )

        self.assertNotIn("Lotte", rewritten)
        for name in gv._proper_names(rewritten):
            if len(name.split()) < 2:
                continue
            with self.subTest(name=name):
                self.assertTrue(
                    any(name in held for held in HOTELS)
                    or any(word in " ".join(HOTELS) for word in name.split()),
                )


if __name__ == "__main__":
    unittest.main()
