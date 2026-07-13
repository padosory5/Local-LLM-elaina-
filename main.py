from brain.chat_engine import ChatEngine
from voice.stt import SpeechToText


engine = ChatEngine()

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

        engine.chat(user_input)

except KeyboardInterrupt:
    print("\nStopping Elaina...")

finally:
    engine.audio.stop()
    print("Goodbye!")