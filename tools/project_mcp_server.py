from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import subprocess
import textwrap
import time
import uuid
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field


mcp = FastMCP("Elaina Project Tools")

PROJECT_ROOT = Path(
    os.environ["ELAINA_PROJECT_ROOT"]
).resolve()

IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".vscode",
    ".elaina_backups",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "models",
    "dist",
    "build",
    "coverage",
}

BLOCKED_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "secrets.json",
}

TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".scss",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
    ".sql",
    ".java",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".rs",
    ".go",
    ".php",
    ".rb",
    ".swift",
    ".kt",
}

MAX_FILE_BYTES = 1_000_000
MAX_READ_LINES = 300
MAX_SEARCH_RESULTS = 30
MAX_PROPOSAL_CHANGES = 8
MAX_DIFF_CHARACTERS = 16_000
PROPOSAL_TTL_SECONDS = 10 * 60
GIT_PROPOSAL_TTL_SECONDS = 10 * 60

# Created by ChatEngine for this MCP process and never shown to the LLM.
APPROVAL_TOKEN = os.environ.get("ELAINA_APPROVAL_TOKEN", "")

# Proposals live only in this private MCP process and disappear on restart.
PENDING_PROPOSALS: dict[str, dict] = {}
PENDING_GIT_PROPOSALS: dict[str, dict] = {}

GIT_IGNORED_FILE_GLOBS = {
    "*.bin",
    "*.ckpt",
    "*.db",
    "*.faiss",
    "*.gif",
    "*.jpeg",
    "*.jpg",
    "*.mp3",
    "*.onnx",
    "*.png",
    "*.pt",
    "*.pth",
    "*.safetensors",
    "*.sqlite",
    "*.sqlite3",
    "*.wav",
}


class FileChange(BaseModel):
    """One focused file operation shown explicitly in the MCP tool schema."""

    action: Literal[
        "replace",
        "create",
        "insert_after_html_id",
        "remove_html_id",
    ] | None = Field(
        default=None,
        description=(
            "Use replace for exact text, create for a new file, "
            "insert_after_html_id to add UI beside an element, or "
            "remove_html_id to remove a complete HTML element. When omitted, "
            "old_text means replace and no old_text means create."
        ),
    )
    path: str | None = Field(
        default=None,
        description="Project-relative target path, such as desktop/renderer/index.html.",
    )
    file: str | None = Field(
        default=None,
        description="Compatibility alias for path. Prefer path.",
    )
    old_text: str | None = Field(
        default=None,
        description=(
            "Exact existing text to replace. Required for replace and must "
            "appear exactly once."
        ),
    )
    element_id: str | None = Field(
        default=None,
        description=(
            "Existing HTML id used by insert_after_html_id or remove_html_id, "
            "such as screen-button."
        ),
    )
    new_text: str = Field(
        default="",
        description="Complete replacement text or new file content.",
    )


def is_blocked_filename(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in BLOCKED_FILENAMES
        or normalized.startswith(".env.")
        or normalized.endswith((".pem", ".key"))
    )


def safe_path(relative_path: str) -> Path:
    """Resolve a path while preventing access outside the project."""

    candidate = (
        PROJECT_ROOT / relative_path
    ).resolve()

    try:
        candidate.relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise ValueError(
            "Access outside the selected project is not allowed."
        ) from error

    if any(
        part in IGNORED_DIRECTORIES
        for part in candidate.parts
    ):
        raise ValueError(
            "That directory is excluded from project access."
        )

    if is_blocked_filename(candidate.name):
        raise ValueError(
            "That file may contain secrets and cannot be accessed."
        )

    return candidate


def is_allowed_file(path: Path) -> bool:
    if not path.is_file():
        return False

    if is_blocked_filename(path.name):
        return False

    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return False

    if any(
        part in IGNORED_DIRECTORIES
        for part in path.parts
    ):
        return False

    try:
        return path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def is_allowed_write_target(path: Path) -> bool:
    """Validate an existing or new text-file destination."""
    if is_blocked_filename(path.name):
        return False
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return False
    if any(part in IGNORED_DIRECTORIES for part in path.parts):
        return False
    return True


