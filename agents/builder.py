from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from core.paths import PROJECT_ROOT


@dataclass
class AgentBuildSession:
    blueprint: str
    original_request: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentBuildResult:
    status: str
    message: str
    definition: dict[str, Any] | None = None


class AgentBuilder:
    """
    Gather requirements for a known, reviewed capability blueprint.

    The first implementation intentionally creates declarative agent
    definitions only. It does not execute model-generated Python.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        keep_alive: int | str,
        blueprint_directory: Path | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        self.blueprint_directory = (
            blueprint_directory
            or PROJECT_ROOT / "agents" / "blueprints"
        )
        self.session: AgentBuildSession | None = None

    @property
    def active(self) -> bool:
        return self.session is not None

    def handle(self, user_input: str) -> AgentBuildResult:
        if self.session is None:
            return self._begin(user_input)
        return self._continue(user_input)

    def _begin(self, user_input: str) -> AgentBuildResult:
        extracted = self._extract_settings(user_input)
        capability = extracted.pop("_capability", "unsupported")
        extracted.pop("_cancel_requested", None)
        if capability != "google_calendar":
            return AgentBuildResult(
                status="unsupported",
                message=(
                    "I can create a new agent only when Elaina already has a "
                    "reviewed tool for the requested action. Right now the "
                    "first installable blueprint is Google Calendar. For a "
                    "different capability, I need an implemented and tested "
                    "tool before I can safely create its agent."
                ),
            )

        self.session = AgentBuildSession(
            blueprint="google_calendar",
            original_request=user_input,
        )
        self.session.values.update(extracted)
        return self._next_result()

    def _continue(self, user_input: str) -> AgentBuildResult:
        extracted = self._extract_settings(user_input)
        if extracted.pop("_cancel_requested", False):
            self.session = None
            return AgentBuildResult(
                status="cancelled",
                message="Okay, I cancelled the agent setup.",
            )

        assert self.session is not None
        extracted.pop("_capability", None)
        self.session.values.update(extracted)
        return self._next_result()

    def _next_result(self) -> AgentBuildResult:
        assert self.session is not None
        values = self.session.values

        timezone_name = str(values.get("timezone", "")).strip()
        if timezone_name:
            try:
                ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                values.pop("timezone", None)
                return AgentBuildResult(
                    status="input_required",
                    message=(
                        f"I don't recognize the time zone {timezone_name}. "
                        "Please give me an IANA time zone such as Asia/Seoul "
                        "or America/Los_Angeles."
                    ),
                )

        missing = []
        if not str(values.get("timezone", "")).strip():
            missing.append("your time zone, such as Asia/Seoul")
        if not str(values.get("calendar_id", "")).strip():
            missing.append(
                "the calendar to use, usually primary"
            )
        else:
            calendar_id = str(values["calendar_id"]).strip()
            if calendar_id.casefold() != "primary" and "@" not in calendar_id:
                values.pop("calendar_id", None)
                return AgentBuildResult(
                    status="input_required",
                    message=(
                        "For this first version, use the primary calendar or "
                        "provide the calendar's exact ID, which is often an "
                        "email address. A display name alone is not reliable."
                    ),
                )
        if not values.get("default_duration_minutes"):
            missing.append(
                "a default event duration, such as 60 minutes"
            )
        if values.get("approval_confirmed") is not True:
            missing.append(
                "confirmation that every calendar write must require approval"
            )

        if missing:
            return AgentBuildResult(
                status="input_required",
                message=(
                    "Before I create the Google Calendar Agent, I need "
                    + "; ".join(missing)
                    + ". You can answer in one sentence, for example: "
                    "Asia/Seoul, primary calendar, 60 minutes, and yes, ask "
                    "before every change. The agent will also need a Google "
                    "OAuth Desktop credential file configured in your .env "
                    "before it can write its first event."
                ),
            )

        blueprint_path = (
            self.blueprint_directory / "google_calendar.yaml"
        )
        definition = yaml.safe_load(
            blueprint_path.read_text(encoding="utf-8")
        )
        definition["settings"].update({
            "timezone": str(values["timezone"]),
            "calendar_id": str(values["calendar_id"]),
            "default_duration_minutes": int(
                values["default_duration_minutes"]
            ),
            "approval_required": True,
        })
        self.session = None

        return AgentBuildResult(
            status="ready",
            message=(
                "The Google Calendar Agent definition is ready. Review its "
                "permissions in Electron; no agent has been installed yet."
            ),
            definition=definition,
        )

    def _extract_settings(self, user_input: str) -> dict[str, Any]:
        prompt = (
            "Extract Google Calendar Agent setup preferences from the user's "
            "message. Return JSON only with capability, cancel_requested, "
            "timezone, calendar_id, default_duration_minutes, and "
            "approval_confirmed. capability is google_calendar only when the "
            "requested agent manages calendar events; otherwise unsupported. "
            "cancel_requested is true when the user semantically cancels an "
            "active setup, regardless of their exact wording. Use an empty "
            "string or null when a value is not provided. timezone must be an "
            "IANA name such as Asia/Seoul. calendar_id should be 'primary' "
            "when the user says main or primary calendar. Do not convert a "
            "calendar display name into an invented ID. "
            "approval_confirmed is true only when the user agrees that every "
            "calendar write requires confirmation. Do not infer agreement "
            "from an unrelated yes.\n\n"
            f"Message: {user_input}"
        )

        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                stream=False,
                format="json",
                options={"temperature": 0, "num_predict": 120},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            payload = json.loads(
                str(self._value(message, "content", "{}"))
            )
            if not isinstance(payload, dict):
                return {}
        except Exception as error:
            print(
                f"[Agent Builder] Requirement extraction failed: "
                f"{type(error).__name__}: {error}"
            )
            return {}

        result: dict[str, Any] = {}
        capability = str(payload.get("capability") or "").strip().lower()
        result["_capability"] = (
            "google_calendar"
            if capability == "google_calendar"
            else "unsupported"
        )
        result["_cancel_requested"] = (
            payload.get("cancel_requested") is True
        )
        timezone_name = str(payload.get("timezone") or "").strip()
        calendar_id = str(payload.get("calendar_id") or "").strip()

        if timezone_name:
            result["timezone"] = timezone_name
        if calendar_id:
            result["calendar_id"] = calendar_id

        try:
            duration = int(payload.get("default_duration_minutes") or 0)
        except (TypeError, ValueError):
            duration = 0
        if 5 <= duration <= 1440:
            result["default_duration_minutes"] = duration

        if payload.get("approval_confirmed") is True:
            result["approval_confirmed"] = True

        return result

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
