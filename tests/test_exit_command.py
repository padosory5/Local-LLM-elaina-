"""Saying "quit" has to be enough.

B-55, from the second dogfooding session:

    You said: quit.
    Elaina: Okay, I'll quit. See you later.
    Listening...
    [she kept listening; it took "disconnect" to actually close her]

The transcript was `quit.` -- with a full stop, which Whisper adds and a
person cannot hear. The exit check was a set membership on the raw
transcript:

    command = user_input.lower().strip()
    if command in {"quit", "exit", "goodbye", ...}

`"quit."` is not `"quit"`, so the turn fell through to the router, which
read it correctly as ending the conversation, and she said goodbye and
carried on listening. Session 1 happened to transcribe a bare `quit` and
worked, which is why this survived a shutdown phase.

So the exit command is read as a closed grammatical class, the way the
bare acknowledgement and cancellation fast paths already are -- and the
two halves that matter are what leaves and what must not. "Quit" is an
instruction to her; "I want to quit my job" is a sentence about a job.
"""

import unittest

from brain.social_lines import SocialLineSelector, reads_as_farewell


class LeavingTests(unittest.TestCase):

    def test_the_live_transcript_is_an_exit_command(self):
        self.assertTrue(reads_as_farewell("quit."))

    def test_the_ways_people_say_it(self):
        for said in (
            "quit", "quit.", "Quit!", "  quit  ",
            "exit", "exit.",
            "goodbye", "goodbye.", "Goodbye Elaina", "goodbye elaina.",
            "bye", "bye.", "bye bye", "byebye",
            "see you", "see you later", "see ya",
            "stop elaina", "shut down", "shutdown",
            "okay, quit", "alright, goodbye", "quit please",
            "quit for now", "ok bye",
            "종료", "종료해", "끝내자", "잘 있어",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_farewell(said), said)

    def test_a_sentence_that_merely_contains_the_word_does_not_leave(self):
        # The whole risk. Every one of these must reach the router.
        for said in (
            "I want to quit my job",
            "quit Spotify",
            "quit the browser for me",
            "close the browser",
            "shut down my PC",
            "how do I exit vim",
            "say goodbye to that idea",
            "exit the folder",
            "I said goodbye to him yesterday",
            "did you quit the app",
            "bye is a funny word",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_farewell(said), said)

    def test_an_ordinary_turn_is_not_a_farewell(self):
        for said in (
            "what time is it in Seattle",
            "okay",
            "never mind",
            "thanks, that's everything",
            "hello",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_farewell(said), said)


class SayingGoodbyeTests(unittest.TestCase):
    """She leaves with a word, not in silence."""

    def test_there_is_something_to_say(self):
        line = SocialLineSelector().farewell()

        self.assertTrue(line)
        self.assertLessEqual(len(line.split()), 12, line)

    def test_she_does_not_repeat_herself(self):
        selector = SocialLineSelector()

        lines = [selector.farewell() for _ in range(4)]

        self.assertEqual(len(set(lines)), len(lines))

    def test_korean_gets_korean(self):
        self.assertTrue(SocialLineSelector(language="ko").farewell())


class WiredIntoMainTests(unittest.TestCase):
    """main.py is a script body, so this reads it."""

    def _main(self) -> str:
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1] / "main.py"
        ).read_text(encoding="utf-8")

    def test_the_exit_check_is_not_a_raw_string_comparison(self):
        # The drift guard. A set of literal transcripts is what let a full
        # stop keep the process alive.
        source = self._main()

        self.assertIn("reads_as_farewell", source)
        self.assertNotIn('"goodbye elaina",', source)

    def test_leaving_goes_through_the_one_stop_path(self):
        source = self._main()

        self.assertIn("_begin_stop()", source)


if __name__ == "__main__":
    unittest.main()
