from __future__ import annotations

import json
import os
import hashlib
import time
from datetime import datetime
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VisualSearchResult:
    best_guess_labels: list[str]
    web_entities: list[dict[str, Any]]
    matching_pages: list[dict[str, Any]]
    full_matches: list[str]
    partial_matches: list[str]
    similar_images: list[str]

    @property
    def has_useful_evidence(self) -> bool:
        return bool(
            self.best_guess_labels
            or self.web_entities
            or self.matching_pages
            or self.full_matches
            or self.partial_matches
        )

    def to_prompt_text(self) -> str:
        return json.dumps(
            asdict(self),
            ensure_ascii=False,
            indent=2,
        )


class VisualSearchTool:
    """Search Google's web-image index using the selected image bytes."""

    def __init__(self, config=None) -> None:
        self._client = None
        self._vision_module = None
        self.enabled = True
        self.provider = "google_cloud_vision"
        self.credentials_env = "GOOGLE_APPLICATION_CREDENTIALS"
        self.maximum_items = 8
        self.monthly_request_limit = 900
        self.cache_seconds = 86400
        self._cache: dict[str, tuple[float, VisualSearchResult]] = {}
        self.usage_file = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "visual_search_usage.json"
        )

        if config is not None:
            self.enabled = bool(config.get(
                "visual_search",
                "enabled",
                default=True,
                required=False,
            ))
            self.provider = str(config.get(
                "visual_search",
                "provider",
                default="google_cloud_vision",
                required=False,
            ))
            self.credentials_env = str(config.get(
                "visual_search",
                "google_cloud_vision",
                "credentials_env",
                default="GOOGLE_APPLICATION_CREDENTIALS",
                required=False,
            ))
            self.maximum_items = int(config.get(
                "visual_search",
                "google_cloud_vision",
                "max_results",
                default=8,
                required=False,
            ))
            self.monthly_request_limit = int(config.get(
                "visual_search",
                "google_cloud_vision",
                "monthly_request_limit",
                default=900,
                required=False,
            ))
            self.cache_seconds = int(config.get(
                "visual_search",
                "google_cloud_vision",
                "cache_seconds",
                default=86400,
                required=False,
            ))
            configured_usage_file = str(config.get(
                "visual_search",
                "google_cloud_vision",
                "usage_file",
                default="data/visual_search_usage.json",
                required=False,
            ))
            configured_path = Path(configured_usage_file)
            if configured_path.is_absolute():
                self.usage_file = configured_path
            else:
                self.usage_file = (
                    Path(__file__).resolve().parents[1]
                    / configured_path
                )

    def search_image(
        self,
        image_bytes: bytes,
        *,
        maximum_items: int | None = None,
    ) -> VisualSearchResult:
        if not self.enabled:
            raise RuntimeError("Visual search is disabled in config.yaml.")
        if self.provider != "google_cloud_vision":
            raise RuntimeError(
                f"Unsupported visual-search provider: {self.provider}"
            )
        if not image_bytes:
            raise ValueError("The selected image is empty.")

        image_key = hashlib.sha256(image_bytes).hexdigest()
        cached = self._cache.get(image_key)
        if cached is not None:
            cached_at, cached_result = cached
            if time.monotonic() - cached_at < self.cache_seconds:
                print("[Visual Search] Using cached image match.")
                return cached_result
            self._cache.pop(image_key, None)

        client, vision = self._get_client()
        self._record_request()
        image = vision.Image(content=image_bytes)
        response = client.web_detection(image=image)

        if response.error.message:
            raise RuntimeError(response.error.message)

        annotation = response.web_detection
        requested_limit = (
            self.maximum_items
            if maximum_items is None
            else maximum_items
        )
        limit = max(1, min(int(requested_limit), 20))

        result = VisualSearchResult(
            best_guess_labels=[
                str(item.label)
                for item in list(annotation.best_guess_labels)[:limit]
                if getattr(item, "label", "")
            ],
            web_entities=[
                {
                    "description": str(item.description),
                    "score": round(float(item.score), 4),
                }
                for item in list(annotation.web_entities)[:limit]
                if getattr(item, "description", "")
            ],
            matching_pages=[
                {
                    "title": str(item.page_title),
                    "url": str(item.url),
                    "score": round(float(item.score), 4),
                }
                for item in list(annotation.pages_with_matching_images)[:limit]
                if getattr(item, "url", "")
            ],
            full_matches=[
                str(item.url)
                for item in list(annotation.full_matching_images)[:limit]
                if getattr(item, "url", "")
            ],
            partial_matches=[
                str(item.url)
                for item in list(annotation.partial_matching_images)[:limit]
                if getattr(item, "url", "")
            ],
            similar_images=[
                str(item.url)
                for item in list(annotation.visually_similar_images)[:limit]
                if getattr(item, "url", "")
            ],
        )
        self._cache[image_key] = (time.monotonic(), result)
        if len(self._cache) > 10:
            oldest_key = min(
                self._cache,
                key=lambda key: self._cache[key][0],
            )
            self._cache.pop(oldest_key, None)
        return result

    def _get_client(self):
        if self._client is not None and self._vision_module is not None:
            return self._client, self._vision_module

        try:
            from google.cloud import vision
            from google.oauth2 import service_account
        except ImportError as error:
            raise RuntimeError(
                "Google visual search is not installed. Run: "
                "pip install google-cloud-vision"
            ) from error

        credentials_path = os.getenv(self.credentials_env, "").strip()
        if not credentials_path:
            raise RuntimeError(
                f"The environment variable {self.credentials_env} is not set."
            )

        credential_file = Path(credentials_path).expanduser()
        if not credential_file.is_file():
            raise RuntimeError(
                f"Google credential file does not exist: {credential_file}"
            )

        try:
            credentials = (
                service_account.Credentials.from_service_account_file(
                    str(credential_file)
                )
            )
            self._client = vision.ImageAnnotatorClient(
                credentials=credentials
            )
        except Exception as error:
            raise RuntimeError(
                "Google Cloud Vision credentials are unavailable. Set "
                "GOOGLE_APPLICATION_CREDENTIALS to your service-account "
                "JSON file."
            ) from error

        self._vision_module = vision
        return self._client, self._vision_module

    def _record_request(self) -> None:
        """Enforce Elaina's local monthly request ceiling before API usage."""
        month = datetime.now().strftime("%Y-%m")
        usage = {
            "month": month,
            "requests": 0,
        }

        try:
            if self.usage_file.is_file():
                loaded = json.loads(
                    self.usage_file.read_text(encoding="utf-8")
                )
                if isinstance(loaded, dict) and loaded.get("month") == month:
                    usage["requests"] = int(loaded.get("requests", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A damaged counter must not silently disable the safety limit.
            raise RuntimeError(
                f"Could not read visual-search usage counter: {self.usage_file}"
            )

        if usage["requests"] >= self.monthly_request_limit:
            raise RuntimeError(
                "Elaina's monthly Google Vision request limit has been "
                f"reached ({self.monthly_request_limit})."
            )

        usage["requests"] += 1
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        self.usage_file.write_text(
            json.dumps(usage, indent=2),
            encoding="utf-8",
        )
