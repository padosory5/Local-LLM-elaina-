from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.paths import PROJECT_ROOT, RUNTIME_ROOT

if TYPE_CHECKING:
    from config.loader import Config


class GoogleCalendarTool:
    """Create Google Calendar events using local OAuth desktop credentials."""

    SCOPES = (
        "https://www.googleapis.com/auth/calendar.events",
    )

    def __init__(self, config: "Config") -> None:
        self.config = config
        self.token_path = (
            RUNTIME_ROOT / "secrets" / "google_calendar_token.json"
        )
        self.token_path.parent.mkdir(parents=True, exist_ok=True)

    def credential_path(self) -> Path | None:
        configured = os.getenv(
            "GOOGLE_CALENDAR_CREDENTIALS",
            "",
        ).strip()
        if not configured:
            configured = str(self.config.get(
                "calendar",
                "google",
                "credentials_file",
                default="",
                required=False,
            )).strip()
        if not configured:
            return None

        path = Path(os.path.expandvars(configured)).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    def readiness(self) -> tuple[bool, str]:
        path = self.credential_path()
        if path is None:
            return (
                False,
                "GOOGLE_CALENDAR_CREDENTIALS is not configured in .env.",
            )
        if not path.is_file():
            return False, f"Google OAuth credentials were not found at {path}."
        return True, "Google Calendar credentials are configured."

    def create_event(
        self,
        *,
        calendar_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        ready, reason = self.readiness()
        if not ready:
            raise RuntimeError(reason)

        self._validate_event(event)
        service = self._build_service()
        created = (
            service.events()
            .insert(
                calendarId=str(calendar_id or "primary"),
                body=event,
                sendUpdates="none",
            )
            .execute()
        )
        return {
            "status": "created",
            "event_id": str(created.get("id", "")),
            "summary": str(created.get("summary", event["summary"])),
            "start": dict(created.get("start", event["start"])),
            "end": dict(created.get("end", event["end"])),
            "calendar_id": str(calendar_id or "primary"),
        }

    def _build_service(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as error:
            raise RuntimeError(
                "Google Calendar packages are not installed. Run "
                "'pip install -r requirements.txt'."
            ) from error

        credentials = None
        if self.token_path.is_file():
            try:
                credentials = Credentials.from_authorized_user_file(
                    str(self.token_path),
                    list(self.SCOPES),
                )
            except (ValueError, json.JSONDecodeError, OSError):
                credentials = None

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

        if not credentials or not credentials.valid:
            credential_path = self.credential_path()
            assert credential_path is not None
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credential_path),
                list(self.SCOPES),
            )
            credentials = flow.run_local_server(
                port=0,
                open_browser=True,
                authorization_prompt_message=(
                    "Open this URL to authorize Elaina's Calendar Agent: {url}"
                ),
                success_message=(
                    "Google Calendar authorization completed. You can close "
                    "this browser tab and return to Elaina."
                ),
            )
            self.token_path.write_text(
                credentials.to_json(),
                encoding="utf-8",
            )

        return build(
            "calendar",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )

    @staticmethod
    def _validate_event(event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            raise ValueError("Calendar event must be an object.")
        if not str(event.get("summary", "")).strip():
            raise ValueError("Calendar event title is missing.")
        for boundary in ("start", "end"):
            value = event.get(boundary)
            if not isinstance(value, dict) or not value.get("dateTime"):
                raise ValueError(
                    f"Calendar event {boundary} date-time is missing."
                )
