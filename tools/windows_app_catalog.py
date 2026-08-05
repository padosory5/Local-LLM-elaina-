"""Discover and safely launch applications registered with Windows."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import unicodedata
import webbrowser
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from tools.app_name_aliases import alias_candidates


DEFAULT_BROWSER_START_URL = "https://www.google.com"
_NON_APP_PREFIXES = (
    "documentation",
    "help",
    "readme",
    "release notes",
    "uninstall",
    "website",
)
_GENERIC_SUFFIXES = {"app", "application", "client", "launcher"}


def normalize_app_name(value: str) -> str:
    """Normalize display-name variations without guessing user intent."""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _name_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value))
    return [
        token.casefold()
        for token in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    ]


def app_name_aliases(display_name: str, *extra: str) -> frozenset[str]:
    """Derive strict aliases such as BattleNet and VSCode from a display name."""
    aliases = {
        normalize_app_name(display_name),
        *(normalize_app_name(value) for value in extra),
    }
    tokens = _name_tokens(display_name)
    if len(tokens) > 1 and tokens[-1] in _GENERIC_SUFFIXES:
        aliases.add(normalize_app_name("".join(tokens[:-1])))
        tokens = tokens[:-1]
    if len(tokens) > 1:
        aliases.add(normalize_app_name("".join(tokens)))
        aliases.add(normalize_app_name(
            "".join(token[0] for token in tokens[:-1]) + tokens[-1]
        ))
    return frozenset(alias for alias in aliases if alias)


@dataclass(frozen=True)
class AppEntry:
    id: str
    display_name: str
    launch_kind: str
    launch_value: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    source: str = ""

    @classmethod
    def create(
        cls,
        display_name: str,
        launch_kind: str,
        launch_value: str,
        *,
        aliases: Iterable[str] = (),
        source: str = "",
    ) -> "AppEntry":
        identity = f"{launch_kind}\0{launch_value}".encode(
            "utf-8", errors="replace"
        )
        return cls(
            id=hashlib.sha256(identity).hexdigest()[:20],
            display_name=str(display_name).strip(),
            launch_kind=str(launch_kind).strip(),
            launch_value=str(launch_value).strip(),
            aliases=app_name_aliases(display_name, *aliases),
            source=str(source).strip(),
        )


@dataclass(frozen=True)
class AppResolution:
    status: str
    query: str
    entry: AppEntry | None = None
    candidates: tuple[str, ...] = ()


class WindowsAppCatalog:
    """Lazy local catalog of launchable Start, registry, and Store apps."""

    _PRIORITY = {
        "shortcut": 0,
        "executable": 1,
        "uwp": 2,
        "protocol": 3,
        "browser": 4,
    }

    def __init__(
        self,
        *,
        entries: Iterable[AppEntry] | None = None,
        user_aliases: dict[str, str] | None = None,
    ) -> None:
        self._seed_entries = (
            tuple(entries) if entries is not None else None
        )
        self.user_aliases = {
            normalize_app_name(alias): normalize_app_name(target)
            for alias, target in (user_aliases or {}).items()
            if normalize_app_name(alias) and normalize_app_name(target)
        }
        self._entries: tuple[AppEntry, ...] = ()
        self._loaded = False

    @property
    def entries(self) -> tuple[AppEntry, ...]:
        self._ensure_loaded()
        return self._entries

    def refresh(self) -> tuple[AppEntry, ...]:
        discovered = (
            list(self._seed_entries)
            if self._seed_entries is not None
            else self._discover_windows_apps()
        )
        self._entries = self._deduplicate(discovered)
        self._loaded = True
        print(f"[Computer Control] Cataloged {len(self._entries)} apps.")
        return self._entries

    def resolve(self, query: str, *, refresh_on_miss: bool = True) -> AppResolution:
        self._ensure_loaded()
        normalized_query = normalize_app_name(query)
        if not normalized_query:
            return AppResolution("not_found", str(query))

        alias_target = self.user_aliases.get(normalized_query)
        lookup = alias_target or normalized_query
        matches = [entry for entry in self._entries if lookup in entry.aliases]

        if not matches:
            # The query and the app's real registered display name can be
            # in different languages (e.g. "Settings" vs. an OS whose
            # Settings app is only registered as "설정") -- retry with any
            # known translation before giving up.
            for candidate in alias_candidates(query.casefold()):
                candidate_lookup = normalize_app_name(candidate)
                matches = [
                    entry for entry in self._entries
                    if candidate_lookup in entry.aliases
                ]
                if matches:
                    break

        matches = self._unique_app_matches(matches)

        if len(matches) == 1:
            return AppResolution("resolved", str(query), entry=matches[0])
        if len(matches) > 1:
            return AppResolution(
                "ambiguous",
                str(query),
                candidates=tuple(entry.display_name for entry in matches[:5]),
            )

        if refresh_on_miss and self._seed_entries is None:
            self.refresh()
            return self.resolve(query, refresh_on_miss=False)

        suggestions = self._prefix_suggestions(normalized_query)
        if suggestions:
            return AppResolution(
                "ambiguous",
                str(query),
                candidates=tuple(suggestions),
            )

        # A likely STT mishearing (e.g. "battle nest" for "Battle.net") is
        # still a real, close name -- surface it as a candidate to confirm
        # rather than silently guessing or giving up outright.
        fuzzy_match = self._fuzzy_suggestion(normalized_query)
        if fuzzy_match:
            return AppResolution(
                "ambiguous", str(query), candidates=(fuzzy_match,),
            )
        return AppResolution("not_found", str(query))

    def _fuzzy_suggestion(self, query: str) -> str | None:
        """The closest installed app name if it's a near-miss, else None."""
        best_name: str | None = None
        best_ratio = 0.0
        for entry in self._unique_app_matches(list(self._entries)):
            for alias in entry.aliases:
                if len(alias) < 4:
                    continue
                ratio = SequenceMatcher(None, query, alias).ratio()
                if ratio > best_ratio:
                    best_ratio, best_name = ratio, entry.display_name
        return best_name if best_ratio >= 0.82 else None

    def get(self, entry_id: str) -> AppEntry | None:
        self._ensure_loaded()
        return next(
            (entry for entry in self._entries if entry.id == str(entry_id)),
            None,
        )

    def launch(self, entry: AppEntry) -> None:
        """Launch one catalog-produced descriptor without a shell or arguments."""
        if entry.launch_kind == "browser":
            if not webbrowser.open_new(entry.launch_value):
                raise OSError("Windows did not accept the browser request.")
            return
        if entry.launch_kind in {"shortcut", "protocol"}:
            if os.name != "nt" or not hasattr(os, "startfile"):
                raise OSError("Windows application launching is unavailable.")
            os.startfile(entry.launch_value)  # type: ignore[attr-defined]
            return
        if entry.launch_kind == "executable":
            executable = Path(entry.launch_value)
            if not executable.is_file():
                raise OSError("The registered application path no longer exists.")
            subprocess.Popen([str(executable)], shell=False)
            return
        if entry.launch_kind == "uwp":
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{entry.launch_value}"],
                shell=False,
            )
            return
        raise PermissionError(f"Unsupported launch kind: {entry.launch_kind}")

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()

    def _discover_windows_apps(self) -> list[AppEntry]:
        entries = [AppEntry.create(
            "Default Browser",
            "browser",
            DEFAULT_BROWSER_START_URL,
            aliases=("browser", "web browser", "default browser"),
            source="Windows default web handler",
        )]
        if os.name != "nt":
            return entries
        entries.extend(self._discover_start_menu_shortcuts())
        entries.extend(self._discover_app_paths())
        entries.extend(self._discover_start_apps())
        entries.extend(self._discover_spotify_protocol())
        return entries

    @staticmethod
    def _discover_start_menu_shortcuts() -> list[AppEntry]:
        roots = {
            Path(os.environ.get("APPDATA", ""))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(os.environ.get("PROGRAMDATA", ""))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        }
        entries = []
        for root in roots:
            if not root.is_dir():
                continue
            for shortcut in root.rglob("*.lnk"):
                name = shortcut.stem.strip()
                if not name or name.casefold().startswith(_NON_APP_PREFIXES):
                    continue
                entries.append(AppEntry.create(
                    name,
                    "shortcut",
                    str(shortcut.resolve()),
                    source="Start Menu",
                ))
        return entries

    @staticmethod
    def _discover_app_paths() -> list[AppEntry]:
        try:
            import winreg
        except ImportError:
            return []

        entries = []
        registry_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                root = winreg.OpenKey(hive, registry_path)
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        key_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, key_name) as app_key:
                            executable = str(winreg.QueryValue(app_key, None))
                    except OSError:
                        continue
                    path = Path(executable)
                    if not path.is_file():
                        continue
                    entries.append(AppEntry.create(
                        path.stem,
                        "executable",
                        str(path.resolve()),
                        aliases=(Path(key_name).stem,),
                        source="Windows App Paths",
                    ))
        return entries

    @staticmethod
    def _discover_start_apps() -> list[AppEntry]:
        # Windows PowerShell 5.1 writes redirected stdout in the console's
        # OEM codepage (cp949 on a Korean-locale system), not UTF-8 --
        # decoding that as UTF-8 silently turned every non-ASCII app name
        # (e.g. Settings' real "설정") into unmatchable U+FFFD replacement
        # characters. Base64-encoding the JSON inside PowerShell first
        # makes the pipe itself pure ASCII, so no codepage survives to be
        # guessed wrong on the Python side.
        command = (
            "$json = Get-StartApps | Select-Object Name,AppID | "
            "ConvertTo-Json -Compress; "
            "[Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))"
        )
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                capture_output=True,
                text=True,
                encoding="ascii",
                errors="replace",
                timeout=12,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode or not result.stdout.strip():
                return []
            payload_json = base64.b64decode(result.stdout.strip()).decode(
                "utf-8", errors="replace"
            )
            payload = json.loads(payload_json)
        except (
            OSError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
            ValueError,  # base64.b64decode on malformed input
        ):
            return []

        items = payload if isinstance(payload, list) else [payload]
        return [
            AppEntry.create(
                str(item["Name"]),
                "uwp",
                str(item["AppID"]),
                source="Windows Start Apps",
            )
            for item in items
            if isinstance(item, dict) and item.get("Name") and item.get("AppID")
        ]

    @staticmethod
    def _discover_spotify_protocol() -> list[AppEntry]:
        # Preserve the proven Phase 1 Spotify URI as a compatibility entry.
        # ShellExecute still owns the real availability check; if Spotify has
        # been removed, launch returns a truthful failed result.
        return [AppEntry.create(
            "Spotify",
            "protocol",
            "spotify:",
            source="Registered URL protocol",
        )]

    def _deduplicate(self, entries: Iterable[AppEntry]) -> tuple[AppEntry, ...]:
        by_descriptor = {}
        for entry in entries:
            if not entry.display_name or not entry.launch_value:
                continue
            key = (entry.launch_kind, entry.launch_value.casefold())
            by_descriptor[key] = entry
        return tuple(sorted(
            by_descriptor.values(),
            key=lambda entry: (
                normalize_app_name(entry.display_name),
                self._PRIORITY.get(entry.launch_kind, 99),
            ),
        ))

    def _unique_app_matches(self, matches: list[AppEntry]) -> list[AppEntry]:
        if not matches:
            return []
        grouped: dict[str, list[AppEntry]] = {}
        for entry in matches:
            grouped.setdefault(normalize_app_name(entry.display_name), []).append(entry)
        selected = []
        for group in grouped.values():
            selected.append(min(
                group,
                key=lambda entry: self._PRIORITY.get(entry.launch_kind, 99),
            ))
        return sorted(selected, key=lambda entry: entry.display_name.casefold())

    def _prefix_suggestions(self, query: str) -> list[str]:
        matches = []
        for entry in self._entries:
            if any(alias.startswith(query) or query.startswith(alias) for alias in entry.aliases):
                matches.append(entry)
        return [
            entry.display_name
            for entry in self._unique_app_matches(matches)[:5]
        ]
