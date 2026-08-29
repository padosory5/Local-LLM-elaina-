"""Short, varied, outcome-locked voice responses for actions and agents.

Every kind here reports a **result** and names a **subject** -- "Got it,
Spotify is open." They are outcome-locked on purpose: a line claiming success
for a failure is a correctness bug, so each candidate is validated against the
real status before it is allowed out, and the model call is worth its latency
because the wording has to carry a specific subject.

The contentless lines -- an acknowledgement, a status while work is still
running -- belong to :mod:`brain.action_status` instead. Those are chosen
locally with no model call, because the sentence covering a wait must not
itself wait. The ``work_started`` kind that used to live here was exactly that
sentence, and it is gone rather than duplicated.
"""

from __future__ import annotations

import re
from collections import deque
from difflib import SequenceMatcher
from typing import Any

from brain.text_filter import TextFilter


class BriefResponseGenerator:
    """Generate natural variety without allowing a line to change action state."""

    MAX_WORDS = 7

    # How many recent openings a category bars. Every line competes for these
    # slots now, not only the stock ones, so the window has to leave a small
    # bank somewhere to go: the narrowest here holds three lines.
    OPENING_WINDOW = 4

    # What counts as reporting something that did not happen. Public, and used
    # by scripts/live_brief_response_check.py, because that check kept its own
    # shorter copy: "That app is missing." is one of this class's own
    # not_found lines and the check called it untruthful.
    NEGATIVE_PHRASES = (
        "can't", "cannot", "couldn't", "didn't", "doesn't", "don't",
        "failed", "isn't", "not found", "won't", "missing", "wrong",
        "not allowed", "invalid", "restricted", "unavailable", "not ",
    )

    # Kinds that read as the same *type* of line (a plain success ack, or a
    # yes/no offer) share one opening-repetition budget, so "Got it" isn't
    # allowed back-to-back just because the second one was technically a
    # different kind (opened then closed, say). Anything not listed here
    # tracks against its own kind alone -- "on it" starting a task must
    # never compete with "got it" closing one for the same few slots, or
    # the rarer kind starves and the busier one dominates every fallback.
    _OPENING_CATEGORIES = {
        "opened": "success",
        "closed": "success",
        "close_requested": "success",
        "force_quit": "success",
        "url_opened": "success",
        "file_created": "success",
        "folder_created": "success",
        "file_deleted": "success",
        "folder_deleted": "success",
        "force_quit_offer": "offer",
        "delete_offer": "offer",
        "ui_action_offer": "offer",
    }

    _FALLBACKS = {
        "opened": (
            "Got it—{subject} is open.",
            "Sure, {subject} is ready.",
            "Done—{subject} is open.",
            "All set with {subject}.",
            "Opened {subject}.",
            "There you go—{subject} is up.",
            "{subject} is open now.",
            "Up and running—{subject}.",
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
            "{subject} is closed now.",
            "That's {subject} closed.",
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
            "New tab, {subject} is up.",
            "There it is—{subject}.",
        ),
        "file_created": (
            "Got it—created {subject}.",
            "Sure, {subject} is ready.",
            "Done—your file is ready.",
            "Created {subject}.",
            "{subject} is ready to go.",
            "Made {subject} for you.",
        ),
        "folder_created": (
            "Got it—created {subject}.",
            "Sure, the folder is ready.",
            "Done—{subject} is ready.",
            "Created the {subject} folder.",
            "{subject} folder is ready.",
            "Made the {subject} folder.",
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
        self._recent_openings: dict[str, deque[str]] = {}

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
            self._recent_openings.setdefault(
                self._category(kind), deque(maxlen=self.OPENING_WINDOW)
            ).append(opening)
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
        }[kind]
        variation = (
            "minimal acknowledgement",
            "friendly conversational phrasing",
            "target-first phrasing",
            "verb-first phrasing",
            "lightly playful but clear phrasing",
        )[len(self._recent) % 5]
        # Naming stock openers here taught the model to use them: the old
        # wording listed okay/sure/got it/done/all set as examples, and those
        # words then opened most of what she said. Now the recent openings go
        # in as words to avoid, and starting with the result or the target is
        # what gets recommended instead.
        avoid = ", ".join(self._recent_openings_for(kind)) or "(none yet)"
        return (
            "Write one natural voice-bot line only, without quotes or labels. "
            f"Use no more than {self.MAX_WORDS} words. Start the line with a "
            "different word than the recent ones; leading with the result or "
            "the target usually sounds better than a stock opener. Never add "
            "an offer to help. Never repeat or closely paraphrase a recent "
            "line. The trusted status is absolute: never claim success for "
            "failure or failure for success. Confirmation lines must be "
            "questions and clearly name the action and target.\n\n"
            f"Status: {kind}\nOperation: {operation or '(none)'}\n"
            f"Subject: {subject or '(none)'}\nDetail: {detail or '(none)'}\n"
            f"Instruction: {rules}\nVariation style: {variation}\n"
            f"Do not start with: {avoid}\n"
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
        if self._is_repeated(text, subject, kind):
            return False

        negative = self.reads_as_negative(text)
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
        # "blocked" is also where every unrecognized kind lands, so it is the
        # last line of defence for a caller asking for something this class no
        # longer knows about. Without it, a model line such as "Done, I found
        # it." was returned verbatim for an action she had actually refused.
        if kind in {
            "not_found", "item_not_found", "failed", "declined", "not_running",
            "invalid_target", "outside_allowed", "wrong_type", "blocked",
        } and not negative:
            return False
        if kind == "blocked" and "?" in text:
            return False
        # A close was requested, not observed. Claiming the app is closed is
        # the same class of lie as claiming success for a failure, and the
        # prompt's "do not claim full exit" was the only thing preventing it.
        if kind == "close_requested" and any(
            phrase in lowered
            for phrase in (
                "is closed", "has closed", "closed it", "is gone",
                "fully closed", "is now closed",
            )
        ):
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
        # Scanning from index 0 every time systematically preferred whatever
        # sat first in each bank -- and the first entry was nearly always the
        # stock "Got it" or "Sure". Starting the scan at a rotating offset
        # gives the rest of the bank the same chance.
        start = len(self._recent) % len(short_options)
        ordered = short_options[start:] + short_options[:start]
        return next(
            (
                option
                for option in ordered
                if not self._is_repeated(option, subject, kind)
            ),
            ordered[0],
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

    @classmethod
    def reads_as_negative(cls, text: str) -> bool:
        """Whether this line reports something that did not happen."""
        lowered = str(text).casefold()
        return any(phrase in lowered for phrase in cls.NEGATIVE_PHRASES)

    def _category(self, kind: str) -> str:
        """The opening-repetition budget this kind draws on."""
        return self._OPENING_CATEGORIES.get(kind, kind)

    def _recent_openings_for(self, kind: str) -> tuple[str, ...]:
        return tuple(self._recent_openings.get(self._category(kind), ()))

    def _is_repeated(
        self, candidate: str, subject: str = "", kind: str = "",
    ) -> bool:
        opening = self._opening(candidate)
        category = self._category(kind)
        if opening and opening in self._recent_openings.get(category, ()):
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
        """The first word, for any line -- not only the stock ones.

        This used to return "" unless the line began with one of eight
        approved openers, so "Opened Discord" and "Created the Trip folder"
        recorded nothing and never displaced "sure" or "done" from the
        window. Measured over twenty spoken outcome lines, three stock words
        opened twelve of them. Every line competes for the window now.
        """
        words = re.findall(r"[a-z]+", str(value).casefold())
        return words[0] if words else ""

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
