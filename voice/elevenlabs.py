from __future__ import annotations

import os
import tempfile
import threading
import time

import pygame
from elevenlabs.client import ElevenLabs

from config.loader import Config
from voice.base import BaseTTS


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

        audio = self.client.text_to_speech.convert(
            voice_id=self.voice_id,
            model_id=self.model,
            text=text,
            output_format=self.output_format,
        )

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

    def stop(self) -> None:
        self._stop_event.set()

        with self._lock:
            channel = self._channel

        if channel is not None:
            channel.stop()