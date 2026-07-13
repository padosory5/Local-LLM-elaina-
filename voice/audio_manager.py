from __future__ import annotations

import queue
import threading

from voice.manager import VoiceManager
from core.event_bus import EventBus

class AudioManager:
    def __init__(
        self,
        event_bus: EventBus | None = None,
    ) -> None:
        self.voice = VoiceManager()
        self.events = event_bus

        self._queue: queue.Queue[
            tuple[int, str]
        ] = queue.Queue()

        self._lock = threading.Lock()
        self._generation = 0
        self._speaking = False

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
        )
        self._worker_thread.start()

    def speak(self, text: str) -> None:
        text = text.strip()

        if not text:
            return

        with self._lock:
            generation = self._generation

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

                self._queue.task_done()

    def stop(self) -> None:
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
        
        if was_speaking and self.events is not None:
            self.events.emit("tts_interrupted")

    def is_speaking(self) -> bool:
        with self._lock:
            return self._speaking or not self._queue.empty()