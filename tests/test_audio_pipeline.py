"""How long before she makes a sound, and does she stop making it.

Piper ran on this machine, so "synthesise the whole reply, then play it"
cost almost nothing. ElevenLabs is a network call, and the same code then
meant the person heard silence until the last word of the answer had been
synthesised. Measured against the real API on a three-sentence reply:

    whole reply    1.31s
    first sentence 0.68s

So the reply is handed over a sentence at a time, and the next sentence is
synthesised while the current one plays -- otherwise splitting it would
only trade one long silence for a short one between every pair.

The timings here are simulated. What is under test is the *pipeline*: that
playback starts on the first chunk, that the chunks run back to back, and
that an interruption leaves nothing playing and no files behind.
"""

import os
import queue
import tempfile
import threading
import time
import unittest

from voice import audio_manager

SYNTH_SECONDS = 0.20
PLAY_SECONDS = 0.40


class FakeVoice:
    """A voice with the two halves, and a stopwatch on each."""

    def __init__(self):
        self.events = []
        self.made = []
        self.lock = threading.Lock()
        self.started = time.perf_counter()
        self._stop = threading.Event()

    def _mark(self, kind, detail):
        with self.lock:
            self.events.append((time.perf_counter() - self.started, kind, detail))

    def synthesize(self, text):
        self._mark("synth", text)
        time.sleep(SYNTH_SECONDS)
        handle = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        handle.close()
        with self.lock:
            self.made.append(handle.name)
        return handle.name

    def play_file(self, path):
        self._mark("play", path)
        deadline = time.perf_counter() + PLAY_SECONDS
        while time.perf_counter() < deadline:
            if self._stop.is_set():
                break
            time.sleep(0.01)
        try:
            os.remove(path)
        except OSError:
            pass

    def speak(self, text):
        self.play_file(self.synthesize(text))

    def stop(self):
        self._stop.set()

    def plays(self):
        return [stamp for stamp, kind, _ in self.events if kind == "play"]


class PlainVoice:
    """A voice offering only ``speak`` -- Piper has nothing to overlap."""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()
        self.started = time.perf_counter()

    def _mark(self, kind, detail):
        with self.lock:
            self.events.append((time.perf_counter() - self.started, kind, detail))

    def plays(self):
        return [stamp for stamp, kind, _ in self.events if kind == "play"]

    def stop(self):
        pass

    def speak(self, text):
        self._mark("synth", text)
        time.sleep(SYNTH_SECONDS)
        self._mark("play", text)
        time.sleep(PLAY_SECONDS)


def _manager(voice):
    """An AudioManager around a fake voice, without touching real audio."""
    manager = audio_manager.AudioManager.__new__(audio_manager.AudioManager)
    manager.voice = voice
    manager.events = None
    manager._response_language = "en"
    manager._queue = queue.Queue()
    manager._lock = threading.Lock()
    manager._generation = 0
    manager._speaking = False
    manager._spoke_this_turn_at = None
    manager._current_text = ""
    manager._recent_text = ""
    manager._recent_text_expires_at = 0.0
    manager._ready_audio = {}
    manager._prefetching = False
    threading.Thread(target=manager._worker_loop, daemon=True).start()
    return manager


REPLY = (
    "The LG UltraGear is a solid pick at that price. "
    "It runs at 165Hz with a 1ms response time. "
    "Want me to check whether it is still in stock?"
)


class OneReplyIsSpokenSentenceBySentenceTests(unittest.TestCase):

    def test_a_reply_is_split_where_a_person_would_pause(self):
        self.assertEqual(len(audio_manager._speakable_chunks(REPLY)), 3)

    def test_a_short_reply_is_not_chopped_up(self):
        """A three-word sentence alone sounds clipped and costs a round trip."""
        for said in ("Got it.", "Anytime.", "Sure. Got it. Done."):
            with self.subTest(said=said):
                self.assertEqual(len(audio_manager._speakable_chunks(said)), 1)

    def test_a_trailing_fragment_joins_what_came_before(self):
        chunks = audio_manager._speakable_chunks(
            "That monitor is a good price for what it does. Yes."
        )
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].endswith("Yes."))

    def test_korean_splits_too(self):
        chunks = audio_manager._speakable_chunks(
            "지금은 165Hz로 동작하고 이번 주에 할인 중입니다. "
            "재고가 남아 있는지 확인해 드릴까요? 바로 알아보겠습니다."
        )
        self.assertGreaterEqual(len(chunks), 2)

    def test_nothing_at_all_is_no_chunks(self):
        self.assertEqual(audio_manager._speakable_chunks("   "), [])


class SynthesisRunsAheadOfPlaybackTests(unittest.TestCase):
    """Otherwise splitting only moves the silence rather than removing it."""

    def test_sound_starts_after_the_first_sentence_not_the_whole_reply(self):
        voice = FakeVoice()
        _manager(voice).speak(REPLY)
        time.sleep(SYNTH_SECONDS + PLAY_SECONDS * 3 + 0.35)

        plays = voice.plays()
        self.assertEqual(len(plays), 3, voice.events)
        # One synthesis, not three, before the first sound.
        self.assertLess(plays[0], SYNTH_SECONDS * 2)

    def test_the_sentences_run_back_to_back(self):
        voice = FakeVoice()
        _manager(voice).speak(REPLY)
        time.sleep(SYNTH_SECONDS + PLAY_SECONDS * 3 + 0.35)

        plays = voice.plays()
        self.assertEqual(len(plays), 3, voice.events)
        for earlier, later in zip(plays, plays[1:]):
            # A gap of a whole synthesis between sentences is the thing
            # the prefetch exists to prevent.
            self.assertLess(
                later - earlier, PLAY_SECONDS + SYNTH_SECONDS * 0.6,
                f"a synthesis-sized gap opened up: {plays}",
            )

    def test_a_voice_without_the_two_halves_still_works(self):
        """Piper has nothing to overlap and must not break."""
        voice = PlainVoice()
        _manager(voice).speak(REPLY)
        time.sleep((SYNTH_SECONDS + PLAY_SECONDS) * 3 + 0.35)
        self.assertEqual(len(voice.plays()), 3, voice.events)


class StoppingLeavesNothingBehindTests(unittest.TestCase):

    def test_an_interruption_drops_the_sentences_not_yet_spoken(self):
        voice = FakeVoice()
        manager = _manager(voice)
        manager.speak(REPLY)
        time.sleep(SYNTH_SECONDS + 0.10)
        manager.stop()
        time.sleep(PLAY_SECONDS * 3)

        self.assertLessEqual(
            len(voice.plays()), 1,
            f"kept talking after being stopped: {voice.events}",
        )

    def test_audio_synthesised_for_a_cancelled_turn_is_deleted(self):
        """Prefetched files nobody plays are temp files nobody deletes."""
        voice = FakeVoice()
        manager = _manager(voice)
        manager.speak(REPLY)
        time.sleep(SYNTH_SECONDS + 0.10)
        manager.stop()
        time.sleep(SYNTH_SECONDS * 3 + 0.35)

        with manager._lock:
            self.assertEqual(manager._ready_audio, {})
        left = [path for path in voice.made if os.path.exists(path)]
        self.assertEqual(left, [], f"left temp files behind: {left}")


if __name__ == "__main__":
    unittest.main()
