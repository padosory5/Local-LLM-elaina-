import os
import signal
import subprocess
import sys
import threading
import _thread
from pathlib import Path

from dotenv import load_dotenv

# Elaina's replies naturally include characters such as em dashes and curly
# quotes. On a non-UTF-8 console codepage (e.g. cp949 on Korean Windows),
# printing them would otherwise raise UnicodeEncodeError mid-turn.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Screen-native browser control reads element rectangles from UI Automation
# in physical pixels and drives the pointer with them. Windows only lets a
# process declare its DPI awareness before its first UI call, so this has to
# happen here, ahead of Electron, pygame, and every window Elaina touches --
# left unset on a scaled display, every synthetic click lands off target.
from tools.screen_control.dpi import ensure_per_monitor_dpi_aware

ensure_per_monitor_dpi_aware()

# Load local API keys and credential paths before creating ChatEngine.
load_dotenv()

from brain.chat_engine import ChatEngine
from core import timing
from core.lifecycle import (
    Lifecycle,
    StartupTimeout,
    StopRequest,
    build_within,
)
from core.websocket_server import WebSocketServer
from voice.stt import SpeechToText


# Owns every handle and child process Elaina opens, and the order to release
# them in. Nothing is registered until it has actually started.
lifecycle = Lifecycle()

# The engine is built with a deadline. It wires ~45 collaborators, several of
# which reach outside the process -- Ollama, an MCP subprocess, the audio
# device -- and it had no bound at all: measured in 4E-G it sometimes reached
# ready in ~90s and sometimes not in 300s, with nothing to report either way.
# The cap is generous because a cold model load is legitimately slow; what it
# rules out is waiting forever.
ENGINE_STARTUP_TIMEOUT = float(os.getenv("ELAINA_ENGINE_TIMEOUT", "240"))

try:
    engine = build_within(
        "chat engine", ChatEngine, timeout=ENGINE_STARTUP_TIMEOUT,
    )
except StartupTimeout as error:
    # Nothing is registered with the lifecycle yet, so there is nothing to
    # unwind -- but say so in the same words the rest of startup uses, and
    # leave with a failing status rather than a traceback.
    print(f"[Lifecycle] NOT READY -- {error}")
    print(
        "[Lifecycle] Nothing had been registered yet, so there is nothing to "
        "release. Check that Ollama is running and no previous backend is "
        "still holding the microphone."
    )
    raise SystemExit(1) from None
except Exception as error:
    print(f"[Lifecycle] NOT READY -- chat engine: {type(error).__name__}: {error}")
    raise SystemExit(1) from None
response_thread = None
response_thread_lock = threading.Lock()
electron_process = None
electron_closed = threading.Event()

# Voice mode is the default. Cleared while in text mode so the mic loop
# below parks instead of listening, and the microphone stream itself is
# closed in handle_desktop_command so it truly stops, not just gets ignored.
voice_mode_enabled = threading.Event()
voice_mode_enabled.set()

# The first turn pays for model loading no later turn does. Kept apart in the
# report rather than averaged together, which would describe neither.
_first_turn = True


def launch_electron_if_requested():
    """
    Open Electron when the user starts Elaina with ``python main.py``.

    Electron sets an ownership flag when it launches this backend itself. That
    prevents the backend from opening a second Electron process, so the same
    project remains compatible with the BAT/VBS launchers.
    """
    global electron_process

    if os.getenv("ELAINA_STARTED_BY_ELECTRON") == "1":
        return
    if os.getenv("ELAINA_OPEN_DESKTOP", "1") == "0":
        return

    desktop_directory = Path(__file__).resolve().parent / "desktop"
    npm_command = "npm.cmd" if os.name == "nt" else "npm"
    environment = os.environ.copy()
    environment["ELAINA_PYTHON_OWNS_BACKEND"] = "1"

    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt"
        else 0
    )

    try:
        electron_process = subprocess.Popen(
            [npm_command, "start", "--silent"],
            cwd=desktop_directory,
            env=environment,
            creationflags=creation_flags,
        )
    except (FileNotFoundError, OSError) as error:
        print(
            "[Desktop] Electron could not start. Install Node.js, then run "
            "'npm install' inside the desktop folder. "
            f"Details: {error}"
        )
        return

    def watch_electron():
        assert electron_process is not None
        exit_code = electron_process.wait()
        print(f"[Desktop] Electron exited with code {exit_code}.")
        _begin_stop()

    threading.Thread(
        target=watch_electron,
        name="elaina-electron-watch",
        daemon=True,
    ).start()


def run_response(user_input, selected_screen):
    """Generate one response without blocking the microphone listener."""
    engine.chat(
        user_input,
        screen_snapshot=selected_screen,
    )


