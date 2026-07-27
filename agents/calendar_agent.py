from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents.base import AgentDefinition


@dataclass
class CalendarDraft:
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CalendarTurnResult:
    status: str
    message: str
    event: dict[str, Any] | None = None
    calendar_id: str = "primary"


class GoogleCalendarAgent:
    """Collect and validate one calendar event before requesting approval."""

    def __init__(
        self,
        client: Any,
        model: str,
        keep_alive: int | str,
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        self.draft: CalendarDraft | None = None

    @property
    def active(self) -> bool:
        return self.draft is not None

    def cancel(self) -> None:
        self.draft = None

    def handle(
        self,
        user_input: str,
        definition: AgentDefinition,
    ) -> CalendarTurnResult:
        normalized = " ".join(user_input.lower().split())
        if self.draft is not None and normalized in {
            "cancel",
            "stop",
            "never mind",
            "nevermind",
            "don't add it",
            "do not add it",
        }:
            self.draft = None
            return CalendarTurnResult(
                status="cancelled",
                message="Okay, I cancelled that calendar event.",
            )

        if self.draft is None:
            self.draft = CalendarDraft()

        settings = definition.settings
        timezone_name = str(
            settings.get("timezone", "Asia/Seoul")
        )
        calendar_id = str(
            settings.get("calendar_id", "primary")
        )
        default_duration = int(
            settings.get("default_duration_minutes", 60)
        )

        extracted = self._extract_event(
            user_input=user_input,
            current_values=self.draft.values,
            timezone_name=timezone_name,
        )
        self.draft.values.update({
            key: value
            for key, value in extracted.items()
            if value is not None and value != ""
        })
        values = self.draft.values

        missing = []
        if not str(values.get("summary", "")).strip():
            missing.append("the event title")
        if not str(values.get("start", "")).strip():
            missing.append("the date and start time")

        if missing:
            return CalendarTurnResult(
                status="input_required",
                message=(
                    "I still need "
                    + " and ".join(missing)
                    + ". You can say something like, Math review tomorrow at "
                    "3 PM for 90 minutes."
                ),
                calendar_id=calendar_id,
            )

        try:
            ZoneInfo(timezone_name)
            start = self._parse_datetime(values["start"], timezone_name)
            raw_end = str(values.get("end", "")).strip()
            end = (
                self._parse_datetime(raw_end, timezone_name)
                if raw_end
                else start + timedelta(minutes=default_duration)
            )
        except (ValueError, ZoneInfoNotFoundError) as error:
            self.draft.values.pop("start", None)
            self.draft.values.pop("end", None)
            return CalendarTurnResult(
                status="input_required",
                message=(
                    "I couldn't interpret that date or time safely. Please "
                    f"repeat it with a date and start time. Details: {error}"
                ),
                calendar_id=calendar_id,
            )

        if end <= start:
            self.draft.values.pop("end", None)
            return CalendarTurnResult(
                status="input_required",
                message=(
                    "The ending time must be after the starting time. What "
                    "time should the event end?"
                ),
                calendar_id=calendar_id,
            )

        event = {
            "summary": str(values["summary"]).strip(),
            "description": str(values.get("description", "")).strip(),
            "location": str(values.get("location", "")).strip(),
            "start": {
                "dateTime": start.isoformat(),
                "timeZone": timezone_name,
            },
            "end": {
                "dateTime": end.isoformat(),
                "timeZone": timezone_name,
            },
        }
        self.draft = None
        return CalendarTurnResult(
            status="ready",
            message=(
                "I prepared the calendar event. Review the exact title, time, "
                "calendar, and location in Electron before I create it."
            ),
            event=event,
            calendar_id=calendar_id,
        )

    def _extract_event(
        self,
        *,
        user_input: str,
        current_values: dict[str, Any],
        timezone_name: str,
    ) -> dict[str, Any]:
        now = datetime.now(ZoneInfo(timezone_name))
        prompt = (
            "Extract details for one Google Calendar event. Return JSON only "
            "with summary, start, end, description, and location. Resolve "
            "relative dates using the supplied current local date and time. "
            "start and end must be ISO 8601 date-time strings including an "
            "offset. Preserve existing draft values unless the user corrects "
            "them. If an end time or duration was not stated, leave end empty. "
            "Do not invent a title, date, or time.\n\n"
            f"Time zone: {timezone_name}\n"
            f"Current local time: {now.isoformat()}\n"
            f"Existing draft: {json.dumps(current_values)}\n"
            f"Latest message: {user_input}"
        )

        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 180},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            payload = json.loads(
                str(self._value(message, "content", "{}"))
            )
            return payload if isinstance(payload, dict) else {}
        except Exception as error:
            print(
                f"[Calendar Agent] Event extraction failed: "
                f"{type(error).__name__}: {error}"
            )
            return {}

    @staticmethod
    def _parse_datetime(value: Any, timezone_name: str) -> datetime:
        text = str(value).strip()
        if not text:
            raise ValueError("Date and time are missing.")

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        timezone = ZoneInfo(timezone_name)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
        else:
            parsed = parsed.astimezone(timezone)
        return parsed

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
