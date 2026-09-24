from __future__ import annotations

import ctypes
import os
import re
import sysconfig
import tempfile
import wave

from core import timing
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path

from faster_whisper import WhisperModel

from config.loader import Config
from voice.vad import VoiceActivityDetector
from voice.transcription_policy import (
    heard_unclearly,
    reads_as_silence,
    retry_language_for_detection,
    segment_is_usable,
)


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
        configured_languages = config.get(
            "stt",
            "faster_whisper",
            "allowed_languages",
            default=["en", "ko"],
            required=False,
        )
        self.allowed_languages = tuple(
            str(language).strip().lower()
            for language in (
                configured_languages
                if isinstance(configured_languages, list)
                else [configured_languages]
            )
            if str(language).strip()
        )
        # What the microphone last heard, for the language decision. Reset
        # per transcription, never stale beyond one utterance.
        self.last_detected_language = ""
        self.last_language_probability = 0.0
        self.language_retry_threshold = float(config.get(
            "stt",
            "faster_whisper",
            "language_retry_threshold",
            default=0.60,
            required=False,
        ))
        self.no_speech_threshold = float(config.get(
            "stt",
            "faster_whisper",
            "no_speech_threshold",
            default=0.60,
            required=False,
        ))
        self.log_probability_threshold = float(config.get(
            "stt",
            "faster_whisper",
            "log_probability_threshold",
            default=-1.0,
            required=False,
        ))
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
        self.beam_size = max(1, int(config.get(
            "stt",
            "faster_whisper",
            "beam_size",
            default=1,
            required=False,
        )))
        self.hotwords_from_conversation = bool(config.get(
            "stt",
            "faster_whisper",
            "hotwords_from_conversation",
            default=True,
            required=False,
        ))
        # What the conversation is about right now, set by the voice loop
        # before each listen. Whisper hears every clip cold otherwise, so
        # the names and acronyms the person has been saying for ten minutes
        # are guessed from scratch every time.
        self.conversation_language = ""
        self.hotwords = ""
        # When a whole transcript is a guess (voice/transcription_policy),
        # the engine asks to hear it again instead of answering it.
        self.unclear_lowest_word = float(config.get(
            "stt", "faster_whisper", "unclear_lowest_word",
            default=0.30, required=False,
        ))
        self.unclear_average_word = float(config.get(
            "stt", "faster_whisper", "unclear_average_word",
            default=0.65, required=False,
        ))
        self.last_heard_unclearly = False
        # The mean word probability of the last transcript (0.0 when there
        # were no words). The engine reads a doubtful one for sense
        # (brain/sense_check.py); a clear one is never second-guessed.
        self.last_word_average = 0.0

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
        self.last_heard_unclearly = False
        self.last_word_average = 0.0
        segments, info = self._transcribe_once(
            audio_path,
            language=self.language,
        )

        detected_language = str(
            self._value(info, "language", "")
        ).strip().lower()
        language_probability = float(
            self._value(info, "language_probability", 0.0) or 0.0
        )
        # Kept, not just used. Whisper's reading of which language was
        # spoken was computed here and then dropped on the floor -- it
        # decided whether to retry the transcription and nothing else. It
        # is the tiebreaker brain/turn_language.py wants when the script
        # and the sound disagree.
        self.last_detected_language = detected_language
        self.last_language_probability = language_probability
        retry_language = retry_language_for_detection(
            configured_language=self.language,
            detected_language=detected_language,
            probability=language_probability,
            allowed_languages=self.allowed_languages,
            minimum_probability=self.language_retry_threshold,
            preferred_language=self.conversation_language or None,
        )
        if retry_language is not None:
            print(
                "[STT] Uncertain or unexpected language "
                f"'{detected_language or 'unknown'}' "
                f"({language_probability:.2f}); retrying as "
                f"'{retry_language}'."
            )
            segments, _ = self._transcribe_once(
                audio_path,
                language=retry_language,
            )

        accepted_text: list[str] = []
        word_probabilities: list[float] = []
        for segment in segments:
            segment_text = str(
                self._value(segment, "text", "")
            ).strip()
            if not segment_text:
                continue
            no_speech_probability = float(
                self._value(segment, "no_speech_prob", 0.0) or 0.0
            )
            average_log_probability = float(
                self._value(segment, "avg_logprob", 0.0) or 0.0
            )
            if not segment_is_usable(
                no_speech_probability=no_speech_probability,
                average_log_probability=average_log_probability,
                no_speech_threshold=self.no_speech_threshold,
                log_probability_threshold=self.log_probability_threshold,
            ):
                continue
            accepted_text.append(segment_text)
            for word in self._value(segment, "words", None) or ():
                probability = self._value(word, "probability", None)
                if probability is not None:
                    word_probabilities.append(float(probability))

        text = " ".join(accepted_text).strip()
        if text and reads_as_silence(
            text,
            detected_language=detected_language,
            probability=language_probability,
        ):
            # "감사합니다." from a clip Whisper could not even place in a
            # language -- the phrase it ends videos with, not speech.
            print(f"[STT] Ignored {text!r}: Whisper's habit on silence "
                  f"(first read as {detected_language or '?'} at "
                  f"{language_probability:.2f}).")
            text = ""
        if text and self._reads_back_the_hints(text):
            # Whisper's known failure with a prompt: on a quiet clip it
            # reads the prompt back as if it had been said.
            print("[STT] Ignored a transcript that only repeats the "
                  "listening hints.")
            text = ""
        if text:
            print(f"You said: {text}")
            if word_probabilities:
                # Logged every time, so the thresholds can be tuned on the
                # person's own voice rather than a synthesised one.
                average = sum(word_probabilities) / len(word_probabilities)
                print(f"[STT] Word confidence: lowest "
                      f"{min(word_probabilities):.2f}, average {average:.2f}")
                self.last_word_average = average
            self.last_heard_unclearly = heard_unclearly(
                word_probabilities,
                lowest_below=self.unclear_lowest_word,
                average_below=self.unclear_average_word,
            )
            if self.last_heard_unclearly:
                print("[STT] Heard too unclearly to act on; she will ask to "
                      "hear it again.")
        else:
            print("[STT] No speech detected.")
        return text

    def set_conversation_context(self, *, language: str = "", terms=()) -> None:
        """Listen for the conversation's own words, in its language.

        At most eight terms and sixty characters: a long prompt is what
        Whisper repeats verbatim on an unclear clip, which is why the
        configured ``initial_prompt`` is kept empty.
        """
        self.conversation_language = str(language or "").strip().lower()[:2]
        if not self.hotwords_from_conversation:
            self.hotwords = ""
            return
        chosen: list[str] = []
        length = 0
        for term in terms or ():
            term = " ".join(str(term or "").split())
            if not term or term in chosen:
                continue
            if len(chosen) >= 8 or length + len(term) > 60:
                break
            chosen.append(term)
            length += len(term) + 2
        self.hotwords = ", ".join(chosen)

    def _reads_back_the_hints(self, text: str) -> bool:
        """Whether a transcript is the hints read back, not speech.

        Measured with noisy clips and a list of place names as hints: at
        5 dB Whisper answered "인천공항, 시애틀, 김포공항, 제주도, ..." and
        "워싱턴 대학, 제주도, 밴쿠버, 샌프란시스코, 로스앤젤레스" -- the list,
        sometimes with a word of its own in front. Both are dropped: all
        hints, or three or more of them in a comma list that is mostly them.
        A real sentence naming three places is not a comma list.
        """
        if not self.hotwords:
            return False
        hints = [hint.strip() for hint in self.hotwords.split(",") if hint.strip()]
        hint_words = {
            word.casefold() for hint in hints
            for word in re.findall(r"[\w'-]+", hint)
        }
        words = [word.casefold() for word in re.findall(r"[\w'-]+", text)]
        if not words:
            return False
        if all(word in hint_words for word in words):
            return True
        present = [hint for hint in hints if hint.casefold() in text.casefold()]
        covered = sum(1 for word in words if word in hint_words)
        return (
            len(present) >= 3
            and text.count(",") >= 2
            and covered >= 0.6 * len(words)
        )

    def _transcribe_once(
        self,
        audio_path: str,
        *,
        language: str | None,
    ):
        return self.model.transcribe(
            audio_path,
            language=language,
            beam_size=self.beam_size,
            # Silero already captured a speech-only clip. Running Whisper's VAD
            # again caused legitimate soft sentences to disappear.
            vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt or None,
            hotwords=self.hotwords or None,
            # Each word's own probability, for heard_unclearly. Measured at
            # no cost: ~425 ms per clip without, ~430 ms with.
            word_timestamps=True,
            no_speech_threshold=self.no_speech_threshold,
            log_prob_threshold=self.log_probability_threshold,
        )

    @staticmethod
    def _value(item, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

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

            # Audio finalised -> transcript ready. Measured separately from
            # the VAD wait above it, because they are fixed by completely
            # different things: one by a silence threshold, one by model size.
            with timing.stage("stt"):
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

    def pause_listening(self) -> None:
        """Stop capturing microphone audio (used when switching to text mode)."""
        self.vad.pause()

    def resume_listening(self) -> bool:
        """Reopen the microphone (used when switching back to voice mode)."""
        return self.vad.resume()

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
        # A short new utterance can coincidentally appear as a literal
        # substring of a much longer, unrelated earlier reply (e.g. Elaina
        # recommending "open Spotify" just before the user actually says
        # "open Spotify"). Require it to cover most of what was said before
        # treating containment alone as echo; a real echo captures close to
        # the full recent utterance, not a small fragment of it.
        if heard in spoken and len(heard) >= 0.6 * len(spoken):
            return True
        return SequenceMatcher(None, heard, spoken).ratio() >= 0.78
