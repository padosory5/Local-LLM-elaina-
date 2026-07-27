import unittest

from core.paths import (
    FAISS_INDEX_PATH,
    MEMORY_DATABASE_PATH,
    PROJECT_ROOT,
    VISUAL_SEARCH_USAGE_PATH,
)


class RuntimePathTests(unittest.TestCase):
    def test_generated_state_is_under_runtime(self):
        for path in (
            FAISS_INDEX_PATH,
            MEMORY_DATABASE_PATH,
            VISUAL_SEARCH_USAGE_PATH,
        ):
            self.assertTrue(path.is_relative_to(PROJECT_ROOT / "runtime"))


if __name__ == "__main__":
    unittest.main()
