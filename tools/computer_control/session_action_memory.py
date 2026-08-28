"""Local, per-session memory of desktop actions Elaina just performed.

The same principle as :mod:`session_item_memory`, applied to actions rather
than files: a follow-up like "stop it" or "pause that" has to resolve to
something real, and the only trustworthy record of what Elaina just did is
the one she wrote herself from *verified* tool results.

Without this, "stop the music" depends on the model remembering, from
conversation text, which track it queued -- and a small local model asked to
recall a song title will confidently produce a plausible wrong one. Recording
the subject at the moment a verified action succeeds means the follow-up
binds to state instead. This never lets the model choose a target; it only
lets a deictic reference bind to something Elaina herself just did.

Only verified actions are recorded. A click that could not be confirmed is
not evidence that anything is playing, and remembering it would make "stop
it" act on a track that was never started.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Enough to cover "stop it" pointing a few turns back, without letting a
# long session accumulate stale targets that outlive their usefulness.
_MAX_ACTIONS_PER_APP = 4
_MAX_ACTIONS = 12
# Beyond this, a deictic follow-up almost certainly means something else.
_RELEVANCE_SECONDS = 1800.0


@dataclass(frozen=True)
class SessionAction:
    """One verified thing Elaina did in an application."""

    app: str
    family: str  # "activation", "text_input", "selection", "launch", ...
    subject: str  # the track, file, or item the action was about
    window_title: str
    control_name: str
    created_at: float
    # Window handle, because a title is not stable identity: Spotify
    # retitles its window to the playing track, so the title recorded when
    # a song was started no longer names any window by the time "stop it"
    # arrives. The handle still does.
    window_handle: int | None = None

    def describe(self) -> str:
        if self.subject:
            return f"{self.family} {self.subject!r} in {self.app}"
        return f"{self.family} in {self.app}"


class SessionActionMemory:
    """Remember the most recent verified desktop actions this session."""

    def __init__(self, *, clock=None) -> None:
        self._clock = clock or time.time
        self._actions: list[SessionAction] = []

    def record(
        self,
        *,
        app: str,
        family: str,
        subject: str = "",
        window_title: str = "",
        control_name: str = "",
        window_handle: int | None = None,
    ) -> SessionAction | None:
        """Record one verified action. Returns it, or None if unusable."""
        app_name = str(app or window_title or "").strip()
        family_name = str(family or "").strip()
        if not app_name or not family_name:
            return None
        action = SessionAction(
            app=app_name,
            family=family_name,
            subject=str(subject or "").strip(),
            window_title=str(window_title or "").strip(),
            control_name=str(control_name or "").strip(),
            created_at=self._clock(),
            window_handle=window_handle,
        )
        self._actions.append(action)
        self._trim()
        return action

    def _trim(self) -> None:
        by_app: dict[str, list[SessionAction]] = {}
        for action in self._actions:
            by_app.setdefault(action.app.casefold(), []).append(action)
        kept = [
            action
            for app_actions in by_app.values()
            for action in app_actions[-_MAX_ACTIONS_PER_APP:]
        ]
        kept.sort(key=lambda action: action.created_at)
        self._actions = kept[-_MAX_ACTIONS:]

    # ------------------------------------------------------------------
    # queries

    def recent(self, *, app: str = "", family: str = "") -> tuple[SessionAction, ...]:
        wanted_app = str(app or "").strip().casefold()
        wanted_family = str(family or "").strip()
        cutoff = self._clock() - _RELEVANCE_SECONDS
        return tuple(
            action
            for action in self._actions
            if action.created_at >= cutoff
            and (not wanted_app or action.app.casefold() == wanted_app)
            and (not wanted_family or action.family == wanted_family)
        )

    def last_action(self, *, app: str = "") -> SessionAction | None:
        """The most recent still-relevant action, optionally within one app."""
        actions = self.recent(app=app)
        return actions[-1] if actions else None

    def last_subject(self, *, app: str = "") -> SessionAction | None:
        """The most recent action that was *about* something nameable.

        This is what a bare "stop it" resolves against: the last action that
        carried a subject, so "stop it" after playing a track targets that
        track rather than a subsequent volume change.
        """
        for action in reversed(self.recent(app=app)):
            if action.subject:
                return action
        return None

    def recent_context(self) -> tuple[dict[str, str], ...]:
        """Plain-dict snapshot, matching active_desktop_surface's shape."""
        return tuple(
            {
                "app": action.app,
                "action": action.family,
                "subject": action.subject,
                "window": action.window_title,
                "handle": action.window_handle,
            }
            for action in self.recent()
        )

    def clear(self) -> None:
        self._actions = []
