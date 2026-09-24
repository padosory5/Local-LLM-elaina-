"""Grounding a stated number in what the encyclopedia says.

The model states facts it does not have: Portland, Maine's population came
back as 694,000 twice (it is about 68,000). No prompt fixes that, and the
grounded-value guard cannot either -- it checks a reply against evidence,
and a turn answered from the model's own memory has none.

The network is stubbed here. What is tested is the part that decides
whether to trust a summary at all, which is where this can do harm.
"""

from __future__ import annotations

import unittest

from brain import world_facts


def canned(title, extract):
    def fetch(url, timeout):
        if "search/title" in url:
            return {"pages": [{"key": title.replace(" ", "_"), "title": title}]}
        return {
            "title": title,
            "extract": extract,
            "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title}"}},
        }
    return fetch


PORTLAND = ("Portland, Maine",
            "Portland is the most populous city in the U.S. state of Maine. Its "
            "population was 68,408 at the 2020 census. It sits on Casco Bay.")


class WhatIsBeingAskedAboutTests(unittest.TestCase):

    def test_the_attribute_is_not_the_subject(self):
        self.assertEqual(
            world_facts.subject_of("What's the population of Portland, Maine?"),
            "Portland, Maine",
        )
        self.assertEqual(
            world_facts.subject_of("Who wrote Pride and Prejudice?"),
            "Pride and Prejudice",
        )
        self.assertEqual(world_facts.subject_of("What is the capital of France?"), "France")

    def test_in_korean(self):
        self.assertEqual(world_facts.subject_of("대한민국 수도가 어디야?"), "대한민국")
        # The quantity word sits mid-sentence; everything from it on is the
        # question, and the particle goes with it.
        self.assertEqual(world_facts.subject_of("물은 몇 도에서 끓어?"), "물")


class OnlyANamedThingTests(unittest.TestCase):
    """The guard that keeps this from doing harm. "How many ounces are in a
    pound?" leaves "a pound", whose article search finds *Pound sterling* --
    and its numbers would then contradict the right answer, 16."""

    def test_a_name(self):
        for subject in ("Portland, Maine", "Pride and Prejudice", "France", "대한민국"):
            with self.subTest(subject=subject):
                self.assertTrue(world_facts.names_an_entity(subject))

    def test_not_a_common_noun_or_a_unit(self):
        for subject in ("a pound", "80", "long is the flight", "물", ""):
            with self.subTest(subject=subject):
                self.assertFalse(world_facts.names_an_entity(subject))

    def test_nothing_is_fetched_for_one(self):
        asked = []

        def fetch(url, timeout):
            asked.append(url)
            return {}

        self.assertIsNone(world_facts.lookup(
            "How many ounces are in a pound?", fetch=fetch))
        self.assertEqual(asked, [])


class WhatComesBackTests(unittest.TestCase):

    def setUp(self):
        world_facts._cache.clear()

    def test_the_summary_and_where_it_came_from(self):
        fact = world_facts.lookup(
            "What's the population of Portland, Maine?", fetch=canned(*PORTLAND))

        self.assertEqual(fact.title, "Portland, Maine")
        self.assertIn("68,408", fact.summary)
        self.assertIn("wikipedia.org", fact.url)
        self.assertEqual(fact.sentence(1),
                         "Portland is the most populous city in the U.S. state of Maine.")

    def test_a_failure_is_not_an_answer(self):
        def broken(url, timeout):
            raise TimeoutError("no network")

        self.assertIsNone(world_facts.lookup(
            "What's the population of Portland, Maine?", fetch=broken))


class WhenItMayCorrectTests(unittest.TestCase):

    def setUp(self):
        world_facts._cache.clear()
        self.fact = world_facts.lookup(
            "What's the population of Portland, Maine?", fetch=canned(*PORTLAND))

    def test_a_wrong_number(self):
        self.assertTrue(world_facts.contradicts(
            "Portland's population is around 694,000.", self.fact))

    def test_the_right_number(self):
        self.assertFalse(world_facts.contradicts(
            "Portland, Maine has about 68,408 people.", self.fact))

    def test_no_number_at_all(self):
        self.assertFalse(world_facts.contradicts(
            "It is a coastal city in Maine.", self.fact))

    def test_only_when_the_summary_speaks_to_the_attribute(self):
        self.assertTrue(world_facts.covers_the_attribute(
            "What's the population of Portland, Maine?", self.fact))
        # The summary's numbers are a population and a census year; an area
        # question is not settled by them.
        self.assertFalse(world_facts.covers_the_attribute(
            "What's the area of Portland, Maine?", self.fact))
        # Nothing recognisable to check: left alone.
        self.assertFalse(world_facts.covers_the_attribute(
            "What is Portland, Maine like?", self.fact))


class TheEngineUsesItTests(unittest.TestCase):

    def setUp(self):
        import contextlib
        import io as streams
        from tests.turn_harness import build_engine
        with contextlib.redirect_stdout(streams.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "en"
        world_facts._cache.clear()
        self.fact = world_facts.lookup(
            "What's the population of Portland, Maine?", fetch=canned(*PORTLAND))
        self.looked_up = []
        self.real_lookup = world_facts.lookup
        world_facts.lookup = self._lookup

    def tearDown(self):
        world_facts.lookup = self.real_lookup
        self.engine.close()

    def _lookup(self, question, **kwargs):
        self.looked_up.append(question)
        return self.fact

    def check(self, said, reply):
        import contextlib
        import io as streams
        with contextlib.redirect_stdout(streams.StringIO()):
            return self.engine._checked_against_the_encyclopedia(said, reply)

    def test_the_wrong_number_is_replaced_with_what_the_summary_says(self):
        said = self.check("What's the population of Portland, Maine?",
                          "Portland's population is around 694,000.")

        self.assertIn("68,408", said)
        self.assertNotIn("694,000", said)

    def test_a_right_answer_is_left_alone(self):
        reply = "Portland, Maine has a population of about 68,408."

        self.assertEqual(self.check("What's the population of Portland, Maine?", reply), reply)

    def test_an_answer_with_no_number_is_not_even_looked_up(self):
        reply = "Portland is on the coast of Maine."

        self.assertEqual(self.check("What's the population of Portland, Maine?", reply), reply)
        self.assertEqual(self.looked_up, [])

    def test_an_attribute_the_summary_does_not_cover_is_left_alone(self):
        reply = "Portland, Maine covers about 69 square miles."

        self.assertEqual(self.check("What's the area of Portland, Maine?", reply), reply)


if __name__ == "__main__":
    unittest.main()
