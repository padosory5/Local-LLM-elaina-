"""Which language a turn is answered in.

The cases here are the ones agreed before any of it was written, because a
language rule is easy to make plausible and hard to make right: the failure
mode is not "wrong language" but "flips constantly", and that only shows up
across a conversation.
"""

import unittest

from brain import turn_language
from brain.personality_loader import PersonalityLoader
from brain.turn_language import (
    ENGLISH,
    KOREAN,
    decide,
    reads_as_language_request,
    script_language,
)


class ScriptLanguageTests(unittest.TestCase):

    def test_a_plain_sentence_in_each_language(self):
        self.assertEqual(script_language("hey what is up"), ENGLISH)
        self.assertEqual(script_language("오늘 날씨 어때?"), KOREAN)

    def test_a_korean_sentence_with_an_english_noun_is_korean(self):
        # Code-switching is normal Korean and must not read as English.
        for said in ("그 monitor 어때?", "이 laptop 사고 싶어", "그 hotel 예약해줘"):
            self.assertEqual(script_language(said), KOREAN, said)

    def test_an_english_sentence_with_a_korean_noun_is_english(self):
        for said in ("let's eat 삼겹살 tonight", "I love 김치"):
            self.assertEqual(script_language(said), ENGLISH, said)

    def test_an_emoticon_is_not_evidence(self):
        # ㅋㅋ and ㅠㅠ are jamo, not syllables, and say nothing about which
        # language the sentence is built in.
        self.assertEqual(script_language("that is so funny ㅋㅋ"), ENGLISH)

    def test_nothing_to_go_on(self):
        self.assertEqual(script_language(""), "")
        self.assertEqual(script_language("123 456"), "")


class LanguageRequestTests(unittest.TestCase):

    def test_asking_in_the_other_language_still_counts(self):
        # The request is Korean; the answer it asks for is English.
        self.assertEqual(reads_as_language_request("영어로 말해줘"), ENGLISH)
        self.assertEqual(reads_as_language_request("speak korean please"), KOREAN)

    def test_the_common_phrasings(self):
        for said, wanted in (
            ("can you speak in english", ENGLISH),
            ("english please", ENGLISH),
            ("한국어로 말해줘", KOREAN),
            ("한국말로", KOREAN),
            ("영어로 부탁해", ENGLISH),
        ):
            self.assertEqual(reads_as_language_request(said), wanted, said)

    def test_a_translation_question_does_not_repin_her(self):
        # "How do you say this in Korean?" is a question about a word, not
        # an instruction about the conversation.
        for said in (
            "how do you say this in korean",
            "what is 사과 in english",
            "korean food is great",
        ):
            self.assertEqual(reads_as_language_request(said), "", said)


class StickinessTests(unittest.TestCase):
    """The rule that matters most: she must not flip constantly."""

    def test_a_short_turn_never_switches(self):
        # "ok", "네", "yeah" are the most frequent turns in real use and
        # evidence of nothing.
        self.assertEqual(decide("ok", current=KOREAN).language, KOREAN)
        self.assertEqual(decide("응", current=ENGLISH).language, ENGLISH)
        self.assertEqual(decide("yeah", current=KOREAN).language, KOREAN)

    def test_a_full_turn_switches(self):
        chosen = decide("can you check the weather for me", current=KOREAN)

        self.assertEqual(chosen.language, ENGLISH)
        self.assertTrue(chosen.switched)

    def test_code_switching_does_not_flip_her(self):
        self.assertEqual(decide("그 monitor 어때?", current=KOREAN).language,
                         KOREAN)
        self.assertEqual(decide("let's eat 삼겹살 tonight", current=ENGLISH)
                         .language, ENGLISH)

    def test_an_explicit_request_pins_and_keeps_winning(self):
        asked = decide("영어로 말해줘", current=KOREAN)
        self.assertEqual(asked.language, ENGLISH)
        self.assertTrue(asked.pinned)

        # And a later Korean sentence does not undo it.
        after = decide("오늘 날씨 어때?", current=ENGLISH, pinned=ENGLISH)
        self.assertEqual(after.language, ENGLISH)
        self.assertTrue(after.pinned)

    def test_a_new_request_replaces_the_pin(self):
        chosen = decide("한국어로 말해줘", current=ENGLISH, pinned=ENGLISH)

        self.assertEqual(chosen.language, KOREAN)
        self.assertTrue(chosen.pinned)


