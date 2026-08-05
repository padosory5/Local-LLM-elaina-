import unittest
from unittest.mock import Mock, patch

from voice.audio_manager import AudioManager


class EchoReferenceWindowTests(unittest.TestCase):
    def _manager(self) -> AudioManager:
        config = Mock()
        config.get.return_value = "en"
        # Keep this independent of the real voice engine and worker thread,
        # matching the pattern in test_audio_manager_language.py.
        with (
            patch("voice.audio_manager.VoiceManager"),
            patch("voice.audio_manager.threading.Thread"),
        ):
            return AudioManager(config=config)

    def test_echo_reference_available_immediately_after_speaking_ends(self):
        manager = self._manager()
        manager._recent_text = "Got it, Discord is open."
        manager._recent_text_expires_at = 106.0

        with patch("voice.audio_manager.time.monotonic", return_value=100.5):
            self.assertEqual(
                manager.echo_reference_text(), "Got it, Discord is open."
            )

    def test_echo_reference_expires_after_the_window(self):
        # Regression: without a bound, this stayed "the last thing Elaina
        # said" for the rest of the session, so a later unrelated command
        # sharing wording with an old reply could be misread as echo and
        # silently dropped.
        manager = self._manager()
        manager._recent_text = "Got it, Discord is open."
        manager._recent_text_expires_at = 100.0

        with patch("voice.audio_manager.time.monotonic", return_value=107.0):
            self.assertEqual(manager.echo_reference_text(), "")

    def test_current_text_while_speaking_always_wins_over_expiry(self):
        manager = self._manager()
        manager._current_text = "Currently speaking this."
        manager._recent_text = "An old reply."
        manager._recent_text_expires_at = 0.0

        self.assertEqual(
            manager.echo_reference_text(), "Currently speaking this."
        )


if __name__ == "__main__":
    unittest.main()