def _clean_expired_proposals() -> None:
    now = time.monotonic()
    for proposal_id, proposal in list(PENDING_PROPOSALS.items()):
        if now > proposal["expires_at"]:
            del PENDING_PROPOSALS[proposal_id]

    for proposal_id, proposal in list(PENDING_GIT_PROPOSALS.items()):
        if now > proposal["expires_at"]:
            del PENDING_GIT_PROPOSALS[proposal_id]


def _require_approval_token(token: str) -> None:
    if not APPROVAL_TOKEN or not hmac.compare_digest(token, APPROVAL_TOKEN):
        raise PermissionError(
            "This action requires approval from the Electron interface."
        )


def _file_hash(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _run_git(
    arguments: list[str],
    timeout: int = 20,
) -> subprocess.CompletedProcess:
    """Run Git without a shell so commit text cannot become a command."""
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_path_allowed(relative_path: str) -> bool:
    """Allow safe project text files while rejecting secrets and generated data."""
    try:
        target = safe_path(relative_path)
    except ValueError:
        return False

    if is_blocked_filename(target.name):
        return False
    if target.name == ".gitignore":
        return not target.is_file() or target.stat().st_size <= MAX_FILE_BYTES
    if target.suffix.lower() not in TEXT_EXTENSIONS:
        return False
    if target.is_file() and target.stat().st_size > MAX_FILE_BYTES:
        return False
    return True


def _git_safe_pathspecs() -> list[str]:
    """Select the project while excluding generated and sensitive locations."""
    pathspecs = ["."]

    for directory in sorted(IGNORED_DIRECTORIES):
        pathspecs.append(
            f":(exclude,glob)**/{directory}/**"
        )

    for pattern in sorted(GIT_IGNORED_FILE_GLOBS):
        pathspecs.append(
            f":(exclude,glob)**/{pattern}"
        )

    for filename in sorted(BLOCKED_FILENAMES):
        pathspecs.append(
            f":(exclude,glob)**/{filename}"
        )

    pathspecs.append(":(exclude,glob)**/.env.*")
    pathspecs.append(":(exclude,glob)**/*.key")
    pathspecs.append(":(exclude,glob)**/*.pem")
    return pathspecs


def _proposal_preview(
    path: str,
    before: str,
    after: str,
    is_new: bool,
) -> str:
    before_lines = [] if is_new else before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="/dev/null" if is_new else f"a/{path}",
        tofile=f"b/{path}",
    ))


def _find_html_element(
    content: str,
    element_id: str,
) -> re.Match:
    """Find one complete paired HTML element by its id attribute."""
    element_id = str(element_id).strip()
    if not element_id:
        raise ValueError("An HTML element_id is required.")

    pattern = re.compile(
        r"<(?P<tag>[A-Za-z][\w:-]*)\b"
        r"(?=[^>]*\bid\s*=\s*[\"']"
        + re.escape(element_id)
        + r"[\"'])[^>]*>"
        r".*?</(?P=tag)\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    matches = list(pattern.finditer(content))

    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one complete HTML element with id "
            f"'{element_id}', but found {len(matches)}."
        )

    return matches[0]


@mcp.tool()
def project_info() -> dict:
    """Return basic information about the active project."""

    files = [
        path
        for path in PROJECT_ROOT.rglob("*")
        if is_allowed_file(path)
    ]

    extension_counts: dict[str, int] = {}

    for path in files:
        extension = path.suffix.lower() or "(none)"

        extension_counts[extension] = (
            extension_counts.get(extension, 0) + 1
        )

    return {
        "name": PROJECT_ROOT.name,
        "path": str(PROJECT_ROOT),
        "text_file_count": len(files),
        "extensions": extension_counts,
    }


@mcp.tool()
def list_files(
    directory: str = ".",
    limit: int = 200,
) -> list[str]:
    """List readable project files beneath a directory."""

    target = safe_path(directory)

    if not target.is_dir():
        raise ValueError("The requested path is not a directory.")

    limit = max(1, min(limit, 500))
    results: list[str] = []

    for path in sorted(target.rglob("*")):
        if is_allowed_file(path):
            results.append(
                path.relative_to(PROJECT_ROOT).as_posix()
            )

        if len(results) >= limit:
            break

    return results