def dispatch_response(user_input, selected_screen):
    """
    Start one response turn, whether it came from the microphone or a typed
    message. Both entry points share this lock so a spoken turn and a typed
    turn can never mutate conversation/tool state at the same time.
    """
    global response_thread

    with response_thread_lock:
        # An interruption sets the old turn's cancellation event. Give that
        # worker a moment to leave its Ollama stream before starting a new
        # turn so conversation and tool state are never mutated concurrently.
        if response_thread is not None and response_thread.is_alive():
            response_thread.join(timeout=5)

        if response_thread is not None and response_thread.is_alive():
            print(
                "[ChatEngine] The previous response is still stopping. "
                "Please repeat the request."
            )
            return

        response_thread = threading.Thread(
            target=run_response,
            args=(user_input, selected_screen),
            name="elaina-response",
            daemon=True,
        )
        response_thread.start()


def handle_desktop_command(message):
    """Handle actions sent by the Electron interface."""
    command = message.get("command")

    if command == "shutdown":
        # The graceful counterpart to Electron's taskkill. Asking first lets
        # the backend release what it owns -- the browser service, the MCP
        # subprocess, the microphone -- instead of being force-killed with
        # its children still holding handles. The caller may still escalate
        # if this does not land.
        print("[Lifecycle] Shutdown requested by the desktop window.")
        _begin_stop()
        return

    if command == "get_computer_control_mode":
        engine.publish_computer_control_mode()
        return

    if command == "set_computer_control_mode":
        enabled = message.get("enabled")
        if not isinstance(enabled, bool):
            print("[Computer Control Mode] Invalid state.")
            engine.publish_computer_control_mode()
            return
        engine.set_computer_control_mode(enabled)
        return

    if command == "queue_screen_region":
        region = message.get("region")

        if not isinstance(region, dict):
            print("[Screen Selection] Invalid region.")
            return

        engine.prepare_screen_region(region)
        return

    if command == "set_input_mode":
        mode = message.get("mode")

        if mode == "text":
            voice_mode_enabled.clear()
            if speech_to_text is not None:
                speech_to_text.pause_listening()
        elif mode == "voice":
            if speech_to_text is None:
                # Degraded start: there is no microphone to resume.
                print("[Input Mode] No microphone is available; staying in text mode.")
                engine.events.emit("input_mode_changed", mode="text")
                return
            speech_to_text.resume_listening()
            voice_mode_enabled.set()
        else:
            print("[Input Mode] Invalid mode.")
            return

        engine.events.emit("input_mode_changed", mode=mode)
        return

    if command == "send_text_message":
        text = message.get("text")

        if not isinstance(text, str) or not text.strip():
            print("[Text Message] Invalid text.")
            return

        # Typing a new message while Elaina is speaking should interrupt her,
        # the same way starting to talk over her does for voice input.
        engine.on_speech_start()

        selected_screen = engine.consume_pending_screen_snapshot()
        dispatch_response(text.strip(), selected_screen)
        return

    if command == "project_change_decision":
        proposal_id = message.get("proposal_id")
        decision = message.get("decision")
        revised_texts = message.get("revised_texts")

        if not isinstance(proposal_id, str):
            print("[Project Change] Invalid proposal ID.")
            return

        if decision not in {"approve", "reject"}:
            print("[Project Change] Invalid decision.")
            return

        if revised_texts is not None and (
            not isinstance(revised_texts, list)
            or not all(isinstance(text, str) for text in revised_texts)
        ):
            print("[Project Change] Invalid edited code.")
            return

        engine.resolve_project_change(
            proposal_id=proposal_id,
            approved=decision == "approve",
            revised_texts=revised_texts,
        )
        return

    if command == "git_action_decision":
        proposal_id = message.get("proposal_id")
        decision = message.get("decision")
        commit_message = message.get("commit_message", "")

        if not isinstance(proposal_id, str):
            print("[Git Action] Invalid proposal ID.")
            return
        if decision not in {"commit_push", "commit_only", "reject"}:
            print("[Git Action] Invalid decision.")
            return
        if not isinstance(commit_message, str):
            print("[Git Action] Invalid commit message.")
            return

        engine.resolve_git_action(
            proposal_id=proposal_id,
            approved=decision != "reject",
            commit_message=commit_message,
            push=decision == "commit_push",
        )
        return

    if command == "action_approval_decision":
        proposal_id = message.get("proposal_id")
        decision = message.get("decision")

        if not isinstance(proposal_id, str):
            print("[Agent Action] Invalid proposal ID.")
            return
        if decision not in {"approve", "reject"}:
            print("[Agent Action] Invalid decision.")
            return

        engine.resolve_agent_action(
            proposal_id=proposal_id,
            approved=decision == "approve",
        )


def _start_websocket_server():
    server = WebSocketServer(
        event_bus=engine.events,
        host="127.0.0.1",
        port=8765,
        command_handler=handle_desktop_command,
    )
    server.start()
    return server


def _stop_electron(timeout: float = 5.0) -> None:
    """Close the window Elaina opened -- and only that one.

    Escalates to kill if it does not go, which ``.terminate()`` alone never
    did: an Electron that ignored the request simply stayed running. Scoped
    to the single process handle this backend spawned; nothing here matches
    by process name, so a window the user opened themselves is untouched.
    """
    if electron_process is None or electron_process.poll() is not None:
        return
    electron_process.terminate()
    try:
        electron_process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print("[Desktop] Electron did not exit; killing the process we spawned.")
        electron_process.kill()
        try:
            electron_process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print("[Desktop] Electron could not be stopped.")


