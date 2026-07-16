from networkx import config

from config.loader import Config
from voice.piper import PiperTTS
from voice.elevenlabs import ElevenLabsTTS

class VoiceManager:

    def __init__(self, config: Config) -> None:
        provider = config.active_provider("tts")

        if provider == "piper":
            self.engine = PiperTTS(config=config)
        elif provider == "elevenlabs":
            self.engine = ElevenLabsTTS(config=config)
        else:
            raise ValueError(
                f"TTS provider is not implemented: {provider}"
            )

    def speak(self, text: str) -> None:
        self.engine.speak(text)

    def stop(self) -> None:
        self.engine.stop()