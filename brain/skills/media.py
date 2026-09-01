"""Playing things in a media app, as procedures rather than as guesswork.

Two skills so far, and they differ in exactly the way the app does:

* **play_track** -- the requested song is one row among near-identical
  decoys, so everything here is about aiming: search with title *and*
  artist, activate a control that names the exact title, and prove the app
  is playing it. Ported unchanged from the planner, where it was written
  and verified live against Spotify.
* **play_collection** -- nothing needs aiming, because the person named a
  place rather than an item. Open it, start it, and prove something began.

Both hand back rather than guess. "Hand back" means the ordinary planning
loop takes over with every guard still in place; it never means acting on
a target this code could not establish.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from brain.deliberation.goal import Goal
from brain.media_target import MediaTarget
from tools.computer_control.windows_ui_control import UIActionResult
from tools.computer_control.windows_ui_observer import ControlInfo, WindowInfo

# Results arrive asynchronously after a query is typed, and a CEF tree
# rebuilds a beat behind what is already drawn.
_RESULT_ATTEMPTS = 3
_RESULT_SETTLE_SECONDS = 1.2
# A cold tree needs waking before it can be said to lack a search box.
_TREE_WAKE_ATTEMPTS = 3
_TREE_WAKE_SECONDS = 0.4
# One retry: a first double-click that lands while the result list is still
# reflowing hits the row that was there a moment ago.
_ACTIVATION_ATTEMPTS = 2
# Playback starts a beat after the click lands, and the app renames its own
# window only once audio is running. Each attempt is one window-title read.
_PLAYBACK_ATTEMPTS = 8
_PLAYBACK_INTERVAL_SECONDS = 0.4
_WINDOW_APPEAR_ATTEMPTS = 6
_WINDOW_APPEAR_INTERVAL_SECONDS = 0.6
# An app closed to the tray needs longer than one merely being focused.
_LAUNCHED_WINDOW_ATTEMPTS = 16

_SEARCH_STEMS = ("search", "검색", "찾기", "찾아보기")
_SEARCH_FIELD_ROLES = frozenset({"edit", "combobox"})
_SEARCH_LABELS = frozenset({
    "search", "검색", "검색하기", "찾기", "찾아보기", "search spotify",
})
_PLAY_VERBS = ("재생하기", "재생", "play", "듣기")
# Labels that contain a play word without being a play control. Measured
# live: "WORKOUT PLAYLIST 2026 플레이리스트 • Systemic" contains "play"
# inside "playlist", and clicking it started someone else's playlist while
# looking for the button that starts the liked songs.
_PLAY_DECOYS = (
    "재생 목록", "재생목록", "재생 중", "재생중", "플레이리스트",
    "playlists", "playlist",
)
_PAUSE_TERMS = ("pause", "일시 정지", "일시정지", "정지", "중지")
_SHUFFLE_TERMS = ("shuffle", "셔플")

# What each collection is called in the app's own sidebar. A collection is
# playable only if it appears here: "my playlist" names no particular
# playlist, and guessing which one would be exactly the kind of confident
# wrong answer this layer exists to prevent.
_COLLECTION_LABELS = {
    "liked songs": ("liked songs", "좋아요 표시한 곡", "좋아요 표시된 곡"),
    "saved songs": ("liked songs", "좋아요 표시한 곡"),
    "favourites": ("liked songs", "좋아요 표시한 곡"),
    "favorites": ("liked songs", "좋아요 표시한 곡"),
}

# Window titles that mean the app is idle rather than playing something.
_IDLE_TITLE_WORDS = frozenset({"spotify", "premium", "free"})


def _normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.split()).strip()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", _normalized(value), flags=re.UNICODE))


def _has_search_stem(label: str) -> bool:
    text = _normalized(label)
    return any(stem in text for stem in _SEARCH_STEMS)


def _is_search_label(label: str) -> bool:
    return _normalized(label) in _SEARCH_LABELS


def _names_play(label: str) -> bool:
    """Whether this control starts something, rather than merely naming one."""
    text = _normalized(label)
    for decoy in _PLAY_DECOYS:
        text = text.replace(decoy, " ")
    if "play" in set(re.findall(r"[^\W_]+", text)):
        return True
    return any(verb in text for verb in ("재생하기", "재생", "듣기"))


def _without_play_verb(label: str) -> str:
    """The label with its play verb removed, in either language."""
    text = _normalized(label)
    for verb in _PLAY_VERBS:
        text = text.replace(verb, " ")
    return " ".join(text.split())


def _names_pause(label: str) -> bool:
    text = _normalized(label)
    return any(term in text for term in _PAUSE_TERMS)


def _names_shuffle(label: str) -> bool:
    return any(term in _normalized(label) for term in _SHUFFLE_TERMS)


def live_window_titles(observer: Any) -> dict[int, str]:
    """Current title of every open window, keyed by handle."""
    try:
        windows = observer.list_windows()
    except Exception:
        return {}
    titles: dict[int, str] = {}
    for window in windows:
        handle = getattr(window, "handle", None)
        title = str(getattr(window, "title", "") or "")
        if handle is not None and title:
            titles[handle] = title
    return titles


def playback_evidence(
    observer: Any,
    snapshot: WindowInfo | None,
    title: str,
    *,
    baseline: str = "",
    sleeper=time.sleep,
) -> tuple[bool, str]:
    """Whether the named track is audibly playing, from live state.

    Spotify renames its own top-level window to the track it is playing and
    back to the plain product name when it stops, so the strongest proof
    available costs one window-title read rather than another walk of a
    700-node tree. The window is followed by handle, not by name, precisely
    because that name is what changes.
    """
    title_key = _normalized(title)
    if not title_key:
        return False, "The request named no exact title to verify."
    handle = getattr(snapshot, "handle", None)
    for attempt in range(_PLAYBACK_ATTEMPTS):
        if attempt:
            sleeper(_PLAYBACK_INTERVAL_SECONDS)
        titles = live_window_titles(observer)
        live = str(titles.get(handle, "") if handle is not None else "")
        if not live:
            live = next(
                (
                    value for value in titles.values()
                    if title_key in _normalized(value)
                ),
                "",
            )
        if title_key not in _normalized(live):
            continue
        if _normalized(live) != _normalized(baseline):
            return True, f"The app's own window is now titled {live!r}."
        # The window already named this track before we touched it, so the
        # title proves it is loaded, not that it is playing. A control
        # offering to *pause* does prove that.
        return _transport_evidence(observer, snapshot, live)
    return False, f"No open window names {title!r} as playing."


def _transport_evidence(
    observer: Any, snapshot: WindowInfo | None, live_title: str,
) -> tuple[bool, str]:
    """Whether the app is offering to pause -- which only playback does."""
    target = snapshot if snapshot is not None else live_title
    try:
        observation = observer.describe_window(target)
    except Exception:
        return False, "The app's controls could not be inspected."
    if getattr(observation, "status", "") != "observed":
        return False, "The app's controls could not be inspected."
    for control in observation.controls:
        if _names_pause(control.name):
            return True, (
                f"{control.name!r} is offering to pause, so it is playing."
            )
    return False, (
        f"{live_title!r} was already showing before this, and nothing is "
        "offering to pause."
    )


@dataclass(frozen=True)
class SkillResult:
    """What a skill did, in terms its caller can report honestly."""

    # "done", "failed", "interrupted", or "handed_back" -- the last meaning
    # this procedure established nothing and the ordinary loop should try.
    status: str
    summary: str = ""
    steps: tuple[str, ...] = ()
    failure_code: str = ""
    activated: str = ""

    @property
    def handed_back(self) -> bool:
        return self.status == "handed_back"


HANDED_BACK = SkillResult("handed_back")


class Skill(Protocol):
    name: str
    goal_kinds: tuple[str, ...]
    required_slots: tuple[str, ...]

    def run(self, goal: Goal, surface: "MediaSurface") -> SkillResult:
        ...


class MediaSurface:
    """Everything a media skill needs from the live machine.

    The skills know what to do; this knows how to look and how to touch.
    Keeping them apart is what lets a skill be read as a procedure.
    """

    def __init__(
        self,
        *,
        observer: Any,
        control: Any,
        computer_control: Any = None,
        session_actions: Any = None,
        sleeper=None,
    ) -> None:
        self.observer = observer
        self.control = control
        self.computer_control = computer_control
        self.session_actions = session_actions
        self._sleep = sleeper or time.sleep

    # ------------------------------------------------------------------
    # capability

    @property
    def can_activate(self) -> bool:
        """Whether this driver can make the gestures these skills need."""
        return bool(
            getattr(self.control, "double_click_control", None)
            and getattr(self.control, "available", True)
        )

    # ------------------------------------------------------------------
    # looking

    def window(self, app: str) -> WindowInfo | None:
        """The app's live window, even once it has renamed itself.

        Spotify's window title becomes the track it is playing, so a title
        search for "Spotify" finds nothing precisely when music is already
        on. A handle recorded earlier this session still names it.
        """
        found = self.observer.find_window(app)
        if found is not None:
            return found if isinstance(found, WindowInfo) else self._info(found)
        memory = self.session_actions
        if memory is not None:
            app_key = _normalized(app)
            try:
                recent = memory.recent_context()
            except Exception:
                recent = ()
            live = live_window_titles(self.observer)
            for item in reversed(list(recent or ())):
                handle = item.get("handle")
                if handle in live and app_key in _normalized(
                    str(item.get("app", ""))
                ):
                    snapshot = WindowInfo(title=live[handle], handle=handle)
                    if self.observer.find_window(snapshot) is not None:
                        return snapshot
        if self.computer_control is None:
            return None
        opened = self.computer_control.open_app(app)
        if not opened.succeeded:
            return None
        # An app closed to the tray is "opened" by restoring a hidden
        # window, which takes longer than the ordinary budget allows.
        return self._wait_for_window(
            opened.display_name or app, attempts=_LAUNCHED_WINDOW_ATTEMPTS,
        )

    def _wait_for_window(
        self, hint: str, *, attempts: int = _WINDOW_APPEAR_ATTEMPTS,
    ) -> WindowInfo | None:
        for _ in range(attempts):
            window = self.observer.find_window(hint)
            if window is not None:
                return (
                    window if isinstance(window, WindowInfo)
                    else self._info(window)
                )
            self._sleep(_WINDOW_APPEAR_INTERVAL_SECONDS)
        return None

    def _info(self, window: Any) -> WindowInfo:
        observer = self.observer
        title = observer._safe_text(window)
        handle = getattr(observer, "_safe_handle", lambda _w: None)(window)
        class_name = getattr(observer, "_safe_class_name", lambda _w: "")(window)
        process_id = getattr(observer, "_safe_process_id", lambda _w: None)(window)
        return WindowInfo(
            title=title,
            app_name=class_name,
            handle=handle,
            process_id=process_id,
            class_name=class_name,
        )

    def observe(self, window: WindowInfo, *, expecting=None) -> Any:
        """Read the window, waking a cold tree before believing it.

        A CEF window exposes only its frame until something queries it
        (Spotify measured 25 nodes cold, 1465 warm), and these scans run
        immediately after focusing.
        """
        observation = None
        for attempt in range(_TREE_WAKE_ATTEMPTS):
            if attempt:
                self._sleep(_TREE_WAKE_SECONDS)
            observation = self.observer.describe_window(window)
            if getattr(observation, "status", "") != "observed":
                continue
            if expecting is None or any(
                expecting(control) for control in observation.controls
            ):
                break
        return observation

    def live_title(self, window: WindowInfo) -> str:
        return live_window_titles(self.observer).get(
            getattr(window, "handle", None), "",
        )

    # ------------------------------------------------------------------
    # acting

    def search(self, window: WindowInfo, query: str) -> UIActionResult | None:
        """Type a query into the app's own search affordance."""
        observation = self.observe(
            window, expecting=lambda control: _has_search_stem(control.name),
        )
        if getattr(observation, "status", "") != "observed":
            return None
        field = next(
            (
                control for control in observation.controls
                if _role_key(control.role) in _SEARCH_FIELD_ROLES
                and _has_search_stem(control.name)
            ),
            None,
        )
        if field is not None:
            return self.control.type_text(
                window, field.name, query, element_id=field.element_id,
            )
        # Exact label first. A stem match alone once picked a control called
        # "Spotify - 검색하기" -- something else entirely that merely contains
        # the word -- clicked it, and typed the query into whatever that
        # opened. Chromium/CEF apps often expose only the button that
        # reveals the real field, which is what click_then_type is for.
        button = next(
            (
                control for control in observation.controls
                if control.is_actionable and _is_search_label(control.name)
            ),
            None,
        ) or next(
            (
                control for control in observation.controls
                if control.is_actionable and _has_search_stem(control.name)
            ),
            None,
        )
        if button is None:
            return None
        return self.control.click_then_type(
            window, button.name, query, element_id=button.element_id,
        )

    def exact_row(
        self, window: WindowInfo, title: str, artist: str = "",
    ) -> ControlInfo | None:
        """The one live control whose name is exactly the requested title.

        Exactness is the whole guard: "Bang Bang Radio", "Bang Bang Mix"
        and "Bang Bang by IVE" all contain the title and none of them are
        it. When an artist was named it must be visible beside the match,
        which is what separates two same-titled songs.
        """
        title_key = _normalized(title)
        artist_key = _normalized(artist)
        for _attempt in range(_RESULT_ATTEMPTS):
            self._sleep(_RESULT_SETTLE_SECONDS)
            observation = self.observer.describe_window(window)
            if getattr(observation, "status", "") != "observed":
                continue
            controls = tuple(observation.controls)
            for index, control in enumerate(controls):
                if _normalized(control.name) != title_key:
                    continue
                if not artist_key:
                    return control
                nearby = controls[max(0, index - 6):index + 7]
                if any(
                    artist_key in _normalized(
                        " ".join((other.name, other.value))
                    )
                    for other in nearby
                ):
                    return control
        return None

    def own_play_control(
        self, window: WindowInfo, title: str,
    ) -> ControlInfo | None:
        """A play control that names the requested item and nothing else.

        Spotify labels each result row's button with the track it belongs
        to -- "After LIKE 재생하기", "Play After LIKE". Removing the play
        verb must leave the exact title: "BANG BANG Radio 재생하기" leaves
        "BANG BANG Radio", which is the station, not the song.
        """
        title_key = _normalized(title)
        if not title_key:
            return None
        observation = self.observer.describe_window(window)
        if getattr(observation, "status", "") != "observed":
            return None
        for control in observation.controls:
            if not control.is_actionable:
                continue
            label = _normalized(control.name)
            if not label or label == title_key or not _names_play(label):
                continue
            if _normalized(_without_play_verb(label)) == title_key:
                return control
        return None

    def play_opened_item(
        self, window: WindowInfo, title: str,
    ) -> UIActionResult | None:
        """Press Play on the page the requested item just opened, or nothing.

        A generic Play control is normally the exact thing this layer
        refuses: on a results list it starts whatever the app feels like.
        On the item's own page it can only start that item, and two live
        conditions separate those cases -- the exact title is present, and
        none of the near-miss rows that only exist in a results list are.
        """
        observation = self.observer.describe_window(window)
        if getattr(observation, "status", "") != "observed":
            return None
        controls = tuple(observation.controls)
        title_key = _normalized(title)
        title_terms = _tokens(title)
        titled = False
        for control in controls:
            label = _normalized(control.name)
            if label == title_key:
                titled = True
                continue
            if title_terms and title_terms <= _tokens(label):
                return None
        if not titled:
            return None
        play = next(
            (
                control for control in controls
                if control.is_actionable and _names_play(control.name)
            ),
            None,
        )
        if play is None:
            return None
        return self.control.click_control(
            window, play.name, element_id=play.element_id,
        )

    def remember(
        self, *, window: WindowInfo, subject: str, control_name: str,
    ) -> None:
        """Record a verified activation, so "stop it" has a real target."""
        memory = self.session_actions
        if memory is None:
            return
        title = str(getattr(window, "title", "") or "")
        try:
            memory.record(
                app=title or str(getattr(window, "app_name", "") or ""),
                family="activation",
                subject=subject,
                window_title=title,
                control_name=control_name,
                window_handle=getattr(window, "handle", None),
            )
        except Exception:
            # Memory helps later turns; it is never a reason to fail this one.
            pass


