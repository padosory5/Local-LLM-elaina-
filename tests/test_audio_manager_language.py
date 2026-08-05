import unittest
from unittest.mock import Mock, patch

from brain.text_filter import TextFilter
from voice.audio_manager import AudioManager


class AudioManagerLanguageTests(unittest.TestCase):
    def test_queues_language_safe_text_without_changing_input(self):
        config = Mock()
        config.get.return_value = "en"
        original = "Clicked 설정."

        # Keep this unit test independent of the real voice engine and worker
        # thread. The queue is the boundary immediately before TTS playback.
        with (
            patch("voice.audio_manager.VoiceManager"),
            patch("voice.audio_manager.threading.Thread") as thread_type,
        ):
            manager = AudioManager(config=config)
            manager.speak(original)

        config.get.assert_called_once_with(
            "language",
            "response",
            default="en",
            required=False,
        )
        thread_type.return_value.start.assert_called_once_with()

        generation, queued_text = manager._queue.get_nowait()
        self.assertEqual(generation, 0)
        self.assertEqual(queued_text, "Clicked the requested control.")
        self.assertIsNone(TextFilter.HANGUL_PATTERN.search(queued_text))
        self.assertEqual(original, "Clicked 설정.")


if __name__ == "__main__":
    unittest.main()
