from __future__ import annotations

import ctypes
import os
import sysconfig
import tempfile
import wave
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path

from faster_whisper import WhisperModel

from config.loader import Config
from voice.vad import VoiceActivityDetector


_DLL_DIRECTORY_HANDLES = []


def _configure_cuda_runtime() -> None:
    """Expose pip-installed CUDA DLLs on Windows without blocking CPU fallback."""
    if os.name != "nt":
        return

    site_packages = Path(sysconfig.get_path("purelib"))
    candidate_directories = (
        site_packages / "nvidia" / "cublas" / "bin",
        site_packages / "nvidia" / "cudnn" / "bin",
        site_packages / "nvidia" / "cuda_runtime" / "bin",
    )

    for directory in candidate_directories:
        if not directory.is_dir():
            continue

        os.environ["PATH"] = (
            str(directory)
            + os.pathsep
            + os.environ.get("PATH", "")
        )
        if hasattr(os, "add_dll_directory"):
            _DLL_DIRECTORY_HANDLES.append(
                os.add_dll_directory(str(directory))
            )

    # Loading these explicitly avoids a Windows search-order problem on some
    # Faster-Whisper installations. Missing files are allowed: model creation
    # below will either locate CUDA normally or fall back to CPU.
    for name in (
        "cublasLt64_12.dll",
        "cublas64_12.dll",
        "cudnn64_9.dll",
    ):
        for directory in candidate_directories:
            dll_path = directory / name
            if not dll_path.is_file():
                continue
            try:
                ctypes.WinDLL(str(dll_path))
            except OSError as error:
                print(f"[CUDA Warning] Could not load {name}: {error}")
            break


class SpeechToText:
    """One configured Faster-Whisper model plus Silero microphone capture."""

    def __init__(
        self,
        config: Config,
    ) -> None:
        self.config = config
        self.model_size = str(config.get(
            "stt",
            "faster_whisper",
            "model_size",
        ))
        self.language = config.get(
            "stt",
            "faster_whisper",
            "language",
            default=None,
            required=False,
        )
        self.preferred_device = str(config.get(
            "stt",
            "faster_whisper",
            "device",
            default="cuda",
            required=False,
        )).lower()
        self.compute_type = str(config.get(
            "stt",
            "faster_whisper",
            "compute_type",
            default="float16",
            required=False,
        ))
        self.cpu_compute_type = str(config.get(
            "stt",
            "faster_whisper",
            "cpu_compute_type",
            default="int8",
            required=False,
        ))
        self.initial_prompt = str(config.get(
            "stt",
            "faster_whisper",
            "initial_prompt",
            default="",
            required=False,
        )).strip()

        self.sample_rate = int(config.get(
            "vad",
            "silero",
            "sample_rate",
        ))
        self.device_index = config.get(
            "vad",
            "silero",
            "device_index",
            default=None,
            required=False,
        )
        self.device_name = config.get(
            "vad",
            "silero",
            "device_name",
            default=None,
            required=False,
        )
        self.preferred_host_api = config.get(
            "vad",
            "silero",
            "preferred_host_api",
            default=None,
            required=False,
        )
        self.capture_sample_rate = config.get(
            "vad",
            "silero",
            "capture_sample_rate",
            default=None,
            required=False,
        )
        self.using_gpu = False

        self.vad = VoiceActivityDetector(
            sample_rate=self.sample_rate,
            device_index=self.device_index,
            device_name=self.device_name,
            preferred_host_api=self.preferred_host_api,
            capture_sample_rate=self.capture_sample_rate,
            threshold=float(config.get(
                "vad",
                "silero",
                "threshold",
            )),
            silence_ms=int(config.get(
                "vad",
                "silero",
                "silence_ms",
            )),
            minimum_speech_ms=int(config.get(
                "vad",
                "silero",
                "minimum_speech_ms",
            )),
            pre_speech_ms=int(config.get(
                "vad",
                "silero",
                "pre_speech_ms",
            )),
            start_timeout_seconds=float(config.get(
                "vad",
                "silero",
                "start_timeout_seconds",
            )),
            maximum_recording_seconds=float(config.get(
                "vad",
                "silero",
                "maximum_recording_seconds",
            )),
        )

        self._load_model()
        self.vad.start()

    def _load_model(self) -> None:
        if self.preferred_device == "cpu":
            self._load_cpu_model()
            return

        _configure_cuda_runtime()
        try:
            self.model = WhisperModel(
                self.model_size,
                device="cuda",
                compute_type=self.compute_type,
            )
            self.using_gpu = True
            print("[STT] Faster-Whisper configured for GPU.")
        except Exception as error:
            print(f"[STT] GPU initialization failed: {error}")
            self._load_cpu_model()

    def _load_cpu_model(self) -> None:
        self.model = WhisperModel(
            self.model_size,
            device="cpu",
            compute_type=self.cpu_compute_type,
        )
        self.using_gpu = False
        print("[STT] Faster-Whisper loaded on CPU.")

    def transcribe(self, audio_path: str) -> str:
        print("[STT] Transcribing...")

        try:
            return self._run_transcription(audio_path)
        except RuntimeError as error:
            if not self.using_gpu:
                print(f"[STT Error] {error}")
                return ""

            print(f"[STT] GPU transcription failed: {error}")
            print("[STT] Retrying on CPU...")
            self._load_cpu_model()
            try:
                return self._run_transcription(audio_path)
            except Exception as cpu_error:
                print(f"[STT CPU Error] {cpu_error}")
                return ""
        except Exception as error:
            print(f"[STT Error] {error}")
            return ""

    def _run_transcription(self, audio_path: str) -> str:
        segments, _ = self.model.transcribe(
            audio_path,
            language=self.language,
            beam_size=1,
            # Silero already captured a speech-only clip. Running Whisper's VAD
            # again caused legitimate soft sentences to disappear.
            vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt or None,
        )

        text = " ".join(
            segment.text.strip()
            for segment in segments
            if segment.text.strip()
        ).strip()

        if text:
            print(f"You said: {text}")
        else:
            print("[STT] No speech detected.")
        return text

    def listen_and_transcribe(
        self,
        on_speech_start: Callable[[], None] | None = None,
        is_tts_speaking: Callable[[], bool] | None = None,
        echo_text_provider: Callable[[], str] | None = None,
    ) -> str:
        audio = self.vad.record(
            on_speech_start=on_speech_start,
            is_barge_in=is_tts_speaking,
        )

        if audio is None or audio.size == 0:
            return ""

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
        ) as temporary_file:
            wav_path = temporary_file.name

        try:
            with wave.open(wav_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(audio.tobytes())

            transcript = self.transcribe(wav_path)
            if (
                transcript
                and echo_text_provider is not None
                and self._looks_like_tts_echo(
                    transcript,
                    echo_text_provider(),
                )
            ):
                print("[STT] Ignored probable speaker echo.")
                return ""
            return transcript
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

    def close(self) -> None:
        """Close the microphone stream when the application shuts down."""
        self.vad.close()

    @staticmethod
    def _looks_like_tts_echo(transcript: str, spoken_text: str) -> bool:
        """Reject audio that is almost certainly Elaina hearing herself."""

        def normalize(value: str) -> str:
            characters = (
                character.lower()
                for character in value
                if character.isalnum() or character.isspace()
            )
            return " ".join("".join(characters).split())

        heard = normalize(transcript)
        spoken = normalize(spoken_text)
        if len(heard) < 8 or not spoken:
            return False
        if heard in spoken:
            return True
        return SequenceMatcher(None, heard, spoken).ratio() >= 0.78