class HeardVersusWrittenTests(unittest.TestCase):

    def test_a_confident_microphone_breaks_the_tie(self):
        # Transcribed into Latin script, but Whisper is sure it heard
        # Korean -- a transcription fault, not a language change.
        chosen = decide(
            "annyeong haseyo jal jinaess eoyo", current=KOREAN,
            detected=KOREAN, probability=0.95,
        )

        self.assertEqual(chosen.language, KOREAN)

    def test_an_unsure_microphone_cannot_override_the_script(self):
        # The microphone thinks it heard Korean and is not sure. The words
        # on the page are plainly English, and they are the better
        # evidence: Whisper writes Korean speech in Hangul, so a Latin
        # transcript that survived the retry is usually really English.
        chosen = decide(
            "could you look that up for me", current=ENGLISH,
            detected=KOREAN, probability=0.2,
        )

        self.assertEqual(chosen.language, ENGLISH)

    def test_agreement_switches_normally(self):
        chosen = decide(
            "could you look that up for me", current=KOREAN,
            detected=ENGLISH, probability=0.99,
        )

        self.assertEqual(chosen.language, ENGLISH)
        self.assertTrue(chosen.switched)


class OnePersonTwoRenderingsTests(unittest.TestCase):
    """The two personality files describe the same character.

    They did not, before this. Korean described a close friend speaking
    반말 and carried an examples section English lacked; English carried
    three sections Korean lacked. Nobody decided that -- it happened one
    edit at a time, because nothing compared them.
    """

    def setUp(self):
        self.loader = PersonalityLoader()

    def test_both_files_have_the_same_sections_in_the_same_order(self):
        self.assertEqual(
            list(self.loader.sections(ENGLISH)),
            list(self.loader.sections(KOREAN)),
        )

    def test_every_section_carries_the_same_number_of_rules(self):
        english = self.loader.sections(ENGLISH)
        korean = self.loader.sections(KOREAN)

        for section, rules in english.items():
            with self.subTest(section=section):
                self.assertEqual(
                    len(rules), len(korean[section]),
                    f"{section} has {len(rules)} rules in English and "
                    f"{len(korean[section])} in Korean",
                )

    def test_both_demonstrate_the_same_number_of_exchanges(self):
        self.assertEqual(
            self.loader.example_count(ENGLISH),
            self.loader.example_count(KOREAN),
        )

    def test_neither_file_is_empty_of_the_sections_that_carry_safety(self):
        for language in (ENGLISH, KOREAN):
            with self.subTest(language=language):
                self.assertTrue(self.loader.sections(language)["SAFETY"])


class SwitchingTheLineBanksTests(unittest.TestCase):
    """A component holding its own language bank has to be told."""

    def test_the_status_selector_follows_the_language(self):
        import random

        from brain.action_status import ActionStatusSelector, StatusContext

        selector = ActionStatusSelector(language="en", rng=random.Random(2))
        selector.speak_in("ko")
        line = selector.select(StatusContext(phase="closing", force=True))

        self.assertTrue(any("가" <= ch <= "힣" for ch in line), line)

    def test_the_greeting_selector_follows_the_language(self):
        from brain.social_lines import SocialLineSelector

        selector = SocialLineSelector(language="en")
        selector.speak_in("ko")

        self.assertTrue(
            any("가" <= ch <= "힣" for ch in selector.greeting("안녕하세요"))
        )

    def test_switching_forgets_the_other_bank(self):
        # Recency is about *these* lines. Carried across a switch it would
        # bar a Korean line because an English one was said, and let the
        # first Korean line repeat freely.
        import random

        from brain.action_status import ActionStatusSelector, StatusContext

        selector = ActionStatusSelector(language="en", rng=random.Random(5))
        selector.select(StatusContext(phase="closing", force=True))
        self.assertTrue(selector.recent)

        selector.speak_in("ko")
        self.assertFalse(selector.recent)


if __name__ == "__main__":
    unittest.main()


