from brain.chat_engine import ChatEngine
from core.websocket_server import WebSocketServer
from voice.stt import SpeechToText


engine = ChatEngine()


def handle_desktop_command(message):
    """Handle actions sent by the Electron interface."""
    command = message.get("command")

    if command == "queue_screen_region":
        region = message.get("region")

        if not isinstance(region, dict):
            print("[Screen Selection] Invalid region.")
            return

        engine.prepare_screen_region(region)
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


websocket_server = WebSocketServer(
    event_bus=engine.events,
    host="127.0.0.1",
    port=8765,
    command_handler=handle_desktop_command,
)

websocket_server.start()

speech_to_text = SpeechToText(
    model_size="small",
    language=None,
)

print("\nElaina is ready.")
print("Microphone mode active.")
print("Say 'goodbye Elaina' to quit.")
print("Press Ctrl+C to stop manually.")


try:
    while True:
        user_input = speech_to_text.listen_and_transcribe(
            on_speech_start=engine.on_speech_start,
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

        engine.chat(
            user_input,
            screen_snapshot=selected_screen,
        )
        # Wait before reopening the microphone so Elaina
        # does not hear and interrupt her own voice.
        engine.audio.wait_until_idle()

except KeyboardInterrupt:
    print("\nStopping Elaina...")

finally:
    engine.close()
    print("Goodbye!")
