import unittest

try:
    from brain.text_filter import TextFilter
except ModuleNotFoundError:
    # Allows this changed-files bundle to be tested before copying its files.
    from text_filter import TextFilter


class TextFilterTests(unittest.TestCase):
    def test_removes_markdown_from_spoken_identification(self):
        text = (
            "**Answer:** Marathon\n"
            "**Confidence:** High\n"
            "This is *Marathon* by Bungie."
        )

        self.assertEqual(
            TextFilter.for_speech(text),
            "Marathon This is Marathon by Bungie.",
        )

    def test_keeps_link_label_without_reading_url(self):
        text = (
            "See [Bungie's page](https://example.com/game) "
            "for the active_model."
        )

        self.assertEqual(
            TextFilter.for_speech(text),
            "See Bungie's page for the active model.",
        )

    def test_display_cleaner_removes_stars(self):
        self.assertEqual(
            TextFilter.clean("That is **Marathon**."),
            "That is Marathon.",
        )

    def test_voice_response_flattens_markdown_without_cutting_sentences(self):
        text = (
            "Sure! Here's what I can do:\n"
            "- **Coding Agent:** I can inspect code.\n"
            "- **Git Agent:** I can prepare commits.\n"
            "- **Research Agent:** I can search the web."
        )

        result = TextFilter.for_voice_response(
            text,
            max_words=30,
            max_sentences=2,
        )

        self.assertNotIn("*", result)
        self.assertNotIn("\n", result)
        self.assertNotIn("- ", result)
        self.assertEqual(
            result,
            (
                "Sure! Here's what I can do: Coding Agent: I can inspect code. "
                "Git Agent: I can prepare commits. Research Agent: I can "
                "search the web."
            ),
        )

    def test_voice_response_does_not_enforce_word_limit_by_slicing(self):
        result = TextFilter.for_voice_response(
            "one two three four five six seven",
            max_words=5,
            max_sentences=2,
        )

        self.assertEqual(result, "one two three four five six seven")

    def test_malformed_markdown_does_not_fuse_words(self):
        self.assertEqual(
            TextFilter.for_speech("3D models**offer more flexibility."),
            "3D models offer more flexibility.",
        )

    def test_removes_generic_voice_follow_up(self):
        self.assertEqual(
            TextFilter.for_voice_response(
                "OpenAI was founded in 2015. Want to know anything else?"
            ),
            "OpenAI was founded in 2015.",
        )

    def test_english_speech_replaces_korean_clicked_control(self):
        result = TextFilter.for_configured_speech(
            "Clicked 설정.",
            response_language="en",
        )

        self.assertEqual(result, "Clicked the requested control.")
        self.assertIsNone(TextFilter.HANGUL_PATTERN.search(result))

    def test_english_speech_keeps_native_control_confirmation_as_question(self):
        result = TextFilter.for_configured_speech(
            "Click 설정?",
            response_language="en",
        )

        self.assertEqual(result, "Click the requested control?")
        self.assertIsNone(TextFilter.HANGUL_PATTERN.search(result))

    def test_english_speech_replaces_korean_focused_window(self):
        result = TextFilter.for_configured_speech(
            "Focused 제목 없음 - 메모장.",
            response_language="en-US",
        )

        self.assertEqual(result, "Focused the requested window.")
        self.assertIsNone(TextFilter.HANGUL_PATTERN.search(result))

    def test_english_speech_replaces_korean_typed_target(self):
        result = TextFilter.for_configured_speech(
            "grocery list typed into제목 없음 - 메모장.",
            response_language="en",
        )

        self.assertEqual(
            result,
            "Entered the text in the requested field.",
        )
        self.assertIsNone(TextFilter.HANGUL_PATTERN.search(result))

    def test_english_speech_uses_safe_fallback_for_native_title(self):
        result = TextFilter.for_configured_speech(
            "제목 없음 - 메모장.",
            response_language="en",
        )

        self.assertEqual(result, "The result is shown on screen.")
        self.assertIsNone(TextFilter.HANGUL_PATTERN.search(result))

    def test_english_speech_preserves_truthful_failure(self):
        result = TextFilter.for_configured_speech(
            "I couldn't click 설정.",
            response_language="en",
        )

        self.assertEqual(result, "I couldn't click.")
        self.assertIsNone(TextFilter.HANGUL_PATTERN.search(result))

    def test_english_text_is_unchanged_by_language_guard(self):
        self.assertEqual(
            TextFilter.for_configured_speech(
                "Clicked Settings.",
                response_language="en",
            ),
            "Clicked Settings.",
        )

    def test_non_english_speech_keeps_native_text(self):
        self.assertEqual(
            TextFilter.for_configured_speech(
                "Clicked 설정.",
                response_language="ko",
            ),
            "Clicked 설정.",
        )

    def test_length_arguments_never_delete_a_complete_answer(self):
        result = TextFilter.for_voice_response(
            "This is Eros, the Greek god of love. "
            "The following long evidence explanation contains many extra "
            "details that should never be cut into an unfinished fragment.",
            max_words=12,
            max_sentences=2,
        )

        self.assertEqual(
            result,
            (
                "This is Eros, the Greek god of love. The following long "
                "evidence explanation contains many extra details that "
                "should never be cut into an unfinished fragment."
            ),
        )


