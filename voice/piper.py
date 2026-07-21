from __future__ import annotations

import array
import os
import subprocess
import tempfile
import threading
import time
import wave

import pygame

from config.loader import Config
from core.event_bus import EventBus
from voice.base import BaseTTS


class PiperTTS(BaseTTS):

    def __init__(
        self,
        config: Config,
        event_bus: EventBus | None = None,
    ) -> None:
        self.events = event_bus

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
            self._generate_audio(text, output)

            if self._stop_event.is_set():
                return

            lip_sync_values, frame_duration = (
                self._calculate_lip_sync(output)
            )

            sound = pygame.mixer.Sound(output)
            channel = sound.play()

            with self._lock:
                self._sound = sound
                self._channel = channel

            if channel is None:
                raise RuntimeError(
                    "Pygame could not create an audio channel."
                )

            start_time = time.monotonic()
            last_frame_index = -1

            while channel.get_busy():
                if self._stop_event.is_set():
                    channel.stop()
                    break

                elapsed = time.monotonic() - start_time
                frame_index = int(elapsed / frame_duration)

                if (
                    frame_index != last_frame_index
                    and frame_index < len(lip_sync_values)
                ):
                    self._emit_lip_sync(
                        lip_sync_values[frame_index]
                    )
                    last_frame_index = frame_index

                time.sleep(0.01)

        finally:
            # Always close the mouth when playback ends or is interrupted.
            self._emit_lip_sync(0.0)

            with self._lock:
                self._process = None
                self._channel = None
                self._sound = None

            try:
                os.remove(output)
            except (PermissionError, FileNotFoundError):
                pass

    def _generate_audio(
        self,
        text: str,
        output_path: str,
    ) -> None:
        with self._lock:
            self._process = subprocess.Popen(
                [
                    self.piper,
                    "--quiet",
                    "--model",
                    self.model,
                    "--output_file",
                    output_path,
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

    def _calculate_lip_sync(
        self,
        wav_path: str,
        frame_duration: float = 0.03,
    ) -> tuple[list[float], float]:
        """
        Calculate one mouth-open value for every 30 ms of audio.

        Returns values from 0.0 to 1.0.
        """
        values: list[float] = []

        with wave.open(wav_path, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            channel_count = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()

            if sample_width != 2:
                raise RuntimeError(
                    "Lip sync currently expects 16-bit WAV audio."
                )

            samples_per_frame = max(
                1,
                int(sample_rate * frame_duration),
            )

            while True:
                raw_audio = wav_file.readframes(
                    samples_per_frame
                )

                if not raw_audio:
                    break

                samples = array.array("h")
                samples.frombytes(raw_audio)

                # For stereo audio, use every channel sample.
                # RMS still provides a suitable overall volume value.
                if not samples:
                    values.append(0.0)
                    continue

                square_sum = sum(
                    sample * sample
                    for sample in samples
                )

                rms = (
                    square_sum / len(samples)
                ) ** 0.5

                # Adjust these values later to tune sensitivity.
                noise_gate = 250.0
                normalizer = 5000.0

                if rms <= noise_gate:
                    mouth_value = 0.0
                else:
                    mouth_value = (
                        rms - noise_gate
                    ) / normalizer

                mouth_value = max(
                    0.0,
                    min(1.0, mouth_value),
                )

                # Slightly exaggerate quieter speech.
                mouth_value = mouth_value ** 0.65

                values.append(mouth_value)

        return values, frame_duration

    def _emit_lip_sync(self, value: float) -> None:
        if self.events is None:
            return

        self.events.emit(
            "lip_sync",
            value=round(float(value), 3),
        )

    def stop(self) -> None:
        self._stop_event.set()
        self._emit_lip_sync(0.0)

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