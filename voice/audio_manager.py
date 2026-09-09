from __future__ import annotations

import os
import queue
import re
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

# Sentence ends in both scripts. Korean declaratives end on a period like
# English ones, and the second branch catches the case with no space after
# it.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])(?=[가-힣])")

# Below this, a chunk is merged into the next one. A three-word sentence
# spoken alone sounds clipped, and it costs a whole extra synthesis round
# trip to sound worse -- "Got it." and "Anything else?" belong to the
# sentence beside them.
_MIN_SPOKEN_WEIGHT = 40

# A Hangul syllable is a whole syllable; a Latin letter is a fraction of
# one. Counting characters alike meant every Korean sentence measured
# short, so a Korean reply was never split and Korean got none of this --
# a threshold calibrated on English, quietly applied to both. Rule 4, in
# the one place where the unit of measurement is the bug.
_SYLLABIC = re.compile(r"[가-힣぀-ヿ一-鿿]")


def _spoken_weight(text: str) -> float:
    """Roughly how much speech this is, in English-character equivalents."""
    said = str(text or "")
    syllables = len(_SYLLABIC.findall(said))
    return (len(said) - syllables) + syllables * 2.5


def _discard(path: str) -> None:
    """Delete an audio file nobody is going to play."""
    try:
        os.remove(path)
    except (OSError, TypeError):
        pass


def _speakable_chunks(text: str) -> list[str]:
    """One reply, split into the pieces she can start speaking soonest.

    The whole reply used to go to the voice in a single call, so nothing
    was audible until the last word had been synthesised. Measured against
    ElevenLabs on a typical three-sentence reply: **1.31s for the whole
    thing, 0.68s for the first sentence.** The rest is synthesised while
    that one is playing, so the person hears her twice as fast and the
    saving grows with the length of the answer.

    Merging short sentences forward is what keeps this from being a
    downgrade: more chunks means more round trips, and a chunk too short
    to carry prosody sounds worse than the same words inside a longer one.
    """
    parts = [
        part.strip() for part in _SENTENCE_END.split(str(text or "").strip())
        if part.strip()
    ]
    if not parts:
        return []

    chunks: list[str] = []
    for part in parts:
        if chunks and _spoken_weight(chunks[-1]) < _MIN_SPOKEN_WEIGHT:
            chunks[-1] = f"{chunks[-1]} {part}"
        else:
            chunks.append(part)
    # A trailing fragment has nothing after it to merge into, so it joins
    # what came before rather than being spoken on its own.
    if len(chunks) > 1 and _spoken_weight(chunks[-1]) < _MIN_SPOKEN_WEIGHT:
        tail = chunks.pop()
        chunks[-1] = f"{chunks[-1]} {tail}"
    return chunks

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

        # Chunks already synthesised and waiting to be played, keyed by
        # their text. Small by construction: only ever the one chunk after
        # the one currently playing.
        self._ready_audio: dict[str, str] = {}
        self._prefetching = False

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
        )
        self._worker_thread.start()

    def _start_prefetch(self, generation: int) -> None:
        """Begin synthesising the next queued chunk, without waiting for it."""
        with self._lock:
            if self._prefetching or self._generation != generation:
                return
            upcoming = [
                text for queued_generation, text in tuple(self._queue.queue)
                if queued_generation == generation
                and text not in self._ready_audio
            ]
            if not upcoming:
                return
            self._prefetching = True

        def run() -> None:
            try:
                path = self.voice.synthesize(upcoming[0])
            except Exception as error:
                print(f"[TTS Prefetch] {error}")
                path = ""
            with self._lock:
                self._prefetching = False
                # A cancellation while this was in flight makes it rubbish.
                if path and self._generation == generation:
                    self._ready_audio[upcoming[0]] = path
                    return
            if path:
                _discard(path)

        threading.Thread(target=run, daemon=True).start()

    def speak_in(self, language: str) -> None:
        """Speak in this language from now on.

        This was read once from config.yaml at construction and never again,
        which is the same fault ChatEngine had and a worse one to leave
        here: for_configured_speech strips Hangul when the language is
        English, so every Korean reply was replaced at the audio boundary
        with "The result is shown on screen." She answered correctly in
        Korean and then refused to say it out loud.
        """
        language = str(language or "").strip().lower()
        if language:
            self._response_language = language

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

        # One sentence at a time, so the first sound arrives after the
        # first sentence is synthesised rather than the whole reply. The
        # worker synthesises the next chunk while this one plays, so the
        # split costs nothing in continuity.
        for chunk in _speakable_chunks(text):
            self._queue.put((generation, chunk))

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

                # Synthesise the next chunk while this one plays. Without
                # it, splitting a reply into sentences would trade the
                # long silence at the start for a short one between every
                # pair -- about 0.7s each, measured. The voice only has to
                # offer the two halves; Piper does not, and falls through
                # to the whole operation.
                ready = self._ready_audio.pop(text, None)
                pipelined = (
                    hasattr(self.voice, "synthesize")
                    and hasattr(self.voice, "play_file")
                )
                if pipelined:
                    if ready is None:
                        ready = self.voice.synthesize(text)
                    self._start_prefetch(generation)
                    self.voice.play_file(ready)
                else:
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

        # Audio already synthesised for sentences that will now never be
        # spoken. Left behind, these are temp files that never get deleted.
        with self._lock:
            orphans = list(self._ready_audio.values())
            self._ready_audio.clear()
        for path in orphans:
            _discard(path)

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
