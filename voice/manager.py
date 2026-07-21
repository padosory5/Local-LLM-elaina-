from __future__ import annotations

from config.loader import Config
from core.event_bus import EventBus
from voice.elevenlabs import ElevenLabsTTS
from voice.piper import PiperTTS


class VoiceManager:

    def __init__(
        self,
        config: Config,
        event_bus: EventBus | None = None,
    ) -> None:
        provider = config.active_provider("tts")

        if provider == "piper":
            self.engine = PiperTTS(
                config=config,
                event_bus=event_bus,
            )

        elif provider == "elevenlabs":
            self.engine = ElevenLabsTTS(
                config=config,
            )

        else:
            raise ValueError(
                f"TTS provider is not implemented: {provider}"
            )

    def speak(self, text: str) -> None:
        self.engine.speak(text)

    def stop(self) -> None:
        self.engine.stop()