@mcp.tool()
def read_file(
    path: str,
    start_line: int = 1,
    line_count: int = 120,
) -> str:
    """Read a limited range from one text file."""

    target = safe_path(path)

    if not is_allowed_file(target):
        raise ValueError(
            "The requested file is unavailable or unsupported."
        )

    start_line = max(1, start_line)
    line_count = max(
        1,
        min(line_count, MAX_READ_LINES),
    )

    lines = target.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    selected = lines[
        start_line - 1:
        start_line - 1 + line_count
    ]

    return "\n".join(
        f"{number:04d}: {line}"
        for number, line in enumerate(
            selected,
            start=start_line,
        )
    )


@mcp.tool()
def search_code(
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Search for literal text across readable project files."""

    query = query.strip()

    if not query:
        raise ValueError("Search text cannot be empty.")

    limit = max(
        1,
        min(limit, MAX_SEARCH_RESULTS),
    )

    results: list[dict] = []
    normalized_query = re.sub(
        r"[\W_]+",
        " ",
        query.lower(),
    ).strip()

    for path in PROJECT_ROOT.rglob("*"):
        if not is_allowed_file(path):
            continue

        try:
            lines = path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
        except OSError:
            continue

        for line_number, line in enumerate(
            lines,
            start=1,
        ):
            normalized_line = re.sub(
                r"[\W_]+",
                " ",
                line.lower(),
            ).strip()

            if (
                query.lower() not in line.lower()
                and normalized_query not in normalized_line
            ):
                continue

            results.append({
                "path": path.relative_to(
                    PROJECT_ROOT
                ).as_posix(),
                "line": line_number,
                "text": line.strip()[:300],
            })

            if len(results) >= limit:
                return results

    return results


@mcp.tool()
def propose_file_changes(
    summary: str,
    changes: list[FileChange],
) -> str:
    """
    Propose text-file changes for approval in Electron without writing files.

    Use action="replace" with exact old_text and new_text, or action="create"
    with the new file content. Keep replacements small and focused.
    """
    _clean_expired_proposals()

    summary = str(summary).strip()
    if not summary:
        raise ValueError("A short proposal summary is required.")
    if not isinstance(changes, list) or not changes:
        raise ValueError("At least one file change is required.")
    if len(changes) > MAX_PROPOSAL_CHANGES:
        raise ValueError(
            f"A proposal can contain at most {MAX_PROPOSAL_CHANGES} changes."
        )

    # Build final files entirely in memory. The disk stays untouched until the
    # Electron user approves this exact proposal.
    working_files: dict[str, dict] = {}
    ordered_paths: list[str] = []
    stored_operations: list[dict] = []

    for change_model in changes:
        if hasattr(change_model, "model_dump"):
            raw_change = change_model.model_dump()
        elif hasattr(change_model, "dict"):
            raw_change = change_model.dict()
        elif isinstance(change_model, dict):
            raw_change = change_model
        else:
            raise ValueError("Every change must be an object.")

        relative_path = str(
            raw_change.get("path")
            or raw_change.get("file")
            or ""
        ).strip()
        old_text = raw_change.get("old_text")
        element_id = raw_change.get("element_id")
        action = str(raw_change.get("action") or "").strip().lower()
        if not action:
            action = "replace" if old_text is not None else "create"
        new_text = raw_change.get("new_text")

        if action not in {
            "replace",
            "create",
            "insert_after_html_id",
            "remove_html_id",
        }:
            raise ValueError(
                "Unsupported action. Use replace, create, "
                "insert_after_html_id, or remove_html_id."
            )
        if not relative_path:
            raise ValueError("Every change requires a relative path.")
        if not isinstance(new_text, str):
            raise ValueError("new_text must be a string.")

        target = safe_path(relative_path)
        normalized_path = target.relative_to(PROJECT_ROOT).as_posix()

        if not is_allowed_write_target(target):
            raise ValueError(
                f"Writing this file is not allowed: {normalized_path}"
            )

        if normalized_path not in working_files:
            if target.exists():
                if not is_allowed_file(target):
                    raise ValueError(
                        f"The file is unavailable or too large: {normalized_path}"
                    )
                original = target.read_text(
                    encoding="utf-8",
                    errors="strict",
                )
            else:
                original = None

            working_files[normalized_path] = {
                "original": original,
                "content": original,
            }
            ordered_paths.append(normalized_path)

        file_state = working_files[normalized_path]

        if action == "create":
            if file_state["content"] is not None:
                raise ValueError(
                    f"Create cannot overwrite an existing file: {normalized_path}"
                )
            file_state["content"] = new_text
        elif action == "replace":
            if file_state["content"] is None:
                raise ValueError(
                    f"Replace requires an existing file: {normalized_path}"
                )

            if not isinstance(old_text, str) or not old_text:
                raise ValueError(
                    "A replace change requires non-empty exact old_text."
                )

            # Removing only an opening HTML button tag leaves its label and
            # closing tag visible. Require the complete element instead.
            old_text_lower = old_text.lower()
            new_text_lower = new_text.lower()
            if (
                target.suffix.lower() == ".html"
                and "<button" in old_text_lower
                and "</button>" not in old_text_lower
                and "<button" not in new_text_lower
            ):
                raise ValueError(
                    "A button removal must include the complete HTML element, "
                    "from the opening <button> through </button>, in old_text. "
                    "Read the surrounding source and try again."
                )

            occurrences = file_state["content"].count(old_text)
            if occurrences != 1:
                raise ValueError(
                    f"old_text must appear exactly once in {normalized_path}; "
                    f"found {occurrences} matches. For HTML controls, use "
                    "insert_after_html_id or remove_html_id so formatting does "
                    "not need to match exactly."
                )

            file_state["content"] = file_state["content"].replace(
                old_text,
                new_text,
                1,
            )
        elif action == "insert_after_html_id":
            if file_state["content"] is None:
                raise ValueError(
                    f"HTML insertion requires an existing file: {normalized_path}"
                )
            if target.suffix.lower() not in {".html", ".htm"}:
                raise ValueError(
                    "insert_after_html_id can only modify an HTML file."
                )
            if not isinstance(new_text, str) or not new_text.strip():
                raise ValueError(
                    "HTML insertion requires non-empty new_text."
                )

            match = _find_html_element(
                file_state["content"],
                element_id,
            )
            line_start = file_state["content"].rfind(
                "\n",
                0,
                match.start(),
            ) + 1
            indentation = file_state["content"][
                line_start:match.start()
            ]
            if indentation.strip():
                indentation = ""

            inserted_html = textwrap.indent(
                new_text.strip(),
                indentation,
            )
            file_state["content"] = (
                file_state["content"][:match.end()]
                + "\n\n"
                + inserted_html
                + file_state["content"][match.end():]
            )
        else:
            if file_state["content"] is None:
                raise ValueError(
                    f"HTML removal requires an existing file: {normalized_path}"
                )
            if target.suffix.lower() not in {".html", ".htm"}:
                raise ValueError(
                    "remove_html_id can only modify an HTML file."
                )

            match = _find_html_element(
                file_state["content"],
                element_id,
            )
            removal_start = match.start()
            removal_end = match.end()
            line_start = file_state["content"].rfind(
                "\n",
                0,
                removal_start,
            ) + 1

            if not file_state["content"][line_start:removal_start].strip():
                removal_start = line_start
                if (
                    removal_end < len(file_state["content"])
                    and file_state["content"][removal_end] == "\n"
                ):
                    removal_end += 1

            file_state["content"] = (
                file_state["content"][:removal_start]
                + file_state["content"][removal_end:]
            )

        if len(file_state["content"].encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(
                f"The proposed file is too large: {normalized_path}"
            )

        stored_operations.append({
            "action": action,
            "path": normalized_path,
            "old_text": old_text,
            "element_id": element_id,
            "new_text": new_text,
        })

    proposal_id = uuid.uuid4().hex
    stored_files: list[dict] = []
    previews: list[str] = []

    for relative_path in ordered_paths:
        file_state = working_files[relative_path]
        original = file_state["original"]
        final_content = file_state["content"]
        is_new = original is None

        stored_files.append({
            "path": relative_path,
            "original_hash": _file_hash(original),
            "original": original,
            "content": final_content,
            "is_new": is_new,
        })
        previews.append(_proposal_preview(
            relative_path,
            original or "",
            final_content,
            is_new,
        ))

    full_preview = "\n".join(previews)
    now = time.monotonic()
    PENDING_PROPOSALS[proposal_id] = {
        "summary": summary[:500],
        "files": stored_files,
        "operations": stored_operations,
        "expires_at": now + PROPOSAL_TTL_SECONDS,
        "status": "pending",
    }

    return json.dumps({
        "status": "awaiting_approval",
        "proposal_id": proposal_id,
        "summary": summary[:500],
        "files": ordered_paths,
        "editable_changes": stored_operations,
        "diff": full_preview[:MAX_DIFF_CHARACTERS],
        "diff_truncated": len(full_preview) > MAX_DIFF_CHARACTERS,
        "expires_in_seconds": PROPOSAL_TTL_SECONDS,
    }, ensure_ascii=False)


@mcp.tool()
def revise_project_proposal(
    proposal_id: str,
    approval_token: str,
    revised_texts: list[str],
) -> str:
    """
    Rebuild a pending proposal using user-edited replacement text.

    Paths, actions, and original matching text remain locked to the reviewed
    proposal. Only each operation's new_text can be changed by Electron.
    """
    _require_approval_token(approval_token)
    _clean_expired_proposals()

    proposal = PENDING_PROPOSALS.get(proposal_id)
    if proposal is None:
        raise ValueError("The proposal is missing or has expired.")
    if proposal["status"] != "pending":
        raise ValueError("The proposal has already been resolved.")
    if len(revised_texts) != len(proposal["operations"]):
        raise ValueError(
            "The number of edited code blocks does not match the proposal."
        )
    if not all(isinstance(text, str) for text in revised_texts):
        raise ValueError("Every edited code block must be text.")

    revised_changes = []
    for operation, revised_text in zip(
        proposal["operations"],
        revised_texts,
        strict=True,
    ):
        revised_changes.append(FileChange(
            action=operation["action"],
            path=operation["path"],
            old_text=operation["old_text"],
            element_id=operation.get("element_id"),
            new_text=revised_text,
        ))

    revised_result = json.loads(propose_file_changes(
        proposal["summary"],
        revised_changes,
    ))
    proposal["status"] = "superseded"
    revised_result["revised_from"] = proposal_id

    return json.dumps(revised_result, ensure_ascii=False)


@mcp.tool()
def apply_project_proposal(
    proposal_id: str,
    approval_token: str,
) -> str:
    """Apply one exact proposal after authenticated Electron approval."""
    _require_approval_token(approval_token)
    _clean_expired_proposals()

    proposal = PENDING_PROPOSALS.get(proposal_id)
    if proposal is None:
        raise ValueError("The proposal is missing or has expired.")
    if proposal["status"] != "pending":
        raise ValueError("The proposal has already been resolved.")

    # Refuse stale edits if a reviewed file changed before approval.
    for file_change in proposal["files"]:
        target = safe_path(file_change["path"])

        if file_change["is_new"]:
            if target.exists():
                raise ValueError(
                    f"{file_change['path']} now exists. Nothing was changed."
                )
            continue

        if not target.is_file():
            raise ValueError(
                f"{file_change['path']} no longer exists. Nothing was changed."
            )

        current = target.read_text(encoding="utf-8", errors="strict")
        if _file_hash(current) != file_change["original_hash"]:
            raise ValueError(
                f"{file_change['path']} changed after review. "
                "Nothing was changed."
            )

    backup_root = PROJECT_ROOT / ".elaina_backups" / proposal_id

    try:
        for file_change in proposal["files"]:
            target = safe_path(file_change["path"])
            original = file_change["original"]

            if original is not None:
                backup = backup_root / file_change["path"]
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(original, encoding="utf-8")

            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.elaina-{proposal_id}.tmp"
            )
            temporary.write_text(
                file_change["content"],
                encoding="utf-8",
            )
            os.replace(temporary, target)
    except Exception:
        # Restore all originals if any write fails.
        for file_change in proposal["files"]:
            target = safe_path(file_change["path"])
            original = file_change["original"]
            try:
                if original is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(original, encoding="utf-8")
            except OSError:
                pass
        raise

    proposal["status"] = "applied"

    return json.dumps({
        "status": "applied",
        "proposal_id": proposal_id,
        "summary": proposal["summary"],
        "files": [
            file_change["path"]
            for file_change in proposal["files"]
        ],
        "backup_directory": backup_root.relative_to(
            PROJECT_ROOT
        ).as_posix(),
    }, ensure_ascii=False)


@mcp.tool()
def reject_project_proposal(
    proposal_id: str,
    approval_token: str,
) -> str:
    """Reject a pending proposal without writing project files."""
    _require_approval_token(approval_token)
    _clean_expired_proposals()

    proposal = PENDING_PROPOSALS.get(proposal_id)
    if proposal is None:
        raise ValueError("The proposal is missing or has expired.")
    if proposal["status"] != "pending":
        raise ValueError("The proposal has already been resolved.")

    proposal["status"] = "rejected"

    return json.dumps({
        "status": "rejected",
        "proposal_id": proposal_id,
        "summary": proposal["summary"],
        "files": [
            file_change["path"]
            for file_change in proposal["files"]
        ],
    }, ensure_ascii=False)


@mcp.tool()
def prepare_git_proposal(
    commit_message: str = "",
) -> str:
    """
    Prepare a reviewable commit-and-push proposal without changing Git state.

    Fast mode reviews the commands, branch, remote, and editable commit message.
    It intentionally avoids a full working-tree scan before approval.
    """
    _clean_expired_proposals()
    branch_result = _run_git(["branch", "--show-current"])
    branch = branch_result.stdout.strip()
    if branch_result.returncode != 0 or not branch:
        raise ValueError(
            "Git actions are disabled while the repository is detached."
        )

    remote_result = _run_git(["remote"])
    remotes = [
        line.strip()
        for line in remote_result.stdout.splitlines()
        if line.strip()
    ]

    upstream_result = _run_git([
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
    ])
    upstream = (
        upstream_result.stdout.strip()
        if upstream_result.returncode == 0
        else ""
    )
    remote = (
        upstream.split("/", 1)[0]
        if "/" in upstream
        else ("origin" if "origin" in remotes else "")
    )

    message = str(commit_message).strip() or "Update Elaina project files"
    message = " ".join(message.splitlines()).strip()[:200]

    proposal_id = uuid.uuid4().hex
    now = time.monotonic()
    PENDING_GIT_PROPOSALS[proposal_id] = {
        "branch": branch,
        "remote": remote,
        "upstream": upstream,
        "expires_at": now + GIT_PROPOSAL_TTL_SECONDS,
        "status": "pending",
    }

    return json.dumps({
        "status": "awaiting_git_approval",
        "proposal_id": proposal_id,
        "branch": branch,
        "remote": remote,
        "upstream": upstream,
        "push_available": bool(remote or upstream),
        "commit_message": message,
        "files": [{
            "status": "--",
            "path": "All non-protected project changes",
        }],
        "diff_stat": "Fast command mode",
        "diff": (
            "git add -A -- .  (protected paths excluded)\n"
            f'git commit -m "{message}"\n'
            "git push"
        ),
        "diff_truncated": False,
        "expires_in_seconds": GIT_PROPOSAL_TTL_SECONDS,
    }, ensure_ascii=False)


@mcp.tool()
def execute_git_proposal(
    proposal_id: str,
    approval_token: str,
    commit_message: str,
    push: bool = True,
) -> str:
    """Stage, commit, and optionally push one Electron-approved Git proposal."""
    _require_approval_token(approval_token)
    _clean_expired_proposals()

    proposal = PENDING_GIT_PROPOSALS.get(proposal_id)
    if proposal is None:
        raise ValueError("The Git proposal is missing or has expired.")
    if proposal["status"] != "pending":
        raise ValueError("The Git proposal has already been resolved.")

    message = str(commit_message).strip()
    if not message or len(message) > 200 or "\n" in message or "\r" in message:
        raise ValueError(
            "The commit message must be one line between 1 and 200 characters."
        )

    branch_result = _run_git(["branch", "--show-current"])
    if branch_result.stdout.strip() != proposal["branch"]:
        raise ValueError(
            "The active branch changed after review. Nothing was staged."
        )

    add_result = _run_git([
        "add",
        "-A",
        "--",
        *_git_safe_pathspecs(),
    ], timeout=60)
    if add_result.returncode != 0:
        raise RuntimeError(
            add_result.stderr.strip() or "Git staging failed."
        )

    staged_result = _run_git(["diff", "--cached", "--name-only"])
    staged_paths = {
        line.strip().replace("\\", "/")
        for line in staged_result.stdout.splitlines()
        if line.strip()
    }

    protected_staged = {
        path
        for path in staged_paths
        if (
            any(
                part in IGNORED_DIRECTORIES
                for part in Path(path).parts
            )
            or not _git_path_allowed(path)
        )
    }
    if protected_staged:
        raise RuntimeError(
            "Protected files were already staged and the commit was stopped: "
            + ", ".join(sorted(protected_staged))
        )
    if not staged_paths:
        raise RuntimeError("There are no staged changes to commit.")

    paths = sorted(staged_paths)
    commit_result = _run_git(["commit", "-m", message], timeout=60)
    if commit_result.returncode != 0:
        raise RuntimeError(
            commit_result.stderr.strip()
            or commit_result.stdout.strip()
            or "Git commit failed."
        )

    commit_hash_result = _run_git(["rev-parse", "--short", "HEAD"])
    commit_hash = commit_hash_result.stdout.strip()
    proposal["status"] = "committed"

    if not push:
        return json.dumps({
            "status": "committed",
            "proposal_id": proposal_id,
            "commit": commit_hash,
            "branch": proposal["branch"],
            "files": paths,
            "message": message,
        }, ensure_ascii=False)

    if proposal["upstream"]:
        push_result = _run_git(["push", "--porcelain"], timeout=120)
    elif proposal["remote"]:
        push_result = _run_git([
            "push",
            "--porcelain",
            "--set-upstream",
            proposal["remote"],
            proposal["branch"],
        ], timeout=120)
    else:
        return json.dumps({
            "status": "commit_created_push_failed",
            "proposal_id": proposal_id,
            "commit": commit_hash,
            "branch": proposal["branch"],
            "files": paths,
            "message": message,
            "error": "No Git remote is configured.",
        }, ensure_ascii=False)

    if push_result.returncode != 0:
        return json.dumps({
            "status": "commit_created_push_failed",
            "proposal_id": proposal_id,
            "commit": commit_hash,
            "branch": proposal["branch"],
            "remote": proposal["remote"],
            "files": paths,
            "message": message,
            "error": (
                push_result.stderr.strip()
                or push_result.stdout.strip()
                or "Git push failed."
            ),
        }, ensure_ascii=False)

    proposal["status"] = "pushed"
    return json.dumps({
        "status": "pushed",
        "proposal_id": proposal_id,
        "commit": commit_hash,
        "branch": proposal["branch"],
        "remote": proposal["remote"],
        "files": paths,
        "message": message,
    }, ensure_ascii=False)


@mcp.tool()
def reject_git_proposal(
    proposal_id: str,
    approval_token: str,
) -> str:
    """Reject a Git proposal without staging, committing, or pushing."""
    _require_approval_token(approval_token)
    _clean_expired_proposals()

    proposal = PENDING_GIT_PROPOSALS.get(proposal_id)
    if proposal is None:
        raise ValueError("The Git proposal is missing or has expired.")
    if proposal["status"] != "pending":
        raise ValueError("The Git proposal has already been resolved.")

    proposal["status"] = "rejected"
    return json.dumps({
        "status": "rejected",
        "proposal_id": proposal_id,
    })


@mcp.tool()
def git_status() -> str:
    """Return concise Git status without changing the repository."""

    result = subprocess.run(
        [
            "git",
            "status",
            "--short",
            "--branch",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    if result.returncode != 0:
        return "This project is not a Git repository."

    return result.stdout.strip() or "Working tree is clean."


if __name__ == "__main__":
    # STDIO keeps this server private to the process that launched it.
    mcp.run(transport="stdio")