def _role_key(role: str) -> str:
    return re.sub(r"[^a-z]", "", str(role or "").casefold())


class PlayTrackSkill:
    """Play one exactly-named track, without asking the model to aim."""

    name = "play_track"
    goal_kinds = ("play_track",)
    required_slots = ("title",)

    def run(self, goal: Goal, surface: MediaSurface) -> SkillResult:
        if not surface.can_activate:
            return HANDED_BACK
        target = MediaTarget(
            application=goal.value("provider") or "Spotify",
            title=goal.value("title"),
            artist=goal.value("artist"),
        )
        if not target.title:
            return HANDED_BACK
        window = surface.window(target.application)
        if window is None:
            return HANDED_BACK

        steps: list[str] = []
        focus = surface.control.focus_window(window)
        if focus.status == "user_took_over":
            return _interrupted(focus, steps)
        if focus.status != "focused":
            return HANDED_BACK
        steps.append(focus.message)

        typed = surface.search(window, target.search_query)
        if typed is None:
            return HANDED_BACK
        if typed.status == "user_took_over":
            return _interrupted(typed, steps)
        if typed.status != "typed":
            return HANDED_BACK
        steps.append(typed.message)

        # A title the window already carried cannot be evidence that this
        # run started it.
        baseline = surface.live_title(window)
        activated = ""
        for attempt in range(_ACTIVATION_ATTEMPTS):
            if attempt == 0:
                # A row's own play control is the least ambiguous thing on
                # screen: it names the track it plays, so one click starts
                # exactly that -- no double-click, and nothing a link could
                # open instead.
                own = surface.own_play_control(window, target.title)
                if own is not None:
                    result = surface.control.click_control(
                        window, own.name, element_id=own.element_id,
                    )
                    if result.status == "user_took_over":
                        return _interrupted(result, steps)
                    if result.status == "clicked":
                        steps.append(result.message)
                        playing, evidence = playback_evidence(
                            surface.observer, window, target.title,
                            baseline=baseline, sleeper=surface._sleep,
                        )
                        if playing:
                            return _played(
                                surface, window, target, own.name, steps,
                            )
                row = surface.exact_row(window, target.title, target.artist)
                if row is None:
                    # No provable row means no click. Duplicate titles, a
                    # result list that has not arrived, and an artist
                    # nowhere near the match all land here -- every one of
                    # them a reason to hand back rather than to guess.
                    return HANDED_BACK
                activated = row.name
                result = surface.control.double_click_control(
                    window, row.name, element_id=row.element_id,
                )
            else:
                # The double-click started nothing, which on this app means
                # the title was a link and we are standing on the item's own
                # page. play_opened_item refuses unless the page proves
                # that, so a results list full of decoys never reaches it.
                opened = surface.play_opened_item(window, target.title)
                if opened is None:
                    break
                result = opened
            if result.status == "user_took_over":
                return _interrupted(result, steps)
            if result.status != "clicked":
                return HANDED_BACK
            steps.append(result.message)
            playing, _evidence = playback_evidence(
                surface.observer, window, target.title,
                baseline=baseline, sleeper=surface._sleep,
            )
            if playing:
                return _played(surface, window, target, activated, steps)

        return SkillResult(
            "failed",
            (
                f"I opened {target.title} in {target.application}, but I "
                "couldn't confirm it started playing."
            ),
            steps=tuple(steps),
            failure_code="playback_unverified",
        )


