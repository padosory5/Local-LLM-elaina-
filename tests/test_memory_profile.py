"""What she knows about the person, put in front of her every turn.

Measured before this existed: ten facts told, seven stored, and after a
restart 0 of 11 recalled -- the store worked, nothing ever showed it to her.
The profile is that showing. These tests hold what goes in it (facts, not
moods; each once; a few project notes) and what the engine does with it:
a question about a detail they never told her is answered with a fixed
line, never generated.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from datetime import datetime

from brain import guard_lines, memory_gate
from memory.memory_manager import select_profile
from tests.turn_harness import build_engine

DAY = datetime(2026, 9, 11, 12, 0)


def rows(*items):
    return [(category, content, DAY) for category, content in items]


class WhatGoesInTests(unittest.TestCase):

    def test_facts_not_moods(self):
        facts = select_profile(rows(
            ("personal", "The user feels exhausted due to lack of sleep."),
            ("personal", "The user is doing well."),
            ("personal", "The user's name is Minjun Park."),
        ))

        self.assertEqual(facts, ["The user's name is Minjun Park."])

    def test_each_near_duplicate_once(self):
        facts = select_profile(rows(
            ("preference", "The user does not drink coffee after 2pm."),
            ("preference", "The user does not consume coffee after 2 PM."),
            ("preference", "The user does not drink coffee after 2 PM."),
        ))

        self.assertEqual(len(facts), 1)

    def test_a_short_fact_is_not_a_duplicate_of_every_other(self):
        # Measured: "The user is vegetarian." was dropped as a duplicate of
        # "The user is returning to Seattle next Friday." -- they share only
        # "the" and "user".
        facts = select_profile(rows(
            ("personal", "The user is returning to Seattle next Friday."),
            ("preference", "The user is vegetarian."),
            ("personal", "The user is allergic to peanuts."),
            ("personal", "The user's name is Minjun Park."),
        ))

        self.assertEqual(len(facts), 4)
        self.assertIn("The user is vegetarian.", facts)

    def test_a_few_project_notes_at_most(self):
        facts = select_profile(rows(
            ("project", "The user is adding chat bubbles to the desktop window."),
            ("project", "The user wants a live 2D model in the interface."),
            ("project", "The user is fixing bugs in the router."),
            ("project", "The user pushed changes to a remote repository."),
            ("project", "The user is testing a screen analysis feature."),
            ("personal", "The user's name is Minjun Park."),
        ))

        self.assertEqual(sum("The user's name" not in fact for fact in facts), 3)
        self.assertIn("The user's name is Minjun Park.", facts)

    def test_the_extractors_fallback_category_is_included(self):
        # Measured after a restart: "I'm allergic to peanuts." was filed as
        # "general" -- the extractor's fallback -- and the profile query
        # left that category out, so she said "You haven't told me that yet."
        from memory.memory_manager import MemoryManager
        self.assertIn("general", MemoryManager.PROFILE_CATEGORIES)
        self.assertEqual(
            select_profile(rows(("general", "The user is allergic to peanuts."))),
            ["The user is allergic to peanuts."],
        )

    def test_a_relative_day_carries_the_day_it_was_said(self):
        [fact] = select_profile(rows(
            ("personal", "The user is returning to Seattle next Friday."),
        ))

        self.assertEqual(fact, "The user is returning to Seattle next Friday. "
                               "(said on 2026-09-11)")


class QuestionsAboutThemselvesTests(unittest.TestCase):

    def test_what_counts(self):
        for said in ("What's my name?", "When's my birthday?", "Do I have any food allergies?",
                     "우리 강아지 이름 뭐였지?", "내 여동생 어디 산다고 했지?",
                     "내가 수업 끝나고 무슨 게임 한다고 했지?"):
            with self.subTest(said=said):
                self.assertTrue(memory_gate.asks_about_themselves(said))
        for said in ("what's the weather tomorrow?", "tell me a joke", "what's on my screen?",
                     "오늘 날씨 어때?", "hey",
                     # Advice, not a fact about them. Taken for one, the
                     # first was answered from memory with an invented casino.
                     "Since there's a casino on Bainbridge Island, which one should I go to?",
                     "what should I eat tonight?", "how do I get to the airport?",
                     "which laptop should I buy?", "우리 뭐 먹을까?", "우리 어디 갈까?"):
            with self.subTest(said=said):
                self.assertFalse(memory_gate.asks_about_themselves(said))
        self.assertTrue(memory_gate.asks_about_themselves("Which school do I go to?"))
        self.assertTrue(memory_gate.asks_about_themselves("Where do I live?"))

    def test_a_topic_they_told_her_about(self):
        told = ["The user's name is Minjun Park.",
                "The user attends the University of Washington.",
                "The user has a peanut allergy."]
        self.assertTrue(memory_gate.shares_a_topic("Which school do I go to?", told))
        self.assertTrue(memory_gate.shares_a_topic("Do I have any food allergies?", told))
        self.assertTrue(memory_gate.shares_a_topic("What's my name?", told))
        # About the brother, not about their own name.
        self.assertFalse(memory_gate.shares_a_topic("What's my brother's name?", told))
        # A word inside another word is not the same topic.
        self.assertFalse(memory_gate.shares_a_topic(
            "which one should I go to?", ["Someone at the bakery is the user's friend."]))

    def test_a_personal_detail_is_narrower_than_a_question_about_them(self):
        self.assertTrue(memory_gate.asks_for_a_personal_detail("What's my brother's name?"))
        self.assertTrue(memory_gate.asks_for_a_personal_detail("내가 제일 좋아하는 색깔이 뭐였지?"))
        # A tool answers these.
        self.assertFalse(memory_gate.asks_for_a_personal_detail("Do I need an umbrella tomorrow?"))
        self.assertFalse(memory_gate.asks_for_a_personal_detail("What's my schedule tomorrow?"))

    def test_a_question_is_never_stored(self):
        self.assertTrue(memory_gate.is_question("내가 무슨 전공인지 기억해?"))
        self.assertFalse(memory_gate.is_question("나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어"))

    def test_said_the_way_people_say_it(self):
        for said in ("내 여동생은 부산에 살아", "나 수업 끝나고 보통 젠레스 존 제로 해",
                     "다음 주 금요일에 시애틀로 돌아가", "My sister lives in Busan.",
                     "I usually play Zenless Zone Zero after class."):
            with self.subTest(said=said):
                self.assertTrue(memory_gate.carries_something_to_remember(said))
        self.assertFalse(memory_gate.carries_something_to_remember("오늘 좀 피곤합니다"))


class TheEngineTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"
        self.engine.memory_enabled = True
        self.engine.memory_manager = object()   # present; profile comes from the stub below
        self.told: list[str] = []
        self.engine._what_they_told_her = lambda: list(self.told)

    def tearDown(self):
        self.engine.memory_enabled = False
        self.engine.memory_manager = None
        self.engine.close()

    def say(self, text):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._route_turn(text, timings={})

    def test_a_detail_never_told_is_said_to_be_unknown(self):
        self.told = ["The user's name is Minjun Park."]

        routing = self.say("내가 제일 좋아하는 색깔이 뭐였지?")

        self.assertEqual(routing.locked_response, guard_lines.say("not_told_yet", "ko"))

    def test_a_detail_she_was_told_is_not(self):
        self.told = ["The user's dog is named 콩 (in their words: \"우리 집 강아지 이름은 콩이야\")."]

        routing = self.say("우리 강아지 이름 뭐였지?")

        self.assertNotEqual(routing.locked_response, guard_lines.say("not_told_yet", "ko"))
        self.assertEqual(routing.route.intent, "conversation")

    def test_not_while_what_they_told_her_is_still_being_written(self):
        # Measured on the contamination matrix: asked a second after telling.
        import threading
        release = threading.Event()
        writing = threading.Thread(target=release.wait, daemon=True)
        writing.start()
        self.engine._memory_stores = [writing]
        try:
            routing = self.say("내가 제일 좋아하는 색깔이 뭐였지?")
        finally:
            release.set()
            writing.join()
            self.engine._memory_stores = []

        self.assertNotEqual(routing.locked_response, guard_lines.say("not_told_yet", "ko"))

    def test_not_about_something_said_in_this_conversation(self):
        self.engine.conversation.add("user", "My favorite color is green.")
        self.engine.conversation.add("assistant", "Green is a lovely color.")

        routing = self.say("What's my favorite color?")

        self.assertNotEqual(routing.locked_response, guard_lines.say("not_told_yet", "en"))

    def test_her_own_name_is_not_given_as_theirs(self):
        # Measured twice: "What's my name?" -> "Your name is Elaina."
        self.told = ["The user's name is Minjun Park."]
        self.engine._turn_language = "en"
        with contextlib.redirect_stdout(io.StringIO()):
            said = self.engine._their_name_not_hers("What's my name?", "Your name is Elaina.")

        self.assertEqual(said, "Your name is Minjun Park.")

    def test_her_own_name_is_not_given_as_theirs_in_korean(self):
        self.told = ["The user's name is Minjun Park."]
        with contextlib.redirect_stdout(io.StringIO()):
            said = self.engine._their_name_not_hers("내 이름 뭐였지?", "이름은 엘레나입니다.")

        self.assertEqual(said, "이름은 Minjun Park입니다.")

    def test_a_right_answer_and_her_own_name_elsewhere_are_left_alone(self):
        self.told = ["The user's name is Minjun Park."]
        self.engine._turn_language = "en"
        for said, reply in (("What's my name?", "Your name is Minjun Park."),
                            ("What's your name?", "My name is Elaina."),
                            ("Who are you?", "I'm Elaina.")):
            with self.subTest(reply=reply):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(self.engine._their_name_not_hers(said, reply), reply)

    def test_a_question_a_tool_answers_is_left_to_the_router(self):
        routing = self.say("Do I need an umbrella tomorrow?")

        self.assertNotEqual(routing.locked_response, guard_lines.say("not_told_yet", "en"))


class TheirOwnWordsSurviveExtractionTests(unittest.TestCase):
    """Measured across ten restart runs: asked "내가 수업 끝나고 무슨 게임
    한다고 했지?" she answered "수업 끝나고 보통 Genres of Zero 합니다" --
    the fact recalled, the name mangled, because the extraction prompt was
    written entirely in English and said nothing about which language to
    answer in. The same bug is filed against other multilingual memory
    systems; the fix they settled on is the one here: emit the source
    language, keep non-Latin names as they were written.
    """

    def test_the_prompt_says_which_language_to_write_in(self):
        from memory.extractor import SYSTEM_PROMPT

        self.assertIn("language the person used", SYSTEM_PROMPT)
        self.assertIn("Never", SYSTEM_PROMPT)
        self.assertIn("젠레스 존 제로", SYSTEM_PROMPT)

    def test_it_shows_a_korean_example_with_the_name_intact(self):
        from memory.extractor import SYSTEM_PROMPT

        # An instruction alone did not hold: the four worked examples were
        # all English, and examples win.
        self.assertIn("우리 집 강아지 이름은 콩이야", SYSTEM_PROMPT)
        self.assertIn("콩입니다", SYSTEM_PROMPT)

    def test_a_korean_memory_keeps_its_relative_day(self):
        [fact] = select_profile(rows(
            ("personal", "사용자는 다음 주 금요일에 시애틀로 돌아갑니다."),
        ))

        self.assertIn("(said on 2026-09-11)", fact)


class NothingIsDroppedInSilenceTests(unittest.TestCase):
    """The consolidator decides ADD / UPDATE / IGNORE, and IGNORE loses the
    fact. Measured: "나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어" was
    extracted correctly, consolidated away without a word, and the restart
    afterwards could not say which school -- two of eleven recall questions
    lost to one silent verdict."""

    def read(self, content):
        from memory.consolidator import MemoryConsolidator
        return MemoryConsolidator.read({"message": {"content": content}})

    def test_an_unreadable_verdict_keeps_the_memory(self):
        for content in ("", "not json", "[1, 2]", '{"action": "WHAT"}', None):
            with self.subTest(content=content):
                self.assertEqual(self.read(content), {"action": "ADD"})

    def test_a_clean_verdict_is_obeyed(self):
        self.assertEqual(self.read('{"action":"IGNORE"}'), {"action": "IGNORE"})
        self.assertEqual(
            self.read('{"action":"UPDATE","memory_id":5,"content":"now Tacoma"}'),
            {"action": "UPDATE", "memory_id": 5, "content": "now Tacoma"},
        )

    def test_an_update_that_names_nothing_keeps_the_memory(self):
        for content in ('{"action":"UPDATE"}',
                        '{"action":"UPDATE","memory_id":5}',
                        '{"action":"UPDATE","content":"x"}'):
            with self.subTest(content=content):
                self.assertEqual(self.read(content), {"action": "ADD"})

    def test_the_prompt_says_another_language_is_not_a_duplicate(self):
        from memory.consolidator import SYSTEM_PROMPT

        self.assertIn("NOT a duplicate", SYSTEM_PROMPT)
        self.assertIn("ADD when it says something the existing memories do not",
                      SYSTEM_PROMPT)


class TheExtractionLanguageIsCheckedTests(unittest.TestCase):
    """The prompt says to write in the language they used, and one run in two
    it did not: "나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어" came back as
    "... at Washington University" -- the wrong language, and a different
    university. Checked here rather than hoped for."""

    def extractor(self, *answers):
        from memory.extractor import MemoryExtractor
        engine = MemoryExtractor.__new__(MemoryExtractor)
        remaining = list(answers)
        self.asked = []

        def once(said, insist=False):
            self.asked.append(insist)
            return remaining.pop(0)

        engine._extract_once = once
        return engine

    def test_what_counts_as_the_wrong_language(self):
        from memory.extractor import wrong_language

        self.assertTrue(wrong_language("나 워싱턴 대학교 다녀",
                                       "The user attends Washington University."))
        self.assertFalse(wrong_language("나 워싱턴 대학교 다녀",
                                        "사용자는 워싱턴 대학교에 다닙니다."))
        self.assertFalse(wrong_language("I go to UW.", "The user goes to UW."))

    def test_it_asks_once_more_and_takes_the_second(self):
        english = {"save": True, "content": "The user attends Washington University.",
                   "category": "education"}
        korean = {"save": True, "content": "사용자는 워싱턴 대학교에 다닙니다.",
                  "category": "education"}
        engine = self.extractor(english, korean)

        with contextlib.redirect_stdout(io.StringIO()):
            result = engine.extract("나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어")

        self.assertEqual(result["content"], korean["content"])
        self.assertEqual(self.asked, [False, True])

    def test_their_own_words_when_it_never_complies(self):
        english = {"save": True, "content": "The user attends Washington University.",
                   "category": "education"}
        engine = self.extractor(english, dict(english))

        with contextlib.redirect_stdout(io.StringIO()):
            result = engine.extract("나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어")

        self.assertEqual(result["content"], "나 워싱턴 대학교에서 컴퓨터공학 전공하고 있어")
        self.assertEqual(result["category"], "education")

    def test_an_english_turn_is_asked_once(self):
        answer = {"save": True, "content": "The user goes to UW.", "category": "education"}
        engine = self.extractor(answer)

        with contextlib.redirect_stdout(io.StringIO()):
            result = engine.extract("I go to UW.")

        self.assertEqual(result, answer)
        self.assertEqual(self.asked, [False])

    def test_the_prompt_keeps_only_what_stays_true(self):
        from memory.extractor import SYSTEM_PROMPT

        self.assertIn("Keep only what stays true", SYSTEM_PROMPT)
        self.assertIn("The user is vegetarian.", SYSTEM_PROMPT)


class TheConsolidatorMayNotOverwriteAnythingTests(unittest.TestCase):
    """Its UPDATE overwrites a row, so the model proposes and code decides.

    Measured: it answered UPDATE for the game habit against the education
    memory -- two facts sharing only the word for "the user" -- and the
    education fact was gone, its row still labelled education. Two of
    eleven recall questions went with it.
    """

    def test_what_counts_as_the_same_fact(self):
        from memory.consolidator import same_fact

        self.assertTrue(same_fact("The user is going to UW in Seattle.",
                                  "The user is going to UW in Tacoma."))
        self.assertTrue(same_fact("The user does not drink coffee after 2pm.",
                                  "The user does not drink coffee after 2 PM."))
        self.assertFalse(same_fact("사용자는 워싱턴 대학교에서 컴퓨터공학을 전공하고 있습니다.",
                                   "사용자는 수업이 끝나면 보통 젠레스 존 제로를 합니다."))
        self.assertFalse(same_fact("사용자의 강아지 이름은 콩입니다.",
                                   "사용자의 여동생은 부산에 살아."))
        self.assertFalse(same_fact("The user's name is Minjun Park.",
                                   "The user is allergic to peanuts."))
        self.assertFalse(same_fact("", "The user is vegetarian."))


class TheStoreObeysThatCheckTests(unittest.TestCase):

    def setUp(self):
        from types import SimpleNamespace
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.stored, self.updated = [], []
        self.existing = SimpleNamespace(
            id=1, content="사용자는 워싱턴 대학교에서 컴퓨터공학을 전공하고 있습니다.")
        outer = self

        class Manager:
            def search_memory_objects(self, content):
                return [outer.existing]

            def store_memory(self, content, category, importance):
                outer.stored.append(content)

            def update_memory(self, memory_id, content):
                outer.updated.append((memory_id, content))

        class Extractor:
            def extract(self, said):
                return {"save": True, "content": outer.new_content,
                        "category": "personal"}

        class Consolidator:
            def consolidate(self, similar, content):
                return outer.verdict

        self.engine.memory_enabled = True
        self.engine.memory_manager = Manager()
        self.engine.extractor = Extractor()
        self.engine.consolidator = Consolidator()
        self.new_content = "사용자는 수업이 끝나면 보통 젠레스 존 제로를 합니다."
        self.verdict = {"action": "ADD"}

    def tearDown(self):
        self.engine.memory_enabled = False
        self.engine.memory_manager = None
        self.engine.close()

    def store(self, said="나 수업 끝나고 보통 젠레스 존 제로 해"):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine._store_memory_candidate(said)

    def test_an_update_onto_a_different_fact_keeps_both(self):
        self.verdict = {"action": "UPDATE", "memory_id": 1,
                        "content": "사용자는 수업이 끝나면 젠레스 존 제로를 합니다."}
        self.store()

        self.assertEqual(self.updated, [])
        self.assertEqual(self.stored, [self.new_content])

    def test_an_update_onto_the_same_fact_is_obeyed(self):
        self.existing.content = "The user is going to UW in Seattle."
        self.new_content = "The user is going to UW in Tacoma."
        self.verdict = {"action": "UPDATE", "memory_id": 1,
                        "content": self.new_content}
        self.store("no I mean I'm going to UW in Tacoma")

        self.assertEqual(self.updated, [(1, self.new_content)])
        self.assertEqual(self.stored, [])

    def test_ignore_of_something_she_does_not_have_still_keeps_it(self):
        self.verdict = {"action": "IGNORE"}
        self.store()

        self.assertEqual(self.stored, [self.new_content])

    def test_ignore_of_something_she_does_have_drops_it(self):
        self.existing.content = "사용자는 수업이 끝나면 젠레스 존 제로를 합니다."
        self.verdict = {"action": "IGNORE"}
        self.store()

        self.assertEqual(self.stored, [])


class AQuestionIsNeverStoredTests(unittest.TestCase):
    """Measured after a restart: the extractor declined "우리 강아지 이름
    뭐였지?", the gate's durable pattern matched "강아지 이름", and the
    question was kept as a memory of the person -- which then made a
    question about something never told look answered."""

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.asked: list[str] = []
        asked = self.asked

        class Extractor:
            def extract(self, said):
                asked.append(said)
                return {"save": False, "content": "", "category": "personal"}

        self.engine.memory_enabled = True
        self.engine.memory_manager = object()
        self.engine.extractor = Extractor()
        self.engine.consolidator = object()

    def tearDown(self):
        self.engine.memory_enabled = False
        self.engine.memory_manager = None
        self.engine.close()

    def store(self, said):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine._store_memory_candidate(said)

    def test_a_question_does_not_reach_the_extractor(self):
        for said in ("우리 강아지 이름 뭐였지?", "내 여동생 어디 산다고 했지?",
                     "내가 무슨 전공인지 기억해?", "What's my name?",
                     "Do I have any food allergies?"):
            with self.subTest(said=said):
                self.store(said)

        self.assertEqual(self.asked, [])

    def test_what_they_tell_her_still_does(self):
        for said in ("우리 집 강아지 이름은 콩이야", "I'm allergic to peanuts.",
                     "I go to school at UW, where is the office?"):
            self.store(said)

        self.assertEqual(len(self.asked), 3)


if __name__ == "__main__":
    unittest.main()
