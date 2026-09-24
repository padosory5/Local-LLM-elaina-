"""The transcriber listens for the conversation's words, in its language.

Two changes to how Whisper is asked, both about the context it never had:

* an uncertain language reading follows the conversation. Before, a short
  Korean clip detected as Japanese was re-transcribed as English -- the
  first allowed language -- which turns Korean speech into nonsense;
* the names and acronyms the conversation holds are passed as hotwords,
  kept short, and a transcript that is nothing but those hints read back
  (Whisper's known failure with a prompt, on a quiet clip) is dropped.

No model is loaded here: a fake stands in for it and records what it was
asked.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from voice.stt import SpeechToText
from voice.transcription_policy import retry_language_for_detection

ALLOWED = ("en", "ko")


def _retry(detected: str, probability: float, preferred=None):
    return retry_language_for_detection(
        configured_language=None, detected_language=detected,
        probability=probability, allowed_languages=ALLOWED,
        minimum_probability=0.60, preferred_language=preferred,
    )


class TheRetryFollowsTheConversationTests(unittest.TestCase):

    def test_an_unexpected_language_retries_in_the_conversations(self):
        self.assertEqual(_retry("ja", 0.90, preferred="ko"), "ko")

    def test_an_uncertain_reading_retries_in_the_conversations(self):
        self.assertEqual(_retry("en", 0.45, preferred="ko"), "ko")

    def test_a_confident_reading_is_never_overruled(self):
        # Switching language mid-conversation still works.
        self.assertIsNone(_retry("en", 0.92, preferred="ko"))

    def test_without_a_conversation_the_old_rule_stands(self):
        self.assertEqual(_retry("ja", 0.90), "en")
        self.assertEqual(_retry("ko", 0.40), "ko")


class FakeModel:
    """Records every transcribe call; answers with scripted readings."""

    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append(kwargs)
        language, probability, text = self.readings.pop(0)
        segment = SimpleNamespace(text=text, no_speech_prob=0.0, avg_logprob=-0.2)
        return [segment], SimpleNamespace(
            language=language, language_probability=probability,
        )


def _stt(readings) -> SpeechToText:
    stt = SpeechToText.__new__(SpeechToText)
    stt.language = None
    stt.allowed_languages = ALLOWED
    stt.language_retry_threshold = 0.60
    stt.no_speech_threshold = 0.60
    stt.log_probability_threshold = -1.0
    stt.initial_prompt = ""
    stt.beam_size = 1
    stt.hotwords_from_conversation = True
    stt.conversation_language = ""
    stt.hotwords = ""
    stt.unclear_lowest_word = 0.30
    stt.unclear_average_word = 0.65
    stt.last_heard_unclearly = False
    stt.last_detected_language = ""
    stt.last_language_probability = 0.0
    stt.model = FakeModel(readings)
    return stt


class WhisperOnSilenceTests(unittest.TestCase):
    """large-v3-turbo says "감사합니다." over silence; a person also says it.

    Measured: near-silence was first read as Portuguese at 0.19 ("Obrigado.")
    and, retried in Korean, came back "감사합니다."; a real 감사합니다 was read
    as Korean at 0.99-1.00, a real "Thank you." as English at 0.97-1.00, and a
    real "고마워" as Korean at only 0.19.
    """

    def test_silence_retried_in_korean_is_dropped(self):
        stt = _stt([("pt", 0.19, "Obrigado."), ("ko", 1.0, "감사합니다.")])
        stt.conversation_language = "ko"

        self.assertEqual(stt._run_transcription("quiet.wav"), "")

    def test_real_short_replies_are_kept(self):
        for reading in (
            ("ko", 0.99, "감사합니다."),
            ("en", 0.99, "Thank you."),
        ):
            with self.subTest(reading=reading):
                stt = _stt([reading])
                stt.conversation_language = "ko"
                self.assertEqual(stt._run_transcription("clip.wav"), reading[2])

    def test_an_unsure_real_word_is_kept(self):
        # Read as Korean at 0.19, retried in Korean -- and not a phrase
        # Whisper says over silence.
        stt = _stt([("ko", 0.19, "고마워."), ("ko", 1.0, "고마워.")])
        stt.conversation_language = "ko"

        self.assertEqual(stt._run_transcription("clip.wav"), "고마워.")

    def test_the_rule(self):
        from voice.transcription_policy import reads_as_silence

        self.assertTrue(reads_as_silence("Thank you.", detected_language="en", probability=0.28))
        self.assertFalse(reads_as_silence("Thank you.", detected_language="en", probability=0.99))
        self.assertFalse(reads_as_silence("오늘 좀 힘들었어", detected_language="pt", probability=0.19))


class TheTranscriberIsToldTheContextTests(unittest.TestCase):

    def test_hotwords_and_the_retry_language_reach_the_model(self):
        stt = _stt([("ja", 0.9, "ｼｰﾋﾟｰﾃｨｰ"), ("ko", 1.0, "CPT 신청하려면 뭐가 필요해?")])
        stt.set_conversation_context(language="ko", terms=("CPT", "OPT", "UW"))

        text = stt._run_transcription("clip.wav")

        self.assertEqual(text, "CPT 신청하려면 뭐가 필요해?")
        self.assertEqual(stt.model.calls[0]["hotwords"], "CPT, OPT, UW")
        self.assertIsNone(stt.model.calls[0]["language"])
        self.assertEqual(stt.model.calls[1]["language"], "ko")

    def test_the_hint_list_stays_short(self):
        stt = _stt([])
        terms = ("CPT", "OPT", "UW", "베인브리지", "시애틀", "인천공항",
                 "International Student Services", "Curricular Practical Training",
                 "유학생", "F-1")

        stt.set_conversation_context(language="ko", terms=terms)

        self.assertLessEqual(len(stt.hotwords.split(", ")), 8)
        self.assertLessEqual(len(stt.hotwords), 70)
        self.assertTrue(stt.hotwords.startswith("CPT, OPT, UW"))

    def test_the_hints_read_back_are_not_speech(self):
        stt = _stt([(None, 0.0, "CPT, OPT")])
        stt.set_conversation_context(language="ko", terms=("CPT", "OPT", "UW"))
        stt.model.readings = [("ko", 0.95, "CPT, OPT")]

        self.assertEqual(stt._run_transcription("quiet.wav"), "")

    def test_a_list_read_back_with_a_word_of_its_own_is_not_speech(self):
        # Measured at 5 dB with place names as hints.
        stt = _stt([("ko", 0.9, "워싱턴 대학, 제주도, 밴쿠버, 샌프란시스코, 로스앤젤레스")])
        stt.set_conversation_context(
            language="ko", terms=("제주도", "밴쿠버", "샌프란시스코", "로스앤젤레스"),
        )

        self.assertEqual(stt._run_transcription("noisy.wav"), "")

    def test_a_real_sentence_naming_three_hints_is_kept(self):
        said = "인천공항에서 시애틀 거쳐서 밴쿠버까지 가는 방법 알려줘"
        stt = _stt([("ko", 0.95, said)])
        stt.set_conversation_context(
            language="ko", terms=("인천공항", "시애틀", "밴쿠버"),
        )

        self.assertEqual(stt._run_transcription("clip.wav"), said)

    def test_a_real_sentence_with_a_hint_in_it_is_kept(self):
        stt = _stt([("ko", 0.95, "CPT 신청 기간이 언제야?")])
        stt.set_conversation_context(language="ko", terms=("CPT", "OPT"))

        self.assertEqual(stt._run_transcription("clip.wav"), "CPT 신청 기간이 언제야?")

    def test_switched_off_in_config_nothing_is_passed(self):
        stt = _stt([("ko", 0.95, "안녕")])
        stt.hotwords_from_conversation = False
        stt.set_conversation_context(language="ko", terms=("CPT",))

        stt._run_transcription("clip.wav")

        self.assertIsNone(stt.model.calls[0]["hotwords"])


if __name__ == "__main__":
    unittest.main()