class PlayCollectionSkill:
    """Start a collection the person named: their liked songs, say.

    Nothing here needs aiming, which is the whole difference from
    play_track: they named a place rather than an item, so there is no
    near-miss row to be tricked by and no exact title to prove. What must
    be proved is only that something started.
    """

    name = "play_collection"
    goal_kinds = ("play_collection",)
    required_slots = ("collection",)

    def run(self, goal: Goal, surface: MediaSurface) -> SkillResult:
        collection = _normalized(goal.value("collection"))
        labels = _COLLECTION_LABELS.get(collection, ())
        if not labels or not surface.can_activate:
            return HANDED_BACK
        window = surface.window("Spotify")
        if window is None:
            return HANDED_BACK

        steps: list[str] = []
        focus = surface.control.focus_window(window)
        if focus.status == "user_took_over":
            return _interrupted(focus, steps)
        if focus.status != "focused":
            return HANDED_BACK
        steps.append(focus.message)

        baseline = surface.live_title(window)
        entry = self._entry(surface, window, labels)
        if entry is None:
            return SkillResult(
                "failed",
                f"I couldn't find your {collection} in Spotify.",
                steps=tuple(steps),
                failure_code="collection_not_found",
            )

        # Its own play control, if the sidebar offers one, is a single step.
        if _names_play(entry.name):
            started = surface.control.click_control(
                window, entry.name, element_id=entry.element_id,
            )
        else:
            opened = surface.control.click_control(
                window, entry.name, element_id=entry.element_id,
            )
            if opened.status == "user_took_over":
                return _interrupted(opened, steps)
            if opened.status != "clicked":
                return HANDED_BACK
            steps.append(opened.message)
            started = self._start(surface, window, labels)
            if started is None:
                return SkillResult(
                    "failed",
                    f"I opened your {collection}, but found nothing to play it.",
                    steps=tuple(steps),
                    failure_code="collection_not_playable",
                )
        if started.status == "user_took_over":
            return _interrupted(started, steps)
        if started.status != "clicked":
            return HANDED_BACK
        steps.append(started.message)

        playing, now = _collection_playing(surface, window, baseline)
        if not playing:
            return SkillResult(
                "failed",
                (
                    f"I started your {collection}, but couldn't confirm "
                    "anything is playing."
                ),
                steps=tuple(steps),
                failure_code="playback_unverified",
            )
        surface.remember(
            window=window, subject=now or collection,
            control_name=started.control_name or entry.name,
        )
        spoken = f"Playing your {collection}"
        return SkillResult(
            "done",
            f"{spoken} -- {now} is on." if now else f"{spoken}.",
            steps=tuple(steps),
            activated=entry.name,
        )

    @staticmethod
    def _entry(
        surface: MediaSurface, window: WindowInfo, labels: tuple[str, ...],
    ) -> ControlInfo | None:
        """The sidebar entry for this collection, by its own name."""
        def names_it(control: ControlInfo) -> bool:
            label = _normalized(control.name)
            return any(name in label for name in labels)

        observation = surface.observe(window, expecting=names_it)
        if getattr(observation, "status", "") != "observed":
            return None
        matches = [
            control for control in observation.controls if names_it(control)
        ]
        # Prefer something that plays it outright, then anything actionable.
        return next(
            (
                control for control in matches
                if control.is_actionable and _names_play(control.name)
            ),
            next(
                (control for control in matches if control.is_actionable),
                matches[0] if matches else None,
            ),
        )

    @staticmethod
    def _start(
        surface: MediaSurface, window: WindowInfo, labels: tuple[str, ...],
    ) -> UIActionResult | None:
        """The play control on the collection's own page."""
        def is_play(control: ControlInfo) -> bool:
            return control.is_actionable and _names_play(control.name)

        observation = surface.observe(window, expecting=is_play)
        if getattr(observation, "status", "") != "observed":
            return None
        controls = tuple(observation.controls)
        if not any(
            any(name in _normalized(control.name) for name in labels)
            for control in controls
        ):
            # The page does not name the collection, so a Play control on it
            # could belong to anything.
            return None
        def names_it(control: ControlInfo) -> bool:
            label = _normalized(control.name)
            return any(name in label for name in labels)

        # A play control that names the collection is unambiguous; a bare
        # one is only safe because the page was already proved to be this
        # collection's own.
        play = next(
            (
                control for control in controls
                if is_play(control) and names_it(control)
            ),
            next((control for control in controls if is_play(control)), None),
        )
        if play is None:
            return None
        return surface.control.click_control(
            window, play.name, element_id=play.element_id,
        )


