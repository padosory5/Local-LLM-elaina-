"""The search index is rebuilt from the database when the two disagree.

A memory is three writes: the row, its vector in the FAISS index, and the row
that says which vector is whose. The desktop window stops the backend with
``taskkill /F``, so any of them can be the last thing that happened:

  * killed while the index file was being written: the file is unreadable,
    a new empty index was made in its place, and every vector position the
    database remembered now pointed at nothing -- then the next memory took
    position 0, which another row already owned, and every store after that
    failed on the unique constraint;
  * killed after the row and before its vector: the memory is in the database
    and can never be found by search.

So the index file is replaced whole or not at all, and at start the index is
checked against the database and rebuilt from it when positions have drifted
or a memory has no vector. The database is what she was told; the index is
only a way to find it.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from memory.memory_manager import MemoryManager
from memory.models import Memory, VectorMapping


class Embedder:
    """Eight numbers from the words of a text; the same text, the same vector."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def encode(self, text: str):
        self.asked.append(str(text))
        vector = np.zeros(8, dtype=np.float32)
        for word in str(text).casefold().split():
            vector[sum(map(ord, word)) % 8] += 1.0
        return vector


class IndexTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.database = os.path.join(folder.name, "memory.db")
        self.index = os.path.join(folder.name, "faiss.index")
        self.engines = []

    def manager(self) -> tuple[MemoryManager, Embedder, str]:
        """She starts: a new process on the same files."""
        engine = create_engine(f"sqlite:///{self.database}")
        self.engines.append(engine)
        self.addCleanup(engine.dispose)
        embedder = Embedder()
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            manager = MemoryManager(sessions=sessionmaker(bind=engine), bind=engine,
                                    embedder=embedder, index_path=self.index)
        self.addCleanup(manager.db.close)      # before the engine goes, or Windows keeps the file
        return manager, embedder, log.getvalue()

    def told(self, manager: MemoryManager) -> list[int]:
        with contextlib.redirect_stdout(io.StringIO()):
            return [manager.store_memory(content=text, category="personal")
                    for text in ("The user lives in Seattle.", "The user is allergic to peanuts.",
                                 "The user's dog is named Kong.")]

    def found(self, manager: MemoryManager, query: str) -> list[str]:
        return [memory.content for memory in manager.search(query, k=1)]

    # -- nothing wrong, nothing done

    def test_a_store_in_order_is_left_alone(self):
        manager, _, _ = self.manager()
        self.told(manager)

        again, embedder, log = self.manager()
        self.assertNotIn("rebuil", log)
        self.assertEqual(embedder.asked, ["hello"])          # the dimension probe, nothing re-embedded
        self.assertEqual(self.found(again, "The user is allergic to peanuts."), ["The user is allergic to peanuts."])

    def test_a_vector_whose_memory_is_gone_is_harmless(self):
        manager, _, _ = self.manager()
        ids = self.told(manager)
        manager.db.query(Memory).filter_by(id=ids[0]).delete()
        manager.db.commit()

        again, _, log = self.manager()
        self.assertNotIn("rebuil", log)
        self.assertEqual(self.found(again, "The user's dog is named Kong."), ["The user's dog is named Kong."])

    # -- killed part way

    def test_an_unreadable_index_is_rebuilt_from_the_database(self):
        manager, _, _ = self.manager()
        self.told(manager)
        with open(self.index, "wb") as handle:
            handle.write(b"half a file")

        again, _, log = self.manager()
        self.assertIn("rebuil", log)
        self.assertEqual(again.faiss.index.ntotal, 3)
        self.assertEqual(self.found(again, "The user lives in Seattle."), ["The user lives in Seattle."])
        self.assertEqual(sorted(m.faiss_index for m in again.db.query(VectorMapping).all()), [0, 1, 2])

    def test_a_missing_index_is_rebuilt_too(self):
        manager, _, _ = self.manager()
        self.told(manager)
        os.remove(self.index)

        again, _, log = self.manager()
        self.assertIn("rebuil", log)
        self.assertEqual(self.found(again, "The user's dog is named Kong."), ["The user's dog is named Kong."])

    def test_a_memory_kept_without_its_vector_gets_one(self):
        manager, _, _ = self.manager()
        self.told(manager)
        manager.db.add(Memory(content="The user has asthma.", category="personal"))
        manager.db.commit()                                   # killed here, before the vector

        again, _, log = self.manager()
        self.assertIn("rebuil", log)
        self.assertEqual(self.found(again, "The user has asthma."), ["The user has asthma."])

    def test_a_vector_nobody_owns_no_longer_shifts_the_rest(self):
        manager, embedder, _ = self.manager()
        self.told(manager)
        manager.faiss.add_vector(embedder.encode("written to the index and never to the database"))
        manager.faiss.save()

        again, _, log = self.manager()
        self.assertIn("rebuil", log)
        self.assertEqual(again.faiss.index.ntotal, 3)

    def test_she_can_keep_new_things_after_a_rebuild(self):
        manager, _, _ = self.manager()
        self.told(manager)
        os.remove(self.index)
        again, _, _ = self.manager()

        with contextlib.redirect_stdout(io.StringIO()):
            again.store_memory(content="The user plays tennis on Saturdays.", category="personal")
        self.assertEqual(self.found(again, "The user plays tennis on Saturdays."),
                         ["The user plays tennis on Saturdays."])
        self.assertEqual(self.found(again, "The user lives in Seattle."), ["The user lives in Seattle."])

    def test_a_rebuild_that_fails_leaves_everything_as_it_was(self):
        manager, _, _ = self.manager()
        self.told(manager)
        manager.db.add(Memory(content="The user has asthma.", category="personal"))
        manager.db.commit()

        engine = create_engine(f"sqlite:///{self.database}")
        self.addCleanup(engine.dispose)

        class Broken(Embedder):
            def encode(self, text):
                if text != "hello":
                    raise RuntimeError("the embedding model did not load")
                return super().encode(text)

        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            again = MemoryManager(sessions=sessionmaker(bind=engine), bind=engine,
                                  embedder=Broken(), index_path=self.index)
        self.addCleanup(again.db.close)
        self.assertIn("could not be rebuilt", log.getvalue())
        self.assertEqual(again.db.query(Memory).count(), 4)
        self.assertEqual(again.db.query(VectorMapping).count(), 3)

    # -- the file itself

    def test_the_index_file_is_replaced_whole_or_not_at_all(self):
        manager, _, _ = self.manager()
        self.told(manager)
        before = open(self.index, "rb").read()

        def cut_short(index, path):
            with open(path, "wb") as handle:
                handle.write(b"half")
            raise OSError("killed while writing")

        with mock.patch("memory.faiss_manager.faiss.write_index", cut_short):
            with self.assertRaises(OSError):
                manager.faiss.save()
        self.assertEqual(open(self.index, "rb").read(), before)


