import unittest

from voice.stt import SpeechToText


class LooksLikeTtsEchoTests(unittest.TestCase):
    def test_near_identical_recent_reply_is_echo(self):
        self.assertTrue(
            SpeechToText._looks_like_tts_echo(
                "Got it, Discord is open.",
                "Got it, Discord is open.",
            )
        )

    def test_short_transcript_is_never_flagged(self):
        # Too short to safely judge -- also guards against comparing empty
        # or near-empty STT noise against a real reply.
        self.assertFalse(
            SpeechToText._looks_like_tts_echo("Okay", "Got it, Discord is open.")
        )

    def test_no_reference_text_is_never_flagged(self):
        self.assertFalse(
            SpeechToText._looks_like_tts_echo("Open Spotify please", "")
        )

    def test_unrelated_new_command_is_not_echo(self):
        self.assertFalse(
            SpeechToText._looks_like_tts_echo(
                "Launch Discord for me",
                "The weather today is sunny with a high of 75.",
            )
        )

    def test_short_command_inside_a_much_longer_earlier_reply_is_not_echo(self):
        # Regression: a real user command ("Open Spotify") landing as a
        # literal substring of Elaina's earlier, much longer recommendation
        # to enable Computer Control must never be swallowed as an echo --
        # that previously caused legitimate repeat requests to be silently
        # ignored (measured live: "[STT] Ignored probable speaker echo.").
        self.assertFalse(
            SpeechToText._looks_like_tts_echo(
                "Open Spotify",
                (
                    "Sure, once you enable Computer Control I can open "
                    "Spotify or check something else for you -- just let "
                    "me know what you'd like to do next."
                ),
            )
        )

    def test_full_length_echo_of_a_long_reply_is_still_caught(self):
        long_reply = (
            "Sure, once you enable Computer Control I can open Spotify or "
            "check something else for you."
        )
        self.assertTrue(
            SpeechToText._looks_like_tts_echo(long_reply, long_reply)
        )


if __name__ == "__main__":
    unittest.main()
