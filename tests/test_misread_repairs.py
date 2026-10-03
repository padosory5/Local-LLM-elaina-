"""Catching what she would otherwise misunderstand.

Each case is a reply measured live on the misunderstanding check
(scripts/live_misread_check.py): a correction that lost the question, "it"
with nothing to refer to, a correction deleted with the invented name next
to it. Offline here -- the engine is the real one, the model is scripted.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from types import SimpleNamespace

from brain import guard_lines
from tests.turn_harness import build_engine


class EngineTestCase(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "en"
        self.history: list[str] = []
        self.engine._recent_messages = self._recent

    def _recent(self, *, exclude="", limit=12, roles=("user", "assistant")):
        return [said for role, said in self.history if role in roles][-limit:]

    def tearDown(self):
        self.engine.close()

    def corrected(self, previous, said):
        self.history = [("user", previous), ("assistant", "…")]
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._with_correction_applied(said)


class ACorrectionKeepsTheQuestionTests(EngineTestCase):

    def test_the_place_they_meant(self):
        self.assertEqual(
            self.corrected("What's the population of Portland?", "No, I meant Portland, Maine."),
            "What's the population of Portland, Maine?",
        )

    def test_in_korean(self):
        self.assertEqual(
            self.corrected("파리 날씨 어때?", "아니 텍사스에 있는 파리 말하는 거야"),
            "텍사스에 있는 파리 날씨 어때?",
        )

    def test_this_not_that(self):
        self.assertEqual(
            self.corrected("CPT 신청 서류 뭐가 필요해?", "아니 CPT 말고 OPT"),
            "OPT 신청 서류 뭐가 필요해?",
        )

    def test_a_kind_of_thing(self):
        self.assertEqual(
            self.corrected("Recommend a movie for tonight.",
                           "No, I meant a TV series, not a movie."),
            "Recommend a TV series for tonight.",
        )

    def test_said_again_whole(self):
        # Measured on the contamination matrix: put in at the first shared
        # word, this made "I'm I'm going to UW in Tacoma to UW in Seattle."
        self.assertEqual(
            self.corrected("I'm going to UW in Seattle.", "no I mean I'm going to UW in Tacoma"),
            "I'm going to UW in Tacoma.",
        )
        self.assertEqual(self.engine._corrected_to, "Tacoma")

    def test_what_they_took_back_leaves_her_profile(self):
        # Measured on the contamination matrix: the Seattle fact was stored,
        # the Tacoma one still being written, and she answered "Seattle".
        self.corrected("I'm going to UW in Seattle.", "no I mean I'm going to UW in Tacoma")
        with contextlib.redirect_stdout(io.StringIO()):
            kept = self.engine._without_what_they_took_back([
                "The user is going to UW in Seattle.",
                "The user is returning to Seattle next Friday.",
                "The user is vegetarian.",
            ])

        self.assertEqual(kept, ["The user is returning to Seattle next Friday.",
                                "The user is vegetarian."])

    def test_the_stored_rewrite_of_their_sentence_leaves_too(self):
        # The fact the store actually keeps. Measured on the contamination
        # matrix, 2026-09-23: only one memory existed after the correction
        # -- "The user is attending the University of Washington in
        # Seattle." -- and "where is my school again?" was answered from
        # it. It shares two words with their sentence, one of them "in",
        # so the overlap rule above never saw it.
        self.corrected("I'm going to UW in Seattle.", "no I mean I'm going to UW in Tacoma")
        with contextlib.redirect_stdout(io.StringIO()):
            kept = self.engine._without_what_they_took_back([
                "The user is attending the University of Washington in Seattle.",
                "The user is attending the University of Washington in Tacoma.",
                "The user works in Seattle.",
            ])

        self.assertEqual(kept, [
            "The user is attending the University of Washington in Tacoma.",
            "The user works in Seattle.",
        ])

    def test_an_abbreviation_is_read_as_what_it_stands_for(self):
        spells_out = self.engine._spells_out

        self.assertTrue(spells_out("UW", "attending the University of Washington"))
        self.assertTrue(spells_out("MIT", "the Massachusetts Institute of Technology"))
        # Not a run of capitals that happens to contain the letters.
        self.assertFalse(spells_out("UW", "returning to Seattle next Friday"))
        self.assertFalse(spells_out("UW", "the University in Seattle"))
        self.assertFalse(spells_out("U", "the University of Washington"))

    def test_a_correction_is_not_ignored_as_a_duplicate(self):
        # Measured on the contamination matrix, 2026-09-23: the database
        # held one memory after both turns -- "The user is attending the
        # University of Washington in Seattle." -- because the
        # consolidator was shown it, decided the corrected sentence said
        # the same thing, and answered IGNORE.
        self.corrected("I'm going to UW in Seattle.", "no I mean I'm going to UW in Tacoma")
        stored = SimpleNamespace(
            id=1,
            content="The user is attending the University of Washington in Seattle.",
        )
        updates = []
        self.engine.memory_manager = SimpleNamespace(
            update_memory=lambda memory_id, content: updates.append(
                (memory_id, content),
            ),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            corrects = self.engine._replaces_what_they_took_back(
                [stored],
                "The user is attending the University of Washington in Tacoma.",
            )

        self.assertIs(corrects, stored)

    def test_only_the_fact_it_corrects_is_replaced(self):
        self.corrected("I'm going to UW in Seattle.", "no I mean I'm going to UW in Tacoma")
        unrelated = SimpleNamespace(
            id=2, content="The user is returning to Seattle next Friday.",
        )

        with contextlib.redirect_stdout(io.StringIO()):
            # Not something they took back.
            self.assertIsNone(self.engine._replaces_what_they_took_back(
                [unrelated],
                "The user is attending the University of Washington in Tacoma.",
            ))
            # Took it back, but the new fact does not say what replaced it.
            self.assertIsNone(self.engine._replaces_what_they_took_back(
                [SimpleNamespace(
                    id=1,
                    content="The user is attending the University of Washington in Seattle.",
                )],
                "The user is vegetarian.",
            ))

    def test_the_same_thing_said_again_is_not_a_correction(self):
        self.assertEqual(
            self.corrected("I'm going to UW in Seattle.", "no I mean I'm going to UW in Seattle"),
            "no I mean I'm going to UW in Seattle",
        )

    def test_what_is_not_a_correction(self):
        for said in ("I mean it", "아니 괜찮아", "no thanks", "No, that's fine.",
                     "I mean, whatever works"):
            with self.subTest(said=said):
                self.assertEqual(self.corrected("Recommend a movie for tonight.", said), said)


class NothingToReferToTests(EngineTestCase):

    def route(self, said):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._route_turn(said, timings={})

    def test_first_thing_said(self):
        for said, language in (("Open it.", "en"), ("그거 얼마나 걸려?", "ko"),
                               ("how long does it take?", "en"), ("그거 틀어줘", "ko")):
            with self.subTest(said=said):
                self.engine._turn_language = language
                self.assertEqual(self.route(said).locked_response,
                                 guard_lines.say("refers_to_nothing", language))

    def test_after_she_has_said_something_it_is_resolved_as_usual(self):
        self.history = [("user", "find me the Bainbridge ferry schedule"),
                        ("assistant", "The ferry leaves every 50 minutes.")]

        routing = self.route("how long does it take?")

        self.assertNotEqual(routing.locked_response,
                            guard_lines.say("refers_to_nothing", "en"))


class TheGuardKeepsTheCorrectionTests(EngineTestCase):

    def test_the_clause_without_the_unchecked_name_survives(self):
        reply = ("There's no casino on Bainbridge Island, but you could try Emerald "
                 "Lucky Casino in Kingston.")
        with contextlib.redirect_stdout(io.StringIO()):
            kept = self.engine._enforce_grounded_entities(
                reply,
                user_input="Since there's a casino on Bainbridge Island, which one should I go to?",
                action_performed=False,
            )

        self.assertTrue(kept.startswith("There's no casino on Bainbridge Island."), kept)
        self.assertNotIn("Emerald Lucky", kept)


class AckClient:
    """Answers the acknowledgement call with a set line; the rest goes to the
    scripted model."""

    def __init__(self, inner, line):
        self.inner, self.line, self.asked = inner, line, []

    def chat(self, **kwargs):
        messages = kwargs.get("messages") or []
        if messages and "just told you something about their own" in str(messages[0].get("content", "")):
            self.asked.append(messages[-1]["content"])
            return {"message": {"content": self.line}}
        return self.inner.chat(**kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class ToldSomethingTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})
        self.engine._turn_language = "ko"

    def tearDown(self):
        self.engine.close()

    def check(self, said, answer, acknowledgement):
        self.engine.client = AckClient(self.engine.client, acknowledgement)
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._acknowledgement_if_missed(said, answer)

    def test_a_missed_answer_is_replaced_by_one_that_understood(self):
        # The first is what the ordinary answer said, measured live.
        said = self.check("내 여동생은 부산에 살아", "제가 잘 지내고 있습니다.",
                          "여동생분이 부산에 사시는군요. 기억해 두겠습니다.")

        self.assertEqual(said, "여동생분이 부산에 사시는군요. 기억해 두겠습니다.")

    def test_when_that_misses_too_the_fixed_line(self):
        said = self.check("내 여동생은 부산에 살아", "제가 잘 지내고 있습니다.",
                          "제가 잘 지내고 있습니다.")

        self.assertEqual(said, guard_lines.say("noted_fact", "ko"))

    def test_the_cold_pronoun_goes(self):
        # Measured: the prompt says 님/분, and it still came back with 당신.
        said = self.check("내 여동생은 부산에 살아", "제가 잘 지내고 있습니다.",
                          "당신의 여동생이 부산에 계시는군요.")

        self.assertEqual(said, "여동생이 부산에 계시는군요.")

    def test_a_question_back_is_replaced(self):
        said = self.check("저 다음 달에 이사가요", "다음 달에 이사하시나요?", "다음 달에 이사하시나요?")

        self.assertEqual(said, guard_lines.say("noted_fact", "ko"))

    def test_an_answer_that_took_it_in_is_left_alone(self):
        self.assertIsNone(self.check("I am going to UW in Seattle.",
                                     "UW is a great school, and Seattle is lovely.", "x"))
        self.assertEqual(self.engine.client.asked, [])

    def test_an_answer_that_took_it_in_and_asks_after_is_left_alone(self):
        # Measured: replaced for ending in a question, by something worse.
        self.assertIsNone(self.check("내 여동생은 부산에 살아",
                                     "여동생분께서 부산에 계시는군요. 잘 계시는가요?", "x"))
        self.assertEqual(self.engine.client.asked, [])

    def test_an_answer_to_her_own_question_is_left_alone(self):
        self.engine.conversation.add("user", "뭐 먹을지 추천해줘")
        self.engine.conversation.add("assistant", "혹시 못 드시는 음식이 있으신가요?")

        self.assertIsNone(self.check("나 채식주의자야", "좋습니다.", "채식을 하시는군요."))
        self.assertEqual(self.engine.client.asked, [])

    def test_a_request_or_an_instruction_is_not_just_telling(self):
        for said in ("By the way I'm vegetarian. What's a quick dinner I can make?",
                     "Remember that I'm allergic to peanuts."):
            with self.subTest(said=said):
                self.assertIsNone(self.check(said, "ok", "x"))
        self.assertEqual(self.engine.client.asked, [])


class RetryClient:
    """Answers any non-streamed call with a set reply, and counts them."""

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def chat(self, **kwargs):
        self.calls += 1
        return {"message": {"content": self.reply}}


class TheCorrectedQuestionIsAnsweredTests(EngineTestCase):

    def test_the_rewrite_records_what_they_meant(self):
        self.corrected("CPT 신청 서류 뭐가 필요해?", "아니 CPT 말고 OPT")

        self.assertEqual(self.engine._corrected_to, "OPT")

    def test_an_answer_about_the_old_one_is_asked_again_without_the_history(self):
        # Measured: the rewritten OPT question got the CPT answer back.
        self.engine._turn_language = "ko"
        self.engine.client = RetryClient("OPT 신청에는 I-765, I-20, EAD 사진이 필요합니다.")
        with contextlib.redirect_stdout(io.StringIO()):
            answer = self.engine._answer_the_corrected_question("OPT 신청 서류 뭐가 필요해?", "OPT")

        self.assertIn("OPT", answer)

    def test_what_they_meant_is_the_word_that_changed(self):
        self.corrected("What's the population of Portland?", "No, I meant Portland, Maine.")
        self.assertEqual(self.engine._corrected_to, "Maine")
        self.corrected("파리 날씨 어때?", "아니 텍사스에 있는 파리 말하는 거야")
        self.assertEqual(self.engine._corrected_to, "텍사스에 있는")

    def test_an_answer_names_it_the_way_it_is_said(self):
        names = self.engine._names_what_they_meant
        self.assertTrue(names("텍사스주 파리는 지금 맑고 28도입니다.", "텍사스에 있는"))
        self.assertTrue(names("OPT needs an I-765 and a new I-20.", "OPT"))
        self.assertFalse(names("CPT 신청 서류는 학업계획서입니다.", "OPT"))
        self.assertFalse(names("You have one option.", "OPT"))
        self.assertFalse(names("프랑스 파리는 맑습니다.", "텍사스에 있는"))

    def test_a_retry_still_about_the_old_one_is_not_used(self):
        self.engine.client = RetryClient("CPT 신청 서류는 학업계획서입니다.")
        with contextlib.redirect_stdout(io.StringIO()):
            answer = self.engine._answer_the_corrected_question("OPT 신청 서류 뭐가 필요해?", "OPT")

        self.assertEqual(answer, "")


class PremiseClient:
    """Answers the premise judge with a set verdict and counts the calls."""

    def __init__(self, verdict):
        self.verdict, self.calls = verdict, 0

    def chat(self, **kwargs):
        self.calls += 1
        return {"message": {"content": self.verdict}}


class AFalsePremiseIsCorrectedTests(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def check(self, language, said, reply, verdict):
        import json
        self.engine._turn_language = language
        self.engine.client = PremiseClient(json.dumps(verdict, ensure_ascii=False))
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._premise_corrected(said, reply)

    def test_an_answer_that_went_along_with_it_is_replaced(self):
        # Measured: "한국은 엔화 쓰잖아…" -> "환전 시 엔화 사용은 가능합니다".
        said = self.check(
            "ko", "한국은 엔화 쓰잖아, 환전할 때 엔으로 바꾸면 되지?",
            "환전 시 엔화 사용은 가능합니다.",
            {"kind": "fact", "holds": False,
             "correction": "한국은 엔화가 아니라 원화를 씁니다. 환전할 때는 원화로 바꾸시면 됩니다."},
        )

        self.assertTrue(said.startswith("한국은 엔화가 아니라 원화를"), said)

    def test_an_answer_that_already_said_so_is_left_alone(self):
        self.assertIsNone(self.check(
            "en", "Tokyo is the capital of China, right?", "Tokyo isn't the capital of China, Beijing is.",
            {"kind": "fact", "holds": False, "correction": "Tokyo is not the capital of China; Beijing is."},
        ))

    def test_a_correction_in_the_other_language_is_not_said(self):
        # Measured: an English question got its correction in Korean.
        self.assertIsNone(self.check(
            "en", "Einstein failed math in school, didn't he?", "Yes, he did.",
            {"kind": "fact", "holds": False, "correction": "에인슈타인은 수학을 잘했습니다."},
        ))

    def test_nothing_assumed_nothing_asked(self):
        self.assertIsNone(self.check(
            "en", "What's the capital of China?", "Beijing.",
            {"kind": "fact", "holds": False, "correction": "x"},
        ))
        self.assertEqual(self.engine.client.calls, 0)


class TheLastAnswerIsNotThisAnswerTests(EngineTestCase):
    """Measured on scripts/live_basics_check.py: asked "물은 몇 도에서
    끓어?" one turn after a question about Fahrenheit, she answered "물의
    동결점은 32도 화씨입니다" -- the previous answer."""

    def test_her_previous_answer_again_is_asked_again(self):
        self.engine._turn_language = "ko"
        before = "물의 동결점은 32도 화씨입니다."
        self.engine._things_she_has_said = lambda: (before, [])
        self.engine.client = RetryClient("물의 끓는점은 100도입니다.")
        with contextlib.redirect_stdout(io.StringIO()):
            said = self.engine._not_her_last_answer("물은 몇 도에서 끓어?", before)

        self.assertEqual(said, "물의 끓는점은 100도입니다.")
        self.assertEqual(self.engine.client.calls, 1)

    def test_an_answer_of_its_own_is_left_alone(self):
        self.engine._things_she_has_said = lambda: ("Water freezes at 32 degrees Fahrenheit.", [])
        self.engine.client = RetryClient("x")
        reply = "Water boils at 212 degrees Fahrenheit."
        with contextlib.redirect_stdout(io.StringIO()):
            said = self.engine._not_her_last_answer("What is the boiling point?", reply)

        self.assertEqual(said, reply)
        self.assertEqual(self.engine.client.calls, 0)

    def test_a_follow_up_is_asked_again_as_the_whole_question(self):
        # Phase 3 final rerun, nine times out of nine: "얼마나 우려야 돼?"
        # after a cold-brew answer rightly repeated its 12 hours, was asked
        # again bare -- and a bare "얼마나 우려야 돼?" came back as how
        # worried to be, or how long to tumble-dry clothes.
        self.engine._turn_language = "ko"
        before = "차가운 물과 원두를 섞어 실온에서 12시간 이상 우려내면 됩니다."
        self.engine._things_she_has_said = lambda: (before, [])

        class Asked:
            def __init__(self):
                self.questions = []

            def chat(self, **kwargs):
                prompt = str(kwargs.get("messages"))
                self.questions.append(prompt)
                if "cold brew" in prompt:
                    return {"message": {"content": "콜드브루는 12시간에서 24시간 정도 우리면 됩니다."}}
                return {"message": {"content": "어떤 옷인지에 따라 달라집니다."}}

        self.engine.client = Asked()
        with contextlib.redirect_stdout(io.StringIO()):
            said = self.engine._not_her_last_answer(
                "얼마나 우려야 돼?", "실온에서 12시간 이상 우려내면 됩니다.",
                question="How long should cold brew coffee be steeped?",
            )

        self.assertIn("cold brew", self.engine.client.questions[0])
        self.assertNotIn("옷", said)

    def test_without_a_router_reading_the_words_said_are_asked(self):
        self.engine._turn_language = "ko"
        before = "물의 동결점은 32도 화씨입니다."
        self.engine._things_she_has_said = lambda: (before, [])
        self.engine.client = RetryClient("물의 끓는점은 100도입니다.")
        with contextlib.redirect_stdout(io.StringIO()):
            said = self.engine._not_her_last_answer("물은 몇 도에서 끓어?", before, question="  ")

        self.assertEqual(said, "물의 끓는점은 100도입니다.")

    def test_a_second_attempt_that_repeats_too_is_not_used(self):
        self.engine._turn_language = "ko"
        before = "물의 동결점은 32도 화씨입니다."
        self.engine._things_she_has_said = lambda: (before, [])
        self.engine.client = RetryClient(before)
        with contextlib.redirect_stdout(io.StringIO()):
            said = self.engine._not_her_last_answer("물은 몇 도에서 끓어?", before)

        self.assertEqual(said, before)


class ACorrectedNumberIsNotAMangledOneTests(unittest.TestCase):

    def test_the_correction_is_kept(self):
        from brain.grounded_values import _mangled_numbers

        self.assertEqual(
            _mangled_numbers("물은 0도에서 얼고, 냉동실은 영하 18도입니다.",
                             "물은 100도에서 얼잖아, 그럼 냉동실은 몇 도야?"),
            set(),
        )

    def test_a_dropped_digit_is_still_caught(self):
        from brain.grounded_values import _mangled_numbers

        self.assertEqual(
            _mangled_numbers("Your budget is 150.", "My budget is 1500. Repeat that back."),
            {"150"},
        )


if __name__ == "__main__":
    unittest.main()
