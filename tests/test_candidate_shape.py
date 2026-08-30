"""Is this even a candidate, and do we actually know it fits?

Two failures, both measured live after constraints and locale were working.

The search for an electric guitar around 500,000 won came back with "25
Best Electric Guitars in 2026", and that was recommended -- an article
about guitars is not a guitar. The search for a soft dinner came back with
recipe collections and a travel vlog, and then with three restaurants none
of which said anything about texture; "soft" was UNCHECKED for all of them
and Korean BBQ was recommended anyway.

Silence is not evidence. A candidate that nothing has shown to meet an
important constraint is not thereby a candidate that meets it.
"""

from __future__ import annotations

import unittest

from brain import candidate_fit as cf
from brain import recommendation_state as rs
from brain import semantic_fit
from brain.task_session import TaskSessionStore


def _guitar():
    store = TaskSessionStore()
    for turn in ("I want a guitar.", "Electric.", "About 500,000 won."):
        problem = store.note_recommendation_turn(turn, subject="guitar")
    return problem


def _dinner():
    store = TaskSessionStore()
    store.note_recommendation_turn(
        "What should I eat for dinner?", subject="dinner",
    )
    return store.note_recommendation_turn(
        "I have a sore throat, something soft.", subject="dinner",
    )


LISTICLE = {
    "title": "25 Best Electric Guitars in 2026",
    "url": "https://guitarlobby.com/best-electric-guitars/",
    "summary": "Our picks for every budget",
}
REAL_GUITAR = {
    "title": "Cort X250 Electric Guitar",
    "url": "https://danawa.com/info/1",
    "summary": "Electric guitar, 480,000 won",
}
VLOG = {
    "title": "10 Days Around SOUTH KOREA - YouTube",
    "url": "https://youtube.com/watch?v=abc",
    "summary": "First time visiting",
}
RECIPES = {
    "title": "12 Cozy Korean Dinner Recipes",
    "url": "https://example.com/blog/recipes",
    "summary": "Warm meals for evenings",
}
REAL_PLACE = {
    "title": "Juk Story Myeongdong",
    "url": "https://diningcode.com/profile/1",
    "summary": "Rating 4.3, menu, open 09:00 until 21:00",
}
BBQ = {
    "title": "401 Restaurant Korean BBQ",
    "url": "https://diningcode.com/profile/2",
    "summary": "Rating 4.2, open until 22:00",
}


class ExpectedShapeTests(unittest.TestCase):

    def test_a_thing_to_buy_expects_products(self):
        self.assertEqual(cf.expected_shape(_guitar()), cf.PRODUCT)

    def test_a_place_to_eat_expects_places(self):
        self.assertEqual(cf.expected_shape(_dinner()), cf.PLACE)

    def test_an_ordinary_recommendation_expects_nothing_in_particular(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "I want something relaxing.", subject="evening",
        )

        self.assertEqual(cf.expected_shape(problem), cf.ANY)


class ArticlesAreNotCandidatesTests(unittest.TestCase):
    """The scenario-C requirement, exactly."""

    def test_a_round_up_is_rejected_as_an_article(self):
        fits = cf.evaluate([LISTICLE], _guitar())

        self.assertEqual(fits[0].verdict, "OFF-TARGET")
        self.assertFalse(fits[0].viable)

    def test_a_real_product_survives(self):
        fits = cf.evaluate([REAL_GUITAR], _guitar())

        self.assertEqual(fits[0].verdict, "FITS")

    def test_an_article_can_never_outrank_a_candidate(self):
        fits = cf.evaluate([LISTICLE, REAL_GUITAR], _guitar())

        self.assertEqual(fits[0].name, REAL_GUITAR["title"])

    def test_a_video_is_not_a_restaurant(self):
        fits = cf.evaluate([VLOG], _dinner())

        self.assertEqual(fits[0].verdict, "OFF-TARGET")

    def test_a_recipe_collection_is_not_a_restaurant(self):
        fits = cf.evaluate([RECIPES], _dinner())

        self.assertEqual(fits[0].verdict, "OFF-TARGET")

    def test_a_real_place_survives(self):
        fits = cf.evaluate([REAL_PLACE], _dinner())

        self.assertFalse(fits[0].shape_problem)

    def test_the_shape_check_is_off_when_nothing_particular_is_expected(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "I want something relaxing.", subject="evening",
        )

        fits = cf.evaluate([LISTICLE], problem)

        self.assertEqual(fits[0].shape_problem, "")


