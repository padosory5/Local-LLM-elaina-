from __future__ import annotations

from pathlib import Path

import pygame


class AudioPlayer:
    def __init__(self) -> None:
        if not pygame.mixer.get_init():
            pygame.mixer.init()

        self.cache: dict[str, pygame.mixer.Sound] = {}

    def play(self, path: str | Path) -> pygame.mixer.Channel | None:
        sound_path = Path(path).resolve()
        cache_key = str(sound_path)

        if not sound_path.exists():
            print(f"[Audio Player Error] Sound not found: {sound_path}")
            return None

        try:
            if cache_key not in self.cache:
                self.cache[cache_key] = pygame.mixer.Sound(cache_key)

            return self.cache[cache_key].play()

        except pygame.error as error:
            print(f"[Audio Player Error] {error}")
            return None