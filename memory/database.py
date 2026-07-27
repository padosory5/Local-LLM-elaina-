from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from core.paths import MEMORY_DATABASE_PATH, ensure_runtime_directories


ensure_runtime_directories()
DATABASE_URL = f"sqlite:///{MEMORY_DATABASE_PATH.as_posix()}"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
