"""Education module and progress tracking models."""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


class DifficultyEnum(str, enum.Enum):
    """Education module difficulty levels."""

    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"


class EducationModule(Base):
    __tablename__ = "education_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # BEGINNER, INTERMEDIATE, ADVANCED
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    sector_relevance: Mapped[str | None] = mapped_column(String(100), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    progress = relationship("EducationProgress", back_populates="module", lazy="selectin")


class EducationProgress(Base):
    __tablename__ = "education_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    module_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("education_modules.id"), nullable=False
    )
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    user = relationship("User", back_populates="education_progress", lazy="selectin")
    module = relationship("EducationModule", back_populates="progress", lazy="selectin")
