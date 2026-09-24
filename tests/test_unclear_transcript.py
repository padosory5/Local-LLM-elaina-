"""When the transcriber is guessing at a whole sentence, she hears it as unclear.

The numbers are real: large-v3-turbo's word probabilities on synthesised
Korean speech with noise, beside each clip's character error. A garbled
transcript has a doubtful word *and* a weak average; a sound sentence with
one misheard name has the first without the second, and is left to the
near-miss repair. A one-word reply is never judged.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from voice.stt import SpeechToText
from voice.transcription_policy import heard_unclearly


class TheRuleTests(unittest.TestCase):

    def test_garbled_transcripts_are_unclear(self):
        # Shaped like the garbled clips measured: averages of 0.36-0.55
        # ("제인브리지 산을 타고 있습니다." averaged 0.36 at cer 0.75), with a
        # least-sure word under 0.30.
        for lowest, average in ((0.12, 0.36), (0.20, 0.49), (0.25, 0.55)):
            with self.subTest(lowest=lowest):
                words = [lowest] + [average * 2 - lowest] * 1
                self.assertTrue(heard_unclearly(words))

    def test_sound_sentences_are_not(self):
        for words in (
            [0.35, 0.93],                    # "오늘 좀 힘들었어" at 5 dB
            [0.45, 0.61],                    # "간단한 걸로" at 3 dB
            [0.14, 0.95, 0.97, 0.92, 0.90],  # one doubtful name, the rest sure
            [0.99, 0.98, 0.97],
        ):
            with self.subTest(words=words):
                self.assertFalse(heard_unclearly(words))

    def test_a_one_word_reply_is_never_judged(self):
        # A real "네" scored 0.41; at 0.1 it is still not asked about.
        self.assertFalse(heard_unclearly([0.1]))
        self.assertFalse(heard_unclearly([]))


class FakeModel:
    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append(kwargs)
        language, probability, text, words = self.readings.pop(0)
        segment = SimpleNamespace(
            text=text, no_speech_prob=0.0, avg_logprob=-0.3,
            words=[SimpleNamespace(probability=p) for p in words],
        )
        return [segment], SimpleNamespace(
            language=language, language_probability=probability,
        )


def _stt(readings) -> SpeechToText:
    stt = SpeechToText.__new__(SpeechToText)
    stt.language = None
    stt.allowed_languages = ("en", "ko")
    stt.language_retry_threshold = 0.60
    stt.no_speech_threshold = 0.60
    stt.log_probability_threshold = -1.0
    stt.initial_prompt = ""
    stt.beam_size = 5
    stt.hotwords_from_conversation = True
    stt.conversation_language = "ko"
    stt.hotwords = ""
    stt.unclear_lowest_word = 0.30
    stt.unclear_average_word = 0.65
    stt.last_heard_unclearly = False
    stt.last_detected_language = ""
    stt.last_language_probability = 0.0
    stt.model = FakeModel(readings)
    return stt


class TheTranscriberSetsItTests(unittest.TestCase):

    def test_a_garbled_clip_is_flagged_and_still_returned(self):
        stt = _stt([("ko", 0.9, "인산부한테 인산비날 가는 것이 있죠", [0.15, 0.30, 0.55, 0.62, 0.40])])

        text = stt._run_transcription("noisy.wav")

        self.assertEqual(text, "인산부한테 인산비날 가는 것이 있죠")
        self.assertTrue(stt.last_heard_unclearly)
        self.assertTrue(stt.model.calls[0]["word_timestamps"])

    def test_a_clear_clip_is_not(self):
        stt = _stt([("ko", 1.0, "인천공항에서 미국 시애틀까지 가는 데 몇 시간 걸려", [0.97, 0.95, 0.91, 0.96, 0.99, 0.98])])

        stt._run_transcription("clip.wav")

        self.assertFalse(stt.last_heard_unclearly)

    def test_each_clip_starts_clear(self):
        stt = _stt([
            ("ko", 0.9, "인산부한테 인산비날 가는 것이 있죠", [0.15, 0.30, 0.55, 0.62, 0.40]),
            ("ko", 1.0, "고마워", [0.99]),
        ])
        stt._run_transcription("noisy.wav")

        stt._run_transcription("clip.wav")

        self.assertFalse(stt.last_heard_unclearly)


if __name__ == "__main__":
    unittest.main()
