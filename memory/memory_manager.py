from datetime import datetime, timedelta
import functools
import json
import re
import threading

from sqlalchemy import func

from memory.database import Base, SessionLocal, engine

from memory.models import (
    Memory,
    VectorMapping
)

from memory.embedding import EmbeddingModel

from memory.faiss_manager import FAISSManager

from memory.said import SaidJournal


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

_RELATIVE_TIME = re.compile(
    r"\b(?:next|this|tomorrow|tonight|yesterday|last)\b|다음|이번|내일|모레|어제|지난",
    re.IGNORECASE,
)

# How someone was, not who they are. Stores from before the memory gate
# learned the difference still hold them -- a real one kept "The user feels
# exhausted due to lack of sleep." and "The user is doing well." as facts,
# and a profile that says so in every turn is a caricature.
_MOOD = re.compile(
    r"\b(?:feels?|feeling|is\s+doing|expressed|seems?)\b"
    r"|\b(?:tired|exhausted|sleepy|stressed|bored|in\s+a\s+good\s+state)\b",
    re.IGNORECASE,
)
# A real store held fourteen variations of "the user is working on a
# project". The newest few say it; the rest crowd out the facts.
_PER_CATEGORY = {"project": 3}
# How the extractor starts every memory; not what any of them says.
_SHARED_WORDS = {
    "the", "user", "users", "user's", "their", "they", "them", "and", "has",
    "have", "had", "was", "are", "who", "with", "that", "this", "for", "from",
    "does", "not", "into", "words", "said", "사용자", "사용자는", "사용자의", "사용자가",
}


# Said about a moment of one day: "I went to the gym this morning", "어제 잠을
# 잘 못 잤어". True when it was said, and not who they are a week later. The
# extractor keeps about half of these (8 of 18, measured with its reasoning
# off), so they are dated like any relative day and, after a few days, no
# longer put in front of her. They stay in the database.
_A_MOMENT = re.compile(
    r"\b(?:last\s+night|this\s+(?:morning|afternoon|evening)|today|tonight|"
    r"yesterday|just\s+now|right\s+now)\b|어제|오늘|방금|아까",
    re.IGNORECASE,
)
MOMENT_DAYS = 3


def select_profile(rows, *, limit=24, now=None):
    """Which of their memories go in front of her, from (category, content,
    created_at) rows newest first: no moods, each near-duplicate once, a few
    project notes at most, a relative day ("next Friday") dated to when it
    was said, and a remark about one day left out once that day is past."""
    now = now or datetime.utcnow()
    seen = []
    counts = {}
    facts = []
    for category, content, created_at in rows:
        text = " ".join(str(content or "").split())
        moment = bool(_A_MOMENT.search(text))
        if moment and created_at is not None and (now - created_at).days >= MOMENT_DAYS:
            continue
        # Compared on what the fact says, not on how every fact starts:
        # "The user is vegetarian." shares "the" and "user" with every other
        # memory, and that alone once made it a duplicate -- measured, the
        # diet was dropped and "Do I follow any particular diet?" was told
        # she had not been told.
        # A Korean word is a word at two syllables. Counted from three, a
        # memory kept in their own words -- "나 매운 거 못 먹어" -- had no
        # words at all and was never shown.
        words = set(re.findall(r"[가-힣]{2,}|[^\W_]{3,}", text.casefold())) - _SHARED_WORDS
        if not text or not words or _MOOD.search(text):
            continue
        cap = _PER_CATEGORY.get(category)
        if cap is not None and counts.get(category, 0) >= cap:
            continue
        # Most of the shorter one, rounded: cut down instead, two facts of
        # two words each were one fact if they shared a verb -- "The user
        # likes jazz." went as a duplicate of "The user likes pizza.", and
        # she had not been told about the jazz.
        if any(
            len(words & other) >= max(1, round(0.8 * min(len(words), len(other))))
            for other in seen
        ):
            continue
        seen.append(words)
        counts[category] = counts.get(category, 0) + 1
        if (moment or _RELATIVE_TIME.search(text)) and created_at is not None:
            text = f"{text} (said on {created_at:%Y-%m-%d})"
        facts.append(text)
        if len(facts) >= limit:
            break
    return facts


def _one_at_a_time(method):
    """One thread in the session at a time, no transaction left open, and a
    failed write rolled back.

    The session is shared by the turn (recall, the profile) and by the
    background thread that keeps what was said, and a SQLAlchemy session is
    not built for that. Measured live, 2026-10-13: a search from the turn
    landed inside a store from the background thread, the same new row was
    flushed twice ("UNIQUE constraint failed: memories.id"), and the session
    stayed in that failed state -- so every later turn that read memory
    raised ``PendingRollbackError`` in the reply thread and she stopped
    answering at all until she was restarted. The engine's own lock was held
    by the storing thread only; the turn never took it for a search.

    So the manager guards itself. Held for the database work only, never
    across a model call.
    """
    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            try:
                result = method(self, *args, **kwargs)
                # Ends the transaction a read began, too: left open it holds
                # the file against the journal's own short writes.
                self.db.commit()
                return result
            except Exception:
                try:
                    self.db.rollback()
                except Exception:
                    pass
                raise
    return guarded


