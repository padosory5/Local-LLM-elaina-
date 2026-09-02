from __future__ import annotations

import queue
import threading
import time

from core import timing

from voice.manager import VoiceManager
from core.event_bus import EventBus
from config.loader import Config
from brain.text_filter import TextFilter

# How long a just-finished TTS line remains usable as an echo reference.
# Real speaker-loopback echo arrives within about a second of Elaina
# finishing; without a bound, _recent_text stayed "the last thing Elaina
# said" for the rest of the session, so an unrelated later reply that
# happened to mention the same words (e.g. recommending "open Spotify"
# just before the user actually says "open Spotify") could silently
# swallow a real, unrelated command as an echo.
_ECHO_REFERENCE_WINDOW_SECONDS = 6.0

class AudioManager:

    def __init__(
        self,
        config: Config,
        event_bus: EventBus | None = None,
    ) -> None:
        self.voice = VoiceManager(
            config=config,
            event_bus=event_bus,
        )
        self.events = event_bus
        self._response_language = str(
            config.get(
                "language",
                "response",
                default="en",
                required=False,
            )
            or "en"
        ).strip().lower()

        self._queue: queue.Queue[
            tuple[int, str]
        ] = queue.Queue()

        self._lock = threading.Lock()
        self._generation = 0
        self._speaking = False
        # When this turn's first sentence was handed to the voice, so the
        # gap until sound actually starts can be measured.
        self._spoke_this_turn_at = None
        self._current_text = ""
        self._recent_text = ""
        self._recent_text_expires_at = 0.0

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
        )
        self._worker_thread.start()

    def speak(self, text: str) -> None:
        text = TextFilter.for_configured_speech(
            text,
            response_language=self._response_language,
        )

        if not text:
            return

        with self._lock:
            generation = self._generation

        if self._spoke_this_turn_at is None:
            self._spoke_this_turn_at = time.perf_counter()
        self._queue.put((generation, text))

    def _worker_loop(self) -> None:
        while True:
            generation, text = self._queue.get()

            try:
                with self._lock:
                    current_generation = self._generation

                # Ignore audio queued before an interruption.
                if generation != current_generation:
                    continue

                with self._lock:
                    self._speaking = True
                    if self._spoke_this_turn_at is not None:
                        # Text ready -> first audible audio. Synthesis total
                        # is a different number and a less useful one: what
                        # the person notices is the silence before she starts.
                        timing.mark(
                            "tts_start",
                            time.perf_counter() - self._spoke_this_turn_at,
                        )
                        self._spoke_this_turn_at = None
                    self._current_text = text
                    self._recent_text = text

                if self.events is not None:
                    self.events.emit(
                        "tts_started",
                        text=text,
                    )

                self.voice.speak(text)

                if self.events is not None:
                    self.events.emit(
                        "tts_finished",
                        text=text,
                    )

            except Exception as error:
                print(f"[TTS Error] {error}")

            finally:
                with self._lock:
                    self._speaking = False
                    self._current_text = ""
                    self._recent_text_expires_at = (
                        time.monotonic() + _ECHO_REFERENCE_WINDOW_SECONDS
                    )

                self._queue.task_done()

    def stop(self) -> None:
        interrupt_started = time.perf_counter()
        was_speaking = self.is_speaking()
        # Invalidates all sentences queued before this interruption.
        with self._lock:
            self._generation += 1

        # Stop the sentence currently playing.
        self.voice.stop()

        # Remove pending sentences from the queue.
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        
        if was_speaking:
            # How long it takes for sound to actually stop once she is
            # interrupted. "Feels immediate" is the requirement, and it is
            # not the same thing as the request being accepted immediately.
            timing.mark("interrupt_stop", time.perf_counter() - interrupt_started)
        self._spoke_this_turn_at = None
        if was_speaking and self.events is not None:
            self.events.emit("tts_interrupted")

    def is_speaking(self) -> bool:
        with self._lock:
            return self._speaking or not self._queue.empty()

    def echo_reference_text(self) -> str:
        """Return recent TTS text so STT can reject speaker-loopback echoes.

        Only valid for a short window after speech ends -- a real echo
        arrives almost immediately, and an unbounded reference would keep
        comparing brand new, unrelated user requests against whatever
        Elaina happened to say much earlier in the conversation.
        """
        with self._lock:
            if self._current_text:
                return self._current_text
            if time.monotonic() < self._recent_text_expires_at:
                return self._recent_text
            return ""
