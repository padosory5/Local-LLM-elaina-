import os
from pathlib import Path

import faiss
import numpy as np

from core.paths import FAISS_INDEX_PATH, ensure_runtime_directories


class FAISSManager:

    def __init__(self, dimension, index_path=None):
        self.dimension = dimension
        ensure_runtime_directories()
        self.index_path = Path(index_path or FAISS_INDEX_PATH)

        if os.path.exists(self.index_path):
            try:
                self.index = faiss.read_index(str(self.index_path))
                print("Loaded existing FAISS index.")
            except RuntimeError:
                # MemoryManager sees the empty index against a database that
                # is not, and rebuilds it from there.
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

    def reset(self):
        """An empty index, for a rebuild. Nothing on disk changes until save()."""
        self.index = faiss.IndexFlatL2(self.dimension)

    def save(self):
        """The whole file or the old one. The desktop window stops the backend
        with ``taskkill /F``; killed part way through a write straight onto
        the index, the file was unreadable at the next start and every
        position the database remembered pointed at nothing."""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.index_path.with_name(self.index_path.name + ".writing")
        try:
            faiss.write_index(self.index, str(partial))
            os.replace(partial, self.index_path)
        finally:
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
