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


if __name__ == "__main__":
    unittest.main()
