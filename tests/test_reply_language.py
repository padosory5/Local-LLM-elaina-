"""What she says comes back in the language the person just used.

The person's rule for a two-language conversation: answer in Korean when I
speak Korean, in English when I speak English. The language decision
follows it; what was missing was any check on the reply itself. Measured on
the final mixed verification run:

    You:     김치찌개 만드는 법 알려줘
    Elaina:  김치찌개는 김치, 고기, ... 끓여 만듭니다.
    You:     can you make it shorter?
    Elaina:  김치찌개는 김치, 고기, ... 끓여 만듭니다.

Asked to shorten her own Korean answer, the model did it in Korean.

And the first version of the fix failed in a way this file now pins down.
Live, the one time it fired, the model answered the rewrite brief instead
of rewriting: "Do not mention the draft or the language." That is English,
so a check on the script alone spoke it. A re-say is now accepted only if it
is the answer rather than the request.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain import korean_register
from tests.turn_harness import build_engine

KOREAN = "김치찌개는 김치와 고기, 마늘을 함께 끓여 만듭니다."
ENGLISH = "Kimchi stew is kimchi, pork and garlic, simmered together."


class _Engine(unittest.TestCase):

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = build_engine({})

    def tearDown(self):
        self.engine.close()

    def _check(self, reply: str) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            return self.engine._answered_in_the_turns_language(
                reply, model="test", keep_alive=0, max_words=60,
            )


class TheReplyFollowsTheTurnTests(_Engine):

    def test_a_korean_reply_to_an_english_turn_is_said_again_in_english(self):
        self.engine._turn_language = "en"
        self.engine.client.reply = ENGLISH

        self.assertEqual(self._check(KOREAN), ENGLISH)

    def test_an_english_reply_to_a_korean_turn_is_said_again_in_korean(self):
        self.engine._turn_language = "ko"
        self.engine.client.reply = KOREAN

        self.assertEqual(self._check(ENGLISH), KOREAN)

    def test_korean_that_arrives_late_still_gets_her_register(self):
        # The register pass has already run by the time this does.
        self.engine._turn_language = "ko"
        self.engine.client.reply = "김치찌개는 김치랑 고기로 만들어요."

        said = self._check(ENGLISH)

        self.assertEqual(
            said, korean_register.to_formal("김치찌개는 김치랑 고기로 만들어요."),
        )

    def test_a_reply_already_in_the_right_language_costs_nothing(self):
        for language, reply in (("en", ENGLISH), ("ko", KOREAN)):
            with self.subTest(language=language):
                self.engine._turn_language = language
                before = len(self.engine.client.calls)

                self.assertEqual(self._check(reply), reply)
                self.assertEqual(len(self.engine.client.calls), before)

    def test_a_korean_reply_carrying_an_english_name_is_korean(self):
        # Code-switching inside a reply is not the wrong language: the
        # sentence is built in Korean.
        self.engine._turn_language = "ko"
        reply = "Bainbridge Island에는 카지노가 없습니다."
        before = len(self.engine.client.calls)

        self.assertEqual(self._check(reply), reply)
        self.assertEqual(len(self.engine.client.calls), before)


class AReSayThatIsNotAnAnswerIsNotSpokenTests(_Engine):
    """Each of these keeps the original rather than speaking the re-say."""

    def _keeps_the_original(self, re_say: str) -> None:
        self.engine._turn_language = "en"
        self.engine.client.reply = re_say

        self.assertEqual(self._check(KOREAN), KOREAN)

    def test_the_measured_leak(self):
        self._keeps_the_original("Do not mention the draft or the language.")

    def test_repeating_the_instruction(self):
        self._keeps_the_original(
            "Output only the translation. Kimchi stew is kimchi and pork.",
        )

    def test_talking_about_the_translation(self):
        self._keeps_the_original(
            "Here is the translation: Kimchi stew is kimchi and pork.",
        )

    def test_still_the_wrong_language(self):
        self._keeps_the_original("여전히 한국어로 답합니다.")

    def test_nothing(self):
        self._keeps_the_original("")


if __name__ == "__main__":
    unittest.main()
