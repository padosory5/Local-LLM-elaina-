"""Check live short-response variety without executing any action tools.

Only the outcome-reporting kinds are model-generated and belong here.
Status lines while work runs moved to ``brain.action_status``, which
chooses locally and is covered by ``tests/test_action_status.py`` -- there
is no model to check.
"""

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
        ("app closed", "closed", "Discord", "Closed Discord", "close_app"),
        ("file created", "file_created", "notes.txt", "Created file", "create_file"),
        ("app not running", "not_running", "Steam", "Not running", "close_app"),
        ("open Discord", "opened", "Discord", "Opened Discord", "open_app"),
        ("open Steam", "opened", "Steam", "Opened Steam", "open_app"),
        ("open Battle.net", "opened", "Battle.net", "Opened Battle.net", "open_app"),
        ("control mode off", "control_mode_off", "github.com", "Mode is off", "open_url"),
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
            # Ask the class itself rather than keeping a shorter copy:
            # this list once omitted "missing" and failed one of the
            # module's own not_found lines.
            truthful = generator.reads_as_negative(reply)
        if kind == "control_mode_off":
            truthful = "computer control" in reply.casefold() and any(
                phrase in reply.casefold()
                for phrase in ("enable", "turn on", "switch on")
            )
        if kind == "delete_offer":
            truthful = "?" in reply and any(
                word in reply.casefold()
                for word in ("delete", "recycle", "trash", "remove")
            )
        if kind == "not_running":
            truthful = (
                generator.reads_as_negative(reply)
                or "already closed" in reply.casefold()
            )
        passed = short and unique and not generic and truthful
        failures += 0 if passed else 1
        replies.append(reply)
        print(f"[{status_label(passed)}] {name}: {reply}")
    print(f"\nResult: {len(cases) - failures}/{len(cases)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
