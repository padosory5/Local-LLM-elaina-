class Attention:

    def __init__(self):

        self.topic = None
        self.subtopic = None
        self.goal = None

    def update(self, user_input):

        text = user_input.lower()

        # Very simple rules for now
        if "unity" in text:
            self.topic = "Unity"

        if "python" in text:
            self.topic = "Python"

        if "ollama" in text:
            self.topic = "Ollama"

        if "faiss" in text:
            self.topic = "FAISS"

        if "memory" in text:
            self.subtopic = "Memory"

        if "tts" in text:
            self.goal = "Voice"

    def build_context(self):

        context = ""

        if self.topic:
            context += f"Current Topic: {self.topic}\n"

        if self.subtopic:
            context += f"Subtopic: {self.subtopic}\n"

        if self.goal:
            context += f"Goal: {self.goal}\n"

        return context