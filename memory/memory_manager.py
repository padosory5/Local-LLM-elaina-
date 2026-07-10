from datetime import datetime

from memory.database import SessionLocal

from memory.models import (
    Memory,
    VectorMapping
)

from memory.embedding import EmbeddingModel

from memory.faiss_manager import FAISSManager


class MemoryManager:

    def __init__(self):

        self.db = SessionLocal()

        self.embedder = EmbeddingModel()

        dimension = len(
            self.embedder.encode("hello")
        )

        self.faiss = FAISSManager(dimension)

    def store_memory(
        self,
        content,
        category="general",
        importance=1.0,
        source="conversation"
    ):

        memory = Memory(
            content=content,
            category=category,
            importance=importance,
            source=source
        )

        self.db.add(memory)

        self.db.commit()

        self.db.refresh(memory)

        vector = self.embedder.encode(content)

        self.faiss.add_vector(vector)

        faiss_position = self.faiss.index.ntotal - 1

        mapping = VectorMapping(
            faiss_index=faiss_position,
            memory_id=memory.id
        )

        self.db.add(mapping)

        self.db.commit()

        self.faiss.save()

        return memory.id

    def search(self, query, k=5):

        vector = self.embedder.encode(query)

        distances, indices = self.faiss.search(vector, k)

        results = []

        for idx in indices:

            if idx == -1:
                continue

            mapping = (
                self.db.query(VectorMapping)
                .filter_by(faiss_index=int(idx))
                .first()
            )

            if mapping is None:
                continue

            memory = (
                self.db.query(Memory)
                .filter_by(id=mapping.memory_id)
                .first()
            )

            if memory is None:
                continue

            memory.last_accessed = datetime.utcnow()
            memory.access_count += 1

            self.db.commit()

            results.append(memory)

        return results
    
    def get_all_memories(self):

        return (
        self.db.query(Memory)
        .filter_by(is_active=True)
        .all()
        )

    def update_memory(
        self,
        memory_id,
        new_content
    ):

        memory = (
            self.db.query(Memory)
            .filter_by(id=memory_id)
            .first()
        )

        if memory:

            memory.content = new_content

            self.db.commit()

    def search_memory_objects(self, text, k=5):

        vector = self.embedder.encode(text)

        distances, indices = self.faiss.search(vector, k)

        memories = []

        for idx in indices:

            if idx == -1:
                continue

            mapping = (
                self.db.query(VectorMapping)
                .filter_by(faiss_index=int(idx))
                .first()
            )

            if mapping is None:
                continue

            memory = (
                self.db.query(Memory)
                .filter_by(id=mapping.memory_id)
                .first()
            )

            if memory:
                memories.append(memory)

        return memories