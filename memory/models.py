from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
)

from sqlalchemy.orm import relationship

from datetime import datetime

from .database import Base


class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True)

    content = Column(String, nullable=False)

    category = Column(String, default="general")

    importance = Column(Float, default=1.0)

    created_at = Column(DateTime, default=datetime.utcnow)

    last_accessed = Column(DateTime, default=datetime.utcnow)

    access_count = Column(Integer, default=0)

    is_active = Column(Boolean, default=True)

    source = Column(String, default="conversation")

    vector = relationship(
        "VectorMapping",
        back_populates="memory",
        uselist=False
    )


class VectorMapping(Base):
    __tablename__ = "vector_mappings"

    id = Column(Integer, primary_key=True)

    faiss_index = Column(Integer, unique=True)

    memory_id = Column(
        Integer,
        ForeignKey("memories.id")
    )

    memory = relationship(
        "Memory",
        back_populates="vector"
    )