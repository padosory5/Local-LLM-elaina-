"""Create the runtime memory database tables."""

from memory.database import engine
from memory.models import Base

Base.metadata.create_all(bind=engine)

print("Runtime memory database is ready.")
