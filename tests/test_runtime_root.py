"""A verification backend can keep its state out of the person's.

Every live verification run wrote into the person's own runtime: a replayed
"다음부턴 그렇게 설명해라" became a real standing order, and each searched
test turn saved a research memory, forty-nine of them in one afternoon.
ELAINA_RUNTIME_ROOT points every writer somewhere else. Unset, nothing
changes.

Checked in a subprocess, because core.paths is read once at import and a
test that reloads it would be testing the reload rather than the start-up
the backend actually does.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRINT_PATHS = (
    "from core import paths; "
    "print(paths.RUNTIME_ROOT); print(paths.MEMORY_DATABASE_PATH); "
    "print(paths.FAISS_INDEX_PATH); print(paths.DATA_DIRECTORY)"
)


def _paths(env: dict) -> list[str]:
    output = subprocess.run(
        [sys.executable, "-c", PRINT_PATHS], cwd=str(ROOT), env=env,
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.strip() for line in output.splitlines() if line.strip()]


class TheRuntimeRootTests(unittest.TestCase):

    def test_unset_it_is_the_projects_own_runtime(self):
        env = {k: v for k, v in os.environ.items() if k != "ELAINA_RUNTIME_ROOT"}

        root, *_ = _paths(env)

        self.assertEqual(Path(root), ROOT / "runtime")

    def test_set_every_writer_follows_it(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            env = dict(os.environ, ELAINA_RUNTIME_ROOT=elsewhere)

            found = _paths(env)

            for path in found:
                with self.subTest(path=path):
                    self.assertTrue(
                        Path(path).is_relative_to(Path(elsewhere)),
                        f"{path} is still inside the person's runtime",
                    )


if __name__ == "__main__":
    unittest.main()