class UncheckedIsNotPermissionTests(unittest.TestCase):
    """The scenario-D requirement."""

    def test_an_important_constraint_nothing_shows_is_unresolved(self):
        fits = cf.evaluate([BBQ, REAL_PLACE], _dinner())

        self.assertIn("soft", cf.unresolved_constraints(fits, _dinner()))

    def test_all_unchecked_means_no_confident_winner(self):
        problem = _dinner()
        fits = cf.evaluate([BBQ, REAL_PLACE], problem)

        self.assertFalse(cf.confident(fits, problem))

    def test_a_resolved_constraint_allows_a_winner(self):
        problem = _guitar()
        fits = cf.evaluate([REAL_GUITAR], problem)

        self.assertTrue(cf.confident(fits, problem))

    def test_no_survivors_is_also_not_confidence(self):
        problem = _guitar()
        fits = cf.evaluate([LISTICLE], problem)

        self.assertFalse(cf.confident(fits, problem))

    def test_a_conflict_never_becomes_the_top_recommendation(self):
        # The invariant, stated on its own.
        problem = _guitar()
        acoustic = {
            "title": "Yamaha APX500 Acoustic-Electric Guitar",
            "url": "https://danawa.com/info/2",
            "summary": "450,000 won",
        }

        fits = cf.evaluate([acoustic, REAL_GUITAR], problem)

        self.assertFalse(fits[0].rejected)
        self.assertEqual(fits[0].name, REAL_GUITAR["title"])


class SemanticFallbackTests(unittest.TestCase):
    """Only for what deterministic checks genuinely cannot settle."""

    class _Model:
        def __init__(self, text):
            self.text = text
            self.calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            return {"message": {"content": self.text}}

    class _Broken:
        def chat(self, **kwargs):
            raise RuntimeError("the model is not running")

    def test_a_judgement_becomes_an_ordinary_match_or_conflict(self):
        problem = _dinner()
        fits = cf.evaluate([BBQ, REAL_PLACE], problem)
        model = self._Model(
            '{"verdicts":[{"n":1,"answer":"no"},{"n":2,"answer":"yes"}]}'
        )

        verdicts = semantic_fit.check(
            model, "m", cf.viable(fits), "soft",
        )
        after = cf.with_semantic(fits, "soft", verdicts)

        self.assertEqual(after[0].name, REAL_PLACE["title"])
        self.assertEqual(after[0].verdict, "FITS")
        self.assertEqual(
            [fit.verdict for fit in after if "BBQ" in fit.name], ["MISMATCH"],
        )

    def test_it_resolves_the_confidence_question(self):
        problem = _dinner()
        fits = cf.evaluate([BBQ, REAL_PLACE], problem)
        after = cf.with_semantic(
            fits, "soft",
            {BBQ["title"]: "no", REAL_PLACE["title"]: "yes"},
        )

        self.assertTrue(cf.confident(after, problem))

    def test_one_call_for_the_whole_shortlist(self):
        fits = cf.evaluate([BBQ, REAL_PLACE], _dinner())
        model = self._Model('{"verdicts":[]}')

        semantic_fit.check(model, "m", cf.viable(fits), "soft")

        self.assertEqual(model.calls, 1)

    def test_a_model_that_is_down_leaves_it_unresolved(self):
        problem = _dinner()
        fits = cf.evaluate([BBQ, REAL_PLACE], problem)

        verdicts = semantic_fit.check(
            self._Broken(), "m", cf.viable(fits), "soft",
        )

        self.assertEqual(verdicts, {})
        self.assertFalse(cf.confident(fits, problem))

    def test_unparseable_output_leaves_it_unresolved(self):
        fits = cf.evaluate([BBQ, REAL_PLACE], _dinner())

        self.assertEqual(
            semantic_fit.check(
                self._Model("I think the first one, probably"),
                "m", cf.viable(fits), "soft",
            ),
            {},
        )

    def test_an_unknown_answer_changes_nothing(self):
        problem = _dinner()
        fits = cf.evaluate([BBQ, REAL_PLACE], problem)

        after = cf.with_semantic(
            fits, "soft", {BBQ["title"]: "unknown"},
        )

        self.assertEqual(
            [fit.verdict for fit in after if "BBQ" in fit.name], ["UNCHECKED"],
        )

    def test_the_shortlist_is_bounded(self):
        self.assertLessEqual(semantic_fit.MAX_CANDIDATES, 5)

    def test_nothing_is_asked_when_there_is_nothing_to_ask_about(self):
        model = self._Model('{"verdicts":[]}')

        semantic_fit.check(model, "m", (), "soft")

        self.assertEqual(model.calls, 0)


if __name__ == "__main__":
    unittest.main()


