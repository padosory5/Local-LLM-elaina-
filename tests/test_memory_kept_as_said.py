"""What she keeps about a person is what they said, and it is never lost on the way.

Three things were wrong with the path from a sentence to a memory, found on
2026-10-13 by reading it against the real store and the desktop window:

1. **It was lost when she was closed.** The memory was written at the end of a
   background thread, after two model calls. The window stops the backend with
   ``taskkill /F`` (desktop/main.js), so ``ChatEngine.close()`` -- which waits
   for those threads -- never runs. Now the sentence is written down in the
   turn (memory/said.py) and whatever is still waiting is finished at the next
   start.

2. **It could say more than they did.** The wording is a model's, and the
   update path wrote a *second* model's wording over the row. Now both are
   checked against their sentence (memory/faithful.py); when the wording says
   anything they did not, their own words are kept instead.

3. **Half of it was never looked at.** The gate that decides which sentences
   reach the extractor is a list of patterns, and it let through 44 of 95
   things a person says about themselves ("I have asthma", "I'm training for
   a marathon", "작년에 미국으로 이사 왔어"). Now anything stated in the first
   person is looked at, and the extractor decides what is worth keeping.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace

from memory import faithful
from tests.turn_harness import build_engine


class Journal:
    """memory.said.SaidJournal, recording."""

    def __init__(self, waiting=()):
        self.noted, self.settled, self.attempts = [], [], []
        self._waiting = list(waiting)

    def note(self, text):
        self.noted.append(text)
        return len(self.noted)

    def settle(self, said_id, memory_id=None, kept=None):
        self.settled.append((said_id, memory_id, bool(memory_id is not None if kept is None else kept)))

    def tried(self, said_id):
        self.attempts.append(said_id)
        return self.attempts.count(said_id)

    def waiting(self, limit=50):
        return list(self._waiting)


class _Store(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.stored, self.updated, self.asked = [], [], []
        self.existing = []
        self.journal = Journal()
        self.extracted = {"save": True, "content": "", "category": "personal"}
        self.verdict = {"action": "ADD"}
        outer = self

        class Manager:
            said = outer.journal

            def search_memory_objects(self, content):
                return list(outer.existing)

            def store_memory(self, content, category, importance):
                outer.stored.append(content)
                return 100 + len(outer.stored)

            def update_memory(self, memory_id, content):
                outer.updated.append((memory_id, content))

        class Extractor:
            def extract(self, said):
                outer.asked.append(said)
                if isinstance(outer.extracted, Exception):
                    raise outer.extracted
                return dict(outer.extracted)

        class Consolidator:
            def consolidate(self, similar, content):
                return dict(outer.verdict)

        self.engine.memory_enabled = True
        self.engine.memory_manager = Manager()
        self.engine.extractor = Extractor()
        self.engine.consolidator = Consolidator()

    def tearDown(self):
        self.engine.memory_enabled = False
        self.engine.memory_manager = None
        self.engine.close()

    def store(self, said, wording="", *, save=True, said_id=None) -> str:
        if not isinstance(self.extracted, Exception):
            self.extracted = {"save": save, "content": wording, "category": "personal"}
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.engine._store_memory_candidate(said, said_id=said_id)
        return log.getvalue()


class TheirsOrTheirOwnWordsTests(_Store):

    def test_a_rewording_that_says_the_same_is_kept(self):
        self.store("I live in Seattle.", "The user lives in Seattle.")
        self.assertEqual(self.stored, ["The user lives in Seattle."])

    def test_a_guess_at_what_they_meant_is_not(self):
        log = self.store("I'm a junior at UW.", "The user is a junior at the University of Washington.")

        self.assertEqual(self.stored, ['They said: "I\'m a junior at UW."'])
        self.assertIn("said more than they did", log)
        self.assertIn("washington", log)

    def test_the_measured_ones(self):
        for said, wording in (
            ("우리 집 강아지 이름은 콩이야", "The user's dog is named Kongi."),
            ("나 수업 끝나고 보통 젠레스 존 제로 해", "The user usually plays Genres Zero after class."),
            ("나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어",
             "The user is majoring in Computer Engineering at Washington University."),
        ):
            with self.subTest(said=said):
                self.stored.clear()
                self.store(said, wording)
                self.assertEqual(self.stored, [faithful.as_said(said)])

    def test_only_what_they_stated_not_what_they_asked(self):
        self.store("By the way I'm vegetarian. What's a quick dinner I can make tonight?",
                   "The user is vegetarian and wants a quick dinner tonight.")
        self.assertEqual(self.stored, ['They said: "By the way I\'m vegetarian."'])

    def test_when_the_extractor_declines_a_fact_their_words_are_kept(self):
        self.store("i'm vegetarian by the way", save=False)
        self.assertEqual(self.stored, ['They said: "i\'m vegetarian by the way"'])

    def test_an_update_writes_what_was_checked_not_the_second_model_s_wording(self):
        self.existing = [SimpleNamespace(id=1, content="The user is going to UW in Seattle.")]
        self.verdict = {"action": "UPDATE", "memory_id": 1,
                        "content": "The user attends the University of Washington Tacoma."}
        self.store("no I mean I'm going to UW in Tacoma", "The user is going to UW in Tacoma.")

        self.assertEqual(self.updated, [(1, "The user is going to UW in Tacoma.")])
        self.assertEqual(self.stored, [])


class WrittenDownFirstTests(_Store):

    def test_kept_says_which_memory_it_became(self):
        self.store("I live in Seattle.", "The user lives in Seattle.", said_id=4)
        self.assertEqual(self.journal.settled, [(4, 101, True)])

    def test_an_update_names_the_memory_it_went_into(self):
        self.existing = [SimpleNamespace(id=1, content="The user is going to UW in Seattle.")]
        self.verdict = {"action": "UPDATE", "memory_id": 1, "content": "anything"}
        self.store("no I mean I'm going to UW in Tacoma", "The user is going to UW in Tacoma.", said_id=5)
        self.assertEqual(self.journal.settled, [(5, 1, True)])

    def test_something_she_already_knew_points_at_where_it_is(self):
        self.existing = [SimpleNamespace(id=9, content="The user lives in Seattle.")]
        self.verdict = {"action": "IGNORE"}
        self.store("I live in Seattle.", "The user lives in Seattle.", said_id=6)

        self.assertEqual(self.stored, [])
        self.assertEqual(self.journal.settled, [(6, 9, True)])

    def test_nothing_to_keep_is_settled_as_looked_at(self):
        self.store("I see what you mean about that.", save=False, said_id=7)

        self.assertEqual(self.stored, [])
        self.assertEqual(self.journal.settled, [(7, None, False)])

    def test_a_question_is_settled_without_asking_a_model(self):
        self.store("What's my name?", said_id=8)

        self.assertEqual(self.asked, [])
        self.assertEqual(self.journal.settled, [(8, None, False)])

    def test_a_failure_leaves_it_waiting_for_the_next_start(self):
        self.extracted = ConnectionError("Ollama is not running")
        self.store("I have asthma.", said_id=9)

        self.assertEqual(self.journal.settled, [])
        self.assertEqual(self.journal.attempts, [9])

    def test_without_a_journal_nothing_else_changes(self):
        self.engine.memory_manager.__class__.said = None
        try:
            self.store("I live in Seattle.", "The user lives in Seattle.", said_id=None)
        finally:
            self.engine.memory_manager.__class__.said = self.journal
        self.assertEqual(self.stored, ["The user lives in Seattle."])


class FinishedAtTheNextStartTests(_Store):

    def finish(self) -> str:
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.engine._finish_what_was_left()
            for thread in list(getattr(self.engine, "_memory_stores", [])):
                thread.join(timeout=10)
        return log.getvalue()

    def test_what_was_left_when_she_was_closed_is_made_into_memories(self):
        self.journal._waiting = [(5, "I have asthma."), (6, "나 천식이 있어")]
        self.extracted = {"save": True, "content": "", "category": "personal"}
        log = self.finish()

        self.assertEqual(self.asked, ["I have asthma.", "나 천식이 있어"])
        self.assertEqual(self.stored, ['They said: "I have asthma."', 'They said: "나 천식이 있어"'])
        self.assertEqual([(said_id, kept) for said_id, _memory, kept in self.journal.settled], [(5, True), (6, True)])
        self.assertIn("2 thing", log)

    def test_nothing_left_nothing_done(self):
        log = self.finish()
        self.assertEqual(self.asked, [])
        self.assertNotIn("[Memory]", log)

    def test_not_when_memory_is_off(self):
        self.journal._waiting = [(5, "I have asthma.")]
        self.engine.memory_enabled = False
        self.finish()
        self.assertEqual(self.asked, [])


class MoreIsLookedAtTests(_Store):
    """Which turns are written down and shown to the extractor."""

    def worth(self, said, intent="conversation", flagged=False, shape="state", act="statement") -> bool:
        route = SimpleNamespace(intent=intent, memory_candidate=flagged, answer_shape=shape, speech_act=act)
        return self.engine._worth_writing_down(said, route)

    def test_a_request_is_not_her_being_told_about_them(self):
        # Read by the router as asking for something; both became "memories".
        for said in ("Okay, but how exactly do you turn a 32-bit float into a 16-bit integer? "
                     "I know what those are, but I don't get the actual math.",
                     "Wait, you said it saves computation, but I thought integers are faster "
                     "just because they're smaller."):
            for act in ("information_request", "action_request"):
                with self.subTest(said=said, act=act):
                    self.assertFalse(self.worth(said, intent="knowledge_question", act=act))
        # The patterns are not held back by it.
        self.assertTrue(self.worth("I'm allergic to shellfish, find me somewhere for dinner.",
                                   intent="web_search", act="action_request"))

    def test_inside_an_explanation_the_first_person_is_about_the_subject(self):
        # Each of these became a "memory" when the real extractor was shown it.
        for said in ("Okay, that makes sense. I thought the Earth's shadow covered part of the moon.",
                     "Wait, that's not right. If I just truncate the decimal, I'm not actually saving bits.",
                     "아 근데 나는 배당금이 주가 오르면 생기는 돈인 줄 알았어."):
            for shape in ("explain", "example"):
                with self.subTest(said=said, shape=shape):
                    self.assertFalse(self.worth(said, shape=shape))

    def test_what_the_patterns_know_is_kept_even_there(self):
        self.assertTrue(self.worth("I'm a nurse, so explain it the way you would to a nurse.", shape="explain"))
        self.assertTrue(self.worth("내 여동생은 부산에 살아", shape="explain"))

    def test_what_the_patterns_knew_is_still_kept(self):
        for said in ("I'm allergic to shellfish, find me somewhere for dinner.", "My name is Jisoo.",
                     "내 여동생은 부산에 살아", "Remember that I park on level 3."):
            with self.subTest(said=said):
                self.assertTrue(self.worth(said, intent="web_search"))

    def test_what_they_did_not_know_is_looked_at_now(self):
        for said in ("I have asthma.", "I'm lactose intolerant.", "My favorite color is blue.",
                     "I'm training for a marathon.", "I just got a new job at Microsoft.",
                     "I was born in Busan.", "We're moving to Portland next month."):
            with self.subTest(said=said):
                self.assertTrue(self.worth(said, intent="conversation"))

    def test_korean_leaves_the_subject_out(self):
        for said in ("작년에 미국으로 이사 왔어", "매일 아침 여섯 시에 일어나", "요즘 일본어 공부하고 있어",
                     "유당불내증이라 우유 못 마셔"):
            with self.subTest(said=said):
                self.assertTrue(self.worth(said, intent="conversation"))
                # A request to do something is not her being told about them.
                self.assertFalse(self.worth(said, intent="task_action"))

    def test_what_is_not_about_them_is_not(self):
        for said in ("What's my name?", "Do I have any allergies?", "우리 강아지 이름 뭐였지?",
                     "I'm tired.", "I'm so hungry right now.", "오늘 좀 피곤해",
                     "Okay, thanks.", "That makes sense.", "그렇구나", "고마워",
                     "What's the weather like?", "How does quantization work?",
                     "The capital of France is Paris.", "Open YouTube."):
            with self.subTest(said=said):
                self.assertFalse(self.worth(said))

    def test_the_router_s_own_flag_still_counts(self):
        self.assertTrue(self.worth("Portland, probably in the spring.", flagged=True))
        self.assertFalse(self.worth("Portland, probably in the spring?", flagged=True))

    def test_not_while_she_is_only_being_asked_to_say_it_again(self):
        self.engine._asked_to_repeat = True
        self.assertFalse(self.worth("I have asthma."))


class AMemoryFailureDoesNotCostTheReplyTests(unittest.TestCase):
    """The reply thread read memory with nothing around it, so when the
    memory session broke the exception went up through the turn and she said
    nothing at all (measured live, 2026-10-13: three turns in a row, 150
    seconds each, no reply)."""

    def test_she_answers_without_it(self):
        from unittest import mock

        from tests.turn_harness import ScriptedClient

        asked = "what's a good restaurant near my school?"
        route = {"intent": "conversation", "confidence": 0.99, "normalized_request": asked,
                 "reason": "scripted", "speech_act": "information_request", "memory_relevant": True,
                 "answer_shape": "state", "is_follow_up": False}

        class Tripwire:
            def __init__(self, *args, **kwargs):
                pass

            def text(self, query, max_results=3, **kwargs):
                raise AssertionError("the real search backend was asked")

        class Broken:
            said = None

            def search(self, *args, **kwargs):
                raise RuntimeError("This Session's transaction has been rolled back")

            def profile(self, *args, **kwargs):
                return []

        with mock.patch("tools.web_search.DDGS", Tripwire):
            with contextlib.redirect_stdout(io.StringIO()):
                engine = build_engine({asked: route})
            self.addCleanup(engine.close)
            client = ScriptedClient(routes={asked: route}, reply="I don't know which school you go to yet.")
            engine.client = client
            for owner in (engine.intent_router, getattr(engine, "task_intent_gate", None)):
                if owner is not None and hasattr(owner, "client"):
                    owner.client = client
            engine.memory_enabled = True
            engine.memory_manager = Broken()
            engine.extractor = engine.consolidator = None
            log = io.StringIO()
            try:
                with contextlib.redirect_stdout(log):
                    reply = engine.chat(asked)
            finally:
                engine.memory_enabled = False
                engine.memory_manager = None

        self.assertTrue(str(reply).strip())
        self.assertIn("Could not read memory for this turn", log.getvalue())


class WhatOneTurnIsShownTests(_Store):
    """What she knows is all of it; what goes in front of the model on one
    turn is bounded. With one cap doing both jobs (the newest 24), the first
    things a person ever said -- their name -- were the first to go, and
    "what's my name?" would have been answered "you haven't told me"."""

    def setUp(self):
        super().setUp()
        self.told = [f"The user visited place number {n}." for n in range(40)] + [
            "The user's name is Jisoo.", "The user is allergic to peanuts."]

    def test_a_short_list_goes_whole(self):
        self.assertEqual(self.engine._told_for_this_turn(self.told[:12], "hello"), self.told[:12])

    def test_a_long_one_keeps_the_newest_and_what_this_turn_is_about(self):
        shown = self.engine._told_for_this_turn(self.told, "What's my name?")

        self.assertEqual(shown[:20], self.told[:20])
        self.assertIn("The user's name is Jisoo.", shown)
        self.assertNotIn("The user is allergic to peanuts.", shown)
        self.assertLessEqual(len(shown), 30)

    def test_the_same_list_another_question(self):
        shown = self.engine._told_for_this_turn(self.told, "Do I have any food allergies?")

        self.assertIn("The user is allergic to peanuts.", shown)
        self.assertNotIn("The user's name is Jisoo.", shown)

    def test_nothing_on_the_subject_shows_only_the_newest(self):
        self.assertEqual(self.engine._told_for_this_turn(self.told, "tell me a joke"), self.told[:20])


class TwoShortFactsAreTwoFactsTests(unittest.TestCase):
    """Found while testing the above: the profile shows each near-duplicate
    once, and two facts of two words each counted as one if they shared a
    verb. "The user likes jazz." went as a duplicate of "The user likes
    pizza." -- kept in the database, never in front of her."""

    def test_sharing_a_verb_is_not_saying_the_same_thing(self):
        from datetime import datetime

        from memory.memory_manager import select_profile

        day = datetime(2026, 10, 13, 12, 0)
        facts = select_profile([
            ("preference", "The user likes pizza.", day),
            ("preference", "The user likes jazz.", day),
            ("personal", "The user lives in Seattle.", day),
            ("personal", "The user lives in Busan with a roommate.", day),
            ("personal", "The user has asthma.", day),
            ("personal", "The user has a dog.", day),
        ])
        self.assertEqual(len(facts), 6)

    def test_a_short_korean_sentence_is_shown_at_all(self):
        # Its words are two syllables each; counted from three there were none.
        from datetime import datetime

        from memory.memory_manager import select_profile

        day = datetime(2026, 10, 13, 12, 0)
        facts = select_profile([
            ("personal", 'They said: "나 매운 거 못 먹어"', day),
            ("personal", 'They said: "나 아이오닉 타"', day),
            ("personal", "사용자는 천식이 있습니다.", day),
            ("personal", "사용자는 결혼했습니다.", day),
        ], now=day)
        self.assertEqual(len(facts), 4)

    def test_the_same_fact_reworded_is_still_shown_once(self):
        from datetime import datetime

        from memory.memory_manager import select_profile

        day = datetime(2026, 10, 13, 12, 0)
        facts = select_profile([
            ("preference", "The user does not drink coffee after 2pm.", day),
            ("preference", "The user does not consume coffee after 2 PM.", day),
            ("preference", "The user likes jazz.", day),
            ("preference", "The user likes jazz a lot.", day),
        ])
        self.assertEqual(facts, ["The user does not drink coffee after 2pm.", "The user likes jazz."])