if __name__ == "__main__":
    unittest.main()


class SpokenDashTests(unittest.TestCase):
    """A dash is punctuation on a page and a stumble in a voice.

    Measured live, on a turn about buying a monitor:

        Elaina: I don't want to send you somewhere I haven't checked --
                want me to look up real ones?

    Spoken, that is two sentences shoved together with a gap in the middle.
    The dashes are not all the model's either -- plenty are written into her
    own lines -- so a prompt instruction could not have fixed this one.
    """

    def test_a_parenthetical_dash_becomes_a_pause(self):
        self.assertEqual(
            TextFilter.natural_dashes(
                "I don't want to send you somewhere I haven't checked -- "
                "want me to look up real ones?"
            ),
            "I don't want to send you somewhere I haven't checked, "
            "want me to look up real ones?",
        )

    def test_every_dash_character_is_covered(self):
        for dash in ("--", "---", "–", "—"):
            with self.subTest(dash=dash):
                self.assertEqual(
                    TextFilter.natural_dashes(f"Got it{dash}Spotify is open."),
                    "Got it, Spotify is open.",
                )

    def test_a_spaced_hyphen_is_a_dash_too(self):
        self.assertEqual(
            TextFilter.natural_dashes("Wait - what?"), "Wait, what?",
        )

    def test_a_hyphenated_word_is_left_alone(self):
        for text in (
            "A 27-inch panel.",
            "Force-quit is off.",
            "Send me an e-mail.",
        ):
            with self.subTest(text=text):
                self.assertEqual(TextFilter.natural_dashes(text), text)

    def test_a_number_range_is_said_as_a_range(self):
        self.assertEqual(
            TextFilter.natural_dashes("It runs 200,000-350,000 won."),
            "It runs 200,000 to 350,000 won.",
        )

    def test_a_currency_range_is_said_as_a_range(self):
        self.assertEqual(
            TextFilter.natural_dashes("Prices are $100 – $200."),
            "Prices are $100 to $200.",
        )

    def test_a_comma_is_not_stacked_on_existing_punctuation(self):
        self.assertEqual(
            TextFilter.natural_dashes("Two sentences. -- And more."),
            "Two sentences. And more.",
        )

    def test_a_list_marker_survives_the_speech_filter(self):
        # The bullet strip runs first on purpose: a leading "- " is a list
        # marker, and turning that into a comma would be wrong.
        self.assertEqual(TextFilter.for_speech("- first item"), "first item")

    def test_the_speech_filter_applies_it(self):
        self.assertEqual(
            TextFilter.for_speech("I could check monitors for you -- worth it?"),
            "I could check monitors for you, worth it?",
        )

    def test_empty_text_is_unchanged(self):
        self.assertEqual(TextFilter.natural_dashes(""), "")
