"""Conservative Windows process discovery and app closing primitives."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ctypes import wintypes

from tools.windows_app_catalog import AppEntry, normalize_app_name


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    title: str = ""
    path: str = ""


@dataclass(frozen=True)
class ProcessResolution:
    status: str
    processes: tuple[ProcessInfo, ...] = ()
    message: str = ""


class WindowsProcessControl:
    """Match processes to a catalog entry before closing anything."""

    def __init__(self, *, runner=None, sleep=None) -> None:
        self._runner = runner or subprocess.run
        self._sleep = sleep or time.sleep
        self.last_error = ""

    def resolve(self, entry: AppEntry) -> ProcessResolution:
        if os.name != "nt":
            return ProcessResolution("failed", message="Process control requires Windows.")
        try:
            processes = self._list_processes()
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            return ProcessResolution("failed", message=str(error))

        hints = set(entry.aliases)
        hints.add(normalize_app_name(entry.display_name))
        executable_hint = self._executable_hint(entry)
        if executable_hint:
            hints.add(normalize_app_name(executable_hint))
        hints.discard("")

        matches = []
        for process in processes:
            process_name = normalize_app_name(process.name)
            title = normalize_app_name(process.title)
            exact_name = process_name in hints
            titled = any(len(hint) >= 4 and hint in title for hint in hints)
            if exact_name or titled:
                matches.append(process)

        # Once an app-owned visible window establishes a process executable,
        # include its same-name helper processes for a complete force quit.
        generic_hosts = {"applicationframehost", "explorer", "runtimebroker"}
        owned_names = {
            normalize_app_name(process.name)
            for process in matches
            if process.title
            and normalize_app_name(process.name) not in generic_hosts
        }
        if owned_names:
            matched_ids = {process.pid for process in matches}
            matches.extend(
                process
                for process in processes
                if process.pid not in matched_ids
                and normalize_app_name(process.name) in owned_names
            )

        if not matches:
            return ProcessResolution(
                "not_running",
                message=f"{entry.display_name} does not appear to be running.",
            )
        return ProcessResolution("resolved", tuple(matches))

    def close(self, processes: Iterable[ProcessInfo]) -> str:
        """Request a normal close through each app-owned top-level window."""
        self.last_error = ""
        items = tuple(processes)
        window_ids = {item.pid for item in items if item.title}
        if not window_ids:
            self.last_error = "No app-owned top-level window was found."
            return "failed"
        try:
            sent = self._post_close_messages(window_ids)
        except OSError as error:
            self.last_error = str(error)
            return "failed"
        if sent == 0:
            if self._wait_until_stopped(items, 0.3):
                return "closed"
            self.last_error = "Windows did not accept a close request."
            return "failed"
        return "closed" if self._wait_until_stopped(items, 3.0) else "close_requested"

    def force_quit(self, processes: Iterable[ProcessInfo]) -> str:
        """Terminate only handles whose executable still matches discovery."""
        self.last_error = ""
        items = tuple(processes)
        if not items:
            return "not_running"
        errors = []
        for process in items:
            status, error = self._terminate_verified_process(process)
            if status == "failed" and error:
                errors.append(error)

        # The desired outcome owns the final status. A helper process may exit
        # between discovery and termination without turning success into failure.
        if self._wait_until_stopped(items, 3.0):
            return "force_quit"
        self.last_error = "; ".join(errors) or "One or more processes are still running."
        return "failed"

    @staticmethod
    def _post_close_messages(process_ids: set[int]) -> int:
        if os.name != "nt":
            raise OSError("Graceful window closing requires Windows.")

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL

        sent = 0
        wm_close = 0x0010

        def visit(window, _parameter):
            nonlocal sent
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
            if process_id.value in process_ids:
                if user32.PostMessageW(window, wm_close, 0, 0):
                    sent += 1
            return True

        callback = callback_type(visit)
        ctypes.set_last_error(0)
        if not user32.EnumWindows(callback, 0):
            error = ctypes.get_last_error()
            if error:
                raise OSError(error, "Windows window enumeration failed.")
        return sent

    @staticmethod
    def _terminate_verified_process(process: ProcessInfo) -> tuple[str, str]:
        if os.name != "nt":
            return "failed", "Process termination requires Windows."

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_terminate = 0x0001
        process_query_limited_information = 0x1000
        access = process_terminate | process_query_limited_information
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        handle = kernel32.OpenProcess(access, False, int(process.pid))
        if not handle:
            error = ctypes.get_last_error()
            if error in {0, 87, 1168}:
                return "not_running", ""
            return "failed", f"PID {process.pid}: {ctypes.WinError(error)}"

        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            ctypes.set_last_error(0)
            if not kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            ):
                error = ctypes.get_last_error()
                return (
                    "failed",
                    f"PID {process.pid}: could not verify executable: "
                    f"{ctypes.WinError(error)}",
                )

            current_path = Path(buffer.value)
            expected_name = normalize_app_name(process.name)
            current_name = normalize_app_name(current_path.stem)
            if not expected_name or current_name != expected_name:
                return (
                    "failed",
                    f"PID {process.pid}: executable identity changed.",
                )
            if process.path and os.path.normcase(os.path.abspath(process.path)) != (
                os.path.normcase(os.path.abspath(str(current_path)))
            ):
                return (
                    "failed",
                    f"PID {process.pid}: executable path changed.",
                )

            ctypes.set_last_error(0)
            if not kernel32.TerminateProcess(handle, 1):
                error = ctypes.get_last_error()
                return "failed", f"PID {process.pid}: {ctypes.WinError(error)}"
            return "terminated", ""
        finally:
            kernel32.CloseHandle(handle)

    def _list_processes(self) -> tuple[ProcessInfo, ...]:
        script = (
            "Get-Process | Select-Object Id,ProcessName,MainWindowTitle,Path | "
            "ConvertTo-Json -Compress"
        )
        result = self._runner(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise OSError("Windows process discovery failed.")
        payload = json.loads(result.stdout)
        records = payload if isinstance(payload, list) else [payload]
        return tuple(
            ProcessInfo(
                pid=int(record["Id"]),
                name=str(record.get("ProcessName") or ""),
                title=str(record.get("MainWindowTitle") or ""),
                path=str(record.get("Path") or ""),
            )
            for record in records
            if isinstance(record, dict) and record.get("Id") and record.get("ProcessName")
        )

    def _wait_until_stopped(self, processes: tuple[ProcessInfo, ...], timeout: float) -> bool:
        expected = {item.pid for item in processes}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._sleep(0.15)
            try:
                active = {item.pid for item in self._list_processes()}
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                return False
            if not expected.intersection(active):
                return True
        return False

    @staticmethod
    def _executable_hint(entry: AppEntry) -> str:
        if entry.launch_kind == "executable":
            return Path(entry.launch_value).stem
        return ""
