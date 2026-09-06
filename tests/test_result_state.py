"""What a search found, and pointing at one of them a turn later.

The Phase 4F.5 acceptance suite. A result set used to be eight strings: the
fit layer worked out a URL, a ranking and a reason, and ``record_candidates``
kept the name and dropped the rest in the same breath. So "open the second
one" had a position to count to and nothing to open, and the entity guard
could name a result but never link to it.

The property that matters most here is **identity**. Phase 4E is a long
record of a thing's visible label and the thing itself coming apart, and
every bug that followed was some version of trusting the label. A candidate
is identified by its URL where it has one; the name is the fallback, not the
identity.

The negatives carry the weight, as they do everywhere in this project:

    "open the second one"   -> opens the second result's own address
    "open the fifth one"    -> opens nothing; only three are in hand
    "open the second tab"   -> the browser's tabs, not her shortlist
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain import candidate_fit as cf
from brain import result_state as rs
from brain.task_session import TaskSessionStore
from tests.turn_harness import build_engine, machine_actions


MONITORS = (
    rs.Candidate(name="Dell S2722DGM Curved Gaming Monitor",
                 url="https://dell.com/product/s2722dgm", why="fits gaming"),
    rs.Candidate(name="LG UltraGear 27GP850-B",
                 url="https://bestbuy.com/site/lg/6467884.p", why="fits gaming"),
    rs.Candidate(name="Samsung Odyssey G5",
                 url="https://samsung.com/odyssey-g5", why="fits gaming"),
)


class IdentityIsNotTheLabelTests(unittest.TestCase):
    """A result is its address. The title is what it happens to say today."""

    def test_the_same_page_keeps_its_identity_under_a_new_title(self):
        first = rs.Candidate(name="LG UltraGear 27GP850-B",
                             url="https://bestbuy.com/site/lg/6467884.p")
        renamed = rs.Candidate(name="LG UltraGear 27GP850-B | Best Buy",
                               url="https://bestbuy.com/site/lg/6467884.p")

        self.assertEqual(first.id, renamed.id)

    def test_two_different_pages_are_two_different_things(self):
        self.assertNotEqual(MONITORS[0].id, MONITORS[1].id)

    def test_a_result_with_no_address_falls_back_to_its_name(self):
        bare = rs.Candidate(name="Juk Story Myeongdong")

        self.assertTrue(bare.id)
        self.assertFalse(bare.openable)

    def test_an_identity_survives_a_rerank(self):
        # "Anything cheaper?" re-ranks what is in hand. It does not produce
        # different things, and new ids would break every reference the
        # conversation has already made.
        held = rs.ResultSet(items=MONITORS)

        moved = held.reranked([MONITORS[2].id, MONITORS[0].id])

        self.assertEqual(
            {item.id for item in moved}, {item.id for item in held},
        )
        self.assertEqual(moved.at(0).id, MONITORS[2].id)
        self.assertEqual(moved.at(0).rank, 0)


class WhatTheFitLayerKnewIsKeptTests(unittest.TestCase):
    """Everything computed a line earlier used to be thrown away."""

    def _fits(self):
        store = TaskSessionStore()
        problem = store.note_recommendation_turn(
            "find me a good gaming monitor", subject="monitors",
        )
        return cf.evaluate([
            {"title": "LG UltraGear 27GP850-B 27in QHD Gaming Monitor",
             "url": "https://bestbuy.com/site/lg/6467884.p",
             "summary": "165Hz IPS, $349"},
            {"title": "Dell S2722DGM Curved Gaming Monitor",
             "url": "https://dell.com/product/s2722dgm",
             "summary": "165Hz curved, $279"},
        ], problem, shape=cf.PRODUCT)

    def test_the_address_survives(self):
        found = rs.from_fits(cf.viable(self._fits()))

        self.assertTrue(all(item.openable for item in found))

    def test_the_reason_survives(self):
        found = rs.from_fits(cf.viable(self._fits()))

        self.assertTrue(all(item.why for item in found))

    def test_the_verdict_survives(self):
        found = rs.from_fits(cf.viable(self._fits()))

        self.assertTrue(all(item.verdict for item in found))

    def test_the_ranking_survives(self):
        found = rs.from_fits(cf.viable(self._fits()))

        self.assertEqual([item.rank for item in found], [0, 1])


class PointingAtOneOfThemTests(unittest.TestCase):
    """Positions count against what is in hand, and never past the end."""

    def _held(self):
        return rs.ResultSet(items=MONITORS)

    def test_a_position_resolves(self):
        self.assertEqual(self._held().at(1).name, "LG UltraGear 27GP850-B")

    def test_the_last_one_is_the_last_one(self):
        self.assertEqual(self._held().at(-1).name, "Samsung Odyssey G5")

    def test_a_position_past_the_end_resolves_to_nothing(self):
        # Deliberately not clamped. "The fifth one" against three results
        # means the person is talking about something she is not holding,
        # and quietly handing back the third is how a wrong thing opens.
        self.assertIsNone(self._held().at(4))

    def test_an_empty_set_resolves_to_nothing(self):
        self.assertIsNone(rs.ResultSet().at(0))

    def test_a_candidate_can_be_found_by_identity(self):
        held = self._held()

        self.assertEqual(held.by_id(MONITORS[1].id).name, MONITORS[1].name)


class TheStoreKeepsRecordsNotLabelsTests(unittest.TestCase):
    """And every caller that treated them as strings still works."""

    def _stored(self, items):
        store = TaskSessionStore()
        store.note_recommendation_turn("find me a monitor", subject="monitors")
        store.record_candidates(items, evidence=("shortlist",))
        return store

    def test_whole_candidates_are_kept(self):
        store = self._stored(MONITORS)

        held = store.results()

        self.assertEqual(len(held), 3)
        self.assertTrue(held.at(0).openable)

    def test_bare_names_still_work(self):
        store = self._stored(["Juk Story", "Han River BBQ"])

        held = store.results()

        self.assertEqual(held.names(), ("Juk Story", "Han River BBQ"))
        self.assertFalse(held.at(0).openable)

    def test_a_candidate_reads_as_its_name(self):
        # The compatibility that let this land without touching the logs,
        # the reference resolver or the grounding guards.
        store = self._stored(MONITORS)
        problem = store.active_recommendation()

        self.assertEqual(
            [str(item) for item in problem.candidates][:1],
            ["Dell S2722DGM Curved Gaming Monitor"],
        )

    def test_a_new_search_replaces_the_set(self):
        store = self._stored(MONITORS)

        store.record_candidates(
            [rs.Candidate(name="Keychron K2", url="https://keychron.com/k2")],
            evidence=("new shortlist",),
        )

        self.assertEqual(store.results().names(), ("Keychron K2",))

    def test_a_result_set_can_go_stale(self):
        held = rs.ResultSet(items=MONITORS, created_at=0.0)

        self.assertTrue(held.expired(now=rs.DEFAULT_TTL_SECONDS + 1))
        self.assertFalse(held.expired(now=1.0))


class OpenTheSecondOneTests(unittest.TestCase):
    """The whole point, end to end: the position becomes an address."""

    def _engine(self):
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            engine = build_engine(routes={})
            engine._web_search_enabled = True
            engine.task_sessions.note_recommendation_turn(
                "find me a good gaming monitor", subject="monitors",
            )
            engine.task_sessions.record_candidates(
                MONITORS, evidence=("shortlist",),
            )
            engine.client.reply = "Opening it."
        return engine, quiet

    def _opened(self, said):
        engine, quiet = self._engine()
        try:
            with contextlib.redirect_stdout(quiet):
                engine.chat(said)
        except Exception:
            # Routed onward to the browser layers, which the harness has no
            # real window for. What matters is that no candidate opened.
            pass
        return [
            target for kind, target in machine_actions(engine)
            if kind.startswith("open_url")
        ]

    def test_the_second_one_opens_the_second_address(self):
        self.assertEqual(
            self._opened("open the second one"),
            ["https://bestbuy.com/site/lg/6467884.p"],
        )

    def test_the_first_one_opens_the_first(self):
        self.assertEqual(
            self._opened("pull up the first one"),
            ["https://dell.com/product/s2722dgm"],
        )

    def test_the_last_one_opens_the_last(self):
        self.assertEqual(
            self._opened("open the last one"),
            ["https://samsung.com/odyssey-g5"],
        )

    def test_a_position_that_is_not_there_opens_nothing(self):
        self.assertEqual(self._opened("open the fifth one"), [])

    def test_a_browser_tab_is_not_one_of_her_results(self):
        # The counting vocabulary is identical for both, so the noun beside
        # it is the only thing that tells them apart. Without this, "open
        # the second tab" opened a monitor.
        self.assertEqual(self._opened("open the second tab"), [])

    def test_a_follow_up_that_asks_rather_than_acts_opens_nothing(self):
        self.assertEqual(self._opened("which one would you choose?"), [])

    def test_a_named_address_is_still_a_named_address(self):
        # A stale shortlist must never capture a turn that names where to go.
        self.assertEqual(
            self._opened("open naver.com"), ["naver.com"],
        )


class NothingInHandResolvesNothingTests(unittest.TestCase):
    """A position named against no result set is a question, not a guess."""

    def test_an_ordinal_with_no_results_opens_nothing(self):
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            engine = build_engine(routes={})
            engine._web_search_enabled = True
            engine.client.reply = "I'm not sure which one you mean."
            try:
                engine.chat("open the second one")
            except Exception:
                pass

        self.assertEqual(
            [t for k, t in machine_actions(engine) if k.startswith("open_url")],
            [],
        )


if __name__ == "__main__":
    unittest.main()
