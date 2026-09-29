"""Measuring her must never spend the person's ElevenLabs credits.

Every verification run used to speak through the configured provider. The
twelve-turn dogfood arcs logged fifteen to twenty-two ElevenLabs errors
each, which is to say they were calling it on every reply, and the eight
live checks that build a ChatEngine in-process did the same. The harness
set ``tts.enabled = False`` to stop it, and nothing read the key.
"""

from __future__ import annotations

import copy
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from config.loader import Config
from voice.manager import VoiceManager, speech_enabled
from voice.silent import SilentTTS


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config(**tts) -> Config:
    config = Config()
    config.data = copy.deepcopy(config.data)
    config.data["tts"].update(tts)
    return config


class SpeechSwitchTests(unittest.TestCase):

    def test_the_environment_turns_speech_off(self):
        for value in ("off", "0", "false", "silent", "OFF"):
            with self.subTest(value=value), patch.dict(os.environ, {"ELAINA_TTS": value}):
                self.assertFalse(speech_enabled(_config()))

    def test_the_configured_key_is_read_now(self):
        with patch.dict(os.environ, {"ELAINA_TTS": ""}):
            self.assertFalse(speech_enabled(_config(enabled=False)))
            self.assertTrue(speech_enabled(_config(enabled=True)))

    def test_speech_stays_on_when_nothing_says_otherwise(self):
        # The app itself must sound exactly as it did.
        config = _config()
        config.data["tts"].pop("enabled", None)
        with patch.dict(os.environ, {"ELAINA_TTS": ""}):
            self.assertTrue(speech_enabled(config))

    def test_a_silenced_voice_never_builds_a_provider(self):
        refuse = AssertionError("a provider was built while speech was off")
        with (
            patch.dict(os.environ, {"ELAINA_TTS": "off"}),
            patch("voice.manager.ElevenLabsTTS", side_effect=refuse),
            patch("voice.manager.PiperTTS", side_effect=refuse),
        ):
            manager = VoiceManager(config=_config(provider="elevenlabs"))
            self.assertIsInstance(manager.engine, SilentTTS)
            manager.speak("This goes nowhere.")
            manager.stop()


class LauncherTests(unittest.TestCase):
    """Every script that brings a backend to life silences it by default."""

    _STARTS_A_BACKEND = re.compile(
        r"ChatEngine\(\)|\[[^\]]*\"main\.py\"\]", re.MULTILINE,
    )

    def test_every_backend_a_script_starts_is_silent_by_default(self):
        loud = []
        places = ("scripts", "evals")
        for path in sorted(
            path for place in places for path in (PROJECT_ROOT / place).glob("*.py")
        ):
            source = path.read_text(encoding="utf-8")
            if self._STARTS_A_BACKEND.search(source) and "ELAINA_TTS" not in source:
                loud.append(path.name)
        self.assertEqual(
            loud, [],
            "these scripts start a backend that would speak through the "
            "paid voice; default ELAINA_TTS to off in them",
        )

    def test_the_test_runner_defaults_to_silence(self):
        source = (PROJECT_ROOT / "tests" / "run_tests.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.setdefault("ELAINA_TTS", "off")', source)


if __name__ == "__main__":
    unittest.main()
