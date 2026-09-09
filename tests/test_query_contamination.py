"""A held task may not put its own subject into an unrelated query.

From a real Korean session (``runtime/koreanSession.log``). Four turns
about the Korean War opened a task; the conversation then moved to the
user's visa paperwork and then to their university. Nothing retired the
task, so the query builder kept leading with its subject:

    [Query] source: active_task
            text: 6/25 war I-20
    [Query] source: active_task
            text: 6/25 war Washington University Seattle
    [Tool] Using cached web search for: 6/25 war Washington University Seattle
    [Query] source: active_task
            text: 6/25 war Washington University Bill Gates Seattle

Two layers had already noticed, in the same log, on the same turns:

    [Context] Starting clean: the router says the topic moved
    [Context] The held results are about the old subject; not naming them.

so this is specifically about the query, which asked nobody. The searches
then went into the cache under the contaminated key, so a later, correctly
built query for the same subject would have been answered from them.

The other half of the file is the reason the fix is narrow: the whole
point of building a query from the open task is that a subjectless
follow-up ("pull up some spots") carries three turns of constraints that
the turn's own words do not. Those must keep working.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from brain import context_policy


class TheTurnSubjectDecidesTests(unittest.TestCase):
    """The predicate, in both languages, on both sides of the question."""

    def test_the_session_that_produced_this_reads_as_a_different_subject(self):
        for held, current in (
            ("6/25 war", "SEVIS fee"),
            ("6/25 war", "Washington University in Seattle"),
            ("SEVIS fee", "Washington University in Seattle"),
        ):
            with self.subTest(held=held, current=current):
                self.assertTrue(
                    context_policy.names_a_different_subject(held, current),
                    f"{held!r} and {current!r} are not the same thing",
                )

    def test_korean_subjects_are_compared_in_korean(self):
        # Rule 4. ``subjects_agree``'s four-character floor drops every one
        # of these -- a Korean subject is two to four syllables -- so it
        # answered "agree" for the whole session above.
        same = (("한국전쟁", "육이오 전쟁"), ("전쟁", "전쟁이 언제 끝났어"),
                ("노트북 추천", "노트북"))
        different = (("노트북", "모니터"), ("한국전쟁", "시애틀 날씨"),
                     ("전쟁", "학교"))
        for held, current in same:
            with self.subTest(same=(held, current)):
                self.assertFalse(
                    context_policy.names_a_different_subject(held, current),
                )
        for held, current in different:
            with self.subTest(different=(held, current)):
                self.assertTrue(
                    context_policy.names_a_different_subject(held, current),
                )

    def test_short_english_nouns_are_not_invisible(self):
        # "war", "fee", "job", "car" are three characters. The permissive
        # test above cannot see any of them.
        self.assertTrue(context_policy.names_a_different_subject("war", "fee"))
        self.assertTrue(context_policy.names_a_different_subject("job", "car"))
        # ...and containment stays off at that length, so these stay apart.
        self.assertTrue(
            context_policy.names_a_different_subject("war", "warrant"),
        )

    def test_a_plural_is_the_same_subject(self):
        for held, current in (
            ("Seoul hotels", "hotels in Seoul"),
            ("gaming monitors", "monitor"),
            ("restaurant", "restaurants in Gangnam"),
        ):
            with self.subTest(held=held, current=current):
                self.assertFalse(
                    context_policy.names_a_different_subject(held, current),
                )

    def test_an_unknown_subject_is_not_a_disagreement(self):
        # The safe direction, and the one that keeps a subjectless
        # follow-up inheriting what the task established.
        for held, current in (
            ("Korean BBQ dinner", ""),
            ("", "Seoul hotels"),
            ("Korean BBQ dinner", "the second one"),
            ("hotels", "which one would you choose"),
            ("monitors", "some other options"),
            ("노트북", "그것"),
        ):
            with self.subTest(held=held, current=current):
                self.assertFalse(
                    context_policy.names_a_different_subject(held, current),
                    f"{current!r} names nothing; that is not a disagreement",
                )


class TheQueryDropsAStaleTaskTests(unittest.TestCase):
    """Through ``_resolved_search_query``, on a real engine.

    The task is opened the way the session opened it -- a subject, then a
    turn asking for a lookup -- because a problem with nothing requested
    of it never reaches the branch under test, and a test that never
    reaches the branch passes for the wrong reason.
    """

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine({})
        self.engine.task_sessions.note_recommendation_turn(
            "Tell me about the 6/25 war", subject="6/25 war",
        )
        self.engine.task_sessions.note_recommendation_turn(
            "show me some options", follow_up=True,
        )
        held = self.engine.task_sessions.active_recommendation()
        self.assertEqual(held.subject, "6/25 war")
        self.assertTrue(held.lookup_requested)

    def tearDown(self):
        self.engine.close()

    def _query(self, *, subject: str, request: str, query: str) -> str:
        route = SimpleNamespace(
            search_query=query, normalized_request=request,
            topic=subject, is_follow_up=True,
        )
        return self.engine._resolved_search_query(
            route, SimpleNamespace(subject=subject),
        )

    def test_the_school_query_carries_no_war(self):
        # Without the guard this returns the log's own string, verbatim:
        # "6/25 war Washington University Seattle".
        resolved = self._query(
            subject="Washington University in Seattle",
            request="What is famous about Washington University in Seattle",
            query="Washington University Seattle famous",
        )

        self.assertNotIn("war", resolved.casefold())
        self.assertNotIn("6/25", resolved)
        self.assertIn("washington", resolved.casefold())

    def test_the_visa_query_carries_no_war(self):
        resolved = self._query(
            subject="SEVIS fee",
            request="SEVIS fee amount for I-20 received",
            query="SEVIS fee amount I-20",
        )

        self.assertNotIn("war", resolved.casefold())
        self.assertIn("sevis", resolved.casefold())

    def test_a_follow_up_about_the_same_thing_still_inherits(self):
        # The regression to watch, and the reason the branch exists: a
        # turn that names nothing keeps everything the task established.
        resolved = self._query(
            subject="", request="which one would you choose?",
            query="which one would you choose",
        )

        self.assertIn("war", resolved.casefold())

    def test_a_korean_turn_about_the_same_thing_still_inherits(self):
        self.engine.task_sessions.note_recommendation_turn(
            "노트북 추천해줘", subject="노트북",
        )
        self.engine.task_sessions.note_recommendation_turn(
            "show me some options", follow_up=True,
        )
        resolved = self._query(
            subject="노트북", request="게이밍용 노트북은 어때?",
            query="게이밍 노트북",
        )

        self.assertIn("노트북", resolved)


if __name__ == "__main__":
    unittest.main()
