from __future__ import annotations

import os
import tempfile
import threading
import time

import pygame
from elevenlabs.client import ElevenLabs

from config.loader import Config
from voice.base import BaseTTS


def _clamped(value, *, default: float, low: float = 0.0,
             high: float = 1.0) -> float:
    """A number inside its allowed range, or the default when it is not one."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


class ElevenLabsTTS(BaseTTS):

    def __init__(self, config: Config) -> None:
        self.api_key = config.get_env(
            "tts",
            "elevenlabs",
            "api_key_env",
        )

        self.voice_id = config.get(
            "tts",
            "elevenlabs",
            "voice_id",
        )

        self.model = config.get(
            "tts",
            "elevenlabs",
            "model",
        )

        self.output_format = config.get(
            "tts",
            "elevenlabs",
            "output_format",
            default="mp3_44100_128",
            required=False,
        )

        if not self.voice_id:
            raise ValueError(
                "ElevenLabs voice_id is missing in config.yaml."
            )

        # How the line is delivered. Nothing was sent before, so every
        # reply used the voice's own defaults and came out sounding
        # aggressive and loud. Absent config keeps the old behaviour, so a
        # missing block cannot silently change how she sounds.
        self.voice_settings = self._read_voice_settings(config)
        self.volume = _clamped(
            config.get(
                "tts", "elevenlabs", "volume",
                default=1.0, required=False,
            ),
            default=1.0,
        )

        self.client = ElevenLabs(
            api_key=self.api_key,
        )

        if not pygame.mixer.get_init():
            pygame.mixer.init()

        self._stop_event = threading.Event()
        self._channel: pygame.mixer.Channel | None = None
        self._sound: pygame.mixer.Sound | None = None
        self._lock = threading.Lock()

    def speak(self, text: str) -> None:
        text = text.strip()

        if not text:
            return

        self._stop_event.clear()

        request = {
            "voice_id": self.voice_id,
            "model_id": self.model,
            "text": text,
            "output_format": self.output_format,
        }
        if self.voice_settings is not None:
            request["voice_settings"] = self.voice_settings
        audio = self.client.text_to_speech.convert(**request)

        with tempfile.NamedTemporaryFile(
            suffix=".mp3",
            delete=False,
        ) as temporary_file:
            output_path = temporary_file.name

            for chunk in audio:
                temporary_file.write(chunk)

        try:
            if self._stop_event.is_set():
                return

            sound = pygame.mixer.Sound(output_path)
            # Loudness is not delivery. Turning the style down makes her
            # calmer; this is the separate question of how loud calm is.
            sound.set_volume(self.volume)
            channel = sound.play()

            if channel is None:
                raise RuntimeError(
                    "Pygame could not create an audio channel."
                )

            with self._lock:
                self._sound = sound
                self._channel = channel

            while channel.get_busy():
                if self._stop_event.is_set():
                    channel.stop()
                    break

                time.sleep(0.02)

        finally:
            with self._lock:
                self._channel = None
                self._sound = None

            try:
                os.remove(output_path)
            except (PermissionError, FileNotFoundError):
                pass

    @staticmethod
    def _read_voice_settings(config):
        """The delivery settings from config, or ``None`` to use the voice's.

        Returns ``None`` when the block is absent so that an older
        config.yaml behaves exactly as it did before this existed. A
        missing setting must not quietly change how she sounds.
        """
        block = config.get(
            "tts", "elevenlabs", "voice_settings",
            default=None, required=False,
        )
        if not isinstance(block, dict) or not block:
            return None

        from elevenlabs import VoiceSettings

        settings = {}
        for name in ("stability", "similarity_boost", "style", "speed"):
            if name in block:
                settings[name] = _clamped(
                    block[name],
                    default=0.5,
                    low=0.0 if name != "speed" else 0.7,
                    high=1.0 if name != "speed" else 1.2,
                )
        if "use_speaker_boost" in block:
            settings["use_speaker_boost"] = bool(block["use_speaker_boost"])
        return VoiceSettings(**settings) if settings else None

    def stop(self) -> None:
        self._stop_event.set()

        with self._lock:
            channel = self._channel

        if channel is not None:
            channel.stop()