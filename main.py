import os
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
from core.websocket_server import WebSocketServer
from voice.stt import SpeechToText


engine = ChatEngine()
response_thread = None
response_thread_lock = threading.Lock()
electron_process = None
electron_closed = threading.Event()

# Voice mode is the default. Cleared while in text mode so the mic loop
# below parks instead of listening, and the microphone stream itself is
# closed in handle_desktop_command so it truly stops, not just gets ignored.
voice_mode_enabled = threading.Event()
voice_mode_enabled.set()


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
        electron_closed.set()
        engine.cancel_active_turn()
        _thread.interrupt_main()

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
            speech_to_text.pause_listening()
        elif mode == "voice":
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


websocket_server = WebSocketServer(
    event_bus=engine.events,
    host="127.0.0.1",
    port=8765,
    command_handler=handle_desktop_command,
)

websocket_server.start()
launch_electron_if_requested()

speech_to_text = SpeechToText(
    config=engine.config,
)

print("\nElaina is ready.")
print("Microphone mode active.")
print("Say 'goodbye Elaina' to quit.")
print("Press Ctrl+C to stop manually.")


try:
    while True:
        if electron_closed.is_set():
            break

        if not voice_mode_enabled.is_set():
            # Text mode: leave the microphone untouched until voice mode is
            # selected again from Electron.
            voice_mode_enabled.wait(timeout=1.0)
            continue

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
    engine.cancel_active_turn()
    speech_to_text.close()
    if response_thread is not None:
        response_thread.join(timeout=5)
    if electron_process is not None and electron_process.poll() is None:
        electron_process.terminate()
    engine.close()
    print("Goodbye!")