class ARemarkAboutOneDayTests(unittest.TestCase):
    """"I went to the gym this morning" is true the day it is said. The
    extractor keeps about half of such remarks (8 of 18, measured), so they
    are dated, and after a few days no longer put in front of her."""

    def shown(self, said_days_ago: int) -> list[str]:
        from datetime import datetime, timedelta

        from memory.memory_manager import select_profile

        now = datetime(2026, 10, 13, 12, 0)
        day = now - timedelta(days=said_days_ago)
        return select_profile([
            ("personal", "The user went to the gym this morning.", day),
            ("personal", 'They said: "어제 잠을 잘 못 잤어"', day),
            ("personal", "The user watched a movie last night.", day),
            ("personal", "The user has asthma.", day),
            ("personal", "The user is returning to Seattle next Friday.", day),
        ], now=now)

    def test_the_same_day_it_is_shown_with_its_date(self):
        shown = self.shown(0)
        self.assertEqual(len(shown), 5)
        self.assertIn("The user went to the gym this morning. (said on 2026-10-13)", shown)
        self.assertIn("The user has asthma.", shown)

    def test_a_week_later_only_what_lasts_is_shown(self):
        self.assertEqual(self.shown(7), [
            "The user has asthma.",
            "The user is returning to Seattle next Friday. (said on 2026-10-06)",
        ])


class WhatTheyStatedTests(unittest.TestCase):
    """Their own words, without the part that only asks."""

    def stated(self, said: str) -> str:
        from brain import memory_gate

        return memory_gate.what_they_stated(said)

    def test_the_question_is_left_out(self):
        self.assertEqual(
            self.stated("By the way I'm vegetarian. What's a quick dinner I can make tonight?"),
            "By the way I'm vegetarian.")
        self.assertEqual(
            self.stated("I go to school at UW, where is the international students office?"),
            "I go to school at UW")

    def test_a_sentence_that_only_states_is_untouched(self):
        for said in ("I live in Seattle.", "작년에 미국으로 이사 왔어", "I don't eat pork",
                     "My dog's name is Kong. He is three."):
            with self.subTest(said=said):
                self.assertEqual(self.stated(said), said)

    def test_a_request_inside_the_same_clause_stays(self):
        # One clause, and theirs word for word.
        said = "I'm allergic to shellfish, find me somewhere for dinner."
        self.assertEqual(self.stated(said), said)

    def test_only_questions_states_nothing(self):
        self.assertEqual(self.stated("What's my name? Where do I live?"), "")


if __name__ == "__main__":
    unittest.main()
