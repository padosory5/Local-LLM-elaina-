from __future__ import annotations

import math
import queue
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from silero_vad import load_silero_vad

from voice.audio_player import AudioPlayer


class VoiceActivityDetector:
    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.5,
        silence_ms: int = 1100,
        minimum_speech_ms: int = 250,
        pre_speech_ms: int = 300,
        start_timeout_seconds: float = 15.0,
        maximum_recording_seconds: float = 30.0,
        device_index: int | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.silence_ms = silence_ms
        self.minimum_speech_ms = minimum_speech_ms
        self.pre_speech_ms = pre_speech_ms
        self.start_timeout_seconds = start_timeout_seconds
        self.maximum_recording_seconds = maximum_recording_seconds
        self.device_index = device_index

        # Silero expects 512 samples for 16 kHz streaming audio.
        self.chunk_samples = 512
        self.chunk_ms = (
            self.chunk_samples / self.sample_rate * 1000
        )

        torch.set_num_threads(1)
        self.model = load_silero_vad()

        self.audio_player = AudioPlayer()

        self.start_sound_path = (
            Path(__file__).resolve().parent
            / "sounds"
            / "start.wav"
        )

    def record(
        self,
        on_speech_start: Callable[[], None] | None = None,
    ) -> np.ndarray | None:
        audio_queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=100
        )

        pre_roll_chunks = max(
            1,
            math.ceil(
                self.pre_speech_ms / self.chunk_ms
            ),
        )

        pre_roll: deque[np.ndarray] = deque(
            maxlen=pre_roll_chunks
        )

        recorded_chunks: list[np.ndarray] = []

        speech_started = False
        consecutive_speech_ms = 0.0
        consecutive_silence_ms = 0.0

        waiting_started_at = time.monotonic()
        recording_started_at: float | None = None

        self.model.reset_states()

        def audio_callback(
            input_data: np.ndarray,
            frame_count: int,
            time_info,
            status: sd.CallbackFlags,
        ) -> None:
            if status:
                print(
                    f"\n[Microphone Warning] {status}"
                )

            try:
                audio_queue.put_nowait(
                    input_data[:, 0].copy()
                )
            except queue.Full:
                pass

        print("\nListening...")

        try:
            with sd.InputStream(
                device=self.device_index,
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self.chunk_samples,
                callback=audio_callback,
            ):
                while True:
                    try:
                        chunk = audio_queue.get(
                            timeout=0.2
                        )

                    except queue.Empty:
                        if (
                            not speech_started
                            and time.monotonic()
                            - waiting_started_at
                            >= self.start_timeout_seconds
                        ):
                            print(
                                "No speech detected."
                            )
                            return None

                        continue

                    normalized_audio = (
                        chunk.astype(np.float32)
                        / 32768.0
                    )

                    audio_tensor = torch.from_numpy(
                        normalized_audio
                    )

                    with torch.no_grad():
                        speech_probability = float(
                            self.model(
                                audio_tensor,
                                self.sample_rate,
                            ).item()
                        )

                    is_speech = (
                        speech_probability
                        >= self.threshold
                    )

                    if not speech_started:
                        pre_roll.append(chunk)

                        if is_speech:
                            consecutive_speech_ms += (
                                self.chunk_ms
                            )
                        else:
                            consecutive_speech_ms = 0.0

                        if (
                            consecutive_speech_ms
                            >= self.minimum_speech_ms
                        ):
                            speech_started = True
                            recording_started_at = (
                                time.monotonic()
                            )

                            recorded_chunks.extend(
                                pre_roll
                            )
                            pre_roll.clear()

                            if (
                                on_speech_start
                                is not None
                            ):
                                try:
                                    on_speech_start()
                                except Exception as error:
                                    print(
                                        "[Speech Start "
                                        "Callback Error] "
                                        f"{error}"
                                    )

                            self.audio_player.play(
                                self.start_sound_path
                            )

                            print(
                                "Speech detected..."
                            )

                        elif (
                            time.monotonic()
                            - waiting_started_at
                            >= self.start_timeout_seconds
                        ):
                            print(
                                "No speech detected."
                            )
                            return None

                        continue

                    recorded_chunks.append(chunk)

                    if is_speech:
                        consecutive_silence_ms = 0.0
                    else:
                        consecutive_silence_ms += (
                            self.chunk_ms
                        )

                    if (
                        consecutive_silence_ms
                        >= self.silence_ms
                    ):
                        print(
                            "Finished listening."
                        )
                        break

                    if (
                        recording_started_at
                        is not None
                        and time.monotonic()
                        - recording_started_at
                        >= self.maximum_recording_seconds
                    ):
                        print(
                            "Maximum recording "
                            "time reached."
                        )
                        break

        except sd.PortAudioError as error:
            print(
                f"[Microphone Error] {error}"
            )
            return None

        finally:
            self.model.reset_states()

        if not recorded_chunks:
            return None

        return np.concatenate(
            recorded_chunks
        )