class SurfacesAreNotCandidatesTests(unittest.TestCase):
    """A map is how you find a restaurant. It is not a restaurant.

    The first version of the shape layer rejected Naver Maps along with the
    blog posts, which threw away the most useful result on the page -- in
    Korea it is where a great many people actually look for somewhere to
    eat. A directory, a marketplace or a job board is a source, and a
    source is kept and never recommended.
    """

    def setUp(self):
        from brain import acquisition
        from brain.user_locale import UserLocale

        self.acq = acquisition
        self.hosts = acquisition.surface_hosts(
            UserLocale(country="KR", city="Seoul"), "restaurant",
        )

    def test_a_map_search_is_a_surface(self):
        self.assertEqual(
            self.acq.classify(
                "https://map.naver.com/p/search/juk",
                surface_hosts=self.hosts,
            ),
            self.acq.SOURCE_SURFACE,
        )

    def test_a_directory_list_page_is_a_surface(self):
        self.assertEqual(
            self.acq.classify(
                "https://www.diningcode.com/list.dc?query=seoul",
                surface_hosts=self.hosts,
            ),
            self.acq.SOURCE_SURFACE,
        )

    def test_an_entity_page_on_that_same_site_is_a_candidate(self):
        # The distinction is the shape of the URL, not the host: one site
        # serves both.
        self.assertEqual(
            self.acq.classify(
                "https://www.diningcode.com/profile.php?rid=qk74g6MEO1Vf",
                surface_hosts=self.hosts,
            ),
            self.acq.CANDIDATE,
        )

    def test_a_blog_on_a_surface_host_is_still_a_blog(self):
        # m.blog.naver.com is Naver and is not a map.
        self.assertEqual(
            self.acq.classify(
                "https://m.blog.naver.com/yr1109/223917523114",
                surface_hosts=self.hosts,
            ),
            self.acq.OFF_TARGET,
        )

    def test_a_product_listing_is_a_candidate(self):
        self.assertEqual(
            self.acq.classify("https://danawa.com/info/?pcode=1234567"),
            self.acq.CANDIDATE,
        )

    def test_no_market_site_is_named_in_the_logic(self):
        # Market knowledge belongs to the locale layer. The code here knows
        # publishing platforms and URL shapes and nothing else -- the
        # docstring names sites only as examples, so it is excluded.
        import inspect

        source = inspect.getsource(self.acq)
        code = "\n".join(
            line for line in source.split('"""', 2)[-1].splitlines()
            if not line.lstrip().startswith("#")
        ).casefold()

        for site in ("diningcode", "naver", "coupang", "danawa", "booking"):
            self.assertNotIn(site, code)

    def test_each_market_reaches_for_its_own_surfaces(self):
        from brain.user_locale import UserLocale

        korea = self.acq.surface_names(
            UserLocale(country="KR"), "restaurant",
        )
        america = self.acq.surface_names(
            UserLocale(country="US"), "restaurant",
        )

        self.assertTrue(korea and america)
        self.assertNotEqual(korea, america)

    def test_an_unserved_market_reaches_for_no_surface(self):
        from brain.user_locale import UserLocale

        # A wrong market's directory is worse than no directory.
        self.assertEqual(
            self.acq.surface_names(
                UserLocale(country="BR"), "restaurant",
            ),
            (),
        )

    def test_an_unknown_category_reaches_for_no_surface(self):
        from brain.user_locale import UserLocale

        self.assertEqual(
            self.acq.surface_names(UserLocale(country="KR"), "spaceship"),
            (),
        )

    def test_a_surface_never_ranks_above_a_candidate(self):
        problem = _dinner()
        results = [
            {
                "title": "Naver Map", "summary": "",
                "url": "https://map.naver.com/p/search/juk",
            },
            REAL_PLACE,
        ]

        fits = cf.evaluate(results, problem, surface_hosts=self.hosts)

        self.assertEqual(fits[0].name, REAL_PLACE["title"])
        self.assertTrue(fits[1].is_surface)

    def test_a_surface_outranks_an_article(self):
        problem = _dinner()
        results = [
            RECIPES,
            {
                "title": "Naver Map", "summary": "",
                "url": "https://map.naver.com/p/search/juk",
            },
        ]

        fits = cf.evaluate(results, problem, surface_hosts=self.hosts)

        self.assertTrue(fits[0].is_surface)

    def test_a_surface_is_never_viable_as_a_recommendation(self):
        problem = _dinner()
        fits = cf.evaluate(
            [{
                "title": "Naver Map", "summary": "",
                "url": "https://map.naver.com/p/search/juk",
            }],
            problem, surface_hosts=self.hosts,
        )

        self.assertEqual(fits[0].verdict, "SOURCE")
        self.assertFalse(fits[0].viable)
        self.assertFalse(cf.confident(fits, problem))

    def test_surfaces_are_reported_separately(self):
        problem = _dinner()
        fits = cf.evaluate(
            [{
                "title": "Naver Map", "summary": "",
                "url": "https://map.naver.com/p/search/juk",
            }, REAL_PLACE],
            problem, surface_hosts=self.hosts,
        )

        block = cf.log_block(fits)

        self.assertIn("Surfaces:", block)
        self.assertIn("never recommended", block)
