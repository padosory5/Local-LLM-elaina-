import unittest

from tools.computer_control.session_item_memory import SessionItemMemory


class SessionItemMemoryTests(unittest.TestCase):
    def test_records_and_returns_recent_items_by_kind(self):
        memory = SessionItemMemory()

        memory.record(name="Test Notes", location="Desktop", kind="folder")
        memory.record(name="notes.txt", location="Documents", kind="file")

        folders = memory.recent("folder")
        files = memory.recent("file")

        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0].name, "Test Notes")
        self.assertEqual(folders[0].location, "Desktop")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "notes.txt")

    def test_recent_context_returns_plain_dicts(self):
        memory = SessionItemMemory()
        memory.record(name="Test Notes", location="Desktop", kind="folder")

        context = memory.recent_context()

        self.assertEqual(
            context,
            ({"name": "Test Notes", "location": "Desktop", "kind": "folder"},),
        )

    def test_blank_name_is_ignored(self):
        memory = SessionItemMemory()

        memory.record(name="  ", location="Desktop", kind="folder")

        self.assertEqual(memory.recent("folder"), ())

    def test_keeps_only_the_most_recent_items_per_kind(self):
        memory = SessionItemMemory()

        for index in range(8):
            memory.record(
                name=f"Folder {index}", location="Desktop", kind="folder",
            )

        recent = memory.recent("folder")

        self.assertEqual(len(recent), 5)
        self.assertEqual(recent[-1].name, "Folder 7")
        self.assertEqual(recent[0].name, "Folder 3")

    def test_different_kinds_do_not_crowd_each_other_out(self):
        memory = SessionItemMemory()
        memory.record(name="Test Notes", location="Desktop", kind="folder")
        for index in range(6):
            memory.record(
                name=f"note{index}.txt", location="Documents", kind="file",
            )

        self.assertEqual(len(memory.recent("folder")), 1)
        self.assertEqual(len(memory.recent("file")), 5)


if __name__ == "__main__":
    unittest.main()
