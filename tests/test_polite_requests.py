"""A request phrased as a question is still a request.

Found live, and it made the assistant unusable for a day: "can you close
Spotify" was answered with "Yes. I can open, close, and force-quit Windows
apps... Want me to use it now?" -- every time, for every politely-phrased
instruction. The interception exists for a good reason (a model asked what
it can do will confidently deny abilities it has), but the literal reading
of "can you X" is the pedantic one. A person hearing it does the thing.
"""

import unittest

from brain.capabilities import CapabilityRegistry


class PoliteRequestTests(unittest.TestCase):
    def test_a_polite_instruction_is_not_a_question_about_abilities(self):
        for text in (
            "can you close spotify",
            "could you close spotify?",
            "can you open discord",
            "can you play Bang Bang by IVE in Spotify",
            "can you search for hotels in Guam",
            "could you type my address in Notepad",
        ):
            with self.subTest(text=text):
                self.assertFalse(CapabilityRegistry.is_ability_question(text))

    def test_a_real_question_about_abilities_still_is_one(self):
        for text in (
            "what can you do",
            "can you control my browser",
            "are you able to click things",
            "do you have web search",
            "뭐 할 수 있어",
            "what are your capabilities",
        ):
            with self.subTest(text=text):
                self.assertTrue(CapabilityRegistry.is_ability_question(text))

    def test_the_inventory_question_survives_a_named_app(self):
        # "What can you do in Spotify" asks about the inventory, however
        # many nouns follow it.
        self.assertTrue(
            CapabilityRegistry.is_ability_question("what can you do in Spotify")
        )

    def test_a_bare_instruction_was_never_a_question(self):
        for text in ("close spotify", "open discord", "play some music"):
            with self.subTest(text=text):
                self.assertFalse(CapabilityRegistry.is_ability_question(text))


if __name__ == "__main__":
    unittest.main()
