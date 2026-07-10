from sentence_transformers import SentenceTransformer


class EmbeddingModel:

    def __init__(self):
        self.model = SentenceTransformer(
            "BAAI/bge-m3"
        )

    def encode(self, text: str):
        return self.model.encode(text)