"""Restricted creation and recoverable deletion for Elaina."""

from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import UUID


_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_KNOWN_FOLDER_IDS = {
    "desktop": UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641"),
    "documents": UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7"),
    "downloads": UUID("374DE290-123F-4565-9164-39C4925E467B"),
}


@dataclass(frozen=True)
class FileResolution:
    status: str
    requested_name: str
    location: str
    path: str = ""
    message: str = ""


class SafeFilesystemControl:
    """Create or recycle one item beneath an explicitly allowlisted root."""

    def __init__(
        self,
        allowed_roots: Iterable[str | Path] | None = None,
        *,
        recycler=None,
    ) -> None:
        if isinstance(allowed_roots, (str, Path)):
            configured = (allowed_roots,)
        else:
            configured = tuple(
                allowed_roots or ("Desktop", "Documents", "Downloads")
            )
        self.roots = self._resolve_roots(configured)
        self._recycler = recycler or self._recycle_windows

    def resolve_creation(
        self,
        *,
        name: str,
        location: str,
    ) -> FileResolution:
        requested_name = str(name).strip()
        requested_location = str(location).strip()
        if not requested_name:
            return self._result("invalid_target", requested_name, requested_location,
                                message="A file or folder name is required.")
        if not requested_location:
            return self._result("needs_location", requested_name, requested_location,
                                message="Please name an allowed location.")
        if not self._valid_leaf_name(requested_name):
            return self._result("invalid_target", requested_name, requested_location,
                                message="That file or folder name is not valid.")

        parent = self._resolve_location(requested_location)
        if parent is None:
            return self._result("outside_allowed", requested_name, requested_location,
                                message="That location is outside the allowed folders.")
        if not parent.is_dir():
            return self._result("parent_not_found", requested_name, requested_location,
                                message="The requested parent folder does not exist.")

        destination = (parent / requested_name).resolve(strict=False)
        if not self._within_any_root(destination):
            return self._result("outside_allowed", requested_name, requested_location,
                                message="That location is outside the allowed folders.")
        if destination.exists():
            return self._result("already_exists", requested_name, requested_location,
                                path=str(destination),
                                message=f"{requested_name} already exists.")
        return self._result("resolved", requested_name, requested_location,
                            path=str(destination),
                            message=f"Ready to create {requested_name}.")

    def resolve_deletion(
        self,
        *,
        name: str,
        location: str,
        expected_kind: str,
    ) -> FileResolution:
        requested_name = str(name).strip()
        requested_location = str(location).strip()
        if expected_kind not in {"file", "folder"}:
            return self._result(
                "invalid_target",
                requested_name,
                requested_location,
                message="The deletion type is not supported.",
            )
        if not requested_name:
            return self._result(
                "invalid_target",
                requested_name,
                requested_location,
                message=f"A {expected_kind} name is required.",
            )
        if not requested_location:
            return self._result(
                "needs_location",
                requested_name,
                requested_location,
                message="Please name an allowed location.",
            )
        if not self._valid_leaf_name(requested_name):
            return self._result(
                "invalid_target",
                requested_name,
                requested_location,
                message=f"That {expected_kind} name is not valid.",
            )

        parent = self._resolve_location(requested_location)
        if parent is None:
            return self._result(
                "outside_allowed",
                requested_name,
                requested_location,
                message="That location is outside the allowed folders.",
            )
        if not parent.is_dir():
            return self._result(
                "parent_not_found",
                requested_name,
                requested_location,
                message="The requested parent folder does not exist.",
            )

        unresolved = parent / requested_name
        if not unresolved.exists():
            return self._result(
                "item_not_found",
                requested_name,
                requested_location,
                path=str(unresolved),
                message=f"{requested_name} does not exist there.",
            )
        destination = unresolved.resolve(strict=True)
        if not self._within_any_root(destination):
            return self._result(
                "outside_allowed",
                requested_name,
                requested_location,
                message="That item resolves outside the allowed folders.",
            )
        correct_type = (
            destination.is_file()
            if expected_kind == "file"
            else destination.is_dir()
        )
        if not correct_type:
            return self._result(
                "wrong_type",
                requested_name,
                requested_location,
                path=str(destination),
                message=f"{requested_name} is not a {expected_kind}.",
            )
        return self._result(
            "resolved",
            requested_name,
            requested_location,
            path=str(destination),
            message=f"Ready to recycle {requested_name}.",
        )

    def create_file(self, path: str) -> None:
        destination = self._validated_prepared_path(path)
        with destination.open("x", encoding="utf-8"):
            pass

    def create_folder(self, path: str) -> None:
        destination = self._validated_prepared_path(path)
        destination.mkdir(exist_ok=False)

    def delete_file(self, path: str) -> None:
        self._delete(path, "file")

    def delete_folder(self, path: str) -> None:
        self._delete(path, "folder")

    def _delete(self, path: str, expected_kind: str) -> None:
        destination = self._validated_existing_path(path, expected_kind)
        self._recycler(destination)
        if destination.exists():
            raise OSError("Windows did not remove the item from its original path.")

    def _validated_prepared_path(self, value: str) -> Path:
        destination = Path(value).resolve(strict=False)
        if not self._within_any_root(destination):
            raise PermissionError("Prepared path left the allowed roots.")
        if not destination.parent.is_dir():
            raise FileNotFoundError("The parent folder no longer exists.")
        if destination.exists():
            raise FileExistsError("The destination already exists.")
        return destination

    def _validated_existing_path(self, value: str, expected_kind: str) -> Path:
        destination = Path(value).resolve(strict=True)
        if not self._within_any_root(destination):
            raise PermissionError("Prepared path left the allowed roots.")
        correct_type = (
            destination.is_file()
            if expected_kind == "file"
            else destination.is_dir()
        )
        if not correct_type:
            raise FileNotFoundError(f"The prepared {expected_kind} no longer exists.")
        return destination

    @staticmethod
    def _recycle_windows(path: Path) -> None:
        """Move one exact path to the Windows Recycle Bin without prompts."""
        if os.name != "nt":
            raise OSError("Recycle Bin deletion requires Windows.")

        class FileOperation(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        source = f"{path}\0\0"
        operation = FileOperation()
        operation.wFunc = 0x0003  # FO_DELETE
        operation.pFrom = source
        operation.fFlags = (
            0x0004  # FOF_SILENT
            | 0x0010  # FOF_NOCONFIRMATION
            | 0x0040  # FOF_ALLOWUNDO (Recycle Bin)
            | 0x0400  # FOF_NOERRORUI
        )
        result = ctypes.windll.shell32.SHFileOperationW(  # type: ignore[attr-defined]
            ctypes.byref(operation)
        )
        if result != 0 or operation.fAnyOperationsAborted:
            raise OSError(
                int(result),
                "Windows Recycle Bin operation failed or was cancelled.",
            )

    def _resolve_location(self, value: str) -> Path | None:
        supplied = Path(os.path.expandvars(value)).expanduser()
        if supplied.is_absolute():
            resolved = supplied.resolve(strict=False)
            return resolved if self._within_any_root(resolved) else None

        parts = supplied.parts
        if not parts:
            return None
        root = self.roots.get(self._root_key(parts[0]))
        if root is None:
            return None
        candidate = root.joinpath(*parts[1:]).resolve(strict=False)
        return candidate if self._within(candidate, root) else None

    def _within_any_root(self, path: Path) -> bool:
        return any(self._within(path, root) for root in self.roots.values())

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _valid_leaf_name(name: str) -> bool:
        if name in {".", ".."} or Path(name).name != name:
            return False
        if any(character in name for character in '<>:"/\\|?*\0'):
            return False
        if name.endswith((" ", ".")):
            return False
        stem = name.split(".", 1)[0].casefold()
        return stem not in _WINDOWS_RESERVED_NAMES and len(name) <= 255

    @classmethod
    def _resolve_roots(cls, values: Iterable[str | Path]) -> dict[str, Path]:
        roots: dict[str, Path] = {}
        for value in values:
            text = str(value).strip()
            key = cls._root_key(text)
            if key in _KNOWN_FOLDER_IDS:
                resolved = cls._known_folder(key)
            else:
                resolved = Path(os.path.expandvars(text)).expanduser().resolve(strict=False)
            if resolved.is_dir():
                roots[key] = resolved
        return roots

    @staticmethod
    def _known_folder(name: str) -> Path:
        if os.name != "nt":
            return (Path.home() / name.title()).resolve(strict=False)
        guid = _KNOWN_FOLDER_IDS[name]
        path_pointer = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
            ctypes.byref((ctypes.c_byte * 16).from_buffer_copy(guid.bytes_le)),
            0,
            None,
            ctypes.byref(path_pointer),
        )
        if result != 0:
            return (Path.home() / name.title()).resolve(strict=False)
        try:
            return Path(path_pointer.value).resolve(strict=False)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path_pointer)  # type: ignore[attr-defined]

    @staticmethod
    def _root_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).casefold())

    @staticmethod
    def _result(status: str, name: str, location: str, *, path: str = "",
                message: str = "") -> FileResolution:
        return FileResolution(status, name, location, path, message)
