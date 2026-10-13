"""What the person said, written down the moment they said it.

A memory used to exist only at the end of a background thread: an extraction
call, a consolidation call, then the row. Measured, that takes up to 13
seconds -- and the desktop window stops the backend with ``taskkill /F``
(desktop/main.js), so anything told to her in the last moments before she was
closed was never written at all. ``ChatEngine.close()`` waits for those
threads, and a force-kill never reaches it.

So the sentence itself goes into the database first, in the turn, as one
small insert. What a model later makes of it is a second step that can be
interrupted and done again: a row still ``waiting`` when she starts is
finished then. Nothing said to her is lost to a closed window, a crash or a
power cut.

It is also the record of where every memory came from. A memory's wording is
a model's (memory/extractor.py); the row here is the person's, exactly as
they said it, and stays beside it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from memory.database import Base, SessionLocal, engine

WAITING = "waiting"   # said; nothing made of it yet
KEPT = "kept"         # a memory holds it (``memory_id``)
PASSED = "passed"     # looked at; nothing in it to keep

# A sentence that fails this many times is left alone, said out loud, rather
# than tried again at every start for ever.
MOST_TRIES = 3


class Said(Base):
    __tablename__ = "said"

    id = Column(Integer, primary_key=True)

    text = Column(String, nullable=False)

    said_at = Column(DateTime, default=datetime.utcnow)

    state = Column(String, default=WAITING)

    memory_id = Column(Integer, nullable=True)

    tries = Column(Integer, default=0)


class SaidJournal:
    """Each call is its own short session, so the turn never waits on the
    store's lock (which is held across two model calls)."""

    def __init__(self, sessions=None, bind=None) -> None:
        self.sessions = sessions or SessionLocal
        # A database made before this table existed gets it here; an
        # existing table is left exactly as it is.
        Said.__table__.create(bind or engine, checkfirst=True)

    def note(self, text) -> int | None:
        """Write down what they said. None when it could not be written --
        which must never stop the turn."""
        said = " ".join(str(text or "").split())
        if not said:
            return None
        try:
            with self.sessions() as session:
                row = Said(text=said)
                session.add(row)
                session.commit()
                return int(row.id)
        except Exception as error:
            print(f"[Memory] Could not write down what they said: {type(error).__name__}: {error}")
            return None

    def settle(self, said_id, memory_id=None, kept=None) -> None:
        """What became of it: kept (in ``memory_id``, when the store said
        which), or looked at and nothing to keep."""
        if said_id is None:
            return
        if kept is None:
            kept = memory_id is not None
        try:
            with self.sessions() as session:
                row = session.get(Said, int(said_id))
                if row is None:
                    return
                row.state = KEPT if kept else PASSED
                row.memory_id = int(memory_id) if memory_id is not None else None
                session.commit()
        except Exception as error:
            print(f"[Memory] Could not mark what they said as done: {type(error).__name__}: {error}")

    def tried(self, said_id) -> int:
        """Count one more attempt; how many that makes."""
        if said_id is None:
            return 0
        try:
            with self.sessions() as session:
                row = session.get(Said, int(said_id))
                if row is None:
                    return 0
                row.tries = int(row.tries or 0) + 1
                session.commit()
                return int(row.tries)
        except Exception as error:
            print(f"[Memory] Could not count an attempt: {type(error).__name__}: {error}")
            return 0

    def waiting(self, limit: int = 50) -> list[tuple[int, str]]:
        """Sentences nothing was made of yet, oldest first."""
        try:
            with self.sessions() as session:
                rows = (
                    session.query(Said)
                    .filter(Said.state == WAITING)
                    .filter(Said.tries < MOST_TRIES)
                    .order_by(Said.id)
                    .limit(limit)
                    .all()
                )
                return [(int(row.id), str(row.text)) for row in rows]
        except Exception as error:
            print(f"[Memory] Could not read what was left to do: {type(error).__name__}: {error}")
            return []

    def behind(self, memory_id) -> list[str]:
        """Their own sentences a memory was made from, oldest first."""
        try:
            with self.sessions() as session:
                rows = (
                    session.query(Said)
                    .filter(Said.memory_id == int(memory_id))
                    .order_by(Said.id)
                    .all()
                )
                return [str(row.text) for row in rows]
        except Exception:
            return []
