from __future__ import annotations

import queue
from collections import deque

import numpy as np


def resample_capture_chunk(
    audio: np.ndarray,
    target_samples: int,
) -> np.ndarray:
    """Convert one normalized capture block into 16-bit model-rate audio."""
    source = np.asarray(audio, dtype=np.float32).reshape(-1)
    if source.size == 0:
        return np.empty(0, dtype=np.int16)

    if source.size != target_samples:
        source_positions = np.arange(source.size, dtype=np.float64)
        target_positions = np.linspace(
            0,
            source.size - 1,
            num=target_samples,
            dtype=np.float64,
        )
        source = np.interp(
            target_positions,
            source_positions,
            source,
        ).astype(np.float32)

    return np.clip(
        np.rint(source * 32767.0),
        -32768,
        32767,
    ).astype(np.int16)


class RollingAudioBuffer:
    """Thread-safe microphone buffer that keeps the newest audio blocks."""

    def __init__(self, max_chunks: int = 256) -> None:
        self._queue: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=max(8, int(max_chunks))
        )

    def push(self, chunk: np.ndarray) -> None:
        """Never block PortAudio; discard the oldest block when necessary."""
        try:
            self._queue.put_nowait(chunk)
            return
        except queue.Full:
            pass

        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            pass

    def get(self, timeout: float) -> np.ndarray:
        return self._queue.get(timeout=timeout)

    def drain_recent(self, maximum: int) -> list[np.ndarray]:
        recent: deque[np.ndarray] = deque(maxlen=max(0, int(maximum)))
        while True:
            try:
                recent.append(self._queue.get_nowait())
            except queue.Empty:
                return list(recent)

    def clear(self) -> None:
        self.drain_recent(0)