class ShuffleCollectionSkill:
    """Open one named collection and use its observed Shuffle control."""

    name = "shuffle_collection"
    goal_kinds = ("shuffle_collection",)
    required_slots = ("collection",)

    def run(self, goal: Goal, surface: MediaSurface) -> SkillResult:
        collection = _normalized(goal.value("collection"))
        labels = _COLLECTION_LABELS.get(collection, ())
        if not labels or not surface.can_activate:
            return HANDED_BACK
        window = surface.window("Spotify")
        if window is None:
            return SkillResult(
                "failed", "I couldn't find the Spotify window.",
                failure_code="spotify_not_found",
            )
        focus = surface.control.focus_window(window)
        if focus.status == "user_took_over":
            return _interrupted(focus, [])
        if focus.status != "focused":
            return HANDED_BACK
        entry = PlayCollectionSkill._entry(surface, window, labels)
        if entry is None:
            return SkillResult(
                "failed",
                "Spotify isn't exposing your Liked Songs controls, so I can't safely shuffle it.",
                steps=(focus.message,), failure_code="collection_not_observed",
            )
        opened = surface.control.click_control(
            window, entry.name, element_id=entry.element_id,
        )
        if opened.status == "user_took_over":
            return _interrupted(opened, [focus.message])
        if opened.status != "clicked":
            return SkillResult(
                "failed", "I couldn't open your Liked Songs in Spotify.",
                steps=(focus.message,), failure_code="collection_open_failed",
            )
        observation = surface.observe(
            window,
            expecting=lambda control: control.is_actionable and _names_shuffle(control.name),
        )
        shuffle = next(
            (
                control for control in getattr(observation, "controls", ())
                if control.is_actionable and _names_shuffle(control.name)
            ),
            None,
        )
        if shuffle is None:
            return SkillResult(
                "failed",
                "I opened your Liked Songs, but Spotify did not expose a Shuffle control I can safely use.",
                steps=(focus.message, opened.message), failure_code="shuffle_not_observed",
            )
        shuffled = surface.control.click_control(
            window, shuffle.name, element_id=shuffle.element_id,
        )
        if shuffled.status == "user_took_over":
            return _interrupted(shuffled, [focus.message, opened.message])
        if shuffled.status != "clicked":
            return SkillResult(
                "failed", "I couldn't activate Shuffle in your Liked Songs.",
                steps=(focus.message, opened.message), failure_code="shuffle_failed",
            )
        return SkillResult(
            "done", "Shuffling your liked songs now.",
            steps=(focus.message, opened.message, shuffled.message),
            activated=shuffle.name,
        )