class MemoryManager:

    def __init__(self, sessions=None, bind=None, embedder=None, index_path=None):
        """The arguments are for tests: another database, a stand-in for the
        embedding model, another index file. Left out, they are her own."""
        sessions = sessions or SessionLocal
        bind = bind or engine
        self._lock = threading.RLock()

        # Nothing else ever created the tables. A database that already
        # existed kept working; a new one -- a fresh install, a deleted
        # file, a new runtime folder -- failed every store with "no such
        # table: memories", silently, so she remembered nothing at all.
        # Idempotent: an existing table is left exactly as it is.
        Base.metadata.create_all(bind)

        # What a search hands back is read by the caller after the lock is
        # released. Expired on commit, every attribute read would be another
        # query on this session, from the caller's thread.
        self.db = sessions(expire_on_commit=False)

        # What they said, written down before anything is made of it
        # (memory/said.py). Its own short sessions, never this one.
        self.said = SaidJournal(sessions, bind)

        self.embedder = embedder or EmbeddingModel()

        dimension = len(
            self.embedder.encode("hello")
        )

        self.faiss = FAISSManager(dimension, index_path)

        self._check_index()

    @_one_at_a_time
    def _check_index(self):
        """Rebuild the search index from the database when the two disagree.

        A memory is three writes -- the row, its vector, the row saying which
        vector is whose -- and the backend is stopped with ``taskkill /F``, so
        any of them can be the last. Two outcomes do damage: positions that
        no longer line up (an unreadable or missing index file is replaced by
        an empty one, and the next memory then takes a position another row
        owns, which the unique constraint refuses -- every store after that
        fails), and a memory with no vector, which search can never find.

        A vector whose memory is gone is harmless: search skips it.
        """
        mapped = self.db.query(VectorMapping).count()
        highest = self.db.query(func.max(VectorMapping.faiss_index)).scalar()
        total = int(self.faiss.index.ntotal)
        without = (
            self.db.query(Memory)
            .outerjoin(VectorMapping, VectorMapping.memory_id == Memory.id)
            .filter(VectorMapping.id.is_(None))
            .count()
        )
        in_step = mapped == total and (highest is None or int(highest) == total - 1)
        if in_step and not without:
            return
        print(
            f"[Memory] The search index ({total} vectors) and the database "
            f"({mapped} placed, {without} memories without one) disagree; "
            "rebuilding the index from the database."
        )
        self.rebuild_index()

    @_one_at_a_time
    def rebuild_index(self):
        """One vector per memory, in the order they were kept. The database is
        what she was told; the index is only a way to find it. Nothing is
        changed unless all of it can be: a model that will not load must not
        cost her the index she had."""
        try:
            memories = self.db.query(Memory).order_by(Memory.id).all()
            vectors = [self.embedder.encode(memory.content) for memory in memories]
        except Exception as error:
            self.db.rollback()
            print(
                "[Memory] The search index could not be rebuilt "
                f"({type(error).__name__}: {error}); left as it was."
            )
            return
        previous = self.faiss.index
        try:
            self.faiss.reset()
            self.db.query(VectorMapping).delete()
            for position, (memory, vector) in enumerate(zip(memories, vectors)):
                self.faiss.add_vector(vector)
                self.db.add(VectorMapping(faiss_index=position, memory_id=memory.id))
            self.faiss.save()
            self.db.commit()
        except Exception as error:
            self.db.rollback()
            self.faiss.index = previous
            print(
                "[Memory] The search index could not be rebuilt "
                f"({type(error).__name__}: {error}); left as it was."
            )
            return
        print(f"[Memory] Rebuilt the search index: {len(memories)} memories.")

    @_one_at_a_time
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

    @_one_at_a_time
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

        # The distances were being discarded. MemoryRanker weights similarity
        # at 0.50 -- its largest term by far -- and read it with
        # getattr(memory, "similarity", 1.0), so with nothing ever setting the
        # attribute every memory scored an identical 1.0 there. Ranking was
        # decided entirely by importance, recency and access count, and how
        # well a memory actually matched the question counted for nothing.
        distances, indices = self.faiss.search(vector, k)

        cutoff = None
        if newer_than_seconds is not None:
            cutoff = datetime.utcnow() - timedelta(seconds=newer_than_seconds)

        results = []

        for distance, idx in zip(distances, indices):

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

            # A bounded, monotonically decreasing function of L2 distance.
            # Deliberately not 1 - d/2 (the cosine identity), because that
            # assumes normalised embeddings and would go negative when they
            # are not. This only has to order correctly, which it does for
            # any non-negative distance.
            memory.similarity = 1.0 / (1.0 + float(distance))

            memory.last_accessed = datetime.utcnow()
            memory.access_count += 1

            self.db.commit()

            results.append(memory)

        return results

    # ------------------------------------------------------------ profile

    # What the person has told her about themselves. "general" is left out
    # on purpose: in a real store it holds world knowledge the extractor
    # mistook for a memory ("Hatsune Miku is a virtual singer...").
    # Every category the extractor can write, "general" included -- it is
    # also the extractor's fallback for anything it did not classify, so
    # leaving it out hides whatever it was unsure about. Measured after a
    # restart: "I'm allergic to peanuts." was filed as general, never
    # reached the profile, and "Do I have any food allergies?" was answered
    # "You haven't told me that yet." What is *not* a fact about someone is
    # dropped by ``select_profile`` (moods), not by the label here.
    PROFILE_CATEGORIES = (
        "personal", "education", "preference", "relationship", "goal", "project",
        "general",
    )

    @_one_at_a_time
    def profile(self, limit=200):
        """Everything they have told her about themselves, newest first,
        each near-duplicate once.

        All of it, not the newest two dozen: this is also what "have they
        told me that?" is answered from, and with a cap of 24 the first
        things a person ever said -- their name -- were the first to fall
        out of it once they had said 25. What goes in front of the model on
        one turn is chosen from this (ChatEngine._told_for_this_turn).

        Retrieval by similarity cannot carry this: measured on a store of
        seven facts, "tell me a joke" scored 0.53 against them and "which
        school do I go to?" 0.50 -- no threshold separates a question about
        the person from one about anything else. A person knows who they
        are talking to; so does she, from this list.
        """
        rows = (
            self.db.query(Memory)
            .filter(Memory.is_active.is_(True))
            .filter(Memory.category.in_(self.PROFILE_CATEGORIES))
            .order_by(Memory.created_at.desc())
            .limit(2000)
            .all()
        )
        return select_profile(
            [(row.category, row.content, row.created_at) for row in rows],
            limit=limit,
        )

    # ------------------------------------------------- research evidence

    @_one_at_a_time
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

    @_one_at_a_time
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
    
    @_one_at_a_time
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

    # How close a memory has to be to what the person asked to forget.
    # Set conservatively: the cost of forgetting too little is that they
    # say it again, and the cost of forgetting too much is silently losing
    # something they told you once. Both are visible, because forget()
    # returns what it removed and the caller says it out loud -- which is
    # the whole point of the operation being visible rather than quiet.
    FORGET_SIMILARITY_FLOOR = 0.35
    FORGET_LIMIT = 5
    # How far below the closest match a memory may be and still count as
    # part of the same request. Found in a dogfood turn: "forget that I'm
    # vegetarian" removed the vegetarian memory *and* an unrelated one
    # about not drinking coffee after 2pm -- both were about food, both
    # cleared the floor. Naming one subject must not clear the shelf.
    FORGET_MARGIN = 0.15

    @_one_at_a_time
    def forget(self, subject="", *, everything=False):
        """Deactivate what the person asked to be rid of, and say what went.

        A7's "forgetting works and is visible to the user". Before this
        there was **no way to delete a memory at all** -- store, search
        and update, and nothing else -- so "forget what I told you about
        my school" changed nothing and she carried on knowing it.

        Soft, via the ``is_active`` flag the model already carries and
        ``search`` already honours. The FAISS vector stays in the index
        and its row is filtered out on read, which avoids rebuilding an
        append-only index to delete one row.

        Returns the contents removed, so the caller can name them. A
        forget that reports nothing is indistinguishable from a forget
        that did nothing.
        """
        if everything:
            memories = (
                self.db.query(Memory).filter_by(is_active=True).all()
            )
            removed = [memory.content for memory in memories]
            for memory in memories:
                memory.is_active = False
            self.db.commit()
            return removed

        subject = str(subject or "").strip()
        if not subject:
            return []

        matches = [
            memory for memory in self.search(subject, k=self.FORGET_LIMIT)
            if getattr(memory, "similarity", 0.0) >= self.FORGET_SIMILARITY_FLOOR
        ]
        if not matches:
            return []

        # Only the cluster around the closest match. A request names one
        # subject; anything appreciably further away is a different fact
        # that happens to share a topic.
        best = max(
            getattr(memory, "similarity", 0.0) for memory in matches
        )
        removed = []
        for memory in matches:
            if best - getattr(memory, "similarity", 0.0) > self.FORGET_MARGIN:
                continue
            memory.is_active = False
            removed.append(memory.content)
        if removed:
            self.db.commit()
        return removed

    @_one_at_a_time
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
