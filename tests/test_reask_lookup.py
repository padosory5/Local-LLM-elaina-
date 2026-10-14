"""A question asked again is looked up the way it was the first time.

Found by the turn-move regression (docs/TURN_MOVE_AB.md, 2026-10-03). "What's
the newest iPhone model right now?" was searched twice over -- the router's
query and a dated official-source one -- and answered from Apple's own
announcement. Asked again, the router filed the turn as conversation that
needs live information and wrote no query. So the search was built from the
new sentence, once and unverified, it missed the five-minute cache the first
search had filled, and it came back with blog posts about older models. She
took back a supported answer in 5 of 8 runs.

Two things already keep a follow-up on what she found, and a re-ask gets past
both: recall wants a word that points back, and the cache wants the same
query. ``ChatEngine._lookup_as_first_asked`` closes that: the same question
is the same lookup.

The web here is a table from query to pages, so each test says what is out
there for which search. DuckDuckGo itself is replaced by a tripwire.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from brain import turn_move as tm
from core import turn_trace
from tests.test_turn_move import AXIS0, FakeEmbedder, _at
from tests.turn_harness import RecordingSearch, build_engine, drafting_parts, drafting_prompts

FIRST = "What's the newest iPhone model right now?"
AGAIN = "Wait, so which iPhone model is the newest one right now?"
PRICE = "How much does it cost?"
KOREA = "Is it out in Korea yet?"

FIRST_KO = "요즘 제일 최신 아이폰 모델이 뭐야?"
AGAIN_KO = "아니 그러니까 지금 제일 최신 아이폰 모델이 뭐냐고?"

# As the router wrote them in the recorded runs.
LOOKED_UP = {"intent": "web_search", "confidence": 1.0, "speech_act": "information_request",
             "topic": "iPhone", "information_freshness": "live", "requires_external_evidence": True,
             "verification_required": True}
# The re-ask: conversation, live, and no query.
FILED_AS_TALK = {"intent": "conversation", "confidence": 1.0, "speech_act": "information_request",
                 "topic": "iPhone", "information_freshness": "live", "requires_external_evidence": False,
                 "is_follow_up": True}

ROUTES = {
    "What's the newest": {**LOOKED_UP, "normalized_request": "What is the newest iPhone model right now?",
                          "search_query": "newest iPhone model 2026"},
    "Wait, so which": {**FILED_AS_TALK,
                       "normalized_request": "Which iPhone model is the newest one right now?"},
    "How much does it": {**LOOKED_UP, "is_follow_up": True, "verification_required": False,
                         "normalized_request": "How much does the iPhone 18 Pro cost?",
                         "search_query": "iPhone 18 Pro price"},
    "Is it out in Korea": {**FILED_AS_TALK, "normalized_request": "Is the iPhone 18 Pro out in Korea yet?"},
    "요즘 제일": {**LOOKED_UP, "normalized_request": "What is the latest iPhone model?",
              "search_query": "latest iPhone model 2026"},
    "아니 그러니까": {**FILED_AS_TALK,
                "normalized_request": "What is the latest iPhone model currently available?"},
}

APPLE = {"title": "Apple debuts iPhone 18 Pro and iPhone 18 Pro Max - Apple",
         "url": "https://apple.example.test/newsroom/iphone-18-pro",
         "summary": "Apple today announced iPhone 18 Pro and iPhone 18 Pro Max."}
BLOG = {"title": "iPhone 16 vs 15: worth the upgrade?",
        "url": "https://blog.example.test/iphone-16",
        "summary": "The iPhone 16 is the newest iPhone you can buy."}
PRICES = {"title": "iPhone 18 Pro pricing", "url": "https://store.example.test/iphone-18-pro",
          "summary": "iPhone 18 Pro starts at $1,099."}

EVIDENCE = "CURRENT RETRIEVED EVIDENCE"


class Web(RecordingSearch):
    """What is out there, by search: the first key found in the query decides."""

    def __init__(self, pages: dict[str, list[dict]], otherwise: list[dict]):
        super().__init__()
        self.pages, self.otherwise = pages, otherwise

    def search_web_structured(self, query: str, max_results: int = 3) -> list[dict[str, str]]:
        query = str(query).strip()
        if not query:
            return []
        self.queries.append(query)
        for key, pages in self.pages.items():
            if key.casefold() in query.casefold():
                return [dict(page) for page in pages]
        return [dict(page) for page in self.otherwise]


class _Conversation(unittest.TestCase):

    def setUp(self):
        class Tripwire:
            def __init__(self, *args, **kwargs):
                pass

            def text(self, query, max_results=3, **kwargs):
                raise AssertionError(f"the real search backend was asked for {query!r}")

        patched = mock.patch("tools.web_search.DDGS", Tripwire)
        patched.start()
        self.addCleanup(patched.stop)

    def engine(self, vectors, *, pages=None, otherwise=None):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine(ROUTES)
        self.addCleanup(engine.close)
        self.assertEqual(engine._turn_move_mode, tm.OFF)        # the fix is not behind the flag
        engine._web_search_enabled = True
        engine.web_search_tool = Web(
            {"iPhone model 2026": [APPLE], "요즘 제일 최신 아이폰": [APPLE], "iPhone 18 Pro price": [PRICES]}
            if pages is None else pages,
            [BLOG] if otherwise is None else otherwise,
        )
        engine._turn_moves = tm.Record(embed=FakeEmbedder(vectors))
        engine.client.reply = "The newest is the iPhone 18 Pro."
        return engine

    def say(self, engine, said):
        """One turn: (what reached the web, what was searched for, the drafting prompt, the trace)."""
        before, prompts = len(engine.web_search_tool.queries), len(engine.client.prompts)
        with contextlib.redirect_stdout(io.StringIO()):
            engine.chat(said)
        record = turn_trace.last().as_record()
        # The turn's rules, evidence included, wherever the prompt puts them.
        drafts = [drafting_parts(prompt)[1] for prompt in drafting_prompts(engine.client.prompts[prompts:])]
        self.assertTrue(drafts, said)
        return (engine.web_search_tool.queries[before:],
                list((record.get("evidence") or {}).get("search_queries") or ()),
                drafts[0], record["context"])


class AskedAgainTests(_Conversation):

    def test_an_english_question_asked_again_is_the_same_lookup(self):
        engine = self.engine({FIRST: AXIS0, AGAIN: _at(0.95)})
        reached, searched, prompt, _context = self.say(engine, FIRST)
        self.assertEqual(reached, searched)
        self.assertEqual(searched[0], "newest iPhone model 2026")
        self.assertTrue(searched[1].startswith("official source newest iPhone model 2026 as of "))
        self.assertIn(APPLE["title"], prompt)

        reached, searched_again, prompt, context = self.say(engine, AGAIN)
        self.assertEqual(searched_again, searched)
        # Out of the cache: the evidence the first answer was built on.
        self.assertEqual(reached, [])
        self.assertIn(APPLE["title"], prompt)
        self.assertEqual(context["lookup_as_first_asked"]["search_query"], "newest iPhone model 2026")

    def test_a_korean_question_asked_again_is_the_same_lookup(self):
        engine = self.engine({FIRST_KO: AXIS0, AGAIN_KO: _at(0.95)})
        _reached, searched, prompt, _context = self.say(engine, FIRST_KO)
        # Their words, and the router's query in the other language, checked.
        self.assertEqual(searched[0], FIRST_KO.rstrip("?"))
        self.assertTrue(searched[1].startswith("official source latest iPhone model 2026 as of "))
        self.assertIn(APPLE["title"], prompt)

        reached, searched_again, prompt, context = self.say(engine, AGAIN_KO)
        self.assertEqual(searched_again, searched)
        self.assertEqual(reached, [])
        self.assertIn(APPLE["title"], prompt)
        self.assertNotIn(AGAIN_KO.rstrip("?"), " ".join(searched_again))
        self.assertIn("lookup_as_first_asked", context)

    def test_weaker_pages_for_the_new_sentence_are_never_fetched(self):
        # The first answer had Apple's announcement. A search on the re-ask's
        # own sentence finds a blog that says otherwise.
        engine = self.engine({FIRST: AXIS0, AGAIN: _at(0.95)})
        self.say(engine, FIRST)
        reached, searched, prompt, context = self.say(engine, AGAIN)
        self.assertFalse([query for query in reached + searched if "which iphone" in query.casefold()])
        self.assertIn(APPLE["title"], prompt)
        self.assertNotIn(BLOG["title"], prompt)
        self.assertNotIn("blog.example.test", prompt)
        # What it would have searched is on the record.
        self.assertIn("Which iPhone model", context["lookup_as_first_asked"]["instead_of"])

    def test_after_the_cache_it_is_searched_again_as_carefully_as_the_first_time(self):
        engine = self.engine({FIRST: AXIS0, AGAIN: _at(0.95)})
        first_reached, _searched, _prompt, _context = self.say(engine, FIRST)
        engine._search_cache.clear()
        reached, _searched, prompt, _context = self.say(engine, AGAIN)
        self.assertEqual(reached, first_reached)
        self.assertEqual(len(reached), 2)
        self.assertIn(APPLE["title"], prompt)

    def test_a_third_asking_is_the_same_lookup_too(self):
        third = "No, I mean which iPhone is the newest right now?"
        routes = {**ROUTES, "No, I mean which": ROUTES["Wait, so which"]}
        with mock.patch.dict(ROUTES, routes):
            engine = self.engine({FIRST: AXIS0, AGAIN: _at(0.95), third: _at(0.9)})
            _reached, searched, _prompt, _context = self.say(engine, FIRST)
            self.say(engine, AGAIN)
            reached, searched_third, prompt, _context = self.say(engine, third)
        self.assertEqual((searched_third, reached), (searched, []))
        self.assertIn(APPLE["title"], prompt)

    def test_the_writer_of_a_re_ask_under_deep_gets_the_same_evidence(self):
        engine = self.engine({FIRST: AXIS0, AGAIN: _at(0.95)})
        engine._turn_move_mode, engine._turn_move_model = tm.DEEP, "the-27b"
        self.say(engine, FIRST)
        _reached, _searched, prompt, context = self.say(engine, AGAIN)
        self.assertEqual(context["turn_move_applied"]["written_by"], "the-27b")
        self.assertIn(APPLE["title"], prompt)
        self.assertNotIn(BLOG["title"], prompt)


class SearchedAsBeforeTests(_Conversation):
    """What the rule leaves alone."""

    def test_a_re_ask_the_router_wrote_a_query_for_is_searched_as_the_router_said(self):
        # Results that agree with the first answer, under a query of its own.
        routes = {**ROUTES, "Wait, so which": {
            **LOOKED_UP, "is_follow_up": True,
            "normalized_request": "Which iPhone model is the newest one right now?",
            "search_query": "which iPhone model 2026 is newest"}}
        with mock.patch.dict(ROUTES, routes):
            engine = self.engine({FIRST: AXIS0, AGAIN: _at(0.95)})
            self.say(engine, FIRST)
            reached, searched, prompt, context = self.say(engine, AGAIN)
        self.assertEqual(searched[0], "which iPhone model 2026 is newest")
        self.assertEqual(reached, searched)
        self.assertIn(APPLE["title"], prompt)
        self.assertNotIn("lookup_as_first_asked", context)

    def test_a_follow_up_search_with_its_own_query_is_untouched(self):
        engine = self.engine({FIRST: AXIS0, PRICE: _at(0.5)})
        self.say(engine, FIRST)
        reached, searched, prompt, context = self.say(engine, PRICE)
        self.assertEqual(searched[0], "iPhone 18 Pro price")
        self.assertEqual(reached, searched)
        self.assertIn(PRICES["title"], prompt)
        self.assertNotIn("lookup_as_first_asked", context)

    def test_a_different_question_with_no_query_is_built_from_the_turn_as_before(self):
        engine = self.engine({FIRST: AXIS0, KOREA: _at(0.6)})
        _reached, first, _prompt, _context = self.say(engine, FIRST)
        reached, searched, _prompt, context = self.say(engine, KOREA)
        self.assertEqual(reached, searched)
        self.assertEqual(len(searched), 1)
        self.assertIn("Korea", searched[0])
        self.assertNotIn(searched[0], first)
        self.assertNotIn("lookup_as_first_asked", context)

    def test_a_close_question_is_not_a_re_ask_below_the_threshold(self):
        engine = self.engine({FIRST: AXIS0, AGAIN: _at(0.79)})
        _reached, first, _prompt, _context = self.say(engine, FIRST)
        _reached, searched, _prompt, context = self.say(engine, AGAIN)
        self.assertNotEqual(searched, first)
        self.assertNotIn("lookup_as_first_asked", context)

    def test_after_the_topic_moved_it_is_a_new_question(self):
        routes = {**ROUTES, "Wait, so which": {**ROUTES["Wait, so which"], "topic_shift": True}}
        with mock.patch.dict(ROUTES, routes):
            engine = self.engine({FIRST: AXIS0, AGAIN: _at(0.95)})
            _reached, first, _prompt, _context = self.say(engine, FIRST)
            _reached, searched, _prompt, context = self.say(engine, AGAIN)
        self.assertNotEqual(searched, first)
        self.assertNotIn("lookup_as_first_asked", context)

    def test_without_an_embedder_nothing_changes(self):
        engine = self.engine({})
        engine._turn_moves = tm.Record(embed=None)
        _reached, first, _prompt, _context = self.say(engine, FIRST)
        _reached, searched, _prompt, context = self.say(engine, AGAIN)
        self.assertNotEqual(searched, first)
        self.assertNotIn("lookup_as_first_asked", context)

    def test_a_question_that_was_not_searched_has_no_lookup_to_repeat(self):
        record = tm.Record(embed=FakeEmbedder({"why is the sky blue?": AXIS0, "so why is the sky blue?": AXIS0}))
        record.decide("why is the sky blue?", is_follow_up=False, topic_shift=False, speech_act="",
                      budget="explain")
        record.record("why is the sky blue?", "Air scatters blue light the most.")
        (asked,) = record.asked_earlier("so why is the sky blue?")
        self.assertIsNone(asked.lookup)


class TheRecordTests(unittest.TestCase):
    """brain/turn_move.py: what was asked before, read ahead of the move."""

    def _record(self):
        embed = FakeEmbedder({"q": AXIS0, "q again": _at(0.9), "something else": _at(0.3)})
        record = tm.Record(embed=embed)
        record.decide("q", is_follow_up=False, topic_shift=False, speech_act="", budget="value")
        record.record("q", "a", lookup={"search_query": "q", "verify": True})
        return record, embed

    def test_it_returns_the_exchanges_and_what_they_searched(self):
        record, _embed = self._record()
        (asked,) = record.asked_earlier("q again")
        self.assertEqual((asked.said, asked.lookup), ("q", {"search_query": "q", "verify": True}))
        self.assertEqual(record.asked_earlier("something else"), ())
        self.assertEqual(record.asked_earlier("q again", topic_shift=True), ())

    def test_it_records_nothing_and_the_sentence_is_embedded_once(self):
        record, embed = self._record()
        calls = embed.calls
        record.asked_earlier("q again")
        self.assertEqual(len(record.thread), 1)
        move = record.decide("q again", is_follow_up=True, topic_shift=False, speech_act="", budget="value")
        record.record("q again", "a2")
        self.assertEqual(move.move, tm.DIFFERENTLY)
        self.assertEqual(embed.calls, calls + 1)


if __name__ == "__main__":
    unittest.main()