class FindInCollectionSkill:
    """Find one exact track by scrolling a named, observed collection."""

    name = "find_in_collection"
    goal_kinds = ("find_in_collection",)
    required_slots = ("collection", "title")

    def run(self, goal: Goal, surface: MediaSurface) -> SkillResult:
        collection = _normalized(goal.value("collection"))
        title = goal.value("title").strip()
        labels = _COLLECTION_LABELS.get(collection, ())
        if not labels or not title or not surface.can_activate:
            return HANDED_BACK
        window = surface.window("Spotify")
        if window is None:
            return SkillResult("failed", "I couldn't find the Spotify window.")
        focus = surface.control.focus_window(window)
        if focus.status == "user_took_over":
            return _interrupted(focus, [])
        if focus.status != "focused":
            return HANDED_BACK
        entry = PlayCollectionSkill._entry(surface, window, labels)
        if entry is None:
            return SkillResult(
                "failed",
                "Spotify isn't exposing your Liked Songs controls, so I can't safely search inside it.",
                steps=(focus.message,), failure_code="collection_not_observed",
            )
        opened = surface.control.click_control(window, entry.name, element_id=entry.element_id)
        if opened.status == "user_took_over":
            return _interrupted(opened, [focus.message])
        if opened.status != "clicked":
            return SkillResult("failed", "I couldn't open your Liked Songs in Spotify.")
        steps = [focus.message, opened.message]
        scroll = getattr(surface.control, "scroll_control", None)
        for _attempt in range(6):
            row = surface.exact_row(window, title)
            if row is not None:
                baseline = surface.live_title(window)
                played = surface.control.double_click_control(
                    window, row.name, element_id=row.element_id,
                )
                if played.status == "user_took_over":
                    return _interrupted(played, steps)
                if played.status == "clicked":
                    playing, _ = playback_evidence(
                        surface.observer, window, title,
                        baseline=baseline, sleeper=surface._sleep,
                    )
                    if playing:
                        return SkillResult(
                            "done", f"Playing {title} from your liked songs.",
                            steps=tuple(steps + [played.message]), activated=row.name,
                        )
                return SkillResult(
                    "failed", f"I found {title}, but couldn't confirm it started playing.",
                    steps=tuple(steps + [played.message]), failure_code="playback_unverified",
                )
            observation = surface.observe(window)
            container = next(
                (
                    control for control in getattr(observation, "controls", ())
                    if control.is_actionable
                    and _role_key(control.role) in {"list", "document", "pane", "table", "tree"}
                ),
                None,
            )
            if container is None or not callable(scroll):
                return SkillResult(
                    "failed",
                    "I opened your Liked Songs, but Spotify did not expose a list I can safely scroll.",
                    steps=tuple(steps), failure_code="collection_not_scrollable",
                )
            moved = scroll(window, container.name, "down", element_id=container.element_id)
            if moved.status == "user_took_over":
                return _interrupted(moved, steps)
            if moved.status != "scrolled":
                return SkillResult(
                    "failed", "I couldn't scroll your Liked Songs safely.",
                    steps=tuple(steps), failure_code="collection_scroll_failed",
                )
            steps.append(moved.message)
        return SkillResult(
            "failed", f"I couldn't find {title} in the visible part of your Liked Songs.",
            steps=tuple(steps), failure_code="track_not_found_in_collection",
        )


