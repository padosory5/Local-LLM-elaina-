from __future__ import annotations

import ctypes
import io
import platform
import time
from dataclasses import dataclass

import mss
from PIL import Image

from config.loader import Config


@dataclass(frozen=True)
class ScreenSnapshot:
    """One compressed screen frame and its desktop metadata."""

    image_bytes: bytes
    mime_type: str
    active_window_title: str
    capture_target: str
    captured_at: float


class ScreenMonitor:
    """
    Capture a fresh screen frame only when ChatEngine requests one.

    No background recording is performed. The screenshot exists only as JPEG
    bytes in RAM for the duration of the current conversation turn.
    """

    def __init__(self, config: Config) -> None:
        self.enabled = bool(
            config.get("vision", "enabled", default=False, required=False)
        )
        self.monitor_index = int(
            config.get("vision", "monitor_index", default=1, required=False)
        )
        self.maximum_width = int(
            config.get("vision", "maximum_width", default=1280, required=False)
        )
        self.jpeg_quality = int(
            config.get("vision", "jpeg_quality", default=70, required=False)
        )

    def start(self) -> None:
        if self.enabled:
            print("[Vision] On-demand screen vision is ready.")

    def stop(self) -> None:
        """Provided for a consistent ChatEngine shutdown interface."""
        return

    def capture_now(self, target: str = "configured") -> ScreenSnapshot | None:
        """Capture the requested monitor, or the configured monitor by default."""
        if not self.enabled:
            return None

        try:
            with mss.mss() as screen_capture:
                monitors = screen_capture.monitors
                monitor, target_label = self._select_monitor(monitors, target)
                raw_frame = screen_capture.grab(monitor)

                return ScreenSnapshot(
                    image_bytes=self._compress_frame(raw_frame),
                    mime_type="image/jpeg",
                    active_window_title=self._get_active_window_title(),
                    capture_target=target_label,
                    captured_at=time.time(),
                )
        except Exception as error:
            print(f"[Vision Capture Warning] {error}")
            return None

    def capture_region(self, region: dict) -> ScreenSnapshot | None:
        """Capture one user-selected desktop rectangle."""
        if not self.enabled:
            return None

        try:
            requested = {
                "left": int(region["left"]),
                "top": int(region["top"]),
                "width": int(region["width"]),
                "height": int(region["height"]),
            }

            if requested["width"] < 20 or requested["height"] < 20:
                raise ValueError("The selected region is too small.")

            with mss.mss() as screen_capture:
                desktop = screen_capture.monitors[0]

                left = max(requested["left"], desktop["left"])
                top = max(requested["top"], desktop["top"])
                right = min(
                    requested["left"] + requested["width"],
                    desktop["left"] + desktop["width"],
                )
                bottom = min(
                    requested["top"] + requested["height"],
                    desktop["top"] + desktop["height"],
                )

                if right - left < 20 or bottom - top < 20:
                    raise ValueError(
                        "The selected region is outside the visible desktop."
                    )

                raw_frame = screen_capture.grab({
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                })

                return ScreenSnapshot(
                    image_bytes=self._compress_frame(raw_frame),
                    mime_type="image/jpeg",
                    active_window_title=self._get_active_window_title(),
                    capture_target="selected screen region",
                    captured_at=time.time(),
                )
        except Exception as error:
            print(f"[Vision Region Warning] {error}")
            return None

    def _select_monitor(self, monitors, target: str):
        """Resolve natural positions without relying on Windows monitor numbers."""
        if not monitors:
            raise RuntimeError("No displays were detected.")

        # MSS index 0 is the entire virtual desktop; physical monitors start at 1.
        physical_monitors = list(monitors[1:])

        if target == "all":
            return monitors[0], "all screens"

        if physical_monitors and target == "left":
            return (
                min(physical_monitors, key=lambda item: item["left"]),
                "left screen",
            )

        if physical_monitors and target == "right":
            return max(
                physical_monitors,
                key=lambda item: item["left"] + item["width"],
            ), "right screen"

        if self.monitor_index < 0 or self.monitor_index >= len(monitors):
            print(
                f"[Vision Warning] Monitor {self.monitor_index} does not "
                "exist. Falling back to monitor 1."
            )
            selected_index = 1 if len(monitors) > 1 else 0
        else:
            selected_index = self.monitor_index

        label = "main screen" if target == "main" else f"monitor {selected_index}"
        return monitors[selected_index], label

    def _compress_frame(self, raw_frame: mss.base.ScreenShot) -> bytes:
        image = Image.frombytes(
            "RGB", raw_frame.size, raw_frame.bgra, "raw", "BGRX"
        )

        if image.width > self.maximum_width:
            new_height = round(image.height * self.maximum_width / image.width)
            image = image.resize(
                (self.maximum_width, new_height), Image.Resampling.LANCZOS
            )

        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=max(40, min(95, self.jpeg_quality)),
            optimize=True,
        )
        return output.getvalue()

    @staticmethod
    def _get_active_window_title() -> str:
        """Read the foreground-window title on Windows without extra packages."""
        if platform.system() != "Windows":
            return ""

        try:
            user32 = ctypes.windll.user32
            window_handle = user32.GetForegroundWindow()
            title_length = user32.GetWindowTextLengthW(window_handle)

            if title_length <= 0:
                return ""

            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(
                window_handle, title_buffer, title_length + 1
            )
            return title_buffer.value.strip()
        except Exception:
            return ""