class KoreanRegisterTests(unittest.TestCase):
    """습니다체, enforced rather than requested.

    personality_ko.txt specifies it and qwen3:8b does not hold it: measured
    live, most generated Korean sentences came back 해요체, and one rewrite
    turned 해요체 into 반말 and passed a review that only knew about 해요체.

    The rule is therefore an allowlist of what is correct, not a blocklist
    of what is wrong -- an unusual valid ending costs one extra model call,
    while an unusual invalid one used to cost nothing and ship.
    """

    def test_the_formal_register_passes(self):
        from brain.conversation_style import _drifts_from_the_register

        for said in (
            "15%의 84는 12.60입니다.",
            "런던 시간은 오후 3시 13분입니다.",
            "도움이 되었다면 다행입니다.",
            "말씀해 주시겠습니까?",
            "잠시만 기다려 주십시오.",
            "네.",
        ):
            self.assertEqual(_drifts_from_the_register(said), "", said)

    def test_a_permission_question_is_part_of_the_register(self):
        # "진행할까요?" is how this register asks, and it appears in her own
        # line banks. Flagging it would condemn the thing it enforces.
        from brain.conversation_style import _drifts_from_the_register

        for said in ("진행할까요?", "확인해 드릴까요?", "한번 알아볼까요?"):
            self.assertEqual(_drifts_from_the_register(said), "", said)

    def test_a_lexicalised_greeting_is_not_drift(self):
        # 안녕하세요 is -세요 by shape and is the standard polite greeting in
        # every register. A rule about grammar has to know which phrases
        # stopped being grammar.
        from brain.conversation_style import _drifts_from_the_register

        self.assertEqual(
            _drifts_from_the_register("안녕하세요. 무엇을 도와드릴까요?"), "",
        )

    def test_haeyo_is_caught(self):
        """Plain 해요체, which is drift.

        "퇴근하셨다니 힘들었겠네요." used to be asserted here too, and it
        is not drift. ~네요 was put to the Korean speaker this is built
        for during the detector audit -- with that very sentence as the
        example -- and came back natural, along with ~군요, ~나요? and
        ~(으)신가요?. See tests/test_korean_register.py, which holds the
        whole answer and the forms it did *not* cover.
        """
        from brain.conversation_style import _drifts_from_the_register

        for said in ("고마워요.", "천만에요.", "좋아요."):
            self.assertTrue(_drifts_from_the_register(said), said)

    def test_banmal_is_caught(self):
        # The one the first attempt missed, and the reason it matters: a
        # rewrite offered 반말 in place of 해요체 and went out clean.
        from brain.conversation_style import _drifts_from_the_register

        for said in ("쉬고 있어.", "필요하면 언제든 도와줄게.", "알겠어 해볼게."):
            self.assertTrue(_drifts_from_the_register(said), said)

    def test_english_is_never_judged_by_the_korean_rule(self):
        from brain.conversation_style import RoboticTells

        found = RoboticTells.inspect(
            "The Logitech M330 is forty five dollars.",
            language="en",
        )
        self.assertNotIn(
            "register_drift", {finding.failure for finding in found},
        )

    def test_every_korean_line_she_can_say_satisfies_her_own_rule(self):
        """Her banks must pass the guard the banks' own register defines.

        The invariant that catches a bank edited without the rule in mind,
        which is how "별말씀을요." and "아직 안 주무셨군요." got written.
        """
        import brain.action_status as status
        import brain.recommendation as offers
        import brain.social_lines as social
        from brain.conversation_style import _drifts_from_the_register

        lines: list[str] = []
        for bank in (status._KO_EXECUTION, status._KO_PHASES):
            for group in bank.values():
                lines.extend(group)
        lines.extend(status._KO_HEDGED)
        for group in social._BANKS["ko"].values():
            lines.extend(group)
        lines.extend(
            template.format(what="모니터")
            for template in offers._PHRASINGS["ko"]
        )

        drifting = [line for line in lines if _drifts_from_the_register(line)]
        self.assertEqual(drifting, [])


class EachLanguageIsMeasuredInItsOwnUnitTests(unittest.TestCase):
    """A word count calibrated on English refuses to switch on Korean.

    Measured live: "방금 퇴근했어" and "좀 피곤하네" are complete Korean
    sentences and both are two words, so a three-word floor kept her
    answering a Korean conversation in English for three turns.
    """

    def test_a_short_korean_sentence_still_switches(self):
        for said in ("방금 퇴근했어", "좀 피곤하네", "오늘 길었어"):
            with self.subTest(said=said):
                self.assertEqual(decide(said, current=ENGLISH).language, KOREAN)

    def test_the_acknowledgements_still_do_not(self):
        # The whole point of the floor. These must stay put in either
        # direction, which is what sets the syllable threshold.
        for said in ("응", "네", "고마워", "알겠어"):
            with self.subTest(said=said):
                self.assertEqual(decide(said, current=ENGLISH).language,
                                 ENGLISH)
                self.assertEqual(decide(said, current=KOREAN).language, KOREAN)