def _collection_playing(
    surface: MediaSurface, window: WindowInfo, baseline: str,
) -> tuple[bool, str]:
    """Whether *something* started, and what it is, from the window title.

    A collection names no track in advance, so the proof is the app's own
    title changing away from its idle product name to whatever it started.
    """
    for attempt in range(_PLAYBACK_ATTEMPTS):
        if attempt:
            surface._sleep(_PLAYBACK_INTERVAL_SECONDS)
        live = surface.live_title(window)
        if not live or _normalized(live) == _normalized(baseline):
            continue
        if _tokens(live) <= _IDLE_TITLE_WORDS:
            continue
        return True, live.strip()
    return False, ""


def _played(
    surface: MediaSurface,
    window: WindowInfo,
    target: MediaTarget,
    activated: str,
    steps: list[str],
) -> SkillResult:
    surface.remember(
        window=window, subject=target.title,
        control_name=activated or target.title,
    )
    spoken = (
        f"Playing {target.title} by {target.artist} in {target.application}."
        if target.artist
        else f"Playing {target.title} in {target.application}."
    )
    return SkillResult("done", spoken, steps=tuple(steps), activated=activated)


def _interrupted(result: UIActionResult, steps: list[str]) -> SkillResult:
    return SkillResult(
        "interrupted",
        result.message,
        steps=tuple(steps),
        failure_code="user_took_over",
    )


_SKILLS: tuple[Skill, ...] = (
    PlayTrackSkill(), PlayCollectionSkill(), ShuffleCollectionSkill(),
    FindInCollectionSkill(),
)


def skill_for(goal: Goal) -> Skill | None:
    """The procedure that serves this goal, if she has one."""
    provider = goal.value("provider") or "Spotify"
    for skill in _SKILLS:
        # Exact-track activation is provider-neutral: focus the selected app,
        # search its real UI, activate the exact row, and verify playback.
        # Collection navigation still uses Spotify-specific labels and safely
        # hands other providers to the general desktop planner.
        if provider.casefold() != "spotify" and not isinstance(skill, PlayTrackSkill):
            continue
        if goal.kind in skill.goal_kinds and all(
            goal.has(slot) for slot in skill.required_slots
        ):
            return skill
    return None
