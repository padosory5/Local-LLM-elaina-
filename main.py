from brain.chat_engine import ChatEngine
from voice.stt import SpeechToText


engine = ChatEngine()

# Use language=None for automatic English/Korean detection.
speech_to_text = SpeechToText(
    model_size="small",
    language=None,
)

print("\nElaina is ready!")
print("[k] Keyboard")
print("[m] Microphone")
print("[q] Quit")


while True:
    mode = input("\nChoose input mode [k/m/q]: ").strip().lower()

    if mode in {"q", "quit", "exit"}:
        print("Goodbye!")
        break

    if mode in {"k", "keyboard"}:
        user_input = input("\nYou: ").strip()

    elif mode in {"m", "mic", "microphone"}:
        user_input = speech_to_text.record_until_enter()

        if not user_input:
            continue

    else:
        print("Please enter k, m, or q.")
        continue

    if user_input.lower() in {"quit", "exit"}:
        break

    engine.chat(user_input)