import random
import re
import unittest

from brain.social_lines import SocialLineSelector


class GreetingVarietyTests(unittest.TestCase):
    """The reported bug: every greeting of the session was one sentence.

    Live, over a whole session:

        "hi"           -> "Hey, how are you doing?"
        "hello"        -> "Hey, how are you doing?"
        "good morning" -> "Hey, how are you doing?"
    """

    def selector(self, hour: int = 10, seed: int = 0) -> SocialLineSelector:
        return SocialLineSelector(
            rng=random.Random(seed), clock=lambda: hour,
        )

    def test_a_run_of_greetings_is_not_one_repeated_sentence(self):
        picker = self.selector()

        said = [picker.greeting("hi") for _ in range(8)]

        self.assertGreaterEqual(len(set(said)), 5)

    def test_consecutive_greetings_are_never_identical(self):
        picker = self.selector()

        said = [picker.greeting("hey") for _ in range(12)]

        for earlier, later in zip(said, said[1:]):
            self.assertNotEqual(earlier, later)

    def test_the_recent_window_bars_a_line_from_coming_straight_back(self):
        picker = self.selector()

        said = [picker.greeting("hi") for _ in range(6)]

        self.assertEqual(len(set(said)), len(said))

    def test_every_greeting_is_short_enough_to_be_spoken(self):
        picker = self.selector()

        for _ in range(20):
            self.assertLessEqual(len(picker.greeting("hi").split()), 8)

    def test_not_every_line_is_a_question(self):
        # "How are you doing?" every time is repetitive twice over: the same
        # words, and the same demand that the other person carry the turn.
        picker = self.selector()

        said = {picker.greeting("hi") for _ in range(30)}

        self.assertTrue(any(not line.endswith("?") for line in said))


class TimeOfDayTests(unittest.TestCase):

    def picker(self, hour: int) -> SocialLineSelector:
        return SocialLineSelector(rng=random.Random(1), clock=lambda: hour)

    def test_a_morning_greeting_can_be_answered_in_kind(self):
        said = {self.picker(8).greeting("good morning") for _ in range(20)}

        self.assertTrue(any("orning" in line for line in said))

    def test_she_does_not_say_good_morning_at_midnight(self):
        picker = self.picker(1)

        for _ in range(20):
            self.assertNotIn("morning", picker.greeting("hi").casefold())

    def test_the_greeting_itself_outranks_the_clock(self):
        # Answering "good morning" with "still up?" because the machine
        # clock disagrees is worse than being an hour off.
        picker = self.picker(2)

        said = {picker.greeting("good morning") for _ in range(20)}

        self.assertTrue(any("orning" in line for line in said))
        for line in said:
            self.assertNotIn("still up", line.casefold())


class LanguageTests(unittest.TestCase):

    def test_korean_greetings_are_written_in_korean(self):
        picker = SocialLineSelector(
            language="ko", rng=random.Random(0), clock=lambda: 10,
        )

        for _ in range(10):
            self.assertRegex(picker.greeting("안녕"), r"[가-힣]")

    def test_an_unknown_language_falls_back_rather_than_failing(self):
        picker = SocialLineSelector(
            language="fr", rng=random.Random(0), clock=lambda: 10,
        )

        self.assertEqual(picker.language, "en")
        self.assertTrue(picker.greeting("bonjour").strip())

    def test_korean_lines_also_rotate(self):
        picker = SocialLineSelector(
            language="ko", rng=random.Random(0), clock=lambda: 10,
        )

        said = [picker.greeting("안녕") for _ in range(6)]

        self.assertGreaterEqual(len(set(said)), 4)


class ResetTests(unittest.TestCase):

    def test_reset_forgets_what_was_said(self):
        picker = SocialLineSelector(rng=random.Random(0), clock=lambda: 10)
        picker.greeting("hi")

        self.assertTrue(picker.recent)
        picker.reset()
        self.assertEqual(picker.recent, ())


if __name__ == "__main__":
    unittest.main()
