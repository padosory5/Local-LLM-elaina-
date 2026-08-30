from datetime import datetime, timedelta
import json

from memory.database import SessionLocal

from memory.models import (
    Memory,
    VectorMapping
)

from memory.embedding import EmbeddingModel

from memory.faiss_manager import FAISSManager


# What a memory is *for*. The schema already carried ``category``; nothing
# used it to separate kinds of memory, so one FAISS index served both "what
# this person is like" and "what a search returned five minutes ago".
#
# Keeping research here rather than in a second store is deliberate: the
# retrieval, embedding, ranking and context-building already exist and work.
# What research evidence needs that a personal memory does not is a shorter
# shelf life -- a hotel price is true for an afternoon -- so recall filters
# on age, and the two kinds never appear in each other's results.
CONVERSATION_CATEGORY = "general"
RESEARCH_CATEGORY = "research_evidence"

# Beyond this, a "current" price or availability is no longer current, and
# answering from it would be worse than looking again.
RESEARCH_TTL_SECONDS = 30 * 60


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

    def search(
        self,
        query,
        k=5,
        *,
        categories=None,
        exclude_categories=None,
        newer_than_seconds=None,
    ):
        """Semantic search, optionally restricted to a kind of memory.

        The filters are applied after retrieval rather than inside FAISS --
        one index, one embedding call, and the caller says which kinds it
        wants. Without them, asking "how has my week been" could return a
        hotel price as a fact about the person.
        """
        vector = self.embedder.encode(query)

        _, indices = self.faiss.search(vector, k)

        cutoff = None
        if newer_than_seconds is not None:
            cutoff = datetime.utcnow() - timedelta(seconds=newer_than_seconds)

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

            if not memory.is_active:
                continue
            if categories is not None and memory.category not in categories:
                continue
            if (
                exclude_categories is not None
                and memory.category in exclude_categories
            ):
                continue
            if cutoff is not None and (memory.created_at or cutoff) < cutoff:
                continue

            memory.last_accessed = datetime.utcnow()
            memory.access_count += 1

            self.db.commit()

            results.append(memory)

        return results

    # ------------------------------------------------- research evidence

    def remember_research(
        self,
        *,
        subject,
        query,
        evidence,
        sources=(),
        items=(),
    ):
        """Keep what a search found, so the next turn need not repeat it.

        Stored through the same interface as everything else -- one row, one
        vector, one index -- and marked with a category so it is only ever
        retrieved deliberately. The subject leads the content because that is
        what a follow-up will be resolved to and searched by; "which one
        would you choose?" embeds to nothing useful on its own.
        """
        subject = str(subject or "").strip()
        evidence = str(evidence or "").strip()
        if not subject or not evidence:
            return None

        detail = {
            "subject": subject,
            "query": str(query or "").strip(),
            "sources": [str(source) for source in sources if str(source).strip()],
            "items": [str(item) for item in items if str(item).strip()],
        }
        content = (
            f"{subject}\n"
            f"Asked: {detail['query'] or subject}\n"
            f"Found: {evidence}"
        )
        if detail["items"]:
            content += "\nOptions: " + "; ".join(detail["items"][:8])
        if detail["sources"]:
            content += "\nSources: " + ", ".join(detail["sources"][:4])

        return self.store_memory(
            content=content,
            category=RESEARCH_CATEGORY,
            importance=0.5,
            source=json.dumps(detail, ensure_ascii=False)[:900],
        )

    def recall_research(self, subject, k=3, max_age_seconds=None):
        """Recent research about this subject, freshest first."""
        subject = str(subject or "").strip()
        if not subject:
            return []
        age = (
            RESEARCH_TTL_SECONDS if max_age_seconds is None
            else max_age_seconds
        )
        found = self.search(
            subject,
            k=max(k, 5),
            categories={RESEARCH_CATEGORY},
            newer_than_seconds=age,
        )
        found.sort(
            key=lambda memory: memory.created_at or datetime.utcnow(),
            reverse=True,
        )
        return found[:k]
    
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

        _, indices = self.faiss.search(vector, k)

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
