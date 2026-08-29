"""She answers in the configured language, wherever the user lives.

Reported from a real session: `language.response` was "en", and her opening
line was Korean. That setting only chose which personality *file* loaded --
nothing in any prompt ever named the reply language, while the locale block
said "always answer the user in their own language" directly beneath "The
user is in South Korea". The model did as it was told.
"""

from __future__ import annotations

import unittest

from brain.response_messages import build_personality_messages
from brain.user_locale import UserLocale, language_name
from tests.turn_harness import build_engine


def _korean_locale() -> UserLocale:
    """Where the bug lives: a Korean market, an English reply language."""
    return UserLocale(country="KR")


class LanguageNameTests(unittest.TestCase):
    def test_codes_become_prompt_ready_names(self):
        self.assertEqual(language_name("en"), "English")
        self.assertEqual(language_name("ko"), "Korean")
        self.assertEqual(language_name("EN"), "English")
        self.assertEqual(language_name("en-US"), "English")

    def test_an_unknown_code_is_not_silently_dropped(self):
        self.assertEqual(language_name("ja"), "JA")
        self.assertEqual(language_name(""), "English")


class LocaleContextTests(unittest.TestCase):
    """The block that caused it."""

    def test_a_korean_locale_does_not_ask_for_a_korean_answer(self):
        text = _korean_locale().context_text("en")

        self.assertNotIn("their own language", text)
        self.assertIn("write the answer itself in English", text)

    def test_the_market_language_is_still_stated_as_readable(self):
        # Reading Korean sources is the point of the locale layer; only the
        # reply language was ever wrong.
        text = _korean_locale().context_text("en")

        self.assertIn("South Korea", text)
        self.assertIn("KRW", text)
        self.assertIn("Korean", text)

    def test_nothing_is_said_when_the_two_languages_agree(self):
        text = _korean_locale().context_text("ko")

        self.assertNotIn("write the answer itself", text)

    def test_a_korean_reply_language_is_honoured(self):
        text = UserLocale(country="US").context_text("ko")

        self.assertIn("write the answer itself in Korean", text)


class PromptRuleTests(unittest.TestCase):
    """Every prompt names the answer language, including the rewrite path."""

    def test_the_rule_is_present_and_names_the_language(self):
        messages = build_personality_messages(
            system_prompt="PERSONALITY",
            history=[],
            user_input="안녕",
            response_language="en",
        )
        prompt = messages[-1]["content"]

        self.assertIn("ANSWER LANGUAGE", prompt)
        self.assertIn("Write the entire reply in English", prompt)

    def test_the_greeting_is_covered_explicitly(self):
        # The reported failure was the very first line of the conversation.
        messages = build_personality_messages(
            system_prompt="PERSONALITY",
            history=[],
            user_input="hi",
            response_language="en",
        )

        self.assertIn("including the greeting", messages[-1]["content"])

    def test_korean_evidence_does_not_change_the_reply_language(self):
        messages = build_personality_messages(
            system_prompt="PERSONALITY",
            history=[],
            user_input="who won",
            context_sections=(("CURRENT RETRIEVED EVIDENCE", "스페인이 이겼다"),),
            response_language="en",
        )
        prompt = messages[-1]["content"]

        self.assertIn("Write the entire reply in English", prompt)
        self.assertIn("Translate what you found into English", prompt)

    def test_a_korean_configuration_asks_for_Korean(self):
        messages = build_personality_messages(
            system_prompt="PERSONALITY",
            history=[],
            user_input="hello",
            response_language="ko",
        )

        self.assertIn("Write the entire reply in Korean", messages[-1]["content"])

    def test_the_rule_sits_next_to_the_message_being_answered(self):
        # Instructions far from the request get ignored more often, and the
        # locale block that caused this sat several sections earlier.
        messages = build_personality_messages(
            system_prompt="PERSONALITY",
            history=[],
            user_input="hi",
            response_language="en",
        )
        prompt = messages[-1]["content"]

        self.assertLess(
            prompt.index("ANSWER LANGUAGE"),
            prompt.index("CURRENT USER MESSAGE"),
        )
        between = prompt[
            prompt.index("ANSWER LANGUAGE"):prompt.index("CURRENT USER MESSAGE")
        ]
        self.assertNotIn("USER LOCATION", between)


class EngineWiringTests(unittest.TestCase):
    """The engine passes its configured language, not the locale's."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def test_the_configured_language_reaches_the_answer_prompt(self):
        messages = self.engine._build_tool_result_messages("hi", "done")

        self.assertIn(
            f"Write the entire reply in "
            f"{language_name(self.engine.response_language)}",
            messages[-1]["content"],
        )

    def test_no_prompt_asks_for_the_user_s_own_language(self):
        # The exact wording that produced the Korean greeting.
        context = self.engine._capability_context()

        self.assertNotIn("their own language", context)

    def test_the_locale_block_still_localizes_recommendations(self):
        context = self.engine._capability_context()

        self.assertIn("USER LOCATION", context)


if __name__ == "__main__":
    unittest.main()
