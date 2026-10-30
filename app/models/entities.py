"""SQLAlchemy ORM models."""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(str, enum.Enum):
    admin = "admin"
    contributor = "contributor"
    reviewer = "reviewer"


class ExpressionStatus(str, enum.Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    published = "published"
    archived = "archived"


class RecordingStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.contributor.value)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserSession(Base):
    """Server-side session. Only a SHA-256 of the cookie token is stored."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")

    user: Mapped[User] = relationship()


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Speaker(Base):
    __tablename__ = "speakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    variety: Mapped[str] = mapped_column(String(100), default="")
    consent_scope: Mapped[str] = mapped_column(Text, default="")
    consent_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    reuse_restrictions: Mapped[str] = mapped_column(Text, default="")
    attribution: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def has_consent(self) -> bool:
        return bool(self.consent_scope.strip()) and self.consent_date is not None


class Expression(Base):
    __tablename__ = "expressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    expression_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    gondi_text: Mapped[str] = mapped_column(Text, default="")
    transliteration: Mapped[str] = mapped_column(Text, default="")
    telugu: Mapped[str] = mapped_column(Text, default="")
    english: Mapped[str] = mapped_column(Text, default="")
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    variety: Mapped[str] = mapped_column(String(100), default="")
    script: Mapped[str] = mapped_column(String(50), default="")
    usage_note: Mapped[str] = mapped_column(Text, default="")
    provenance: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=ExpressionStatus.draft.value, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    category: Mapped[Category | None] = relationship()
    recordings: Mapped[list["Recording"]] = relationship(
        back_populates="expression", cascade="all, delete-orphan", order_by="Recording.id"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="expression", cascade="all, delete-orphan", order_by="Review.id"
    )

    @property
    def default_recording(self) -> "Recording | None":
        for r in self.recordings:
            if r.is_default:
                return r
        return None


class Recording(Base):
    __tablename__ = "recordings"
    __table_args__ = (Index("ix_recordings_expr_default", "expression_id", "is_default"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    expression_id: Mapped[int] = mapped_column(ForeignKey("expressions.id", ondelete="CASCADE"), index=True)
    speaker_id: Mapped[int | None] = mapped_column(ForeignKey("speakers.id", ondelete="SET NULL"), nullable=True)
    stored_filename: Mapped[str] = mapped_column(String(128), unique=True)
    source_filename: Mapped[str] = mapped_column(String(128), default="")  # archived original if transcoded
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=RecordingStatus.pending.value)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    expression: Mapped[Expression] = relationship(back_populates="recordings")
    speaker: Mapped[Speaker | None] = relationship()


class Review(Base):
    """Workflow history: every submit / approve / reject / publish action."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    expression_id: Mapped[int] = mapped_column(ForeignKey("expressions.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision: Mapped[str] = mapped_column(String(20))
    comments: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    expression: Mapped[Expression] = relationship(back_populates="reviews")
    reviewer: Mapped[User | None] = relationship()


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    zip_filename: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="previewed")  # previewed|committed|failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="", index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


__all__ = [
    "AuditLog",
    "Category",
    "Expression",
    "ExpressionStatus",
    "ImportJob",
    "Recording",
    "RecordingStatus",
    "Review",
    "Role",
    "Speaker",
    "User",
    "UserSession",
    "UniqueConstraint",
]
