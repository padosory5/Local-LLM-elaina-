import faiss
import numpy as np
import os


class FAISSManager:

    def __init__(self, dimension, index_path="database/faiss.index"):
        self.dimension = dimension
        self.index_path = index_path

        if os.path.exists(index_path):
            try:
                self.index = faiss.read_index(index_path)
                print("Loaded existing FAISS index.")
            except RuntimeError:
                print("Corrupted index detected. Creating a new one.")
                self.index = faiss.IndexFlatL2(dimension)
        else:
            self.index = faiss.IndexFlatL2(dimension)
            print("Created new FAISS index.")

    def add_vector(self, vector):
        vector = np.array([vector], dtype=np.float32)
        self.index.add(vector)

    def search(self, vector, k=5):
        vector = np.array([vector], dtype=np.float32)
        distances, indices = self.index.search(vector, k)
        return distances[0], indices[0]

    def save(self):
        faiss.write_index(self.index, self.index_path)