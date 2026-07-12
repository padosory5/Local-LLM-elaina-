import ctypes
import os
import sysconfig

# Correct site-packages location inside the virtual environment
site_packages = sysconfig.get_path("purelib")

cublas_dir = os.path.join(
    site_packages,
    "nvidia",
    "cublas",
    "bin",
)

cudnn_dir = os.path.join(
    site_packages,
    "nvidia",
    "cudnn",
    "bin",
)

cuda_runtime_dir = os.path.join(
    site_packages,
    "nvidia",
    "cuda_runtime",
    "bin",
)

cuda_directories = [
    cublas_dir,
    cudnn_dir,
    cuda_runtime_dir,
]

DLL_DIRECTORY_HANDLES = []

for directory in cuda_directories:
    if os.path.isdir(directory):
        os.environ["PATH"] = (
            directory
            + os.pathsep
            + os.environ.get("PATH", "")
        )

        if hasattr(os, "add_dll_directory"):
            DLL_DIRECTORY_HANDLES.append(
                os.add_dll_directory(directory)
            )

        print(f"[CUDA] Added: {directory}")

cublas_lt_path = os.path.join(
    cublas_dir,
    "cublasLt64_12.dll",
)

cublas_path = os.path.join(
    cublas_dir,
    "cublas64_12.dll",
)

cudnn_path = os.path.join(
    cudnn_dir,
    "cudnn64_9.dll",
)

for dll_path in [
    cublas_lt_path,
    cublas_path,
    cudnn_path,
]:
    if not os.path.isfile(dll_path):
        raise FileNotFoundError(
            f"Required CUDA DLL not found: {dll_path}"
        )

ctypes.WinDLL(cublas_lt_path)
ctypes.WinDLL(cublas_path)
ctypes.WinDLL(cudnn_path)

print("[CUDA] cuBLAS and cuDNN loaded successfully.")

import tempfile
import threading
import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel


class SpeechToText:

    def __init__(
        self,
        model_size: str = "small",
        sample_rate: int = 16000,
        language: str | None = None,
    ):
        self.model_size = model_size
        self.sample_rate = sample_rate
        self.language = language
        self.using_gpu = False

        self._load_gpu_model()

    def _load_gpu_model(self) -> None:
        try:
            self.model = WhisperModel(
                self.model_size,
                device="cuda",
                compute_type="float16",
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
            compute_type="int8",
        )

        self.using_gpu = False
        print("[STT] Faster-Whisper loaded on CPU.")

    def record_until_enter(self) -> str:
        frames: list[np.ndarray] = []
        stop_event = threading.Event()

        def callback(indata, frame_count, time_info, status):
            if status:
                print(f"\n[Microphone Warning] {status}")

            frames.append(indata.copy())

        def wait_for_enter():
            input()
            stop_event.set()

        print("\nListening... Press Enter to stop recording.")

        listener = threading.Thread(
            target=wait_for_enter,
            daemon=True,
        )
        listener.start()

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                callback=callback,
            ):
                stop_event.wait()

        except sd.PortAudioError as error:
            print(f"[Microphone Error] {error}")
            return ""

        if not frames:
            print("[STT] No audio was recorded.")
            return ""

        audio = np.concatenate(frames, axis=0)

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

            return self.transcribe(wav_path)

        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

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
            beam_size=5,
            vad_filter=True,
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