"""Central filesystem locations used by Elaina."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
DATABASE_DIRECTORY = RUNTIME_ROOT / "database"
DATA_DIRECTORY = RUNTIME_ROOT / "data"
DEBUG_DIRECTORY = RUNTIME_ROOT / "debug"
SCREEN_CAPTURE_DIRECTORY = DEBUG_DIRECTORY / "screen_captures"

MEMORY_DATABASE_PATH = DATABASE_DIRECTORY / "memory.db"
FAISS_INDEX_PATH = DATABASE_DIRECTORY / "faiss.index"
VISUAL_SEARCH_USAGE_PATH = DATA_DIRECTORY / "visual_search_usage.json"


def ensure_runtime_directories() -> None:
    """Create generated-data directories without touching their contents."""
    for directory in (
        DATABASE_DIRECTORY,
        DATA_DIRECTORY,
        SCREEN_CAPTURE_DIRECTORY,
    ):
        directory.mkdir(parents=True, exist_ok=True)
