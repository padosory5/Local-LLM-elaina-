"""Short, varied, outcome-locked voice responses for actions and agents."""

from __future__ import annotations

import re
from collections import deque
from difflib import SequenceMatcher
from typing import Any

from brain.text_filter import TextFilter


class BriefResponseGenerator:
    """Generate natural variety without allowing a line to change action state."""

    MAX_WORDS = 7
    _FALLBACKS = {
        "opened": (
            "Got it—{subject} is open.",
            "Sure, {subject} is ready.",
            "Done—{subject} is open.",
            "All set with {subject}.",
            "Opened {subject}.",
        ),
        "control_mode_off": (
            "Enable Computer Control to {action} {subject}.",
            "Turn on Computer Control for that.",
            "{subject} needs Computer Control enabled.",
        ),
        "force_quit_offer": (
            "Force-quit {subject}? Unsaved work?",
            "Quit {subject} completely? Unsaved work?",
            "Force-close {subject}? Unsaved work?",
        ),
        "delete_offer": (
            "Move {subject} to Recycle Bin?",
            "Recycle {subject} now?",
            "Delete {subject} to Recycle Bin?",
            "Trash {subject} now?",
        ),
        "ui_action_offer": (
            "Click {subject}?",
            "Go ahead and click {subject}?",
            "Confirm clicking {subject}?",
        ),
        "closed": (
            "Got it—{subject} is closed.",
            "Done, {subject} is closed.",
            "All set—closed {subject}.",
            "Sure, {subject} is closed.",
        ),
        "close_requested": (
            "Closing {subject} now.",
            "I asked {subject} to close.",
            "Close request sent to {subject}.",
        ),
        "force_quit": (
            "Got it—{subject} is fully stopped.",
            "All set—{subject} is gone.",
            "Done—{subject} fully quit.",
            "Completely closed {subject}.",
        ),
        "url_opened": (
            "Got it—{subject} is open.",
            "Sure, new tab opened.",
            "Opened {subject} in a new tab.",
            "All set—{subject} is open.",
        ),
        "file_created": (
            "Got it—created {subject}.",
            "Sure, {subject} is ready.",
            "Done—your file is ready.",
            "Created {subject}.",
        ),
        "folder_created": (
            "Got it—created {subject}.",
            "Sure, the folder is ready.",
            "Done—{subject} is ready.",
            "Created the {subject} folder.",
        ),
        "file_deleted": (
            "Got it—{subject} is recycled.",
            "Moved {subject} to Recycle Bin.",
            "Done—your file is in Recycle Bin.",
        ),
        "folder_deleted": (
            "Got it—{subject} is recycled.",
            "Moved {subject} to Recycle Bin.",
            "Done—the folder is in Recycle Bin.",
        ),
        "not_running": (
            "{subject} isn't running.",
            "I can't find {subject} running.",
            "{subject} is already closed.",
        ),
        "already_exists": (
            "{subject} already exists.",
            "There's already a {subject} there.",
            "That name is already taken.",
        ),
        "item_not_found": (
            "I couldn't find {subject} there.",
            "{subject} isn't in that folder.",
            "That item is missing.",
        ),
        "wrong_type": (
            "That item is the wrong type.",
            "That isn't the requested item type.",
            "File and folder types don't match.",
        ),
        "invalid_target": (
            "That target isn't valid.",
            "I need a valid target.",
            "That destination isn't usable.",
        ),
        "outside_allowed": (
            "That location isn't allowed.",
            "I can't use that location.",
            "Choose Desktop, Documents, or Downloads.",
        ),
        "needs_location": (
            "Which allowed folder should I use?",
            "Desktop, Documents, or Downloads?",
            "Where should I put it?",
        ),
        "not_found": (
            "I couldn't find {subject}.",
            "{subject} isn't registered here.",
            "That app is missing.",
        ),
        "failed": (
            "I couldn't complete that for {subject}.",
            "That action failed for {subject}.",
            "I can't complete that right now.",
        ),
        "ambiguous": (
            "Which app did you mean?",
            "I found multiple matches. Which one?",
            "Which matching app should I use?",
        ),
        "blocked": (
            "That action isn't supported yet.",
            "That PC action isn't supported yet.",
            "I can't safely do that yet.",
        ),
        "declined": (
            "Okay, I won't do that.",
            "Got it, I won't proceed.",
            "No problem, I won't continue.",
        ),
        "work_started": (
            "On it.",
            "Checking now.",
            "Right away.",
            "Got it.",
            "On that.",
            "Sure, one moment.",
        ),
    }

    def __init__(
        self,
        client: Any,
        model: str,
        *,
        keep_alive: int | str = -1,
        recent_limit: int = 16,
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        self._recent: deque[str] = deque(maxlen=max(6, int(recent_limit)))
        self._recent_shapes: deque[str] = deque(maxlen=max(6, int(recent_limit)))
        self._recent_openings: deque[str] = deque(maxlen=4)

    def generate(
        self,
        kind: str,
        *,
        subject: str = "",
        detail: str = "",
        operation: str = "",
    ) -> str:
        kind = kind if kind in self._FALLBACKS else "blocked"
        prompt = self._prompt(kind, subject, detail, operation)
        candidate = ""
        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "system", "content": prompt}],
                stream=False,
                options={"temperature": 1.0, "num_predict": 40},
                keep_alive=self.keep_alive,
                think=False,
            )
            message = self._value(response, "message", {})
            candidate = TextFilter.for_voice_response(
                self._value(message, "content", "")
            )
            candidate = self._safe_punctuation(candidate)
        except Exception as error:
            print(
                "[Brief Response] Generation failed safely: "
                f"{type(error).__name__}: {error}"
            )

        if not self._valid(candidate, kind, subject, operation):
            candidate = self._safe_punctuation(
                self._fallback(kind, subject, operation)
            )
        self._recent.append(candidate)
        self._recent_shapes.append(self._response_shape(candidate, subject))
        opening = self._opening(candidate)
        if opening:
            self._recent_openings.append(opening)
        return candidate

    def _prompt(
        self,
        kind: str,
        subject: str,
        detail: str,
        operation: str,
    ) -> str:
        rules = {
            "opened": "The app really opened. Give a varied casual acknowledgement.",
            "control_mode_off": (
                "Desktop Control Mode is off. Recommend enabling the visible "
                "Computer Control toggle for this supported action."
            ),
            "force_quit_offer": "Ask to force quit and mention unsaved work.",
            "delete_offer": "Ask to move this exact item to the Recycle Bin.",
            "ui_action_offer": "Ask to click this exact, real on-screen control.",
            "closed": "The app really closed. Give a varied acknowledgement.",
            "close_requested": "A close was requested; do not claim full exit.",
            "force_quit": "The app was fully stopped. Acknowledge that result.",
            "url_opened": "The website opened in a new tab. Acknowledge it.",
            "file_created": "The new empty file was created. Acknowledge it.",
            "folder_created": "The new folder was created. Acknowledge it.",
            "file_deleted": "The file moved to Recycle Bin. Acknowledge it.",
            "folder_deleted": "The folder moved to Recycle Bin. Acknowledge it.",
            "not_running": "State that the app is not running.",
            "already_exists": "State that the item already exists.",
            "item_not_found": "State that the item was not found there.",
            "wrong_type": "State that the file/folder type does not match.",
            "invalid_target": "State that the target is invalid.",
            "outside_allowed": "State that the location is not allowed.",
            "needs_location": "Ask which allowed location should be used.",
            "not_found": "State that the application could not be found.",
            "failed": "State that the requested action failed.",
            "ambiguous": "Ask which matching application the user means.",
            "blocked": "State that this computer action is unsupported.",
            "declined": "Acknowledge that the action will not happen.",
            "work_started": "Work is beginning. Do not claim completion.",
        }[kind]
        variation = (
            "minimal acknowledgement",
            "friendly conversational phrasing",
            "target-first phrasing",
            "verb-first phrasing",
            "lightly playful but clear phrasing",
        )[len(self._recent) % 5]
        return (
            "Write one natural voice-bot line only, without quotes or labels. "
            f"Use no more than {self.MAX_WORDS} words. Vary openings among "
            "phrases like okay, sure, got it, done, all set, and original "
            "alternatives. Never add an offer to help. Never repeat or closely "
            "paraphrase a recent line. The trusted status is absolute: never "
            "claim success for failure or failure for success. Confirmation "
            "lines must be questions and clearly name the action and target.\n\n"
            f"Status: {kind}\nOperation: {operation or '(none)'}\n"
            f"Subject: {subject or '(none)'}\nDetail: {detail or '(none)'}\n"
            f"Instruction: {rules}\nVariation style: {variation}\n"
            f"Recent lines: {' | '.join(self._recent) or '(none)'}"
        )

    def _valid(
        self,
        text: str,
        kind: str,
        subject: str,
        operation: str,
    ) -> bool:
        if not text or "\n" in text or len(text.split()) > self.MAX_WORDS:
            return False
        lowered = text.casefold()
        if any(
            phrase in lowered
            for phrase in ("anything else", "let me know", "need help")
        ):
            return False
        if self._is_repeated(text, subject):
            return False

        negative = any(
            phrase in lowered
            for phrase in (
                "can't", "cannot", "couldn't", "didn't", "failed", "isn't",
                "not found", "won't", "missing", "wrong", "not allowed",
                "invalid", "restricted", "unavailable", "not ",
            )
        )
        if kind in {
            "opened", "closed", "force_quit", "url_opened", "file_created",
            "folder_created", "file_deleted", "folder_deleted",
        } and negative:
            return False
        if kind in {
            "opened", "closed", "force_quit", "url_opened", "file_created",
            "folder_created", "file_deleted", "folder_deleted",
        } and (
            "?" in text
            or "confirm" in lowered
            or "take over" in lowered
            or "takeover" in lowered
        ):
            return False
        if kind in {
            "not_found", "item_not_found", "failed", "declined", "not_running",
            "invalid_target", "outside_allowed", "wrong_type",
        } and not negative:
            return False

        if kind == "control_mode_off":
            if "computer control" not in lowered:
                return False
            if not any(word in lowered for word in ("enable", "turn on", "switch on")):
                return False
            if "?" in text:
                return False
            if any(
                word in lowered
                for word in ("opened", "closed", "created", "deleted", "done")
            ):
                return False
        if kind == "force_quit_offer":
            if "?" not in text or not self._operation_is_named(
                text, "force_quit_app"
            ):
                return False
            if not self._subject_is_named(text, subject):
                return False
        if kind == "delete_offer":
            if "?" not in text or not self._operation_is_named(text, operation):
                return False
            if not self._subject_is_named(text, subject):
                return False
        if kind == "ui_action_offer":
            if "?" not in text:
                return False
            if not self._subject_is_named(text, subject):
                return False
        if kind == "already_exists" and not any(
            phrase in lowered for phrase in ("already", "exists", "taken")
        ):
            return False
        if kind == "needs_location" and "?" not in text:
            return False
        if kind == "ambiguous" and "?" not in text:
            return False
        if kind == "work_started" and any(
            phrase in lowered
            for phrase in (
                "completed", "done", "finished", "found it", "opened",
                "confirmation", "ready",
            )
        ):
            return False
        if kind == "work_started" and "?" in text:
            return False
        return True

    def _fallback(self, kind: str, subject: str, operation: str) -> str:
        action = {
            "open_app": "open",
            "close_app": "close",
            "open_url": "open",
            "create_file": "create",
            "create_folder": "create",
            "delete_file": "recycle",
            "delete_folder": "recycle",
            "force_quit_app": "force-quit",
            "ui_action": "click",
        }.get(operation, "do")
        options = [
            template.format(
                subject=subject or "that",
                action=action,
            )
            for template in self._FALLBACKS[kind]
        ]
        short_options = [
            option for option in options if len(option.split()) <= self.MAX_WORDS
        ] or options
        return next(
            (
                option
                for option in short_options
                if not self._is_repeated(option, subject)
            ),
            short_options[len(self._recent) % len(short_options)],
        )

    def _subject_is_named(self, text: str, subject: str) -> bool:
        subject_key = self._normalize(subject).removesuffix("launcher")
        text_key = self._normalize(text)
        if not subject_key:
            return True
        if subject_key in text_key:
            return True
        domain_core = re.sub(r"(?:com|org|net|io|dev|app)$", "", subject_key)
        return bool(domain_core and len(domain_core) >= 3 and domain_core in text_key)

    @staticmethod
    def _operation_is_named(text: str, operation: str) -> bool:
        words = set(re.findall(r"[a-z]+", text.casefold()))
        alternatives = {
            "open_app": {"open", "launch", "start"},
            "close_app": {"close", "exit", "quit"},
            "open_url": {"open", "visit", "browse"},
            "create_file": {"create", "make", "add"},
            "create_folder": {"create", "make", "add"},
            "delete_file": {"delete", "remove", "recycle", "trash"},
            "delete_folder": {"delete", "remove", "recycle", "trash"},
            "force_quit_app": {"force", "quit", "close", "terminate"},
        }.get(operation, set())
        if operation == "force_quit_app":
            lowered = text.casefold()
            return (
                "force" in words
                or "terminate" in words
                or "completely" in words and ("quit" in words or "close" in words)
                or "force-quit" in lowered
                or "force-close" in lowered
            )
        return bool(words.intersection(alternatives))

    def _is_repeated(self, candidate: str, subject: str = "") -> bool:
        opening = self._opening(candidate)
        if opening and opening in self._recent_openings:
            return True
        normalized = self._normalize(candidate)
        if any(
            SequenceMatcher(None, normalized, self._normalize(previous)).ratio()
            >= 0.88
            for previous in self._recent
        ):
            return True
        shape = self._response_shape(candidate, subject)
        return bool(shape) and any(
            SequenceMatcher(None, shape, previous).ratio() >= 0.80
            for previous in self._recent_shapes
            if previous
        )

    def _response_shape(self, value: str, subject: str) -> str:
        shape = self._normalize(value)
        subject_key = self._normalize(subject).removesuffix("launcher")
        if subject_key:
            shape = shape.replace(subject_key, "")
            domain_core = re.sub(
                r"(?:com|org|net|io|dev|app)$",
                "",
                subject_key,
            )
            if domain_core:
                shape = shape.replace(domain_core, "")
        return shape

    @staticmethod
    def _opening(value: str) -> str:
        words = re.findall(r"[a-z]+", str(value).casefold())
        if not words or words[0] not in {
            "got", "sure", "okay", "done", "alright", "all", "on", "right",
        }:
            return ""
        return "".join(words[:2]) if len(words) > 1 else words[0]

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).casefold())

    @staticmethod
    def _safe_punctuation(value: str) -> str:
        return str(value).translate(str.maketrans({
            "\u2013": ", ",
            "\u2014": ", ",
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u2026": "...",
        }))

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
