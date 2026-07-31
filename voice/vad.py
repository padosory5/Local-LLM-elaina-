from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np
import sounddevice as sd
import torch
from silero_vad import load_silero_vad

from voice.audio_player import AudioPlayer
from voice.audio_processing import RollingAudioBuffer, resample_capture_chunk


def _host_api_name(device_info: dict) -> str:
    """Return the PortAudio host API name for a SoundDevice device."""
    try:
        host_api = sd.query_hostapis(int(device_info["hostapi"]))
        return str(host_api["name"])
    except (KeyError, TypeError, ValueError, sd.PortAudioError):
        return ""


def find_input_device(
    device_index: int | None,
    device_name: str | None,
    preferred_host_api: str | None,
) -> int | None:
    """Resolve a stable input device, preferring its name over a fragile index."""
    if device_index is not None:
        info = sd.query_devices(int(device_index), "input")
        if int(info["max_input_channels"]) < 1:
            raise ValueError(
                f"Audio device {device_index} has no input channels."
            )
        return int(device_index)

    requested_name = (device_name or "").strip().casefold()
    if not requested_name:
        return None

    requested_host_api = (
        preferred_host_api or ""
    ).strip().casefold()
    candidates: list[tuple[int, int]] = []

    for index, info in enumerate(sd.query_devices()):
        if int(info["max_input_channels"]) < 1:
            continue
        if requested_name not in str(info["name"]).casefold():
            continue

        host_matches = (
            requested_host_api
            and requested_host_api
            in _host_api_name(info).casefold()
        )
        candidates.append((1 if host_matches else 0, index))

    if not candidates:
        raise ValueError(
            "No input device matched "
            f"{device_name!r}. Run "
            "`python -m sounddevice` to list available devices."
        )

    # A matching host API wins. Otherwise keep the first matching microphone.
    return max(candidates, key=lambda candidate: candidate[0])[1]


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
        device_name: str | None = None,
        preferred_host_api: str | None = None,
        capture_sample_rate: int | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.silence_ms = silence_ms
        self.minimum_speech_ms = minimum_speech_ms
        self.pre_speech_ms = pre_speech_ms
        self.start_timeout_seconds = start_timeout_seconds
        self.maximum_recording_seconds = maximum_recording_seconds
        self.device_index = find_input_device(
            device_index=device_index,
            device_name=device_name,
            preferred_host_api=preferred_host_api,
        )

        device_info = sd.query_devices(
            self.device_index,
            "input",
        )
        self.device_name = str(device_info["name"])
        self.host_api_name = _host_api_name(device_info)
        self.capture_sample_rate = int(
            capture_sample_rate
            or round(float(device_info["default_samplerate"]))
        )

        # Silero expects 512 samples for 16 kHz streaming audio.
        self.chunk_samples = 512
        self.capture_chunk_samples = max(
            1,
            round(
                self.chunk_samples
                * self.capture_sample_rate
                / self.sample_rate
            ),
        )
        self.chunk_ms = (
            self.chunk_samples / self.sample_rate * 1000
        )

        torch.set_num_threads(1)
        self.model = load_silero_vad()

        self.audio_player = AudioPlayer()
        self._audio_buffer = RollingAudioBuffer(max_chunks=256)
        self._stream: sd.InputStream | None = None
        self._stream_lock = threading.RLock()
        self._record_lock = threading.Lock()
        self._closed = False
        self._last_audio_frame_at = 0.0

        self.start_sound_path = (
            Path(__file__).resolve().parent
            / "sounds"
            / "start.wav"
        )

        print(
            "[Microphone] "
            f"{self.device_name} via {self.host_api_name or 'PortAudio'} "
            f"at {self.capture_sample_rate} Hz "
            f"(device {self.device_index})."
        )

    def _audio_callback(
        self,
        input_data: np.ndarray,
        frame_count: int,
        time_info,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            print(f"\n[Microphone Warning] {status}")
        if self._closed:
            return
        try:
            converted = resample_capture_chunk(
                input_data[:, 0],
                self.chunk_samples,
            )
            if converted.size:
                self._last_audio_frame_at = time.monotonic()
                self._audio_buffer.push(converted)
        except Exception as error:
            print(
                "\n[Microphone Callback Error] "
                f"{type(error).__name__}: {error}"
            )

    def _stream_active(self) -> bool:
        try:
            return bool(self._stream is not None and self._stream.active)
        except (AttributeError, sd.PortAudioError):
            return False

    def start(self) -> bool:
        """Open one input stream and keep it active for Elaina's lifetime."""
        with self._stream_lock:
            if self._stream_active():
                return True
            self._close_stream_locked()
            self._closed = False
            try:
                self._stream = sd.InputStream(
                    device=self.device_index,
                    samplerate=self.capture_sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=self.capture_chunk_samples,
                    callback=self._audio_callback,
                )
                self._stream.start()
                self._last_audio_frame_at = time.monotonic()
                print("[Microphone] Persistent input stream is active.")
                return True
            except sd.PortAudioError as error:
                self._stream = None
                print(f"[Microphone Error] {error}")
                return False

    def _close_stream_locked(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
        except (AttributeError, sd.PortAudioError):
            pass
        try:
            stream.close()
        except (AttributeError, sd.PortAudioError):
            pass

    def close(self) -> None:
        """Release the persistent device only when the application exits."""
        with self._stream_lock:
            self._closed = True
            self._close_stream_locked()
            self._audio_buffer.clear()

    def _restart_stream(self) -> bool:
        with self._stream_lock:
            self._close_stream_locked()
        self._audio_buffer.clear()
        return self.start()

    def record(
        self,
        on_speech_start: Callable[[], None] | None = None,
        is_barge_in: Callable[[], bool] | None = None,
    ) -> np.ndarray | None:
        pre_roll_chunks = max(
            1,
            math.ceil(
                self.pre_speech_ms / self.chunk_ms
            ),
        )

        pre_roll: deque[np.ndarray] = deque(
            self._audio_buffer.drain_recent(pre_roll_chunks),
            maxlen=pre_roll_chunks,
        )

        recorded_chunks: list[np.ndarray] = []

        speech_started = False
        consecutive_speech_ms = 0.0
        consecutive_silence_ms = 0.0

        waiting_started_at = time.monotonic()
        recording_started_at: float | None = None
        noise_floor = 0.003
        peak_probability = 0.0
        peak_rms = 0.0

        self.model.reset_states()

        print("\nListening...")

        if not self.start():
            return None

        try:
            with self._record_lock:
                while True:
                    try:
                        chunk = self._audio_buffer.get(
                            timeout=0.2
                        )

                    except queue.Empty:
                        no_frames_for = (
                            time.monotonic() - self._last_audio_frame_at
                        )
                        if no_frames_for >= 2.0:
                            print(
                                "[Microphone] Input stream stopped delivering "
                                "audio; restarting it."
                            )
                            if not self._restart_stream():
                                return None
                            waiting_started_at = time.monotonic()
                            continue
                        if (
                            not speech_started
                            and time.monotonic()
                            - waiting_started_at
                            >= self.start_timeout_seconds
                        ):
                            print(
                                "No speech detected. "
                                "(microphone delivered no audio frames; "
                                f"device={self.device_index})"
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

                    rms = float(
                        np.sqrt(
                            np.mean(
                                normalized_audio
                                * normalized_audio
                            )
                        )
                    )
                    peak_probability = max(
                        peak_probability,
                        speech_probability,
                    )
                    peak_rms = max(peak_rms, rms)

                    # Slowly learn quiet-room volume only from chunks that
                    # Silero considers non-speech. This makes soft speech less
                    # likely to disappear without treating fan noise as speech.
                    if speech_probability < 0.10:
                        noise_floor = (
                            noise_floor * 0.98
                            + rms * 0.02
                        )

                    barge_in_active = bool(
                        is_barge_in is not None
                        and is_barge_in()
                    )
                    active_threshold = self.threshold + (
                        0.12 if barge_in_active else 0.0
                    )
                    energy_threshold = max(
                        0.010,
                        noise_floor * 3.0,
                    )
                    is_speech = (
                        speech_probability >= active_threshold
                        or (
                            speech_probability >= 0.15
                            and rms >= energy_threshold
                        )
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
                                "No speech detected. "
                                f"(peak VAD={peak_probability:.2f}, "
                                f"peak level={peak_rms:.4f}, "
                                f"device={self.device_index})"
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