def _release_for_stop() -> None:
    """Let go of everything the main loop is waiting on.

    ``_thread.interrupt_main()`` on its own was not enough, and that is why
    Electron reaches for taskkill: the loop spends nearly all of its time
    blocked inside ``listen_and_transcribe``, waiting on the microphone down
    in C, where a pending KeyboardInterrupt is not seen. Measured -- a
    shutdown request sat unanswered for minutes while the process stayed up.

    The stream has to be closed for that call to return. So the flag the loop
    checks is set first, and the microphone is paused to unblock the read.
    """
    electron_closed.set()
    voice_mode_enabled.clear()
    engine.cancel_active_turn()
    if speech_to_text is not None:
        try:
            speech_to_text.pause_listening()
        except Exception as error:
            print(f"[Lifecycle] Could not pause the microphone: {error}")


_stop_request = StopRequest(
    _release_for_stop, interrupt=lambda: _thread.interrupt_main(),
)


def _begin_stop() -> None:
    """Ask the main loop to leave, from any thread. At most once."""
    _stop_request.notify()


def _request_stop(signum, _frame) -> None:
    """A stop signal must reach the same cleanup a Ctrl+C does.

    ``from_signal`` is what stops the 1,926-line shutdown measured in the
    first dogfooding session: ``interrupt_main()`` delivers SIGINT rather
    than raising ``KeyboardInterrupt`` while this handler is installed, so
    asking for one from inside the handler asks the handler to run again.
    """
    if _stop_request.requested:
        return
    print(f"\n[Lifecycle] Stop signal {signum} received.")
    _stop_request.notify(from_signal=True)


for _signal_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
    _signal = getattr(signal, _signal_name, None)
    if _signal is None:
        continue
    try:
        signal.signal(_signal, _request_stop)
    except (ValueError, OSError):
        # Not the main thread, or unsupported on this platform.
        pass


# Everything from here on registers its own cleanup the moment it starts, so
# a failure part-way through unwinds exactly what came up. This block used to
# run outside the try/finally below: a missing microphone exited on a
# traceback with port 8765 still bound and an Electron window still open, and
# the next launch met a port collision and a second window.
lifecycle.start(
    "chat engine", lambda: engine, cleanup=lambda value: value.close(),
)

websocket_server = lifecycle.start(
    "websocket server",
    lambda: _start_websocket_server(),
    cleanup=lambda server: server.stop(),
)

# Optional on purpose: the backend runs headless for the live checks, and
# ELAINA_OPEN_DESKTOP=0 is a supported way to start it.
lifecycle.start(
    "desktop window",
    launch_electron_if_requested,
    required=False,
    cleanup=lambda _: _stop_electron(),
)

# Also optional. Without a microphone she is still fully usable by text, and
# crashing the whole process over an unavailable input device was the harsher
# of the two failures.
speech_to_text = lifecycle.start(
    "speech to text",
    lambda: SpeechToText(config=engine.config),
    required=False,
    cleanup=lambda stt: stt.close(),
)
if speech_to_text is None:
    # Park the microphone loop rather than calling into something that is
    # not there.
    voice_mode_enabled.clear()

if not lifecycle.ready():
    lifecycle.report_ready()
    lifecycle.shutdown("a required subsystem did not start")
    raise SystemExit(1)

lifecycle.report_ready()
print(
    "Microphone mode active." if speech_to_text is not None
    else "Text mode only -- no microphone available."
)
print("Say 'goodbye Elaina' to quit.")
print("Press Ctrl+C to stop manually.")


try:
    while True:
        if electron_closed.is_set():
            break

        if speech_to_text is None or not voice_mode_enabled.is_set():
            # Text mode: leave the microphone untouched until voice mode is
            # selected again from Electron.
            voice_mode_enabled.wait(timeout=1.0)
            continue

        # A turn begins when the microphone starts listening, not when the
        # transcript arrives -- the VAD wait and transcription are part of
        # what the person experiences as the response time.
        timing.begin(label="voice", cold=_first_turn)
        _first_turn = False
        user_input = speech_to_text.listen_and_transcribe(
            on_speech_start=engine.on_speech_start,
            is_tts_speaking=engine.audio.is_speaking,
            echo_text_provider=engine.audio.echo_reference_text,
        )

        if not user_input:
            continue

        command = user_input.lower().strip()

        if command in {
            "quit",
            "exit",
            "goodbye",
            "goodbye elaina",
            "stop elaina",
        }:
            break

        selected_screen = engine.consume_pending_screen_snapshot()
        dispatch_response(user_input, selected_screen)

except KeyboardInterrupt:
    print("\nStopping Elaina...")

finally:
    # Stop the turn in flight before releasing what it is using, then let the
    # lifecycle unwind the rest in reverse order. Each handler is guarded, so
    # one that hangs or raises cannot leave the microphone open behind it.
    engine.cancel_active_turn()
    if response_thread is not None:
        response_thread.join(timeout=5)
    lifecycle.shutdown(
        "the desktop window closed" if electron_closed.is_set()
        else "the backend was asked to stop"
    )
    print("Goodbye!")
