"""Imperatives reached the 습니다체 converter too.

The register table listed 주세요 / 하세요 / 보세요 as three special cases.
They were instances of a rule: 세요 and 십시오 attach after the same
honorific 시-, so the swap needs no stem surgery, and the general form was
the largest single Korean failure class left.
"""

import unittest

from brain import korean_register




class ImperativesReachedTheRegisterTooTests(unittest.TestCase):
    """The largest single Korean failure class, and it was ours.

    Measured on an unseen Korean dogfood arc: register_drift on 7 of 12
    turns, and forms like "갖으세요" were most of it. The table listed
    주세요 / 하세요 / 보세요 as three special cases; they were instances of
    a rule. 세요 and 십시오 attach after the same honorific 시-, so the
    swap needs no stem surgery.
    """

    def test_any_imperative_becomes_십시오(self):
        for said, expected in (
            ("휴식 시간 잘 갖으세요.", "휴식 시간 잘 갖으십시오."),
            ("안녕히 주무세요.", "안녕히 주무십시오."),
            ("천천히 드세요.", "천천히 드십시오."),
            ("확인해 보세요.", "확인해 보십시오."),
            ("말씀해 주세요.", "말씀해 주십시오."),
        ):
            with self.subTest(said=said):
                self.assertEqual(korean_register.to_formal(said), expected)

    def test_the_honorific_copula_is_not_an_order(self):
        """"선생님이세요" is "you are a teacher", not "be a teacher"."""
        self.assertEqual(
            korean_register.to_formal("그분은 선생님이세요."),
            "그분은 선생님이십니다.",
        )
        self.assertEqual(
            korean_register.to_formal("사장님 아니세요."), "사장님 아니십니다.",
        )

    def test_a_question_is_still_left_alone(self):
        """"잘 지내세요?" takes 지내십니까, not an imperative."""
        for said in ("잘 지내세요?", "오늘은 어떻게 보내고 있나요?"):
            with self.subTest(said=said):
                self.assertEqual(korean_register.to_formal(said), said)

    def test_the_fixed_greetings_are_still_left_alone(self):
        for said in ("안녕하세요.", "안녕히 계세요.", "어서 오세요."):
            with self.subTest(said=said):
                self.assertEqual(korean_register.to_formal(said), said)

    def test_the_farewell_she_actually_produces(self):
        self.assertEqual(
            korean_register.to_formal("잘자요."), "안녕히 주무십시오.",
        )


class NaturalAcknowledgementIsNotDriftTests(unittest.TestCase):
    """~군요 was counted as a register failure and is not one.

    16 of 23 findings across three runs of one arc were this ending. It is
    the ordinary way to acknowledge what someone has just told you, and it
    is natural in 습니다체 speech -- confirmed by the Korean speaker this
    is built for.

    Recorded because loosening a detector makes a number go up, and the
    justification has to be that the sentences were always fine rather
    than that the score was always low. The check on that is the second
    test: the forms it still rejects are ones a 비서 really would not use.
    """

    def test_acknowledgement_passes(self):
        from brain.conversation_style import _drifts_from_the_register

        for said in (
            "회사에서 하루 종일 회의만 했군요.",
            "그 드라마는 마음에 들지 않으셨군요.",
            "오늘 힘드셨군요.",
            "영화를 말씀하시는 거군요.",
        ):
            with self.subTest(said=said):
                self.assertEqual(_drifts_from_the_register(said), "", said)

    def test_real_drift_is_still_caught(self):
        from brain.conversation_style import _drifts_from_the_register

        for said in (
            "오늘은 어떻게 지내?",      # 반말
            "흥미로워요.",              # 해요체
            "어떤 영화였는지 궁금해요?",
            "도와줄게.",
        ):
            with self.subTest(said=said):
                self.assertTrue(_drifts_from_the_register(said), said)


class TheAuditedNaturalFormsTests(unittest.TestCase):
    """Every Korean sentence this detector had ever flagged, grouped and
    judged by the Korean speaker this is built for.

    Four families were put to them; three came back natural. The list
    below is that answer, and the second test is what keeps it from
    becoming "accept everything": the forms they did *not* pick are still
    rejected.
    """

    NATURAL = (
        "오늘은 어떻게 보내고 있나요?",   # ~나요?  softer polite question
        "도와드릴 일은 있으신가요?",      # ~(으)신가요?
        "코미디가 좋으신가요?",
        "좋은 시작이네요.",              # ~네요   mild remark
        "영화 추천이 어렵네요.",
        "회사에서 회의만 했군요.",        # ~군요   acknowledgement
    )

    STILL_DRIFT = (
        "피로가 느껴지시나 봐요.",        # ~나 봐요 -- offered and declined
        "오늘 하루 힘들었나 보죠.",       # ~보죠
        "집에 가요.",                    # 가요 as a statement, not a question
        "흥미로워요.",                   # plain 해요체
        "오늘 길었어.",                  # 반말
        "도와줄게.",
    )

    def test_the_confirmed_forms_are_not_drift(self):
        from brain.conversation_style import _drifts_from_the_register

        for said in self.NATURAL:
            with self.subTest(said=said):
                self.assertEqual(_drifts_from_the_register(said), "", said)

    def test_what_was_not_confirmed_is_still_drift(self):
        """The audit widened the rule; it did not switch it off."""
        from brain.conversation_style import _drifts_from_the_register

        for said in self.STILL_DRIFT:
            with self.subTest(said=said):
                self.assertTrue(_drifts_from_the_register(said), said)

    def test_가요_is_only_natural_as_a_question(self):
        """~할까요? was already accepted and ~신가요? was not, which was an
        inconsistency. Fixing it must not accept 해요체 statements."""
        from brain.conversation_style import _drifts_from_the_register

        self.assertEqual(_drifts_from_the_register("있으신가요?"), "")
        self.assertTrue(_drifts_from_the_register("집에 가요."))


