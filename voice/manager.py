from __future__ import annotations

import os

from config.loader import Config
from core.event_bus import EventBus
from voice.elevenlabs import ElevenLabsTTS
from voice.piper import PiperTTS
from voice.silent import SilentTTS


# Values of ELAINA_TTS that turn speech off. The variable exists for test
# and verification runs: set by scripts/ launchers and tests/run_tests.py so
# that measuring her never spends ElevenLabs credits.
_OFF = frozenset({"0", "off", "false", "no", "none", "silent"})


def speech_enabled(config: Config) -> bool:
    """Whether a real voice provider may be reached at all.

    ``tts.enabled`` was declared -- the turn harness sets it to False -- and
    read by nothing, so the harness built a live provider anyway. It is read
    here now, and ELAINA_TTS overrides it from the environment so a launcher
    can silence a backend without editing config.yaml.
    """
    override = os.environ.get("ELAINA_TTS", "").strip().lower()
    if override in _OFF:
        return False
    return bool(
        config.get("tts", "enabled", default=True, required=False)
    )


class VoiceManager:

    def __init__(
        self,
        config: Config,
        event_bus: EventBus | None = None,
    ) -> None:
        if not speech_enabled(config):
            # Said once at startup, so a verification log shows at a glance
            # that nothing it drove reached a paid voice.
            print("[TTS] Speech is off (ELAINA_TTS / tts.enabled); "
                  "no voice provider will be called.")
            self.engine = SilentTTS()
            return

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
