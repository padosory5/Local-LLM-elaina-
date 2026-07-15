from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time

import pygame

from config.loader import Config
from voice.base import BaseTTS


class PiperTTS(BaseTTS):

    def __init__(self, config: Config) -> None:
        self.piper = str(
            config.resolve_path(
                "tts",
                "piper",
                "executable",
                must_exist=True,
            )
        )

        self.model = str(
            config.resolve_path(
                "tts",
                "piper",
                "model",
                must_exist=True,
            )
        )

        if not pygame.mixer.get_init():
            pygame.mixer.init()

        self._process: subprocess.Popen | None = None
        self._channel: pygame.mixer.Channel | None = None
        self._sound: pygame.mixer.Sound | None = None

        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def speak(self, text: str) -> None:
        self._stop_event.clear()

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
        ) as temp:
            output = temp.name

        try:
            with self._lock:
                self._process = subprocess.Popen(
                    [
                        self.piper,
                        "--quiet",
                        "--model",
                        self.model,
                        "--output_file",
                        output,
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                )

                process = self._process

            process.communicate(input=text)

            with self._lock:
                self._process = None

            if process.returncode != 0:
                raise RuntimeError(
                    f"Piper exited with code {process.returncode}"
                )

            if self._stop_event.is_set():
                return

            sound = pygame.mixer.Sound(output)
            channel = sound.play()

            with self._lock:
                self._sound = sound
                self._channel = channel

            if channel is None:
                raise RuntimeError(
                    "Pygame could not create an audio channel."
                )

            while channel.get_busy():
                if self._stop_event.is_set():
                    channel.stop()
                    break

                time.sleep(0.02)

        finally:
            with self._lock:
                self._process = None
                self._channel = None
                self._sound = None

            try:
                os.remove(output)
            except (PermissionError, FileNotFoundError):
                pass

    def stop(self) -> None:
        self._stop_event.set()

        with self._lock:
            channel = self._channel
            process = self._process

        if channel is not None:
            channel.stop()

        if process is not None and process.poll() is None:
            process.terminate()

            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()