import threading

from voice.manager import VoiceManager


class AudioManager:

    def __init__(self):
        self.voice = VoiceManager()

        self.thread: threading.Thread | None = None
        self.speaking = False
        self._lock = threading.Lock()

    def speak(self, text: str) -> None:
        with self._lock:
            if self.speaking:
                return

            self.speaking = True

        self.thread = threading.Thread(
            target=self._worker,
            args=(text,),
            daemon=True,
        )
        self.thread.start()

    def _worker(self, text: str) -> None:
        try:
            self.voice.speak(text)
        except Exception as error:
            print(f"[TTS Error] {error}")
        finally:
            with self._lock:
                self.speaking = False

    def stop(self) -> None:
        self.voice.stop()

    def is_speaking(self) -> bool:
        with self._lock:
            return self.speaking