class KoreanReachesTheCuratedPathsTests(unittest.TestCase):
    """A greeting is the one turn with a hand-written answer, and Korean
    was the one language that never reached it.

    ``_SIMPLE_GREETING`` listed only English greetings, so "안녕" went to
    the model instead of to the bank, and the model opened the
    conversation in 반말: "오늘은 어떻게 지내?". The register converter
    could not rescue it either -- it skips questions by design.
    """

    def test_korean_greetings_take_the_bank_path(self):
        from brain.chat_engine import _SIMPLE_GREETING

        for said in ("안녕", "안녕하세요", "안녕하십니까", "하이",
                     "좋은 아침", "안녕!"):
            with self.subTest(said=said):
                self.assertTrue(_SIMPLE_GREETING.match(said), said)

    def test_english_greetings_still_do(self):
        from brain.chat_engine import _SIMPLE_GREETING

        for said in ("hey", "hello", "good morning"):
            with self.subTest(said=said):
                self.assertTrue(_SIMPLE_GREETING.match(said))

    def test_a_greeting_with_a_request_behind_it_does_not(self):
        from brain.chat_engine import _SIMPLE_GREETING

        for said in (
            "안녕, 날씨 어때?",
            "안녕하세요 저녁 뭐 먹을까요",
            "hello, can you check Zillow?",
            "안녕히 계세요",          # a farewell, not a greeting
        ):
            with self.subTest(said=said):
                self.assertIsNone(_SIMPLE_GREETING.match(said), said)


class TheKoreanStatusBankSoundsLikeHerTests(unittest.TestCase):
    """"그건 알아보겠습니다" pointed at nothing.

    The pool is generic, so the demonstrative referred to no particular
    thing and only made the line stiff. 한번 is what softens an offer to
    go and look, and 검색 is the natural verb when a search is what
    answers the question.
    """

    def test_the_softened_forms_are_there(self):
        from brain.action_status import _KO_EXECUTION

        searching = _KO_EXECUTION["searching"]
        for wanted in ("한번 알아보겠습니다.", "한번 찾아보겠습니다.",
                       "검색해보겠습니다."):
            with self.subTest(line=wanted):
                self.assertIn(wanted, searching)

    def test_the_stiff_one_is_gone(self):
        from brain.action_status import _KO_EXECUTION

        self.assertNotIn("그건 알아보겠습니다.", _KO_EXECUTION["searching"])

    def test_every_korean_status_line_holds_the_register(self):
        """So a 해요체 line cannot be added later without noticing."""
        from brain.action_status import _KO_EXECUTION, _KO_HEDGED
        from brain.conversation_style import _drifts_from_the_register

        lines = [line for pool in _KO_EXECUTION.values() for line in pool]
        lines.extend(_KO_HEDGED)
        self.assertGreater(len(lines), 40)
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(_drifts_from_the_register(line), "", line)


class AGreetingSetsTheLanguageTests(unittest.TestCase):
    """Short enough to be filtered out, and the one turn that must not be.

    The switch floor exists because "ok" and "네" are the most frequent
    turns there are and evidence of nothing. It cannot separate them from
    a greeting by length -- "안녕" is two syllables and "고마워" is three,
    and only the second is an acknowledgement.

    The difference is what the turn *does*: an acknowledgement answers
    inside a conversation someone is already having, a greeting starts
    one, and you start it in the language you mean to have it in.

    Measured: with "안녕" leaving the turn in English, routing a Korean
    hello to the curated greeting bank produced "Hello. Ready when you
    are." The same held in reverse for a bare "hi".
    """

    def test_a_greeting_sets_the_language_either_way(self):
        from brain.turn_language import decide

        for said, current, wanted in (
            ("안녕", "en", "ko"),
            ("안녕하세요", "en", "ko"),
            ("하이", "en", "ko"),
            ("hi", "ko", "en"),
            ("hello", "ko", "en"),
            ("good morning", "ko", "en"),
        ):
            with self.subTest(said=said):
                self.assertEqual(decide(said, current=current).language, wanted)

    def test_the_acknowledgement_floor_is_untouched(self):
        """The exception is about what the turn does, not about length."""
        from brain.turn_language import decide

        for said in ("응", "네", "고마워", "알겠어", "ok", "yeah"):
            with self.subTest(said=said):
                self.assertEqual(decide(said, current="en").language, "en")
                self.assertEqual(decide(said, current="ko").language, "ko")

    def test_code_switching_still_does_not_flip_her(self):
        from brain.turn_language import decide

        self.assertEqual(decide("그 monitor 어때?", current="ko").language, "ko")
        self.assertEqual(
            decide("let's eat 삼겹살 tonight", current="en").language, "en",
        )
