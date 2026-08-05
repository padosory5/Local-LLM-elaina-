"""Bidirectional English/Korean names for common Windows system apps.

A window's real title and a catalog app's real registered display name are
both whatever the OS display language produced -- on a Korean-locale
system that is "메모장", not "Notepad". The user (or the router/planner)
may still name an app in either language, so both windows_ui_observer.py
(matching a live window's title) and windows_app_catalog.py (resolving an
app to launch) fall back to this same table when a literal match finds
nothing. This is a best-effort list for common built-in utilities, not a
general translator -- anything outside it still needs the app's real name.
"""

from __future__ import annotations

_APP_NAME_GROUPS: tuple[tuple[str, ...], ...] = (
    ("notepad", "메모장"),
    ("settings", "설정"),
    ("file explorer", "explorer", "파일 탐색기", "탐색기"),
    ("calculator", "계산기"),
    ("control panel", "제어판"),
    ("task manager", "작업 관리자"),
    ("paint", "그림판"),
    ("command prompt", "명령 프롬프트"),
    ("powershell", "파워셸"),
    ("recycle bin", "휴지통"),
    ("this pc", "내 pc", "내 컴퓨터"),
)


def _build_alias_index(
    groups: tuple[tuple[str, ...], ...],
) -> dict[str, tuple[str, ...]]:
    index: dict[str, tuple[str, ...]] = {}
    for group in groups:
        folded = tuple(name.casefold() for name in group)
        for name in folded:
            others = tuple(n for n in folded if n != name)
            index[name] = index.get(name, ()) + others
    return index


_APP_NAME_ALIASES = _build_alias_index(_APP_NAME_GROUPS)


def alias_candidates(query: str) -> tuple[str, ...]:
    """Other names this query's app is commonly known by, if any.

    ``query`` should already be casefolded (callers match case-insensitively
    against their own real-world names anyway).
    """
    candidates: list[str] = []
    for key, others in _APP_NAME_ALIASES.items():
        if key in query or query in key:
            for other in others:
                if other not in candidates:
                    candidates.append(other)
    return tuple(candidates)
