from brain.chat_engine import ChatEngine

engine = ChatEngine()

print("Elaina is ready!")

while True:

    user = input("\nYou: ")

    if user.lower() == "quit":
        break

    engine.chat(user)