class FormalRegisterConversionTests(unittest.TestCase):
    """해요체 into 습니다체, mechanically, where the ending is known.

    The prompt did not hold the register and asking for the line again
    mostly returned 해요체 a second time -- once 반말. This is the project's
    usual answer to that: once a behaviour is confirmed live, a
    deterministic guard, not more wording.
    """

    def test_the_measured_endings_convert(self):
        from brain.korean_register import to_formal

        for said, wanted in (
            ("필요하시면 언제든 도와드릴게요.", "필요하시면 언제든 도와드리겠습니다."),
            ("배울 수 있어요.", "배울 수 있습니다."),
            ("영상이 인상적이에요.", "영상이 인상적입니다."),
            ("보시는 게 더 좋아요.", "보시는 게 더 좋습니다."),
            ("말씀해 주세요.", "말씀해 주십시오."),
            ("좋은 시작이네요.", "좋은 시작입니다."),
            ("쉬고 있어야 해요.", "쉬고 있어야 합니다."),
        ):
            with self.subTest(said=said):
                self.assertEqual(to_formal(said), wanted)

    def test_a_lexicalised_greeting_is_not_converted(self):
        # 안녕하세요 is -세요 by shape, and the -세요 rule turned it into
        # "안녕하십시오", which is a word and is not what anyone says.
        from brain.korean_register import to_formal

        for said in ("안녕하세요. 무엇을 도와드릴까요?", "안녕히 가세요."):
            with self.subTest(said=said):
                self.assertEqual(to_formal(said), said)

    def test_what_it_does_not_know_it_leaves_alone(self):
        # Half-converted Korean is worse than politely wrong Korean, so
        # anything needing real verb morphology is left for the re-say.
        from brain.korean_register import to_formal

        for said in ("고마워요.", "오늘은 어떤 하루 보내고 있나요?", "그렇군요."):
            with self.subTest(said=said):
                self.assertEqual(to_formal(said), said)

    def test_it_never_touches_english(self):
        from brain.korean_register import to_formal

        said = "The Logitech M330 is forty five dollars."
        self.assertEqual(to_formal(said), said)

    def test_only_the_drifting_clause_changes(self):
        from brain.korean_register import to_formal

        said = "런던은 오후 3시입니다. 한국보다 8시간 느려요."
        self.assertEqual(
            to_formal(said), "런던은 오후 3시입니다. 한국보다 8시간 느립니다.",
        )

    def test_numbers_and_names_survive(self):
        # The safety argument for doing this deterministically: every entry
        # is a change of politeness form, never of content.
        from brain.korean_register import to_formal

        said = "LG의 27인치 4K 모니터는 45달러예요."
        converted = to_formal(said)
        for value in ("LG", "27", "4K", "45"):
            self.assertIn(value, converted)
        self.assertTrue(converted.endswith("입니다."))


class TheAudioBoundaryFollowsTheTurnTests(unittest.TestCase):
    """She answered in Korean and then refused to say it out loud.

    Reported from real use: every Korean reply came out of the speakers as
    "The result is shown on screen." ``AudioManager`` read the response
    language once from config.yaml at construction -- the same fault
    ChatEngine had, in the one place where it silences her --  and
    ``for_configured_speech`` strips Hangul when that language is English.
    """

    def test_the_filter_keeps_korean_when_the_turn_is_korean(self):
        from brain.text_filter import TextFilter

        said = "컴퓨터 본체가 너무 크다면 27인치 모니터를 추천드립니다."

        self.assertEqual(
            TextFilter.for_configured_speech(said, response_language="ko"),
            said,
        )
        self.assertEqual(
            TextFilter.for_configured_speech(said, response_language="en"),
            "The result is shown on screen.",
        )

    def test_the_audio_manager_can_be_told(self):
        import inspect

        from voice.audio_manager import AudioManager

        self.assertTrue(hasattr(AudioManager, "speak_in"))
        source = inspect.getsource(AudioManager.speak)
        self.assertIn("_response_language", source)

    def test_the_engine_tells_it(self):
        import inspect

        from brain.chat_engine import ChatEngine

        source = inspect.getsource(ChatEngine._use_language)
        self.assertIn("speak_in", source)
        self.assertIn("audio", source)


