from voice.base import BaseTTS

import subprocess
import tempfile
import os

from playsound import playsound


class PiperTTS(BaseTTS):

    def __init__(self):

        self.piper = r"C:\Users\pados\piper\piper.exe"

        self.model = r"C:\Users\pados\piper\voices\en_US-amy-medium.onnx"

    def speak(self, text: str):

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False
        ) as temp:

            output = temp.name

        subprocess.run(
            [
                self.piper,
                "--model",
                self.model,
                "--output_file",
                output
            ],
            input=text,
            text=True,
            encoding="utf-8",
            check=True
        )

        playsound(output)

        try:
            os.remove(output)
        except PermissionError:
            pass