class TwoThreadsOneSessionTests(IndexTests):
    """The turn reads memory while a background thread writes it, through one
    session. Measured live, 2026-10-13: a search landed inside a store, the
    new row was flushed twice ("UNIQUE constraint failed: memories.id"), the
    session stayed failed, and every later turn that read memory raised in
    the reply thread -- she stopped answering until she was restarted."""

    def test_reading_while_writing(self):
        import threading

        manager, _, _ = self.manager()
        self.told(manager)
        problems: list[BaseException] = []

        def write():
            try:
                for number in range(60):
                    manager.store_memory(content=f"The user visited place number {number}.",
                                         category="personal")
            except BaseException as error:      # noqa: BLE001 -- the test is that there is none
                problems.append(error)

        def read():
            try:
                for _ in range(300):
                    for memory in manager.search("The user lives in Seattle.", k=5):
                        memory.content
                    manager.profile()
            except BaseException as error:      # noqa: BLE001
                problems.append(error)

        with contextlib.redirect_stdout(io.StringIO()):
            threads = [threading.Thread(target=write)] + [threading.Thread(target=read) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
        self.assertEqual(problems, [])
        self.assertEqual(manager.db.query(Memory).count(), 63)
        self.assertEqual(manager.db.query(VectorMapping).count(), 63)
        self.assertEqual(manager.faiss.index.ntotal, 63)

    def test_a_write_that_fails_does_not_cost_every_read_after_it(self):
        manager, _, _ = self.manager()
        self.told(manager)

        def refuse(vector):
            raise RuntimeError("the index would not take it")

        with mock.patch.object(manager.faiss, "add_vector", refuse):
            with self.assertRaises(RuntimeError), contextlib.redirect_stdout(io.StringIO()):
                manager.store_memory(content="The user has asthma.", category="personal")
        # The session is usable again, and nothing half-written is in the way.
        self.assertEqual(self.found(manager, "The user lives in Seattle."), ["The user lives in Seattle."])
        self.assertIn("The user is allergic to peanuts.", manager.profile())
        with contextlib.redirect_stdout(io.StringIO()):
            manager.store_memory(content="The user plays tennis on Saturdays.", category="personal")
        self.assertEqual(self.found(manager, "The user plays tennis on Saturdays."),
                         ["The user plays tennis on Saturdays."])

    def test_what_a_search_returns_can_be_read_without_the_database(self):
        manager, _, _ = self.manager()
        self.told(manager)
        [memory] = manager.search("The user's dog is named Kong.", k=1)
        manager.db.close()
        self.assertEqual(memory.content, "The user's dog is named Kong.")


class EverythingToldTests(IndexTests):
    """The profile is everything they told her, not the newest two dozen."""

    def test_the_first_thing_said_is_still_there_after_thirty_more(self):
        manager, _, _ = self.manager()
        with contextlib.redirect_stdout(io.StringIO()):
            manager.store_memory(content="The user's name is Jisoo.", category="personal")
            for number, place in enumerate(
                ("Seattle", "Busan", "Daegu", "Portland", "Tacoma", "Incheon", "Jeju", "Boston",
                 "Austin", "Denver", "Chicago", "Osaka", "Kyoto", "Paris", "Berlin", "Madrid",
                 "Lisbon", "Dublin", "Oslo", "Vienna", "Prague", "Zurich", "Milan", "Athens",
                 "Cairo", "Dubai", "Hanoi", "Bangkok", "Manila", "Sydney")):
                row = Memory(content=f"The user visited {place}.", category="personal")
                manager.db.add(row)
            manager.db.commit()

        told = manager.profile()
        self.assertEqual(len(told), 31)
        self.assertIn("The user's name is Jisoo.", told)


if __name__ == "__main__":
    unittest.main()