class OpeningWithTheirWordsTests(unittest.TestCase):
    """A reply that starts the way the turn started is giving it back.

    Measured live in Korean: "오늘 길었어. 84의 15%가 얼마야?" was answered
    "오늘 길었어. 84의 15%는 12.60입니다." The existing rule looks for a run
    of three content words and Korean sentences are shorter than that, so it
    never fired.

    The threshold is two words on purpose. At one, "오늘 날씨 어때?" answered
    "오늘 날씨는 맑습니다." would be condemned -- the particle makes 날씨는 a
    different token, so only 오늘 matches, and that is a perfectly good
    answer. A rule that damages good answers is worse than a missed echo.
    """

    def test_a_repeated_opening_is_caught(self):
        from brain.conversation_style import RoboticTells

        found = RoboticTells.inspect(
            "오늘 길었어. 84의 15%는 12.60입니다.",
            act="answer",
            user_input="오늘 길었어. 84의 15%가 얼마야?",
            language="ko",
        )
        self.assertIn("request_restated",
                      {finding.failure for finding in found})

    def test_naming_the_subject_is_not_echoing_it(self):
        from brain.conversation_style import RoboticTells

        for reply, said in (
            ("콜드브루는 원두를 찬물에 담가 우려냅니다.", "콜드브루 만드는 법 알아?"),
            ("오늘 날씨는 맑습니다.", "오늘 날씨 어때?"),
            ("15% of 84 is 12.60.", "what's 15% of 84?"),
            ("Cold brew steeps for twelve hours.", "how long should it steep?"),
        ):
            with self.subTest(reply=reply):
                found = RoboticTells.inspect(
                    reply, act="answer", user_input=said,
                    language="ko" if any("가" <= c <= "힣" for c in reply) else "en",
                )
                self.assertNotIn("request_restated",
                                 {finding.failure for finding in found}, reply)


class GuardsSpeakTheTurnsLanguageTests(unittest.TestCase):
    """A guard replaces text; whoever wrote it wrote English.

    Measured across every bilingual live run: 4 of 62 Korean replies
    carried an English sentence, and all four were the same one --

        1080p 해상도와 5ms 응답 시간으로 게임 및 일상 사용에 적합합니다.
        I looked and couldn't find that, so I'd rather not guess.

    Half a reply in the wrong language reads as a fault in her rather than
    in the software.
    """

    def test_every_line_exists_in_both_languages(self):
        from brain import guard_lines

        for name, versions in guard_lines.LINES.items():
            with self.subTest(name=name):
                self.assertTrue(versions.get("en"), name)
                self.assertTrue(versions.get("ko"), name)

    def test_the_korean_lines_are_in_her_register(self):
        # They are her words, so they answer to the same rule her banks do.
        from brain import guard_lines
        from brain.conversation_style import _drifts_from_the_register

        for name in guard_lines.LINES:
            with self.subTest(name=name):
                self.assertEqual(
                    _drifts_from_the_register(guard_lines.say(name, "ko")),
                    "", name,
                )

    def test_an_unknown_language_falls_back_to_english(self):
        from brain import guard_lines

        self.assertEqual(
            guard_lines.say("no_response", "fr"),
            guard_lines.LINES["no_response"]["en"],
        )

    def test_the_engine_asks_for_them_by_language(self):
        import inspect

        from brain.chat_engine import ChatEngine

        source = inspect.getsource(ChatEngine)
        self.assertIn("guard_lines.say(", source)
        self.assertIn("unverified_searched", source)
        self.assertIn("self._turn_language", source)


class AnOfferIsSaidInOneLanguageTests(unittest.TestCase):
    """The offer template follows the turn; its subject came from the router.

    Measured live: "《어바웃 타임》입니다. 극장에서 보시면 좋습니다.
    a movie 확인해 드릴까요?" -- the Korean phrasing with an English subject
    dropped into the middle of it. An offer she cannot say in one language
    is not an offer, so she says nothing instead.
    """

    def test_the_engine_refuses_a_cross_language_subject(self):
        import inspect

        from brain.chat_engine import ChatEngine

        source = inspect.getsource(ChatEngine._offerable_subject)
        self.assertIn("_turn_language", source)
        self.assertIn("Korean", source)
