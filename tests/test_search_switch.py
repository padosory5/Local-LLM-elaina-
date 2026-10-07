"""Search stays inside the test, and stays off when it is switched off.

Found by a test about something else. tests/test_turn_move.py ran two
ordinary turns through the harness, and the second one's draft prompt came
back with scied.ucar.edu, britannica.com and science.nasa.gov in it. The
harness switches the ``search`` section off, and the same prompt said so --
"web search: unavailable: web search is disabled in configuration" -- a few
lines away from results she had just fetched.

Two things were wrong, and each has its half of this file:

* Nothing that searched read the switch (brain/chat_engine.py). It reached
  the list of abilities she is shown and stopped there, so
  ``search.enabled: false`` changed what she was told and not what she did.
* The harness had tied every surface but this one (tests/turn_harness.py).

DuckDuckGo is replaced throughout by a client that only writes down what it
was asked, and answers the way the live one does. So a failure here reads
"the backend was asked for X" -- and nothing in this file can reach it.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from brain import capability_contract
from tests.turn_harness import RecordingSearch, build_engine, reset


FIRST = "What is the greenhouse effect?"
SECOND = "Wait, why can't the heat get out if sunlight comes in?"

# As the test that found it had them: nothing about freshness, which the
# router reads as "unknown", which the capability layer reads as a lookup.
ROUTE = {"intent": "knowledge_question", "confidence": 1.0, "speech_act": "information_request",
         "answer_shape": "explain", "topic": "greenhouse effect"}
ROUTES = {
    "greenhouse effect?": {**ROUTE, "normalized_request": "What is the greenhouse effect?"},
    "Wait, why": {**ROUTE, "is_follow_up": True, "normalized_request": "Why can't the heat get out?"},
}

# Stable, so the capability layer answers directly and only the handler's
# own search is left to be switched off.
SETTLED = {"confidence": 1.0, "information_freshness": "stable", "requires_external_evidence": False}
DISPUTE = "Was it Bainbridge Island?"
DISPUTE_ROUTE = {**SETTLED, "intent": "fact_check", "speech_act": "information_request", "topic": "island",
                 "normalized_request": "Was it Bainbridge Island?", "search_query": "island by ferry from Seattle"}
# Asked for, because a bare "No, I meant Portland, Maine." is a remark and
# the router offers to look it up instead of looking.
RENAMED = "Can you look up Portland, Maine instead?"
RENAMED_ROUTE = {**SETTLED, "intent": "entity_correction", "speech_act": "correction", "topic": "Portland",
                 "entity": "Portland, Maine", "normalized_request": "Portland, Maine"}

SWITCHED_OFF = capability_contract.failed("web_search", "disabled").spoken("en")
EVIDENCE = "CURRENT RETRIEVED EVIDENCE"


class _Offline(unittest.TestCase):
    """Whole turns, with a recorder standing where DuckDuckGo stands."""

    def setUp(self):
        asked = self.asked = []

        class Backend:
            def __init__(self, *args, **kwargs):
                pass

            def text(self, query, max_results=3, **kwargs):
                asked.append(str(query))
                return [{"title": "A live result", "href": "https://live.invalid/", "body": "From the network."}]

        patched = mock.patch("tools.web_search.DDGS", Backend)
        patched.start()
        self.addCleanup(patched.stop)

    def engine(self, routes=None, *, search=False):
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine(ROUTES if routes is None else routes)
        self.addCleanup(engine.close)
        if search:
            # The ability, as tests/test_grounded_commitment.py turns it on.
            engine._web_search_enabled = True
        return engine

    def say(self, engine, said):
        with contextlib.redirect_stdout(io.StringIO()):
            return engine.chat(said)

    @staticmethod
    def shown(engine):
        """Everything the model was handed, in every call."""
        return "\n".join(str(message.get("content", "")) for prompt in engine.client.prompts
                         for message in prompt if isinstance(message, dict))


class TheTurnsThatFoundItTests(_Offline):

    def test_two_ordinary_turns_reach_no_network(self):
        engine = self.engine()
        self.say(engine, FIRST)
        self.say(engine, SECOND)
        self.assertEqual(self.asked, [], "the search backend was queried")
        self.assertEqual(engine.web_search_tool.queries, [])
        shown = self.shown(engine)
        self.assertNotIn(EVIDENCE, shown)
        self.assertNotIn("live.invalid", shown)
        # And these were the turns it happened on: told search is off, and
        # sent to search anyway.
        self.assertIn("web search is disabled in configuration", shown)
        self.assertIn(SWITCHED_OFF, shown)

    def test_the_harness_leaves_search_off_and_holds_no_real_tool(self):
        engine = self.engine()
        self.assertFalse(engine._web_search_enabled)
        self.assertIsInstance(engine.web_search_tool, RecordingSearch)


class SwitchedOffTests(_Offline):
    """``search.enabled: false`` is honoured by what searches."""

    def test_nothing_is_dispatched_and_nothing_says_she_is_looking(self):
        engine = self.engine()
        reply = self.say(engine, FIRST)
        self.assertEqual(engine.audio.spoken, [reply])
        self.assertEqual(engine.action_ledger.action, "")
        # Not a failure of the search either: it must not count towards
        # giving up on it and reaching for the browser instead.
        self.assertFalse(engine._capability_failures)

    def test_a_dispute_is_not_looked_up(self):
        engine = self.engine({"bainbridge": DISPUTE_ROUTE})
        self.say(engine, DISPUTE)
        self.assertEqual((self.asked, engine.web_search_tool.queries), ([], []))
        self.assertIn(SWITCHED_OFF, self.shown(engine))

    def test_a_corrected_name_is_not_looked_up(self):
        engine = self.engine({"portland": RENAMED_ROUTE})
        self.say(engine, RENAMED)
        self.assertEqual((self.asked, engine.web_search_tool.queries), ([], []))
        self.assertIn(SWITCHED_OFF, self.shown(engine))

    def test_both_ways_she_searches_refuse(self):
        engine = self.engine()
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                engine.search_web("the greenhouse effect")
            with self.assertRaises(RuntimeError):
                engine.research_agent.research_structured(search_query="the greenhouse effect")
        self.assertEqual((self.asked, engine.web_search_tool.queries), ([], []))


class SwitchedOnTests(_Offline):
    """Turned back on by a test, she searches -- and the web is still tied."""

    def test_the_query_is_recorded_and_nothing_leaves(self):
        engine = self.engine(search=True)
        self.say(engine, FIRST)
        self.assertEqual(engine.web_search_tool.queries[0], FIRST)
        self.assertEqual(self.asked, [])
        shown = self.shown(engine)
        self.assertNotIn(EVIDENCE, shown)
        self.assertNotIn(SWITCHED_OFF, shown)

    def test_a_dispute_and_a_corrected_name_are_looked_up(self):
        engine = self.engine({"bainbridge": DISPUTE_ROUTE, "portland": RENAMED_ROUTE}, search=True)
        self.say(engine, DISPUTE)
        self.say(engine, RENAMED)
        self.assertIn(DISPUTE_ROUTE["search_query"], engine.web_search_tool.queries)
        self.assertTrue([query for query in engine.web_search_tool.queries if "Portland, Maine" in query])
        self.assertEqual(self.asked, [])

    def test_a_test_says_what_is_out_there(self):
        engine = self.engine(search=True)
        engine.web_search_tool.results = [{
            "title": "The greenhouse effect", "url": "https://example.test/greenhouse",
            "summary": "Greenhouse gases absorb heat the ground gives off.",
        }]
        self.say(engine, FIRST)
        shown = self.shown(engine)
        self.assertIn(EVIDENCE, shown)
        self.assertIn("https://example.test/greenhouse", shown)
        self.assertEqual(self.asked, [])

    def test_structured_acquisition_ends_at_the_same_place(self):
        engine = self.engine(search=True)
        found = {"title": "Keychron Q1", "url": "https://example.test/q1", "summary": "A keyboard."}
        engine.web_search_tool.results = [found]
        results = engine.research_agent.research_structured(
            search_query="mechanical keyboards", query_is_resolved=True)
        self.assertEqual(results, (found,))
        self.assertEqual(engine.web_search_tool.queries, ["mechanical keyboards"])
        self.assertEqual(self.asked, [])

    def test_the_next_case_starts_with_nothing_searched(self):
        engine = self.engine(search=True)
        engine.web_search_tool.results = [{"title": "T", "url": "https://example.test/t", "summary": "S"}]
        with contextlib.redirect_stdout(io.StringIO()):
            engine.search_web("the greenhouse effect")
            reset(engine)
        self.assertEqual((engine.web_search_tool.queries, engine.web_search_tool.results), ([], []))
        self.assertEqual(engine._search_cache, {})


class EngineOrderTests(unittest.TestCase):
    """The engines are tried in one order, not a new random one each search.

    Measured (evals/search_order.py, docs/TURN_MOVE_AB.md): which engine a
    search drew decided what a Korean lookup found. Yahoo's results held the
    answer and Yandex's did not, and the library's own order is a shuffle.
    """

    FOUND = [{"title": "T", "href": "https://example.test/t", "body": "B"}]

    def _library(self, answers):
        """The search library, standing still: each call takes the next of
        ``answers`` -- results, or an exception to raise. Returns
        (the class to patch in, the calls made: (which instance, backend))."""
        from ddgs.exceptions import DDGSException

        calls, pending, made = [], list(answers), []

        class Backend:
            def __init__(self, *args, **kwargs):
                made.append(self)

            def text(self, query, **kwargs):
                calls.append((made.index(self), kwargs.get("backend"), kwargs.get("max_results")))
                answer = pending.pop(0) if pending else DDGSException("No results found.")
                if isinstance(answer, Exception):
                    raise answer
                return answer

        return Backend, calls

    def test_yahoo_is_asked_first_and_its_answer_is_the_answer(self):
        import tools.web_search as web_search

        backend, calls = self._library([self.FOUND, self.FOUND])
        with mock.patch("tools.web_search.DDGS", backend):
            tool = web_search.WebSearchTool()
            found = tool.search_web_structured("요즘 제일 최신 아이폰 모델이 뭐야", 5)
            prose = tool.search_web("latest iPhone model 2026", 5)
        self.assertEqual(found, [{"title": "T", "url": "https://example.test/t", "summary": "B"}])
        self.assertIn("https://example.test/t", prose)
        # One request each, and nothing after it.
        self.assertEqual(calls, [(0, "yahoo", 5), (1, "yahoo", 5)])

    def test_an_empty_answer_is_asked_again_on_a_new_connection(self):
        # Measured: the same connection asked again stayed empty, 0 of 12.
        # The library poses as a random browser per connection, and cannot
        # read the page Yahoo sends a phone.
        from ddgs.exceptions import DDGSException

        import tools.web_search as web_search

        backend, calls = self._library([DDGSException("No results found."), self.FOUND])
        with mock.patch("tools.web_search.DDGS", backend):
            found = web_search.WebSearchTool().search_web_structured("latest iPhone model 2026", 5)
        self.assertEqual(len(found), 1)
        self.assertEqual([name for _instance, name, _n in calls], ["yahoo", "yahoo"])
        instances = [instance for instance, _name, _n in calls]
        self.assertEqual(len(set(instances)), len(instances), "a connection was reused")

    def test_the_retry_is_bounded_and_then_the_whole_order_is_tried(self):
        from ddgs.exceptions import DDGSException

        import tools.web_search as web_search

        empty = [DDGSException("No results found.")] * web_search.YAHOO_TRIES
        backend, calls = self._library([*empty, self.FOUND])
        with mock.patch("tools.web_search.DDGS", backend):
            found = web_search.WebSearchTool().search_web_structured("latest iPhone model 2026", 5)
        self.assertEqual(len(found), 1)
        self.assertEqual([name for _instance, name, _n in calls],
                         ["yahoo"] * web_search.YAHOO_TRIES + [web_search.ENGINE_ORDER])
        instances = [instance for instance, _name, _n in calls]
        self.assertEqual(len(set(instances)), len(instances), "a connection was reused")
        self.assertLessEqual(web_search.YAHOO_TRIES, 3)

    def test_when_nothing_answers_it_stops_and_says_so(self):
        # Never a loop: one request more than the tries, and the failure
        # reaches the caller as it always did.
        from ddgs.exceptions import DDGSException

        import tools.web_search as web_search

        backend, calls = self._library([])
        with mock.patch("tools.web_search.DDGS", backend):
            with self.assertRaises(DDGSException):
                web_search.WebSearchTool().search_web_structured("latest iPhone model 2026", 5)
        self.assertEqual(len(calls), web_search.YAHOO_TRIES + 1)
        self.assertEqual(calls[-1][1], web_search.ENGINE_ORDER)

    def test_a_timeout_is_not_asked_again(self):
        # A phone's page comes back at once. A timeout is Yahoo not answering,
        # and each further try would wait it out again.
        from ddgs.exceptions import TimeoutException

        import tools.web_search as web_search

        backend, calls = self._library([TimeoutException("timed out"), self.FOUND])
        with mock.patch("tools.web_search.DDGS", backend):
            found = web_search.WebSearchTool().search_web_structured("latest iPhone model 2026", 5)
        self.assertEqual(len(found), 1)
        self.assertEqual([name for _instance, name, _n in calls],
                         ["yahoo", web_search._ORDER_WITHOUT_YAHOO])

    def test_after_a_timeout_the_engines_that_follow_do_not_include_yahoo(self):
        # Measured with Yahoo made unreachable: the engine order asked it
        # again, and a search waited out the timeout twice where it had
        # waited once before Yahoo was asked first.
        import tools.web_search as web_search

        order = web_search.ENGINE_ORDER.split(",")
        without = web_search._ORDER_WITHOUT_YAHOO.split(",")
        self.assertNotIn("yahoo", without)
        # Every other engine, in the same order.
        self.assertEqual(without, [engine for engine in order if engine != "yahoo"])
        self.assertEqual(len(without), len(order) - 1)

    def test_an_empty_answer_that_used_up_the_timeout_is_a_timeout(self):
        # Measured with Yahoo made unreachable: the library's own wait ran
        # out first, and it reported "No results found." like any empty
        # answer. Read by the exception alone, that was asked again three
        # times over.
        from types import SimpleNamespace

        from ddgs.exceptions import DDGSException

        import tools.web_search as web_search

        for took, yahoo_calls, then in (
            # Not answering: asked once, and not again in the order either.
            (web_search._TIMEOUT + 0.2, 1, web_search._ORDER_WITHOUT_YAHOO),
            # A phone's page: asked on new connections, then the whole order.
            (0.2, web_search.YAHOO_TRIES, web_search.ENGINE_ORDER),
        ):
            with self.subTest(took=took):
                empty = [DDGSException("No results found.")] * yahoo_calls
                backend, calls = self._library([*empty, self.FOUND])
                # Each Yahoo-only call reads the clock twice: before and after.
                ticks = iter(sum(([n * 100.0, n * 100.0 + took] for n in range(10)), []))
                clock = SimpleNamespace(monotonic=lambda: next(ticks))
                with mock.patch("tools.web_search.DDGS", backend), \
                        mock.patch("tools.web_search.time", clock):
                    found = web_search.WebSearchTool().search_web_structured(
                        "latest iPhone model 2026", 5)
                self.assertEqual(len(found), 1)
                self.assertEqual([name for _instance, name, _n in calls],
                                 ["yahoo"] * yahoo_calls + [then])

    def test_the_order_is_the_librarys_engines_with_yahoo_ahead_of_yandex(self):
        from ddgs.engines import ENGINES

        import tools.web_search as web_search

        order = web_search.ENGINE_ORDER.split(",")
        # Every engine the library has, once: none dropped, so one that starts
        # answering again is still reached.
        self.assertEqual(sorted(order), sorted(ENGINES["text"]))
        self.assertEqual(order[:2], ["wikipedia", "grokipedia"])
        self.assertLess(order.index("yahoo"), order.index("yandex"))
        # Behind the engine that answers: it took ten seconds to return nothing.
        self.assertEqual(order[-2:], ["yandex", "duckduckgo"])


if __name__ == "__main__":
    unittest.main()
