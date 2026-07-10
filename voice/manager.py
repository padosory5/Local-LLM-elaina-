from voice.config import VOICE_ENGINE

from voice.piper import PiperTTS


class VoiceManager:

    def __init__(self):

        if VOICE_ENGINE == "piper":
            self.engine = PiperTTS()

        else:
            raise ValueError("Unknown voice engine")

    def speak(self, text):

        self.engine.speak(text)