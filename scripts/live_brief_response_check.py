"""Check live short-response variety without executing any action tools."""

from __future__ import annotations

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ollama  # noqa: E402

from brain.brief_response import BriefResponseGenerator  # noqa: E402
from config.loader import Config  # noqa: E402
from scripts.console_style import status_label  # noqa: E402


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def main() -> int:
    config = Config()
    model = str(config.get("llm", "ollama", "model"))
    keep_alive = config.get(
        "llm", "ollama", "keep_alive", default=-1, required=False
    )
    client = ollama.Client(host=config.get("llm", "ollama", "base_url"))
    generator = BriefResponseGenerator(
        client,
        model,
        keep_alive=keep_alive,
    )
    cases = (
        ("agent start one", "work_started", "web search", "Search starting", ""),
        ("agent start two", "work_started", "screen analysis", "Vision starting", ""),
        ("agent start three", "work_started", "project review", "Review starting", ""),
        ("open Discord", "opened", "Discord", "Opened Discord", "open_app"),
        ("open Steam", "opened", "Steam", "Opened Steam", "open_app"),
        ("open Battle.net", "opened", "Battle.net", "Opened Battle.net", "open_app"),
        ("website consent", "action_offer", "github.com", "Open github.com", "open_url"),
        ("website opened", "url_opened", "github.com", "Opened new tab", "open_url"),
        ("folder created", "folder_created", "Notes", "Created folder", "create_folder"),
        ("delete consent", "delete_offer", "Notes", "Recycle folder", "delete_folder"),
        ("folder deleted", "folder_deleted", "Notes", "Moved to Recycle Bin", "delete_folder"),
        ("missing app", "not_found", "Missing App", "App was not found", "open_app"),
    )
    replies = []
    failures = 0
    print(f"Testing {model} with {len(cases)} brief-response cases.\n")
    for name, kind, subject, detail, operation in cases:
        reply = generator.generate(
            kind,
            subject=subject,
            detail=detail,
            operation=operation,
        )
        key = normalized(reply)
        short = 0 < len(reply.split()) <= generator.MAX_WORDS
        unique = key not in {normalized(previous) for previous in replies}
        generic = any(
            phrase in reply.casefold()
            for phrase in ("anything else", "let me know", "need help")
        )
        truthful = True
        if kind == "not_found":
            truthful = any(
                phrase in reply.casefold()
                for phrase in ("can't", "couldn't", "isn't", "not found")
            )
        if kind == "action_offer":
            truthful = "?" in reply and (
                "take over" in reply.casefold()
                or "takeover" in reply.casefold()
            )
        if kind == "delete_offer":
            truthful = "?" in reply and any(
                word in reply.casefold()
                for word in ("delete", "recycle", "trash", "remove")
            )
        if kind == "work_started":
            truthful = "?" not in reply and not any(
                word in reply.casefold()
                for word in ("done", "finished", "confirmation")
            )
        passed = short and unique and not generic and truthful
        failures += 0 if passed else 1
        replies.append(reply)
        print(f"[{status_label(passed)}] {name}: {reply}")
    print(f"\nResult: {len(cases) - failures}/{len(cases)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
