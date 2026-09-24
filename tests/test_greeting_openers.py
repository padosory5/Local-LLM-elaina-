"""A reply to a hello opens with a hello.

From a replay of a real Korean session. "안녕" was answered

    네, 어디부터 할까요?

-- "Yes, where shall we start?" That is the reply to a request, not to a
greeting: it opens by agreeing to something nobody asked for. Every English
line in the same bank opens with a greeting word ("Hello.", "Good
morning.", "Still up?"); two of the Korean ones opened with "네,", so a
Korean hello was answered as if it had been an instruction.

Asserted over every line in both banks, so a line added later in either
language is held to the same rule.
"""

from __future__ import annotations

import unittest

from brain.social_lines import _BANKS


# How a greeting in each language opens. "Still up?" / "아직 안
# 주무셨습니까?" and "It's late." / "늦은 시간입니다." are greetings by time
# of night, the way a person says them, and count.
_OPENS_A_GREETING = {
    "en": ("Hello", "Hi", "Good ", "Morning", "Afternoon", "Evening",
           "Still ", "It's late"),
    "ko": ("안녕", "좋은 ", "아직 안", "늦은 시간"),
}

# An answer to a request. Never how a greeting starts, in either language.
_AGREES_TO_SOMETHING = {
    "en": ("Yes", "Sure", "Okay", "OK", "Alright"),
    "ko": ("네", "예,", "알겠", "그럼"),
}


class EveryGreetingOpensWithAGreetingTests(unittest.TestCase):

    def test_both_languages_have_banks(self):
        self.assertEqual(set(_BANKS), {"en", "ko"})

    def test_no_line_opens_by_agreeing_to_something(self):
        for language, parts in _BANKS.items():
            for part, lines in parts.items():
                for line in lines:
                    with self.subTest(language=language, part=part, line=line):
                        self.assertFalse(
                            line.startswith(_AGREES_TO_SOMETHING[language]),
                            f"{line!r} answers a request, not a hello",
                        )

    def test_every_line_opens_with_a_greeting(self):
        for language, parts in _BANKS.items():
            for part, lines in parts.items():
                for line in lines:
                    with self.subTest(language=language, part=part, line=line):
                        self.assertTrue(
                            line.startswith(_OPENS_A_GREETING[language]),
                            f"{line!r} does not open as a greeting",
                        )


if __name__ == "__main__":
    unittest.main()
