from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# SQLite database
DATABASE_URL = "sqlite:///database/memory.db"

# Create the database engine
engine = create_engine(DATABASE_URL, echo=False)

# Create a session factory
SessionLocal = sessionmaker(bind=engine)

# Base class for all database tables
Base = declarative_base()