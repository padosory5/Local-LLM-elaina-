"""Who owns what, and what has to be undone if starting fails halfway.

Startup used to run as a straight line of module-level statements: build the
engine, start the WebSocket server, launch Electron, build speech-to-text,
print "Elaina is ready." The ``try/finally`` that cleans everything up begins
*after* that line, so a failure anywhere in it -- no microphone, no Ollama, a
missing Node install -- exited on an unhandled traceback with the WebSocket
thread still holding port 8765, an Electron window still open, and the
engine's browser service and MCP subprocess still running. The next launch
then met a port collision and a second window.

That is the whole problem this module solves, and it solves it with one rule:

    a subsystem's cleanup is registered the instant it starts, and never
    before.

So whatever came up gets taken back down, in reverse order, no matter where
the failure landed. Nothing is registered speculatively, so nothing is
"cleaned up" that never existed.

Two other properties matter as much:

**Required versus optional is stated, not implied.** A missing microphone
should degrade -- text mode still works. A missing Ollama should abort --
there is nothing to be without it. Which is which was previously encoded only
in whether a constructor happened to raise.

**Cleanup is best-effort and complete.** One handler raising must not skip the
handlers after it; a stuck browser service must not leave the microphone open.
Every failure during shutdown is caught, reported, and the unwind continues.

Nothing here kills by process name. Elaina owns exactly the handles she
opened and the children she spawned, and this module only ever calls the
cleanup that was handed to it alongside them.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Subsystem:
    """One thing that was started, and how to take it back down."""

    name: str
    required: bool = True
    cleanup: Callable[[], Any] | None = None
    started_at: float = 0.0


@dataclass
class Lifecycle:
    """Start subsystems in order; unwind exactly what came up.

    ``log`` is injected so tests can read what was reported rather than
    scraping stdout, and so the console wording lives in one place.
    """

    log: Callable[[str], None] = print
    clock: Callable[[], float] = time.monotonic

    started: list[Subsystem] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    failed_required: str = ""
    shutdown_reason: str = ""
    cleaned: list[str] = field(default_factory=list)
    cleanup_errors: list[str] = field(default_factory=list)
    _shut_down: bool = False

    # ------------------------------------------------------------- startup

    def start(
        self,
        name: str,
        factory: Callable[[], Any],
        *,
        required: bool = True,
        cleanup: Callable[[Any], Any] | None = None,
    ) -> Any:
        """Bring one subsystem up, or record why it did not come up.

        Returns whatever ``factory`` produced, or ``None`` when an optional
        subsystem failed. A required failure returns ``None`` too and sets
        :attr:`failed_required`; the caller checks :meth:`ready` rather than
        catching, so the unwind happens in one place.
        """
        if self.failed_required:
            # Something required has already failed. Starting more would be
            # building on a floor that is coming out.
            return None
        try:
            value = factory()
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            if required:
                self.failed_required = f"{name} ({detail})"
                self.log(f"[Lifecycle] {name} FAILED, and is required: {detail}")
            else:
                self.degraded.append(name)
                self.log(
                    f"[Lifecycle] {name} unavailable, continuing without it: "
                    f"{detail}"
                )
            return None

        self.started.append(Subsystem(
            name=name,
            required=required,
            # Bound to the value that actually exists, so a cleanup can never
            # run against something that was never built.
            cleanup=(lambda: cleanup(value)) if cleanup is not None else None,
            started_at=self.clock(),
        ))
        self.log(f"[Lifecycle] {name} ready.")
        return value

    def ready(self) -> bool:
        """Whether every required subsystem came up."""
        return not self.failed_required

    def report_ready(self) -> str:
        """The one line that says the state, printed only once it is true."""
        if not self.ready():
            line = f"[Lifecycle] NOT READY -- {self.failed_required}"
        elif self.degraded:
            line = (
                "[Lifecycle] READY (degraded: "
                + ", ".join(self.degraded) + ")"
            )
        else:
            line = "[Lifecycle] READY"
        self.log(line)
        return line

    # ------------------------------------------------------------ shutdown

    def shutdown(self, reason: str = "requested") -> None:
        """Undo exactly what was started, newest first.

        Idempotent: a shutdown triggered from Electron and then again from
        the backend's own ``finally`` must not run the handlers twice.
        """
        if self._shut_down:
            return
        self._shut_down = True
        self.shutdown_reason = reason
        self.log(f"[Lifecycle] Shutting down: {reason}")

        for subsystem in reversed(self.started):
            if subsystem.cleanup is None:
                continue
            try:
                subsystem.cleanup()
            except Exception as error:
                # One handler failing must not skip the ones after it: a
                # stuck browser service cannot be allowed to leave the
                # microphone open.
                detail = f"{subsystem.name}: {type(error).__name__}: {error}"
                self.cleanup_errors.append(detail)
                self.log(f"[Lifecycle] Cleanup failed for {detail}")
            else:
                self.cleaned.append(subsystem.name)
                self.log(f"[Lifecycle] Released {subsystem.name}.")

        self.started.clear()
        if self.cleanup_errors:
            self.log(
                f"[Lifecycle] Shutdown complete with "
                f"{len(self.cleanup_errors)} problem(s)."
            )
        else:
            self.log("[Lifecycle] Shutdown complete.")

    @property
    def is_shut_down(self) -> bool:
        return self._shut_down


# --------------------------------------------------------------- watchdog


class StartupTimeout(RuntimeError):
    """A startup stage did not finish inside its budget."""


def build_within(
    name: str,
    factory: Callable[[], Any],
    *,
    timeout: float,
    log: Callable[[str], None] = print,
) -> Any:
    """Run a constructor with a deadline, on a thread that cannot block exit.

    Python cannot interrupt a thread that is blocked in C -- there is no safe
    way to abort a constructor part-way and be left with a usable process. So
    this does not try to. It bounds how long the *caller* waits, and leaves
    the stuck work on a daemon thread, which the interpreter abandons at exit
    rather than joining.

    That distinction is the whole design. The failure this contains is a
    startup that never finishes and never fails: measured in 4E-G, engine
    construction sometimes reached ready in ~90s and sometimes not in 300s,
    with no timeout anywhere and nothing to report. Reproduced on the
    original code with these changes stashed, so it is pre-existing.

    What this guarantees: the caller always gets an answer, so the lifecycle
    can report a required failure and release what it already holds.

    What it cannot guarantee, stated plainly: a constructor abandoned midway
    may have opened things nobody holds a reference to. Its own children get
    their own bounds -- the MCP client already times out at 15s -- and the
    process exits immediately afterwards, which returns handles to the OS.
    A partially-built object is never handed back and never used.
    """
    result: dict[str, Any] = {}

    def build() -> None:
        try:
            result["value"] = factory()
        except BaseException as error:  # noqa: BLE001 - reported, not swallowed
            result["error"] = error

    worker = threading.Thread(
        target=build, name=f"elaina-build-{name}", daemon=True,
    )
    started = time.monotonic()
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        log(
            f"[Lifecycle] {name} did not finish within {timeout:.0f}s and was "
            "abandoned; startup cannot continue."
        )
        raise StartupTimeout(f"{name} timed out after {timeout:.0f}s")
    if "error" in result:
        raise result["error"]
    log(
        f"[Lifecycle] {name} built in {time.monotonic() - started:.1f}s."
    )
    return result["value"]
