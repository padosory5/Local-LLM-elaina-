import unittest

from voice.transcription_policy import (
    retry_language_for_detection,
    segment_is_usable,
)


class SpeechTranscriptionGuardTests(unittest.TestCase):
    def test_unexpected_japanese_detection_retries_as_english(self):
        retry = retry_language_for_detection(
            configured_language=None,
            detected_language="ja",
            probability=0.52,
            allowed_languages=("en", "ko"),
            minimum_probability=0.60,
        )

        self.assertEqual(retry, "en")

    def test_confident_english_does_not_retry(self):
        retry = retry_language_for_detection(
            configured_language=None,
            detected_language="en",
            probability=0.96,
            allowed_languages=("en", "ko"),
            minimum_probability=0.60,
        )

        self.assertIsNone(retry)

    def test_low_confidence_allowed_language_retries_same_language(self):
        retry = retry_language_for_detection(
            configured_language=None,
            detected_language="ko",
            probability=0.40,
            allowed_languages=("en", "ko"),
            minimum_probability=0.60,
        )

        self.assertEqual(retry, "ko")

    def test_low_confidence_silence_segment_is_discarded(self):
        self.assertFalse(segment_is_usable(
            no_speech_probability=0.92,
            average_log_probability=-1.8,
            no_speech_threshold=0.60,
            log_probability_threshold=-1.0,
        ))

    def test_soft_but_confident_speech_is_kept(self):
        self.assertTrue(segment_is_usable(
            no_speech_probability=0.70,
            average_log_probability=-0.30,
            no_speech_threshold=0.60,
            log_probability_threshold=-1.0,
        ))


if __name__ == "__main__":
    unittest.main()
