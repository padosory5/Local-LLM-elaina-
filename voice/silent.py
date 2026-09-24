from __future__ import annotations

from voice.base import BaseTTS


class SilentTTS(BaseTTS):
    """A voice that says nothing and calls nothing.

    Every verification run used to speak through the configured provider,
    and the configured provider is ElevenLabs, which is billed per
    character. A twelve-turn dogfood arc spent credits on every reply, and
    the eight live checks that build a ChatEngine in-process did the same.
    Nothing about what she decides depends on the audio being real, so a
    run that is measuring her decisions gets this instead.

    AudioManager is unchanged around it: sentences are still split, queued
    and reported with tts_started / tts_finished, and the echo reference
    still holds what she would have said. Only the provider call is gone.
    """

    def speak(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass
